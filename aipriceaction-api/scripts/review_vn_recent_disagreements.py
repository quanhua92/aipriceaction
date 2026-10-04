"""Review a bounded recent window from saved four-provider observations, without requests."""

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import asdict, replace
from itertools import combinations
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, date_bounds
from scripts.artifact_budget import ArtifactBudget
from scripts.compare_vn_feeds import NATIVE_FEEDS
from scripts.ohlcv_disagreements import diagnose
from scripts.replay_vci_candidate_pages import replay_record
from scripts.review_vn_minute_universe import compact_local
from scripts.validate_ohlcv import local_comparisons


def aggregate(details):
    prices, totals = Counter(), Counter()
    for detail in details:
        prices.update(detail["price_classes"])
        totals["shared_rows"] += detail["shared_rows"]
        totals["volume_disagreements"] += detail["volume_disagreements"]
        for day in detail["volume_days"]:
            totals["volume_days"] += 1
            totals["matching_timestamp_volume_days"] += day["timestamps_match"]
            totals["equal_total_volume_days"] += (
                day["sqlite_volume_total"] == day["source_volume_total"]
            )
    return {**dict(totals), "price_classes": dict(prices)}


def run(args):
    summary_bytes = (args.audit / "summary.json").read_bytes()
    summary = json.loads(summary_bytes)
    request = json.loads((args.audit / "request.json").read_text())
    start, before = date_bounds(args.start_date), date_bounds(args.end_date, end=True) + 1
    if not summary["completed"] or summary["symbols"] != request["symbols"]:
        raise DataError("Finish the declared source collection before review")
    if (
        before <= start
        or before - start > 7 * 86400
        or start < date_bounds(request["start_date"])
        or before > date_bounds(request["end_date"], end=True) + 1
    ):
        raise DataError("Choose at most seven calendar days inside the collected window")
    if len(set(request["symbols"])) != len(request["symbols"]):
        raise DataError("Duplicate requested symbols")
    settings = Settings.from_env()
    combined = getattr(args, "combined_records", None)
    proofs = getattr(args, "proofs", None)
    combined_identity = None
    if bool(combined) != bool(proofs):
        raise DataError("Combined VCI captures require their exact proof catalog")
    if combined:
        combined_bytes = (combined / "report.json").read_bytes()
        reviewed = json.loads(combined_bytes)
        names = [row["symbol"] for row in reviewed["series"]]
        if (
            not reviewed["completed"]
            or len(names) != len(set(names))
            or set(names) != set(request["symbols"])
            or reviewed["proofs_sha256"] != hashlib.sha256(proofs.read_bytes()).hexdigest()
            or reviewed["audit_sha256"] != hashlib.sha256(summary_bytes).hexdigest()
        ):
            raise DataError("Combined VCI review scope, audit or proof catalog differs")
        combined_identity = {
            "path": str(combined),
            "sha256": hashlib.sha256(combined_bytes).hexdigest(),
            "proofs_sha256": reviewed["proofs_sha256"],
        }
        settings = replace(settings, vci_history_fallback=True, vci_volume_proofs=proofs)
    series, seen = [], set()
    for batch in summary["batches"]:
        root = Path(batch["path"])
        if not root.resolve().is_relative_to(args.audit.resolve()):
            raise DataError("Batch outside selected source collection")
        comparison = json.loads((root / "report.json").read_text())
        if (
            comparison["symbols"] != batch["symbols"]
            or comparison["intervals"] != ["1m"]
            or comparison["feeds"] != list(NATIVE_FEEDS)
            or comparison["intraday_start"] != request["start_date"]
            or comparison["end_date"] != request["end_date"]
        ):
            raise DataError("Batch scope differs from the declared native minute collection")
        records, identities = {}, {}
        for symbol in comparison["symbols"]:
            if symbol in seen or symbol not in request["symbols"]:
                raise DataError("Unexpected or duplicate batch symbol")
            seen.add(symbol)
            identities[symbol] = {}
            for feed in NATIVE_FEEDS:
                path = root / feed / f"{symbol}-1m.json"
                raw = path.read_bytes()
                record = json.loads(raw)
                rows = [row for row in record.get("rows", []) if start <= row["time"] < before]
                supplement = None
                # Replay only when the original accepted pages do not reach this window.
                if (
                    feed == "vci"
                    and combined
                    and (
                        not record.get("rows") or min(row["time"] for row in record["rows"]) > start
                    )
                ):
                    combined_path = combined / f"{symbol}-record.json"
                    combined_raw = combined_path.read_bytes()
                    combined_record = json.loads(combined_raw)
                    if (combined_record["start_date"], combined_record["end_date"]) != (
                        request["start_date"],
                        request["end_date"],
                    ):
                        raise DataError("Combined VCI capture window differs")
                    replay = asyncio.run(
                        replay_record(settings, symbol, combined_record, retain_rows=True)
                    )
                    rows = [asdict(row) for row in replay.pop("rows") if start <= row.time < before]
                    supplement = {
                        "path": str(combined_path),
                        "sha256": hashlib.sha256(combined_raw).hexdigest(),
                        "captured_pages_passed": replay["captured_pages_passed"],
                        "replay_error": replay.get("error"),
                        "accepted_rows": replay["accepted_rows"],
                    }
                if len({row["time"] for row in rows}) != len(rows):
                    raise DataError("Duplicate saved provider timestamps")
                records[(feed, symbol, "1m")] = {"rows": rows}
                identities[symbol][feed] = {
                    "path": str(path),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "observed_rows": len(rows),
                    "original_collection_error": record.get("error"),
                }
                if supplement:
                    identities[symbol][feed]["combined_capture_replay"] = supplement
        scoped = {**comparison, "intraday_start": args.start_date, "end_date": args.end_date}
        local = local_comparisons(
            settings, root, scoped, records=records, diagnose_feed="all", diagnose_samples=3
        )
        for row in local:
            symbol = row["symbol"]
            peers = {}
            for left, right in combinations(NATIVE_FEEDS, 2):
                peers[f"{left}:{right}"] = diagnose(
                    {r["time"]: r for r in records[(left, symbol, "1m")]["rows"]},
                    {r["time"]: r for r in records[(right, symbol, "1m")]["rows"]},
                    samples=3,
                )
            series.append(
                {
                    "symbol": symbol,
                    "observations": identities[symbol],
                    "sqlite_comparison": compact_local(row, samples=3),
                    "provider_pairs": peers,
                }
            )
        print(
            json.dumps(
                {"reviewed_symbols": len(seen), "requested_symbols": len(request["symbols"])}
            ),
            flush=True,
        )
    if seen != set(request["symbols"]):
        raise DataError("Completed collection is missing requested symbols")
    totals = {
        feed: aggregate(
            row["sqlite_comparison"]["providers"][feed]["diagnostics"] for row in series
        )
        for feed in NATIVE_FEEDS
    }
    pair_totals = (
        {
            key: aggregate(row["provider_pairs"][key] for row in series)
            for key in series[0]["provider_pairs"]
        }
        if series
        else {}
    )
    result = {
        "remote_requests": False,
        "canonical_publication": False,
        "perfect_data_proven": False,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "checked_symbols": len(seen),
        "audit_sha256": hashlib.sha256(summary_bytes).hexdigest(),
        "combined_vci_evidence": combined_identity,
        "sqlite_totals": totals,
        "provider_pair_totals": pair_totals,
        "series": series,
        "limitations": [
            "Saved normalized observations are compared; only explicitly recorded VCI supplements replay native captures and parsers.",
            "Original collection errors remain visible; shared observations do not prove full window coverage.",
            "Each batch uses its own read-only SQLite snapshot including verified volume receipts.",
            "For provider pairs, diagnostic sqlite fields denote the left provider; source fields denote the right provider.",
            "No agreement, scale classification or equal day total independently licenses publication.",
        ],
    }
    args.output.mkdir(parents=True, exist_ok=False)
    ArtifactBudget(args.output, 8 * 1024 * 1024).write(
        args.output / "report.json", (json.dumps(result, indent=2, allow_nan=False) + "\n").encode()
    )
    print(
        json.dumps(
            {
                "checked_symbols": len(seen),
                "sqlite_totals": totals,
                "provider_pair_totals": pair_totals,
            }
        )
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--combined-records", type=Path)
    parser.add_argument("--proofs", type=Path)
    run(parser.parse_args())
