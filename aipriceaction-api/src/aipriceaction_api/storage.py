"""SQLite transactions are short; each operation owns its connection/thread."""

import hashlib
import hmac
import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path

from .domain import SOURCES, Candle, DataError, completed_vn_sessions

MAJOR_VN = ("VNINDEX", "VN30", "VN30F1M")
MAJOR_OFF_HOURS = (
    "^GSPC",
    "^DJI",
    "^NDX",
    "GC=F",
    "CL=F",
    "BTCUSDT",
    "ETHUSDT",
    "XRPUSDT",
    "TONUSDT",
)

_ADOPTION_NOT_LOADED = object()
_SQLITE_CACHE_SIZE_KIB = 32 * 1024
_SQLITE_MMAP_SIZE_BYTES = 256 * 1024 * 1024
MAX_HISTORY_FAILURES = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO meta VALUES ('epoch', 0);
INSERT OR IGNORE INTO meta VALUES ('market_epoch', 0);
INSERT OR IGNORE INTO meta VALUES ('health_stats_version', 0);
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
CREATE TABLE IF NOT EXISTS candle_health(
 interval TEXT PRIMARY KEY, row_count INTEGER NOT NULL, last_sync INTEGER NOT NULL);
CREATE TRIGGER IF NOT EXISTS candles_health_insert AFTER INSERT ON candles BEGIN
 INSERT INTO candle_health(interval,row_count,last_sync) VALUES (NEW.interval,1,NEW.updated_at)
 ON CONFLICT(interval) DO UPDATE SET row_count=row_count+1,last_sync=MAX(last_sync,NEW.updated_at);
END;
CREATE TRIGGER IF NOT EXISTS candles_health_update AFTER UPDATE OF interval,updated_at ON candles BEGIN
 UPDATE candle_health SET row_count=row_count-1
 WHERE interval=OLD.interval AND OLD.interval<>NEW.interval;
 DELETE FROM candle_health WHERE interval=OLD.interval AND row_count<=0;
 INSERT INTO candle_health(interval,row_count,last_sync)
 SELECT NEW.interval,1,NEW.updated_at WHERE OLD.interval<>NEW.interval
 ON CONFLICT(interval) DO UPDATE SET row_count=row_count+1,
 last_sync=MAX(last_sync,NEW.updated_at);
 UPDATE candle_health SET last_sync=MAX(last_sync,NEW.updated_at) WHERE interval=NEW.interval;
