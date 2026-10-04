"""Rehearse FPT/TPB corrected minute snapshots in isolated SQLite and RustFS.

This verifies storage and query behavior, not permission to publish a correction.
It requires previously captured candidates, volume proposals and archive audits.
"""

import argparse
import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, cutoff, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository
from scripts.compare_vn_feeds import same
from scripts.stage_yahoo_daily_history import freeze


def load_proposal(root, symbol):
    original = Repository(root / "candidate.sqlite3").read("vn", symbol, "1m")
    proposed = Repository(root / "volume-repair-proposal/candidate.sqlite3").read(
        "vn", symbol, "1m"
    )
    receipt = json.loads((root / "volume-repair-proposal/receipt.json").read_text())
    if not original or len(original) != len(proposed) or receipt["main_publication"]:
        raise DataError("Expected an isolated, populated volume proposal")
    changes = []
    for before, after in zip(original, proposed, strict=True):
        if (before.source, before.symbol, before.interval, before.time) != (
            after.source,
            after.symbol,
            after.interval,
            after.time,
        ) or (before.time, before.open, before.high, before.low, before.close) != (
            after.time,
            after.open,
            after.high,
            after.low,
            after.close,
        ):
            raise DataError("Volume proposal changed prices or timestamps")
        if before.volume != after.volume:
            changes.append((before, after))
    if len(changes) != 1 or not same(asdict(changes[0][0]), receipt["original"]):
        raise DataError("Volume proposal does not match its original receipt")
    if not same(asdict(changes[0][1]), receipt["proposed"]):
        raise DataError("Volume proposal does not match its corrected receipt")
    for row, key in ((changes[0][0], "original"), (changes[0][1], "proposed")):
        if any(
            getattr(row, field) != receipt[key][field]
            for field in ("source", "symbol", "interval", "time")
        ):
            raise DataError("Volume proposal receipt has the wrong candle identity")
    return proposed


def run(args):
    base = Settings.from_env()
    if base.s3_endpoint != "http://127.0.0.1:9100" or base.archive_backend != "s3":
        raise ValueError("This rehearsal requires local RustFS")
    args.output.mkdir(parents=True, exist_ok=False)
    settings = replace(
        base,
        database=args.output / "candidate.sqlite3",
        cache_dir=args.output / "cache",
        s3_prefix=base.s3_prefix + "/candidates/vci-minute-" + uuid.uuid4().hex,
    )
    repo = Repository(settings.database)
    repo.initialize()
    archived = Archive(repo, settings)
    history = History(repo, archived, settings)
    golden = Repository(args.output / "all-sqlite.sqlite3")
    golden.initialize()
    golden_history = History(golden, Archive(golden, settings), settings)
    audit = json.loads((args.archive_audit / "report.json").read_text())
    floor = cutoff(settings.minute_years)
    report = {
        "main_publication": False,
        "database": str(repo.path),
        "prefix": settings.s3_prefix,
        "retention_floor": floor,
        "symbols": [],
        "passed": False,
        "limitations": [
            "Storage rehearsal does not license canonical correction or provider handoff.",
            "Observed timestamp preservation does not prove a complete exchange calendar.",
            "Only FPT/TPB and their captured partitions are covered.",
        ],
    }
    path = args.output / "report.json"

    def save():
        path.write_text(json.dumps(report, indent=2) + "\n")

    save()
    main = Repository(base.database)
    main_archive = Archive(main, base)
    for symbol, root in (("FPT", args.fpt), ("TPB", args.tpb)):
        rows = {r.time: r for r in load_proposal(root, symbol)}
        for item in audit["objects"]:
            if item["archive"]["symbol"] != symbol or "candidate" not in item:
                continue
            raw_path = Path(item["candidate"]["path"])
            raw = raw_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != item["candidate"]["sha256"]:
                raise DataError("Archive candidate capture changed")
            for record in json.loads(raw):
                row = Candle(**record)
                if row.time in rows and not same(asdict(rows[row.time]), record):
                    raise DataError("Archive and recent candidates disagree")
                rows.setdefault(row.time, row)
        originals = main.read("vn", symbol, "1m")
        objects = [o for o in main.archives("vn", symbol, "1m") if o["status"] == "published"]
        for obj in objects:
            originals.extend(main_archive.read(obj))
        if {r.time for r in originals} - rows.keys():
            raise DataError("Rehearsal drops an existing observed timestamp")
        original_image = freeze(
            args.output, "original", json.dumps([asdict(r) for r in originals]).encode()
        )
        revision = "vci-storage-rehearsal-" + uuid.uuid4().hex
        values = [replace(rows[t], revision=revision) for t in sorted(rows)]
        hot = [r for r in values if r.time >= floor]
        repo.put(hot)
        golden.put(values)
        partitions = defaultdict(list)
        for row in values:
            if row.time < floor:
                partitions[datetime.fromtimestamp(row.time, UTC).strftime("%Y-%m")].append(row)
        published = [archived.publish(part, require_current=True) for part in partitions.values()]
        restored = history.read("vn", symbol, "1m")
        if len(restored) != len(values) or any(
            (a.time, a.provider, a.revision) != (b.time, b.provider, b.revision)
            or not same(asdict(a), asdict(b))
            for a, b in zip(values, restored, strict=True)
        ):
            raise DataError("Whole-series SQLite/S3 readback differs")
        cases = []
        for interval in ("1m", "15m", "1h"):
            limit = 3000 if interval == "1m" else 200
            for start, end in ((None, floor + 7 * 86400), (floor - 7 * 86400, floor + 7 * 86400)):
                for ma, ema in ((False, False), (True, False), (True, True)):
                    actual = history.query("vn", symbol, interval, start, end, limit, ma, ema)
                    expected = golden_history.query(
                        "vn", symbol, interval, start, end, limit, ma, ema
                    )
                    if not actual or actual != expected:
                        raise DataError("Retention-boundary query differs from all-SQLite snapshot")
                    times = [parse_time(row["time"]) for row in actual]
                    spans_boundary = min(times) < floor <= max(times)
                    if start is not None and not spans_boundary:
                        raise DataError(
                            "Bounded query did not exercise both cold and retained rows"
                        )
                    cases.append(
                        {
                            "interval": interval,
                            "start": start,
                            "end": end,
                            "ma": ma,
                            "ema": ema,
                            "limit": limit,
                            "spans_retention_boundary": spans_boundary,
                            "rows": len(actual),
                        }
                    )
        report["symbols"].append(
            {
                "symbol": symbol,
                "original": original_image,
                "rows": len(values),
                "hot_rows": len(hot),
                "cold_rows": len(values) - len(hot),
                "archives": published,
                "whole_series_readback": True,
                "query_cases": cases,
                "original_archive_metadata_unchanged": all(
                    o in main.archives("vn", symbol, "1m") for o in objects
                ),
            }
        )
        save()
        print(
            json.dumps(
                {
                    "symbol": symbol,
                    "rows": len(values),
                    "hot_rows": len(hot),
                    "objects": len(published),
                    "query_cases": len(cases),
                }
            ),
            flush=True,
        )
    report["passed"] = True
    save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fpt", type=Path, required=True)
    parser.add_argument("--tpb", type=Path, required=True)
    parser.add_argument("--archive-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
