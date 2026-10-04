"""Continue rejected VCI captures after offline proof replay; never activate data."""

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, date_bounds
from aipriceaction_api.providers import Providers
from scripts.artifact_budget import ArtifactBudget, ArtifactBudgetExceeded
from scripts.compare_vn_feeds import BudgetedTransport
from scripts.replay_vci_candidate_pages import replay_record


def page_identity(page):
    # Exclude runtime timestamps/provenance; compare every actual OHLCV exactly.
    rows = sorted((r.time, r.open, r.high, r.low, r.close, r.volume) for r in page.rows)
    return page.cursor, rows


async def continue_record(providers, symbol, record, replay, max_pages):
    first = date_bounds(record["start_date"])
    cursor = replay["next_cursor"]
    result = {
        "symbol": symbol,
        "pages": [],
        "boundary_reached": False,
        "requested_year_proven": False,
        "captured_rows": sum(p["rows"] for p in replay["pages"]),
        "new_rows": 0,
        "new_rows_by_date": {},
        "seam_verified": False,
    }
    dates = Counter()
    try:
        seam_before = replay["pages"][-1]["before"]
        seam = await providers.page(
            "vn", symbol, "1m", seam_before, count=10000, start=first, provider="vci"
        )
        if page_identity(seam) != page_identity(replay["last_page"]):
            raise DataError("Fresh VCI seam changed timestamps, OHLCV or cursor")
        result["seam_verified"] = True
        for _ in range(max_pages):
            if cursor is not None and cursor <= first:
                result.update(stop="requested_boundary", boundary_reached=True)
                return result
            if cursor is None:
                result["stop"] = "provider_empty_before_boundary"
                return result
            page = await providers.page(
                "vn", symbol, "1m", cursor, count=10000, start=first, provider="vci"
            )
            if any(not first <= r.time < cursor for r in page.rows):
                raise DataError("Continuation row outside requested cursor window")
            next_cursor = page.cursor
            if next_cursor is None and page.rows:
                next_cursor = min(r.time for r in page.rows)
            if next_cursor is not None and next_cursor >= cursor:
                raise DataError("Continuation cursor did not move backwards")
            result["pages"].append(
                {"before": cursor, "cursor": next_cursor, "rows": len(page.rows)}
            )
            dates.update(
                datetime.fromtimestamp(r.time, UTC).strftime("%Y-%m-%d") for r in page.rows
            )
            result["new_rows"] += len(page.rows)
            result["new_rows_by_date"] = dict(sorted(dates.items()))
            cursor = next_cursor
            if cursor is not None and cursor <= first:
                result.update(stop="requested_boundary", boundary_reached=True)
                return result
            if not page.rows:
                result["stop"] = "provider_empty_before_boundary"
                return result
        result["stop"] = "page_budget"
    except DataError as exc:
        result.update(stop="provider_error", error=str(exc))
    result["next_cursor"] = cursor
    return result


async def run(args):
    if not 1 <= args.max_pages <= 20 or not 16 <= args.artifact_budget_mib <= 512:
        raise ValueError("Use 1..20 pages and 16..512 MiB")
    summary = json.loads((args.audit / "summary.json").read_text())
    if not summary["completed"]:
        raise DataError("Finish source collection before continuation")
    settings = replace(
        Settings.from_env(),
        proxies=(),
        allow_direct=True,
        vci_history_fallback=True,
        vci_volume_proofs=args.proofs,
    )
    args.output.mkdir(parents=True, exist_ok=False)
    budget = ArtifactBudget(args.output, args.artifact_budget_mib * 1024 * 1024 - 65536)
    transport = BudgetedTransport(args.output, budget)
    providers = Providers(settings, transport=transport)
    result = {
        "canonical_publication": False,
        "active_catalog_changed": False,
        "proofs_sha256": hashlib.sha256(args.proofs.read_bytes()).hexdigest(),
        "audit": str(args.audit),
        "audit_sha256": hashlib.sha256((args.audit / "summary.json").read_bytes()).hexdigest(),
        "max_pages": args.max_pages,
        "artifact_budget_mib": args.artifact_budget_mib,
        "series": [],
        "offline_blocked": [],
        "completed": False,
    }

    def checkpoint():
        # Reserve room for a final failure report even when captures hit their cap.
        raw = (json.dumps(result, indent=2) + "\n").encode()
        if len(raw) > 32768:
            raise DataError("Continuation summary exceeded reserved report budget")
        ArtifactBudget(args.output, args.artifact_budget_mib * 1024 * 1024).write(
            args.output / "report.json", raw
        )

    symbol, offset = None, 0
    try:
        for batch in summary["batches"]:
            for symbol in batch["symbols"]:
                record = json.loads((Path(batch["path"]) / "vci" / f"{symbol}-1m.json").read_text())
                if "error" not in record:
                    continue
                replay = await replay_record(settings, symbol, record, retain_last_page=True)
                if not replay["captured_pages_passed"]:
                    result["offline_blocked"].append({"symbol": symbol, "error": replay["error"]})
                    checkpoint()
                    continue
                offset = len(transport.captures)
                row = await continue_record(providers, symbol, record, replay, args.max_pages)
                row["captures"] = transport.captures[offset:]
                budget.write(
                    args.output / f"{symbol}.json", (json.dumps(row, indent=2) + "\n").encode()
                )
                # Keep per-date details in the small individual reports.
                result["series"].append(
                    {
                        k: v
                        for k, v in row.items()
                        if k not in ("captures", "new_rows_by_date", "pages")
                    }
                )
                checkpoint()
                print(json.dumps(result["series"][-1]), flush=True)
        result["completed"] = True
    except ArtifactBudgetExceeded:
        result["stop"] = "artifact_budget"
        result["interrupted_series"] = {
            "symbol": symbol,
            "captures": transport.captures[offset:],
        }
    finally:
        await providers.close()
        checkpoint()
    print(
        json.dumps(
            {
                "completed": result["completed"],
                "series": len(result["series"]),
                "offline_blocked": len(result["offline_blocked"]),
            }
        ),
        flush=True,
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--proofs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--artifact-budget-mib", type=int, default=256)
    asyncio.run(run(parser.parse_args()))
