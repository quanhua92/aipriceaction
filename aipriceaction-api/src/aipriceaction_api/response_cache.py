"""The short-lived `/tickers` response cache contract used by the Rust API."""

import time
from collections import OrderedDict
from collections.abc import Callable


class TickersResponseCache:
    def __init__(self, ttl: float = 5, max_entries: int = 500, clock: Callable = time.monotonic):
        self.ttl = ttl
        self.max_entries = max_entries
        self.clock = clock
        self.store = OrderedDict()

    def get(self, key):
        entry = self.store.get(key)
        if entry is None:
            return None
        inserted_at, data = entry
        if self.clock() - inserted_at > self.ttl:
            self.store.pop(key, None)
            return None
        return data

    def put(self, key, data):
        now = self.clock()
        self.store = OrderedDict(
            (stored_key, entry)
            for stored_key, entry in self.store.items()
            if now - entry[0] <= self.ttl
        )
        if len(self.store) >= self.max_entries:
            self.store.popitem(last=False)
        self.store[key] = (now, data)
        self.store.move_to_end(key)

    def clear(self):
        self.store.clear()
