"""Rehearse complete captured candidates in temporary SQLite and an owned local RustFS prefix."""

import argparse
import asyncio
import hashlib
import json
import tempfile
import uuid
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from aipriceaction_api.archive import Archive, S3Store
from aipriceaction_api.coherent_snapshot import capture, publish
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, cutoff, date_bounds, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.storage import COLUMNS, Repository
from scripts.artifact_budget import ArtifactBudget
from scripts.compare_vn_feeds import FIELDS
from scripts.probe_vn_minute_basis import session
from scripts.replay_vci_candidate_pages import replay_record
from scripts.vci_activation_inputs import replay_page, values
from scripts.vn_daily_volume_evidence import captured


def cleanup_prefix(store, prefix, canonical):
    """Delete only this run's UUID namespace, never canonical or rollback objects."""
    expected = canonical + "/candidates/captured-rehearsal-"
    token = prefix.removeprefix(expected)
    if (
        not prefix.startswith(expected)
        or len(token) != 32
        or any(c not in "0123456789abcdef" for c in token)
    ):
        raise DataError("Refuse cleanup outside the owned rehearsal namespace")
    client, bucket = store.client, store.settings.s3_bucket
    count = 0
    while True:
        result = client.list_objects_v2(Bucket=bucket, Prefix=prefix + "/", MaxKeys=1000)
        keys = [obj["Key"] for obj in result.get("Contents", [])]
        if not keys:
            return count
        if any(not key.startswith(prefix + "/") for key in keys):
            raise DataError("Rehearsal object listing escaped its exact namespace")
        response = client.delete_objects(
            Bucket=bucket, Delete={"Objects": [{"Key": key} for key in keys]}
        )
        if response.get("Errors"):
            raise DataError("Rehearsal object cleanup failed")
        count += len(keys)


def require_unversioned_bucket(store):
    status = store.client.get_bucket_versioning(Bucket=store.settings.s3_bucket).get("Status")
    if status in ("Enabled", "Suspended"):
        raise DataError(
            "Rehearsal requires an unversioned local bucket to remove test objects completely"
        )


