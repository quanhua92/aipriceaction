"""Read Rust-compatible OHLCV snapshots from a Redis hash.

This module deliberately depends only on the tiny ``hget(name, key)`` client
interface, so callers can provide their own Redis client and tests can use a
small fake without starting a service.
"""

import json
import math
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Protocol


class RedisHashReader(Protocol):
    """The subset of a Redis client needed by :func:`read_redis_snapshot`."""

    def hget(self, name: str, key: str) -> bytes | str | None: ...


class RedisHashWriter(Protocol):
    """The subset of a Redis client needed to write one response snapshot."""

    def hset(
        self, name: str, key: str | None = None, value: str | None = None, *, mapping=None
    ): ...

    def expire(self, name: str, time: int) -> bool: ...


class _SnapshotObject(dict):
    """JSON object that retains duplicate keys for typed-row validation."""

    def __init__(self, pairs):
        super().__init__()
        self.duplicate_keys = set()
        self.parsed_pairs = []
        for key, value in pairs:
            self.parsed_pairs.append((key, value))
            if key in self:
                self.duplicate_keys.add(key)
            self[key] = value


def _reject_nonstandard_constant(value):
    # serde_json rejects JavaScript's NaN/Infinity extensions, including inside
    # otherwise ignored extra fields in a typed snapshot row.
    raise ValueError(f"invalid JSON number {value}")


def _parse_finite_float(value):
    # Python's json decoder accepts valid JSON exponents that overflow binary64
    # (for example, 1e400) as infinity. serde_json rejects those numbers.
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"JSON number is outside the finite float range: {value}")
    return parsed


def _parse_integer(value):
    # serde_json represents the special JSON integer token -0 as f64(-0.0).
    # Keeping it a float makes the typed integer volume fields reject it too.
    if value == "-0":
        return -0.0
    parsed = int(value)
    try:
        finite_as_float = math.isfinite(float(parsed))
    except OverflowError as error:
        raise ValueError(f"JSON integer is outside the finite number range: {value}") from error
    if not finite_as_float:
        raise ValueError(f"JSON integer is outside the finite number range: {value}")
    return parsed


def _has_invalid_string_or_depth(value: Any, depth: int = 0) -> bool:
    if isinstance(value, str):
        return any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    if isinstance(value, list):
        depth += 1
        return depth > 128 or any(_has_invalid_string_or_depth(item, depth) for item in value)
    if isinstance(value, dict):
        depth += 1
        if depth > 128:
            return True
        return any(
            _has_invalid_string_or_depth(key, depth) or _has_invalid_string_or_depth(item, depth)
            for key, item in getattr(value, "parsed_pairs", value.items())
        )
    return False


_CHRONO_RFC3339_RELAXED = re.compile(
    r"^\s*(?P<year>[+-]?[0-9]+)\s*-\s*(?P<month>[0-9]{1,2})\s*-\s*"
    r"(?P<day>[0-9]{1,2})(?P<separator>[Tt ])\s*(?P<hour>[0-9]{1,2})\s*:"
    r"\s*(?P<minute>[0-9]{1,2})\s*:\s*(?P<second>[0-9]{1,2})"
    r"(?:\.(?P<fraction>[0-9]+))?\s*(?P<zone>(?i:UTC)|[Zz]|[+\-−][0-9]{2}"
    r"[:\s]*[0-9]{2})\s*$"
)


