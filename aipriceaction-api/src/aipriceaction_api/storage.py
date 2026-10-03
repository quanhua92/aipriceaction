"""SQLite transactions are short; each operation owns its connection/thread."""

import hashlib
import hmac
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path

from .domain import SOURCES, Candle, DataError, completed_vn_sessions

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO meta VALUES ('epoch', 0);
CREATE TABLE IF NOT EXISTS tickers(
 source TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT, enabled INTEGER NOT NULL DEFAULT 0,
 next_1d INTEGER NOT NULL DEFAULT 0, next_1h INTEGER NOT NULL DEFAULT 0,
 next_1m INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(source,symbol));
CREATE TABLE IF NOT EXISTS series(
 source TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL,
 provider TEXT NOT NULL, revision TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ready',
 PRIMARY KEY(source,symbol,interval));
CREATE TABLE IF NOT EXISTS candles(
 source TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL, time INTEGER NOT NULL,
 open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
 volume INTEGER NOT NULL CHECK(volume>=0), provider TEXT NOT NULL, revision TEXT NOT NULL,
 updated_at INTEGER NOT NULL, PRIMARY KEY(source,symbol,interval,time));
CREATE TABLE IF NOT EXISTS archives(
 id TEXT PRIMARY KEY, source TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL,
 start INTEGER NOT NULL, end INTEGER NOT NULL, row_count INTEGER NOT NULL,
 object_key TEXT NOT NULL UNIQUE, checksum TEXT NOT NULL, revision TEXT NOT NULL,
 provider TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'published',
 schema_version INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS archives_lookup ON archives(source,symbol,interval,end,start);
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY, source TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL,
 kind TEXT NOT NULL, provider TEXT, revision TEXT NOT NULL, cursor INTEGER,
 floor INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 retry_at INTEGER NOT NULL DEFAULT 0, lease_owner TEXT, lease_until INTEGER NOT NULL DEFAULT 0,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, error TEXT,
 UNIQUE(source,symbol,interval,kind));
CREATE TABLE IF NOT EXISTS staging(
 job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 source TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL, time INTEGER NOT NULL,
 open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
 volume INTEGER NOT NULL, provider TEXT NOT NULL, revision TEXT NOT NULL, updated_at INTEGER NOT NULL,
 PRIMARY KEY(job_id,time));
CREATE TABLE IF NOT EXISTS quality(
 id INTEGER PRIMARY KEY, source TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL,
 kind TEXT NOT NULL, detail TEXT NOT NULL, first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,
 resolved INTEGER NOT NULL DEFAULT 0, UNIQUE(source,symbol,interval,kind,detail));