async def daily_checks(settings, root, symbol, rows, retained):
    groups = defaultdict(list)
    for row in rows:
        groups[row.time // 86400 * 86400].append(row.record())
    aggregates = {day: session(bars) for day, bars in groups.items()}
    references, identities = {"sqlite_daily": {r.time: r.record() for r in retained}}, {}
    for feed in ("vps", "vndirect", "dnse"):
        path = root / feed / f"{symbol}-1D.json"
        raw_record = path.read_bytes()
        record = json.loads(raw_record)
        if record.get("error"):
            raise DataError("Rehearsal daily witness retains a source error")
        first, before = (
            date_bounds(record["start_date"]),
            date_bounds(record["end_date"], end=True) + 1,
        )
        page = await replay_page(
            settings,
            captured(record, {}),
            feed,
            symbol,
            "1D",
            before,
            min(10000, max(100, (before - first) // 86400 + 1)),
            first,
        )
        if values(page.rows) != [
            (r["time"], *(r[field] for field in FIELDS)) for r in record["rows"]
        ]:
            raise DataError("Native daily replay differs from saved witness observations")
        references[feed] = {row.time: row.record() for row in page.rows}
        identities[feed] = hashlib.sha256(raw_record).hexdigest()
    maximum = 0
    volume_days = []
    for day, derived in sorted(aggregates.items()):
        if day not in references["sqlite_daily"]:
            raise DataError("Replacement lacks a retained daily price witness")
        maximum = max(
            maximum,
            *(abs(derived[field] - references["sqlite_daily"][day][field]) for field in FIELDS[:4]),
        )
        matches = [
            feed
            for feed in ("vps", "vndirect", "dnse")
            if day in references[feed] and derived["volume"] == references[feed][day]["volume"]
        ]
        if len(matches) < 2:
            raise DataError(f"Replacement lacks two exact native daily-volume witnesses at {day}")
        volume_days.append({"day": day, "matching_native_feeds": matches})
    if not aggregates or maximum > 1:
        raise DataError("Replacement fails retained daily price coherence")
    return {
        "observed_days": len(aggregates),
        "maximum_retained_daily_price_difference_vnd": maximum,
        "native_daily_record_sha256": identities,
        "native_volume_witnesses": volume_days,
    }


def seed(repo, main, original, symbol):
    repo.register("vn", symbol)
    with repo.connect() as con:
        columns = tuple(original.state)
        con.execute(
            f"INSERT INTO series({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            tuple(original.state.values()),
        )
        con.executemany(
            f"INSERT INTO candles({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)})",
            [tuple(row.record()[key] for key in COLUMNS) for row in original.hot],
        )
    repo.restore_adoptions(
        [
            r
            for r in main.adoptions()
            if (r["source"], r["symbol"], r["interval"]) == ("vn", symbol, "1m")
        ]
    )
    repo.restore_volume_corrections(main.volume_corrections("vn", symbol, "1m"))
    for obj in original.archives:
        repo.publish_archive(obj)


async def run(args):
    base = Settings.from_env()
    if base.s3_endpoint != "http://127.0.0.1:9100" or base.archive_backend != "s3":
        raise DataError("Rehearsal is restricted to the local RustFS environment")
    raw_extension = (args.extension / "report.json").read_bytes()
    extension = json.loads(raw_extension)
    if (
        not extension["completed"]
        or extension["canonical_publication"]
        or extension["proofs_sha256"] != hashlib.sha256(args.proofs.read_bytes()).hexdigest()
    ):
        raise DataError("Use the completed unpublished extension with its exact proof catalog")
    selected = [item for item in extension["series"] if item["candidate_ready"]]
    if not selected or len(selected) > 4 or len({r["symbol"] for r in selected}) != len(selected):
        raise DataError("Need one to four unique complete extended candidates")
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "canonical_publication": False,
        "passed": False,
        "temporary_storage_removed": False,
        "temporary_s3_prefix_removed": False,
        "extension_sha256": hashlib.sha256(raw_extension).hexdigest(),
        "series": [],
        "limitations": [
            "Storage rehearsal does not license live provider handoff or canonical activation.",
            "Hourly query checks use minute-derived hours; the existing API native hourly series remains separate.",
            "Restoration replays the archive index and recorded hot images; it does not claim a full system restore.",
        ],
    }
    budget = ArtifactBudget(args.output, 1024 * 1024)

    def save():
        budget.write(
            args.output / "report.json",
            (json.dumps(report, indent=2, allow_nan=False) + "\n").encode(),
        )

    prefix = base.s3_prefix + "/candidates/captured-rehearsal-" + uuid.uuid4().hex
    report["temporary_s3_prefix"] = prefix
    save()
    store = S3Store(replace(base, s3_prefix=prefix))
    temporary_root = None
    try:
        require_unversioned_bucket(store)
        with tempfile.TemporaryDirectory(prefix="aipa-captured-rehearsal-") as temporary:
            root = Path(temporary)
            temporary_root = root
            settings = replace(
                base,
                database=root / "candidate.sqlite3",
                cache_dir=root / "cache",
                s3_prefix=prefix,
                vci_history_fallback=True,
                vci_volume_proofs=args.proofs,
            )
            repo = Repository(settings.database)
            repo.initialize()
            archive = Archive(repo, settings, store=store)
            main = Repository(base.database)
            original_archive = Archive(main, replace(base, cache_dir=root / "original-cache"))
            golden_settings = replace(
                settings, database=root / "golden.sqlite3", cache_dir=root / "golden-cache"
            )
            golden = Repository(golden_settings.database)
            golden.initialize()
            golden_history = History(golden, Archive(golden, golden_settings), golden_settings)
            history = History(repo, archive, settings)
            floor = cutoff(settings.minute_years)
            for item in selected:
                symbol = item["symbol"]
                raw = (args.extension / f"{symbol}-record.json").read_bytes()
                record = json.loads(raw)
                replay = await replay_record(settings, symbol, record, retain_rows=True)
                rows = sorted(replay.pop("rows"), key=lambda row: row.time)
                if (
                    not replay["captured_pages_passed"]
                    or len(rows) != item["accepted_rows"]
                    or replay.get("next_cursor") is None
                    or replay["next_cursor"] > date_bounds(item["required_start_date"])
                ):
                    raise DataError(
                        "Extended candidate no longer traverses its recorded original floor"
                    )
                checks = await daily_checks(
                    settings, args.daily, symbol, rows, main.read("vn", symbol, "1D")
                )
                original = capture(main, original_archive, symbol)
                seed(repo, main, original, symbol)
                revision = "captured-vci-rehearsal-" + uuid.uuid4().hex
                replacement = [replace(row, revision=revision) for row in rows]
                activation = publish(
                    repo, archive, capture(repo, archive, symbol), replacement, floor, execute=True
                )
                if not activation["published"] or not activation["manifest_published"]:
                    raise DataError("Rehearsal activation or manifest failed")
                golden.put(replacement)
                if history.read("vn", symbol, "1m") != replacement:
                    raise DataError("Rehearsed whole-series readback differs")
                cases = []
                for interval in ("1m", "15m", "1h"):
                    for start in (None, floor - 7 * 86400):
                        for ma, ema in ((False, False), (True, False), (True, True)):
                            end = floor + 7 * 86400
                            actual = history.query(
                                "vn",
                                symbol,
                                interval,
                                start,
                                end,
                                3000 if interval == "1m" else 200,
                                ma,
                                ema,
                            )
                            expected = golden_history.query(
                                "vn",
                                symbol,
                                interval,
                                start,
                                end,
                                3000 if interval == "1m" else 200,
                                ma,
                                ema,
                            )
                            if (
                                not actual
                                or actual != expected
                                or not min(parse_time(r["time"]) for r in actual)
                                < floor
                                <= max(parse_time(r["time"]) for r in actual)
                            ):
                                raise DataError(
                                    "Rehearsal boundary query failed exact hot/cold comparison"
                                )
                            cases.append(
                                {
                                    "interval": interval,
                                    "start": start,
                                    "ma": ma,
                                    "ema": ema,
                                    "rows": len(actual),
                                }
                            )
                report["series"].append(
                    {
                        "symbol": symbol,
                        "source_record_sha256": hashlib.sha256(raw).hexdigest(),
                        "checks": checks,
                        "activation": activation,
                        "original_cold_rows": sum(len(r) for r in original.cold.values()),
                        "query_cases": cases,
                    }
                )
                save()
                print(
                    json.dumps(
                        {"symbol": symbol, "rehearsed_rows": len(rows), "query_cases": len(cases)}
                    ),
                    flush=True,
                )
            restored_settings = replace(
                settings, database=root / "restored.sqlite3", cache_dir=root / "restored-cache"
            )
            restored = Repository(restored_settings.database)
            restored.initialize()
            restored_archive = Archive(restored, restored_settings, store=store)
            report["restored_archive_objects"] = restored_archive.restore_index()
            for item in report["series"]:
                hot_rows = archive.read(item["activation"]["replacement_hot_image"], refresh=True)
                with restored.connect() as con:
                    con.executemany(
                        f"INSERT INTO candles({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)})",
                        [tuple(row.record()[key] for key in COLUMNS) for row in hot_rows],
                    )
                if History(restored, restored_archive, restored_settings).read(
                    "vn", item["symbol"], "1m"
                ) != history.read("vn", item["symbol"], "1m"):
                    raise DataError("Rehearsed index/hot-image restoration differs")
                current = capture(main, original_archive, item["symbol"])
                if (
                    current.state != item["activation"]["original_state"]
                    or current.archives != item["activation"]["original_archives"]
                ):
                    raise DataError("Canonical metadata changed during rehearsal")
                # Workers may change metadata timestamps; compare actual prices,
                # volumes and source revisions instead of treating that as mutation.
                old = sorted(
                    [
                        row
                        for obj in item["activation"]["before_hot_images"]
                        for row in archive.read(obj, refresh=True)
                    ],
                    key=lambda row: row.time,
                )
                if values(current.hot) != values(old) or [
                    (r.provider, r.revision) for r in current.hot
                ] != [(r.provider, r.revision) for r in old]:
                    raise DataError("Canonical OHLCV or provider basis changed during rehearsal")
            report["passed"] = True
        report["temporary_storage_removed"] = not temporary_root.exists()
    except Exception as exc:
        report["error"] = str(exc) if isinstance(exc, DataError) else type(exc).__name__
        report["temporary_storage_removed"] = temporary_root is None or not temporary_root.exists()
        raise
    finally:
        try:
            report["temporary_s3_objects_deleted"] = cleanup_prefix(store, prefix, base.s3_prefix)
            report["temporary_s3_prefix_removed"] = True
        except Exception as exc:
            report["passed"] = False
            report["cleanup_error"] = str(exc) if isinstance(exc, DataError) else type(exc).__name__
            raise
        finally:
            save()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension", type=Path, required=True)
    parser.add_argument("--daily", type=Path, required=True)
    parser.add_argument("--proofs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
