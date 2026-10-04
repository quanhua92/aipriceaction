"""Verify activated captured candles through live HTTP and temporary full archive-index restoration."""

import argparse
import asyncio
import json
import sqlite3
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, cutoff, date_bounds
from aipriceaction_api.history import History
from aipriceaction_api.storage import COLUMNS, Repository
from scripts.artifact_budget import ArtifactBudget
from scripts.replay_vci_candidate_pages import replay_record
from scripts.vci_activation_inputs import values


def verify_http(client, reference, symbol, floor):
    start, end = [
        datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%d")
        for stamp in (floor - 7 * 86400, floor + 7 * 86400)
    ]
    cases = []
    for interval in ("1m", "15m"):
        for ma, ema in ((False, False), (True, False), (True, True)):
            limit = 3000 if interval == "1m" else 200
            response = client.get(
                "/tickers",
                params={
                    "symbol": symbol,
                    "mode": "vn",
                    "interval": interval,
                    "start_date": start,
                    "end_date": end,
                    "limit": limit,
                    "ma": str(ma).lower(),
                    "ema": str(ema).lower(),
                    "cache": "false",
                    "snap": "false",
                },
            )
            expected = reference.query(
                "vn",
                symbol,
                interval,
                date_bounds(start),
                date_bounds(end, end=True),
                limit,
                ma,
                ema,
            )
            if response.status_code != 200 or not expected or response.json() != {symbol: expected}:
                raise DataError("Live HTTP differs from complete captured OHLCV reference")
            cases.append({"interval": interval, "ma": ma, "ema": ema, "rows": len(expected)})
    return cases


async def run(args):
    base = Settings.from_env()
    if (
        base.archive_backend != "s3"
        or base.s3_endpoint != "http://127.0.0.1:9100"
        or args.base_url != "http://127.0.0.1:3001"
    ):
        raise DataError("Verify only the local API and RustFS environment")
    activation = json.loads(args.activation.read_text())
    if (
        not activation["passed"]
        or not activation["execute"]
        or any(
            not row.get("activation", {}).get("published") or not row.get("handoff")
            for row in activation["symbols"]
        )
    ):
        raise DataError("Use a completed local activation with licensed handoffs")
    with sqlite3.connect(
        Path(activation["backup"]).resolve().as_uri() + "?mode=ro", uri=True
    ) as con:
        if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise DataError("Operational rollback backup failed structural check")
    args.output.mkdir(parents=True, exist_ok=False)
    budget = ArtifactBudget(args.output, 256 * 1024)
    report = {
        "completed": False,
        "canonical_writes": False,
        "series": [],
        "rollback_backup_quick_check": "ok",
        "temporary_storage_removed": False,
    }

    def save():
        budget.write(args.output / "report.json", (json.dumps(report, indent=2) + "\n").encode())

    save()
    temporary_root = None
    try:
        with tempfile.TemporaryDirectory(prefix="aipa-captured-activation-check-") as temporary:
            temporary_root = root = Path(temporary)
            settings = replace(
                base,
                database=root / "restored.sqlite3",
                cache_dir=root / "cache",
                vci_history_fallback=True,
                vci_volume_proofs=Path(activation["active_volume_proofs_file"]),
            )
            restored = Repository(settings.database)
            restored.initialize()
            archive = Archive(restored, settings)
            report["restored_archive_objects"] = archive.restore_index()
            main = Repository(base.database)
            main_history = History(
                main, Archive(main, replace(base, cache_dir=root / "main-cache")), base
            )
            with httpx.Client(base_url=args.base_url, timeout=60) as client:
                for entry in activation["symbols"]:
                    symbol = entry["symbol"]
                    replay = await replay_record(
                        settings,
                        symbol,
                        json.loads((args.extension / f"{symbol}-record.json").read_text()),
                        retain_rows=True,
                    )
                    source = sorted(replay.pop("rows"), key=lambda row: row.time)
                    if (
                        not replay["captured_pages_passed"]
                        or len(source) != entry["activation"]["rows"]
                    ):
                        raise DataError("Activated capture replay changed")
                    current = main_history.read("vn", symbol, "1m")
                    if values(current) != values(source) or {r.revision for r in current} != {
                        entry["activation"]["revision"]
                    }:
                        raise DataError("Canonical whole-series data differs from captured source")
                    hot = archive.read(entry["activation"]["replacement_hot_image"], refresh=True)
                    with restored.connect() as con:
                        con.executemany(
                            f"INSERT INTO candles({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)})",
                            [tuple(row.record()[key] for key in COLUMNS) for row in hot],
                        )
                    if values(
                        History(restored, archive, settings).read("vn", symbol, "1m")
                    ) != values(source):
                        raise DataError(
                            "Full-index and hot-image restoration differs from captured source"
                        )
                    golden_settings = replace(
                        settings,
                        database=root / f"{symbol}-reference.sqlite3",
                        cache_dir=root / f"{symbol}-reference-cache",
                    )
                    golden = Repository(golden_settings.database)
                    golden.initialize()
                    golden.put(
                        [replace(row, revision=entry["activation"]["revision"]) for row in source]
                    )
                    reference = History(golden, Archive(golden, golden_settings), golden_settings)
                    cases = verify_http(client, reference, symbol, cutoff(settings.minute_years))
                    report["series"].append(
                        {
                            "symbol": symbol,
                            "source_rows": len(source),
                            "hot_rows": len(hot),
                            "cold_rows": len(source) - len(hot),
                            "http_cases": cases,
                            "full_series_source_and_restoration_match": True,
                        }
                    )
                    save()
            report["completed"] = True
        report["temporary_storage_removed"] = not temporary_root.exists()
    except Exception as exc:
        report["error"] = str(exc) if isinstance(exc, DataError) else type(exc).__name__
        report["temporary_storage_removed"] = temporary_root is None or not temporary_root.exists()
        raise
    finally:
        save()
    print(
        json.dumps(
            {
                "completed": report["completed"],
                "restored_archive_objects": report["restored_archive_objects"],
                "temporary_storage_removed": report["temporary_storage_removed"],
            }
        )
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activation", type=Path, required=True)
    parser.add_argument("--extension", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:3001")
    asyncio.run(run(parser.parse_args()))
