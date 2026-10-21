from __future__ import annotations

import json
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from aipriceaction_api.app import create_app
from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle
from aipriceaction_api.providers import Page
from aipriceaction_api.redis_publish import (
    TICKER_LIST_KEY,
    TICKER_LIST_TTL_SECONDS,
    publish_ticker_list,
    publish_worker_candles,
    replace_worker_cache,
)
from aipriceaction_api.redis_zset import RedisZsetReader
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


class _Pipeline:
    def __init__(self, redis):
        self.redis = redis
        self.commands = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def zremrangebyscore(self, name, minimum, maximum):
        self.commands.append(("zremrangebyscore", name, minimum, maximum))
        return self

    def zadd(self, name, mapping):
        self.commands.append(("zadd", name, mapping))
        return self

    def zremrangebyrank(self, name, start, end):
        self.commands.append(("zremrangebyrank", name, start, end))
        return self

    def delete(self, name):
        self.commands.append(("delete", name))
        return self

    def execute(self):
        for command in self.commands:
            operation, name, *args = command
            if operation == "zremrangebyscore":
                minimum, maximum = args
                self.redis.zsets[name] = [
                    (score, member)
                    for score, member in self.redis.zsets.get(name, [])
                    if not minimum <= score <= maximum
                ]
            elif operation == "zadd":
                mapping = args[0]
                existing = {member: score for score, member in self.redis.zsets.get(name, [])}
                existing.update(mapping)
                self.redis.zsets[name] = [(score, member) for member, score in existing.items()]
            elif operation == "zremrangebyrank":
                start, end = args
                values = sorted(self.redis.zsets.get(name, []), key=lambda row: row[0])
                stop = len(values) + end + 1 if end < 0 else end + 1
                self.redis.zsets[name] = values[:start] + values[stop:]
            elif operation == "delete":
                self.redis.zsets.pop(name, None)
                self.redis.values.pop(name, None)
                self.redis.hashes.pop(name, None)
        return [True] * len(self.commands)


class _Redis:
    def __init__(self):
        self.zsets = {}
        self.values = {}
        self.hashes = {}
        self.ttls = {}

    def pipeline(self, transaction=True):
        assert transaction is True
        return _Pipeline(self)

    def set(self, name, value, *, ex=None):
        self.values[name] = value
        self.ttls[name] = ex

    def hget(self, name, key):
        return self.hashes.get(name, {}).get(key)

    def hset(self, name, key=None, value=None, *, mapping=None):
        values = self.hashes.setdefault(name, {})
        if mapping is not None:
            values.update(mapping)
        elif key is not None:
            values[key] = value
        return 1

    def expire(self, name, seconds):
        self.ttls[name] = seconds
        return name in self.hashes

    def zrevrange(self, name, start, end, *, withscores=False):
        values = sorted(self.zsets.get(name, []), key=lambda row: row[0], reverse=True)
        selected = values[start : end + 1]
        if withscores:
            return [(member, score) for score, member in selected]
        return [member for _, member in selected]

    def zrevrangebyscore(self, name, maximum, minimum, *, start=0, num=None):
        values = sorted(self.zsets.get(name, []), key=lambda row: row[0], reverse=True)
        selected = [row for row in values if float(minimum) <= row[0] <= float(maximum)]
        return [member for _, member in selected[start : start + num]]


def _candle(timestamp, close, *, symbol="VCB"):
    return Candle(
        source="vn",
        symbol=symbol,
        interval="1D",
        time=timestamp,
        open=close - 0.5,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=123,
        provider="fixture",
        revision="fixture-v1",
    )


class _Settings:
    redis_daily_max_size = 3
    redis_hourly_max_size = 30
    redis_minute_max_size = 20


def test_worker_publish_replaces_same_score_and_invalidates_all_snapshots():
    redis = _Redis()
    key = "ohlcv:vn:VCB:1D"
    snapshot = "snap:vn:VCB:1D"
    old = "1760000000000|1|2|0|1|100|1"
    latest = 1_760_000_000
    redis.zsets[key] = [(latest * 1000, old)]
    redis.values[snapshot] = "cached response"

    publish_worker_candles(redis, _Settings(), [_candle(latest, 99.5)])

    read = RedisZsetReader(redis).read("vn", "VCB", "1D", limit=1)
    assert read is not None
    assert len(read.rows) == 1
    assert read.rows[0].close == 99.5
    assert redis.values.get(snapshot) is None


