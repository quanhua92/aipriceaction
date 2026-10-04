"""Stage one coherent VCI minute candidate in an isolated SQLite database."""

import argparse
import asyncio
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, date_bounds
from aipriceaction_api.providers import Providers
from aipriceaction_api.storage import Repository
from scripts.compare_vn_feeds import FIELDS, compare
from scripts.stage_yahoo_daily_history import RecordingTransport, freeze


def original_changes(originals, current):
    before = {r.time: r for r in originals}
    after = {r.time: r for r in current}
    shared = before.keys() & after.keys()
    return {
        "missing_timestamps": sorted(before.keys() - after.keys()),
        "new_timestamps": sorted(after.keys() - before.keys()),
        "record_versions_changed": sum(before[t] != after[t] for t in shared),
        "ohlcv_changed": sum(
            any(getattr(before[t], k) != getattr(after[t], k) for k in FIELDS) for t in shared
        ),
        "provider_revision_changed": sum(
            (before[t].provider, before[t].revision) != (after[t].provider, after[t].revision)
            for t in shared
        ),
    }


async def run(args):
    first, last = date_bounds(args.start_date), date_bounds(args.end_date, end=True)
    today = date_bounds(datetime.now(UTC).strftime("%Y-%m-%d"))
    if first > last or last >= today or last - first > 550 * 86400:
        raise ValueError("Use up to 550 completed days in order")
    base = Settings.from_env()
    if not base.database.exists():
        raise ValueError("Source database must exist")
    entries = json.loads(base.watchlist.read_text())["vn"]
    symbols = {e if isinstance(e, str) else e["symbol"] for e in entries}
    if args.symbol not in symbols:
        raise ValueError("Choose a selected VN ticker")
    args.output.mkdir(parents=True, exist_ok=False)
    main = Repository(base.database)
    originals = main.read("vn", args.symbol, "1m", first, last)
    daily = main.read("vn", args.symbol, "1D", first, last)
    if not originals or not daily:
        raise ValueError("Need populated original minute/daily references")
    settings = replace(
        base,
        database=args.output / "candidate.sqlite3",
        vci_history_fallback=True,
        allow_direct=args.allow_direct,
        proxies=() if args.allow_direct else base.proxies,
        vci_volume_proofs=args.volume_proofs.resolve()
        if args.volume_proofs
        else base.vci_volume_proofs,
    )
    candidate = Repository(settings.database)
    candidate.initialize()
    transport = RecordingTransport(args.output)
    providers = Providers(settings, transport=transport)
    report = {
        "source": "vn",
        "symbol": args.symbol,
        "provider": "vci",
        "start_date": args.start_date,
        "end_date": args.end_date,
        "main_publication": False,
        "crossed_floor": False,
        "complete": False,
        "original_minute_state": main.state("vn", args.symbol, "1m"),
        "original_rows": len(originals),
        "pages": [],
        "original": freeze(
            args.output, "original", json.dumps([asdict(r) for r in originals]).encode()
        ),
        "daily_reference": freeze(
            args.output, "daily", json.dumps([asdict(r) for r in daily]).encode()
        ),
        "limitations": [
            "Staging does not license publication or prove exchange-calendar completeness.",
            "Daily reference can be wrong; discrepancies are retained, never used to scale prices.",
            "The candidate remains one VCI revision; no provider splicing.",
        ],
    }
    path = args.output / "report.json"

    def save():
        path.write_text(json.dumps(report, indent=2) + "\n")

    save()
    before = last + 1
    try:
        for number in range(200):
            first_capture = len(transport.captures)
            page = await providers.page(
                "vn", args.symbol, "1m", before, count=2000, provider="vci", start=first
            )
            if page.cursor is None or page.cursor >= before:
                raise DataError("VCI history ended or failed to advance before requested floor")
            # Reject repeated observations with changed values instead of overwriting
            # a candidate assembled from inconsistent captures.
            if page.rows:
                candidate.record_volume_proofs(page.volume_proofs)
                previous = {
                    r.time: r
                    for r in candidate.read(
                        "vn", args.symbol, "1m", page.rows[0].time, page.rows[-1].time
                    )
                }
                for row in page.rows:
                    if row.time in previous and any(
                        getattr(row, k) != getattr(previous[row.time], k) for k in FIELDS
                    ):
                        raise DataError("VCI changed an observation during pagination")
                candidate.put([replace(r, revision="vci-candidate") for r in page.rows])
            report["pages"].append(
                {
                    "page": number + 1,
                    "before": before,
                    "cursor": page.cursor,
                    "rows": len(page.rows),
                    "captures": transport.captures[first_capture:],
                    "volume_proofs": list(page.volume_proofs),
                }
            )
            report["crossed_floor"] = page.cursor <= first
            save()
            print(
                json.dumps(
                    {
                        "pages": number + 1,
                        "rows": sum(p["rows"] for p in report["pages"]),
                        "cursor": page.cursor,
                    }
                ),
                flush=True,
            )
            if report["crossed_floor"]:
                break
            before = page.cursor
        else:
            raise DataError("VCI candidate exceeded bounded page budget")
        rows = candidate.read("vn", args.symbol, "1m")
        report["candidate_rows"] = len(rows)
        report["comparison"] = compare(
            {"original": [asdict(r) for r in originals], "vci": [asdict(r) for r in rows]}
        )
        observed = {r.time // 86400 * 86400 for r in rows}
        expected = {r.time for r in daily}
        report["daily_dates_without_minutes"] = sorted(expected - observed)
        report["minute_dates_without_daily"] = sorted(observed - expected)
        pairs = report["comparison"]["pairs"]["original:vci"]
        report["preserves_observed_original_timestamps"] = not pairs["only_left"]
        report["complete"] = (
            report["crossed_floor"] and not pairs["only_left"] and not expected - observed
        )
        report["original_changes_during_staging"] = original_changes(
            originals, main.read("vn", args.symbol, "1m", first, last)
        )
        changes = report["original_changes_during_staging"]
        if (
            changes["missing_timestamps"]
            or changes["ohlcv_changed"]
            or changes["provider_revision_changed"]
            or changes["new_timestamps"]
        ):
            report["complete"] = False
        save()
        print(
            json.dumps(
                {
                    k: report[k]
                    for k in (
                        "candidate_rows",
                        "complete",
                        "preserves_observed_original_timestamps",
                        "main_publication",
                    )
                }
            ),
            flush=True,
        )
    except Exception as exc:
        report["error"] = str(exc) if isinstance(exc, DataError) else type(exc).__name__
        report["failed_request_captures"] = transport.captures
        save()
        raise
    finally:
        await providers.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-direct", action="store_true")
    parser.add_argument("--volume-proofs", type=Path)
    asyncio.run(run(parser.parse_args()))
