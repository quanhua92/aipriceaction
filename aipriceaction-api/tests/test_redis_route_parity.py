import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ResponseError

from aipriceaction_api.app import create_app
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle


class FakeRedis:
    def __init__(self, zsets=None, hashes=None, ticker_list=None):
        self.zsets = zsets or {}
        self.hashes = hashes or {}
        self.ticker_list = ticker_list
        self.expiries = {}

    def get(self, key):
        if key != "meta:ticker_list" or self.ticker_list is None:
            return None
        return json.dumps(self.ticker_list)

    def hget(self, name, key):
        return self.hashes.get((name, key))

    def hset(self, name, mapping):
        for key, value in mapping.items():
            self.hashes[(name, key)] = value
        return len(mapping)

    def expire(self, name, seconds):
        self.expiries[name] = seconds
        return True

    def zrevrange(self, name, start, end, withscores=False):
        values = sorted(self.zsets.get(name, ()), key=lambda item: item[0], reverse=True)
        first = start if start >= 0 else len(values) + start
        last = end if end >= 0 else len(values) + end
        selected = values[first : last + 1]
        if withscores:
            return [(member, score) for score, member in selected]
        return [member for _, member in selected]

    def zrevrangebyscore(self, name, maximum, minimum, *, start=0, num=None):
        maximum = float("inf") if maximum == "+inf" else float(maximum)
        minimum = float("-inf") if minimum == "-inf" else float(minimum)
        values = sorted(self.zsets.get(name, ()), key=lambda item: item[0], reverse=True)
        selected = [item for item in values if minimum <= item[0] <= maximum]
        selected = selected[start:] if num is None or num < 0 else selected[start : start + num]
        return [member for _, member in selected]


def member(timestamp, close, volume=100):
    timestamp_ms = int(datetime.fromisoformat(timestamp).replace(tzinfo=UTC).timestamp() * 1000)
    return timestamp_ms, f"{timestamp_ms}|{close}|{close + 1}|{close - 1}|{close}|{volume}|1"


def response_row(time="2026-09-01", symbol="VCB", close=99):
    return {
        "time": time,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 100,
        "symbol": symbol,
    }


@contextmanager
def api_client(tmp_path, redis, database_rows=()):
    settings = replace(
        Settings(),
        database=tmp_path / "api.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
        cache_ttl=5,
    )
    app = create_app(settings, redis_client=redis)
    with TestClient(app) as client:
        if database_rows:
            app.state.repo.put(list(database_rows))
        yield client


def db_row(symbol, close, date="2026-09-01"):
    time = int(datetime.fromisoformat(date).replace(tzinfo=UTC).timestamp())
    return Candle("vn", symbol, "1D", time, close, close + 1, close - 1, close, 100)


def test_live_redis_value_wins_and_diagnostics_match_raw_zset_read(tmp_path):
    timestamp_ms, redis_member = member("2026-09-01", 77)
    redis = FakeRedis({"ohlcv:vn:VCB:1D": [(timestamp_ms, redis_member)]})

    with api_client(tmp_path, redis, [db_row("VCB", 55)]) as client:
        response = client.get(
            "/tickers?symbol=VCB&limit=1&ma=false&redis=true&snap=false&cache=false"
        )

    assert response.status_code == 200
    assert response.json()["VCB"][0]["close"] == 77
    assert response.headers["x-data-source"] == "cache"
    assert response.headers["x-redis-base"] == "1D"
    assert response.headers["x-redis-raw-count"] == "1"
    assert response.headers["x-redis-aligned"] == "1"
    # Rust asks for limit + one change-indicator context row when MA is off.
    assert response.headers["x-redis-limit"] == "2"


@pytest.mark.parametrize(
    ("interval", "with_dates"),
    [("1D", False), ("1D", True), ("1W", False)],
)
def test_existing_redis_rows_are_read_when_configured_retention_is_zero(
    tmp_path, interval, with_dates
):
    timestamp_ms, redis_member = member("2026-09-01", 77)
    redis = FakeRedis({"ohlcv:vn:VCB:1D": [(timestamp_ms, redis_member)]})
    settings = replace(
        Settings(),
        database=tmp_path / "zero-retention.sqlite3",
        cache_dir=tmp_path / "zero-retention-cache",
        object_dir=tmp_path / "zero-retention-objects",
        archive_backend="filesystem",
        redis_daily_max_size=0,
    )
    app = create_app(settings, redis_client=redis)
    with TestClient(app) as client:
        app.state.repo.put([db_row("VCB", 55)])
        params = {
            "symbol": "VCB",
            "interval": interval,
            "limit": 1,
            "ma": "false",
            "redis": "true",
            "snap": "false",
            "cache": "false",
        }
        if with_dates:
            params.update(start_date="2026-09-01", end_date="2026-09-01")
        response = client.get("/tickers", params=params)

    assert response.status_code == 200
    assert response.json()["VCB"][0]["close"] == 77