def test_revision_rebuild_replaces_the_whole_cached_tail_and_trims():
    redis = _Redis()
    key = "ohlcv:vn:VCB:1D"
    snapshot = "snap:vn:VCB:1D"
    redis.zsets[key] = [(stamp * 1000, f"{stamp * 1000}|1|2|0|1|100|1") for stamp in range(10)]
    redis.values[snapshot] = "old snapshot"

    replace_worker_cache(
        redis,
        _Settings(),
        [_candle(100, 10), _candle(200, 20), _candle(300, 30), _candle(400, 40)],
    )

    members = RedisZsetReader(redis).read("vn", "VCB", "1D", limit=10)
    assert members is not None
    assert [row.close for row in members.rows] == [40, 30, 20]
    assert redis.values.get(snapshot) is None


def test_worker_refreshes_rust_ticker_list_shape_and_ttl():
    redis = _Redis()
    publish_ticker_list(
        redis,
        [
            {"source": "vn", "symbol": "VCB"},
            {"source": "crypto", "symbol": "BTCUSDT"},
        ],
    )

    assert json.loads(redis.values[TICKER_LIST_KEY]) == [
        {"source": "vn", "ticker": "VCB"},
        {"source": "crypto", "ticker": "BTCUSDT"},
    ]
    assert redis.ttls[TICKER_LIST_KEY] == TICKER_LIST_TTL_SECONDS


@pytest.mark.asyncio
async def test_worker_sync_updates_the_same_redis_cache_read_by_the_api(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "shared.sqlite3",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "cache",
        archive_backend="filesystem",
        redis_daily_max_size=20,
    )
    repo = Repository(settings.database)
    repo.initialize()
    archive = Archive(repo, settings)
    redis = _Redis()
    today = int(time.time()) // 86400 * 86400

    def crypto_candle(timestamp, close):
        return Candle(
            source="crypto",
            symbol="BTCUSDT",
            interval="1D",
            time=timestamp,
            open=close - 1,
            high=close + 2,
            low=close - 2,
            close=close,
            volume=123,
            provider="binance",
            revision="fixture-v1",
        )

    existing = [crypto_candle(today - offset * 86400, 50_000 + offset) for offset in (3, 2, 1)]
    latest = crypto_candle(today, 51_234)
    repo.put(existing)
    publish_worker_candles(redis, settings, existing)

    snapshot_key = "snap:crypto:BTCUSDT:1D"
    stale_snapshot = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(today - 86400)),
        "open": 50_001.0,
        "high": 50_003.0,
        "low": 49_999.0,
        "close": 50_002.0,
        "volume": 123,
        "symbol": "BTCUSDT",
    }
    redis.hashes[snapshot_key] = {"1:sma": json.dumps([stale_snapshot])}

    class FixtureProviders:
        async def page(self, source, symbol, interval, **kwargs):
            assert (source, symbol, interval) == ("crypto", "BTCUSDT", "1D")
            return Page([*existing[-1:], latest], "binance")

    worker = Worker(
        repo,
        settings,
        FixtureProviders(),
        archive,
        redis_client=redis,
    )
    await worker.sync({"source": "crypto", "symbol": "BTCUSDT"}, "1D")

    cached = RedisZsetReader(redis).read("crypto", "BTCUSDT", "1D", limit=20)
    assert cached is not None and cached.rows[0].close == latest.close
    assert snapshot_key not in redis.hashes

    with TestClient(create_app(settings, redis_client=redis)) as client:
        response = client.get(
            "/tickers?symbol=BTCUSDT&limit=1&ma=true&mode=crypto&format=json&redis=true&snap=true&cache=false"
        )
    assert response.status_code == 200
    assert response.headers["x-data-source"] == "redis-snap"
    assert response.json()["BTCUSDT"][-1]["close"] == latest.close
    refreshed_snapshot = json.loads(redis.hget(snapshot_key, "1:sma"))
    assert refreshed_snapshot[-1]["close"] == latest.close