def _parse_chrono_datetime(value: str) -> tuple[datetime, bool] | None:
    """Parse Chrono's relaxed RFC3339 form for Python-representable years."""

    match = _CHRONO_RFC3339_RELAXED.fullmatch(value)
    if match is None:
        return None
    parts = match.groupdict()
    year_text = parts["year"]
    digits = year_text.lstrip("+-")
    if (not year_text.startswith(("+", "-")) and len(digits) > 4) or len(digits) > 4:
        # Chrono supports signed extended years; Python's datetime is limited to
        # 1..9999, so these values cannot be represented by this API runtime.
        return None
    year = int(year_text)
    if not 1 <= year <= 9999:
        return None

    zone = parts["zone"]
    if zone.upper() in ("UTC", "Z"):
        offset_seconds = 0
    else:
        sign = -1 if zone[0] in "-−" else 1
        hours_minutes = re.fullmatch(r"([0-9]{2})[:\s]*([0-9]{2})", zone[1:])
        if hours_minutes is None:
            return None
        offset_hours, offset_minutes = map(int, hours_minutes.groups())
        if offset_hours > 23 or offset_minutes > 59:
            return None
        offset_seconds = sign * (offset_hours * 3600 + offset_minutes * 60)

    second = int(parts["second"])
    if second > 60:
        return None
    fraction = parts["fraction"] or ""
    microsecond = int((fraction[:6]).ljust(6, "0")) if fraction else 0
    try:
        parsed = datetime(
            year,
            int(parts["month"]),
            int(parts["day"]),
            int(parts["hour"]),
            int(parts["minute"]),
            min(second, 59),
            microsecond,
            tzinfo=timezone(timedelta(seconds=offset_seconds)),
        )
    except ValueError:
        return None
    try:
        parsed = parsed.astimezone(UTC)
    except (OverflowError, ValueError):
        # The Rust chrono type supports extended years; Python's datetime does
        # not. Treat UTC normalization beyond year 1..9999 as a cache miss.
        return None
    return parsed, second == 60


def redis_snapshot_key(source: str, symbol: str, interval: str) -> str:
    """Build Rust's ``snap:{source}:{symbol}:{interval}`` Redis hash key."""

    return f"snap:{source}:{symbol}:{interval}"


def redis_snapshot_field(limit: int, *, ema: bool = False, joined: bool = False) -> str:
    """Build a Rust snapshot field such as ``100:sma:joined``."""

    field = f"{limit}:{'ema' if ema else 'sma'}"
    return f"{field}:joined" if joined else field


