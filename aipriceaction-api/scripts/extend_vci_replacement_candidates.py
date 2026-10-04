"""Extend eligible captured VCI candidates to existing archive floors; never publish."""

import argparse
import asyncio
import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, date_bounds
from aipriceaction_api.providers import Providers
from scripts.artifact_budget import ArtifactBudget
from scripts.compare_vn_feeds import BudgetedTransport
from scripts.continue_vci_candidate_pages import continue_record
from scripts.replay_vci_candidate_pages import replay_record
from scripts.review_vci_combined_captures import combined_record
from scripts.vci_activation_inputs import replay_page, values
from scripts.vn_daily_volume_evidence import captured


async def expand_floor(settings, symbol, original, floor):
    """Reparse immutable pages at the earlier floor, preserving every old observation."""
    if floor > original["start_date"]:
        raise DataError("Expansion cannot discard the original candidate window")
    expanded = {
        "start_date": floor,
        "end_date": original["end_date"],
        "window": {"pages": []},
        "captures": [],
    }
    if len(original["captures"]) != len(original["window"]["pages"]) or "error" in original:
        raise DataError("Expansion requires a complete accepted capture chain")
    for checkpoint, evidence in zip(original["window"]["pages"], original["captures"], strict=True):
        raw = captured({"captures": [evidence]}, {})
        old = await replay_page(
            settings,
            raw,
            "vn",
            symbol,
            "1m",
            checkpoint["before"],
            10000,
            date_bounds(original["start_date"]),
        )
        if len(old.rows) != checkpoint["rows"] or old.cursor != checkpoint["cursor"]:
            raise DataError("Original capture checkpoint changed")
        expanded["captures"].append(evidence)
        try:
            page = await replay_page(
                settings, raw, "vn", symbol, "1m", checkpoint["before"], 10000, date_bounds(floor)
            )
            if page.cursor != old.cursor or values(
                [r for r in page.rows if r.time >= date_bounds(original["start_date"])]
            ) != values(old.rows):
                raise DataError("Expanded floor changed an accepted original observation")
            expanded["window"]["pages"].append(
                {"before": checkpoint["before"], "cursor": page.cursor, "rows": len(page.rows)}
            )
        except DataError as exc:
            expanded["error"] = str(exc)
            return expanded
    return expanded