def test_signed_native_redis_limit_reads_rows_when_retention_is_zero(tmp_path):
    first_ts, first_member = member("2026-09-01", 77)
    second_ts, second_member = member("2026-09-02", 88)
    redis = FakeRedis(
        {"ohlcv:vn:VCB:1D": [(first_ts, first_member), (second_ts, second_member)]}
    )
    settings = replace(
        Settings(),
        database=tmp_path / "zero-retention-signed-limit.sqlite3",
        cache_dir=tmp_path / "zero-retention-signed-limit-cache",
        object_dir=tmp_path / "zero-retention-signed-limit-objects",
        archive_backend="filesystem",
        redis_daily_max_size=0,
    )
    app = create_app(settings, redis_client=redis)
    with TestClient(app) as client:
        app.state.repo.put([db_row("VCB", 55)])
        response = client.get(
            "/tickers?symbol=VCB&limit=-1&ma=false&redis=true&snap=false&cache=false"
        )

    assert response.status_code == 200
    assert [row["close"] for row in response.json()["VCB"]] == [77, 88]
    assert response.headers["x-data-source"] == "cache"
    assert response.headers["x-redis-limit"] == "0"


def test_signed_native_redis_limit_with_ma_reads_rows_when_retention_is_zero(tmp_path):
    first_ts, first_member = member("2026-09-01", 77)
    second_ts, second_member = member("2026-09-02", 88)
    redis = FakeRedis(
        {"ohlcv:vn:VCB:1D": [(first_ts, first_member), (second_ts, second_member)]}
    )
    settings = replace(
        Settings(),
        database=tmp_path / "zero-retention-signed-ma.sqlite3",
        cache_dir=tmp_path / "zero-retention-signed-ma-cache",
        object_dir=tmp_path / "zero-retention-signed-ma-objects",
        archive_backend="filesystem",
        redis_daily_max_size=0,
    )
    app = create_app(settings, redis_client=redis)
    with TestClient(app) as client:
        app.state.repo.put([db_row("VCB", 55)])
        response = client.get(
            "/tickers?symbol=VCB&limit=-1&ma=true&redis=true&snap=false&cache=false"
        )

    assert response.status_code == 200
    assert [row["close"] for row in response.json()["VCB"]] == [77, 88]
    assert response.headers["x-data-source"] == "cache"
    assert response.headers["x-redis-limit"] == "199"


def test_signed_aggregate_redis_limit_reads_rows_when_retention_is_zero(tmp_path):
    zset = [member("2026-09-01", 77), member("2026-09-02", 88), member("2026-09-03", 99)]
    redis = FakeRedis({"ohlcv:vn:VCB:1D": zset})
    settings = replace(
        Settings(),
        database=tmp_path / "zero-retention-aggregate-limit.sqlite3",
        cache_dir=tmp_path / "zero-retention-aggregate-limit-cache",
        object_dir=tmp_path / "zero-retention-aggregate-limit-objects",
        archive_backend="filesystem",
        redis_daily_max_size=0,
    )
    app = create_app(settings, redis_client=redis)
    with TestClient(app) as client:
        app.state.repo.put([db_row("VCB", 55)])
        response = client.get(
            "/tickers?symbol=VCB&interval=1W&limit=-1&ma=false&redis=true&snap=false&cache=false"
        )

    assert response.status_code == 200
    assert [row["close"] for row in response.json()["VCB"]] == [99]
    assert response.headers["x-data-source"] == "cache"


def test_partial_redis_batch_does_not_fill_missing_tickers_from_sql(tmp_path):
    timestamp_ms, redis_member = member("2026-09-01", 77)
    redis = FakeRedis({"ohlcv:vn:VCB:1D": [(timestamp_ms, redis_member)]})

    with api_client(tmp_path, redis, [db_row("VCB", 55), db_row("FPT", 88)]) as client:
        response = client.get(
            "/tickers?symbol=VCB&symbol=FPT&limit=1&ma=false&snap=false&cache=false"
        )

    assert response.status_code == 200
    assert list(response.json()) == ["VCB"]
    assert response.json()["VCB"][0]["close"] == 77
    assert response.headers["x-data-source"] == "cache"


