"""Small, injectable reader for the Rust API's OHLCV Redis ZSET format.

The client only needs ``zrevrange`` and ``zrevrangebyscore`` methods, so this
module has no Redis package dependency and can be exercised with an in-memory
fake. Rows are returned newest first, matching the Rust Redis reader before
the API applies its response ordering.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Protocol

_I64_MIN = -(2**63)
_I64_MAX = 2**63 - 1
# Chrono 0.4.44 (the Rust lockfile version) supports NaiveDate years
# -262143 through +262142. These are the inclusive UTC millisecond endpoints
# of 262143-01-01T00:00:00.000Z and +262142-12-31T23:59:59.999Z.
_CHRONO_MIN_TIMESTAMP_MS = -8_334_601_228_800_000
_CHRONO_MAX_TIMESTAMP_MS = 8_210_266_876_799_999
_INTEGER = re.compile(r"[+-]?[0-9]+\Z", re.ASCII)
_FLOAT = re.compile(
    r"[+-]?(?:NaN|inf|(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)\Z",
    re.ASCII,
)


class RedisZsetClient(Protocol):
    """The synchronous subset of a Redis client used by :class:`RedisZsetReader`."""

    def zrevrange(
        self, name: str, start: int, end: int, *, withscores: bool = False
    ) -> list[bytes | str] | list[tuple[bytes | str, float]]: ...

    def zrevrangebyscore(
        self,
        name: str,
        max_score: int,
        min_score: str,
        *,
        start: int,
        num: int,
    ) -> list[bytes | str]: ...


@dataclass(frozen=True, slots=True)
class RedisZsetBar:
    """One parsed OHLCV member, retaining Rust's millisecond timestamp."""

    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True, slots=True)
class RedisZsetRead:
    """Parsed result plus the raw counts used by Rust's Redis diagnostics."""

    rows: tuple[RedisZsetBar, ...]
    raw_member_count: int
    aligned_count: int
    requested_limit: int
    base_interval: str


def zset_key(source: str, symbol: str, interval: str) -> str:
    """Return Rust's canonical key for one source/symbol/native interval."""

    return f"ohlcv:{source}:{symbol}:{interval}"


def _parse_i64(value: str) -> int | None:
    if not _INTEGER.fullmatch(value):
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if _I64_MIN <= parsed <= _I64_MAX else None


