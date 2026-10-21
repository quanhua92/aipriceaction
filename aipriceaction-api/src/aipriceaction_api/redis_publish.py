"""Publish committed worker candles to the Rust-compatible Redis cache."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict

from .domain import Candle

log = logging.getLogger(__name__)
TICKER_LIST_KEY = "meta:ticker_list"
TICKER_LIST_TTL_SECONDS = 900


def _max_size(settings, interval: str) -> int:
    return {
        "1D": settings.redis_daily_max_size,
        "1h": settings.redis_hourly_max_size,
        "1m": settings.redis_minute_max_size,
    }[interval]


def _member(row: Candle, crawl_ts_ms: int) -> tuple[str, int]:
    timestamp_ms = row.time * 1000
    value = "|".join(
        (
            str(timestamp_ms),
            repr(float(row.open)),
            repr(float(row.high)),
            repr(float(row.low)),
            repr(float(row.close)),
            str(row.volume),
            str(crawl_ts_ms),
        )
    )
    return value, timestamp_ms


def _publish(redis_client, settings, source, symbol, interval, rows, *, replace=False):
    key = f"ohlcv:{source}:{symbol}:{interval}"
    snapshot_key = f"snap:{source}:{symbol}:{interval}"
    crawl_ts_ms = time.time_ns() // 1_000_000
    members = [_member(row, crawl_ts_ms) for row in rows]
    scores = [score for _, score in members]
    values = {member: score for member, score in members}

    try:
        # One Redis transaction makes a published range replacement
        # indivisible to readers. Rust performs the range replacement,
        # retention trim, and snapshot invalidation as separate commands.
        with redis_client.pipeline(transaction=True) as pipe:
            if replace:
                pipe.delete(key)
            else:
                pipe.zremrangebyscore(key, min(scores), max(scores))
            if values:
                pipe.zadd(key, values)
            limit = _max_size(settings, interval)
            if limit > 0:
                pipe.zremrangebyrank(key, 0, -limit - 1)
            pipe.delete(snapshot_key)
            pipe.execute()
    except Exception as exc:  # Redis is deliberately best-effort cache state.
        log.warning(
            "Redis worker cache update failed source=%s symbol=%s interval=%s kind=%s",
            source,
            symbol,
            interval,
            type(exc).__name__,
        )


def publish_worker_candles(redis_client, settings, candles: list[Candle]) -> None:
    """Refresh raw ZSET tails and invalidate every derived snapshot variant.

    The caller invokes this only after the durable SQLite commit. Redis remains
    an optional cache: a Redis failure is logged and never rolls back a candle
    already published to durable storage.
    """

    if redis_client is None or not candles:
        return

    groups = defaultdict(list)
    for candle in candles:
        groups[(candle.source, candle.symbol, candle.interval)].append(candle)

    for (source, symbol, interval), rows in groups.items():
        _publish(redis_client, settings, source, symbol, interval, rows)


def replace_worker_cache(redis_client, settings, candles: list[Candle]) -> None:
    """Replace committed series tails after bootstrap or revision recovery."""

    if redis_client is None:
        return
    groups = defaultdict(list)
    for candle in candles:
        groups[(candle.source, candle.symbol, candle.interval)].append(candle)
    for (source, symbol, interval), rows in groups.items():
        _publish(redis_client, settings, source, symbol, interval, rows, replace=True)


def publish_ticker_list(redis_client, tickers) -> None:
    """Refresh Rust's short-lived ticker identity cache from durable identities."""

    if redis_client is None:
        return
    payload = [
        {"source": row["source"], "ticker": row.get("symbol", row.get("ticker"))} for row in tickers
    ]
    try:
        redis_client.set(
            TICKER_LIST_KEY,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ex=TICKER_LIST_TTL_SECONDS,
        )
    except Exception as exc:
        log.warning("Redis ticker-list cache update failed kind=%s", type(exc).__name__)
