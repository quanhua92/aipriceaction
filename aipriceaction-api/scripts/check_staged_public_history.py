"""Compare an isolated public snapshot with retained records and indicator reads.

Raw retained inventory may contain incompatible revisions. It is compared only
as evidence; normal History reads still enforce adjustment compatibility. This
command never publishes or licenses a replacement.
"""

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import date_bounds
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository
from scripts.check_crypto_daily_history import compare


def inventory(repo, archive, source, symbol, interval):
    """Inventory raw versions using the reader's local/latest-object precedence."""
    local = repo.read(source, symbol, interval)
    local_times = {row.time for row in local}
    merged = {row.time: row for row in local}
    for obj in repo.archives(source, symbol, interval):
        for row in archive.read(obj):
            current = merged.get(row.time)
            if current is None or (
                row.time not in local_times and row.updated_at > current.updated_at
            ):
                merged[row.time] = row
    return sorted(merged.values(), key=lambda row: row.time)


def digest(rows):
    raw = json.dumps([row.__dict__ for row in rows], sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def run(args):
    settings = Settings.from_env()
    main = Repository(settings.database)
    if args.candidate.resolve() == settings.database.resolve():
        raise ValueError("Candidate must be separate from the canonical database")
    if not args.candidate.is_file():
        raise ValueError("Candidate database does not exist")
    args.output.mkdir(parents=True, exist_ok=False)
    config = replace(
        settings,
        database=args.candidate,
        archive_backend="filesystem",
        object_dir=args.objects,
        cache_dir=args.output / "cache",
    )
    candidate = Repository(config.database)
    canonical_archive = Archive(main, settings)
    candidate_archive = Archive(candidate, config)
    report = {
        "passed": False,
        "read_only_canonical": True,
        "publication_authorized": False,
        "canonical_epoch_before": main.epoch(),
        "queries": [],
    }
    try:
        original = inventory(main, canonical_archive, args.source, args.symbol, args.interval)
        rows = History(candidate, candidate_archive, config).read(
            args.source, args.symbol, args.interval
        )
        report.update(
            canonical_inventory_sha256=digest(original),
            candidate_inventory_sha256=digest(rows),
            candidate_rows=len(rows),
            candidate_local_rows=len(candidate.read(args.source, args.symbol, args.interval)),
            candidate_archive_objects=len(
                candidate.archives(args.source, args.symbol, args.interval)
            ),
            candidate_revisions=sorted({row.revision for row in rows}),
            comparison=compare(original, rows),
            new_timestamps=sorted({row.time for row in rows} - {row.time for row in original}),
        )
        for interval in args.query_intervals:
            for ema in (False, True):
                query = {"interval": interval, "ema": ema, "end_date": args.end_date}
                for name, repo, archive, cfg in (
                    ("canonical", main, canonical_archive, settings),
                    ("candidate", candidate, candidate_archive, config),
                ):
                    try:
                        result = History(repo, archive, cfg).query(
                            args.source,
                            args.symbol,
                            interval,
                            end=date_bounds(args.end_date, end=True),
                            limit=20,
                            ma=True,
                            ema=ema,
                        )
                        query[name] = {"rows": len(result), "values": result}
                    except Exception as exc:
                        query[name] = {"error": str(exc), "error_type": type(exc).__name__}
                report["queries"].append(query)
        original_after = inventory(main, canonical_archive, args.source, args.symbol, args.interval)
        report["canonical_epoch_after"] = main.epoch()
        assert report["canonical_epoch_after"] == report["canonical_epoch_before"]
        assert digest(original_after) == report["canonical_inventory_sha256"]
        assert rows and len(report["candidate_revisions"]) == 1
        assert not report["comparison"]["missing"]
        assert all(query["candidate"].get("rows") == 20 for query in report["queries"])
        report["passed"] = True
    except Exception as exc:
        report["error"] = str(exc)
        report["error_type"] = type(exc).__name__
        raise
    finally:
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "candidate_rows": report["candidate_rows"],
                "missing": len(report["comparison"]["missing"]),
                "changed": len(report["comparison"]["changed"]),
                "new": len(report["new_timestamps"]),
                "queries": len(report["queries"]),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--objects", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", default="vn")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--query-intervals", nargs="+", default=["1m", "15m"])
    parser.add_argument("--end-date", required=True)
    run(parser.parse_args())
