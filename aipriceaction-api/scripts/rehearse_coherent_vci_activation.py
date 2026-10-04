"""Rehearse atomic FPT/TPB activation using real original/corrected snapshots.

All SQL mutations and manifest writes target an isolated database/RustFS prefix.
This does not license canonical publication, market accuracy or live refresh.
"""

import argparse
import json
import uuid
from dataclasses import replace
from pathlib import Path

from aipriceaction_api.archive import Archive
from aipriceaction_api.coherent_snapshot import capture, publish
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, cutoff, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.storage import COLUMNS, Repository
from scripts.compare_vn_feeds import same


def run(args):
    base = Settings.from_env()
    if base.s3_endpoint != "http://127.0.0.1:9100" or base.archive_backend != "s3":
        raise ValueError("This isolated activation rehearsal requires local RustFS")
    args.output.mkdir(parents=True, exist_ok=False)
    settings = replace(
        base,
        database=args.output / "candidate.sqlite3",
        cache_dir=args.output / "cache",
        s3_prefix=base.s3_prefix + "/candidates/coherent-vci-" + uuid.uuid4().hex,
    )
    repo = Repository(settings.database)
    repo.initialize()
    archive = Archive(repo, settings)
    main = Repository(base.database)
    original_archive = Archive(main, base)
    golden = Repository(args.output / "all-sqlite.sqlite3")
    golden.initialize()
    history = History(repo, archive, settings)
    golden_history = History(golden, Archive(golden, settings), settings)
    audit = json.loads((args.archive_audit / "report.json").read_text())
    floor = cutoff(settings.minute_years)
    report = {
        "main_publication": False,
        "candidate_database": str(repo.path),
        "prefix": settings.s3_prefix,
        "symbols": [],
        "passed": False,
    }
    path = args.output / "report.json"

    def save():
        path.write_text(json.dumps(report, indent=2) + "\n")

    save()
    for symbol, root in (("FPT", args.fpt), ("TPB", args.tpb)):
        source_report = json.loads((root / "report.json").read_text())
        if not source_report["complete"] or source_report["main_publication"]:
            raise DataError("Use a completed isolated proof-backed candidate")
        original = capture(main, original_archive, symbol)
        # Copy exact original write versions; ordinary put() would update them
        # and invalidate the source's existing frozen-snapshot certificates.
        repo.register("vn", symbol)
        with repo.connect() as con:
            columns = tuple(original.state)
            con.execute(
                f"INSERT INTO series({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(original.state.values()),
            )
            con.executemany(
                f"INSERT INTO candles({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)})",
                [tuple(r.record()[key] for key in COLUMNS) for r in original.hot],
            )
        repo.restore_adoptions(
            [
                r
                for r in main.adoptions()
                if (r["source"], r["symbol"], r["interval"]) == ("vn", symbol, "1m")
            ]
        )
        for obj in original.archives:
            repo.publish_archive(obj)
        rows = {r.time: r for r in Repository(root / "candidate.sqlite3").read("vn", symbol, "1m")}
        for item in audit["objects"]:
            if item["archive"]["symbol"] != symbol or "candidate" not in item:
                continue
            import hashlib

            raw = Path(item["candidate"]["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != item["candidate"]["sha256"]:
                raise DataError("Archived VCI witness checksum changed")
            for record in json.loads(raw):
                if record["time"] in rows and not same(rows[record["time"]].record(), record):
                    raise DataError("Retained and archived VCI witnesses disagree")
                rows.setdefault(record["time"], Candle(**record))
        revision = "coherent-vci-rehearsal-" + uuid.uuid4().hex
        replacement = [replace(rows[t], revision=revision) for t in sorted(rows)]
        frozen = capture(repo, archive, symbol)
        result = publish(repo, archive, frozen, replacement, floor, execute=True)
        if not result["published"] or not result["manifest_published"]:
            raise DataError("Isolated activation or remote checkpoint failed")
        golden.put(replacement)
        actual = history.read("vn", symbol, "1m")
        if actual != replacement:
            raise DataError("Activated whole-series readback differs")
        cases = []
        for interval in ("1m", "15m", "1h"):
            limit = 3000 if interval == "1m" else 200
            for start in (None, floor - 7 * 86400):
                for ma, ema in ((False, False), (True, False), (True, True)):
                    end = floor + 7 * 86400
                    actual = history.query("vn", symbol, interval, start, end, limit, ma, ema)
                    expected = golden_history.query(
                        "vn", symbol, interval, start, end, limit, ma, ema
                    )
                    if not actual or actual != expected:
                        raise DataError("Activated query differs from all-SQLite reference")
                    times = [parse_time(r["time"]) for r in actual]
                    if not min(times) < floor <= max(times):
                        raise DataError("Activation query did not span both storage tiers")
                    cases.append(
                        {
                            "interval": interval,
                            "start": start,
                            "ma": ma,
                            "ema": ema,
                            "rows": len(actual),
                        }
                    )
        current = capture(main, original_archive, symbol)
        if current.state != original.state or current.archives != original.archives:
            raise DataError("Main state/archive metadata changed during rehearsal")
        originals = {r.time: r for r in original.hot}
        if any(
            r.time not in originals
            or not same(r.record(), originals[r.time].record())
            or (r.provider, r.revision) != (originals[r.time].provider, originals[r.time].revision)
            for r in current.hot
        ) or len(current.hot) != len(original.hot):
            raise DataError("Main source OHLCV or timestamps changed during rehearsal")
        report["symbols"].append(
            {
                "symbol": symbol,
                "activation": result,
                "query_cases": cases,
                "main_ohlcv_basis_and_archive_metadata_unchanged": True,
            }
        )
        save()
        print(
            json.dumps(
                {
                    "symbol": symbol,
                    "activated_rows": len(replacement),
                    "query_cases": len(cases),
                    "main_publication": False,
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