END;
CREATE TABLE IF NOT EXISTS archives(
 id TEXT PRIMARY KEY, source TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL,
 start INTEGER NOT NULL, end INTEGER NOT NULL, row_count INTEGER NOT NULL,
 object_key TEXT NOT NULL UNIQUE, checksum TEXT NOT NULL, revision TEXT NOT NULL,
 provider TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'published',
 schema_version INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS archives_lookup ON archives(source,symbol,interval,end,start);
CREATE TABLE IF NOT EXISTS archive_manifest_state(
 id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL DEFAULT 0,
 published_generation INTEGER NOT NULL DEFAULT 0);
INSERT OR IGNORE INTO archive_manifest_state(id,generation,published_generation) VALUES (1,0,0);
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
CREATE TABLE IF NOT EXISTS history_jobs(
 job_id TEXT NOT NULL UNIQUE, source TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL,
 hot_cutoff INTEGER NOT NULL, target_start INTEGER NOT NULL, cursor INTEGER NOT NULL,
 provider TEXT NOT NULL, revision TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 failure_count INTEGER NOT NULL DEFAULT 0,
 retry_at INTEGER NOT NULL DEFAULT 0, error TEXT,
 batch_start INTEGER, batch_end INTEGER, batch_cursor INTEGER,
 batch_status TEXT, csv_keys TEXT NOT NULL DEFAULT '[]', csv_cursor INTEGER NOT NULL DEFAULT 0,
 page_count INTEGER NOT NULL DEFAULT 0, row_count INTEGER NOT NULL DEFAULT 0,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, completed_at INTEGER,
 PRIMARY KEY(source,symbol,interval));
CREATE INDEX IF NOT EXISTS history_jobs_ready ON history_jobs(status,retry_at,updated_at);
CREATE TABLE IF NOT EXISTS history_pending_objects(
 source TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL,
 object_id TEXT NOT NULL, batch_start INTEGER NOT NULL, payload TEXT NOT NULL,
 created_at INTEGER NOT NULL,
 PRIMARY KEY(source,symbol,interval,object_id),
 FOREIGN KEY(source,symbol,interval) REFERENCES history_jobs(source,symbol,interval) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS adjustment_rebases(
 source TEXT NOT NULL, symbol TEXT NOT NULL, status TEXT NOT NULL,
 detected_at INTEGER NOT NULL, evidence TEXT NOT NULL, error TEXT,
 updated_at INTEGER NOT NULL,
 PRIMARY KEY(source,symbol));
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
CREATE TABLE IF NOT EXISTS volume_corrections(
 id TEXT PRIMARY KEY, source TEXT NOT NULL, symbol TEXT NOT NULL,
 interval TEXT NOT NULL, time INTEGER NOT NULL, revision TEXT NOT NULL,
 evidence TEXT NOT NULL, UNIQUE(source,symbol,interval,time,revision));
CREATE TABLE IF NOT EXISTS history_versions(
 source TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(source,symbol,interval));
CREATE TABLE IF NOT EXISTS history_metadata_versions(
 source TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(source,symbol,interval));
CREATE TABLE IF NOT EXISTS history_daily_versions(
 source TEXT NOT NULL, time INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(source,time));
CREATE TRIGGER IF NOT EXISTS history_daily_version_insert AFTER INSERT ON candles
WHEN NEW.interval='1D' BEGIN
 INSERT INTO history_daily_versions(source,time,version) VALUES (NEW.source,NEW.time,1)
 ON CONFLICT(source,time) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS history_daily_version_update_new AFTER UPDATE ON candles
WHEN NEW.interval='1D' BEGIN
 INSERT INTO history_daily_versions(source,time,version) VALUES (NEW.source,NEW.time,1)
 ON CONFLICT(source,time) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS history_daily_version_update_old AFTER UPDATE ON candles
WHEN OLD.interval='1D' AND (NEW.interval<>'1D' OR NEW.source<>OLD.source OR NEW.time<>OLD.time) BEGIN
 INSERT INTO history_daily_versions(source,time,version) VALUES (OLD.source,OLD.time,1)
 ON CONFLICT(source,time) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS history_daily_version_delete AFTER DELETE ON candles
WHEN OLD.interval='1D' BEGIN
 INSERT INTO history_daily_versions(source,time,version) VALUES (OLD.source,OLD.time,1)
 ON CONFLICT(source,time) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS series_metadata_version_insert AFTER INSERT ON series BEGIN
 INSERT INTO history_metadata_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS series_metadata_version_update AFTER UPDATE ON series BEGIN
 INSERT INTO history_metadata_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS series_metadata_version_delete AFTER DELETE ON series BEGIN
 INSERT INTO history_metadata_versions(source,symbol,interval,version)
 VALUES (OLD.source,OLD.symbol,OLD.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS archives_metadata_version_insert AFTER INSERT ON archives BEGIN
 INSERT INTO history_metadata_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS archives_metadata_version_update AFTER UPDATE ON archives BEGIN
 INSERT INTO history_metadata_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS archives_metadata_version_delete AFTER DELETE ON archives BEGIN
 INSERT INTO history_metadata_versions(source,symbol,interval,version)
 VALUES (OLD.source,OLD.symbol,OLD.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS history_gaps_metadata_version_insert AFTER INSERT ON quality
WHEN NEW.kind='history_unavailable' BEGIN
 INSERT INTO history_metadata_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS history_gaps_metadata_version_update AFTER UPDATE ON quality
WHEN OLD.kind='history_unavailable' OR NEW.kind='history_unavailable' BEGIN
 INSERT INTO history_metadata_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS history_gaps_metadata_version_delete AFTER DELETE ON quality
WHEN OLD.kind='history_unavailable' BEGIN
 INSERT INTO history_metadata_versions(source,symbol,interval,version)
 VALUES (OLD.source,OLD.symbol,OLD.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS snapshot_adoptions_metadata_version_insert
AFTER INSERT ON snapshot_adoptions BEGIN
 INSERT INTO history_metadata_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS snapshot_adoptions_metadata_version_update
AFTER UPDATE ON snapshot_adoptions BEGIN
 INSERT INTO history_metadata_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS snapshot_adoptions_metadata_version_delete
AFTER DELETE ON snapshot_adoptions BEGIN
 INSERT INTO history_metadata_versions(source,symbol,interval,version)
 VALUES (OLD.source,OLD.symbol,OLD.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS volume_corrections_metadata_version_insert
AFTER INSERT ON volume_corrections BEGIN
 INSERT INTO history_metadata_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS volume_corrections_metadata_version_update
AFTER UPDATE ON volume_corrections BEGIN
 INSERT INTO history_metadata_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS volume_corrections_metadata_version_delete
AFTER DELETE ON volume_corrections BEGIN
 INSERT INTO history_metadata_versions(source,symbol,interval,version)
 VALUES (OLD.source,OLD.symbol,OLD.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TABLE IF NOT EXISTS archive_repair_batches(
 id TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('collecting','prepared','indexed','manifest_published','published','failed')),
 payload TEXT NOT NULL, retry_at INTEGER NOT NULL DEFAULT 0,
 lease_owner TEXT, lease_until INTEGER NOT NULL DEFAULT 0,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, error TEXT);
CREATE INDEX IF NOT EXISTS archive_repair_batches_status
 ON archive_repair_batches(status,retry_at,created_at);
CREATE TRIGGER IF NOT EXISTS candles_version_insert AFTER INSERT ON candles BEGIN
 INSERT INTO history_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS candles_version_update AFTER UPDATE ON candles BEGIN
 INSERT INTO history_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS candles_version_delete AFTER DELETE ON candles BEGIN
 INSERT INTO history_versions(source,symbol,interval,version)
 VALUES (OLD.source,OLD.symbol,OLD.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS archives_version_insert AFTER INSERT ON archives BEGIN
 INSERT INTO history_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS archives_version_update AFTER UPDATE ON archives BEGIN
 INSERT INTO history_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS archives_version_delete AFTER DELETE ON archives BEGIN
 INSERT INTO history_versions(source,symbol,interval,version)
 VALUES (OLD.source,OLD.symbol,OLD.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS history_gaps_version_insert AFTER INSERT ON quality
WHEN NEW.kind='history_unavailable' BEGIN
 INSERT INTO history_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS history_gaps_version_update AFTER UPDATE ON quality
WHEN OLD.kind='history_unavailable' OR NEW.kind='history_unavailable' BEGIN
 INSERT INTO history_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS history_gaps_version_delete AFTER DELETE ON quality
WHEN OLD.kind='history_unavailable' BEGIN
 INSERT INTO history_versions(source,symbol,interval,version)
 VALUES (OLD.source,OLD.symbol,OLD.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS snapshot_adoptions_version_insert AFTER INSERT ON snapshot_adoptions BEGIN
 INSERT INTO history_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS snapshot_adoptions_version_update AFTER UPDATE ON snapshot_adoptions BEGIN
 INSERT INTO history_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS snapshot_adoptions_version_delete AFTER DELETE ON snapshot_adoptions BEGIN
 INSERT INTO history_versions(source,symbol,interval,version)
 VALUES (OLD.source,OLD.symbol,OLD.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS volume_corrections_version_insert AFTER INSERT ON volume_corrections BEGIN
 INSERT INTO history_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS volume_corrections_version_update AFTER UPDATE ON volume_corrections BEGIN
 INSERT INTO history_versions(source,symbol,interval,version)
 VALUES (NEW.source,NEW.symbol,NEW.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER IF NOT EXISTS volume_corrections_version_delete AFTER DELETE ON volume_corrections BEGIN
 INSERT INTO history_versions(source,symbol,interval,version)
 VALUES (OLD.source,OLD.symbol,OLD.interval,1)
 ON CONFLICT(source,symbol,interval) DO UPDATE SET version=version+1;
END;
"""
COLUMNS = tuple(f.name for f in fields(Candle))
MARKET_COLUMNS = COLUMNS[4:-1]
INSERT = (
    f"INSERT INTO candles ({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)}) ON CONFLICT(source,symbol,interval,time) DO UPDATE SET "
    + ",".join(f"{c}=excluded.{c}" for c in COLUMNS[4:])
    + " WHERE "
    + " OR ".join(f"candles.{column} IS NOT excluded.{column}" for column in MARKET_COLUMNS)
)


class RefreshScheduleError(Exception):
    """A source-local refresh scheduling failure, safe to report to the caller."""

    def __init__(self, source):
        self.source = source
        super().__init__(f"Refresh schedule update failed for source {source}")


class Repository:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=30000")
        # Latest multi-ticker reads seek hundreds of candles for many symbols.
        # Give each bounded read connection enough page cache to avoid thrash
        # and let SQLite mmap the local database where the platform supports it.
        con.execute(f"PRAGMA cache_size=-{_SQLITE_CACHE_SIZE_KIB}")
        con.execute(f"PRAGMA mmap_size={_SQLITE_MMAP_SIZE_BYTES}")
        try:
            with con:
                yield con
        finally:
            con.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            schema_version = con.execute("PRAGMA user_version").fetchone()[0]
            if schema_version > 5:
                raise DataError("Database schema is newer than this application")
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            con.execute("BEGIN IMMEDIATE")
            history_columns = {
                row[1] for row in con.execute("PRAGMA table_info(history_jobs)")
            }
            if "failure_count" not in history_columns:
                con.execute(
                    "ALTER TABLE history_jobs ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0"
                )
            con.execute(
                "UPDATE history_jobs SET failure_count=attempts "
                "WHERE failure_count=0 AND attempts>0"
            )
            con.execute(
                """UPDATE history_jobs SET status='exhausted',retry_at=0
                WHERE failure_count>=? AND status NOT IN ('complete','provider_limit','exhausted')""",
                (MAX_HISTORY_FAILURES,),
            )
            con.execute("PRAGMA user_version=5")
            health_stats_version = con.execute(
                "SELECT value FROM meta WHERE key='health_stats_version'"
            ).fetchone()[0]
            if health_stats_version < 3:
                # Seed exact row totals with a small grouped result, then keep
                # those totals current without rescanning candle history for
                # each public health probe.
                con.execute("DELETE FROM candle_health")
                con.execute(
                    """INSERT INTO candle_health(interval,row_count,last_sync)
                    SELECT interval,COUNT(*),MAX(updated_at) FROM candles GROUP BY interval"""
                )
                # Older databases may already have direct summary triggers.
                # DROP them all because CREATE TRIGGER IF NOT EXISTS does not
                # replace the old definitions with the current row-count logic.
                for trigger in (
                    "candles_health_insert",
                    "candles_health_update",
                    "candles_health_delete",
                ):
                    con.execute(f"DROP TRIGGER IF EXISTS {trigger}")
                con.execute("DROP TABLE IF EXISTS candle_health_sync")
                con.execute(
                    """CREATE TRIGGER candles_health_insert AFTER INSERT ON candles BEGIN
                    INSERT INTO candle_health(interval,row_count,last_sync)
                    VALUES (NEW.interval,1,NEW.updated_at)
                    ON CONFLICT(interval) DO UPDATE SET row_count=row_count+1,
                    last_sync=MAX(last_sync,NEW.updated_at);
                    END"""
                )
                con.execute(
                    """CREATE TRIGGER candles_health_update AFTER UPDATE OF interval,updated_at ON candles BEGIN
                    UPDATE candle_health SET row_count=row_count-1
                    WHERE interval=OLD.interval AND OLD.interval<>NEW.interval;
                    DELETE FROM candle_health WHERE interval=OLD.interval AND row_count<=0;
                    INSERT INTO candle_health(interval,row_count,last_sync)
                    SELECT NEW.interval,1,NEW.updated_at WHERE OLD.interval<>NEW.interval
                    ON CONFLICT(interval) DO UPDATE SET row_count=row_count+1,
                    last_sync=MAX(last_sync,NEW.updated_at);
                    UPDATE candle_health SET last_sync=MAX(last_sync,NEW.updated_at)
                    WHERE interval=NEW.interval;
                    END"""
                )
                con.execute(
                    """CREATE TRIGGER candles_health_delete AFTER DELETE ON candles BEGIN
                    UPDATE candle_health SET row_count=MAX(0,row_count-1) WHERE interval=OLD.interval;
                    DELETE FROM candle_health WHERE interval=OLD.interval AND row_count=0;
                    END"""
                )
                con.execute("UPDATE meta SET value=3 WHERE key='health_stats_version'")

    @staticmethod
    def bump(con, *, market=False):
        con.execute("UPDATE meta SET value=value+1 WHERE key='epoch'")
        if market:
            con.execute("UPDATE meta SET value=value+1 WHERE key='market_epoch'")

    def epoch(self):
        with self.connect() as con:
            return con.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()[0]

    def market_epoch(self):
        with self.connect() as con:
            return con.execute("SELECT value FROM meta WHERE key='market_epoch'").fetchone()[0]

    def history_versions(self, source, symbols, interval):
        """Return per-series data versions for precise in-process cache checks."""
        symbols = list(dict.fromkeys(symbols))
        versions = {symbol: 0 for symbol in symbols}
        if not symbols:
            return versions
        with self.connect() as con:
            for offset in range(0, len(symbols), 400):
                chunk = symbols[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = con.execute(
                    "SELECT symbol,version FROM history_versions "
                    f"WHERE source=? AND interval=? AND symbol IN ({placeholders})",
                    (source, interval, *chunk),
                )
                versions.update({row["symbol"]: row["version"] for row in rows})
        return versions

    def history_version_epoch(self, sources, interval, symbols=None):
        """Return a stable version for the requested source/interval scope."""
        sources = list(dict.fromkeys(sources))
        selected = None if symbols is None else list(dict.fromkeys(symbols))
        total = 0
        with self.connect() as con:
            for source in sources:
                if selected is None:
                    total += con.execute(
                        "SELECT COALESCE(SUM(version),0) FROM history_versions "
                        "WHERE source=? AND interval=?",
                        (source, interval),
                    ).fetchone()[0]
                    continue
                for offset in range(0, len(selected), 400):
                    chunk = selected[offset : offset + 400]
                    placeholders = ",".join("?" for _ in chunk)
                    total += con.execute(
                        "SELECT COALESCE(SUM(version),0) FROM history_versions "
                        f"WHERE source=? AND interval=? AND symbol IN ({placeholders})",
                        (source, interval, *chunk),
                    ).fetchone()[0]
        return total

    def history_versions_as_of(self, source, symbols, interval, end):
        """Return fast daily-change and metadata versions visible by ``end``."""
        symbols = list(dict.fromkeys(symbols))
        if not symbols:
            return 0, {}
        with self.connect() as con:
            daily_version = con.execute(
                "SELECT COALESCE(SUM(version),0) FROM history_daily_versions "
                "WHERE source=? AND time<=?",
                (source, end),
            ).fetchone()[0]
            metadata_versions = {symbol: 0 for symbol in symbols}
            for offset in range(0, len(symbols), 400):
                chunk = symbols[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                metadata_rows = con.execute(
                    "SELECT symbol,version FROM history_metadata_versions "
                    f"WHERE source=? AND interval=? AND symbol IN ({placeholders})",
                    (source, interval, *chunk),
                )
                for row in metadata_rows:
                    metadata_versions[row["symbol"]] = row["version"]
        return daily_version, metadata_versions

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

    def configure_history_job(
        self, source, symbol, interval, hot_cutoff, target_start, provider, revision
    ):
        """Create or reconcile metadata for one cold-history series."""
        now = int(time.time())
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM history_jobs WHERE source=? AND symbol=? AND interval=?",
                (source, symbol, interval),
            ).fetchone()
            if not row:
                con.execute(
                    """INSERT INTO history_jobs(
                    job_id,source,symbol,interval,hot_cutoff,target_start,cursor,provider,revision,
                    status,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?)""",
                    (
                        str(uuid.uuid4()),
                        source,
                        symbol,
                        interval,
                        hot_cutoff,
                        target_start,
                        hot_cutoff,
                        provider,
                        revision,
                        now,
                        now,
                    ),
                )
            else:
                changed_basis = (row["provider"], row["revision"]) != (provider, revision)
                extended = target_start < row["target_start"]
                new_target = min(target_start, row["target_start"])
                if changed_basis:
                    con.execute(
                        "DELETE FROM history_pending_objects WHERE source=? AND symbol=? AND interval=?",
                        (source, symbol, interval),
                    )
                    con.execute(
                        """UPDATE history_jobs SET hot_cutoff=?,target_start=?,cursor=?,
                        provider=?,revision=?,status='pending',attempts=0,failure_count=0,
                        retry_at=0,error=NULL,
                        batch_start=NULL,batch_end=NULL,batch_cursor=NULL,batch_status=NULL,
                        csv_keys='[]',csv_cursor=0,completed_at=NULL,updated_at=?
                        WHERE source=? AND symbol=? AND interval=?""",
                        (
                            hot_cutoff,
                            new_target,
                            hot_cutoff,
                            provider,
                            revision,
                            now,
                            source,
                            symbol,
                            interval,
                        ),
                    )
                else:
                    status = "pending" if extended and row["status"] == "complete" else row["status"]
                    cursor = row["cursor"]
                    if cursor <= new_target and status != "exhausted":
                        status = "complete"
                    con.execute(
                        """UPDATE history_jobs SET hot_cutoff=?,target_start=?,status=?,
                        completed_at=CASE WHEN ?='complete' THEN COALESCE(completed_at,?) ELSE NULL END,
                        updated_at=? WHERE source=? AND symbol=? AND interval=?""",
                        (
                            max(hot_cutoff, row["hot_cutoff"]),
                            new_target,
                            status,
                            status,
                            now,
                            now,
                            source,
                            symbol,
                            interval,
                        ),
                    )
            current = con.execute(
                "SELECT * FROM history_jobs WHERE source=? AND symbol=? AND interval=?",
                (source, symbol, interval),
            ).fetchone()
            return dict(current)

    def advance_unstarted_history_cursor(self, source, symbol, interval, cursor):
        """Move a pristine history job forward to its actual SQLite tail.

        This is used when a source's live SQLite coverage is shorter than the
        standard hot-retention window. Once any page is checkpointed, preserve
        the durable cursor and let the worker finish that pass instead.
        """
        now = int(time.time())
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM history_jobs WHERE source=? AND symbol=? AND interval=?",
                (source, symbol, interval),
            ).fetchone()
            if (
                not row
                or row["page_count"]
                or row["row_count"]
                or row["batch_start"] is not None
                or cursor <= row["cursor"]
            ):
                return False
            pending = con.execute(
                "SELECT 1 FROM history_pending_objects WHERE source=? AND symbol=? AND interval=? LIMIT 1",
                (source, symbol, interval),
            ).fetchone()
            if pending:
                return False
            con.execute(
                """UPDATE history_jobs SET cursor=?,status='pending',attempts=0,retry_at=0,
                error=NULL,completed_at=NULL,updated_at=?
                WHERE source=? AND symbol=? AND interval=?""",
                (cursor, now, source, symbol, interval),
            )
            return True

    def history_job(self, source, symbol, interval):
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM history_jobs WHERE source=? AND symbol=? AND interval=?",
                (source, symbol, interval),
            ).fetchone()
            return dict(row) if row else None

    def history_jobs(self, source=None, symbol=None, interval=None, limit=None):
        where, args = [], []
        for column, value in (("source", source), ("symbol", symbol), ("interval", interval)):
            if value is not None:
                where.append(f"{column}=?")
                args.append(value)
        sql = "SELECT * FROM history_jobs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY status,source,interval,symbol"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        with self.connect() as con:
            return [dict(row) for row in con.execute(sql, args)]

    def history_pending_objects(self, source, symbol, interval, batch_start=None):
        sql = "SELECT payload FROM history_pending_objects WHERE source=? AND symbol=? AND interval=?"
        args = [source, symbol, interval]
        if batch_start is not None:
            sql += " AND batch_start=?"
            args.append(batch_start)
        sql += " ORDER BY created_at,object_id"
        with self.connect() as con:
            return [json.loads(row[0]) for row in con.execute(sql, args)]

    def checkpoint_history_page(
        self, job, *, batch_start, batch_end, batch_cursor, objects, row_count
    ):
        now = int(time.time())
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            current = con.execute(
                "SELECT * FROM history_jobs WHERE source=? AND symbol=? AND interval=?",
                (job["source"], job["symbol"], job["interval"]),
            ).fetchone()
            if (
                not current
                or (current["provider"], current["revision"])
                != (job["provider"], job["revision"])
            ):
                raise DataError("Historical job basis changed before its page checkpoint")
            for obj in objects:
                con.execute(
                    """INSERT OR IGNORE INTO history_pending_objects(
                    source,symbol,interval,object_id,batch_start,payload,created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (
                        job["source"],
                        job["symbol"],
                        job["interval"],
                        obj["id"],
                        batch_start,
                        json.dumps(obj, sort_keys=True, separators=(",", ":")),
                        now,
                    ),
                )
            con.execute(
                """UPDATE history_jobs SET batch_start=?,batch_end=?,batch_cursor=?,
                batch_status='fetching',page_count=page_count+1,row_count=row_count+?,
                attempts=0,retry_at=0,error=NULL,status='pending',completed_at=NULL,updated_at=?
                WHERE source=? AND symbol=? AND interval=?""",
                (
                    batch_start,
                    batch_end,
                    batch_cursor,
                    row_count,
                    now,
                    job["source"],
                    job["symbol"],
                    job["interval"],
                ),
            )

    def mark_history_batch_csv(self, source, symbol, interval, keys):
        now = int(time.time())
        with self.connect() as con:
            con.execute(
                """UPDATE history_jobs SET batch_status='csv',csv_keys=?,csv_cursor=0,
                status='pending',retry_at=0,error=NULL,updated_at=?
                WHERE source=? AND symbol=? AND interval=?""",
                (
                    json.dumps(keys, separators=(",", ":")),
                    now,
                    source,
                    symbol,
                    interval,
                ),
            )

    def advance_history_csv(self, source, symbol, interval, csv_cursor):
        now = int(time.time())
        with self.connect() as con:
            con.execute(
                "UPDATE history_jobs SET csv_cursor=?,updated_at=? WHERE source=? AND symbol=? AND interval=?",
                (csv_cursor, now, source, symbol, interval),
            )

    def finish_history_batch(self, source, symbol, interval, cursor):
        now = int(time.time())
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "DELETE FROM history_pending_objects WHERE source=? AND symbol=? AND interval=?",
                (source, symbol, interval),
            )
            con.execute(
                """UPDATE history_jobs SET cursor=?,batch_start=NULL,batch_end=NULL,
                batch_cursor=NULL,batch_status=NULL,csv_keys='[]',csv_cursor=0,
                status=CASE WHEN ?<=target_start THEN 'complete' ELSE 'pending' END,
                completed_at=CASE WHEN ?<=target_start THEN ? ELSE NULL END,
                attempts=0,retry_at=0,error=NULL,updated_at=?
                WHERE source=? AND symbol=? AND interval=?""",
                (cursor, cursor, cursor, now, now, source, symbol, interval),
            )

    def fail_history_job(
        self,
        source,
        symbol,
        interval,
        error,
        *,
        blocked=False,
        max_failures=MAX_HISTORY_FAILURES,
        retry_base_seconds=30 * 60,
        retry_max_seconds=60 * 60,
    ):
        now = int(time.time())
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            current = con.execute(
                "SELECT attempts,failure_count FROM history_jobs WHERE source=? AND symbol=? AND interval=?",
                (source, symbol, interval),
            ).fetchone()
            if not current:
                return None
            attempts = current["attempts"] + 1
            failure_count = current["failure_count"] + 1
            exhausted = failure_count >= max_failures
            status = "exhausted" if exhausted else "coverage_gap" if blocked else "retry"
            retry_delay = min(
                retry_base_seconds * 2 ** min(failure_count - 1, 20), retry_max_seconds
            )
            con.execute(
                """UPDATE history_jobs SET status=?,attempts=?,failure_count=?,retry_at=?,
                error=?,updated_at=?,completed_at=NULL
                WHERE source=? AND symbol=? AND interval=?""",
                (
                    status,
                    attempts,
                    failure_count,
                    0 if exhausted else now + retry_delay,
                    str(error)[:1000],
                    now,
                    source,
                    symbol,
                    interval,
                ),
            )
            saved = con.execute(
                "SELECT * FROM history_jobs WHERE source=? AND symbol=? AND interval=?",
                (source, symbol, interval),
            ).fetchone()
            return dict(saved)

    def set_history_job_status(self, source, symbol, interval, status, error=None):
        now = int(time.time())
        with self.connect() as con:
            con.execute(
                """UPDATE history_jobs SET status=?,error=?,
                retry_at=CASE WHEN ? IN ('pending','provider_limit','complete') THEN 0 ELSE retry_at END,
                attempts=CASE WHEN ?='pending' THEN 0 ELSE attempts END,
                completed_at=CASE WHEN ?='complete' THEN COALESCE(completed_at,?)
                    WHEN ?='pending' THEN NULL ELSE completed_at END,updated_at=?
                WHERE source=? AND symbol=? AND interval=?""",
                (
                    status,
                    str(error)[:1000] if error else None,
                    status,
                    status,
                    status,
                    now,
                    status,
                    now,
                    source,
                    symbol,
                    interval,
                ),
            )

    def renew_live_lease(self, source, symbol, interval, owner, lease=3600):
        now = int(time.time())
        with self.connect() as con:
            return con.execute(
                "UPDATE live_leases SET until=? WHERE source=? AND symbol=? AND interval=? AND owner=? AND until>=?",
                (now + lease, source, symbol, interval, owner, now),
            ).rowcount == 1

    def begin_adjustment_rebase(self, source, symbol, evidence):
        stamp = int(time.time())
        encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.connect() as con:
            con.execute(
                """INSERT INTO adjustment_rebases(source,symbol,status,detected_at,evidence,updated_at)
                VALUES (?,?,'daily_pending',?,?,?)
                ON CONFLICT(source,symbol) DO UPDATE SET
                status=CASE WHEN adjustment_rebases.status IN ('complete','failed')
                    THEN 'daily_pending' ELSE adjustment_rebases.status END,
                detected_at=CASE WHEN adjustment_rebases.status IN ('complete','failed')
                    THEN excluded.detected_at ELSE adjustment_rebases.detected_at END,
                evidence=CASE WHEN adjustment_rebases.status IN ('complete','failed')
                    THEN excluded.evidence ELSE adjustment_rebases.evidence END,
                error=NULL,updated_at=excluded.updated_at""",
                (source, symbol, stamp, encoded, stamp),
            )

    def adjustment_rebase(self, source, symbol):
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM adjustment_rebases WHERE source=? AND symbol=?",
                (source, symbol),
            ).fetchone()
            return dict(row) if row else None

    def adjustment_rebase_outputs_pending(self, source, symbol):
        """Whether any interval or its public CSV still has adjustment work."""
        with self.connect() as con:
            if con.execute(
                "SELECT 1 FROM archives WHERE source=? AND symbol=? AND status='pending_repair' LIMIT 1",
                (source, symbol),
            ).fetchone():
                return True
            if con.execute(
                """SELECT 1 FROM quality WHERE source=? AND symbol=?
                AND kind='archive_csv_repair_pending' AND resolved=0 LIMIT 1""",
                (source, symbol),
            ).fetchone():
                return True
            batches = con.execute(
                """SELECT payload FROM archive_repair_batches
                WHERE status IN ('collecting','prepared','indexed','manifest_published')"""
            ).fetchall()
        for row in batches:
            payload = json.loads(row["payload"])
            if any(
                target["old"]["source"] == source and target["old"]["symbol"] == symbol
                for target in payload.get("targets", [])
            ):
                return True
        return False

    def set_adjustment_rebase(self, source, symbol, status, error=None):
        now = int(time.time())
        with self.connect() as con:
            con.execute(
                "UPDATE adjustment_rebases SET status=?,error=?,updated_at=? WHERE source=? AND symbol=?",
                (status, str(error)[:1000] if error else None, now, source, symbol),
            )

    def adjustment_rebases(self, status=None):
        with self.connect() as con:
            if status:
                rows = con.execute(
                    "SELECT * FROM adjustment_rebases WHERE status=? ORDER BY updated_at",
                    (status,),
                )
            else:
                rows = con.execute("SELECT * FROM adjustment_rebases ORDER BY updated_at")
            return [dict(row) for row in rows]

    def no_data_retry_at(self, source, symbol, interval):
        """Return a future retry time for a Yahoo bootstrap with no provider data."""
        if source != "yahoo":
            return None
        with self.connect() as con:
            row = con.execute(
                "SELECT status,error,retry_at FROM jobs WHERE source=? AND symbol=? AND interval=? AND kind='bootstrap'",
                (source, symbol, interval),
            ).fetchone()
        if (
            not row
            or row["status"] != "complete"
            or not (row["error"] or "").startswith("no_data:")
            or row["retry_at"] <= int(time.time())
        ):
            return None
        return int(row["retry_at"])

    def skip_no_data_job(self, job, provider, reason, retry_at):
        """Finish an empty first Yahoo page without retrying it every worker cycle."""
        now = int(time.time())
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if (
                not row
                or row["source"] != "yahoo"
                or row["kind"] != "bootstrap"
                or row["cursor"] is not None
                or row["status"] != "running"
                or row["lease_owner"] != job["lease_owner"]
                or row["revision"] != job["revision"]
                or row["lease_until"] < now
                or con.execute(
                    "SELECT 1 FROM staging WHERE job_id=? LIMIT 1", (job["id"],)
                ).fetchone()
            ):
                raise DataError("No-data bootstrap lease changed before it could be skipped")
            changed = con.execute(
                "UPDATE jobs SET status='complete',provider=?,error=?,retry_at=?,lease_owner=NULL,lease_until=0,updated_at=? WHERE id=? AND revision=? AND lease_owner=? AND status='running'",
                (
                    provider,
                    "no_data: " + str(reason)[:480],
                    retry_at,
                    time.time_ns(),
                    job["id"],
                    job["revision"],
                    job["lease_owner"],
                ),
            ).rowcount
            if not changed:
                raise DataError("No-data bootstrap lease changed before it could be skipped")

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

    def liquid_vn_symbols(self, stock_symbols, coverage=0.8, keep=0.8):
        """Return the most liquid share names after broad daily discovery.

        A stock is active when it has at least ten daily observations in the
        latest 45-day market window and traded within fourteen days of the
        newest stored VN stock candle. Selection stays dormant until daily
        series exist for ``coverage`` of the complete stock directory, then
        retains the top ``keep`` fraction by average daily traded value.
        """
        symbols = sorted(set(stock_symbols))
        if not symbols:
            return set()
        with self.connect() as con:
            con.execute(
                "CREATE TEMP TABLE requested_vn_stocks(symbol TEXT PRIMARY KEY) WITHOUT ROWID"
            )
            con.executemany("INSERT INTO requested_vn_stocks VALUES (?)", ((s,) for s in symbols))
            ready = con.execute(
                """SELECT COUNT(*) FROM series s JOIN requested_vn_stocks r USING(symbol)
                WHERE s.source='vn' AND s.interval='1D' AND s.status='ready'"""
            ).fetchone()[0]
            if ready < math.ceil(len(symbols) * coverage):
                return set()
            latest = con.execute(
                """SELECT MAX(c.time) FROM candles c JOIN requested_vn_stocks r USING(symbol)
                WHERE c.source='vn' AND c.interval='1D'"""
            ).fetchone()[0]
            if latest is None:
                return set()
            rows = con.execute(
                """SELECT c.symbol,COUNT(*) observations,MAX(c.time) latest,
                AVG(c.close*c.volume) turnover
                FROM candles c JOIN requested_vn_stocks r USING(symbol)
                WHERE c.source='vn' AND c.interval='1D' AND c.time>=?
                GROUP BY c.symbol HAVING observations>=10 AND latest>=?
                ORDER BY turnover DESC,c.symbol""",
                (latest - 45 * 86400, latest - 14 * 86400),
            ).fetchall()
        count = math.ceil(len(rows) * keep)
        return {row["symbol"] for row in rows[:count]}

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

    def volume_corrections(self, source=None, symbol=None, interval=None, start=None, end=None):
        where, args = [], []
        for key, value in (("source", source), ("symbol", symbol), ("interval", interval)):
            if value is not None:
                where.append(f"{key}=?")
                args.append(value)
        for expression, value in (("time>=?", start), ("time<=?", end)):
            if value is not None:
                where.append(expression)
                args.append(value)
        with self.connect() as con:
            return [
                dict(row)
                for row in con.execute(
                    "SELECT * FROM volume_corrections"
                    + (" WHERE " + " AND ".join(where) if where else "")
                    + " ORDER BY time,id",
                    args,
                )
            ]

    def restore_volume_corrections(self, records):
        from .volume_corrections import validate_record

        for record in records:
            validate_record(record)
        with self.connect() as con:
            for record in records:
                existing = con.execute(
                    "SELECT * FROM volume_corrections WHERE source=? AND symbol=? AND interval=? AND time=? AND revision=?",
                    tuple(record[k] for k in ("source", "symbol", "interval", "time", "revision")),
                ).fetchone()
                if existing and dict(existing) != record:
                    raise DataError("Conflicting volume correction receipt")
                con.execute(
                    "INSERT OR IGNORE INTO volume_corrections VALUES (?,?,?,?,?,?,?)",
                    tuple(
                        record[k]
                        for k in (
                            "id",
                            "source",
                            "symbol",
                            "interval",
                            "time",
                            "revision",
                            "evidence",
                        )
                    ),
                )
            if records:
                self.bump(con, market=True)

    def record_volume_correction(self, record, expected):
        self.record_volume_corrections([(record, expected)])

    def record_volume_corrections(self, candidates):
        from .volume_corrections import validate_record

        candidates = list(candidates)
        if not candidates:
            return
        if len(candidates) > 100 or len({record["id"] for record, _ in candidates}) != len(
            candidates
        ):
            raise DataError("Volume correction batch is too large or repeats a receipt")
        for record, expected in candidates:
            original, _ = validate_record(record)
            if original != expected:
                raise DataError("Volume correction snapshot differs from its original evidence")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            for record, expected in candidates:
                if not expected:
                    raise DataError("Volume correction requires an original day snapshot")
                current = [
                    Candle(**dict(row))
                    for row in con.execute(
                        "SELECT * FROM candles WHERE source=? AND symbol=? AND interval=? AND time>=? AND time<? ORDER BY time",
                        (
                            record["source"],
                            record["symbol"],
                            record["interval"],
                            expected[0].time // 86400 * 86400,
                            (expected[0].time // 86400 + 1) * 86400,
                        ),
                    )
                ]
                if current != expected:
                    raise DataError("Volume correction snapshot changed before publication")
                state = con.execute(
                    "SELECT * FROM series WHERE source=? AND symbol=? AND interval=?",
                    tuple(record[k] for k in ("source", "symbol", "interval")),
                ).fetchone()
                if (
                    not state
                    or state["status"] != "ready"
                    or state["revision"] != record["revision"]
                ):
                    raise DataError("Volume correction series basis changed")
                con.execute(
                    "INSERT INTO volume_corrections VALUES (?,?,?,?,?,?,?)",
                    tuple(
                        record[k]
                        for k in (
                            "id",
                            "source",
                            "symbol",
                            "interval",
                            "time",
                            "revision",
                            "evidence",
                        )
                    ),
                )
            self.bump(con, market=True)

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
        record = self.snapshot_adoption(state)
        return record["snapshot_provider"] if record else None

    def snapshot_hourly_range(self, state):
        if state["source"] != "yahoo" or state["interval"] != "1h":
            return None
        record = self.snapshot_adoption(state)
        return json.loads(record["evidence"]).get("yahoo_hourly_range") if record else None

    def snapshot_adoption(self, state):
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
        return dict(record)

    @staticmethod
    def validate_adoption(record):
        try:
            if record.get("interval") == "1D":
                from .daily_adoption import validate_daily_adoption

                validate_daily_adoption(record)
                return
            evidence = json.loads(record["evidence"])
            if evidence.get("kind") == "native_vci_snapshot_overlap":
                from .vci_adoption import validate_native_adoption

                validate_native_adoption(record)
                return
            hourly_range = evidence.get("yahoo_hourly_range")
            if hourly_range is not None and (
                hourly_range != "5d" or record["source"] != "yahoo" or record["interval"] != "1h"
            ):
                raise DataError("Invalid Yahoo hourly request policy")
            valid = (
                (
                    (
                        record["source"] == "vn"
                        and (
                            record["provider"] in {"vps", "vndirect", "dnse"}
                            or record["provider"] == "vci"
                            and record["interval"] == "1m"
                        )
                    )
                    or (
                        record["source"] == "yahoo"
                        and record["provider"] == "yahoo"
                        and evidence["kind"] == "exact_snapshot_overlap"
                    )
                )
                and (
                    record["interval"] == "1m"
                    and evidence["kind"] != "exact_vn_hourly_snapshot_overlap"
                    or record["interval"] == "1h"
                    and (
                        record["source"] == "yahoo"
                        and evidence["kind"] == "exact_snapshot_overlap"
                        or record["source"] == "vn"
                        and evidence["kind"] == "exact_vn_hourly_snapshot_overlap"
                    )
                )
                and record["snapshot_provider"] == "legacy-api"
                and bool(record["symbol"])
                and bool(record["revision"])
                and evidence["kind"]
                in {
                    "exact_snapshot_overlap",
                    "exact_vn_hourly_snapshot_overlap",
                    "exact_complete_sessions",
                    "corroborated_complete_sessions",
                }
                and type(evidence["matched_rows"]) is int
                and evidence["matched_rows"]
                >= (
                    (100 if record["interval"] == "1h" else 1000)
                    if evidence["kind"]
                    in {"exact_snapshot_overlap", "exact_vn_hourly_snapshot_overlap"}
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
            if valid and record["source"] == "vn" and record["interval"] == "1h":
                valid = (
                    type(evidence["completed_before"]) is int
                    and evidence["completed_before"] % 86400 == 0
                    and evidence["overlap_end"]
                    < evidence["completed_before"]
                    <= completed_vn_sessions(
                        datetime.fromtimestamp(evidence["verified_at_ns"] // 1_000_000_000, UTC)
                    )
                    and all(evidence[key] % 60 == 0 for key in ("overlap_start", "overlap_end"))
                )
            if valid and evidence["kind"] in {
                "exact_complete_sessions",
                "corroborated_complete_sessions",
            }:
                from .adoption import validate_complete_sessions

                validate_complete_sessions(record, evidence)
        except DataError as exc:
            raise DataError(f"Invalid snapshot adoption evidence: {exc}") from exc
        except (KeyError, TypeError, ValueError, OverflowError):
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
                self.bump(con, market=True)

    def validate_basis(self, candles, adoption=_ADOPTION_NOT_LOADED):
        if not candles:
            return
        if len({c.revision for c in candles}) > 1:
            raise DataError("Incompatible adjustment revisions")
        first = candles[0]
        if adoption is _ADOPTION_NOT_LOADED:
            with self.connect() as con:
                record = con.execute(
                    "SELECT * FROM snapshot_adoptions WHERE source=? AND symbol=? AND interval=? AND revision=?",
                    (first.source, first.symbol, first.interval, first.revision),
                ).fetchone()
            record = dict(record) if record else None
        else:
            record = adoption
        providers = {c.provider for c in candles}
        if (len(providers) > 1 and not record) or (
            record and not providers <= {record["snapshot_provider"], record["provider"]}
        ):
            raise DataError("Unverified providers in one adjustment revision")
        if record:
            self.validate_adoption(dict(record))
            verified = json.loads(record["evidence"])["verified_at_ns"]
            if any(
                record["snapshot_provider"] != record["provider"]
                and c.provider == record["snapshot_provider"]
                and not 0 < c.updated_at <= verified
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
        accepted = 0
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
                repair = None
                if state and state["status"] == "repairing" and verification:
                    repair = con.execute(
                        """SELECT id,provider,revision FROM jobs WHERE source=? AND symbol=?
                        AND interval=? AND kind='repair' AND status IN ('pending','running')""",
                        (c.source, c.symbol, c.interval),
                    ).fetchone()
                if (
                    state["revision"] != c.revision
                    or state["provider"] != c.provider
                    or state["status"] not in ("ready", "repairing")
                    or state["status"] == "repairing"
                    and not repair
                ):
                    raise DataError("Provider/revision change requires staged recovery")
                record = c.record()
                record["updated_at"] = stamp
                affected = con.execute(
                    statement, tuple(record[k] for k in COLUMNS)
                ).rowcount
                written += affected
                accepted += 1 if overwrite else affected
                if repair and repair["provider"] == state["provider"]:
                    # A long same-provider backfill must not freeze the public
                    # tail. Mirror each independently verified live candle into
                    # the replacement revision so atomic activation cannot drop
                    # observations that arrived while historical pages ran.
                    existing = con.execute(
                        f"SELECT {','.join(COLUMNS[4:10])} FROM staging WHERE job_id=? AND time=?",
                        (repair["id"], c.time),
                    ).fetchone()
                    if existing and tuple(existing) != tuple(record[key] for key in COLUMNS[4:10]):
                        raise DataError("Verified live candle conflicts with staged repair")
                    staged = record | {"revision": repair["revision"]}
                    con.execute(
                        f"INSERT OR REPLACE INTO staging(job_id,{','.join(COLUMNS)}) "
                        f"VALUES ({','.join('?' for _ in range(len(COLUMNS) + 1))})",
                        (repair["id"], *(staged[k] for k in COLUMNS)),
                    )
            if written:
                self.bump(con, market=True)
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
        return accepted

    def active_repair(self, source, symbol, interval):
        """Whether this series has a durable staged repair in progress."""
        with self.connect() as con:
            return bool(
                con.execute(
                    """SELECT 1 FROM series s JOIN jobs j
                    ON j.source=s.source AND j.symbol=s.symbol AND j.interval=s.interval
                    WHERE s.source=? AND s.symbol=? AND s.interval=?
                    AND s.status='repairing' AND j.kind='repair'
                    AND j.status IN ('pending','running')""",
                    (source, symbol, interval),
                ).fetchone()
            )

    def read(self, source, symbol, interval, start=None, end=None, limit=None, forward=False):
        return self.read_many(
            source,
            [symbol],
            interval,
            start=start,
            end=end,
            limit=limit,
            forward=forward,
        )[symbol]

    def eligible_archive_group(self, source, symbol, interval, floor):
        """Load only the oldest expired provider/revision partition for a series."""
        period = (
            "strftime('%Y',time,'unixepoch')"
            if interval == "1D"
            else "strftime('%Y-%m',time,'unixepoch')"
        )
        with self.connect() as con:
            basis = con.execute(
                f"SELECT provider,revision,MIN(time) AS first_time FROM candles "
                f"WHERE source=? AND symbol=? AND interval=? AND time<? "
                f"GROUP BY provider,revision,{period} ORDER BY first_time LIMIT 1",
                (source, symbol, interval, floor),
            ).fetchone()
            if not basis:
                return []
            value = datetime.fromtimestamp(basis["first_time"], UTC)
            start = (
                datetime(value.year, 1, 1, tzinfo=UTC)
                if interval == "1D"
                else datetime(value.year, value.month, 1, tzinfo=UTC)
            )
            end = (
                datetime(value.year + 1, 1, 1, tzinfo=UTC)
                if interval == "1D" or value.month == 12
                else datetime(value.year, value.month + 1, 1, tzinfo=UTC)
            )
            rows = con.execute(
                f"SELECT {','.join(COLUMNS)} FROM candles WHERE source=? AND symbol=? "
                "AND interval=? AND time>=? AND time<? AND time<? AND provider=? "
                "AND revision=? ORDER BY time",
                (
                    source,
                    symbol,
                    interval,
                    int(start.timestamp()),
                    int(end.timestamp()),
                    floor,
                    basis["provider"],
                    basis["revision"],
                ),
            ).fetchall()
            return [Candle(**dict(row)) for row in rows]

    def read_many(self, source, symbols, interval, start=None, end=None, limit=None, forward=False):
        """Read raw native candle rows for one or many symbols via the shared loader."""
        from .data_loading import SQLiteDataLoader

        return SQLiteDataLoader(self).read_candles_many(
            source,
            symbols,
            interval,
            start=start,
            end=end,
            limit=limit,
            forward=forward,
        )

    def archives(self, source=None, symbol=None, interval=None, start=None, end=None):
        where, args = ["status IN ('published','pending_repair','historical_snapshot')"], []
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

    def archive_manifest_snapshot(self):
        """Read one archive index generation for a manifest publication."""
        with self.connect() as con:
            con.execute("BEGIN")
            objects = [
                dict(row)
                for row in con.execute(
                    "SELECT * FROM archives WHERE status IN "
                    "('published','pending_repair','historical_snapshot') ORDER BY end DESC"
                )
            ]
            state = con.execute(
                "SELECT generation FROM archive_manifest_state WHERE id=1"
            ).fetchone()
            return objects, int(state[0])

    @staticmethod
    def mark_archive_manifest_dirty(con):
        con.execute(
            "UPDATE archive_manifest_state SET generation=generation+1 WHERE id=1"
        )

    def mark_archive_manifest_published(self, generation):
        with self.connect() as con:
            con.execute(
                "UPDATE archive_manifest_state SET published_generation="
                "MAX(published_generation,?) WHERE id=1",
                (generation,),
            )

    def archive_manifest_is_dirty(self):
        with self.connect() as con:
            row = con.execute(
                "SELECT generation>published_generation FROM archive_manifest_state WHERE id=1"
            ).fetchone()
            return bool(row and row[0])

    def resolve_archive_manifest_findings(self):
        with self.connect() as con:
            con.execute(
                "UPDATE quality SET resolved=1 WHERE kind='archive_manifest_pending' "
                "AND (SELECT generation=published_generation FROM archive_manifest_state WHERE id=1)"
            )

    @staticmethod
    def _enqueue_archive_repairs(con, identity, provider, revision):
        source, symbol, interval = identity
        records = con.execute(
            "SELECT id,start FROM archives WHERE source=? AND symbol=? AND interval=? AND status='pending_repair' ORDER BY end,start,id",
            identity,
        ).fetchall()
        now = int(time.time())
        queued = []
        for record in records:
            kind = f"archive_repair:{record['id']}:{revision}"
            existing = con.execute(
                "SELECT * FROM jobs WHERE source=? AND symbol=? AND interval=? AND kind=?",
                (*identity, kind),
            ).fetchone()
            if existing and existing["status"] not in ("complete", "cancelled"):
                if existing["provider"] != provider:
                    if existing["lease_until"] > now:
                        continue
                    con.execute("DELETE FROM staging WHERE job_id=?", (existing["id"],))
                    job_id, job_revision = str(uuid.uuid4()), str(uuid.uuid4())
                    con.execute(
                        "UPDATE jobs SET id=?,provider=?,revision=?,cursor=NULL,floor=?,status='pending',attempts=0,retry_at=0,lease_owner=NULL,lease_until=0,created_at=?,updated_at=?,error=NULL WHERE id=?",
                        (
                            job_id,
                            provider,
                            job_revision,
                            record["start"],
                            now,
                            time.time_ns(),
                            existing["id"],
                        ),
                    )
                    queued.append(job_id)
                else:
                    queued.append(existing["id"])
                continue
            job_id, job_revision = str(uuid.uuid4()), str(uuid.uuid4())
            if existing:
                con.execute("DELETE FROM staging WHERE job_id=?", (existing["id"],))
                con.execute(
                    "UPDATE jobs SET id=?,provider=?,revision=?,cursor=NULL,floor=?,status='pending',attempts=0,retry_at=0,lease_owner=NULL,lease_until=0,created_at=?,updated_at=?,error=NULL WHERE id=?",
                    (
                        job_id,
                        provider,
                        job_revision,
                        record["start"],
                        now,
                        time.time_ns(),
                        existing["id"],
                    ),
                )
            else:
                con.execute(
                    "INSERT INTO jobs(id,source,symbol,interval,kind,provider,revision,floor,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        source,
                        symbol,
                        interval,
                        kind,
                        provider,
                        job_revision,
                        record["start"],
                        now,
                        time.time_ns(),
                    ),
                )
            queued.append(job_id)
        return queued

    @staticmethod
    def _archive_repair_batch_dict(row):
        if row is None:
            return None
        value = dict(row)
        value["payload"] = json.loads(value["payload"])
        return value

    def ensure_archive_repair_batch(
        self, source=None, symbol=None, interval=None, max_items=100
    ):
        if not 1 <= max_items <= 100:
            raise DataError("Archive repair batch must contain one to 100 records", 400)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            active = con.execute(
                "SELECT * FROM archive_repair_batches WHERE status IN ('collecting','prepared','indexed','manifest_published') ORDER BY created_at LIMIT 1"
            ).fetchone()
            if active:
                return self._archive_repair_batch_dict(active)
            where = [
                "a.status='pending_repair'",
                "s.status='ready'",
                "NOT EXISTS (SELECT 1 FROM adjustment_rebases r WHERE r.source=a.source "
                "AND r.symbol=a.symbol AND r.status IN "
                "('daily_pending','hourly_pending','minute_pending'))",
            ]
            args = []
            for column, value in (("a.source", source), ("a.symbol", symbol), ("a.interval", interval)):
                if value is not None:
                    if column == "a.symbol" and isinstance(value, (list, tuple, set)):
                        symbols = sorted(set(value))
                        if not symbols:
                            return None
                        where.append(f"{column} IN ({','.join('?' for _ in symbols)})")
                        args.extend(symbols)
                    else:
                        where.append(f"{column}=?")
                        args.append(value)
            candidate = con.execute(
                "SELECT a.* FROM archives a JOIN series s USING(source,symbol,interval) WHERE "
                + " AND ".join(where)
                + " ORDER BY a.end,a.symbol,a.id LIMIT 1",
                args,
            ).fetchone()
            if not candidate:
                return None
            source_id, symbol_id, interval_id = (
                candidate["source"],
                candidate["symbol"],
                candidate["interval"],
            )

            def period_bounds(stamp):
                value = datetime.fromtimestamp(stamp, UTC)
                if interval_id == "1m":
                    start = datetime(value.year, value.month, value.day, tzinfo=UTC)
                    end = datetime.fromtimestamp(
                        int(start.timestamp()) + 86400, UTC
                    )
                else:
                    start = datetime(value.year, 1, 1, tzinfo=UTC)
                    end = datetime(value.year + 1, 1, 1, tzinfo=UTC)
                return int(start.timestamp()), int(end.timestamp())

            group_start = period_bounds(candidate["start"])[0]
            # Archive ranges use inclusive candle timestamps. Convert the last
            # candle to its containing public-CSV period before expanding the
            # exclusive grouping boundary.
            group_end = period_bounds(candidate["end"])[1]
            # Repair every pending object that contributes to the same public
            # CSV period. Hourly Parquet is monthly but its legacy CSV is yearly;
            # splitting a ticker-year across batches makes History.read reject
            # the yearly CSV while sibling objects remain pending.
            while True:
                group_where = [
                    *where,
                    "a.source=?",
                    "a.symbol=?",
                    "a.interval=?",
                    "a.start<?",
                    "a.end>=?",
                ]
                group_args = [
                    *args,
                    source_id,
                    symbol_id,
                    interval_id,
                    group_end,
                    group_start,
                ]
                records = [
                    dict(row)
                    for row in con.execute(
                        "SELECT a.*,s.provider AS current_provider,s.revision AS current_revision "
                        "FROM archives a JOIN series s USING(source,symbol,interval) WHERE "
                        + " AND ".join(group_where)
                        + " ORDER BY a.end,a.symbol,a.id",
                        group_args,
                    )
                ]
                expanded_start = min(group_start, period_bounds(min(r["start"] for r in records))[0])
                expanded_end = max(
                    group_end, period_bounds(max(r["end"] for r in records))[1]
                )
                if (expanded_start, expanded_end) == (group_start, group_end):
                    break
                group_start, group_end = expanded_start, expanded_end
            if len(records) > max_items:
                raise DataError(
                    f"One public CSV repair period has {len(records)} Parquet objects; "
                    f"batch limit is {max_items}"
                )
            targets = []
            for record in records:
                identity = (record["source"], record["symbol"], record["interval"])
                provider, revision = record.pop("current_provider"), record.pop("current_revision")
                self._enqueue_archive_repairs(con, identity, provider, revision)
                kind = f"archive_repair:{record['id']}:{revision}"
                job = con.execute(
                    "SELECT id,provider FROM jobs WHERE source=? AND symbol=? AND interval=? AND kind=?",
                    (*identity, kind),
                ).fetchone()
                if not job or job["provider"] != provider:
                    return None
                targets.append(
                    {
                        "old": record,
                        "provider": provider,
                        "revision": revision,
                        "job_id": job["id"],
                        "replacement": None,
                        "evidence": None,
                    }
                )
            batch_id = str(uuid.uuid4())
            now_ns = time.time_ns()
            payload = json.dumps(
                {"version": 1, "targets": targets},
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            con.execute(
                "INSERT INTO archive_repair_batches(id,status,payload,created_at,updated_at) VALUES (?,'collecting',?,?,?)",
                (batch_id, payload, int(time.time()), now_ns),
            )
            return self._archive_repair_batch_dict(
                con.execute("SELECT * FROM archive_repair_batches WHERE id=?", (batch_id,)).fetchone()
            )

    def claim_archive_repair_batch(self, owner, lease=1800, force=False):
        now = int(time.time())
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM archive_repair_batches WHERE status IN ('collecting','prepared','indexed','manifest_published') AND (? OR retry_at<=?) AND lease_until<=? ORDER BY created_at LIMIT 1",
                (int(force), now, now),
            ).fetchone()
            if not row:
                return None
            con.execute(
                "UPDATE archive_repair_batches SET lease_owner=?,lease_until=?,retry_at=0,updated_at=? WHERE id=?",
                (owner, now + lease, time.time_ns(), row["id"]),
            )
            return self._archive_repair_batch_dict(
                con.execute("SELECT * FROM archive_repair_batches WHERE id=?", (row["id"],)).fetchone()
            )

    def save_archive_repair_batch(self, batch_id, owner, status, payload, error=None):
        if status not in ("collecting", "prepared"):
            raise DataError("Invalid archive repair batch checkpoint")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            changed = con.execute(
                "UPDATE archive_repair_batches SET status=?,payload=?,error=?,retry_at=0,lease_until=?,updated_at=? WHERE id=? AND lease_owner=? AND lease_until>=? AND status IN ('collecting','prepared')",
                (status, encoded, error, int(time.time()) + 1800, time.time_ns(), batch_id, owner, int(time.time())),
            ).rowcount
            if not changed:
                raise DataError("Archive repair batch lease expired or changed")

    def release_archive_repair_batch(self, batch_id, owner, error, retry_at):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "UPDATE archive_repair_batches SET error=?,retry_at=?,lease_owner=NULL,lease_until=0,updated_at=? WHERE id=? AND lease_owner=? AND status IN ('collecting','prepared','indexed','manifest_published')",
                (str(error)[:500], retry_at, time.time_ns(), batch_id, owner),
            )

    def fail_archive_repair_batch(self, batch_id, owner, error):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            batch = con.execute(
                "SELECT status,payload FROM archive_repair_batches WHERE id=? AND lease_owner=?",
                (batch_id, owner),
            ).fetchone()
            if not batch or batch["status"] not in ("collecting", "prepared"):
                raise DataError("Archive repair batch cannot be failed from its current state")
            payload = json.loads(batch["payload"])
            for target in payload.get("targets", []):
                job_id = target.get("job_id")
                if job_id:
                    con.execute("DELETE FROM staging WHERE job_id=?", (job_id,))
                    con.execute(
                        "UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until=0,error=?,updated_at=? WHERE id=? AND status<>'complete'",
                        (str(error)[:500], time.time_ns(), job_id),
                    )
            changed = con.execute(
                "UPDATE archive_repair_batches SET status='failed',error=?,retry_at=0,lease_owner=NULL,lease_until=0,updated_at=? WHERE id=? AND lease_owner=? AND status IN ('collecting','prepared')",
                (str(error)[:500], time.time_ns(), batch_id, owner),
            ).rowcount
            if not changed:
                raise DataError("Archive repair batch cannot be failed from its current state")

    def apply_archive_repair_batch(self, batch_id, owner):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            batch = con.execute(
                "SELECT * FROM archive_repair_batches WHERE id=?", (batch_id,)
            ).fetchone()
            if not batch or batch["lease_owner"] != owner or batch["lease_until"] < int(time.time()):
                raise DataError("Archive repair batch lease expired")
            if batch["status"] in ("indexed", "manifest_published"):
                return self._archive_repair_batch_dict(batch)
            if batch["status"] != "prepared":
                raise DataError("Archive repair batch is not prepared")
            payload = json.loads(batch["payload"])
            targets = payload.get("targets")
            if payload.get("version") != 1 or not isinstance(targets, list) or not targets:
                raise DataError("Archive repair receipt is invalid")
            if len(targets) > 100 or any(not target.get("replacement") for target in targets):
                raise DataError("Archive repair receipt is incomplete")
            seen = set()
            for target in targets:
                old, obj = target["old"], target["replacement"]
                old_id = old["id"]
                identity = (old["source"], old["symbol"], old["interval"])
                if old_id in seen:
                    raise DataError("Archive repair receipt repeats a target")
                seen.add(old_id)
                current_old = con.execute("SELECT * FROM archives WHERE id=?", (old_id,)).fetchone()
                if not current_old or dict(current_old) != old or old["status"] != "pending_repair":
                    raise DataError("Archive repair target changed after batch capture")
                state = con.execute(
                    "SELECT provider,revision,status FROM series WHERE source=? AND symbol=? AND interval=?",
                    identity,
                ).fetchone()
                if not state or (state["provider"], state["revision"], state["status"]) != (
                    target["provider"],
                    target["revision"],
                    "ready",
                ):
                    raise DataError("Archive repair series basis changed during batch")
                if (
                    (obj["source"], obj["symbol"], obj["interval"], obj["provider"], obj["revision"])
                    != (*identity, target["provider"], target["revision"])
                    or obj["status"] != "published"
                    or obj["start"] != old["start"]
                    or obj["end"] != old["end"]
                    or obj["row_count"] != old["row_count"]
                ):
                    raise DataError("Archive replacement does not preserve its exact target range")
                columns = tuple(obj)
                con.execute(
                    f"INSERT OR IGNORE INTO archives({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    tuple(obj.values()),
                )
                con.execute(
                    "UPDATE archives SET status='superseded' WHERE id=? AND status='pending_repair'",
                    (old_id,),
                )
                job = con.execute("SELECT id FROM jobs WHERE id=?", (target["job_id"],)).fetchone()
                if job:
                    con.execute("DELETE FROM staging WHERE job_id=?", (target["job_id"],))
                    con.execute(
                        "UPDATE jobs SET status='complete',lease_owner=NULL,lease_until=0,retry_at=0,error=NULL,updated_at=? WHERE id=?",
                        (time.time_ns(), target["job_id"]),
                    )
            con.execute(
                "UPDATE archive_repair_batches SET status='indexed',error=NULL,retry_at=0,updated_at=? WHERE id=?",
                (time.time_ns(), batch_id),
            )
            self.bump(con, market=True)
            return self._archive_repair_batch_dict(
                con.execute("SELECT * FROM archive_repair_batches WHERE id=?", (batch_id,)).fetchone()
            )

    def mark_archive_repair_manifest_published(self, batch_id, owner):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            changed = con.execute(
                "UPDATE archive_repair_batches SET status='manifest_published',error=NULL,retry_at=0,updated_at=? WHERE id=? AND status='indexed' AND lease_owner=? AND lease_until>=?",
                (time.time_ns(), batch_id, owner, int(time.time())),
            ).rowcount
            if not changed:
                row = con.execute(
                    "SELECT status,lease_owner FROM archive_repair_batches WHERE id=?", (batch_id,)
                ).fetchone()
                if not row or row["status"] != "manifest_published" or row["lease_owner"] != owner:
                    raise DataError("Archive repair batch lost its manifest checkpoint")

    def complete_archive_repair_batch(self, batch_id, owner):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM archive_repair_batches WHERE id=?", (batch_id,)
            ).fetchone()
            if not row or row["status"] != "manifest_published" or row["lease_owner"] != owner:
                raise DataError("Archive repair batch manifest is not published")
            payload = json.loads(row["payload"])
            identities = {
                (target["old"]["source"], target["old"]["symbol"], target["old"]["interval"])
                for target in payload["targets"]
            }
            for identity in identities:
                state = con.execute(
                    "SELECT provider,revision FROM series WHERE source=? AND symbol=? AND interval=?",
                    identity,
                ).fetchone()
                if state and not con.execute(
                    "SELECT 1 FROM archives WHERE source=? AND symbol=? AND interval=? AND status IN ('published','pending_repair') AND (status='pending_repair' OR provider<>? OR revision<>?)",
                    (*identity, state["provider"], state["revision"]),
                ).fetchone():
                    con.execute(
                        "UPDATE quality SET resolved=1 WHERE source=? AND symbol=? AND interval=? AND kind IN ('archive_repair_pending','imported_revision_boundary')",
                        identity,
                    )
            con.execute(
                "UPDATE archive_repair_batches SET status='published',error=NULL,retry_at=0,lease_owner=NULL,lease_until=0,updated_at=? WHERE id=?",
                (time.time_ns(), batch_id),
            )
            self.bump(con, market=True)

    def pending_archive_repair_batch(self):
        with self.connect() as con:
            return self._archive_repair_batch_dict(
                con.execute(
                    "SELECT * FROM archive_repair_batches WHERE status IN ('collecting','prepared','indexed','manifest_published') ORDER BY created_at LIMIT 1"
                ).fetchone()
            )

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
                accepted_providers = {state["provider"]} if state else set()
                if state and state["status"] == "ready" and state["revision"] == obj["revision"]:
                    adoption = con.execute(
                        "SELECT snapshot_provider FROM snapshot_adoptions WHERE source=? "
                        "AND symbol=? AND interval=? AND revision=? AND provider=?",
                        (
                            obj["source"],
                            obj["symbol"],
                            obj["interval"],
                            state["revision"],
                            state["provider"],
                        ),
                    ).fetchone()
                    if adoption:
                        accepted_providers.add(adoption["snapshot_provider"])
                if (
                    not state
                    or state["status"] != "ready"
                    or state["revision"] != obj["revision"]
                    or obj["provider"] not in accepted_providers
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
            self.bump(con, market=True)

    def publish_archives(self, objects, *, require_current=False):
        """Index a prepared archive batch with one short SQLite transaction."""
        objects = list(objects)
        if not objects:
            return
        identities = {
            (obj["source"], obj["symbol"], obj["interval"], obj["provider"], obj["revision"])
            for obj in objects
        }
        if len(identities) != 1:
            raise DataError("An archive publication batch must contain one series basis")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if require_current:
                source, symbol, interval, provider, revision = next(iter(identities))
                state = con.execute(
                    "SELECT provider,revision,status FROM series WHERE source=? AND symbol=? AND interval=?",
                    (source, symbol, interval),
                ).fetchone()
                if not state or (state["provider"], state["revision"], state["status"]) != (
                    provider,
                    revision,
                    "ready",
                ):
                    raise DataError("Archive batch basis changed before publication")
            for obj in objects:
                con.execute(
                    "INSERT OR IGNORE INTO tickers(source,symbol) VALUES (?,?)",
                    (obj["source"], obj["symbol"]),
                )
                columns = tuple(obj)
                con.execute(
                    f"INSERT OR IGNORE INTO archives({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    tuple(obj[column] for column in columns),
                )
            self.bump(con, market=True)

    def publish_partitioned_history(self, obj, parent, gaps):
        """Atomically refine a coarse marker; retain the unresolved invalid dates."""
        parent_detail = self.validate_history_gap(parent)
        details = [self.validate_history_gap(gap) for gap in gaps]
        identity = (parent["source"], parent["symbol"], parent["interval"])

        def valid_partition(gap):
            shared = (
                (gap["source"], gap["symbol"], gap["interval"]) == identity
                and parent["start"] <= gap["start"] <= gap["end"] <= parent["end"]
                and gap["evidence"].get("original_gap") == parent
                and gap["evidence"].get("snapshot_id") == obj["id"]
            )
            if gap["evidence"].get("kind") == "public_year_partition_basis":
                return shared and (
                    (gap["start"], gap["end"]) == (parent["start"], parent["end"])
                    and gap["evidence"].get("revision") == obj["revision"]
                )
            return shared and (
                gap["evidence"].get("kind") == "partitioned_public_year"
                and gap["start"] % 86400 == 0
                and gap["end"] == gap["start"] + 86399
            )

        if (
            len(gaps) < 2
            or sum(gap["evidence"].get("kind") == "public_year_partition_basis" for gap in gaps)
            != 1
            or obj["status"] != "historical_snapshot"
            or obj["provider"] != "legacy-api"
            or obj["interval"] != "1D"
            or (obj["source"], obj["symbol"], obj["interval"]) != identity
            or not parent["start"] <= obj["start"] <= obj["end"] <= parent["end"]
            or any(not valid_partition(gap) for gap in gaps)
        ):
            raise DataError("Invalid public history partition")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            marker = con.execute(
                "SELECT id FROM quality WHERE source=? AND symbol=? AND interval=? AND kind='history_unavailable' AND detail=? AND resolved=0",
                (*identity, parent_detail),
            ).fetchone()
            if not marker:
                raise DataError("Original unavailable-history marker changed")
            state = con.execute(
                "SELECT revision FROM series WHERE source=? AND symbol=? AND interval=?", identity
            ).fetchone()
            if state and state["revision"] == obj["revision"]:
                raise DataError("Public recovery cannot reuse the active revision")
            cols = tuple(obj)
            con.execute("INSERT OR IGNORE INTO tickers(source,symbol) VALUES (?,?)", identity[:2])
            con.execute(
                f"INSERT INTO archives({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                tuple(obj.values()),
            )
            # The coarse observation is retained as evidence. Its replacement
            # markers remain active: this does not certify a healed full year.
            con.execute("UPDATE quality SET resolved=1 WHERE id=?", (marker["id"],))
            stamp = int(time.time())
            con.executemany(
                "INSERT INTO quality(source,symbol,interval,kind,detail,first_seen,last_seen) VALUES (?,?,?,'history_unavailable',?,?,?)",
                [(*identity, detail, stamp, stamp) for detail in details],
            )
            self.bump(con, market=True)

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
            self.bump(con, market=True)

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
            self._enqueue_archive_repairs(
                con, (source, symbol, interval), state["provider"], state["revision"]
            )
            if count:
                self.bump(con, market=True)
                self.mark_archive_manifest_dirty(con)
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
            self.bump(con, market=True)

    def finding(self, source, symbol, interval, kind, detail, *, resolved=False):
        now = int(time.time())
        with self.connect() as con:
            con.execute(
                "INSERT INTO quality(source,symbol,interval,kind,detail,first_seen,last_seen,resolved) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(source,symbol,interval,kind,detail) DO UPDATE SET last_seen=excluded.last_seen,resolved=excluded.resolved",
                (source, symbol, interval, kind, detail, now, now, int(resolved)),
            )

    def record_volume_proofs(self, proofs):
        from .vci_volume import validate_volume_proof

        for proof in proofs:
            corrected = validate_volume_proof(proof)
            self.finding(
                "vn",
                corrected.symbol,
                "1m",
                "verified_vci_volume",
                json.dumps(proof, sort_keys=True, allow_nan=False),
                resolved=True,
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
                self.bump(con, market=True)

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
            self.bump(con, market=True)

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

    def cancel_superseded_bootstrap(self, source, symbol, interval, floor):
        """Retire an untouched placeholder predating a verified snapshot handoff.

        This is cancellation, not certification of complete session coverage.
        Preserve started jobs and every staged/published record for review.
        """
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            job = con.execute(
                """SELECT * FROM jobs WHERE source=? AND symbol=? AND interval=?
                AND kind='bootstrap' AND status='pending' AND provider IS NULL
                AND cursor IS NULL AND attempts=0 AND lease_owner IS NULL
                AND lease_until=0 AND NOT EXISTS
                (SELECT 1 FROM staging s WHERE s.job_id=jobs.id)""",
                (source, symbol, interval),
            ).fetchone()
            state = con.execute(
                "SELECT * FROM series WHERE source=? AND symbol=? AND interval=?",
                (source, symbol, interval),
            ).fetchone()
            if (
                not job
                or not state
                or state["status"] != "ready"
                or job["revision"] == state["revision"]
            ):
                return False
            record = con.execute(
                """SELECT * FROM snapshot_adoptions WHERE source=? AND symbol=?
                AND interval=? AND revision=? AND provider=?""",
                (source, symbol, interval, state["revision"], state["provider"]),
            ).fetchone()
            if not record:
                return False
            self.validate_adoption(dict(record))
            evidence = json.loads(record["evidence"])
            if (
                job["created_at"] >= evidence["verified_at_ns"] // 1_000_000_000
                or evidence["snapshot_start"] > floor
            ):
                return False
            con.execute(
                """UPDATE jobs SET status='cancelled',updated_at=?,error=? WHERE id=?""",
                (
                    time.time_ns(),
                    "Unstarted bootstrap superseded by verified snapshot adoption",
                    job["id"],
                ),
            )
            self.bump(con)
            return True

    def cancel_empty_bootstrap(self, source, symbol, interval, reason):
        """Cancel an unleased bootstrap that has no staged page to preserve."""
        now = int(time.time())
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            state = con.execute(
                "SELECT 1 FROM series WHERE source=? AND symbol=? AND interval=?",
                (source, symbol, interval),
            ).fetchone()
            job = con.execute(
                """SELECT id FROM jobs WHERE source=? AND symbol=? AND interval=?
                AND kind='bootstrap' AND status IN ('pending','running') AND lease_until<=?
                AND NOT EXISTS (SELECT 1 FROM staging WHERE staging.job_id=jobs.id)""",
                (source, symbol, interval, now),
            ).fetchone()
            if state or not job:
                return False
            changed = con.execute(
                """UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until=0,
                error=?,updated_at=? WHERE id=? AND status IN ('pending','running')
                AND lease_until<=?""",
                (reason[:500], time.time_ns(), job["id"], now),
            ).rowcount
            if changed:
                self.bump(con)
            return bool(changed)

    def claim_job(self, owner, lease=120, allowed=None):
        now = int(time.time())
        if allowed is not None and not allowed:
            return None
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            extra = ""
            if allowed is not None:
                # A catalog-wide worker can have more than SQLite's expression
                # depth or host-parameter limit. A connection-local table keeps
                # eligibility bounded and does not publish scheduler state.
                con.execute(
                    "CREATE TEMP TABLE allowed_jobs(source TEXT,symbol TEXT,interval TEXT,priority INTEGER NOT NULL,PRIMARY KEY(source,symbol,interval)) WITHOUT ROWID"
                )
                ordered = list(dict.fromkeys(allowed))
                con.executemany(
                    "INSERT INTO allowed_jobs VALUES (?,?,?,?)",
                    [(*identity, priority) for priority, identity in enumerate(ordered)],
                )
                extra = " AND EXISTS (SELECT 1 FROM allowed_jobs a WHERE a.source=jobs.source AND a.symbol=jobs.symbol AND a.interval=jobs.interval)"
                # Give every configured series one recent page before resuming
                # deep pagination. Otherwise a catalog worker exhausts years of
                # one high-priority ticker while hundreds of public tails have
                # never been initialized. Likewise, a curated no-data ticker's
                # retry must not jump ahead of untouched catalog tails.
                order = " ORDER BY (cursor IS NOT NULL),(attempts<>0),(SELECT priority FROM allowed_jobs a WHERE a.source=jobs.source AND a.symbol=jobs.symbol AND a.interval=jobs.interval),updated_at,created_at LIMIT 1"
            else:
                order = " ORDER BY updated_at,created_at LIMIT 1"
            row = con.execute(
                "SELECT * FROM jobs WHERE status IN ('pending','running') AND kind NOT LIKE 'archive_repair%' AND retry_at<=? AND lease_until<=?"
                + extra
                + order,
                (now, now),
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

    def release_worker_leases(self, owner):
        """Release an exiting worker's claims without changing durable progress."""
        if not isinstance(owner, str) or not owner:
            raise DataError("A worker lease owner is required", 400)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("DELETE FROM live_leases WHERE owner=?", (owner,))
            con.execute(
                "UPDATE jobs SET status='pending',lease_owner=NULL,lease_until=0 WHERE lease_owner=? AND status='running'",
                (owner,),
            )

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

    def advance_job_floor(self, job, floor):
        """Move a leased recent-data job forward with its rolling retention window."""
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if (
                not row
                or row["kind"] not in ("bootstrap", "repair")
                or row["lease_owner"] != job["lease_owner"]
                or row["revision"] != job["revision"]
                or row["lease_until"] < int(time.time())
            ):
                raise DataError("Recent repair lease expired or superseded")
            floor = max(row["floor"], floor)
            con.execute("UPDATE jobs SET floor=? WHERE id=?", (floor, job["id"]))
            return dict(row) | {"floor": floor}

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
            if (
                not staged[2]
                or staged[1] < row["floor"]
                or (latest is not None and staged[1] < latest)
            ):
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
                changed_archives = con.execute(
                    "UPDATE archives SET status='pending_repair' WHERE source=? AND symbol=? AND interval=? AND revision<>? AND status='published'",
                    (*ident, row["revision"]),
                ).rowcount
                if changed_archives:
                    self.mark_archive_manifest_dirty(con)
                self._enqueue_archive_repairs(con, ident, row["provider"], row["revision"])
            con.execute("DELETE FROM staging WHERE job_id=?", (job["id"],))
            con.execute(
                "UPDATE jobs SET status='complete',lease_owner=NULL,lease_until=0,updated_at=? WHERE id=?",
                (time.time_ns(), job["id"]),
            )
            self.bump(con, market=True)
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

    def cancel_leased_job(self, job, reason):
        """Cancel exactly the currently leased job after a deliberate handoff."""
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if (
                not row
                or row["status"] != "running"
                or row["lease_owner"] != job.get("lease_owner")
                or row["revision"] != job["revision"]
                or row["lease_until"] < int(time.time())
            ):
                raise DataError("Job lease expired or superseded before cancellation")
            con.execute("DELETE FROM staging WHERE job_id=?", (job["id"],))
            con.execute(
                """UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until=0,
                error=?,updated_at=? WHERE id=?""",
                (reason[:500], time.time_ns(), job["id"]),
            )
            self.bump(con)

    def schedule(self, source, symbol, interval, when):
        col = {"1D": "next_1d", "1h": "next_1h", "1m": "next_1m"}[interval]
        with self.connect() as con:
            con.execute(
                f"UPDATE tickers SET {col}=? WHERE source=? AND symbol=?", (when, source, symbol)
            )

    def live_check_backlog(self, source, interval, since_ns):
        """Count enabled published series without a recent successful upstream check."""
        with self.connect() as con:
            return con.execute(
                """SELECT COUNT(*) FROM series s JOIN tickers t
                ON t.source=s.source AND t.symbol=s.symbol
                WHERE s.source=? AND s.interval=? AND t.enabled=1
                AND s.status IN ('ready','repairing')
                AND NOT EXISTS (SELECT 1 FROM source_checks c
                    WHERE c.source=s.source AND c.symbol=s.symbol
                    AND c.interval=s.interval AND c.outcome='succeeded'
                    AND c.successful_at_ns>=?)
                AND NOT EXISTS (SELECT 1 FROM jobs j
                    WHERE j.source=s.source AND j.symbol=s.symbol
                    AND j.interval=s.interval AND j.kind='bootstrap'
                    AND j.updated_at>=?)""",
                (source, interval, since_ns, since_ns),
            ).fetchone()[0]

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
        updated = 0
        for source in sources:
            try:
                with self.connect() as con:
                    due_at = int(time.time())
                    updated += con.execute(
                        f"""UPDATE tickers SET {col}=?
                        WHERE source=?
                        AND EXISTS (
                          SELECT 1 FROM series s WHERE s.source=tickers.source
                          AND s.symbol=tickers.symbol AND s.status='ready'
                        )""",
                        (due_at, source),
                    ).rowcount
            except Exception as exc:
                # Rust commits each source update separately and returns the
                # first source-local failure without undoing earlier sources.
                raise RefreshScheduleError(source) from exc
        return {"interval": col, "sources": list(sources), "updated": updated}

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

    def health_summary(self, now=None):
        """Return exact retained-candle counts and Rust-style major-ticker sync times."""
        now = now or datetime.now(UTC)
        is_vn_trading = now.weekday() < 5 and 2 <= now.hour < 8
        if is_vn_trading:
            major_filter = "t.source='vn' AND t.symbol IN (" + ",".join("?" for _ in MAJOR_VN) + ")"
            major_symbols = MAJOR_VN
        else:
            major_filter = "t.symbol IN (" + ",".join("?" for _ in MAJOR_OFF_HOURS) + ")"
            major_symbols = MAJOR_OFF_HOURS
        active_after = int(now.timestamp()) - 7 * 24 * 60 * 60
        year_start = int(datetime(now.year, 1, 1, tzinfo=UTC).timestamp())
        next_year_start = int(datetime(now.year + 1, 1, 1, tzinfo=UTC).timestamp())
        with self.connect() as con:
            records, last_sync = {}, {}
            for row in con.execute("SELECT interval,row_count,last_sync FROM candle_health"):
                records[row["interval"]] = row["row_count"]
            for interval in ("1D", "1h", "1m"):
                year_filter = ""
                year_params = ()
                if interval in ("1h", "1m"):
                    year_filter = "AND d.time>=? AND d.time<?"
                    year_params = (year_start, next_year_start)
                latest = con.execute(
                    f"""SELECT MAX(
                      CASE
                        WHEN ch.provider=s.provider
                          AND ch.revision=s.revision AND ch.successful_at_ns IS NOT NULL
                        THEN MAX(c.updated_at,ch.successful_at_ns)
                        ELSE c.updated_at
                      END)
                    FROM (
                      SELECT t.source,t.symbol,MAX(d.time) AS latest_time
                      FROM tickers t JOIN candles d USING(source,symbol)
                      WHERE d.interval=? AND {major_filter} {year_filter}
                      GROUP BY t.source,t.symbol
                    ) selected
                    JOIN candles c ON c.source=selected.source AND c.symbol=selected.symbol
                      AND c.interval=? AND c.time=selected.latest_time
                    LEFT JOIN series s ON s.source=c.source AND s.symbol=c.symbol
                      AND s.interval=c.interval
                    LEFT JOIN source_checks ch ON ch.source=c.source AND ch.symbol=c.symbol
                      AND ch.interval=c.interval""",
                    (interval, *major_symbols, *year_params, interval),
                ).fetchone()[0]
                if latest is not None:
                    last_sync[interval] = latest
            return {
                "tickers": con.execute("SELECT COUNT(*) FROM tickers").fetchone()[0],
                "active_tickers": con.execute(
                    "SELECT COUNT(*) FROM (SELECT source,symbol FROM candles "
                    "WHERE interval='1D' AND time>? GROUP BY source,symbol)",
                    (active_after,),
                ).fetchone()[0],
                "records": records,
                "last_sync": last_sync,
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
                "archive_repair_batches": [
                    {
                        "id": row["id"],
                        "status": row["status"],
                        "targets": len(json.loads(row["payload"]).get("targets", [])),
                        "prepared": sum(
                            bool(target.get("replacement"))
                            for target in json.loads(row["payload"]).get("targets", [])
                        ),
                        "retry_at": row["retry_at"],
                        "error": row["error"],
                        "updated_at": row["updated_at"],
                    }
                    for row in con.execute(
                        "SELECT id,status,payload,retry_at,error,updated_at FROM archive_repair_batches WHERE status IN ('collecting','prepared','indexed','manifest_published','failed') ORDER BY created_at DESC LIMIT 20"
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
