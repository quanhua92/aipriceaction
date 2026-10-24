"""Bounded in-process response cache for the `/tickers` endpoint."""

import time
from collections import OrderedDict
from collections.abc import Callable
from sys import getsizeof


class TickersResponseCache:
    # Large chart windows are rebuilt from the shared SQLite history LRU. Do
    # not recursively size and retain their much larger response dictionaries.
    _MAX_CACHED_ROWS = 20_000

    def __init__(
        self,
        ttl: float = 60,
        max_entries: int = 500,
        max_bytes: int = 64 * 1024 * 1024,
        clock: Callable = time.monotonic,
    ):
        self.ttl = ttl
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.clock = clock
        self.store = OrderedDict()
        self.stored_bytes = 0

    @staticmethod
    def _size(value):
        """Estimate retained Python object bytes without double-counting aliases."""
        total = 0
        pending = [value]
        seen = set()
        while pending:
            current = pending.pop()
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            total += getsizeof(current)
            if isinstance(current, dict):
                pending.extend(current.keys())
                pending.extend(current.values())
            elif isinstance(current, (list, tuple, set, frozenset)):
                pending.extend(current)
        return total

    def _remove(self, key):
        entry = self.store.pop(key, None)
        if entry is not None:
            self.stored_bytes -= entry[2]
        return entry

    def get(self, key, *, version=None, timing_stats=None):
        entry = self.store.get(key)
        if entry is None:
            if timing_stats is not None:
                timing_stats["response_cache_miss_reason"] = "absent"
            return None
        inserted_at, data, _size, stored_version = entry
        if self.clock() - inserted_at > self.ttl:
            self._remove(key)
            if timing_stats is not None:
                timing_stats["response_cache_miss_reason"] = "ttl_expired"
            return None
        if stored_version != version:
            self._remove(key)
            if timing_stats is not None:
                timing_stats["response_cache_miss_reason"] = "market_epoch_changed"
            return None
        self.store.move_to_end(key)
        if timing_stats is not None:
            timing_stats["response_cache_miss_reason"] = None
        return data

    def put(self, key, data, *, version=None):
        now = self.clock()
        for stored_key, entry in list(self.store.items()):
            if now - entry[0] > self.ttl:
                self._remove(stored_key)
        if isinstance(data, dict) and all(isinstance(rows, list) for rows in data.values()):
            if sum(len(rows) for rows in data.values()) > self._MAX_CACHED_ROWS:
                self._remove(key)
                return
        size = self._size(data)
        self._remove(key)
        if size > self.max_bytes or self.max_entries <= 0:
            return
        while self.store and (
            len(self.store) >= self.max_entries
            or self.stored_bytes + size > self.max_bytes
        ):
            self._remove(next(iter(self.store)))
        self.store[key] = (now, data, size, version)
        self.store.move_to_end(key)
        self.stored_bytes += size

    def clear(self):
        self.store.clear()
        self.stored_bytes = 0
