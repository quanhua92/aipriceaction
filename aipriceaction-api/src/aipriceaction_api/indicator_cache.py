"""Bounded, version-aware cache for latest multi-ticker indicators.

The cache stores only the latest enhanced row. The history loader owns raw bars.
SQLite remains authoritative: ``history_versions`` changes whenever candle or
history evidence changes, and the next loaded snapshot refreshes this cache.
This works across the API and worker processes without an in-process event bus.
"""

import threading
from collections import OrderedDict
from dataclasses import dataclass
from sys import getsizeof

import numpy as np

from .calculations import enhance_latest_many
from .domain import Candle


@dataclass(frozen=True)
class IndicatorSnapshot:
    """Validated latest-history slice supplied by the SQLite history loader."""

    source: str
    symbol: str
    interval: str
    version: int
    closes: np.ndarray
    rows: tuple[Candle, ...]

    @classmethod
    def from_loaded(cls, source, symbol, interval, version, tail, rows):
        # Internal loader arrays are immutable by convention. Reuse the close
        # tail for calculation instead of copying it on every request.
        closes = np.asarray(tail.closes, dtype=np.float64)
        return cls(
            source=source,
            symbol=symbol,
            interval=interval,
            version=int(version),
            closes=closes,
            rows=tuple(rows[-2:]),
        )


@dataclass
class _Entry:
    version: int
    result: list[dict]
    size: int


class LatestIndicatorCache:
    """Cache one latest MA/EMA response per ticker and request configuration.

    There is no time-based expiry. The API observes cross-process OHLCV changes
    through SQLite's per-series version marker on the next request. On a
    version change, ``resolve`` recomputes from that ticker's fresh compact
    tail and replaces just that cache entry. Unchanged tickers return their
    cached result without running the indicator kernel again. The byte-bounded
    LRU controls memory use.
    """

    DEFAULT_MAX_BYTES = 64 * 1024 * 1024

    def __init__(self, max_bytes=DEFAULT_MAX_BYTES):
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = int(max_bytes)
        self._entries = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    @staticmethod
    def _key(snapshot, ema, periods):
        return (
            snapshot.source,
            snapshot.symbol,
            snapshot.interval,
            bool(ema),
            tuple(periods) if periods is not None else None,
        )

    @staticmethod
    def _copy_result(result):
        # API response assembly may add fields to row dictionaries.
        return [dict(row) for row in result]

    @staticmethod
    def _entry_size(result):
        total = 0
        pending = [result]
        seen = set()
        while pending:
            value = pending.pop()
            identity = id(value)
            if identity in seen:
                continue
            seen.add(identity)
            total += getsizeof(value)
            if isinstance(value, dict):
                pending.extend(value.keys())
                pending.extend(value.values())
            elif isinstance(value, (list, tuple)):
                pending.extend(value)
        return total

    def _remember(self, key, snapshot, result):
        copied = self._copy_result(result)
        size = self._entry_size(copied)
        if size > self.max_bytes:
            return
        value = _Entry(
            version=snapshot.version,
            result=copied,
            size=size,
        )
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= previous.size
            self._entries[key] = value
            self._bytes += size
            while self._bytes > self.max_bytes:
                _old_key, old = self._entries.popitem(last=False)
                self._bytes -= old.size

    def resolve(self, snapshots, *, ema=False, periods=None):
        """Return current rows, computing only cold or version-changed entries."""
        snapshots = list(snapshots)
        results = {}
        missing = []
        with self._lock:
            for snapshot in snapshots:
                key = self._key(snapshot, ema, periods)
                entry = self._entries.get(key)
                if entry is not None and entry.version == snapshot.version:
                    self._entries.move_to_end(key)
                    results[snapshot.symbol] = self._copy_result(entry.result)
                else:
                    missing.append((key, snapshot))

        if not missing:
            return results

        contexts = {snapshot.symbol: list(snapshot.rows) for _, snapshot in missing}
        closes_by_symbol = {snapshot.symbol: snapshot.closes for _, snapshot in missing}
        computed = enhance_latest_many(
            contexts,
            closes_by_symbol,
            missing[0][1].interval,
            ma=True,
            ema=ema,
            periods=periods,
        )
        for key, snapshot in missing:
            result = computed.get(snapshot.symbol, [])
            results[snapshot.symbol] = self._copy_result(result)
            self._remember(key, snapshot, result)
        return results

    def invalidate(self, source=None, symbol=None, interval=None):
        """Drop matching entries, useful for explicit maintenance events."""
        with self._lock:
            removed = []
            for key in self._entries:
                if (
                    (source is None or key[0] == source)
                    and (symbol is None or key[1] == symbol)
                    and (interval is None or key[2] == interval)
                ):
                    removed.append(key)
            for key in removed:
                self._bytes -= self._entries.pop(key).size
            return len(removed)

    @property
    def size_bytes(self):
        with self._lock:
            return self._bytes

    def __len__(self):
        with self._lock:
            return len(self._entries)