def _parse_f64(value: str) -> float | None:
    if not _FLOAT.fullmatch(value):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _score_i64(value: object) -> int | None:
    """Parse the oldest ZSET score using Rust's accepted score representations."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if _I64_MIN <= value <= _I64_MAX else None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        parsed = int(value)
        return parsed if _I64_MIN <= parsed <= _I64_MAX else None
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(value, str):
        return _parse_i64(value)
    return None


def parse_member(member: bytes | str, interval: str) -> tuple[RedisZsetBar, int | None] | None:
    """Parse Rust's ``ts|open|high|low|close|volume|crawl_ts`` member.

    As in Rust, six fields are accepted for legacy members without a crawl
    timestamp. Splitting at most six times also preserves Rust's behavior when
    an unexpected delimiter occurs in the final field: the OHLCV row remains
    parseable and its crawl timestamp is treated as unavailable.
    """

    del interval  # The member has no interval field; callers retain it as metadata.
    if isinstance(member, bytes):
        try:
            member = member.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(member, str):
        return None

    parts = member.split("|", 6)
    if len(parts) < 6:
        return None
    timestamp_ms = _parse_i64(parts[0])
    if timestamp_ms is None or not (
        _CHRONO_MIN_TIMESTAMP_MS <= timestamp_ms <= _CHRONO_MAX_TIMESTAMP_MS
    ):
        return None
    open_price = _parse_f64(parts[1])
    high = _parse_f64(parts[2])
    low = _parse_f64(parts[3])
    close = _parse_f64(parts[4])
    volume = _parse_i64(parts[5])
    if None in (open_price, high, low, close, volume):
        return None

    crawl_ts = _parse_i64(parts[6]) if len(parts) == 7 else None
    return (
        RedisZsetBar(
            timestamp_ms=timestamp_ms,
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
        ),
        crawl_ts,
    )


class RedisZsetReader:
    """Read a bounded newest-first ZSET tail or a tail ending at a score.

    ``max_score_ms`` mirrors Rust's ``ZREVRANGEBYSCORE max -inf LIMIT 0 N``
    path. A missing/empty/corrupt key, or a key with no parseable members,
    returns ``None`` just as the Rust batch helper omits that ticker.
    """

    def __init__(self, client: RedisZsetClient | None):
        self.client = client

    def covers_start(self, source: str, symbol: str, interval: str, start_ms: int) -> bool:
        """Return whether the oldest ZSET score is at or before ``start_ms``.

        This mirrors Rust's ``ZREVRANGE key -1 -1 WITHSCORES`` coverage check.
        Missing clients/keys, Redis errors, malformed result shapes, and invalid
        score metadata all return ``False`` so callers can fall back to durable
        history.
        """

        if self.client is None or isinstance(start_ms, bool) or not isinstance(start_ms, int):
            return False
        if not _I64_MIN <= start_ms <= _I64_MAX:
            return False

        try:
            result = self.client.zrevrange(
                zset_key(source, symbol, interval), -1, -1, withscores=True
            )
        except Exception:
            return False

        if not isinstance(result, (tuple, list)) or not result:
            return False

        first = result[0]
        if isinstance(first, (tuple, list)):
            if len(first) < 2:
                return False
            oldest_score = _score_i64(first[1])
        elif len(result) >= 2:
            # Also accept Fred's flat ``[member, score, ...]`` representation.
            oldest_score = _score_i64(result[1])
        else:
            return False

        return oldest_score is not None and oldest_score <= start_ms

    def read(
        self,
        source: str,
        symbol: str,
        interval: str,
        *,
        limit: int,
        max_score_ms: int | None = None,
    ) -> RedisZsetRead | None:
        """Read at most ``limit`` newest raw members, optionally score-bounded.

        The bound is applied by Redis before parsing/deduplication, matching the
        Rust reader: duplicate or malformed members can therefore leave fewer
        than ``limit`` aligned rows. ``limit`` must be positive so an accidental
        zero cannot become Redis's unbounded ``0, -1`` range.
        """

        if limit <= 0:
            raise ValueError("limit must be positive")
        return self.read_signed(
            source,
            symbol,
            interval,
            limit=limit,
            max_score_ms=max_score_ms,
        )

    def read_signed(
        self,
        source: str,
        symbol: str,
        interval: str,
        *,
        limit: int,
        max_score_ms: int | None = None,
    ) -> RedisZsetRead | None:
        """Read using Redis's signed range semantics, before parsing members.

        Rust forwards ``effective_limit + buffer`` directly to Redis. For a
        no-end ``ZREVRANGE``, zero becomes end index ``-1`` (all members) and
        negative values exclude members from the oldest end. For an
        end-bounded ``ZREVRANGEBYSCORE LIMIT``, zero selects none and a
        negative count selects every matching member after the offset.
        """

        if self.client is None:
            return None

        key = zset_key(source, symbol, interval)
        try:
            if max_score_ms is None:
                members = self.client.zrevrange(key, 0, limit - 1)
            else:
                members = self.client.zrevrangebyscore(
                    key,
                    max_score_ms,
                    "-inf",
                    start=0,
                    num=limit,
                )
        except Exception:
            # Redis is an optional read-through cache in Rust; a command error
            # is treated as a cache miss so the caller can use durable storage.
            return None

        raw_member_count = len(members)
        deduped: dict[int, tuple[RedisZsetBar, int]] = {}
        for member in members:
            parsed = parse_member(member, interval)
            if parsed is None:
                continue
            row, crawl_ts = parsed
            crawl_order = crawl_ts if crawl_ts is not None else 0
            previous = deduped.get(row.timestamp_ms)
            if previous is None or crawl_order > previous[1]:
                deduped[row.timestamp_ms] = (row, crawl_order)

        if not deduped:
            return None

        rows = tuple(
            row for _, (row, _) in sorted(deduped.items(), key=lambda item: item[0], reverse=True)
        )
        return RedisZsetRead(
            rows=rows,
            raw_member_count=raw_member_count,
            aligned_count=len(rows),
            requested_limit=limit,
            base_interval=interval,
        )
