"""Replay combined VCI captures and review calendar/SQLite; no requests or canonical writes."""

import argparse
import asyncio
import hashlib
import json
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, date_bounds
from scripts.artifact_budget import ArtifactBudget
from scripts.audit_vn_provider_dates import expected_dates
from scripts.continue_vci_candidate_pages import page_identity
from scripts.replay_vci_candidate_pages import replay_record
from scripts.review_vn_minute_universe import compact_local
from scripts.validate_ohlcv import local_comparisons


async def combined_record(settings, symbol, original, continuation):
    """Recheck the fresh seam before excluding its duplicate page from the chain."""
    baseline = await replay_record(settings, symbol, original, retain_last_page=True)
    if not baseline["captured_pages_passed"]:
        return original
    if continuation["symbol"] != symbol or not continuation["seam_verified"]:
        raise DataError("Continuation identity or seam verification differs")
    if continuation["captured_rows"] != baseline["accepted_rows"]:
        raise DataError("Continuation baseline row count differs")
    captures, pages = continuation["captures"], continuation["pages"]
    failed = continuation["stop"] == "provider_error"
    if len(captures) != 1 + len(pages) + int(failed):
        raise DataError("Continuation capture/page count differs")
    seam_record = {
        "start_date": original["start_date"],
        "end_date": original["end_date"],
        "window": {"pages": [baseline["pages"][-1]]},
        "captures": captures[:1],
    }
    seam = await replay_record(settings, symbol, seam_record, retain_last_page=True)
    if not seam["captured_pages_passed"] or page_identity(seam["last_page"]) != page_identity(
        baseline["last_page"]
    ):
        raise DataError("Combined replay fresh seam no longer matches captured OHLCV")
    result = {
        "start_date": original["start_date"],
        "end_date": original["end_date"],
        "window": {"pages": baseline["pages"] + pages},
        "captures": original["captures"] + captures[1:],
    }
    if failed:
        result["error"] = continuation["error"]
    return result


async def run(args):
    summary = json.loads((args.audit / "summary.json").read_text())
    request = json.loads((args.audit / "request.json").read_text())
    continuation = json.loads((args.continuation / "report.json").read_text())
    if not summary["completed"] or not continuation["completed"]:
        raise DataError("Finish source collection and continuation before combined review")
    symbols = request["symbols"]
    if len(set(symbols)) != len(symbols) or summary["symbols"] != symbols:
        raise DataError("Audit symbol declarations differ")
    continued = {row["symbol"]: row for row in continuation["series"]}
    if len(continued) != len(continuation["series"]) or not set(continued) <= set(symbols):
        raise DataError("Unexpected or duplicate continuation symbol")
    expected = expected_dates(
        json.loads(args.calendar_catalog.read_text()),
        date.fromisoformat(request["start_date"]),
        date.fromisoformat(request["end_date"]),
    )
    settings = replace(
        Settings.from_env(), vci_history_fallback=True, vci_volume_proofs=args.proofs
    )
    args.output.mkdir(parents=True, exist_ok=False)
    budget = ArtifactBudget(args.output, 8 * 1024 * 1024)
    result = {
        "canonical_publication": False,
        "remote_requests": False,
        "active_catalog_changed": False,
        "perfect_data_proven": False,
        "proofs_sha256": hashlib.sha256(args.proofs.read_bytes()).hexdigest(),
        "audit_sha256": hashlib.sha256((args.audit / "summary.json").read_bytes()).hexdigest(),
        "continuation_sha256": hashlib.sha256(
            (args.continuation / "report.json").read_bytes()
        ).hexdigest(),
        "calendar_catalog_sha256": hashlib.sha256(args.calendar_catalog.read_bytes()).hexdigest(),
        "reference_dates": len(expected),
        "series": [],
        "completed": False,
    }
    seen = set()
    for batch in summary["batches"]:
        root = Path(batch["path"])
        if not root.resolve().is_relative_to(args.audit.resolve()):
            raise DataError("Batch outside selected audit")
        comparison = json.loads((root / "report.json").read_text())
        if comparison["symbols"] != batch["symbols"] or comparison["intervals"] != ["1m"]:
            raise DataError("Batch comparison identity differs")
        if (comparison["intraday_start"], comparison["end_date"]) != (
            request["start_date"],
            request["end_date"],
        ):
            raise DataError("SQLite comparison window differs from source audit")
        for symbol in batch["symbols"]:
            if symbol in seen or symbol not in symbols:
                raise DataError("Duplicate or unexpected audit symbol")
            seen.add(symbol)
            original = json.loads((root / "vci" / f"{symbol}-1m.json").read_text())
            if (original["start_date"], original["end_date"]) != (
                request["start_date"],
                request["end_date"],
            ):
                raise DataError("Source capture window differs from audit request")
            record = original
            if symbol in continued:
                detail = json.loads((args.continuation / f"{symbol}.json").read_text())
                if any(detail.get(k) != v for k, v in continued[symbol].items()):
                    raise DataError("Continuation summary differs from per-symbol evidence")
                record = await combined_record(settings, symbol, original, detail)
            replay = await replay_record(settings, symbol, record, retain_rows=True)
            rows = replay.pop("rows")
            observed = set(replay["rows_by_date"])
            cursor = replay.get("next_cursor")
            replay["boundary_reached"] = (
                replay["captured_pages_passed"]
                and cursor is not None
                and cursor <= date_bounds(request["start_date"])
            )
            replay["missing_reference_date_candidates"] = sorted(expected - observed)
            replay["unexpected_dates"] = sorted(observed - expected)
            replay["original_provider_error"] = original.get("error")
            one = comparison | {"symbols": [symbol]}
            local = local_comparisons(
                settings,
                root,
                one,
                records={("vci", symbol, "1m"): {"rows": [asdict(row) for row in rows]}},
            )
            replay["sqlite_comparison"] = compact_local(local[0])
            # Keep only references/metadata; never duplicate native captures or DBs.
            budget.write(
                args.output / f"{symbol}-record.json",
                (json.dumps(record | {"rows": []}, indent=2) + "\n").encode(),
            )
            result["series"].append(replay)
            budget.write(
                args.output / "report.json", (json.dumps(result, indent=2) + "\n").encode()
            )
    if seen != set(symbols):
        raise DataError("Combined review is missing selected symbols")
    result.update(
        completed=True,
        accepted_rows=sum(row["accepted_rows"] for row in result["series"]),
        boundary_series=sum(row["boundary_reached"] for row in result["series"]),
        blocked_series=sum(not row["captured_pages_passed"] for row in result["series"]),
    )
    budget.write(args.output / "report.json", (json.dumps(result, indent=2) + "\n").encode())
    print(
        json.dumps(
            {
                k: result[k]
                for k in ("completed", "accepted_rows", "boundary_series", "blocked_series")
            }
        ),
        flush=True,
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--continuation", type=Path, required=True)
    parser.add_argument("--proofs", type=Path, required=True)
    parser.add_argument("--calendar-catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