def test_empty_redis_batch_falls_back_to_database_for_the_whole_batch(tmp_path):
    redis = FakeRedis()

    with api_client(tmp_path, redis, [db_row("VCB", 55), db_row("FPT", 88)]) as client:
        response = client.get(
            "/tickers?symbol=VCB&symbol=FPT&limit=1&ma=false&snap=false&cache=false"
        )

    assert response.status_code == 200
    assert set(response.json()) == {"VCB", "FPT"}
    assert response.headers["x-data-source"] == "database"


def test_valid_response_snapshot_is_used_without_reading_zsets(tmp_path):
    redis = FakeRedis(hashes={("snap:vn:VCB:1D", "1:sma"): json.dumps([response_row(close=123)])})

    with api_client(tmp_path, redis, [db_row("VCB", 55)]) as client:
        response = client.get("/tickers?symbol=VCB&limit=1&cache=false")

    assert response.status_code == 200
    assert response.json()["VCB"][0]["close"] == 123
    assert response.headers["x-data-source"] == "redis-snap"
    assert not redis.zsets


def test_snapshot_read_error_for_one_symbol_falls_back_after_other_snapshot_hits(tmp_path):
    class FailingSnapshotRedis(FakeRedis):
        def hget(self, name, key):
            if name == "snap:vn:FPT:1D":
                raise OSError("temporary Redis read failure")
            return super().hget(name, key)

    redis = FailingSnapshotRedis(
        hashes={("snap:vn:VCB:1D", "1:sma"): json.dumps([response_row(close=123)])}
    )

    with api_client(tmp_path, redis, [db_row("VCB", 55), db_row("FPT", 88)]) as client:
        response = client.get(
            "/tickers?symbol=VCB&symbol=FPT&limit=1&ma=true&snap=true&cache=false"
        )

    assert response.status_code == 200
    assert response.json()["VCB"][0]["close"] == 123
    assert response.json()["FPT"][0]["close"] == 88
    assert response.headers["x-data-source"] == "redis-snap"
    assert json.loads(redis.hashes[("snap:vn:FPT:1D", "1:sma")])[0]["close"] == 88


def test_snapshot_hget_error_with_zset_data_keeps_snapshot_path_and_repairs_snapshot(tmp_path):
    class FailingSnapshotRedis(FakeRedis):
        def hget(self, name, key):
            if name == "snap:vn:FPT:1D":
                raise OSError("temporary Redis read failure")
            return super().hget(name, key)

    timestamp_ms, redis_member = member("2026-09-01", 77)
    redis = FailingSnapshotRedis(
        zsets={"ohlcv:vn:FPT:1D": [(timestamp_ms, redis_member)]},
        hashes={("snap:vn:VCB:1D", "1:sma"): json.dumps([response_row(close=123)])},
    )

    with api_client(tmp_path, redis, [db_row("VCB", 55), db_row("FPT", 88)]) as client:
        response = client.get(
            "/tickers?symbol=VCB&symbol=FPT&limit=1&ma=true&snap=true&cache=false"
        )

    assert response.status_code == 200
    assert response.json()["FPT"][0]["close"] == 77
    assert response.headers["x-data-source"] == "redis-snap"
    assert "x-redis-base" not in response.headers
    assert json.loads(redis.hashes[("snap:vn:FPT:1D", "1:sma")])[0]["close"] == 77


def test_snapshot_server_command_error_keeps_snapshot_batch_path(tmp_path):
    class WrongTypeSnapshotRedis(FakeRedis):
        def hget(self, name, key):
            if name == "snap:vn:FPT:1D":
                raise ResponseError("WRONGTYPE snapshot key")
            return super().hget(name, key)

    timestamp_ms, redis_member = member("2026-09-01", 77)
    redis = WrongTypeSnapshotRedis(zsets={"ohlcv:vn:FPT:1D": [(timestamp_ms, redis_member)]})

    with api_client(tmp_path, redis, [db_row("FPT", 55)]) as client:
        response = client.get(
            "/tickers?symbol=FPT&limit=1&ma=true&snap=true&cache=false"
        )

    assert response.status_code == 200
    assert response.json()["FPT"][0]["close"] == 77
    assert response.headers["x-data-source"] == "redis-snap"
    assert "x-redis-base" not in response.headers