def read_redis_snapshot(
    client: RedisHashReader,
    source: str,
    symbol: str,
    interval: str,
    limit: int,
    *,
    ema: bool = False,
    joined: bool = False,
) -> list[Any] | None:
    """Return a nonempty decoded row list, or ``None`` for a cache miss.

    Missing fields, malformed UTF-8/JSON, valid JSON with a non-list top-level
    value, and empty lists are treated as misses. Redis/client errors propagate
    so an unavailable cache is distinguishable from an ordinary miss.
    """

    raw = client.hget(
        redis_snapshot_key(source, symbol, interval),
        redis_snapshot_field(limit, ema=ema, joined=joined),
    )
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(raw, str):
        return None

    try:
        rows = json.loads(
            raw,
            object_pairs_hook=_SnapshotObject,
            parse_constant=_reject_nonstandard_constant,
            parse_float=_parse_finite_float,
            parse_int=_parse_integer,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(rows, list) or not rows or _has_invalid_string_or_depth(rows):
        return None
    return rows


_RESPONSE_REQUIRED = ("time", "open", "high", "low", "close", "volume", "symbol")
_RESPONSE_OPTIONAL = (
    "ma10",
    "ma20",
    "ma50",
    "ma100",
    "ma200",
    "ma10_score",
    "ma20_score",
    "ma50_score",
    "ma100_score",
    "ma200_score",
    "close_changed",
    "volume_changed",
    "total_money_changed",
)
_JOINED_REQUIRED = ("ticker", "time", "open", "high", "low", "close", "volume")


def _has_duplicate_typed_fields(row: dict[str, Any], fields: tuple[str, ...]) -> bool:
    return bool(getattr(row, "duplicate_keys", set()).intersection(fields))


def _finite_float(value: Any) -> tuple[bool, float | None]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, None
    try:
        normalized = float(value)
    except (OverflowError, ValueError):
        return False, None
    return math.isfinite(normalized), normalized


def validate_stock_data_rows(rows: list[Any] | None) -> list[dict[str, Any]] | None:
    """Validate and normalize rows as Rust's ``Vec<StockDataResponse>`` does.

    Serde ignores unknown object fields, requires all non-optional response
    fields, accepts null for optional metrics, and rejects non-finite JSON
    numbers. Returning only declared fields matches the typed Rust decode.
    """

    if not rows:
        return None
    normalized = []
    for row in rows:
        typed_fields = (*_RESPONSE_REQUIRED, *_RESPONSE_OPTIONAL)
        if (
            not isinstance(row, dict)
            or _has_duplicate_typed_fields(row, typed_fields)
            or any(field not in row for field in _RESPONSE_REQUIRED)
        ):
            return None
        if not isinstance(row["time"], str) or not isinstance(row["symbol"], str):
            return None
        if isinstance(row["volume"], bool) or not isinstance(row["volume"], int):
            return None
        if not 0 <= row["volume"] < 1 << 64:
            return None
        required_float_fields = ("open", "high", "low", "close")
        if any(row[field] is None for field in required_float_fields):
            return None
        float_fields = (*required_float_fields, *_RESPONSE_OPTIONAL)
        for field in float_fields:
            if field not in row or row[field] is None:
                continue
            valid, _ = _finite_float(row[field])
            if not valid:
                return None
        normalized_row = {
            key: row[key]
            for key in (*_RESPONSE_REQUIRED, *_RESPONSE_OPTIONAL)
            if key in row and row[key] is not None
        }
        for field in float_fields:
            if field in normalized_row:
                normalized_row[field] = float(normalized_row[field])
        normalized.append(normalized_row)
    return normalized


def validate_joined_data_rows(rows: list[Any] | None) -> list[dict[str, Any]] | None:
    """Validate rows as Rust's serialized ``Vec<OhlcvJoined>`` snapshot."""

    if not rows:
        return None
    normalized = []
    for row in rows:
        typed_fields = (*_JOINED_REQUIRED, *_RESPONSE_OPTIONAL)
        if (
            not isinstance(row, dict)
            or _has_duplicate_typed_fields(row, typed_fields)
            or any(field not in row for field in _JOINED_REQUIRED)
        ):
            return None
        if not isinstance(row["ticker"], str) or not isinstance(row["time"], str):
            return None
        if _parse_chrono_datetime(row["time"]) is None:
            return None
        if isinstance(row["volume"], bool) or not isinstance(row["volume"], int):
            return None
        if not -(1 << 63) <= row["volume"] < 1 << 63:
            return None
        required_float_fields = ("open", "high", "low", "close")
        if any(row[field] is None for field in required_float_fields):
            return None
        float_fields = (*required_float_fields, *_RESPONSE_OPTIONAL)
        for field in float_fields:
            if field not in row or row[field] is None:
                continue
            valid, _ = _finite_float(row[field])
            if not valid:
                return None
        normalized_row = {
            key: row[key] for key in (*_JOINED_REQUIRED, *_RESPONSE_OPTIONAL) if key in row
        }
        for field in float_fields:
            if field in normalized_row and normalized_row[field] is not None:
                normalized_row[field] = float(normalized_row[field])
        normalized.append(normalized_row)
    return normalized


def joined_snapshot_response(row: dict[str, Any], *, daily: bool = True) -> dict[str, Any]:
    """Convert one validated Rust joined row to Python's internal response row."""

    parsed_time = _parse_chrono_datetime(row["time"])
    if parsed_time is None:
        raise ValueError("invalid joined snapshot timestamp")
    dt, leap_second = parsed_time
    date_part = f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"
    second = 60 if leap_second else dt.second
    time_part = f"{dt.hour:02d}:{dt.minute:02d}:{second:02d}"
    result = {
        "symbol": row["ticker"],
        "time": date_part if daily else f"{date_part}T{time_part}",
        **{key: row[key] for key in ("open", "high", "low", "close", "volume")},
    }
    result.update(
        {key: row[key] for key in _RESPONSE_OPTIONAL if key in row and row[key] is not None}
    )
    return result


def joined_snapshot_record(row: dict[str, Any]) -> dict[str, Any]:
    """Convert one Python internal response row to Rust's joined snapshot shape."""

    value = row["time"]
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    record = {
        "ticker": row["symbol"],
        "time": dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        **{key: row[key] for key in ("open", "high", "low", "close", "volume")},
    }
    record.update({key: row.get(key) for key in _RESPONSE_OPTIONAL})
    return record


def write_redis_snapshot(
    client: RedisHashWriter,
    source: str,
    symbol: str,
    interval: str,
    limit: int,
    rows: list[dict[str, Any]],
    *,
    ema: bool = False,
    joined: bool = False,
    ttl_seconds: int = 30,
) -> None:
    """Write one Rust-compatible response snapshot and refresh its hash TTL."""

    if not rows:
        return
    key = redis_snapshot_key(source, symbol, interval)
    field = redis_snapshot_field(limit, ema=ema, joined=joined)
    payload = json.dumps(rows, separators=(",", ":"), allow_nan=False)
    client.hset(key, mapping={field: payload})
    client.expire(key, ttl_seconds)
