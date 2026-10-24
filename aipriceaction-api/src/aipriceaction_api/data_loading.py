"""Reusable, indexed SQLite history loading for one or many ticker identities.

The loader keeps one read transaction and one SQLite connection for candle and
history metadata reads. Bounded per-symbol primary-key seeks read only the bars
needed by indicator and analysis requests.
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
    times: Any
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
    versions: dict[str, int] = field(default_factory=dict)


class SQLiteDataLoader:
    """Load native candle tails and associated validation metadata."""

    # Keep loaded history resident until the byte-bounded LRU needs the space.
    # SQLite per-series versions, checked on every load, provide freshness.
    _CACHE_MAX_BYTES = 256 * 1024 * 1024

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
                    times=tail.times.copy(),
                    oldest_time=tail.oldest_time,
                    mixed_revision=tail.mixed_revision,
                    mixed_provider=tail.mixed_provider,
                )
                for symbol, tail in result.compact_tails.items()
            },
            versions=dict(result.versions),
        )

    @staticmethod
    def _estimated_size(result):
        """Bound retained results, including nested archive and gap evidence."""
        size = 1024 + 128 * len(result.local)
        size += sum(len(rows) * 320 for rows in result.local.values())
        size += sum(
            getsizeof(tail.closes) + getsizeof(tail.times) for tail in result.compact_tails.values()
        )
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

    def _cache_get(self, key, copy_result=True):
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is None:
                return None
            _stored_at, _size, value = cached
            self._cache.move_to_end(key)
        # Cached values are immutable by convention: they were copied on put,
        # and Candle is frozen. Clone containers outside the lock so a large
        # metadata result does not serialize unrelated cache hits.
        return self._copy_loaded(value) if copy_result else value

    @staticmethod
    def _daily_end_slack(cached_end, requested_end):
        """Bound how many daily rows a later cached end can hide."""
        seconds = cached_end - requested_end
        return (seconds + 86_399) // 86_400 + 1

    def _cache_get_compatible(self, key, versions, copy_result=True):
        """Reuse wider cached batches for symbol subsets and dated daily tails.

        Startup warming loads the full source universe so ticker and analysis
        routes can share the same SQLite snapshot. A request can safely reuse
        that read when its symbol set is a subset and its time bounds and sort
        direction match. Dated daily reads may also use a cache ending later
        than the requested date, provided the cached tail has room for every
        intervening daily row. Version checks refresh changed identities; the
        byte-bounded LRU, rather than elapsed time, controls retention.
        """
        cached = self._cache_get(key, copy_result=False)
        if cached is not None:
            stale = [
                symbol
                for symbol in key[1]
                if cached.versions.get(symbol, 0) != versions.get(symbol, 0)
            ]
            if not stale:
                result = self._copy_loaded(cached) if copy_result else cached
                return result, []
            fresh = [symbol for symbol in key[1] if symbol not in set(stale)]
            result = self._slice_cached(
                cached,
                fresh,
                key[5],
                key[6],
                key[7],
                end=key[4],
                copy_metadata=copy_result,
            )
            result.versions = {symbol: versions[symbol] for symbol in fresh}
            return result, stale

        source, symbols, interval, start, end, limit, forward, compact_tail = key
        if not isinstance(limit, int) or limit <= 0:
            return LoadedHistory(), list(symbols)
        requested_symbols = set(symbols)
        with self._cache_lock:
            candidates = []
            for cached_key, entry in self._cache.items():
                (
                    cached_source,
                    cached_symbols,
                    cached_interval,
                    cached_start,
                    cached_end,
                    cached_limit,
                    cached_forward,
                    cached_compact_tail,
                ) = cached_key
                later_daily_end = (
                    interval == "1D"
                    and start == cached_start
                    and end is not None
                    and cached_end is not None
                    and cached_end > end
                    and not forward
                    and not compact_tail
                    and not cached_compact_tail
                    and isinstance(limit, int)
                    and isinstance(cached_limit, int)
                    and cached_limit >= limit + self._daily_end_slack(cached_end, end)
                )
                end_compatible = cached_end == end or later_daily_end
                if (
                    cached_source != source
                    or cached_interval != interval
                    or cached_start != start
                    or not end_compatible
                    or cached_forward != forward
                    or not isinstance(cached_limit, int)
                    or cached_limit
                    < limit + (self._daily_end_slack(cached_end, end) if later_daily_end else 0)
                    or not requested_symbols.issubset(cached_symbols)
                ):
                    continue
                if cached_compact_tail != compact_tail and not (
                    cached_compact_tail and not compact_tail and not forward and limit <= 2
                ):
                    continue
                value = entry[2]
                # Empty identities are reusable too. `history_versions()` was
                # read before this lookup, and every candle/archive/gap/
                # adoption/correction mutation bumps that identity's version.
                # A newly populated symbol is therefore marked stale and
                # reloaded below instead of pinning the old empty result.
                score = (
                    cached_compact_tail != compact_tail,
                    cached_end - end if later_daily_end else 0,
                    len(cached_symbols) - len(requested_symbols),
                    cached_limit
                    - limit
                    - (self._daily_end_slack(cached_end, end) if later_daily_end else 0),
                    -entry[0],
                )
                candidates.append((score, cached_key, value, later_daily_end))
            if not candidates:
                return LoadedHistory(), list(symbols)
            _score, cached_key, value, later_daily_end = min(
                candidates, key=lambda item: item[0]
            )
            self._cache.move_to_end(cached_key)

        cached_limit = cached_key[5]

        stale = [
            symbol for symbol in symbols if value.versions.get(symbol, 0) != versions.get(symbol, 0)
        ]
        stale_set = set(stale)
        if later_daily_end:
            for symbol in symbols:
                if symbol in stale_set:
                    continue
                rows = value.local.get(symbol, [])
                visible_count = sum(row.time <= end for row in rows)
                # If this identity filled the cached limit but fewer than the
                # requested number precede the target date, the wider tail may
                # have truncated rows needed by this historical request.
                if len(rows) >= cached_limit and visible_count < limit:
                    stale.append(symbol)
                    stale_set.add(symbol)
        fresh = [symbol for symbol in symbols if symbol not in stale_set]
        result = self._slice_cached(
            value,
            fresh,
            limit,
            forward,
            compact_tail,
            end=end if later_daily_end else None,
            copy_metadata=copy_result,
        )
        result.versions = {symbol: versions[symbol] for symbol in fresh}
        return result, stale

    @staticmethod
    def _slice_cached(
        value, symbols, limit, forward, compact_tail, *, end=None, copy_metadata=True
    ):
        """Return an isolated subset with the same shape as the requested read."""
        result = LoadedHistory()
        for symbol in symbols:
            rows = value.local.get(symbol, [])
            if compact_tail:
                tail = value.compact_tails.get(symbol)
                result.local[symbol] = list(rows[-2:])
                if tail is not None:
                    start = max(0, len(tail.closes) - limit)
                    closes = tail.closes[start:]
                    times = tail.times[start:]
                    if copy_metadata:
                        closes = closes.copy()
                        times = times.copy()
                    result.compact_tails[symbol] = CompactTail(
                        closes=closes,
                        times=times,
                        oldest_time=int(times[0]) if len(times) else tail.oldest_time,
                        # The wider tail's flags are conservative for every
                        # shorter suffix and preserve the established fallback.
                        mixed_revision=tail.mixed_revision,
                        mixed_provider=tail.mixed_provider,
                    )
            else:
                if end is not None:
                    rows = [row for row in rows if row.time <= end]
                selected = (
                    rows
                    if limit is None
                    else rows[:limit]
                    if forward
                    else rows[-limit:]
                )
                result.local[symbol] = list(selected)

            if copy_metadata:
                archives = value.archives.get(symbol, [])
                gaps = value.gaps.get(symbol, [])
                corrections = value.volume_corrections.get(symbol, [])
                if end is not None:
                    archives = [record for record in archives if record["start"] <= end]
                    gaps = [record for record in gaps if record["start"] <= end]
                    corrections = [record for record in corrections if record["time"] <= end]
                result.archives[symbol] = copy.deepcopy(archives)
                result.gaps[symbol] = copy.deepcopy(gaps)
                result.adoptions[symbol] = copy.deepcopy(value.adoptions.get(symbol, {}))
                result.volume_corrections[symbol] = copy.deepcopy(corrections)
            else:
                archives = value.archives.get(symbol, [])
                gaps = value.gaps.get(symbol, [])
                corrections = value.volume_corrections.get(symbol, [])
                if end is not None:
                    archives = [record for record in archives if record["start"] <= end]
                    gaps = [record for record in gaps if record["start"] <= end]
                    corrections = [record for record in corrections if record["time"] <= end]
                result.archives[symbol] = archives
                result.gaps[symbol] = gaps
                result.adoptions[symbol] = value.adoptions.get(symbol, {})
                result.volume_corrections[symbol] = corrections
            if symbol in value.versions:
                result.versions[symbol] = value.versions[symbol]
        return result

    @staticmethod
    def _merge_loaded(left, right, symbols):
        """Combine an unchanged cached subset with freshly loaded identities."""
        result = LoadedHistory()
        for field_name in (
            "local",
            "archives",
            "gaps",
            "adoptions",
            "volume_corrections",
            "compact_tails",
            "versions",
        ):
            target = getattr(result, field_name)
            target.update(getattr(left, field_name))
            target.update(getattr(right, field_name))
        # Keep every requested symbol represented, including a fresh empty read.
        for symbol in symbols:
            result.local.setdefault(symbol, [])
            result.archives.setdefault(symbol, [])
            result.gaps.setdefault(symbol, [])
            result.adoptions.setdefault(symbol, {})
            result.volume_corrections.setdefault(symbol, [])
        return result

    def _cache_put(self, key, value, now):
        has_history = bool(value.compact_tails) or any(value.local.values())
        has_history = has_history or any(
            records
            for collection in (
                value.archives,
                value.gaps,
                value.adoptions,
                value.volume_corrections,
            )
            for records in collection.values()
        )
        if not has_history:
            # Startup can run before the worker's first successful fetch. An
            # empty warmup must not pin a newly populated source as empty.
            return
        size = self._estimated_size(value)
        if size > self._CACHE_MAX_BYTES:
            return
        cached_value = self._copy_loaded(value)
        with self._cache_lock:
            previous = self._cache.pop(key, None)
            if previous is not None:
                self._cache_bytes -= previous[1]
            self._cache[key] = (now, size, cached_value)
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
        sql = (
            "SELECT * FROM candles INDEXED BY sqlite_autoindex_candles_1 WHERE "
            f"{where} ORDER BY time {'ASC' if forward else 'DESC'}"
        )
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
        """Read bounded per-symbol primary-key ranges and append chronologically.

        Local benchmarks show that these indexed seeks outperform a multi-symbol
        CTE for both short tails and long indicator warmup windows. Keeping the
        result in tuples until ``Candle`` construction also avoids SQLite Row
        wrappers and name-based field conversion for every historical bar.
        """
        sql, _ = cls._candle_query(source, "", interval, start, end, limit, forward)
        cursor = con.cursor()
        cursor.row_factory = None
        for symbol in symbols:
            args = [source, symbol, interval]
            if start is not None:
                args.append(start)
            if end is not None:
                args.append(end)
            if limit is not None:
                args.append(limit)
            rows = cursor.execute(sql, args).fetchall()
            result[symbol].extend(Candle(*row) for row in (rows if forward else reversed(rows)))

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
            chronological_times = rows["time"][::-1].copy()
            result.compact_tails[symbol] = CompactTail(
                closes=rows["close"][::-1].copy(),
                times=chronological_times,
                oldest_time=int(chronological_times[0]),
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
        copy_result=True,
    ):
        """Share identical in-flight reads before consulting the SQLite LRU.

        ``copy_result=False`` is for internal read-only consumers. Candle rows
        are frozen and the history reader only inspects evidence metadata, so
        those callers can share cached objects without copying every ticker's
        full warmup tail. Keep the safe, isolated-copy behavior as the default.
        """
        symbols = list(dict.fromkeys(symbols))
        if not symbols:
            return LoadedHistory()
        key = (
            source,
            tuple(sorted(symbols)),
            interval,
            start,
            end,
            limit,
            bool(forward),
            bool(compact_tail),
        )
        versions = self.repository.history_versions(source, symbols, interval)
        flight_key = (key, tuple(sorted(versions.items())))
        with self._cache_lock:
            future = self._inflight.get(flight_key)
            owner = future is None
            if owner:
                future = Future()
                self._inflight[flight_key] = future
        if not owner:
            value = future.result()
            return self._copy_loaded(value) if copy_result else value

        try:
            cached, stale = self._cache_get_compatible(
                key, versions, copy_result=copy_result
            )
            if not stale:
                result = cached
            else:
                fresh = self._load_many(
                    source,
                    stale,
                    interval,
                    start=start,
                    end=end,
                    limit=limit,
                    forward=forward,
                    compact_tail=compact_tail,
                    copy_result=copy_result,
                    versions={symbol: versions[symbol] for symbol in stale},
                )
                result = self._merge_loaded(cached, fresh, symbols)
                self._cache_put(key, result, time.monotonic())
            future.set_result(result)
            return result
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._cache_lock:
                if self._inflight.get(flight_key) is future:
                    del self._inflight[flight_key]

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
        copy_result=True,
        versions=None,
    ):
        """Read matching rows and metadata for symbols using a single connection."""
        symbols = list(dict.fromkeys(symbols))
        result = LoadedHistory(
            local={symbol: [] for symbol in symbols},
            archives={symbol: [] for symbol in symbols},
            gaps={symbol: [] for symbol in symbols},
            adoptions={symbol: {} for symbol in symbols},
            volume_corrections={symbol: [] for symbol in symbols},
            versions=dict(versions or {}),
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
        return result
