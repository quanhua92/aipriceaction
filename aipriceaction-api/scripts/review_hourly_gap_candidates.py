"""Preserve source-bound hourly gap candidates without publishing or copying live SQLite."""

import argparse
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import duckdb

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, date_bounds, parse_time
from aipriceaction_api.storage import COLUMNS, Repository
from scripts.artifact_budget import ArtifactBudget
from scripts.compare_vn_feeds import FEEDS, FIELDS
from scripts.probe_vn_minute_basis import session
from scripts.vn_daily_volume_evidence import captured


def legacy_candidate(root, path, symbol, day):
    record_raw = path.read_bytes()
    record = json.loads(record_raw)
    if record.get("error") or record.get("http_status") != 200:
        raise DataError("No successful legacy hourly candidate")
    if (record["start_date"], record["end_date"]) != (day, day):
        raise DataError("Legacy hourly candidate window differs")
    capture_path = Path(record["capture"]).resolve()
    if not capture_path.is_relative_to(root.resolve()):
        raise DataError("Legacy capture outside selected audit")
    raw = capture_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if not capture_path.stem.endswith("-" + digest):
        raise DataError("Legacy hourly capture checksum differs")
    payload = json.loads(raw)
    if set(payload) != {symbol} or not isinstance(payload[symbol], list):
        raise DataError("Legacy hourly capture symbol/envelope differs")
    rows = []
    for row in payload[symbol]:
        if row.get("symbol") != symbol:
            raise DataError("Legacy hourly row symbol differs")
        rows.append({"time": parse_time(row["time"]), **{key: row[key] for key in FIELDS}})
    first = date_bounds(day)
    if (
        not rows
        or len(rows) >= 10000
        or len({row["time"] for row in rows}) != len(rows)
        or any(not first <= row["time"] < first + 86400 or row["time"] % 60 for row in rows)
        or [row["time"] for row in rows] != sorted(row["time"] for row in rows)
    ):
        raise DataError("Ambiguous or out-of-window legacy hourly rows")
    if json.dumps(rows, sort_keys=True, allow_nan=False) != json.dumps(
        record["rows"], sort_keys=True, allow_nan=False
    ):
        raise DataError("Legacy hourly rows differ from raw capture")
    candles = [
        Candle(
            "vn", symbol, "1h", **row, provider="legacy-api", revision="candidate-" + digest
        ).validate()
        for row in rows
    ]
    return candles, {
        "record_path": str(path),
        "record_sha256": hashlib.sha256(record_raw).hexdigest(),
        "raw_capture_sha256": digest,
        "raw_legacy_replayed": True,
    }


def round_trip(candles):
    with tempfile.TemporaryDirectory(prefix="aipa-hourly-gap-candidates-") as temporary:
        root = Path(temporary)
        settings = replace(
            Settings(),
            database=root / "candidate.sqlite3",
            archive_backend="filesystem",
            object_dir=root / "objects",
            cache_dir=root / "cache",
        )
        repo = Repository(settings.database)
        repo.initialize()
        records = [tuple(row.record()[key] for key in COLUMNS) for row in candles]
        # Import candidate metadata exactly; ordinary live put() intentionally
        # stamps new ingestion times. This database is an isolated small fixture.
        with repo.connect() as con:
            con.executemany(
                f"INSERT INTO candles({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)})",
                records,
            )
            saved = con.execute(
                f"SELECT {','.join(COLUMNS)} FROM candles ORDER BY symbol,time"
            ).fetchall()
            if [tuple(row) for row in saved] != records:
                raise DataError("Hourly candidate SQLite round trip differs")
        parquet = root / "candidate.parquet"
        Archive(repo, settings)._write(candles, parquet)
        with duckdb.connect(config={"threads": 1}) as con:
            saved = con.execute(
                "SELECT * FROM read_parquet(?) ORDER BY symbol,time", [str(parquet)]
            ).fetchall()
            if saved != records:
                raise DataError("Hourly candidate Parquet round trip differs")
    if root.exists():
        raise DataError("Hourly candidate temporary fixtures remain")