def test_snapshot_permission_error_falls_back_to_database_source_tag(tmp_path):
    class PermissionDeniedRedis(FakeRedis):
        def hget(self, name, key):
            raise ResponseError("NOPERM this user has no permissions to run the command")

        def zrevrange(self, name, start, end, withscores=False):
            raise ResponseError("NOPERM this user has no permissions to run the command")

        def zrevrangebyscore(self, name, maximum, minimum, *, start=0, num=None):
            raise ResponseError("NOPERM this user has no permissions to run the command")

    with api_client(tmp_path, PermissionDeniedRedis(), [db_row("FPT", 55)]) as client:
        response = client.get("/tickers?symbol=FPT&limit=1&ma=true&snap=true&cache=false")

    assert response.status_code == 200
    assert response.json()["FPT"][0]["close"] == 55
    assert response.headers["x-data-source"] == "database"


def test_zset_command_error_falls_back_to_database_at_the_route(tmp_path):
    class FailingZsetRedis(FakeRedis):
        def zrevrange(self, name, start, end, withscores=False):
            raise OSError("temporary Redis range failure")

        def zrevrangebyscore(self, name, maximum, minimum, *, start=0, num=None):
            raise OSError("temporary Redis score-range failure")

    redis = FailingZsetRedis()

    with api_client(tmp_path, redis, [db_row("VCB", 55)]) as client:
        response = client.get(
            "/tickers?symbol=VCB&limit=1&ma=false&redis=true&snap=false&cache=false"
        )

    assert response.status_code == 200
    assert response.json()["VCB"][0]["close"] == 55
    assert response.headers["x-data-source"] == "database"


def test_malformed_snapshot_is_a_miss_and_raw_redis_result_replaces_it(tmp_path):
    timestamp_ms, redis_member = member("2026-09-01", 77)
    redis = FakeRedis(
        zsets={"ohlcv:vn:VCB:1D": [(timestamp_ms, redis_member)]},
        hashes={("snap:vn:VCB:1D", "1:sma"): '[{"time":"wrong-shape"}]'},
    )

    with api_client(tmp_path, redis, [db_row("VCB", 55)]) as client:
        response = client.get("/tickers?symbol=VCB&limit=1&cache=false")

    assert response.status_code == 200
    assert response.json()["VCB"][0]["close"] == 77
    assert response.headers["x-data-source"] == "redis-snap"
    saved = json.loads(redis.hashes[("snap:vn:VCB:1D", "1:sma")])
    assert saved[0]["close"] == 77
    assert redis.expiries["snap:vn:VCB:1D"] == 30


def test_empty_snapshot_and_empty_zset_fall_back_to_sql_and_write_snapshot(tmp_path):
    redis = FakeRedis()

    with api_client(tmp_path, redis, [db_row("VCB", 55)]) as client:
        response = client.get("/tickers?symbol=VCB&limit=1&cache=false")

    assert response.status_code == 200
    assert response.json()["VCB"][0]["close"] == 55
    assert response.headers["x-data-source"] == "redis-snap"
    saved = json.loads(redis.hashes[("snap:vn:VCB:1D", "1:sma")])
    assert saved[0]["close"] == 55


def test_end_bounded_redis_applies_raw_limit_before_parsing_and_reports_it(tmp_path):
    bad_ts = int(datetime.fromisoformat("2026-09-03").replace(tzinfo=UTC).timestamp() * 1000)
    sep2_ts, sep2 = member("2026-09-02", 80)
    sep1_ts, sep1 = member("2026-09-01", 70)
    redis = FakeRedis(
        {
            "ohlcv:vn:VCB:1D": [
                (bad_ts, "malformed newest member"),
                (sep2_ts, sep2),
                (sep1_ts, sep1),
            ]
        }
    )

    with api_client(tmp_path, redis, [db_row("VCB", 55)]) as client:
        response = client.get(
            "/tickers?symbol=VCB&end_date=2026-09-03&limit=1&ma=false&snap=false&cache=false"
        )

    assert response.status_code == 200
    assert response.json()["VCB"][0]["time"] == "2026-09-02"
    assert response.json()["VCB"][0]["close"] == 80
    assert response.json()["VCB"][0].get("close_changed") is None
    assert response.headers["x-data-source"] == "cache"
    assert response.headers["x-redis-limit"] == "2"
    assert response.headers["x-redis-raw-count"] == "2"
    assert response.headers["x-redis-aligned"] == "1"


