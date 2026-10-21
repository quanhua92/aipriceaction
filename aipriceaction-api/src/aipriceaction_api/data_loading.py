"""Reusable, indexed SQLite history loading for one or many ticker identities.

The loader keeps one read transaction and one SQLite connection for candle and
history metadata reads. Multi-symbol candle tails are selected in indexed SQL
batches so Python does not issue and materialize one query per ticker.
"""

import copy
import json
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass, field
from sys import getsizeof
from typing import Any

from .domain import Candle


@dataclass
class CompactTail:
    closes: Any
    oldest_time: int
    mixed_revision: bool
    mixed_provider: bool


@dataclass
class LoadedHistory:
    local: dict[str, list[Candle]] = field(default_factory=dict)
    archives: dict[str, list[dict]] = field(default_factory=dict)
    gaps: dict[str, list[dict]] = field(default_factory=dict)
    adoptions: dict[str, dict[str, dict]] = field(default_factory=dict)
    volume_corrections: dict[str, list[dict]] = field(default_factory=dict)
    compact_tails: dict[str, CompactTail] = field(default_factory=dict)


class SQLiteDataLoader:
    """Load native candle tails and associated validation metadata in batches."""

    # Stay below SQLite builds that still use the historical 999-variable
    # limit. Each requested ticker contributes two bound values.
    _CANDLE_SYMBOL_BATCH_SIZE = 400
    # The batch join shape is intended for longer indicator lookbacks. On
    # SQLite 3.46, SQLite can choose an automatic index for very short tails;
    # a reused indexed point lookup is substantially cheaper for these.
    _CANDLE_SHORT_TAIL_LIMIT = 16
    # Raw market tails are cheap to invalidate through market_epoch but too
    # expensive to reload on every adjacent page endpoint. Keep them for at
    # most 15 seconds; the externally visible response cache remains Rust's
    # 5-second lifetime.
    _CACHE_TTL_SECONDS = 15
    _CACHE_MAX_BYTES = 64 * 1024 * 1024

    def __init__(self, repository):
        self.repository = repository
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._cache_lock = threading.Lock()
        self._inflight = {}

    @staticmethod
    def _copy_loaded(result):
        """Copy containers so endpoint code cannot mutate the shared cache."""
        return LoadedHistory(
            local={symbol: list(rows) for symbol, rows in result.local.items()},
            archives={symbol: copy.deepcopy(rows) for symbol, rows in result.archives.items()},
            gaps={symbol: copy.deepcopy(rows) for symbol, rows in result.gaps.items()},
            adoptions={
                symbol: copy.deepcopy(records) for symbol, records in result.adoptions.items()
            },
            volume_corrections={
                symbol: copy.deepcopy(rows) for symbol, rows in result.volume_corrections.items()
            },
            compact_tails={
                symbol: CompactTail(
                    closes=tail.closes.copy(),
                    oldest_time=tail.oldest_time,
                    mixed_revision=tail.mixed_revision,
                    mixed_provider=tail.mixed_provider,
                )
                for symbol, tail in result.compact_tails.items()
            },
        )

    @staticmethod
    def _estimated_size(result):
        """Bound retained results, including nested archive and gap evidence."""
        size = 1024 + 128 * len(result.local)
        size += sum(len(rows) * 320 for rows in result.local.values())
        size += sum(getsizeof(tail.closes) for tail in result.compact_tails.values())
        pending = [
            result.archives,
            result.gaps,
            result.adoptions,
            result.volume_corrections,
        ]
        seen = set()
        while pending:
            value = pending.pop()
            identity = id(value)
            if identity in seen:
                continue
            seen.add(identity)
            size += getsizeof(value)
            if isinstance(value, dict):
                pending.extend(value.keys())
                pending.extend(value.values())
            elif isinstance(value, (list, tuple, set, frozenset)):
                pending.extend(value)
        return size

    def _cache_get(self, key, now):
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is None:
                return None
            expires_at, size, value = cached
            if expires_at < now:
                self._cache.pop(key)
                self._cache_bytes -= size
                return None
            self._cache.move_to_end(key)
        # Cached values are immutable by convention: they were copied on put,
        # and Candle is frozen. Clone containers outside the lock so a large
        # metadata result does not serialize unrelated cache hits.
        return self._copy_loaded(value)

    def _cache_put(self, key, value, now):
        size = self._estimated_size(value)
        if size > self._CACHE_MAX_BYTES:
            return
        cached_value = self._copy_loaded(value)
        with self._cache_lock:
            previous = self._cache.pop(key, None)
            if previous is not None:
                self._cache_bytes -= previous[1]
            for cached_key, cached in list(self._cache.items()):
                if cached[0] <= now:
                    self._cache.pop(cached_key)
                    self._cache_bytes -= cached[1]
            self._cache[key] = (now + self._CACHE_TTL_SECONDS, size, cached_value)
            self._cache_bytes += size
            while self._cache_bytes > self._CACHE_MAX_BYTES:
                _, (_, evicted_size, _) = self._cache.popitem(last=False)
                self._cache_bytes -= evicted_size

    @staticmethod
    def _chunks(values, size=400):
        for offset in range(0, len(values), size):
            yield values[offset : offset + size]

    @staticmethod
    def _candle_query(source, symbol, interval, start, end, limit, forward):
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
        return sql, args

    @classmethod
    def _read_candle_batch(
        cls,
        con,
        source,
        symbols,
        interval,
        start,
        end,
        limit,
        forward,
        result,
    ):
        """Read indexed tails for symbol chunks and append chronologically.

        The correlated inner lookup uses the candles primary key to seek each
        ticker's newest (or oldest) timestamps. The outer lookup then fetches
        just those complete rows. This avoids scanning a whole interval and
        avoids Python/SQLite round trips per ticker.
        """
        if limit is not None and limit <= cls._CANDLE_SHORT_TAIL_LIMIT:
            # Latest/no-MA requests need only two candles (the requested row
            # plus its change-calculation predecessor). SQLite's prepared
            # primary-key seeks are faster than the VALUES join for this tiny
            # tail and avoid materializing any discarded history.
            sql, _ = cls._candle_query(source, "", interval, start, end, limit, forward)
            for symbol in symbols:
                args = [source, symbol, interval]
                if start is not None:
                    args.append(start)
                if end is not None:
                    args.append(end)
                args.append(limit)
                rows = con.execute(sql, args).fetchall()
                result[symbol].extend(
                    Candle(**dict(row)) for row in (rows if forward else reversed(rows))
                )
            return
        direction = "ASC" if forward else "DESC"
        for offset in range(0, len(symbols), cls._CANDLE_SYMBOL_BATCH_SIZE):
            chunk = symbols[offset : offset + cls._CANDLE_SYMBOL_BATCH_SIZE]
            requested = ",".join("(?,?)" for _ in chunk)
            where = ["c2.source=?", "c2.symbol=r.symbol", "c2.interval=?"]
            args = [
                value for index, symbol in enumerate(chunk, offset) for value in (index, symbol)
            ]
            args.extend((source, interval))
            if start is not None:
                where.append("c2.time>=?")
                args.append(start)
            if end is not None:
                where.append("c2.time<=?")
                args.append(end)
            inner = (
                "SELECT c2.time FROM candles AS c2 WHERE "
                + " AND ".join(where)
                + f" ORDER BY c2.time {direction}"
            )
            if limit is not None:
                inner += " LIMIT ?"
                args.append(limit)
            sql = f"""
                WITH requested(ord,symbol) AS (VALUES {requested})
                SELECT c.*
                FROM requested AS r
                CROSS JOIN candles AS c INDEXED BY sqlite_autoindex_candles_1
                  ON c.source=? AND c.symbol=r.symbol AND c.interval=?
                 AND c.time IN ({inner})
                ORDER BY r.ord,c.time ASC
            """
            # The source/interval placeholders occur in the outer query before
            # the subquery placeholders in SQL text.
            batch_args = args[: 2 * len(chunk)] + [source, interval] + args[2 * len(chunk) :]
            for row in con.execute(sql, batch_args):
                result[row["symbol"]].append(Candle(**dict(row)))

    @classmethod
    def _read_compact_tails(cls, con, source, symbols, interval, limit, result):
        """Load EMA warmup tails as bounded primary-key walks.

        A multi-symbol correlated join makes SQLite sort each chunk of up to
        400 ticker tails into request order. Walking the candles primary key
        once per symbol is faster for this shape and still bounds every lookup
        to ``limit`` rows. NumPy consumes each cursor directly and keeps the
        warmup closes and quality flags in compact arrays.
        """
        import numpy as np

        tail_symbols = []
        cursor = con.cursor()
        cursor.row_factory = None
        sql = """
            SELECT time,close,provider,revision
            FROM candles INDEXED BY sqlite_autoindex_candles_1
            WHERE source=? AND symbol=? AND interval=?
            ORDER BY time DESC LIMIT ?
        """
        dtype = [
            ("time", np.int64),
            ("close", np.float64),
            ("provider", object),
            ("revision", object),
        ]
        for symbol in symbols:
            rows = np.fromiter(
                cursor.execute(sql, (source, symbol, interval, limit)),
                dtype=dtype,
                count=-1,
            )
            if not len(rows):
                continue
            tail_symbols.append(symbol)
            first_provider = rows["provider"][0]
            first_revision = rows["revision"][0]
            result.compact_tails[symbol] = CompactTail(
                closes=rows["close"][::-1].copy(),
                oldest_time=int(rows["time"][-1]),
                mixed_revision=bool(np.any(rows["revision"] != first_revision)),
                mixed_provider=bool(np.any(rows["provider"] != first_provider)),
            )

        cls._read_candle_batch(
            con, source, tail_symbols, interval, None, None, 2, False, result.local
        )

    def read_candles_many(
        self, source, symbols, interval, start=None, end=None, limit=None, forward=False
    ):
        """Load only native candles for callers that do not need History policy."""
        symbols = list(dict.fromkeys(symbols))
        result = {symbol: [] for symbol in symbols}
        if not symbols:
            return result
        with self.repository.connect() as con:
            con.execute("BEGIN")
            self._read_candle_batch(
                con, source, symbols, interval, start, end, limit, forward, result
            )
        return result

    def load_many(
        self,
        source,
        symbols,
        interval,
        start=None,
        end=None,
        limit=None,
        forward=False,
        compact_tail=False,
    ):
        """Share identical in-flight reads before consulting the SQLite LRU."""
        symbols = list(dict.fromkeys(symbols))
        if not symbols:
            return LoadedHistory()
        key = (
            self.repository.market_epoch(),
            source,
            tuple(sorted(symbols)),
            interval,
            start,
            end,
            limit,
            bool(forward),
            bool(compact_tail),
        )
        with self._cache_lock:
            future = self._inflight.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._inflight[key] = future
        if not owner:
            return self._copy_loaded(future.result())

        try:
            result = self._load_many(
                source,
                symbols,
                interval,
                start=start,
                end=end,
                limit=limit,
                forward=forward,
                compact_tail=compact_tail,
            )
            future.set_result(result)
            return result
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._cache_lock:
                if self._inflight.get(key) is future:
                    del self._inflight[key]

    def _load_many(
        self,
        source,
        symbols,
        interval,
        start=None,
        end=None,
        limit=None,
        forward=False,
        compact_tail=False,
    ):
        """Read matching rows and metadata for symbols using a single connection."""
        symbols = list(dict.fromkeys(symbols))
        result = LoadedHistory(
            local={symbol: [] for symbol in symbols},
            archives={symbol: [] for symbol in symbols},
            gaps={symbol: [] for symbol in symbols},
            adoptions={symbol: {} for symbol in symbols},
            volume_corrections={symbol: [] for symbol in symbols},
        )
        if not symbols:
            return result

        archive_where = [
            "source=?",
            "interval=?",
            "status IN ('published','pending_repair','historical_snapshot')",
        ]
        archive_args = [source, interval]
        if start is not None:
            archive_where.append("end>=?")
            archive_args.append(start)
        if end is not None:
            archive_where.append("start<=?")
            archive_args.append(end)

        gap_where = [
            "source=?",
            "interval=?",
            "kind='history_unavailable'",
            "resolved=0",
        ]
        gap_args = [source, interval]

        adoption_where = ["source=?", "interval=?"]
        adoption_args = [source, interval]

        correction_where = ["source=?", "interval=?"]
        correction_args = [source, interval]

        with self.repository.connect() as con:
            # Keep all tables on the same SQLite snapshot during a bulk read.
            con.execute("BEGIN")
            epoch_row = con.execute("SELECT value FROM meta WHERE key='market_epoch'").fetchone()
            epoch = epoch_row[0] if epoch_row else 0
            cache_key = (
                epoch,
                source,
                tuple(sorted(symbols)),
                interval,
                start,
                end,
                limit,
                bool(forward),
                bool(compact_tail),
            )
            cached = self._cache_get(cache_key, time.monotonic())
            if cached is not None:
                return cached
            for chunk in self._chunks(symbols):
                placeholders = ",".join("?" for _ in chunk)
                args = [*archive_args, *chunk]
                rows = con.execute(
                    "SELECT * FROM archives WHERE "
                    + " AND ".join(archive_where)
                    + f" AND symbol IN ({placeholders}) ORDER BY end DESC",
                    args,
                ).fetchall()
                for row in rows:
                    result.archives[row["symbol"]].append(dict(row))

                rows = con.execute(
                    "SELECT source,symbol,interval,detail FROM quality WHERE "
                    + " AND ".join(gap_where)
                    + f" AND symbol IN ({placeholders})",
                    [*gap_args, *chunk],
                ).fetchall()
                for row in rows:
                    try:
                        record = json.loads(row["detail"])
                    except (ValueError, TypeError) as exc:
                        from .domain import DataError

                        raise DataError("Invalid unavailable-history record") from exc
                    self.repository.validate_history_gap(record)
                    if any(record[key] != row[key] for key in ("source", "symbol", "interval")):
                        from .domain import DataError

                        raise DataError(
                            "Unavailable-history identity differs from its stored record"
                        )
                    if (start is None or record["end"] >= start) and (
                        end is None or record["start"] <= end
                    ):
                        result.gaps[row["symbol"]].append(record)

                rows = con.execute(
                    "SELECT * FROM snapshot_adoptions WHERE "
                    + " AND ".join(adoption_where)
                    + f" AND symbol IN ({placeholders})",
                    [*adoption_args, *chunk],
                ).fetchall()
                for row in rows:
                    result.adoptions[row["symbol"]][row["revision"]] = dict(row)

                if source == "vn" and interval == "1m":
                    correction_sql = (
                        "SELECT * FROM volume_corrections WHERE "
                        + " AND ".join(correction_where)
                        + f" AND symbol IN ({placeholders})"
                    )
                    correction_args_for_chunk = [*correction_args, *chunk]
                    if start is not None:
                        correction_sql += " AND time>=?"
                        correction_args_for_chunk.append(start)
                    if end is not None:
                        correction_sql += " AND time<=?"
                        correction_args_for_chunk.append(end)
                    correction_sql += " ORDER BY time,id"
                    rows = con.execute(correction_sql, correction_args_for_chunk).fetchall()
                    for row in rows:
                        result.volume_corrections[row["symbol"]].append(dict(row))

            if compact_tail:
                if start is not None or end is not None or forward or limit is None:
                    raise ValueError("Compact tails require an undated backward limit")
                self._read_compact_tails(con, source, symbols, interval, limit, result)
            else:
                self._read_candle_batch(
                    con, source, symbols, interval, start, end, limit, forward, result.local
                )

        for records in result.gaps.values():
            records.sort(
                key=lambda row: (
                    row["source"],
                    row["symbol"],
                    row["interval"],
                    row["start"],
                    row["end"],
                    row["reason"],
                )
            )
        self._cache_put(cache_key, result, time.monotonic())
        return result