def run(args):
    settings = Settings.from_env()
    root = args.audit.resolve()
    candles, controls, unavailable, seen = [], [], [], set()
    with closing(
        sqlite3.connect(settings.database.resolve().as_uri() + "?mode=ro", uri=True)
    ) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        for directory in sorted(path for path in root.iterdir() if path.is_dir()):
            report_path = directory / "report.json"
            if not report_path.exists():
                continue
            report = json.loads(report_path.read_text())
            day = report["intraday_start"]
            if (
                report["intervals"] != ["1h"]
                or report["feeds"] != list(FEEDS)
                or report["end_date"] != day
                or report["canonical_publication"] is not False
                or report["requests"] != len(report["symbols"]) * 4
            ):
                raise DataError("Expected completed exact-day hourly comparison")
            for symbol in report["symbols"]:
                if (symbol, day) in seen or len(seen) >= 100:
                    raise DataError("Duplicate or excessive hourly candidate scope")
                seen.add((symbol, day))
                native = {}
                for feed in FEEDS[:3]:
                    path = directory / feed / f"{symbol}-1h.json"
                    raw = path.read_bytes()
                    record = json.loads(raw)
                    if (record["start_date"], record["end_date"]) != (day, day):
                        raise DataError("Native hourly control window differs")
                    if any(
                        not Path(item["path"])
                        .resolve()
                        .is_relative_to((directory / feed).resolve())
                        for item in record["captures"]
                    ):
                        raise DataError("Native hourly capture outside its source directory")
                    captured(record, {})
                    native[feed] = {
                        "record_sha256": hashlib.sha256(raw).hexdigest(),
                        "observed_rows": len(record.get("rows", [])),
                        "error": record.get("error"),
                    }
                legacy_path = directory / "legacy" / f"{symbol}-1h.json"
                record = json.loads(legacy_path.read_text())
                if record.get("error") or any(item["observed_rows"] for item in native.values()):
                    unavailable.append(
                        {
                            "symbol": symbol,
                            "date": day,
                            "native_controls": native,
                            "legacy_error": record.get("error"),
                            "candidate_preserved": False,
                        }
                    )
                    continue
                values, identity = legacy_candidate(root, legacy_path, symbol, day)
                first = date_bounds(day)
                if con.execute(
                    "SELECT 1 FROM candles WHERE source='vn' AND symbol=? AND interval='1h' AND time>=? AND time<? LIMIT 1",
                    (symbol, first, first + 86400),
                ).fetchone():
                    raise DataError("Candidate session already contains live hourly rows")
                daily = con.execute(
                    "SELECT open,high,low,close,volume,provider,revision FROM candles WHERE source='vn' AND symbol=? AND interval='1D' AND time=?",
                    (symbol, first),
                ).fetchone()
                if not daily:
                    raise DataError("Missing original daily session control")
                derived = session([row.record() for row in values])
                controls.append(
                    {
                        "symbol": symbol,
                        "date": day,
                        **identity,
                        "rows": len(values),
                        "native_controls": native,
                        "sqlite_daily": dict(daily),
                        "candidate_hourly_aggregate": derived,
                        "maximum_price_difference_vnd": max(
                            abs(derived[key] - daily[key]) for key in FIELDS[:4]
                        ),
                        "volume_difference": derived["volume"] - daily["volume"],
                        "publication_license": False,
                    }
                )
                candles.extend(values)
    candles.sort(key=lambda row: (row.symbol, row.time))
    round_trip(candles)
    args.output.mkdir(parents=True, exist_ok=False)
    budget = ArtifactBudget(args.output, 128 * 1024)
    report = {
        "candidate_only": True,
        "canonical_publication": False,
        "recovered_hourly_rows": len(candles),
        "controls": controls,
        "unavailable": unavailable,
        "sqlite_round_trip_exact": True,
        "parquet_round_trip_exact": True,
        "temporary_fixtures_removed": True,
        "limitations": [
            "Daily price and volume differences remain unresolved; no common price basis is inferred.",
            "Zero or tiny daily volumes cannot authorize excluding dates.",
            "Current candles, archives, source selection and pending jobs remain unchanged.",
        ],
    }
    budget.write(
        args.output / "candidates.json",
        (
            json.dumps(
                {
                    "candidate_only": True,
                    "canonical_publication": False,
                    "rows": [row.record() for row in candles],
                },
                indent=2,
            )
            + "\n"
        ).encode(),
    )
    budget.write(args.output / "report.json", (json.dumps(report, indent=2) + "\n").encode())
    print(
        json.dumps(
            {
                "recovered_rows": len(candles),
                "candidate_sessions": len(controls),
                "unavailable": len(unavailable),
                "bytes": budget.used,
            }
        )
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