def test_start_bounded_redis_requests_retained_rows_plus_indicator_buffer(tmp_path):
    sep1_ts, sep1 = member("2026-09-01", 70)
    sep2_ts, sep2 = member("2026-09-02", 80)
    sep3_ts, sep3 = member("2026-09-03", 90)
    redis = FakeRedis({"ohlcv:vn:VCB:1D": [(sep1_ts, sep1), (sep2_ts, sep2), (sep3_ts, sep3)]})

    with api_client(tmp_path, redis, [db_row("VCB", 55)]) as client:
        response = client.get(
            "/tickers?symbol=VCB&start_date=2026-09-01&limit=1&ma=false&snap=false&cache=false"
        )

    assert response.status_code == 200
    assert response.headers["x-data-source"] == "cache"
    assert response.headers["x-redis-limit"] == "5001"
    assert response.headers["x-redis-raw-count"] == "3"
    assert response.headers["x-redis-aligned"] == "3"


def test_end_bounded_negative_limit_uses_the_exact_raw_member_budget(tmp_path):
    first_day = datetime(2026, 1, 1, tzinfo=UTC)
    zset = []
    for offset in range(200):
        day = first_day + timedelta(days=offset)
        date = day.strftime("%Y-%m-%d")
        timestamp_ms = int(day.timestamp() * 1000)
        value = "malformed newest member" if offset == 199 else member(date, 100 + offset)[1]
        zset.append((timestamp_ms, value))
    redis = FakeRedis({"ohlcv:vn:VCB:1D": zset})

    with api_client(tmp_path, redis) as client:
        response = client.get(
            "/tickers?symbol=VCB&end_date=2026-12-31&limit=-2&ma=true&snap=false&cache=false"
        )

    assert response.status_code == 200
    assert len(response.json()["VCB"]) == 197
    assert response.headers["x-data-source"] == "cache"
    assert response.headers["x-redis-limit"] == "198"
    assert response.headers["x-redis-raw-count"] == "198"
    assert response.headers["x-redis-aligned"] == "197"


def test_unbounded_signed_redis_limit_is_applied_before_member_parsing(tmp_path):
    sep1_ts = int(datetime.fromisoformat("2026-09-01").replace(tzinfo=UTC).timestamp() * 1000)
    sep2_ts, sep2 = member("2026-09-02", 80)
    sep3_ts, sep3 = member("2026-09-03", 90)
    redis = FakeRedis(
        {
            "ohlcv:vn:VCB:1D": [
                (sep1_ts, "malformed oldest member"),
                (sep2_ts, sep2),
                (sep3_ts, sep3),
            ]
        }
    )

    with api_client(tmp_path, redis) as client:
        response = client.get(
            "/tickers?symbol=VCB&limit=-2&ma=false&redis=true&snap=false&cache=false"
        )

    assert response.status_code == 200
    assert [row["time"] for row in response.json()["VCB"]] == ["2026-09-02", "2026-09-03"]
    assert response.headers["x-redis-limit"] == "-1"
    assert response.headers["x-redis-raw-count"] == "2"
    assert response.headers["x-redis-aligned"] == "2"


def test_zero_unbounded_redis_limit_preserves_zrevrange_all_members_behavior(tmp_path):
    sep1_ts = int(datetime.fromisoformat("2026-09-01").replace(tzinfo=UTC).timestamp() * 1000)
    sep2_ts, sep2 = member("2026-09-02", 80)
    sep3_ts, sep3 = member("2026-09-03", 90)
    redis = FakeRedis(
        {
            "ohlcv:vn:VCB:1D": [
                (sep1_ts, "malformed oldest member"),
                (sep2_ts, sep2),
                (sep3_ts, sep3),
            ]
        }
    )

    with api_client(tmp_path, redis) as client:
        response = client.get(
            "/tickers?symbol=VCB&limit=-1&ma=false&redis=true&snap=false&cache=false"
        )

    assert response.status_code == 200
    assert [row["time"] for row in response.json()["VCB"]] == ["2026-09-02", "2026-09-03"]
    assert response.headers["x-redis-limit"] == "0"
    assert response.headers["x-redis-raw-count"] == "3"
    assert response.headers["x-redis-aligned"] == "2"


