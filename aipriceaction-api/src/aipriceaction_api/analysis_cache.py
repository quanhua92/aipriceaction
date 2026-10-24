"""Bounded cache for analysis results keyed by their source data versions."""

from collections import OrderedDict
from copy import deepcopy
from sys import getsizeof
from threading import Lock


class VersionedAnalysisCache:
    """Keep recent analysis payloads while invalidating them on data changes."""

    def __init__(self, max_entries=32, max_bytes=32 * 1024 * 1024):
        if max_entries <= 0 or max_bytes <= 0:
            raise ValueError("analysis cache limits must be positive")
        self.max_entries = int(max_entries)
        self.max_bytes = int(max_bytes)
        self._entries = OrderedDict()
        self._bytes = 0
        self._lock = Lock()

    @staticmethod
    def _size(value):
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

    def get(self, key, version):
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            stored_version, value, size = entry
            if stored_version != version:
                self._entries.pop(key)
                self._bytes -= size
                return None
            self._entries.move_to_end(key)
            return deepcopy(value)

    def put(self, key, version, value):
        stored_value = deepcopy(value)
        size = self._size(stored_value)
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= previous[2]
            if size > self.max_bytes:
                return
            while self._entries and (
                len(self._entries) >= self.max_entries or self._bytes + size > self.max_bytes
            ):
                _old_key, old = self._entries.popitem(last=False)
                self._bytes -= old[2]
            self._entries[key] = (version, stored_value, size)
            self._bytes += size

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    @property
    def size_bytes(self):
        with self._lock:
            return self._bytes