async def run(args):
    if not 1 <= args.max_pages <= 4:
        raise DataError("Use one to four bounded extension pages")
    review_raw = (args.review / "report.json").read_bytes()
    basis_raw = (args.basis / "report.json").read_bytes()
    review, basis = json.loads(review_raw), json.loads(basis_raw)
    proof_sha = hashlib.sha256(args.proofs.read_bytes()).hexdigest()
    if (
        not review["completed"]
        or not basis["completed"]
        or basis["review_sha256"] != hashlib.sha256(review_raw).hexdigest()
        or basis["proofs_sha256"] != proof_sha
        or review["proofs_sha256"] != proof_sha
    ):
        raise DataError("Basis review, captured source review and proof catalog differ")
    selected, refused = [], []
    for row in basis["series"]:
        checks = row["summary"]
        prices = checks["daily_comparisons"]["sqlite_daily"]["vci_minutes"]
        if (
            checks["native_volume_witness_exceptions"]
            or prices["observed_days"] != len(row["days"])
            or prices["price_within_1_vnd_days"] != len(row["days"])
        ):
            refused.append(
                {
                    "symbol": row["symbol"],
                    "reason": "Unresolved observed daily price or native volume witnesses",
                }
            )
        else:
            selected.append(row["symbol"])
    if not selected or len(selected) != len(set(selected)) or len(selected) > 4:
        raise DataError("Need one to four unique eligible captured basis exceptions")
    settings = replace(
        Settings.from_env(),
        vci_history_fallback=True,
        vci_volume_proofs=args.proofs,
        allow_direct=True,
        proxies=(),
    )
    args.output.mkdir(parents=True, exist_ok=False)
    budget = ArtifactBudget(args.output, 32 * 1024 * 1024)
    transport = BudgetedTransport(args.output, budget)
    providers = Providers(settings, transport=transport)
    result = {
        "canonical_publication": False,
        "active_catalog_changed": False,
        "completed": False,
        "review_sha256": hashlib.sha256(review_raw).hexdigest(),
        "basis_sha256": hashlib.sha256(basis_raw).hexdigest(),
        "proofs_sha256": proof_sha,
        "series": [],
        "refused": refused,
        "limitations": [
            "Extended source coverage is not publication permission or a full storage rehearsal.",
            "Existing archive metadata determines the required floor; exact original cold timestamps require a separate readback check.",
        ],
    }

    def save():
        budget.write(
            args.output / "report.json",
            (json.dumps(result, indent=2, allow_nan=False) + "\n").encode(),
        )

    save()
    try:
        with sqlite3.connect(settings.database.resolve().as_uri() + "?mode=ro", uri=True) as con:
            for symbol in selected:
                original_raw = (args.review / f"{symbol}-record.json").read_bytes()
                basis_series = next(row for row in basis["series"] if row["symbol"] == symbol)
                if basis_series["record_sha256"] != hashlib.sha256(original_raw).hexdigest():
                    raise DataError("Captured source record changed after basis review")
                original = json.loads(original_raw)
                hot = con.execute(
                    "SELECT min(time) FROM candles WHERE source='vn' AND symbol=? AND interval='1m'",
                    (symbol,),
                ).fetchone()[0]
                cold = con.execute(
                    "SELECT min(start) FROM archives WHERE source='vn' AND symbol=? AND interval='1m' AND status IN ('published','historical_snapshot','pending_repair')",
                    (symbol,),
                ).fetchone()[0]
                stamps = [t for t in (hot, cold) if t is not None]
                if not stamps:
                    raise DataError("Eligible candidate lacks an existing original series")
                floor = datetime.fromtimestamp(min(stamps), UTC).strftime("%Y-%m-%d")
                if date_bounds(original["end_date"]) - date_bounds(floor) > 550 * 86400:
                    raise DataError("Archive floor exceeds bounded native candidate scope")
                expanded = await expand_floor(settings, symbol, original, floor)
                baseline = await replay_record(settings, symbol, expanded, retain_last_page=True)
                item = {
                    "symbol": symbol,
                    "required_start_date": floor,
                    "end_date": original["end_date"],
                    "accepted_original_rows": baseline["accepted_rows"],
                    "candidate_ready": False,
                }
                result["series"].append(item)
                if not baseline["captured_pages_passed"]:
                    item["error"] = baseline.get("error")
                    record = expanded
                elif baseline["next_cursor"] is not None and baseline["next_cursor"] <= date_bounds(
                    floor
                ):
                    record = expanded
                else:
                    first_capture = len(transport.captures)
                    continuation = await continue_record(
                        providers, symbol, expanded, baseline, args.max_pages
                    )
                    continuation["captures"] = transport.captures[first_capture:]
                    item["continuation"] = continuation
                    record = (
                        await combined_record(settings, symbol, expanded, continuation)
                        if continuation["seam_verified"]
                        else expanded
                    )
                    if not continuation["seam_verified"]:
                        item["error"] = continuation.get("error", "Fresh seam failed")
                replay = await replay_record(settings, symbol, record)
                item.update(
                    accepted_rows=replay["accepted_rows"],
                    captured_pages_passed=replay["captured_pages_passed"],
                    rows_by_date=replay["rows_by_date"],
                    next_cursor=replay.get("next_cursor"),
                )
                item["candidate_ready"] = (
                    not item.get("error")
                    and replay["captured_pages_passed"]
                    and replay.get("next_cursor") is not None
                    and replay["next_cursor"] <= date_bounds(floor)
                )
                if replay.get("error"):
                    item["error"] = replay["error"]
                budget.write(
                    args.output / f"{symbol}-record.json",
                    (json.dumps(record, indent=2) + "\n").encode(),
                )
                save()
                print(
                    json.dumps(
                        {
                            k: item[k]
                            for k in (
                                "symbol",
                                "required_start_date",
                                "accepted_rows",
                                "candidate_ready",
                            )
                        }
                    ),
                    flush=True,
                )
        result["completed"] = True
        save()
    finally:
        await providers.close()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--basis", type=Path, required=True)
    parser.add_argument("--proofs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=2)
    asyncio.run(run(parser.parse_args()))