def test_end_bounded_negative_redis_count_reads_all_matching_raw_members(tmp_path):
    sep1_ts = int(datetime.fromisoformat("2026-09-01").replace(tzinfo=UTC).timestamp() * 1000)
    sep2_ts, sep2 = member("2026-09-02", 80)
    sep3_ts, sep3 = member("2026-09-03", 90)
    redis = FakeRedis(
        {
            "ohlcv:vn:VCB:1D": [
                (sep1_ts, "malformed oldest member"),
                (sep2_ts, sep2),
                (sep3_ts, sep3),
            ]
        }
    )

    with api_client(tmp_path, redis) as client:
        response = client.get(
            "/tickers?symbol=VCB&end_date=2026-09-03&limit=-2&ma=false&redis=true&snap=false&cache=false"
        )

    assert response.status_code == 200
    assert [row["time"] for row in response.json()["VCB"]] == ["2026-09-02", "2026-09-03"]
    assert response.headers["x-redis-limit"] == "-1"
    assert response.headers["x-redis-raw-count"] == "3"
    assert response.headers["x-redis-aligned"] == "2"


def test_end_bounded_signed_limit_filters_embedded_time_after_score_selection(tmp_path):
    sep1_ts, sep1 = member("2026-09-01", 70)
    score_sep2 = int(datetime.fromisoformat("2026-09-02").replace(tzinfo=UTC).timestamp() * 1000)
    _, future_member = member("2026-09-04", 90)
    redis = FakeRedis({"ohlcv:vn:VCB:1D": [(sep1_ts, sep1), (score_sep2, future_member)]})

    with api_client(tmp_path, redis) as client:
        response = client.get(
            "/tickers?symbol=VCB&end_date=2026-09-03&limit=-2&ma=false&redis=true&snap=false&cache=false"
        )

    assert response.status_code == 200
    assert [row["time"] for row in response.json()["VCB"]] == ["2026-09-01"]
    assert response.headers["x-redis-limit"] == "-1"
    assert response.headers["x-redis-raw-count"] == "2"
    # Redis diagnostics count parsed members before the request timestamp filter.
    assert response.headers["x-redis-aligned"] == "2"


def test_redis_false_negative_limit_reads_sql_when_redis_is_configured(tmp_path):
    timestamp_ms, redis_member = member("2026-09-01", 77)
    redis = FakeRedis({"ohlcv:vn:VCB:1D": [(timestamp_ms, redis_member)]})

    with api_client(tmp_path, redis, [db_row("VCB", 55)]) as client:
        response = client.get(
            "/tickers?symbol=VCB&limit=-1&ma=true&redis=false&snap=false&cache=false"
        )

    assert response.status_code == 200
    assert response.json()["VCB"][0]["close"] == 55
    assert response.headers["x-data-source"] == "database"
    assert not any(name.startswith("x-redis-") for name in response.headers)


def test_end_bounded_redis_filters_member_timestamp_after_score_selection(tmp_path):
    sep1_ts, sep1 = member("2026-09-01", 70)
    _, future_member = member("2026-09-04", 90)
    # Redis admits this member by its September 2 score; Rust then rejects the
    # embedded September 4 timestamp for an end_date of September 3.
    score_ts = int(datetime.fromisoformat("2026-09-02").replace(tzinfo=UTC).timestamp() * 1000)
    redis = FakeRedis({"ohlcv:vn:VCB:1D": [(sep1_ts, sep1), (score_ts, future_member)]})

    with api_client(tmp_path, redis) as client:
        response = client.get(
            "/tickers?symbol=VCB&end_date=2026-09-03&limit=-2&ma=true&snap=false&cache=false"
        )

    assert response.status_code == 200
    assert [row["time"] for row in response.json()["VCB"]] == ["2026-09-01"]
    assert response.headers["x-redis-limit"] == "198"
    assert response.headers["x-redis-raw-count"] == "2"
    # Diagnostics report successfully parsed/aligned members before the later
    # request end-date filter, matching Rust's Redis metadata.
    assert response.headers["x-redis-aligned"] == "2"


def test_end_bounded_limit_above_retention_is_not_clamped_in_diagnostics(tmp_path):
    sep2_ts, sep2 = member("2026-09-02", 80)
    sep1_ts, sep1 = member("2026-09-01", 70)
    redis = FakeRedis({"ohlcv:vn:VCB:1D": [(sep1_ts, sep1), (sep2_ts, sep2)]})

    with api_client(tmp_path, redis) as client:
        response = client.get(
            "/tickers?symbol=VCB&end_date=2026-09-03&limit=10000&ma=true&snap=false&cache=false"
        )

    assert response.status_code == 200
    assert len(response.json()["VCB"]) == 2
    assert response.headers["x-redis-limit"] == "10200"
    assert response.headers["x-redis-raw-count"] == "2"
    assert response.headers["x-redis-aligned"] == "2"