CREATE TABLE IF NOT EXISTS sync_kv(
 id TEXT PRIMARY KEY, secret TEXT NOT NULL, value TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS live_leases(
 source TEXT NOT NULL,symbol TEXT NOT NULL,interval TEXT NOT NULL,
 owner TEXT NOT NULL,until INTEGER NOT NULL,PRIMARY KEY(source,symbol,interval));
CREATE TABLE IF NOT EXISTS legacy_imports(
 id TEXT PRIMARY KEY,source TEXT NOT NULL,symbol TEXT NOT NULL,interval TEXT NOT NULL,
 period TEXT NOT NULL,input_checksum TEXT NOT NULL,result TEXT NOT NULL,
 completed_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS snapshot_adoptions(
 source TEXT NOT NULL,symbol TEXT NOT NULL,interval TEXT NOT NULL,revision TEXT NOT NULL,
 snapshot_provider TEXT NOT NULL,provider TEXT NOT NULL,evidence TEXT NOT NULL,
 PRIMARY KEY(source,symbol,interval,revision));
CREATE TABLE IF NOT EXISTS source_checks(
 source TEXT NOT NULL,symbol TEXT NOT NULL,interval TEXT NOT NULL,
 attempted_at_ns INTEGER NOT NULL,outcome TEXT NOT NULL,error TEXT,
 successful_at_ns INTEGER,provider TEXT,revision TEXT,completed_before INTEGER,
 completed_start INTEGER,completed_end INTEGER,completed_rows INTEGER,
 provisional_rows INTEGER,
 PRIMARY KEY(source,symbol,interval));
PRAGMA user_version=2;
"""
COLUMNS = tuple(f.name for f in fields(Candle))
INSERT = (
    f"INSERT INTO candles ({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)}) ON CONFLICT(source,symbol,interval,time) DO UPDATE SET "
    + ",".join(f"{c}=excluded.{c}" for c in COLUMNS[4:])
)


class Repository:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=30000")
        try:
            with con:
                yield con
        finally:
            con.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            if con.execute("PRAGMA user_version").fetchone()[0] > 2:
                raise DataError("Database schema is newer than this application")
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)

    @staticmethod
    def bump(con):
        con.execute("UPDATE meta SET value=value+1 WHERE key='epoch'")

    def epoch(self):
        with self.connect() as con:
            return con.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()[0]

    def register(self, source, symbol, name=None, enabled=False):
        with self.connect() as con:
            con.execute(
                "INSERT INTO tickers(source,symbol,name,enabled) VALUES (?,?,?,?) ON CONFLICT(source,symbol) DO UPDATE SET name=COALESCE(excluded.name,tickers.name),enabled=MAX(tickers.enabled,excluded.enabled)",
                (source, symbol, name, int(enabled)),
            )

    def activate_watchlist(self, entries):
        """Publish the complete ingestion universe in one transaction."""
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("UPDATE tickers SET enabled=0")
            con.executemany(
                "INSERT INTO tickers(source,symbol,enabled) VALUES (?,?,1) ON CONFLICT(source,symbol) DO UPDATE SET enabled=1",
                [(entry["source"], entry["symbol"]) for entry in entries],
            )

    def tickers(self, sources=None, enabled=False):
        where, params = [], []
        if sources:
            where.append(f"source IN ({','.join('?' for _ in sources)})")
            params.extend(sources)
        if enabled:
            where.append("enabled=1")
        with self.connect() as con:
            return [
                dict(r)
                for r in con.execute(
                    "SELECT * FROM tickers"
                    + (" WHERE " + " AND ".join(where) if where else "")
                    + " ORDER BY source,symbol",
                    params,
                )
            ]

    def state(self, source, symbol, interval):
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM series WHERE source=? AND symbol=? AND interval=?",
                (source, symbol, interval),
            ).fetchone()
            return dict(row) if row else None

    def discovered_tickers(self, sources):
        """Include historical identities without enabling ingestion."""
        with self.connect() as con:
            return [
                dict(row)
                for row in con.execute(
                    f"""WITH identities AS (
                    SELECT source,symbol FROM tickers
                    UNION SELECT source,symbol FROM series
                    UNION SELECT source,symbol FROM archives WHERE status<>'superseded')
                    SELECT i.source,i.symbol,t.name FROM identities i
                    LEFT JOIN tickers t USING(source,symbol)
                    WHERE i.source IN ({",".join("?" for _ in sources)})
                    ORDER BY i.source,i.symbol""",
                    sources,
                )
            ]

    def adoptions(self):
        with self.connect() as con:
            return [
                dict(r)
                for r in con.execute(
                    "SELECT * FROM snapshot_adoptions ORDER BY source,symbol,interval,revision"
                )
            ]

    def recoveries(self):
        with self.connect() as con:
            return [
                dict(r)
                for r in con.execute(
                    "SELECT * FROM legacy_imports WHERE json_extract(result,'$.kind')='legacy_daily_timestamp_recovery' ORDER BY id"
                )
            ]

    def restore_recoveries(self, records):
        with self.connect() as con:
            con.executemany(
                "INSERT OR REPLACE INTO legacy_imports VALUES (?,?,?,?,?,?,?,?)",
                [
                    tuple(
                        r[k]
                        for k in (
                            "id",
                            "source",
                            "symbol",
                            "interval",
                            "period",
                            "input_checksum",
                            "result",
                            "completed_at",
                        )
                    )
                    for r in records
                ],
            )

    def snapshot_provider(self, state):
        with self.connect() as con:
            record = con.execute(
                "SELECT * FROM snapshot_adoptions WHERE source=? AND symbol=? AND interval=? AND revision=? AND provider=?",
                (
                    state["source"],
                    state["symbol"],
                    state["interval"],
                    state["revision"],
                    state["provider"],
                ),
            ).fetchone()
        if not record:
            return None
        self.validate_adoption(dict(record))
        return record["snapshot_provider"]

    @staticmethod
    def validate_adoption(record):
        try:
            if record.get("interval") == "1D":
                from .daily_adoption import validate_daily_adoption

                validate_daily_adoption(record)
                return
            evidence = json.loads(record["evidence"])
            valid = (
                (
                    (record["source"] == "vn" and record["provider"] in {"vps", "vndirect", "dnse"})
                    or (
                        record["source"] == "yahoo"
                        and record["provider"] == "yahoo"
                        and evidence["kind"] == "exact_snapshot_overlap"
                    )
                )
                and (
                    record["interval"] == "1m"
                    or record["interval"] == "1h"
                    and record["source"] == "yahoo"
                    and evidence["kind"] == "exact_snapshot_overlap"
                )
                and record["snapshot_provider"] == "legacy-api"
                and bool(record["symbol"])
                and bool(record["revision"])
                and evidence["kind"]
                in {
                    "exact_snapshot_overlap",
                    "exact_complete_sessions",
                    "corroborated_complete_sessions",
                }
                and type(evidence["matched_rows"]) is int
                and evidence["matched_rows"]
                >= (
                    (100 if record["interval"] == "1h" else 1000)
                    if evidence["kind"] == "exact_snapshot_overlap"
                    else 5
                )
                and type(evidence["completed_sessions"]) is int
                and evidence["completed_sessions"] >= 5
                and evidence["snapshot_rows"] >= evidence["matched_rows"]
                and evidence["snapshot_start"]
                <= evidence["overlap_start"]
                <= evidence["overlap_end"]
                <= evidence["snapshot_end"]
                and type(evidence["verified_at_ns"]) is int
                and evidence["verified_at_ns"] > 0
                and evidence["snapshot_end"] <= evidence["verified_at_ns"] // 1_000_000_000
                and all(
                    isinstance(evidence[key], str)
                    and len(evidence[key]) == 64
                    and set(evidence[key]) <= set("0123456789abcdef")
                    for key in ("snapshot_checksum", "overlap_checksum")
                )
            )
            if valid and record["source"] == "yahoo":
                step = 3600 if record["interval"] == "1h" else 60
                valid = (
                    type(evidence["completed_before"]) is int
                    and evidence["completed_before"] % step == 0
                    and evidence["overlap_end"]
                    < evidence["completed_before"]
                    <= evidence["verified_at_ns"] // (step * 1_000_000_000) * step
                )
                if record["interval"] == "1h":
                    valid = valid and all(
                        evidence[key] % step == 0 for key in ("overlap_start", "overlap_end")
                    )
            if valid and evidence["kind"] in {
                "exact_complete_sessions",
                "corroborated_complete_sessions",
            }:
                from .adoption import validate_complete_sessions

                validate_complete_sessions(record, evidence)
        except (KeyError, TypeError, ValueError, OverflowError, DataError):
            valid = False
        if not valid:
            raise DataError("Invalid snapshot adoption evidence")

    def restore_adoptions(self, records):
        for record in records:
            self.validate_adoption(record)
        with self.connect() as con:
            con.executemany(
                "INSERT OR REPLACE INTO snapshot_adoptions VALUES (?,?,?,?,?,?,?)",
                [
                    tuple(
                        r[k]
                        for k in (
                            "source",
                            "symbol",
                            "interval",
                            "revision",
                            "snapshot_provider",
                            "provider",
                            "evidence",
                        )
                    )
                    for r in records
                ],
            )
            if records:
                self.bump(con)

    def validate_basis(self, candles):
        if not candles:
            return
        if len({c.revision for c in candles}) > 1:
            raise DataError("Incompatible adjustment revisions")
        first = candles[0]
        with self.connect() as con:
            record = con.execute(
                "SELECT * FROM snapshot_adoptions WHERE source=? AND symbol=? AND interval=? AND revision=?",
                (first.source, first.symbol, first.interval, first.revision),
            ).fetchone()
        providers = {c.provider for c in candles}
        if (len(providers) > 1 and not record) or (
            record and not providers <= {record["snapshot_provider"], record["provider"]}
        ):
            raise DataError("Unverified providers in one adjustment revision")
        if record:
            self.validate_adoption(dict(record))
            verified = json.loads(record["evidence"])["verified_at_ns"]
            if any(
                c.provider == record["snapshot_provider"] and not 0 < c.updated_at <= verified
                for c in candles
            ):
                raise DataError(
                    "Snapshot was changed after adoption; use a new revision and verify it independently"
                )

    def start_source_check(self, source, symbol, interval):
        stamp = time.time_ns()
        with self.connect() as con:
            con.execute(
                """INSERT INTO source_checks(source,symbol,interval,attempted_at_ns,outcome)
                VALUES (?,?,?,?,'running') ON CONFLICT(source,symbol,interval)
                DO UPDATE SET attempted_at_ns=excluded.attempted_at_ns,outcome='running',error=NULL""",
                (source, symbol, interval, stamp),
            )
        return stamp

    def fail_source_check(self, source, symbol, interval, attempt, error, outcome="failed"):
        with self.connect() as con:
            con.execute(
                """UPDATE source_checks SET outcome=?,error=? WHERE source=? AND symbol=?
                AND interval=? AND attempted_at_ns=?""",
                (outcome, error[:500], source, symbol, interval, attempt),
            )

    def put(self, candles, overwrite=True, verification=None):
        candles = [c.validate() for c in candles]
        if not candles:
            return 0
        if verification and not overwrite:
            raise DataError("Provider verification requires publishing every checked candle")
        stamp = time.time_ns()
        written = 0
        statement = (
            INSERT if overwrite else INSERT.split(" ON CONFLICT", 1)[0] + " ON CONFLICT DO NOTHING"
        )
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if verification:
                ident = (candles[0].source, candles[0].symbol, candles[0].interval)
                if any((c.source, c.symbol, c.interval) != ident for c in candles):
                    raise DataError("A provider check must cover one series")
                check = con.execute(
                    "SELECT attempted_at_ns FROM source_checks WHERE source=? AND symbol=? AND interval=?",
                    ident,
                ).fetchone()
                if not check or check[0] != verification["attempt_ns"]:
                    raise DataError("Provider check superseded by another attempt")
            for c in candles:
                con.execute(
                    "INSERT OR IGNORE INTO tickers(source,symbol) VALUES (?,?)",
                    (c.source, c.symbol),
                )
                con.execute(
                    "INSERT OR IGNORE INTO series(source,symbol,interval,provider,revision) VALUES (?,?,?,?,?)",
                    (c.source, c.symbol, c.interval, c.provider, c.revision),
                )
                state = con.execute(
                    "SELECT provider,revision,status FROM series WHERE source=? AND symbol=? AND interval=?",
                    (c.source, c.symbol, c.interval),
                ).fetchone()
                if (
                    state["revision"] != c.revision
                    or state["provider"] != c.provider
                    or state["status"] != "ready"
                ):
                    raise DataError("Provider/revision change requires staged recovery")
                record = c.record()
                record["updated_at"] = stamp
                written += con.execute(statement, tuple(record[k] for k in COLUMNS)).rowcount
            if written:
                self.bump(con)
            if verification:
                completed = [c.time for c in candles if c.time < verification["completed_before"]]
                con.execute(
                    """UPDATE source_checks SET outcome='succeeded',error=NULL,
                    successful_at_ns=?,provider=?,revision=?,completed_before=?,
                    completed_start=?,completed_end=?,completed_rows=?,provisional_rows=?
                    WHERE source=? AND symbol=? AND interval=?""",
                    (
                        stamp,
                        candles[0].provider,
                        candles[0].revision,
                        verification["completed_before"],
                        min(completed) if completed else None,
                        max(completed) if completed else None,
                        len(completed),
                        len(candles) - len(completed),
                        *ident,
                    ),
                )
                con.execute(
                    "UPDATE quality SET resolved=1 WHERE source=? AND symbol=? AND interval=? AND kind IN ('provider_failure','provider_handoff_pending')",
                    ident,
                )
        return written

    def read(self, source, symbol, interval, start=None, end=None, limit=None, forward=False):
        where = "source=? AND symbol=? AND interval=?"
        args = [source, symbol, interval]
        if start is not None:
            where += " AND time>=?"
            args.append(start)
        if end is not None:
            where += " AND time<=?"
            args.append(end)
        sql = f"SELECT * FROM candles WHERE {where} ORDER BY time {'ASC' if forward else 'DESC'}"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        with self.connect() as con:
            rows = con.execute(sql, args).fetchall()
            return [Candle(**dict(r)) for r in (rows if forward else reversed(rows))]

    def archives(self, source=None, symbol=None, interval=None, start=None, end=None):
        where, args = ["status IN ('published','pending_repair')"], []
        for col, value in (("source", source), ("symbol", symbol), ("interval", interval)):
            if value is not None:
                where.append(f"{col}=?")
                args.append(value)
        if start is not None:
            where.append("end>=?")
            args.append(start)
        if end is not None:
            where.append("start<=?")
            args.append(end)
        with self.connect() as con:
            return [
                dict(r)
                for r in con.execute(
                    "SELECT * FROM archives WHERE " + " AND ".join(where) + " ORDER BY end DESC",
                    args,
                )
            ]

    def publish_archive(self, obj, snapshot=(), prune=False, require_current=False):
        # Compare all exported values including nanosecond write version: a
        # reconciliation write after export can never be accidentally pruned.
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if require_current:
                state = con.execute(
                    "SELECT provider,revision,status FROM series WHERE source=? AND symbol=? AND interval=?",
                    (obj["source"], obj["symbol"], obj["interval"]),
                ).fetchone()
                if not state or (state["provider"], state["revision"], state["status"]) != (
                    obj["provider"],
                    obj["revision"],
                    "ready",
                ):
                    raise DataError("Archive candidate superseded by a series repair")
            con.execute(
                "INSERT OR IGNORE INTO tickers(source,symbol) VALUES (?,?)",
                (obj["source"], obj["symbol"]),
            )
            cols = tuple(obj)
            con.execute(
                f"INSERT OR IGNORE INTO archives({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                tuple(obj.values()),
            )
            if prune:
                active = con.execute(
                    "SELECT status,checksum FROM archives WHERE id=?", (obj["id"],)
                ).fetchone()
                if active["status"] != "published" or active["checksum"] != obj["checksum"]:
                    raise DataError("Archive is no longer verified for pruning")
                predicate = " AND ".join(f"{k}=?" for k in COLUMNS)
                con.executemany(
                    f"DELETE FROM candles WHERE {predicate}",
                    [tuple(c.record()[k] for k in COLUMNS) for c in snapshot],
                )
            self.bump(con)

    def replace_archive(self, old_id, obj):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            old = con.execute("SELECT * FROM archives WHERE id=?", (old_id,)).fetchone()
            if not old or any(old[k] != obj[k] for k in ("source", "symbol", "interval")):
                raise DataError(
                    "Archive replacement target is missing or belongs to another series"
                )
            if old["status"] == "superseded":
                # A retry after local publication/remote manifest failure may
                # advertise the same already-active candidate. A different
                # candidate from a stale worker cannot revive this old target.
                active = con.execute("SELECT * FROM archives WHERE id=?", (obj["id"],)).fetchone()
                if (
                    not active
                    or active["status"] != "published"
                    or any(
                        active[k] != value
                        for k, value in obj.items()
                        if k not in ("created_at", "status")
                    )
                ):
                    raise DataError("Archive replacement target was superseded")
            current = con.execute(
                "SELECT * FROM series WHERE source=? AND symbol=? AND interval=?",
                (obj["source"], obj["symbol"], obj["interval"]),
            ).fetchone()
            if current and (
                current["revision"] != obj["revision"]
                or current["provider"] != obj["provider"]
                or current["status"] != "ready"
            ):
                raise DataError("Archive replacement superseded by a newer series repair")
            cols = tuple(obj)
            con.execute(
                f"INSERT OR IGNORE INTO archives({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                tuple(obj.values()),
            )
            con.execute(
                "UPDATE archives SET status='superseded' WHERE id=? AND id<>?", (old_id, obj["id"])
            )
            self.bump(con)

    def retire_archive_jobs(self, source=None, symbol=None, interval=None):
        """Cancel only unleased work whose immutable target is no longer active."""
        where = [
            "j.kind LIKE 'archive_repair:%'",
            "j.status IN ('pending','running')",
            "j.lease_until<=?",
        ]
        args = [int(time.time())]
        for column, value in (("source", source), ("symbol", symbol), ("interval", interval)):
            if value is not None:
                where.append(f"j.{column}=?")
                args.append(value)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            jobs = con.execute(
                "SELECT j.id,j.kind FROM jobs j WHERE " + " AND ".join(where), args
            ).fetchall()
            retired = 0
            for job in jobs:
                target = job["kind"].split(":", 2)[1]
                state = con.execute("SELECT status FROM archives WHERE id=?", (target,)).fetchone()
                if state and state["status"] != "superseded":
                    continue
                con.execute("DELETE FROM staging WHERE job_id=?", (job["id"],))
                con.execute(
                    "UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until=0,updated_at=? WHERE id=?",
                    (time.time_ns(), job["id"]),
                )
                retired += 1
            return retired

    def mark_archive_repairs(self, source, symbol, interval):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            state = con.execute(
                "SELECT * FROM series WHERE source=? AND symbol=? AND interval=?",
                (source, symbol, interval),
            ).fetchone()
            if not state or state["status"] != "ready":
                raise DataError("Archive reconciliation requires a ready retained series")
            record = con.execute(
                "SELECT * FROM snapshot_adoptions WHERE source=? AND symbol=? AND interval=? AND revision=? AND provider=?",
                (source, symbol, interval, state["revision"], state["provider"]),
            ).fetchone()
            snapshot_provider = record["snapshot_provider"] if record else state["provider"]
            count = con.execute(
                "UPDATE archives SET status='pending_repair' WHERE source=? AND symbol=? AND interval=? AND status='published' AND ((provider<>? AND provider<>?) OR revision<>?)",
                (source, symbol, interval, state["provider"], snapshot_provider, state["revision"]),
            ).rowcount
            if count:
                self.bump(con)
            if not con.execute(
                "SELECT 1 FROM archives WHERE source=? AND symbol=? AND interval=? AND status IN ('published','pending_repair') AND (status='pending_repair' OR (provider<>? AND provider<>?) OR revision<>?)",
                (source, symbol, interval, state["provider"], snapshot_provider, state["revision"]),
            ).fetchone():
                con.execute(
                    "UPDATE quality SET resolved=1 WHERE source=? AND symbol=? AND interval=? AND kind IN ('archive_repair_pending','imported_revision_boundary')",
                    (source, symbol, interval),
                )
            return count

    def compact_archives(self, originals, obj):
        """Replace verified fragments atomically; never erase their S3 objects."""
        identity = ("source", "symbol", "interval", "provider", "revision")
        if len(originals) < 2 or any(
            tuple(old[k] for k in identity) != tuple(obj[k] for k in identity) for old in originals
        ):
            raise DataError("Compaction requires one unchanged archive basis")
        identifiers = [old["id"] for old in originals]
        if len(set(identifiers)) != len(identifiers):
            raise DataError("Duplicate compaction fragments")
        placeholders = ",".join("?" for _ in identifiers)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            current = con.execute(
                f"SELECT * FROM archives WHERE id IN ({placeholders})", identifiers
            ).fetchall()
            if {r["id"]: dict(r) for r in current} != {r["id"]: r for r in originals} or any(
                r["status"] != "published" for r in current
            ):
                raise DataError("Archive fragments changed during compaction")
            columns = tuple(obj)
            con.execute(
                f"INSERT OR IGNORE INTO archives({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(obj.values()),
            )
            con.execute(
                f"UPDATE archives SET status='superseded' WHERE id IN ({placeholders}) AND id<>?",
                [*identifiers, obj["id"]],
            )
            self.bump(con)

    def finding(self, source, symbol, interval, kind, detail):
        now = int(time.time())
        with self.connect() as con:
            con.execute(
                "INSERT INTO quality(source,symbol,interval,kind,detail,first_seen,last_seen) VALUES (?,?,?,?,?,?,?) ON CONFLICT(source,symbol,interval,kind,detail) DO UPDATE SET last_seen=excluded.last_seen,resolved=0",
                (source, symbol, interval, kind, detail, now, now),
            )

    def findings(self):
        with self.connect() as con:
            return [
                dict(r)
                for r in con.execute(
                    "SELECT * FROM quality WHERE resolved=0 ORDER BY last_seen DESC"
                )
            ]

    @staticmethod
    def validate_history_gap(record):
        if (
            type(record) is not dict
            or set(record) != {"source", "symbol", "interval", "start", "end", "reason", "evidence"}
            or type(record["source"]) is not str
            or record["source"] not in SOURCES
            or type(record["symbol"]) is not str
            or not record["symbol"]
            or any(ord(c) < 32 for c in record["symbol"])
            or record["interval"] not in ("1D", "1h", "1m")
            or type(record["start"]) is not int
            or type(record["end"]) is not int
            or record["start"] > record["end"]
            or type(record["reason"]) is not str
            or not 1 <= len(record["reason"]) <= 2000
            or type(record["evidence"]) is not dict
        ):
            raise DataError("Invalid unavailable-history record")
        try:
            datetime.fromtimestamp(record["start"], UTC)
            datetime.fromtimestamp(record["end"], UTC)
            encoded = json.dumps(record, sort_keys=True, allow_nan=False)
        except (ValueError, TypeError, OverflowError, OSError) as exc:
            raise DataError("Invalid unavailable-history record") from exc
        if len(encoded.encode()) > 16 * 1024:
            raise DataError("Unavailable-history evidence exceeds its byte budget")
        return encoded

    def history_gaps(self, source=None, symbol=None, interval=None, start=None, end=None):
        where, args = ["kind='history_unavailable'", "resolved=0"], []
        for column, value in (("source", source), ("symbol", symbol), ("interval", interval)):
            if value is not None:
                where.append(f"{column}=?")
                args.append(value)
        result = []
        with self.connect() as con:
            records = con.execute(
                "SELECT source,symbol,interval,detail FROM quality WHERE " + " AND ".join(where),
                args,
            ).fetchall()
        for row in records:
            try:
                record = json.loads(row["detail"])
            except (ValueError, TypeError) as exc:
                raise DataError("Invalid unavailable-history record") from exc
            self.validate_history_gap(record)
            if any(record[key] != row[key] for key in ("source", "symbol", "interval")):
                raise DataError("Unavailable-history identity differs from its stored record")
            if (start is None or record["end"] >= start) and (
                end is None or record["start"] <= end
            ):
                result.append(record)
        return sorted(
            result,
            key=lambda row: (
                row["source"],
                row["symbol"],
                row["interval"],
                row["start"],
                row["end"],
                row["reason"],
            ),
        )

    def restore_history_gaps(self, records):
        # Validate every record before changing the target. An older manifest
        # without this optional field must never clear newer local observations.
        if type(records) is not list:
            raise DataError("Unavailable-history records must be a list")
        encoded = [self.validate_history_gap(record) for record in records]
        stamp = int(time.time())
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.executemany(
                """INSERT INTO quality(source,symbol,interval,kind,detail,first_seen,last_seen)
                VALUES (?,?,?,'history_unavailable',?,?,?)
                ON CONFLICT(source,symbol,interval,kind,detail)
                DO UPDATE SET resolved=0,last_seen=excluded.last_seen""",
                [
                    (record["source"], record["symbol"], record["interval"], detail, stamp, stamp)
                    for record, detail in zip(records, encoded, strict=True)
                ],
            )
            if encoded:
                self.bump(con)

    def record_history_gap(self, source, symbol, interval, start, end, reason, evidence):
        self.restore_history_gaps(
            [
                dict(
                    source=source,
                    symbol=symbol,
                    interval=interval,
                    start=start,
                    end=end,
                    reason=reason,
                    evidence=evidence,
                )
            ]
        )

    def resolve_verified_history_gaps(self, con, source, symbol, interval, start, end):
        """Called only after complete dated recovery; retain the original finding."""
        changed = con.execute(
            """UPDATE quality SET resolved=1 WHERE source=? AND symbol=? AND interval=?
            AND kind='history_unavailable' AND resolved=0
            AND json_extract(detail,'$.start')>=? AND json_extract(detail,'$.end')<=?""",
            (source, symbol, interval, start, end),
        ).rowcount
        if changed:
            self.bump(con)

    def queue(self, source, symbol, interval, kind, floor, provider=None, expected=None):
        now = int(time.time())
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if expected:
                failed = con.execute(
                    "SELECT revision,status,lease_owner FROM jobs WHERE id=?", (expected[0],)
                ).fetchone()
                if (
                    not failed
                    or failed["revision"] != expected[1]
                    or failed["status"] != "pending"
                    or failed["lease_owner"] is not None
                ):
                    raise DataError("Repair fallback superseded by another worker")
            current = con.execute(
                "SELECT * FROM jobs WHERE source=? AND symbol=? AND interval=? AND kind=?",
                (source, symbol, interval, kind),
            ).fetchone()
            if current and current["status"] not in ("complete", "cancelled"):
                if provider is None or provider == current["provider"]:
                    return current["id"]
                # An explicit replacement provider restarts staging from scratch.
                # Published candles remain available under their existing revision.
                con.execute("DELETE FROM staging WHERE job_id=?", (current["id"],))
            job_id, revision = str(uuid.uuid4()), str(uuid.uuid4())
            con.execute(
                "INSERT INTO jobs(id,source,symbol,interval,kind,provider,revision,floor,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,symbol,interval,kind) DO UPDATE SET id=excluded.id,provider=excluded.provider,revision=excluded.revision,cursor=NULL,floor=excluded.floor,status='pending',attempts=0,retry_at=0,lease_owner=NULL,lease_until=0,created_at=excluded.created_at,updated_at=excluded.updated_at,error=NULL",
                (
                    job_id,
                    source,
                    symbol,
                    interval,
                    kind,
                    provider,
                    revision,
                    floor,
                    now,
                    time.time_ns(),
                ),
            )
            if kind == "repair":
                stale = con.execute(
                    "SELECT id FROM jobs WHERE source=? AND symbol=? AND interval=? AND kind='bootstrap' AND status NOT IN ('complete','cancelled')",
                    (source, symbol, interval),
                ).fetchall()
                for old in stale:
                    con.execute("DELETE FROM staging WHERE job_id=?", (old["id"],))
                    con.execute(
                        "UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until=0 WHERE id=?",
                        (old["id"],),
                    )
                con.execute(
                    "UPDATE series SET status='repairing' WHERE source=? AND symbol=? AND interval=?",
                    (source, symbol, interval),
                )
            self.bump(con)
            return job_id

    def claim_job(self, owner, lease=120, allowed=None):
        now = int(time.time())
        if allowed is not None and not allowed:
            return None
        extra = ""
        args = [now, now]
        if allowed is not None:
            extra = (
                " AND ("
                + " OR ".join("(source=? AND symbol=? AND interval=?)" for _ in allowed)
                + ")"
            )
            args.extend(value for identity in allowed for value in identity)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM jobs WHERE status IN ('pending','running') AND kind NOT LIKE 'archive_repair%' AND retry_at<=? AND lease_until<=?"
                + extra
                + " ORDER BY updated_at,created_at LIMIT 1",
                args,
            ).fetchone()
            if not row:
                return None
            con.execute(
                "UPDATE jobs SET status='running',lease_owner=?,lease_until=? WHERE id=?",
                (owner, now + lease, row["id"]),
            )
            return dict(row) | {
                "lease_owner": owner,
                "lease_until": now + lease,
                "status": "running",
            }

    def stage(self, job, candles, cursor, provider):
        candles = [c.validate() for c in candles]
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if (
                not row
                or row["lease_owner"] != job["lease_owner"]
                or row["revision"] != job["revision"]
                or row["lease_until"] < int(time.time())
            ):
                raise DataError("Repair lease expired")
            if row["provider"] and row["provider"] != provider:
                raise DataError("Cannot mix providers in a staged series")
            for c in candles:
                if (c.source, c.symbol, c.interval, c.provider, c.revision) != (
                    job["source"],
                    job["symbol"],
                    job["interval"],
                    provider,
                    job["revision"],
                ):
                    raise DataError("Staging series identity mismatch")
                values = c.record() | {"updated_at": time.time_ns()}
                con.execute(
                    f"INSERT OR REPLACE INTO staging(job_id,{','.join(COLUMNS)}) VALUES ({','.join('?' for _ in range(len(COLUMNS) + 1))})",
                    (job["id"], *(values[k] for k in COLUMNS)),
                )
            con.execute(
                "UPDATE jobs SET cursor=?,provider=?,status='pending',lease_owner=NULL,lease_until=0,updated_at=? WHERE id=?",
                (cursor, provider, time.time_ns(), job["id"]),
            )

    def finish_job(self, job):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if not row or row["status"] == "complete":
                return
            if (
                row["lease_owner"] != job["lease_owner"]
                or row["revision"] != job["revision"]
                or row["lease_until"] < int(time.time())
            ):
                raise DataError("Repair lease expired")
            ident = (job["source"], job["symbol"], job["interval"])
            if job["kind"] == "bootstrap":
                state = con.execute(
                    "SELECT * FROM series WHERE source=? AND symbol=? AND interval=?", ident
                ).fetchone()
                if state and (state["revision"] != row["revision"] or state["status"] != "ready"):
                    raise DataError("Bootstrap revision superseded by recovery")
                # Live updates can advance while the historical backfill runs.
                # Preserve their verified tail in the completed initial revision.
                if state:
                    con.execute(
                        f"INSERT OR REPLACE INTO staging(job_id,{','.join(COLUMNS)}) SELECT ?,{','.join(COLUMNS)} FROM candles WHERE source=? AND symbol=? AND interval=?",
                        (job["id"], *ident),
                    )
            staged = con.execute(
                "SELECT MIN(time),MAX(time),COUNT(*) FROM staging WHERE job_id=?", (job["id"],)
            ).fetchone()
            latest = con.execute(
                "SELECT MAX(time) FROM candles WHERE source=? AND symbol=? AND interval=?",
                (job["source"], job["symbol"], job["interval"]),
            ).fetchone()[0]
            if not staged[2] or (latest is not None and staged[1] < latest):
                raise DataError("Replacement is empty or ends before published data")
            completed = (
                completed_vn_sessions()
                if job["source"] == "vn"
                else int(time.time())
                // {"1D": 86400, "1h": 3600, "1m": 60}[job["interval"]]
                * {"1D": 86400, "1h": 3600, "1m": 60}[job["interval"]]
            )
            # Cursor progress alone cannot prove coverage: a sparse fallback
            # can jump across months. Preserve every completed observed local
            # candle inside this window, including holes within one session.
            # VN weekend daily anomalies are not legitimate trading sessions.
            coverage_query = """SELECT c.time FROM candles c WHERE c.source=? AND c.symbol=? AND c.interval=?
                AND c.time>=? AND c.time<? AND NOT EXISTS
                (SELECT 1 FROM staging s WHERE s.job_id=? AND s.time=c.time)
                AND NOT (c.source='vn' AND c.interval='1D'
                AND strftime('%w',c.time,'unixepoch') IN ('0','6')) LIMIT 1"""
            missing = con.execute(
                coverage_query,
                (*ident, row["floor"], completed, job["id"]),
            ).fetchone()
            if missing:
                raise DataError(f"Replacement drops completed observed coverage at {missing[0]}")
            # floor coverage is checked by the worker against provider page progress.
            con.execute(
                "DELETE FROM candles WHERE source=? AND symbol=? AND interval=? AND time>=?",
                (*ident, row["floor"]),
            )
            con.execute(
                f"INSERT OR REPLACE INTO candles({','.join(COLUMNS)}) SELECT {','.join(COLUMNS)} FROM staging WHERE job_id=? AND time>=?",
                (job["id"], row["floor"]),
            )
            con.execute(
                "INSERT INTO series(source,symbol,interval,provider,revision,status) VALUES (?,?,?,?,?,'ready') ON CONFLICT(source,symbol,interval) DO UPDATE SET provider=excluded.provider,revision=excluded.revision,status='ready'",
                (*ident, row["provider"], row["revision"]),
            )
            if job["kind"] == "repair":
                obsolete = con.execute(
                    "SELECT id FROM jobs WHERE source=? AND symbol=? AND interval=? AND kind LIKE 'archive_repair:%' AND status NOT IN ('complete','cancelled')",
                    ident,
                ).fetchall()
                for stale in obsolete:
                    con.execute("DELETE FROM staging WHERE job_id=?", (stale["id"],))
                    con.execute(
                        "UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until=0 WHERE id=?",
                        (stale["id"],),
                    )
                con.execute(
                    "UPDATE archives SET status='pending_repair' WHERE source=? AND symbol=? AND interval=? AND revision<>? AND status='published'",
                    (*ident, row["revision"]),
                )
            con.execute("DELETE FROM staging WHERE job_id=?", (job["id"],))
            con.execute(
                "UPDATE jobs SET status='complete',lease_owner=NULL,lease_until=0,updated_at=? WHERE id=?",
                (time.time_ns(), job["id"]),
            )
            self.bump(con)
            con.execute(
                "UPDATE quality SET resolved=1 WHERE source=? AND symbol=? AND interval=? AND kind IN ('coverage_pending','incomplete_repair','provider_failure','historical_revision','provider_switch')",
                ident,
            )

    def fail_job(self, job, reason):
        now = int(time.time())
        with self.connect() as con:
            con.execute(
                "UPDATE jobs SET status='pending',attempts=attempts+1,error=?,retry_at=?,lease_owner=NULL,lease_until=0,updated_at=? WHERE id=? AND revision=? AND status IN ('pending','running') AND (lease_owner=? OR lease_owner IS NULL)",
                (
                    reason[:500],
                    now + min(3600, 30 * 2 ** min(job["attempts"], 7)),
                    time.time_ns(),
                    job["id"],
                    job["revision"],
                    job["lease_owner"],
                ),
            )

    def schedule(self, source, symbol, interval, when):
        col = {"1D": "next_1d", "1h": "next_1h", "1m": "next_1m"}[interval]
        with self.connect() as con:
            con.execute(
                f"UPDATE tickers SET {col}=? WHERE source=? AND symbol=?", (when, source, symbol)
            )

    def live_claim(self, source, symbol, interval, owner, lease=120):
        now = int(time.time())
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT until FROM live_leases WHERE source=? AND symbol=? AND interval=?",
                (source, symbol, interval),
            ).fetchone()
            if row and row[0] > now:
                return False
            con.execute(
                "INSERT OR REPLACE INTO live_leases VALUES (?,?,?,?,?)",
                (source, symbol, interval, owner, now + lease),
            )
            return True

    def live_release(self, source, symbol, interval, owner):
        with self.connect() as con:
            con.execute(
                "DELETE FROM live_leases WHERE source=? AND symbol=? AND interval=? AND owner=?",
                (source, symbol, interval, owner),
            )

    def refresh(self, sources, interval):
        col = {"1D": "next_1d", "1h": "next_1h", "1m": "next_1m"}.get(interval)
        if not col:
            raise DataError("Invalid interval. Must be one of: 1D, 1h, 1m", 400)
        with self.connect() as con:
            n = con.execute(
                f"UPDATE tickers SET {col}=0 WHERE enabled=1 AND source IN ({','.join('?' for _ in sources)})",
                sources,
            ).rowcount
        return {"updated": n, "interval": col, "sources": list(sources)}

    def sync(self, key, secret, value=None, write=False):
        key = str(uuid.UUID(key))
        digest = hashlib.sha256(secret.encode()).hexdigest()
        stamp = datetime.now(UTC).isoformat()
        with self.connect() as con:
            if write:
                con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM sync_kv WHERE id=?", (key,)).fetchone()
            if row and not hmac.compare_digest(row["secret"], digest):
                raise DataError("Invalid secret", 403)
            if write:
                con.execute(
                    "INSERT INTO sync_kv VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                    (key, digest, json.dumps(value, allow_nan=False), stamp, stamp),
                )
                row = con.execute("SELECT * FROM sync_kv WHERE id=?", (key,)).fetchone()
            if not row:
                raise DataError("Key not found", 404)
            return {
                "id": key,
                "value": json.loads(row["value"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def status(self):
        with self.connect() as con:
            series = [
                dict(r)
                for r in con.execute(
                    """WITH counts AS (
                    SELECT source,symbol,interval,COUNT(*) rows,MIN(time) first,MAX(time) last,
                    MAX(updated_at) last_ingest_ns FROM candles GROUP BY source,symbol,interval),
                    archived AS (
                    SELECT source,symbol,interval,
                    SUM(status='published') archive_objects,
                    SUM(CASE WHEN status='published' THEN row_count ELSE 0 END) archive_rows,
                    MIN(CASE WHEN status='published' THEN start END) archive_first,
                    MAX(CASE WHEN status='published' THEN end END) archive_last,
                    SUM(status='pending_repair') pending_archive_repairs,
                    CASE WHEN COUNT(DISTINCT provider)=1 THEN MIN(provider) END provider,
                    CASE WHEN COUNT(DISTINCT revision)=1 THEN MIN(revision) END revision
                    FROM archives WHERE status<>'superseded' GROUP BY source,symbol,interval),
                    identities AS (
                    SELECT source,symbol,interval FROM series
                    UNION SELECT source,symbol,interval FROM archived)
                    SELECT i.source,i.symbol,i.interval,
                    COALESCE(s.provider,a.provider) provider,
                    COALESCE(s.revision,a.revision) revision,
                    COALESCE(s.status,CASE WHEN a.archive_objects>0 THEN 'archived'
                    ELSE 'archive_pending' END) status,
                    COALESCE(c.rows,0) rows,c.first,c.last,c.last_ingest_ns,
                    COALESCE(a.archive_objects,0) archive_objects,
                    COALESCE(a.archive_rows,0) archive_rows,a.archive_first,a.archive_last,
                    COALESCE(a.pending_archive_repairs,0) pending_archive_repairs,
                    COALESCE(t.enabled,0) enabled,ch.attempted_at_ns,ch.outcome,ch.error,
                    ch.successful_at_ns,ch.provider checked_provider,ch.revision checked_revision,
                    ch.completed_before,ch.completed_start,ch.completed_end,
                    ch.completed_rows,ch.provisional_rows
                    FROM identities i LEFT JOIN series s USING(source,symbol,interval)
                    LEFT JOIN counts c USING(source,symbol,interval)
                    LEFT JOIN archived a USING(source,symbol,interval)
                    LEFT JOIN tickers t USING(source,symbol)
                    LEFT JOIN source_checks ch USING(source,symbol,interval)
                    ORDER BY i.source,i.symbol,i.interval"""
                )
            ]
            grouped = {}
            for row in series:
                current = (
                    row["status"] == "ready"
                    and row["provider"] == row["checked_provider"]
                    and row["revision"] == row["checked_revision"]
                    and row["successful_at_ns"] is not None
                    and row["last_ingest_ns"] is not None
                    and row["successful_at_ns"] >= row["last_ingest_ns"]
                )
                row["verification_current"] = current
                row["latest_verification"] = (
                    "completed_recheck"
                    if current and row["last"] == row["completed_end"]
                    else "provisional_at_check"
                    if current and row["last"] >= row["completed_before"]
                    else "unverified"
                )
                if not row["rows"]:
                    continue
                key = (row["source"], row["interval"])
                if key not in grouped:
                    grouped[key] = {
                        "source": row["source"],
                        "interval": row["interval"],
                        "rows": 0,
                        "first": row["first"],
                        "last": row["last"],
                        "last_ingest_ns": row["last_ingest_ns"],
                    }
                group = grouped[key]
                group["rows"] += row["rows"]
                group["first"] = min(group["first"], row["first"])
                group["last"] = max(group["last"], row["last"])
                group["last_ingest_ns"] = max(group["last_ingest_ns"], row["last_ingest_ns"])
            coverage = [grouped[key] for key in sorted(grouped)]
            counts, last_sync = {}, {}
            for row in coverage:
                iv = row["interval"]
                counts[iv] = counts.get(iv, 0) + row["rows"]
                last_sync[iv] = max(last_sync.get(iv, 0), row["last_ingest_ns"])
            return {
                "tickers": con.execute("SELECT COUNT(*) FROM tickers").fetchone()[0],
                "active_tickers": con.execute(
                    "SELECT COUNT(*) FROM tickers WHERE enabled=1"
                ).fetchone()[0],
                "records": counts,
                "coverage": coverage,
                "series": series,
                "archives": con.execute(
                    "SELECT COUNT(*) FROM archives WHERE status='published'"
                ).fetchone()[0],
                "pending_archives": con.execute(
                    "SELECT COUNT(*) FROM archives WHERE status='pending_repair'"
                ).fetchone()[0],
                "jobs": [
                    dict(r)
                    for r in con.execute(
                        "SELECT id,source,symbol,interval,kind,status,attempts,error FROM jobs WHERE status NOT IN ('complete','cancelled')"
                    )
                ],
                "last_sync": last_sync,
                "history_gaps": self.history_gaps(),
            }

    def backup(self, path):
        path = Path(path)
        if path.exists() or path.resolve() == self.path.resolve():
            raise DataError("Backup destination must be a new file", 400)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con, sqlite3.connect(path) as dest:
            con.backup(dest)
