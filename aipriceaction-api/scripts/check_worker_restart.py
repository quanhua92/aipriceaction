"""Verify consistent SQLite images captured before and after a worker restart.

Reads both images without initializing or modifying either database. Independent
workers may advance provisional/native records; frozen records and both disputed
VN indices must retain exact versions. This does not prove multi-day uptime.
"""

import argparse
import json
import sqlite3
from pathlib import Path

from aipriceaction_api.archive import sha256

NATIVE = {"vn": ("vps", "vndirect", "dnse"), "crypto": ("binance",), "yahoo": ("yahoo",)}


def verify(before, after, sources, checkpoint_at_ns, excluded_symbols=()):
    before, after = Path(before).resolve(), Path(after).resolve()
    if before == after or not before.is_file() or not after.is_file():
        raise ValueError("Before and after must be distinct existing SQLite images")
    if not sources or set(sources) - NATIVE.keys() or checkpoint_at_ns < 1:
        raise ValueError("Choose native worker sources and a positive restart checkpoint")
    report = {
        "passed": False,
        "checkpoint_at_ns": checkpoint_at_ns,
        "sources": {},
        "protected": {},
        "scope": "Previously verified completed values; exact frozen/index versions and archive metadata. Provisional/native update timestamps may advance.",
    }
    with sqlite3.connect(after.as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("ATTACH DATABASE ? AS original", (before.as_uri() + "?mode=ro",))
        report["integrity"] = {
            name: con.execute(f"PRAGMA {name}.quick_check").fetchone()[0]
            for name in ("main", "original")
        }
        for source in sources:
            changed = con.execute(
                """SELECT count(*) FROM original.candles b
                JOIN original.source_checks s USING(source,symbol,interval)
                LEFT JOIN candles a USING(source,symbol,interval,time)
                WHERE b.source=? AND b.time<s.completed_before AND
                (a.time IS NULL OR a.open IS NOT b.open OR a.high IS NOT b.high
                OR a.low IS NOT b.low OR a.close IS NOT b.close OR a.volume IS NOT b.volume
                OR a.provider IS NOT b.provider OR a.revision IS NOT b.revision)""",
                (source,),
            ).fetchone()[0]
            expected = [
                row[0]
                for row in con.execute(
                    "SELECT symbol,provider FROM original.series WHERE source=? AND interval='1m' AND status='ready'",
                    (source,),
                )
                if row[1] in NATIVE[source] and row[0] not in excluded_symbols
            ]
            checks = {
                row["symbol"]: dict(row)
                for row in con.execute(
                    """SELECT symbol,outcome,successful_at_ns,provider,revision
                    FROM source_checks WHERE source=? AND interval='1m'
                    AND successful_at_ns>?""",
                    (source, checkpoint_at_ns),
                )
            }
            missing = []
            for symbol in expected:
                state = con.execute(
                    "SELECT provider,revision FROM original.series WHERE source=? AND symbol=? AND interval='1m'",
                    (source, symbol),
                ).fetchone()
                check = checks.get(symbol)
                if not check or (check["provider"], check["revision"]) != tuple(state):
                    missing.append(symbol)
            report["sources"][source] = {
                "formerly_completed_records_changed": changed,
                "expected_minute_symbols": sorted(expected),
                "missing_fresh_minute_checks": sorted(missing),
                "fresh_minute_checks": [checks[s] for s in sorted(expected) if s in checks],
            }
        scopes = {
            "archives": ("archives", "1"),
            "snapshot_adoptions": ("snapshot_adoptions", "1"),
            "legacy_imports": ("legacy_imports", "1"),
            "index_candle_versions": (
                "candles",
                "source='vn' AND symbol IN ('VNINDEX','VN30')",
            ),
            "frozen_candle_versions": (
                "candles",
                "provider IN ('legacy-api','legacy-s3','legacy')",
            ),
        }
        for name, (table, where) in scopes.items():
            report["protected"][name] = all(
                not con.execute(
                    f"SELECT * FROM {left} WHERE {where} EXCEPT SELECT * FROM {right} WHERE {where} LIMIT 1"
                ).fetchone()
                for left, right in ((table, f"original.{table}"), (f"original.{table}", table))
            )
        for source in sources:
            report["protected"][f"{source}_series"] = all(
                not con.execute(
                    f"SELECT * FROM {left} WHERE source=? EXCEPT SELECT * FROM {right} WHERE source=? LIMIT 1",
                    (source, source),
                ).fetchone()
                for left, right in (("series", "original.series"), ("original.series", "series"))
            )
    report["before_sha256"], report["after_sha256"] = sha256(before), sha256(after)
    report["passed"] = (
        all(value == "ok" for value in report["integrity"].values())
        and all(report["protected"].values())
        and all(
            not item["formerly_completed_records_changed"]
            and not item["missing_fresh_minute_checks"]
            for item in report["sources"].values()
        )
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--source", choices=tuple(NATIVE), action="append", required=True)
    parser.add_argument("--checkpoint-at-ns", type=int, required=True)
    parser.add_argument("--exclude-symbol", action="append", default=[])
    args = parser.parse_args()
    report = verify(
        args.before,
        args.after,
        args.source,
        args.checkpoint_at_ns,
        [symbol.upper() for symbol in args.exclude_symbol],
    )
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
