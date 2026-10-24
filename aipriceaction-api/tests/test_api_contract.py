import asyncio
import csv
import importlib.util
import io
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote_to_bytes

import httpx
import pytest
from fastapi.responses import Response
from fastapi.testclient import TestClient

import aipriceaction_api.app as app_module
import aipriceaction_api.config as config_module
from aipriceaction_api.app import RUST_QUERY_FIELDS, RustCompatibleStaticFiles, create_app
from aipriceaction_api.archive import Archive
from aipriceaction_api.config import RUST_DEFAULT_CORS_ORIGINS, Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.responses import CSV_COLUMNS


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OTEL_ENABLED", "false")
    settings = replace(
        Settings(),
        database=tmp_path / "db.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
        sync_tokens=("test-token",),
        refresh_secret="refresh-test",
        cors_origins=(
            "https://aipriceaction.com",
            "https://api.aipriceaction.com",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://192.168.1.13:5173",
        ),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        origin = datetime(2024, 1, 1, tzinfo=UTC)
        rows = []
        for symbol, start in (("FPT", 100000), ("VCB", 80000), ("VNINDEX", 1200)):
            for i in range(240):
                close = start + i * 10
                rows.append(
                    Candle(
                        "vn",
                        symbol,
                        "1D",
                        int((origin + timedelta(days=i)).timestamp()),
                        close,
                        close + 10,
                        close - 10,
                        close,
                        200000 + i,
                        "fixture",
                    )
                )
        app.state.repo.put(rows)
        app.state.health_snapshot.update(app.state.repo.health_summary())
        client.app_instance = app
        yield client


def test_repeated_symbols_defaults_and_order(client):
    data = client.get("/tickers?symbol=FPT&symbol=VCB").json()
    assert set(data) == {"FPT", "VCB"}
    assert len(data["FPT"]) == 1
    single = client.get("/tickers?symbol=FPT").json()["FPT"]
    assert len(single) == 240
    assert single[0]["time"] < single[-1]["time"]
    assert single[-1]["ma20"] == 102295
    blank_response = client.get("/tickers?symbol=&limit=1")
    all_vn = client.get("/tickers?limit=1")
    assert blank_response.json() == all_vn.json()
    # The first request is a cold read; the omitted-symbol equivalent follows
    # it and hits the same response-cache key in Rust and Python.
    assert blank_response.headers["x-data-source"] == "database"
    assert all_vn.headers["x-data-source"] == "in-memory"
    all_blank_response = client.get("/tickers?symbol=&symbol=&mode=all&format=csv")
    all_tickers = client.get("/tickers?mode=all&format=csv")
    assert all_blank_response.text == all_tickers.text
    comma_only = client.get("/tickers?symbol=,&mode=all&format=csv")
    assert comma_only.json() == {}
    assert "x-data-source" not in comma_only.headers
    mixed_blank_symbol = client.get("/tickers?symbol=&symbol=FPT&limit=1")
    assert mixed_blank_symbol.headers["x-data-source"] == "database"
    mixed_blank_symbol = mixed_blank_symbol.json()
    assert set(mixed_blank_symbol) == {"FPT"}
    assert len(mixed_blank_symbol["FPT"]) == 1
    comma_delimited = client.get("/tickers?symbol=FPT,VCB&limit=1&ma=false").json()
    assert set(comma_delimited) == {"FPT", "VCB"}
    assert len(comma_delimited["FPT"]) == 1
    assert len(comma_delimited["VCB"]) == 1
    mixed_delimited = client.get("/tickers?symbol=FPT,VCB&symbol=VNINDEX&limit=1&ma=false").json()
    assert set(mixed_delimited) == {"FPT", "VCB", "VNINDEX"}
    assert client.get("/tickers/info?ticker=").json() == client.get("/tickers/info").json()


def test_tickers_json_matches_rust_object_key_and_row_field_order(client):
    app = client.app_instance
    timestamp = parse_time("2024-01-01")
    app.state.repo.register("yahoo", "ZZZ", "Yahoo ZZZ")
    app.state.repo.register("sjc", "AAA", "SJC AAA")
    app.state.repo.put(
        [
            Candle("yahoo", "ZZZ", "1D", timestamp, 10, 11, 9, 10, 100),
            Candle("sjc", "AAA", "1D", timestamp, 20, 21, 19, 20, 200),
        ]
    )

    yahoo = client.get("/tickers?mode=yahoo&limit=1&ma=false&redis=false&cache=false&format=json")
    assert yahoo.status_code == 200
    assert yahoo.content.startswith(b'{"AAA":')
    payload = yahoo.json()
    assert list(payload) == ["AAA", "ZZZ"]
    assert list(payload["AAA"][0]) == [
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "symbol",
    ]
    yahoo_fields = ("time", "open", "high", "low", "close", "volume", "symbol")
    yahoo_field_positions = [yahoo.content.index(f'"{field}":'.encode()) for field in yahoo_fields]
    assert yahoo_field_positions == sorted(yahoo_field_positions)

    vn = client.get("/tickers?symbol=FPT&limit=1&ma=true&redis=false&cache=false")
    row = vn.json()["FPT"][0]
    rust_row_fields = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "symbol",
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
    ]
    assert list(row) == rust_row_fields
    row_field_positions = [vn.content.index(f'"{field}":'.encode()) for field in rust_row_fields]
    assert row_field_positions == sorted(row_field_positions)


def test_json_value_and_analysis_field_order_matches_rust(client):
    def assert_value_maps_are_sorted(value):
        if isinstance(value, dict):
            assert list(value) == sorted(value)
            for child in value.values():
                assert_value_maps_are_sorted(child)
        elif isinstance(value, list):
            for child in value:
                assert_value_maps_are_sorted(child)

    for path in (
        "/tickers/group?mode=all",
        "/tickers/name?mode=all",
        "/tickers/info",
    ):
        assert_value_maps_are_sorted(client.get(path).json())

    performers = client.get(
        "/analysis/top-performers?mode=vn&limit=5&min_volume=0&snap=false"
    ).json()
    assert performers["data"]["performers"]
    performer = performers["data"]["performers"][0]
    assert list(performer) == [
        "symbol",
        "close",
        "volume",
        "close_changed",
        "volume_changed",
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
        "sector",
        "total_money_changed",
    ]

    jdk = client.get(
        "/analysis/rrg?algorithm=jdk&mode=vn&trails=2&min_volume=0&benchmark=VNINDEX&period=10"
    ).json()
    assert list(jdk["data"]) == ["benchmark", "algorithm", "period", "tickers"]
    mascore = client.get("/analysis/rrg?algorithm=mascore&mode=vn&trails=0&min_volume=0").json()
    assert list(mascore["data"]) == ["algorithm", "tickers"]


def test_sync_json_value_keys_match_serde_json_value_order(client):
    key = str(uuid.uuid4())
    value = {"z": 1, "middle": {"y": 2, "a": 3}, "a": [2, {"z": 4, "b": 5}]}
    response = client.post(
        f"/sync/{key}",
        json={"secret": "secret-123", "value": value},
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert list(payload) == ["id", "value", "created_at", "updated_at"]
    assert list(payload["value"]) == ["a", "middle", "z"]
    assert list(payload["value"]["middle"]) == ["a", "y"]
    assert list(payload["value"]["a"][1]) == ["b", "z"]


def test_tickers_json_uses_rust_dated_ma_context_and_omits_null_fields(client):
    response = client.get(
        "/tickers?symbol=FPT&interval=1D&start_date=2024-08-23&end_date=2024-08-28"
        "&limit=5&ma=true&ema=true&redis=false&cache=false"
    )

    assert response.status_code == 200
    assert "FPT" in response.json(), response.text
    rows = response.json()["FPT"]
    assert len(rows) == 5
    assert all(value is not None for row in rows for value in row.values())
    # Rust reads up to 600 bars before the requested start when ema=true. The
    # fixture has enough earlier data for EMA200 and for the first row's change
    # fields, even though only five dated rows are returned.
    assert all("ma200" in row and "ma200_score" in row for row in rows)
    assert "close_changed" in rows[0]


def test_ticker_discovery_uses_catalog_identities_not_series_only(client):
    repo = client.app_instance.state.repo
    repo.put(
        [
            Candle(
                "vn",
                "ARCHIVEONLY",
                "1D",
                parse_time("2024-09-01"),
                100,
                101,
                99,
                100,
                1000,
            )
        ]
    )
    with repo.connect() as con:
        con.execute("DELETE FROM tickers WHERE source='vn' AND symbol='ARCHIVEONLY'")

    no_symbol = client.get("/tickers?mode=vn&limit=1&ma=false&cache=false").json()
    explicit_all_mode = client.get(
        "/tickers?symbol=ARCHIVEONLY&mode=all&limit=1&ma=false&cache=false"
    ).json()

    assert "ARCHIVEONLY" not in no_symbol
    assert "ARCHIVEONLY" not in explicit_all_mode


def test_latest_tickers_omit_a_series_with_pending_history_repair(client):
    history = client.app_instance.state.history
    repo = client.app_instance.state.repo
    original_query = history.query
    original_query_many = history.query_many
    repo.register("vn", "VTH")
    errors = {"VTH": "Historical adjustment repair pending for VTH 1D"}

    def query(source, symbol, interval, *args, **kwargs):
        if symbol in errors:
            raise DataError(errors[symbol])
        return original_query(source, symbol, interval, *args, **kwargs)

    history.query = query

    def query_many(source, symbols, interval, *args, **kwargs):
        rows = original_query_many(source, symbols, interval, *args, **kwargs)
        for symbol in symbols:
            if symbol in errors:
                rows[symbol] = DataError(errors[symbol])
        return rows

    history.query_many = query_many

    latest_batch = client.get("/tickers?mode=vn&limit=1&ma=false&redis=false&cache=false")
    explicit_latest = client.get("/tickers?symbol=VTH&limit=1&ma=false&redis=false&cache=false")
    dated = client.get(
        "/tickers?symbol=VTH&start_date=2024-01-01&end_date=2024-12-31"
        "&limit=1&ma=false&redis=false&cache=false"
    )
    end_only = client.get(
        "/tickers?symbol=VTH&end_date=2024-12-31&limit=1&ma=false&redis=false&cache=false"
    )
    malformed_date = client.get(
        "/tickers?symbol=VTH&start_date=not-a-date&limit=1&ma=false&redis=false&cache=false"
    )

    assert latest_batch.status_code == 200
    assert set(latest_batch.json()) == {"FPT", "VCB", "VNINDEX"}
    assert explicit_latest.status_code == 200
    assert explicit_latest.json() == {}
    assert dated.status_code == 503
    assert "Historical adjustment repair pending" in dated.json()["error"]
    assert end_only.status_code == 503
    # Rust parses optional date strings with `and_then`; malformed values are
    # ignored, so this request has the same latest-row semantics as no date.
    assert malformed_date.status_code == 200
    assert malformed_date.json() == {}

    # A same-status history failure stays visible if live candles exist.
    errors["VCB"] = "Historical adjustment repair pending for VCB 1D"
    live_series = client.get("/tickers?symbol=VCB&limit=1&ma=false&redis=false&cache=false")
    assert live_series.status_code == 503

    # A different 503 must not be hidden by the latest-row omission rule.
    errors["VTH"] = "Incompatible adjustment revisions for VTH 1D"
    incompatible = client.get("/tickers?symbol=VTH&limit=1&ma=false&redis=false&cache=false")
    assert incompatible.status_code == 503
    assert "Incompatible adjustment revisions" in incompatible.json()["error"]


def test_latest_tickers_use_live_rows_while_dated_reads_keep_pending_repairs_strict(client):
    app = client.app_instance
    repo = app.state.repo
    historical = [
        Candle(
            "vn",
            "PENDLIVE",
            "1D",
            parse_time(f"2024-01-{day:02d}"),
            100 + day,
            101 + day,
            99 + day,
            100 + day,
            1000,
        )
        for day in range(1, 26)
    ]
    repo.put(historical)
    historical = repo.read("vn", "PENDLIVE", "1D")
    app.state.archive.publish(historical, prune=True)
    pending_row = Candle("vn", "PENDLIVE", "1D", parse_time("2024-01-26"), 126, 127, 125, 126, 1260)
    live_row = Candle("vn", "PENDLIVE", "1D", parse_time("2024-01-27"), 125, 126, 124, 125, 1250)
    repo.put([live_row])
    pending = app.state.archive.prepare([replace(pending_row, revision="pending-repair-basis")])
    repo.publish_archive(pending | {"status": "pending_repair"})

    latest = client.get("/tickers?symbol=PENDLIVE&limit=1&ma=true&ema=true&cache=false")
    dated = client.get(
        "/tickers?symbol=PENDLIVE&start_date=2024-01-26&end_date=2024-01-26"
        "&limit=1&ma=false&redis=false&cache=false"
    )

    assert latest.status_code == 200
    assert latest.json()["PENDLIVE"][0]["close"] == 125
    assert 100 < latest.json()["PENDLIVE"][0]["ma20"] < 125
    assert dated.status_code == 503
    assert "Historical adjustment repair pending" in dated.json()["error"]


def test_latest_tickers_keep_newer_pending_repair_strict_with_older_live_rows(client):
    app = client.app_instance
    repo = app.state.repo
    repo.register("vn", "PENDINGNEW")
    repo.put([Candle("vn", "PENDINGNEW", "1D", parse_time("2024-01-03"), 10, 11, 9, 10, 1000)])
    pending = app.state.archive.prepare(
        [Candle("vn", "PENDINGNEW", "1D", parse_time("2024-01-04"), 11, 12, 10, 11, 1100)]
    )
    repo.publish_archive(pending | {"status": "pending_repair"})

    latest = client.get("/tickers?symbol=PENDINGNEW&limit=1&ma=false&redis=false&cache=false")

    assert latest.status_code == 503
    assert "Historical adjustment repair pending" in latest.json()["error"]

    repo.register("vn", "PENDINGEQUAL")
    repo.put([Candle("vn", "PENDINGEQUAL", "1D", parse_time("2024-01-03"), 10, 11, 9, 10, 1000)])
    same_time_pending = app.state.archive.prepare(
        [Candle("vn", "PENDINGEQUAL", "1D", parse_time("2024-01-03"), 99, 100, 98, 99, 9900)]
    )
    repo.publish_archive(same_time_pending | {"status": "pending_repair"})
    equal_time = client.get("/tickers?symbol=PENDINGEQUAL&limit=1&ma=false&redis=false&cache=false")
    assert equal_time.status_code == 503
    assert "Historical adjustment repair pending" in equal_time.json()["error"]


def test_latest_tickers_do_not_fall_back_to_published_archive_for_pending_only_series(client):
    app = client.app_instance
    repo = app.state.repo
    repo.register("vn", "PENDINGARCHIVE")
    repo.register("vn", "PUBLISHEDONLY")

    repo.put([Candle("vn", "PENDINGARCHIVE", "1D", parse_time("2024-01-03"), 10, 11, 9, 10, 1000)])
    archived = repo.read("vn", "PENDINGARCHIVE", "1D")[0]
    app.state.archive.publish([archived], prune=True)
    assert repo.read("vn", "PENDINGARCHIVE", "1D") == []
    pending = app.state.archive.prepare(
        [Candle("vn", "PENDINGARCHIVE", "1D", parse_time("2024-01-03"), 11, 12, 10, 11, 1100)]
    )
    repo.publish_archive(pending | {"status": "pending_repair"})

    repo.put([Candle("vn", "PUBLISHEDONLY", "1D", parse_time("2024-01-03"), 20, 21, 19, 20, 2000)])
    published_only = repo.read("vn", "PUBLISHEDONLY", "1D")[0]
    app.state.archive.publish([published_only], prune=True)

    pending_latest = client.get(
        "/tickers?symbol=PENDINGARCHIVE&limit=1&ma=false&redis=false&cache=false"
    )
    published_latest = client.get(
        "/tickers?symbol=PUBLISHEDONLY&limit=1&ma=false&redis=false&cache=false"
    )

    assert pending_latest.status_code == 200
    assert pending_latest.json() == {}
    assert published_latest.status_code == 200
    assert published_latest.json()["PUBLISHEDONLY"][0]["close"] == 20

    repo.register("vn", "PUBLISHEDNEWER")
    repo.put([Candle("vn", "PUBLISHEDNEWER", "1D", parse_time("2024-01-05"), 30, 31, 29, 30, 3000)])
    published_newer = repo.read("vn", "PUBLISHEDNEWER", "1D")[0]
    app.state.archive.publish([published_newer], prune=True)
    older_pending = app.state.archive.prepare(
        [Candle("vn", "PUBLISHEDNEWER", "1D", parse_time("2024-01-04"), 29, 30, 28, 29, 2900)]
    )
    repo.publish_archive(older_pending | {"status": "pending_repair"})
    newer_published_latest = client.get(
        "/tickers?symbol=PUBLISHEDNEWER&limit=1&ma=false&redis=false&cache=false"
    )
    assert newer_published_latest.status_code == 200
    assert newer_published_latest.json()["PUBLISHEDNEWER"][0]["close"] == 30

    bounded_history = app.state.history.read(
        "vn",
        "PENDINGARCHIVE",
        "1D",
        start=archived.time,
        end=archived.time,
        limit=1,
        ignore_pending_repairs=True,
    )
    assert len(bounded_history) == 1
    assert bounded_history[0].close == 10


@pytest.mark.parametrize("interval", ["1D", "1W"])
def test_latest_yahoo_merged_reads_omit_only_pending_empty_sources(client, monkeypatch, interval):
    repo = client.app_instance.state.repo
    history = client.app_instance.state.history
    original_read = history.read
    for symbol in ("PENDING", "SHARED"):
        repo.register("yahoo", symbol)
        repo.register("sjc", symbol)
    repo.put(
        [
            Candle(
                "yahoo",
                "SHARED",
                "1D",
                parse_time("2024-09-02"),
                100,
                102,
                99,
                101,
                1000,
            )
        ]
    )

    def read(source, symbol, native_interval, *args, **kwargs):
        if source == "sjc" and symbol in {"PENDING", "SHARED"}:
            raise DataError(f"Historical adjustment repair pending for {symbol} {native_interval}")
        return original_read(source, symbol, native_interval, *args, **kwargs)

    monkeypatch.setattr(history, "read", read)

    params = f"mode=yahoo&interval={interval}&limit=1&ma=false&redis=false&cache=false"
    pending = client.get(f"/tickers?symbol=PENDING&{params}")
    shared = client.get(f"/tickers?symbol=SHARED&{params}")

    assert pending.status_code == 200
    assert pending.json() == {}
    assert shared.status_code == 200
    assert len(shared.json()["SHARED"]) == 1


def test_mode_all_uses_rust_source_priority_for_duplicate_symbols(client):
    repo = client.app_instance.state.repo
    repo.register("vn", "CAR")
    repo.register("yahoo", "CAR")
    repo.put(
        [
            Candle("vn", "CAR", "1D", parse_time("2024-09-01"), 100, 101, 99, 100, 1000),
            Candle("yahoo", "CAR", "1D", parse_time("2024-09-01"), 500, 501, 499, 500, 2000),
        ]
    )

    response = client.get("/tickers?symbol=CAR&mode=all&limit=1&ma=false")

    assert response.status_code == 200
    assert response.json()["CAR"][0]["close"] == 100


def test_aggregated_batch_uses_first_symbol_for_redis_coverage(tmp_path):
    class FakeRedis:
        def __init__(self):
            self.zsets = {}

        def zrevrange(self, name, start, end, *, withscores=False):
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
            selected = [item for item in values if minimum <= item[0] <= maximum][start:]
            if num is not None and num >= 0:
                selected = selected[:num]
            return [member for _, member in selected]

    settings = replace(
        Settings(),
        database=tmp_path / "batch-coverage.sqlite3",
        cache_dir=tmp_path / "batch-coverage-cache",
        object_dir=tmp_path / "batch-coverage-objects",
        archive_backend="filesystem",
        redis_daily_max_size=10,
    )
    redis = FakeRedis()
    app = create_app(settings, redis_client=redis)
    with TestClient(app) as client:
        origin = datetime(2024, 1, 1, tzinfo=UTC)
        rows = [
            Candle(
                "vn",
                "FPT",
                "1D",
                int((origin + timedelta(days=i)).timestamp()),
                100 + i,
                110 + i,
                90 + i,
                100 + i,
                1000,
                "fixture",
            )
            for i in range(5)
        ]
        rows.extend(
            Candle(
                "vn",
                "VCB",
                "1D",
                int((origin + timedelta(days=i)).timestamp()),
                500 + i,
                900 + i if 7 <= i <= 9 else 600 + i,
                490 + i,
                500 + i,
                2000,
                "fixture",
            )
            for i in range(20)
        )
        rows.extend(
            Candle(
                "yahoo",
                "AAPL",
                "1D",
                int((origin + timedelta(days=i)).timestamp()),
                10_000 if 10 <= i <= 13 else 500 + i,
                (10_005 if 10 <= i <= 13 else 505 + i),
                9_995 if 10 <= i <= 13 else 495 + i,
                10_000 if 10 <= i <= 13 else 500 + i,
                3000,
                "fixture",
            )
            for i in range(20)
        )
        rows.extend(
            Candle(
                "vn",
                symbol,
                "1D",
                int((origin + timedelta(days=i)).timestamp()),
                base + i,
                base + i + 1,
                base + i - 1,
                base + i,
                1000,
                "fixture",
            )
            for symbol, base, count in (("AAA", 100, 10), ("BBB", 200, 20))
            for i in range(count)
        )
        app.state.repo.put(rows)
        for symbol in ("FPT", "VCB"):
            symbol_rows = [row for row in rows if row.source == "vn" and row.symbol == symbol]
            if symbol == "VCB":
                symbol_rows = symbol_rows[-10:]
            key = f"ohlcv:vn:{symbol}:1D"
            redis.zsets[key] = [
                (
                    row.time * 1000,
                    f"{row.time * 1000}|{row.open}|{row.high}|{row.low}|{row.close}|{row.volume}|1",
                )
                for row in symbol_rows
            ]

        redis_batch = client.get(
            "/tickers?symbol=FPT&symbol=VCB&interval=1W&start_date=2024-01-08"
            "&end_date=2024-01-20&limit=2&ma=false&cache=false"
        )
        postgres_batch = client.get(
            "/tickers?symbol=VCB&symbol=FPT&interval=1W&start_date=2024-01-08"
            "&end_date=2024-01-20&limit=2&ma=false&cache=false"
        )
        yahoo_with_extra_source = client.get(
            "/tickers?symbol=AAPL&mode=yahoo&interval=1W&start_date=2024-01-15"
            "&end_date=2024-01-20&limit=2&ma=true&cache=false"
        )
        end_only_batch = client.get(
            "/tickers?symbol=AAA&symbol=BBB&interval=1W&end_date=2024-01-10"
            "&limit=2&ma=false&redis=false&cache=false"
        )
        native_end_only_batch = client.get(
            "/tickers?symbol=AAA&symbol=BBB&interval=1D&end_date=2024-01-10"
            "&limit=2&ma=false&redis=false&cache=false"
        )

    assert redis_batch.status_code == postgres_batch.status_code == 200
    assert redis_batch.json()["VCB"][0]["high"] == 613
    assert postgres_batch.json()["VCB"][0]["high"] == 909
    assert yahoo_with_extra_source.status_code == 200
    row = yahoo_with_extra_source.json()["AAPL"][0]
    assert row["close"] == 519
    assert row["ma10"] == 519
    assert end_only_batch.status_code == 200
    assert set(end_only_batch.json()) == {"AAA", "BBB"}
    assert native_end_only_batch.status_code == 200
    assert set(native_end_only_batch.json()) == {"AAA", "BBB"}


def test_mode_all_legacy_scaling_requires_three_ascii_letters(client):
    repo = client.app_instance.state.repo
    repo.register("yahoo", "A1A")
    repo.put([Candle("yahoo", "A1A", "1D", parse_time("2024-09-01"), 1000, 1010, 990, 1000, 1000)])

    response = client.get("/tickers?symbol=A1A&mode=all&limit=1&ma=false&legacy=true&cache=false")

    assert response.status_code == 200
    assert response.json()["A1A"][0]["close"] == 1000


def test_negative_futures_previous_close_omits_changed_money(client):
    repo = client.app_instance.state.repo
    repo.register("yahoo", "CL=F")
    repo.put(
        [
            Candle("yahoo", "CL=F", "1D", parse_time("2024-09-01"), -37, -36, -38, -37, 100),
            Candle("yahoo", "CL=F", "1D", parse_time("2024-09-02"), -36, -35, -37, -36, 120),
        ]
    )

    response = client.get("/tickers?symbol=CL=F&mode=yahoo&limit=1&ma=false&cache=false")

    assert response.status_code == 200
    assert "total_money_changed" not in response.json()["CL=F"][0]


def test_health_uses_rust_major_ticker_sync_selection(client):
    data = client.get("/health").json()
    repo = client.app_instance.state.repo
    coverage = repo.status()["coverage"]
    row = next(r for r in coverage if r["source"] == "vn" and r["interval"] == "1D")
    assert row["rows"] == data["daily_records_count"] == 720
    first = datetime.fromtimestamp(row["first"], UTC)
    latest = datetime.fromtimestamp(row["last"], UTC)
    last_ingest = datetime.fromtimestamp(row["last_ingest_ns"] / 1e9, UTC)
    assert first.isoformat().startswith("2024-01-01")
    assert latest.isoformat().startswith("2024-08-27")
    assert last_ingest > latest
    selected_sync = repo.health_summary()["last_sync"].get("1D")
    assert data["daily_last_sync"] == (
        datetime.fromtimestamp(selected_sync / 1e9, UTC).isoformat().replace("+00:00", "Z")
        if selected_sync is not None
        else None
    )


def test_health_endpoint_refreshes_its_rust_style_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "HEALTH_SNAPSHOT_REFRESH_SECONDS", 0.02)
    settings = replace(
        Settings(),
        database=tmp_path / "db.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
    )
    app = create_app(settings)
    repo = app.state.repo
    repo.initialize()
    repo.register("vn", "AAA", "First")
    repo.put([Candle("vn", "AAA", "1D", parse_time("2026-10-20"), 100, 101, 99, 100, 1000)])

    with TestClient(app) as client:
        initial = client.get("/health").json()
        initial_tickers = repo.health_summary()["tickers"]
        assert initial["total_tickers_count"] == initial_tickers
        assert initial["daily_records_count"] == 1

        repo.register("vn", "BBB", "Second")
        repo.put([Candle("vn", "BBB", "1D", parse_time("2026-10-20"), 200, 201, 199, 200, 2000)])
        expected_tickers = repo.health_summary()["tickers"]
        deadline = time.monotonic() + 2
        refreshed = initial
        while time.monotonic() < deadline:
            refreshed = client.get("/health").json()
            if refreshed["total_tickers_count"] == expected_tickers:
                break
            time.sleep(0.01)

    assert refreshed["total_tickers_count"] == initial_tickers + 1
    assert refreshed["daily_records_count"] == 2


def test_health_snapshot_retries_initial_failure_without_waiting_a_refresh_interval(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app_module, "HEALTH_SNAPSHOT_REFRESH_SECONDS", 60)
    settings = replace(
        Settings(),
        database=tmp_path / "db.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
    )
    app = create_app(settings)
    repo = app.state.repo
    repo.initialize()
    repo.register("vn", "AAA", "First")
    repo.put([Candle("vn", "AAA", "1D", parse_time("2026-10-20"), 100, 101, 99, 100, 1000)])
    original_health_summary = repo.health_summary
    calls = 0

    def fail_initial_refresh_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient health query failure")
        return original_health_summary()

    repo.health_summary = fail_initial_refresh_once

    with TestClient(app) as client:
        deadline = time.monotonic() + 2
        response = client.get("/health")
        while time.monotonic() < deadline:
            response = client.get("/health")
            if response.json()["daily_records_count"] == 1:
                break
            time.sleep(0.01)

    assert calls >= 2
    assert response.json()["daily_records_count"] == 1


def test_csv_legacy_and_index_scaling(client):
    response = client.get("/tickers?symbol=FPT&symbol=VNINDEX&limit=1&format=csv&legacy=true")
    reader = csv.DictReader(io.StringIO(response.text))
    assert tuple(reader.fieldnames) == CSV_COLUMNS
    rows = list(reader)
    assert float(rows[0]["close"]) == 102.39
    # Legacy behavior scales OHLC, not MAs or index prices.
    assert float(rows[0]["ma20"]) == 102295
    assert float(rows[1]["close"]) == 3590


def test_legacy_scales_vn_aliases_not_in_rust_index_list(client):
    repo = client.app_instance.state.repo
    repo.register("vn", "HNXINDEX")
    repo.put([Candle("vn", "HNXINDEX", "1D", parse_time("2024-01-01"), 1000, 1000, 1000, 1000, 1)])

    response = client.get("/tickers?symbol=HNXINDEX&limit=1&ma=false&legacy=true")

    assert response.status_code == 200
    assert response.json()["HNXINDEX"][0]["close"] == 1


def test_query_validation_and_ma_false(client):
    assert client.get("/tickers?interval=bogus").status_code == 400
    assert client.get("/tickers?limit=0").json() == {}
    negative_default = client.get("/tickers?symbol=FPT&limit=-1&cache=false").json()["FPT"]
    negative_no_ma = client.get("/tickers?symbol=FPT&limit=-1&ma=false&redis=false&cache=false")
    assert len(negative_default) == 199
    # With no configured Redis, Rust's SQL path binds limit + 1 = 0 when MA is
    # disabled, so the signed limit returns an empty response.
    assert negative_no_ma.status_code == 200 and negative_no_ma.json() == {}
    assert client.get("/tickers?symbol=FPT&limit=-1&ma=false&redis=false&cache=false").json() == {}
    ignored_date = client.get("/tickers?symbol=FPT&limit=1&start_date=broken&ma=false")
    assert ignored_date.status_code == 200 and len(ignored_date.json()["FPT"]) == 1
    reversed_dates = client.get(
        "/tickers?symbol=FPT&start_date=2024-02-02&end_date=2024-02-01&ma=false"
    )
    assert reversed_dates.status_code == 200 and reversed_dates.json() == {}
    assert client.get("/tickers?ma=yes").status_code == 400


def test_csv_symbol_escaping_matches_rust_direct_field_writes(client):
    repo = client.app_instance.state.repo
    repo.register("vn", "A,B")
    repo.put([Candle("vn", "A,B", "1D", parse_time("2024-09-01"), 10, 11, 9, 10, 100)])

    response = client.get("/tickers?symbol=A%2CB&limit=1&ma=false&format=csv&cache=false")

    assert response.status_code == 200
    assert response.text.splitlines()[1].startswith("A,B,2024-09-01,10.000,")
    assert not response.text.splitlines()[1].startswith('"A,B"')
    row = client.get("/tickers?symbol=FPT&limit=1&ma=false").json()["FPT"][0]
    assert "ma20" not in row
    assert "close_changed" in row
    assert client.get("/tickers?symbol=FPT&mode=stocks&limit=1").status_code == 200


def test_health_stays_responsive_while_read_worker_is_blocked(client, monkeypatch):
    app = client.app_instance
    entered = threading.Event()
    release = threading.Event()
    original = app.state.history.query_many

    def blocked_query(*args, **kwargs):
        entered.set()
        release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(app.state.history, "query_many", blocked_query)
    with ThreadPoolExecutor(max_workers=1) as caller:
        request = caller.submit(client.get, "/tickers?symbol=FPT&limit=1")
        try:
            assert entered.wait(2)
            started = time.monotonic()
            response = client.get("/health")
            assert response.status_code == 200
            assert time.monotonic() - started < 1
        finally:
            release.set()
        assert request.result(timeout=5).status_code == 200


def test_health_counts_are_maintained_by_candle_writes(client):
    repo = client.app_instance.state.repo
    health_now = datetime(2026, 10, 20, 3, tzinfo=UTC)
    baseline = repo.health_summary(now=health_now)["records"].get("1D", 0)
    baseline_sync = repo.health_summary(now=health_now)["last_sync"]["1D"]
    first_time = parse_time("2024-09-01")
    candles = [
        Candle("vn", "AAA", "1D", first_time, 100, 101, 99, 100, 1000),
        Candle("vn", "AAA", "1D", first_time + 86400, 101, 102, 100, 101, 1100),
    ]

    assert repo.put(candles) == 2
    assert repo.health_summary()["records"]["1D"] == baseline + 2
    assert repo.put([candles[0]]) == 1
    assert repo.health_summary()["records"]["1D"] == baseline + 2
    with repo.connect() as con:
        con.execute(
            "UPDATE candles SET updated_at=? WHERE source='vn' AND symbol='AAA' AND interval='1D' AND time=?",
            (baseline_sync, first_time),
        )
        con.execute(
            "UPDATE candles SET updated_at=? WHERE source='vn' AND symbol='AAA' AND interval='1D' AND time=?",
            (baseline_sync + 1, first_time + 86400),
        )
    assert repo.health_summary(now=health_now)["last_sync"]["1D"] == baseline_sync
    with repo.connect() as con:
        latest_vnindex = con.execute(
            "SELECT time,updated_at FROM candles WHERE source='vn' AND symbol='VNINDEX' "
            "AND interval='1D' ORDER BY time DESC LIMIT 1"
        ).fetchone()
        con.execute(
            "UPDATE candles SET updated_at=? WHERE source='vn' AND symbol='VNINDEX' "
            "AND interval='1D' AND time=?",
            (baseline_sync + 1, latest_vnindex["time"]),
        )
    assert repo.health_summary(now=health_now)["last_sync"]["1D"] == baseline_sync + 1
    with repo.connect() as con:
        con.execute(
            "UPDATE candles SET updated_at=? WHERE source='vn' AND symbol='VNINDEX' "
            "AND interval='1D' AND time=?",
            (latest_vnindex["updated_at"], latest_vnindex["time"]),
        )
    assert repo.health_summary(now=health_now)["last_sync"]["1D"] == baseline_sync
    with repo.connect() as con:
        con.execute(
            "DELETE FROM candles WHERE source='vn' AND symbol='AAA' AND interval='1D' AND time=?",
            (first_time + 86400,),
        )
    assert repo.health_summary()["records"]["1D"] == baseline + 1
    assert repo.health_summary(now=health_now)["last_sync"]["1D"] == baseline_sync


def test_health_active_tickers_follow_recent_daily_rows(client):
    repo = client.app_instance.state.repo
    now = datetime.now(UTC)
    repo.register("vn", "STALE_ENABLED", enabled=True)
    repo.put(
        [
            Candle(
                "vn",
                "STALE_ENABLED",
                "1D",
                parse_time((now.date() - timedelta(days=8)).isoformat()),
                10,
                11,
                9,
                10,
                100,
            ),
            Candle(
                "vn",
                "RECENT_DISABLED",
                "1D",
                parse_time((now.date() - timedelta(days=1)).isoformat()),
                10,
                11,
                9,
                10,
                100,
            ),
        ]
    )
    with repo.connect() as con:
        assert (
            con.execute(
                "SELECT enabled FROM tickers WHERE source='vn' AND symbol='RECENT_DISABLED'"
            ).fetchone()[0]
            == 0
        )
    assert repo.health_summary(now=now)["active_tickers"] == 1


@pytest.mark.parametrize("interval", ["1h", "1m"])
def test_health_intraday_sync_uses_only_the_current_year_partition(client, interval):
    repo = client.app_instance.state.repo
    now = datetime(2026, 1, 2, 3, tzinfo=UTC)
    repo.put(
        [
            Candle(
                "vn",
                "VNINDEX",
                interval,
                parse_time("2025-12-31"),
                100,
                101,
                99,
                100,
                1000,
            )
        ]
    )
    assert interval not in repo.health_summary(now=now)["last_sync"]

    repo.put(
        [
            Candle(
                "vn",
                "VNINDEX",
                interval,
                parse_time("2026-01-01"),
                100,
                101,
                99,
                100,
                1000,
            )
        ]
    )
    assert repo.health_summary(now=now)["last_sync"][interval] > 0


@pytest.mark.parametrize("legacy_version", [1, 2])
def test_health_stats_upgrade_replaces_old_summary_triggers(client, legacy_version):
    repo = client.app_instance.state.repo
    health_now = datetime(2026, 10, 20, 3, tzinfo=UTC)
    with repo.connect() as con:
        con.execute("UPDATE meta SET value=? WHERE key='health_stats_version'", (legacy_version,))
        if legacy_version == 2:
            con.execute(
                "CREATE TABLE candle_health_sync(interval TEXT, updated_at INTEGER, row_count INTEGER, "
                "PRIMARY KEY(interval,updated_at))"
            )
            con.execute("INSERT INTO candle_health_sync VALUES ('1D',123,1)")
        con.execute("UPDATE candle_health SET last_sync=last_sync+99 WHERE interval='1D'")
        for trigger in (
            "candles_health_insert",
            "candles_health_update",
            "candles_health_delete",
        ):
            con.execute(f"DROP TRIGGER {trigger}")
        con.execute(
            """CREATE TRIGGER IF NOT EXISTS candles_health_insert AFTER INSERT ON candles BEGIN
            INSERT INTO candle_health(interval,row_count,last_sync) VALUES (NEW.interval,1,NEW.updated_at)
            ON CONFLICT(interval) DO UPDATE SET row_count=row_count+1,last_sync=MAX(last_sync,NEW.updated_at);
            END"""
        )
        con.execute(
            """CREATE TRIGGER IF NOT EXISTS candles_health_update AFTER UPDATE ON candles BEGIN
            UPDATE candle_health SET last_sync=MAX(last_sync,NEW.updated_at) WHERE interval=NEW.interval;
            END"""
        )
        con.execute(
            """CREATE TRIGGER IF NOT EXISTS candles_health_delete AFTER DELETE ON candles BEGIN
            UPDATE candle_health SET row_count=MAX(0,row_count-1) WHERE interval=OLD.interval;
            DELETE FROM candle_health WHERE interval=OLD.interval AND row_count=0;
            END"""
        )

    repo.initialize()
    summary = repo.health_summary(now=health_now)
    with repo.connect() as con:
        assert (
            con.execute("SELECT value FROM meta WHERE key='health_stats_version'").fetchone()[0]
            == 3
        )
        assert (
            con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='candle_health_sync'"
            ).fetchone()
            is None
        )
        trigger = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='candles_health_update'"
        ).fetchone()[0]
        assert "UPDATE OF interval,updated_at" in trigger
        actual = con.execute("SELECT MAX(updated_at) FROM candles WHERE interval='1D'").fetchone()[
            0
        ]
    assert summary["last_sync"]["1D"] == actual
    assert summary["records"]["1D"] == 720

    inserted = Candle("vn", "HEALTHUPGRADE", "1D", parse_time("2030-01-01"), 10, 11, 9, 10, 100)
    assert repo.put([inserted]) == 1
    with repo.connect() as con:
        con.execute(
            "UPDATE candles SET interval='1h' WHERE source='vn' "
            "AND symbol='HEALTHUPGRADE' AND interval='1D'"
        )
    summary = repo.health_summary(now=health_now)
    assert summary["records"]["1D"] == 720
    assert summary["records"]["1h"] == 1
    with repo.connect() as con:
        con.execute(
            "DELETE FROM candles WHERE source='vn' AND symbol='HEALTHUPGRADE' AND interval='1h'"
        )
    summary = repo.health_summary(now=health_now)
    assert summary["last_sync"]["1D"] == actual
    assert summary["records"]["1D"] == 720
    assert "1h" not in summary["records"]


@pytest.mark.asyncio
async def test_slow_request_body_uses_rust_request_timeout(tmp_path):
    settings = replace(
        __import__("aipriceaction_api.config", fromlist=["Settings"]).Settings(),
        database=tmp_path / "slow-body.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
        query_timeout=0.01,
    )
    app = create_app(settings)

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(0.05)
            yield b"slow body"

        async def aclose(self):
            return None

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as caller:
        response = await caller.post(
            "/sync/not-a-key",
            content=SlowBody(),
            headers={"content-type": "application/json"},
        )

    # Axum applies one request deadline that includes body extraction.
    assert response.status_code == 408
    assert response.content == b""


@pytest.mark.asyncio
async def test_body_and_handler_share_one_request_deadline(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "shared-deadline.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
        sync_tokens=("test-token",),
        query_timeout=0.05,
    )
    app = create_app(settings)
    handler_started = threading.Event()

    def slow_sync(key, secret, value=None, write=False):
        handler_started.set()
        time.sleep(0.03)
        return {"value": value}

    app.state.repo.sync = slow_sync

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(0.03)
            yield b'{"secret":"s","value":{}}'

        async def aclose(self):
            return None

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as caller:
            response = await caller.post(
                "/sync/550e8400-e29b-41d4-a716-446655440000",
                content=SlowBody(),
                headers={
                    "authorization": "Bearer test-token",
                    "content-type": "application/json",
                },
            )

    assert handler_started.is_set()
    assert response.status_code == 408
    assert response.content == b""


def test_market_wide_reads_use_one_shared_history_batch(tmp_path, monkeypatch):
    settings = replace(
        __import__("aipriceaction_api.config", fromlist=["Settings"]).Settings(),
        database=tmp_path / "bounded.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
        market_read_concurrency=2,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        rows = [
            Candle("vn", symbol, "1D", parse_time("2024-01-01"), 100, 101, 99, 100, 1000)
            for symbol in ("AAA", "BBB", "CCC")
        ]
        app.state.repo.put(rows)
        active = maximum = 0
        lock = threading.Lock()
        original = app.state.history.query_many

        def delayed_query(*args, **kwargs):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                time.sleep(0.03)
                return original(*args, **kwargs)
            finally:
                with lock:
                    active -= 1

        monkeypatch.setattr(app.state.history, "query_many", delayed_query)
        response = client.get("/tickers?mode=vn&limit=1&ma=false&cache=false")

    assert response.status_code == 200
    assert set(response.json()) == {"AAA", "BBB", "CCC"}
    assert maximum == 1


def test_ticker_sql_errors_discard_rust_equivalent_source_batches(client, monkeypatch):
    app = client.app_instance
    stamp = parse_time("2024-01-02")
    app.state.repo.register("yahoo", "AAPL", "Apple")
    app.state.repo.put(
        [Candle("yahoo", "AAPL", "1D", stamp, 200, 202, 198, 201, 2000, "fixture")]
    )
    original_query_many = app.state.history.query_many

    def fail_fpt(source, symbols, *args, **kwargs):
        if "FPT" in symbols:
            raise sqlite3.OperationalError("injected ticker read failure")
        return original_query_many(source, symbols, *args, **kwargs)

    monkeypatch.setattr(app.state.history, "query_many", fail_fpt)

    single_source = client.get(
        "/tickers?symbol=FPT&symbol=VCB&mode=vn&limit=1&ma=false"
        "&redis=false&snap=false&cache=false"
    )
    assert single_source.status_code == 200
    assert single_source.json() == {}
    assert single_source.headers["x-data-source"] == "database"

    all_sources = client.get(
        "/tickers?symbol=FPT&symbol=AAPL&mode=all&limit=1&ma=false"
        "&redis=false&snap=false&cache=false"
    )
    assert all_sources.status_code == 200
    assert list(all_sources.json()) == ["AAPL"]
    assert len(all_sources.json()["AAPL"]) == 1
    assert all_sources.headers["x-data-source"] == "database"


def test_head_uses_get_handler_and_returns_no_body(client):
    get_response = client.get("/health", headers={"accept-encoding": "identity"})
    head_response = client.head("/health", headers={"accept-encoding": "br"})

    assert get_response.status_code == head_response.status_code == 200
    assert head_response.content == b""
    assert head_response.headers["content-type"] == get_response.headers["content-type"]
    assert head_response.headers["content-encoding"] == "br"
    assert "accept-encoding" in head_response.headers["vary"].lower()


def test_head_preflight_matches_rust_allow_list_and_status(client):
    response = client.options(
        "/health",
        headers={
            "Origin": "https://aipriceaction.com",
            "Access-Control-Request-Method": "HEAD",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://aipriceaction.com"
    assert response.headers["access-control-allow-methods"] == "GET,POST,DELETE,OPTIONS"
    assert (
        response.headers["access-control-allow-headers"] == "content-type,authorization,user-agent"
    )
    assert "access-control-max-age" not in response.headers
    assert response.headers["allow"] == "GET,HEAD"
    assert response.content == b""


def test_query_timeout_matches_rust_empty_408(tmp_path):
    settings = replace(
        __import__("aipriceaction_api.config", fromlist=["Settings"]).Settings(),
        database=tmp_path / "timeout.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
        query_timeout=0,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
    assert response.status_code == 408
    assert response.content == b""


def test_cancelled_read_keeps_capacity_until_worker_finishes(tmp_path, monkeypatch):
    settings = replace(
        __import__("aipriceaction_api.config", fromlist=["Settings"]).Settings(),
        database=tmp_path / "cancel.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
        query_timeout=0.1,
        read_concurrency=1,
    )
    app = create_app(settings)
    entered = threading.Event()
    release = threading.Event()
    worker_finished = threading.Event()
    both_requests_timed_out = threading.Event()
    response_lock = threading.Lock()
    timeout_responses = 0
    calls = 0
    calls_lock = threading.Lock()

    async def observe_responses(scope, receive, send):
        async def observe_send(message):
            nonlocal timeout_responses
            if message["type"] == "http.response.start" and message["status"] == 408:
                with response_lock:
                    timeout_responses += 1
                    if timeout_responses == 2:
                        both_requests_timed_out.set()
            await send(message)

        await app(scope, receive, observe_send)

    with TestClient(observe_responses) as client:
        client.app_instance = app
        app.state.repo.put(
            [Candle("vn", "FPT", "1D", parse_time("2024-01-01"), 100, 101, 99, 100, 1000)]
        )
        original = app.state.history.query_many

        def blocked_query(*args, **kwargs):
            nonlocal calls
            with calls_lock:
                calls += 1
            entered.set()
            try:
                release.wait(5)
                return original(*args, **kwargs)
            finally:
                worker_finished.set()

        monkeypatch.setattr(app.state.history, "query_many", blocked_query)
        with ThreadPoolExecutor(max_workers=2) as caller:
            first = caller.submit(client.get, "/tickers?symbol=FPT&limit=1")
            second = None
            try:
                assert entered.wait(2)
                # A second request must wait on the occupied slot rather than
                # creating another unbounded queued worker task.
                second = caller.submit(client.get, "/tickers?symbol=FPT&limit=1")
                assert both_requests_timed_out.wait(2)
                with calls_lock:
                    assert calls == 1
                started = time.monotonic()
                assert client.get("/health").status_code == 200
                assert time.monotonic() - started < 1
            finally:
                release.set()
            assert worker_finished.wait(2)
            assert first.result(timeout=5).status_code == 408
            assert second.result(timeout=5).status_code == 408
        with calls_lock:
            assert calls == 1


def test_start_date_returns_first_candles_instead_of_latest(client):
    rows = client.get("/tickers?symbol=FPT&start_date=2024-02-01&limit=2").json()["FPT"]
    assert [r["time"] for r in rows] == ["2024-02-01", "2024-02-02"]
    monthly = client.get("/tickers?symbol=FPT&interval=1M&start_date=2024-02-01&limit=1").json()[
        "FPT"
    ]
    assert monthly[0]["time"] == "2024-02-01"
    assert monthly[0]["close"] == 100590  # Complete February, including leap day.


def test_minute_monthly_and_weekly_contract(client):
    assert client.get("/tickers?symbol=FPT&interval=1m").json() == {}
    monthly = client.get("/tickers?symbol=FPT&interval=1M&limit=2").json()["FPT"]
    assert len(monthly) == 2
    assert all(row["time"].endswith("-01") for row in monthly)
    weekly = client.get("/tickers?symbol=FPT&interval=1W&limit=2").json()["FPT"]
    assert all(datetime.fromisoformat(r["time"]).weekday() == 0 for r in weekly)


@pytest.mark.parametrize("iv", ["1W", "2W", "1M"])
@pytest.mark.parametrize("indicator", ["none", "sma", "ema"])
def test_dated_aggregate_without_redis_keeps_bounded_sql_bucket(client, monkeypatch, iv, indicator):
    monkeypatch.setattr(client.app_instance.state.history, "redis_client", None)
    response = client.get(
        "/tickers",
        params={
            "symbol": "FPT",
            "interval": iv,
            "start_date": "2024-02-07",
            "end_date": "2024-02-08",
            "ma": str(indicator != "none").lower(),
            "ema": str(indicator == "ema").lower(),
            "limit": 1,
            "redis": "false",
        },
    )
    assert response.status_code == 200
    rows = response.json()["FPT"]
    assert len(rows) == 1
    assert (
        rows[0]["time"]
        == {
            "1W": "2024-02-05",
            "2W": "2024-02-05",
            "1M": "2024-02-01",
        }[iv]
    )
    assert rows[0]["open"] == 100370
    assert rows[0]["high"] == 100390
    assert rows[0]["low"] == 100360
    assert rows[0]["close"] == 100380
    assert rows[0]["volume"] == 400075


def test_midmonth_limit_completes_only_requested_first_bucket(client):
    response = client.get(
        "/tickers",
        params={
            "symbol": "FPT",
            "interval": "1M",
            "start_date": "2024-02-01",
            "limit": 1,
            "ma": "false",
            "redis": "false",
        },
    )
    row = response.json()["FPT"][0]
    assert row["time"] == "2024-02-01"
    assert row["open"] == 100310 and row["close"] == 100590
    assert row["volume"] == sum(200000 + i for i in range(31, 60))


def test_dated_aggregate_without_redis_uses_bounded_sql_for_both_redis_flags(client, monkeypatch):
    monkeypatch.setattr(client.app_instance.state.history, "redis_client", None)
    response = client.get(
        "/tickers",
        params={
            "symbol": "FPT",
            "interval": "1W",
            "start_date": "2024-02-07",
            "end_date": "2024-02-12",
            "limit": 1,
            "ma": "false",
            "cache": "false",
        },
    )
    postgres = client.get(
        "/tickers",
        params={
            "symbol": "FPT",
            "interval": "1W",
            "start_date": "2024-02-07",
            "end_date": "2024-02-12",
            "limit": 1,
            "ma": "false",
            "redis": "false",
            "cache": "false",
        },
    )
    assert response.status_code == 200
    assert response.json() == postgres.json()
    assert response.json()["FPT"][0]["time"] == "2024-02-05"
    assert response.json()["FPT"][0]["close"] == 100410


@pytest.mark.parametrize("end_only", [False, True], ids=["latest", "end-only"])
def test_undated_aggregate_without_redis_keeps_full_sql_lookback(client, end_only):
    """A missing Redis client must not turn SQLite into a retained-cache tail."""
    origin = datetime(2000, 1, 1, tzinfo=UTC)
    rows = []
    for i in range(8000):
        close = 100_000 + i
        rows.append(
            Candle(
                "vn",
                "FPT",
                "1D",
                int((origin + timedelta(days=i)).timestamp()),
                close,
                close + 10,
                close - 10,
                close,
                200_000 + i,
                "fixture",
            )
        )
    client.app_instance.state.repo.put(rows)

    params = {
        "symbol": "FPT",
        "interval": "1W",
        "limit": 800,
        "ma": "true",
        "redis": "true",
        "cache": "false",
    }
    if end_only:
        params["end_date"] = (origin + timedelta(days=7999)).date().isoformat()

    response = client.get("/tickers", params=params)

    assert response.status_code == 200
    weekly = response.json()["FPT"]
    # 8,000 daily rows contain enough weekly history for all 800 requested
    # bars and their MA200. The default 5,000-row cache retention does not.
    assert len(weekly) == 800
    assert weekly[0]["ma200"] is not None


def test_minute_only_ticker_does_not_synthesize_rust_hourly_intervals(client):
    origin = parse_time("2024-01-03T02:15:00+00:00")
    client.app_instance.state.repo.put(
        [
            Candle("vn", "OCB", "1m", origin + i * 60, 100 + i, 102 + i, 99 + i, 101 + i, 10)
            for i in range(120)
        ]
    )
    params = dict(symbol="OCB", limit=20, ma="false", cache="false")
    assert client.get("/tickers", params={**params, "interval": "1m"}).json()["OCB"]
    for alias in ("1H", "hourly"):
        assert client.get("/tickers", params={**params, "interval": alias}).json() == {}
    assert client.get("/tickers", params={**params, "interval": "4h"}).json() == {}


def test_health_reports_safe_opentelemetry_configuration(client, monkeypatch):
    monkeypatch.setenv("OTEL_ENABLED", "false")
    monkeypatch.setenv("OTEL_ENDPOINT", "http://collector:5080")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "aipriceaction-health-test")
    monkeypatch.setenv("OTEL_AUTH_HEADER", "Basic health-test-secret")

    response = client.get("/health")
    assert response.status_code == 200
    otel = response.json()["configuration"]["opentelemetry"]
    assert otel == {
        "enabled": False,
        "endpoint_configured": True,
        "service_name": "aipriceaction-health-test",
        "auth_configured": True,
        "exporter_initialized": False,
    }
    assert "collector:5080" not in response.text
    assert "health-test-secret" not in response.text


def test_metadata_static_and_health(client):
    assert "ALL_STOCKS" not in client.get("/tickers/group").json()
    assert "FPT" in client.get("/tickers/name").json()
    assert "SJC-GOLD" in client.get("/tickers/group?mode=all").json()["Commodity"]
    assert client.get("/tickers/info?ticker=FPT").json()["ticker"] == "FPT"
    health_response = client.get("/health")
    health = health_response.json()
    assert list(health) == sorted(health)
    health_field_positions = [
        health_response.content.index(f'"{field}":'.encode()) for field in sorted(health)
    ]
    assert health_field_positions == sorted(health_field_positions)
    assert set(health) == {
        "configuration",
        "total_tickers_count",
        "active_tickers_count",
        "is_trading_hours",
        "trading_hours_timezone",
        "uptime_secs",
        "current_system_time",
        "daily_records_count",
        "daily_last_sync",
        "hourly_records_count",
        "hourly_last_sync",
        "minute_records_count",
        "minute_last_sync",
        "crypto_last_sync",
        "daily_iteration_count",
        "slow_iteration_count",
        "crypto_iteration_count",
        "memory_usage_bytes",
        "memory_usage_mb",
        "memory_limit_mb",
        "memory_usage_percent",
        "disk_cache_entries",
        "disk_cache_size_bytes",
        "disk_cache_size_mb",
        "disk_cache_limit_mb",
        "disk_cache_usage_percent",
    }
    assert health["daily_records_count"] == 720
    assert health["trading_hours_timezone"] == "Asia/Ho_Chi_Minh"
    assert "storage" not in health
    assert health["configuration"]["ingest_universe"] == "catalog"
    assert health["configuration"]["vn_direct_access_enabled"] is True
    assert health["configuration"]["http_proxies_configured"] is False
    assert health["configuration"]["api_read_concurrency"] == 4
    assert health["configuration"]["api_market_read_concurrency"] == 4
    assert client.get("/explorer").status_code == 200
    ranged_explorer = client.get("/explorer", headers={"Range": "bytes=0-0"})
    assert ranged_explorer.status_code == 200
    assert ranged_explorer.headers["content-type"] == "text/html; charset=utf-8"
    assert "content-range" not in ranged_explorer.headers
    assert "accept-ranges" not in ranged_explorer.headers
    javascript = client.get("/public/js/app.js")
    assert javascript.headers["cache-control"] == "max-age=300, public"
    assert javascript.headers["content-type"] == "text/javascript"
    assert client.get("/public/js/README.md").status_code == 404
    missing_png = client.get("/public/missing.png")
    missing_ico = client.get("/public/missing.ico")
    assert missing_png.headers["cache-control"] == "max-age=86400, public"
    assert missing_ico.headers["cache-control"] == "max-age=86400, public"
    assert missing_png.content == missing_ico.content == b""


def test_yahoo_native_empty_cache_fallback_keeps_sql_merged_sources(client, monkeypatch):
    app = client.app_instance
    origin = datetime(2024, 1, 2, tzinfo=UTC)
    rows = []
    for source, base in (("sjc", 200.0), ("yahoo", 100.0)):
        rows.extend(
            Candle(
                source,
                "COLL",
                "1D",
                int((origin + timedelta(days=index)).timestamp()),
                base + index,
                base + index + 1,
                base + index - 1,
                base + index,
                10_000 + index,
                "fixture",
                "fixture-v1",
            )
            for index in range(2)
        )
    app.state.repo.put(rows)

    # Rust can have an empty Redis batch while PostgreSQL still has rows. Keep
    # durable reads intact while modeling that cache miss.
    monkeypatch.setattr(app.state.history, "cache_tail", lambda *_args, **_kwargs: [])
    original = app.state.history.query_merged_native_sources
    merged_calls = []

    def track_merged(*args, **kwargs):
        merged_calls.append(args[:2])
        return original(*args, **kwargs)

    monkeypatch.setattr(app.state.history, "query_merged_native_sources", track_merged)
    response = client.get(
        "/tickers?symbol=COLL&mode=yahoo&interval=1D&limit=1&ma=false&cache=false"
    )

    expected = original(("sjc", "yahoo"), "COLL", "1D", limit=1, ma=False, ema=False)
    assert response.status_code == 200
    assert merged_calls == [(("sjc", "yahoo"), "COLL")]
    assert response.json()["COLL"] == expected


def test_yahoo_collision_without_interval_bars_omits_symbol(client, monkeypatch):
    app = client.app_instance
    repo = app.state.repo
    original_tickers = repo.tickers

    def tickers_with_collision(sources=None, enabled=False):
        rows = original_tickers(sources, enabled)
        if sources is not None and {"sjc", "yahoo"}.issubset(sources):
            rows.extend(
                [
                    {"source": "sjc", "symbol": "COLL"},
                    {"source": "yahoo", "symbol": "COLL"},
                ]
            )
        return rows

    monkeypatch.setattr(repo, "tickers", tickers_with_collision)
    response = client.get(
        "/tickers?symbol=COLL&mode=yahoo&interval=1h&limit=3&ma=false&format=json&cache=false"
    )

    assert response.status_code == 200
    assert response.json() == {}


def test_static_files_match_rust_validators_ranges_and_exact_routes(client):
    asset_path = "/public/js/app.js"
    asset = client.get(asset_path, headers={"accept-encoding": "identity"})
    assert asset.status_code == 200
    assert asset.content
    assert "etag" not in asset.headers
    assert "last-modified" in asset.headers

    # tower-http ignores ETags but honors HTTP-date modification validators.
    if_none_match = client.get(
        asset_path,
        headers={"accept-encoding": "identity", "if-none-match": "*"},
    )
    assert if_none_match.status_code == 200
    assert if_none_match.content == asset.content
    assert "etag" not in if_none_match.headers

    if_modified_since = client.get(
        asset_path,
        headers={
            "accept-encoding": "identity",
            "if-modified-since": "Tue, 01 Jan 2030 00:00:00 GMT",
        },
    )
    assert if_modified_since.status_code == 304
    assert if_modified_since.content == b""

    malformed_if_modified_since = client.get(
        asset_path,
        headers={
            "accept-encoding": "identity",
            "if-modified-since": "Thu, 01 Jan 2030 00:00:00",
        },
    )
    assert malformed_if_modified_since.status_code == 200
    assert malformed_if_modified_since.content == asset.content

    not_unmodified = client.get(
        asset_path,
        headers={
            "accept-encoding": "identity",
            "if-unmodified-since": "Thu, 01 Jan 1970 00:00:00 GMT",
        },
    )
    assert not_unmodified.status_code == 412

    ranged = client.get(
        asset_path,
        headers={"accept-encoding": "identity", "range": "bytes=0-31"},
    )
    assert ranged.status_code == 206
    assert ranged.headers["content-range"].startswith("bytes 0-31/")
    assert ranged.content == asset.content[:32]

    for range_header in ("bytes=bad", "bytes=999999999-"):
        rejected_range = client.get(
            asset_path,
            headers={"accept-encoding": "identity", "range": range_header},
        )
        assert rejected_range.status_code == 416
        assert rejected_range.content == b""
        assert rejected_range.headers["accept-ranges"] == "bytes"
        assert rejected_range.headers["content-range"].startswith("bytes */")

    for range_header in (
        "bytes=00-1",
        "bytes=-999999999",
        "bytes=bad,",
        "bytes=0-1,0-3",
    ):
        rejected_range = client.get(
            asset_path,
            headers={"accept-encoding": "identity", "range": range_header},
        )
        assert rejected_range.status_code == 416
        assert rejected_range.content == b""

    multipart_range = client.get(
        asset_path,
        headers={"accept-encoding": "identity", "range": "bytes=0-1,5-6"},
    )
    assert multipart_range.status_code == 416
    assert multipart_range.content == b"Cannot serve multipart range requests"

    tab_separated_range = client.get(
        asset_path,
        headers={"accept-encoding": "identity", "range": "bytes=0-1,\t5-6"},
    )
    assert tab_separated_range.status_code == 416
    assert tab_separated_range.content == b"Cannot serve multipart range requests"

    # tower-http ignores If-Range and serves its requested range.
    if_range_etag = client.get(
        asset_path,
        headers={
            "accept-encoding": "identity",
            "range": "bytes=0-31",
            "if-range": '"starlette-etag"',
        },
    )
    assert if_range_etag.status_code == 206
    assert if_range_etag.headers["content-range"].startswith("bytes 0-31/")
    assert if_range_etag.content == asset.content[:32]

    head = client.head(asset_path)
    assert head.status_code == 200
    assert head.content == b""
    assert "accept-encoding" not in head.headers.get("vary", "").lower()

    # The active Axum router matches exact paths and does not redirect slashes.
    for path in ("/health/", "/tickers/"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 404
        assert response.json() == {"error": "not found"}


def test_public_root_without_asset_directory_is_an_empty_static_404(tmp_path):
    missing_public = tmp_path / "missing-public"
    settings = replace(
        Settings(),
        database=tmp_path / "db.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
        public_dir=missing_public,
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/public")
        head = client.head("/public")
        missing_file = client.get("/public/missing.js")
        missing_file_head = client.head("/public/missing.js")
        post = client.post("/public/missing.js")
        put = client.put("/public/missing.js")

    assert response.status_code == head.status_code == 404
    assert response.content == head.content == b""
    assert "content-type" not in response.headers
    assert "content-type" not in head.headers
    assert missing_file.status_code == missing_file_head.status_code == 404
    assert missing_file.content == missing_file_head.content == b""
    for unsupported in (post, put):
        assert unsupported.status_code == 405
        assert unsupported.content == b""
        assert unsupported.headers["allow"] == "GET,HEAD"


def test_static_directory_rejects_unsupported_methods_with_allow_header(client):
    for response in (
        client.post("/public/js/app.js"),
        client.put("/public/js/app.js"),
        client.delete("/public/js/app.js"),
    ):
        assert response.status_code == 405
        assert response.content == b""
        assert response.headers["allow"] == "GET,HEAD"


def test_unknown_path_matches_rust_not_found_payload(client):
    response = client.get("/not-a-rust-route")
    assert response.status_code == 404
    assert response.json() == {"error": "not found"}


def test_method_not_allowed_matches_axum_body_and_allow_header(client):
    single_method = client.put("/health")
    merged_methods = client.put(f"/sync/{SYNC_KEY}")

    assert single_method.status_code == merged_methods.status_code == 405
    assert single_method.content == merged_methods.content == b""
    assert single_method.headers["allow"] == "GET,HEAD"
    assert merged_methods.headers["allow"] == "GET,HEAD,POST"


def test_oversized_main_route_matches_tower_response_and_security_headers(client):
    response = client.post(
        "/sync/not-a-key",
        content=b"x" * (5 * 1024 * 1024 + 1),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.content == b"length limit exceeded"
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-xss-protection"] == "1; mode=block"


def test_refresh_mode_deserialization_matches_rust_status_and_content_type(client):
    response = client.post(
        "/tickers/refresh",
        json={"interval": "1D", "mode": "not-a-mode", "key": "refresh-test"},
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text.startswith("Failed to deserialize the JSON body into the target type")


@pytest.mark.parametrize(
    ("secret", "body", "status"),
    [
        (None, {"interval": "1D", "key": ""}, 403),
        ("", {"interval": "1D", "mode": "vn", "key": ""}, 200),
        ("", {"interval": "1D", "mode": "vn"}, 401),
    ],
)
def test_refresh_secret_unset_and_blank_match_rust(tmp_path, secret, body, status):
    settings = replace(
        Settings(),
        database=tmp_path / "db.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
        refresh_secret=secret,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        response = test_client.post("/tickers/refresh", json=body)
    assert response.status_code == status


def test_refresh_secret_environment_distinguishes_unset_from_empty(monkeypatch, tmp_path):
    # Settings.from_env loads this project's .env. Use an empty temporary root
    # so the test verifies process-environment semantics without reading a
    # developer's local secrets.
    monkeypatch.setattr(config_module, "ROOT", tmp_path)
    monkeypatch.delenv("REFRESH_SECRET", raising=False)
    assert Settings.from_env().refresh_secret is None

    monkeypatch.setenv("REFRESH_SECRET", "")

    assert Settings.from_env().refresh_secret == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", 40),
        ("not-a-number", 40),
        (" 40", 40),
        ("4_0", 40),
        ("9223372036854775808", 40),
        ("+12", 12),
        ("-2", -2),
        ("0", 0),
    ],
)
def test_api_max_limit_environment_uses_rust_i64_fallback(monkeypatch, raw, expected):
    monkeypatch.setenv("API_MAX_LIMIT", raw)

    assert Settings.from_env().max_limit == expected


def test_single_ticker_limit_environment_is_ignored_like_rust(monkeypatch):
    monkeypatch.setenv("API_SINGLE_TICKER_MAX_LIMIT", "10")

    assert Settings.from_env().single_limit == 10_000


@pytest.mark.parametrize(
    ("name", "raw", "expected"),
    [
        ("UPLOADS_MAX_BYTES", "", 512 * 1024 * 1024),
        ("UPLOADS_MAX_BYTES", "invalid", 512 * 1024 * 1024),
        ("UPLOADS_MAX_BYTES", "0", 512 * 1024 * 1024),
        ("UPLOADS_MAX_BYTES", "+12", 12),
        ("UPLOADS_MAX_SESSIONS", "-1", 10_000),
        ("UPLOADS_MAX_SESSIONS", "18446744073709551616", 10_000),
        ("UPLOADS_MAX_SESSIONS", "25", 25),
    ],
)
def test_upload_limits_environment_matches_rust_parse_fallback(monkeypatch, name, raw, expected):
    monkeypatch.setenv(name, raw)
    settings = Settings.from_env()
    attr = "uploads_max_bytes" if name == "UPLOADS_MAX_BYTES" else "uploads_max_sessions"

    assert getattr(settings, attr) == expected


@pytest.mark.parametrize(
    ("name", "raw", "expected"),
    [
        ("REDIS_DAILY_MAX_SIZE", "", 5_000),
        ("REDIS_HOURLY_MAX_SIZE", "invalid", 30_000),
        ("REDIS_MINUTE_MAX_SIZE", "0", 0),
        ("REDIS_DAILY_MAX_SIZE", "+12", 12),
        ("REDIS_MINUTE_MAX_SIZE", "18446744073709551616", 20_000),
    ],
)
def test_redis_retention_environment_matches_rust_parse_fallback(monkeypatch, name, raw, expected):
    monkeypatch.setenv(name, raw)
    settings = Settings.from_env()
    attr = {
        "REDIS_DAILY_MAX_SIZE": "redis_daily_max_size",
        "REDIS_HOURLY_MAX_SIZE": "redis_hourly_max_size",
        "REDIS_MINUTE_MAX_SIZE": "redis_minute_max_size",
    }[name]

    assert getattr(settings, attr) == expected


def test_empty_uploads_path_environment_is_preserved(monkeypatch):
    monkeypatch.setenv("UPLOADS_PATH", "")

    assert Settings.from_env().uploads_dir == ""


def test_sync_token_environment_uses_rust_whitespace_trim(monkeypatch):
    monkeypatch.setenv("SYNC_TOKEN", "\x1csecret, secret\x1c")

    assert Settings.from_env().sync_tokens == ("\x1csecret", "secret\x1c")


@pytest.mark.skipif(
    not hasattr(os, "environb"), reason="byte environment is only available on Unix"
)
def test_non_utf8_auth_secrets_match_rust_unset_behavior(monkeypatch):
    monkeypatch.setitem(os.environb, b"SYNC_TOKEN", b"\xff")
    monkeypatch.setitem(os.environb, b"REFRESH_SECRET", b"\xff")
    monkeypatch.setitem(os.environb, b"REDIS_URL", b"redis://127.0.0.1:6379/0#\xff")

    settings = Settings.from_env()

    assert settings.sync_tokens == ()
    assert settings.refresh_secret is None
    assert settings.redis_url is None


def test_empty_static_file_zero_open_range_matches_tower(tmp_path):
    public_dir = tmp_path / "public"
    public_dir.mkdir()
    (public_dir / "empty.txt").touch()
    settings = replace(
        Settings(),
        database=tmp_path / "empty-static.sqlite3",
        cache_dir=tmp_path / "empty-static-cache",
        object_dir=tmp_path / "empty-static-objects",
        public_dir=public_dir,
        archive_backend="filesystem",
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        response = test_client.get(
            "/public/empty.txt",
            headers={"accept-encoding": "identity", "range": "bytes=0-"},
        )

    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 0-0/0"
    assert response.headers["content-length"] == "0"
    assert response.content == b""


def test_refresh_json_rejections_match_rust_status_and_content_type(client):
    malformed_json = client.post(
        "/tickers/refresh",
        content=b"{",
        headers={"content-type": "application/json"},
    )
    wrong_content_type = client.post(
        "/tickers/refresh",
        content=b'{"interval":"1D"}',
        headers={"content-type": "text/plain"},
    )

    assert malformed_json.status_code == 400
    assert malformed_json.headers["content-type"] == "text/plain; charset=utf-8"
    assert malformed_json.text.startswith("Failed to parse the request body as JSON")
    assert wrong_content_type.status_code == 415
    assert wrong_content_type.headers["content-type"] == "text/plain; charset=utf-8"
    assert wrong_content_type.text == "Expected request with `Content-Type: application/json`"


@pytest.mark.parametrize(
    "path",
    ["/tickers/refresh", "/sync/550e8400-e29b-41d4-a716-446655440000"],
)
def test_json_extractor_rejections_match_rust_on_stateful_posts(client, path):
    wrong_content_type = client.post(path, content=b"{}", headers={"content-type": "text/plain"})
    malformed_json = client.post(path, content=b"{", headers={"content-type": "application/json"})
    invalid_utf8 = client.post(path, content=b"\xff", headers={"content-type": "application/json"})
    duplicate_field = client.post(
        path,
        content=(
            b'{"interval":"1D","interval":"1h"}'
            if path == "/tickers/refresh"
            else b'{"secret":"s","secret":"x","value":{}}'
        ),
        headers={
            "content-type": "application/json",
            "authorization": "Bearer test-token",
        },
    )
    serde_data_error_before_later_syntax = client.post(
        path,
        content=(
            b'{"interval":"1D","interval":"1h",'
            if path == "/tickers/refresh"
            else b'{"secret":"s","secret":"x",'
        ),
        headers={"content-type": "application/json"},
    )
    serde_type_error_before_later_syntax = client.post(
        path,
        content=(b'{"interval":1,' if path == "/tickers/refresh" else b'{"secret":1,'),
        headers={"content-type": "application/json"},
    )

    assert wrong_content_type.status_code == 415
    assert wrong_content_type.text == "Expected request with `Content-Type: application/json`"
    assert malformed_json.status_code == 400
    assert malformed_json.text == (
        "Failed to parse the request body as JSON: EOF while parsing an object at line 1 column 1"
    )
    assert invalid_utf8.status_code == 400
    assert invalid_utf8.text == (
        "Failed to parse the request body as JSON: expected value at line 1 column 1"
    )
    assert duplicate_field.status_code == 422
    duplicate_name = "interval" if path == "/tickers/refresh" else "secret"
    duplicate_column = 27 if path == "/tickers/refresh" else 22
    assert duplicate_field.text == (
        "Failed to deserialize the JSON body into the target type: "
        f"duplicate field `{duplicate_name}` at line 1 column {duplicate_column}"
    )
    assert serde_data_error_before_later_syntax.status_code == 422
    assert serde_data_error_before_later_syntax.text == duplicate_field.text
    assert serde_type_error_before_later_syntax.status_code == 422
    type_field = "interval" if path == "/tickers/refresh" else "secret"
    type_column = 13 if path == "/tickers/refresh" else 11
    assert serde_type_error_before_later_syntax.text == (
        "Failed to deserialize the JSON body into the target type: "
        f"{type_field}: invalid type: integer `1`, expected a string "
        f"at line 1 column {type_column}"
    )


def test_json_extractor_accepts_json_suffix_media_types_and_nested_duplicate_value_keys(client):
    response = client.post(
        "/sync/550e8400-e29b-41d4-a716-446655440000",
        content=b'{"secret":"s","value":{"item":1,"item":2}}',
        headers={"content-type": "application/problem+json", "authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.json()["value"] == {"item": 2}

    surrogate_pair = client.post(
        "/sync/550e8400-e29b-41d4-a716-446655440001",
        content=b'{"secret":"s","value":"\\ud83d\\ude00"}',
        headers={"content-type": "application/json", "authorization": "Bearer test-token"},
    )
    assert surrogate_pair.status_code == 200
    assert surrogate_pair.json()["value"] == "😀"


def test_sync_json_rejects_serde_number_unicode_and_depth_limits(client):
    headers = {"content-type": "application/json", "authorization": "Bearer test-token"}
    too_large_number = client.post(
        "/sync/550e8400-e29b-41d4-a716-446655440002",
        content=b'{"secret":"s","value":1e400}',
        headers=headers,
    )
    unpaired_surrogate = client.post(
        "/sync/550e8400-e29b-41d4-a716-446655440003",
        content=b'{"secret":"s","value":"\\ud800"}',
        headers=headers,
    )
    malformed_number = client.post(
        "/sync/550e8400-e29b-41d4-a716-446655440006",
        content=b'{"secret":1e,',
        headers=headers,
    )
    deep_value = b'{"secret":"s","value":' + b"[" * 130 + b"0" + b"]" * 130 + b"}"
    excessive_depth = client.post(
        "/sync/550e8400-e29b-41d4-a716-446655440004",
        content=deep_value,
        headers=headers,
    )

    assert too_large_number.status_code == 400
    assert too_large_number.text == (
        "Failed to parse the request body as JSON: value: number out of range at line 1 column 27"
    )
    assert unpaired_surrogate.status_code == 400
    assert unpaired_surrogate.text == (
        "Failed to parse the request body as JSON: "
        "value: unexpected end of hex escape at line 1 column 30"
    )
    assert malformed_number.status_code == 400
    assert malformed_number.text == (
        "Failed to parse the request body as JSON: secret: invalid number at line 1 column 13"
    )
    assert excessive_depth.status_code == 400
    assert excessive_depth.text.startswith("Failed to parse the request body as JSON: value[0][0]")
    assert excessive_depth.text.endswith("recursion limit exceeded at line 1 column 149")


def test_serde_ignored_json_fields_do_not_validate_values_python_would_reject(client):
    headers = {"content-type": "application/json", "authorization": "Bearer test-token"}
    too_deep = b"[" * 140 + b"0" + b"]" * 140
    cases = (
        b'{"interval":"1D","key":"wrong","extra":1e400}',
        b'{"interval":"1D","key":"wrong","extra":' + too_deep + b"}",
        b'{"interval":"1D","key":"wrong","extra":"\xff"}',
    )

    for body in cases:
        response = client.post(
            "/tickers/refresh", content=body, headers={"content-type": "application/json"}
        )
        assert response.status_code == 401

    sync = client.post(
        "/sync/550e8400-e29b-41d4-a716-446655440007",
        content=b'{"secret":"s","value":{},"extra":"\xff"}',
        headers=headers,
    )
    assert sync.status_code == 200
    assert sync.json()["value"] == {}


def test_serde_json_integer_range_and_depth_boundaries(client):
    headers = {"content-type": "application/json", "authorization": "Bearer test-token"}
    too_large_int = b"9" * 401
    overflow_value = client.post(
        "/sync/550e8400-e29b-41d4-a716-446655440008",
        content=b'{"secret":"s","value":' + too_large_int + b"}",
        headers=headers,
    )
    overflow_secret = client.post(
        "/sync/550e8400-e29b-41d4-a716-446655440009",
        content=b'{"secret":' + too_large_int + b',"value":{}}',
        headers=headers,
    )
    rounded_integer = client.post(
        "/sync/550e8400-e29b-41d4-a716-446655440010",
        content=b'{"secret":"s","value":18446744073709551616}',
        headers=headers,
    )
    depth_127 = b'{"secret":"s","value":' + b"[" * 126 + b"0" + b"]" * 126 + b"}"
    depth_128 = b'{"secret":"s","value":' + b"[" * 127 + b"0" + b"]" * 127 + b"}"
    accepted_depth = client.post(
        "/sync/550e8400-e29b-41d4-a716-446655440011", content=depth_127, headers=headers
    )
    rejected_depth = client.post(
        "/sync/550e8400-e29b-41d4-a716-446655440012", content=depth_128, headers=headers
    )

    for response in (overflow_value, overflow_secret, rejected_depth):
        assert response.status_code == 400
        assert response.text.startswith("Failed to parse the request body as JSON: ")
    assert rounded_integer.status_code == 200
    assert isinstance(rounded_integer.json()["value"], float)
    assert accepted_depth.status_code == 200


def test_json_extractor_matches_serde_struct_sequence_and_error_order(client):
    headers = {"content-type": "application/json", "authorization": "Bearer test-token"}
    base = "/sync/550e8400-e29b-41d4-a716-446655440013"
    sequence = client.post(base, content=b'["s",{}]', headers=headers)
    trailing_sequence = client.post(base, content=b'["s",{}]x', headers=headers)
    incomplete_sequence = client.post(base, content=b"[]", headers=headers)
    incomplete_with_trailing = client.post(base, content=b"[]x", headers=headers)
    wrong_root_with_trailing = client.post(base, content=b"null x", headers=headers)
    wrong_number_root_with_trailing = client.post(base, content=b"42 x", headers=headers)

    assert sequence.status_code == 200
    assert sequence.json()["value"] == {}
    assert trailing_sequence.status_code == 400
    assert incomplete_sequence.status_code == 422
    assert incomplete_sequence.text == (
        "Failed to deserialize the JSON body into the target type: invalid length 0, "
        "expected struct SyncPostBody with 2 elements at line 1 column 2"
    )
    for response in (
        incomplete_with_trailing,
        wrong_root_with_trailing,
        wrong_number_root_with_trailing,
    ):
        assert response.status_code == 422
        assert response.text.startswith(
            "Failed to deserialize the JSON body into the target type: "
        )


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json ",
        "application/json ; charset=utf-8",
        "application/json; charset =utf-8",
        "application/json; charset= utf-8",
        "application/json;\tcharset=utf-8",
        "application/json;charset=utf-8 ",
        "application/json;;",
        "application/json;charset=utf-8;;",
    ],
)
def test_json_media_type_rejects_invalid_parameter_whitespace(client, content_type):
    response = client.post(
        "/tickers/refresh",
        content=b'{"interval":"1D","key":"wrong"}',
        headers={"content-type": content_type},
    )

    assert response.status_code == 415
    assert response.text == "Expected request with `Content-Type: application/json`"


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json;",
        "application/json; ",
        "application/json;charset=utf-8;",
        'application/json; charset="utf-8" ',
        "application/json;charset=utf-8; ",
    ],
)
def test_json_media_type_accepts_mime_final_empty_parameter(client, content_type):
    response = client.post(
        "/tickers/refresh",
        content=b'{"interval":"1D","key":"wrong"}',
        headers={"content-type": content_type},
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("content_type", "accepted"),
    [
        (b"application/json;x=", True),
        (b'application/json;x="a\\b"', True),
        (b'application/json;x=" "', True),
        (b"application/foo++json", True),
        (b'application/json;x=""', False),
        (b'application/json;x="a\tb"', False),
        (b'application/json;x="a\x7fb"', False),
        (b"application/json;x=;", False),
        (b"application/json;;", False),
        (b"application/+json", False),
    ],
)
def test_rust_json_media_type_parser_edge_cases(content_type, accepted):
    assert app_module._rust_json_content_type_matches(content_type) is accepted


def test_rust_header_value_to_str_rejects_non_ascii_bytes():
    request = SimpleNamespace(scope={"headers": [(b"authorization", b"Bearer \xc3\xa9")]})

    assert app_module._rust_header_value_to_str(request, b"authorization") is None


def test_chunked_wrong_media_type_is_rejected_before_body_read(client):
    chunks = iter((b"x" * (5 * 1024 * 1024 + 1),))
    response = client.post(
        "/sync/550e8400-e29b-41d4-a716-446655440005",
        content=chunks,
        headers={"content-type": "text/plain"},
    )

    assert response.status_code == 415
    assert response.text == "Expected request with `Content-Type: application/json`"


def test_refresh_database_failure_matches_rust_error(client, monkeypatch):
    def fail(*_args):
        raise RuntimeError("local database detail")

    monkeypatch.setattr(client.app_instance.state.repo, "refresh", fail)
    response = client.post(
        "/tickers/refresh", json={"interval": "1D", "mode": "vn", "key": "refresh-test"}
    )

    assert response.status_code == 500
    assert response.json() == {"error": "Database error for source vn"}
    assert "local database detail" not in response.text


def test_refresh_all_updates_sources_in_rust_order(client):
    repo = client.app_instance.state.repo
    repo.put(
        [
            Candle("crypto", "BTCUSDT", "1D", parse_time("2024-01-01"), 100, 101, 99, 100, 1),
            Candle("yahoo", "AAPL", "1D", parse_time("2024-01-01"), 100, 101, 99, 100, 1),
        ]
    )

    response = client.post(
        "/tickers/refresh", json={"interval": "1D", "mode": "all", "key": "refresh-test"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "updated": 5,
        "interval": "next_1d",
        "sources": ["vn", "crypto", "yahoo"],
    }


def test_refresh_all_uses_a_source_local_due_time(client, monkeypatch):
    from aipriceaction_api import storage

    repo = client.app_instance.state.repo
    repo.put(
        [
            Candle("crypto", "BTCUSDT", "1D", parse_time("2024-01-01"), 100, 101, 99, 100, 1),
            Candle("yahoo", "AAPL", "1D", parse_time("2024-01-01"), 100, 101, 99, 100, 1),
        ]
    )
    timestamps = iter((1_800_000_000, 1_800_000_001, 1_800_000_002))
    monkeypatch.setattr(storage, "time", SimpleNamespace(time=lambda: next(timestamps)))

    result = repo.refresh(("vn", "crypto", "yahoo"), "1D")

    assert result == {
        "updated": 5,
        "interval": "next_1d",
        "sources": ["vn", "crypto", "yahoo"],
    }
    with repo.connect() as con:
        schedules = {
            row["symbol"]: row["next_1d"]
            for row in con.execute(
                "SELECT symbol,next_1d FROM tickers WHERE symbol IN ('FPT','BTCUSDT','AAPL')"
            )
        }
    assert schedules == {"FPT": 1_800_000_000, "BTCUSDT": 1_800_000_001, "AAPL": 1_800_000_002}


def test_refresh_all_identifies_failing_source_and_keeps_prior_source_updates(client):
    repo = client.app_instance.state.repo
    repo.put(
        [
            Candle("crypto", "BTCUSDT", "1D", parse_time("2024-01-01"), 100, 101, 99, 100, 1),
            Candle("yahoo", "AAPL", "1D", parse_time("2024-01-01"), 100, 101, 99, 100, 1),
        ]
    )
    with repo.connect() as con:
        con.execute(
            """CREATE TRIGGER fail_crypto_refresh
            BEFORE UPDATE OF next_1d ON tickers
            WHEN OLD.source='crypto'
            BEGIN SELECT RAISE(ABORT, 'synthetic source failure'); END"""
        )

    response = client.post(
        "/tickers/refresh", json={"interval": "1D", "mode": "all", "key": "refresh-test"}
    )

    assert response.status_code == 500
    assert response.json() == {"error": "Database error for source crypto"}
    with repo.connect() as con:
        schedules = {}
        for row in con.execute(
            """SELECT t.source,t.symbol,t.next_1d FROM tickers t
            WHERE EXISTS (
              SELECT 1 FROM series s WHERE s.source=t.source AND s.symbol=t.symbol
              AND s.status='ready'
            )"""
        ):
            schedules.setdefault(row["source"], {})[row["symbol"]] = row["next_1d"]
    assert {"vn", "crypto", "yahoo"} <= schedules.keys()
    assert schedules["vn"] and all(value > 0 for value in schedules["vn"].values())
    assert schedules["crypto"] == {"BTCUSDT": 0}
    assert schedules["yahoo"] == {"AAPL": 0}


def test_refresh_all_unknown_database_error_does_not_mislabel_source(client, monkeypatch):
    def fail(*_args):
        raise RuntimeError("unattributed database detail")

    monkeypatch.setattr(client.app_instance.state.repo, "refresh", fail)
    response = client.post(
        "/tickers/refresh", json={"interval": "1D", "mode": "all", "key": "refresh-test"}
    )

    assert response.status_code == 500
    assert response.json() == {"error": "Database error for source all"}
    assert "unattributed database detail" not in response.text


def _active_rust_router_source(source):
    """Blank Rust comments and unrelated string bodies, preserving code offsets."""
    output = list(source)

    def blank(start, end):
        for position in range(start, end):
            if source[position] != "\n":
                output[position] = " "

    position = 0
    while position < len(source):
        if source.startswith("//", position):
            end = source.find("\n", position)
            end = len(source) if end < 0 else end
            blank(position, end)
            position = end
            continue
        if source.startswith("/*", position):
            depth = 1
            end = position + 2
            while end < len(source) and depth:
                if source.startswith("/*", end):
                    depth += 1
                    end += 2
                elif source.startswith("*/", end):
                    depth -= 1
                    end += 2
                else:
                    end += 1
            blank(position, end)
            position = end
            continue

        raw_start = re.match(r"(?:br|r)(#+)\"", source[position:])
        if raw_start:
            terminator = f'"{raw_start.group(1)}'
            end = source.find(terminator, position + raw_start.end())
            end = len(source) if end < 0 else end + len(terminator)
            blank(position, end)
            position = end
            continue

        if source[position] != '"':
            position += 1
            continue

        end = position + 1
        while end < len(source):
            if source[end] == "\\":
                end += 2
            elif source[end] == '"':
                end += 1
                break
            else:
                end += 1
        prefix = "".join(output[:position])
        is_router_path = re.search(r"\.(?:route|nest|nest_service|route_service)\s*\(\s*$", prefix)
        if not is_router_path:
            blank(position, end)
        position = end

    return "".join(output)


def _rust_delimiter_depth_at(source, target):
    """Return Rust's surrounding (), [] and {} depth while skipping strings."""
    depth = [0, 0, 0]
    matching = {
        ")": (0, -1),
        "]": (1, -1),
        "}": (2, -1),
    }
    position = 0
    while position < target:
        character = source[position]
        if character == '"':
            position += 1
            while position < target:
                if source[position] == "\\":
                    position += 2
                elif source[position] == '"':
                    position += 1
                    break
                else:
                    position += 1
            continue
        if character == "'":
            # Skip Rust character literals but leave lifetime names such as
            # 'static untouched. A literal brace must not change nesting depth.
            end = position + 1
            if end < target and source[end] == "\\":
                end += 1
                if (
                    end < target
                    and source[end] == "u"
                    and end + 1 < target
                    and source[end + 1] == "{"
                ):
                    brace_end = source.find("}", end + 2, target)
                    if brace_end >= 0:
                        end = brace_end + 1
                elif end < target and source[end] == "x":
                    end += 3
                else:
                    end += 1
            else:
                end += 1
            if end < target and source[end] == "'":
                position = end + 1
                continue
        if character in "([{":
            depth["([{".index(character)] += 1
        elif character in matching:
            index, amount = matching[character]
            depth[index] += amount
        position += 1
    return tuple(depth)


def test_rust_route_scanner_ignores_comments_and_string_literals():
    source = r"""
        // .route("/comment", axum::routing::get(handler))
        let documentation = ".route(\\\"/string\\\", axum::routing::get(handler))";
        router.route("/health", axum::routing::get(health));
    """
    active = _active_rust_router_source(source)
    declarations = re.findall(
        r'\.route\s*\(\s*"([^"]+)"\s*,\s*axum::routing::(get|post|put|patch|delete)\b',
        active,
    )
    assert declarations == [("/health", "get")]
    assert ".head(" in _active_rust_router_source('router.route("/x", get(x).head(y));')
    chained_head_service = _active_rust_router_source(
        'router.route("/x", axum::routing::get(x).on_service('
        "axum::routing::MethodFilter::HEAD, head_service));"
    )
    assert re.findall(r"\.on_service\s*\(", chained_head_service)
    service_methods = (
        "get_service",
        "head_service",
        "post_service",
        "put_service",
        "patch_service",
        "delete_service",
        "options_service",
        "trace_service",
        "connect_service",
        "on_service",
        "method_not_allowed_fallback_service",
    )
    service_chain = _active_rust_router_source(
        'router.route("/x", axum::routing::get(x)'
        + "".join(f".{method}(service)" for method in service_methods)
        + ");"
    )
    assert re.findall(r"\.([a-z_]+_service)\s*\(", service_chain) == list(service_methods)

    nested_route_source = _active_rust_router_source(
        'let main_routes = Router::new().route("/health", get(health));'
        "{ let _brace = '}'; let _dead = Router::new().route(\"/dead\", get(dead)); };"
    )
    nested_match = re.search(r'\.route\s*\(\s*"/dead"', nested_route_source)
    active_match = re.search(r'\.route\s*\(\s*"/health"', nested_route_source)
    assert active_match is not None and nested_match is not None
    assert _rust_delimiter_depth_at(nested_route_source, active_match.start()) == (0, 0, 0)
    assert _rust_delimiter_depth_at(nested_route_source, nested_match.start()) == (0, 0, 1)

    discarded_router_source = (
        'let main_routes = Router::new().route("/unmounted", get(handler)); '
        'let _unused = Router::new().route("/health", get(health));'
    )
    assert discarded_router_source.count("Router::new()") == 2


def test_fastapi_exposes_every_active_legacy_http_route(client):
    from starlette.routing import Mount

    routes = {
        (method.lower(), path)
        for path, methods in client.app_instance.openapi()["paths"].items()
        for method in methods
    }
    expected_routes = {
        ("get", "/tickers"),
        ("get", "/health"),
        ("get", "/tickers/group"),
        ("get", "/tickers/name"),
        ("get", "/tickers/info"),
        ("post", "/upload/markdown"),
        ("get", "/uploads/{session_id}/markdown/{filename}"),
        ("delete", "/uploads/{session_id}"),
        ("post", "/tickers/refresh"),
        ("get", "/sync/{key}"),
        ("post", "/sync/{key}"),
        ("get", "/analysis/top-performers"),
        ("get", "/analysis/ma-scores-by-sector"),
        ("get", "/analysis/volume-profile"),
        ("get", "/analysis/rrg"),
    }
    assert routes == expected_routes

    def flattened_routes(route_list, prefix="", router=None):
        if router is not None:
            assert not getattr(router, "_low_priority_routes", ()), (
                "FastAPI low-priority frontend routes are not part of the Rust API manifest"
            )
        for route in route_list:
            nested_router = getattr(route, "original_router", None)
            if nested_router is None:
                path = getattr(route, "path", None)
                path = path.replace("{key:path}", "{key}") if path is not None else None
                yield route, f"{prefix}{path}" if path is not None else None
            else:
                context = getattr(route, "include_context", None)
                nested_prefix = getattr(context, "prefix", "")
                yield from flattened_routes(
                    nested_router.routes,
                    f"{prefix}{nested_prefix}",
                    nested_router,
                )

    all_routes = list(
        flattened_routes(
            client.app_instance.router.routes,
            router=client.app_instance.router,
        )
    )
    registered_api_routes = Counter(
        (method.lower(), path)
        for route, path in all_routes
        if path is not None
        for method in (getattr(route, "methods", None) or ())
    )
    expected_registered_api_routes = expected_routes - {
        ("get", "/uploads/{session_id}/markdown/{filename}")
    } | {
        ("get", "/uploads/{session_id:path}/markdown/{filename:path}"),
        ("get", "/explorer"),
        ("get", "/public"),
        ("head", "/public"),
    }
    assert registered_api_routes == Counter(expected_registered_api_routes)
    mounts = [(route, path) for route, path in all_routes if isinstance(route, Mount)]
    assert len(mounts) == 1
    assert mounts[0][0].path == "/public"
    assert mounts[0][1] == "/public"
    assert mounts[0][0].name == "public"
    assert type(mounts[0][0].app) is RustCompatibleStaticFiles
    assert Path(mounts[0][0].app.directory) == client.app_instance.state.settings.public_dir
    public_root_handlers = [
        route
        for route, path in all_routes
        if path == "/public" and getattr(route, "name", None) == "public_root"
    ]
    assert len(public_root_handlers) == 1

    rust_root = (
        Path(__file__).resolve().parents[2]
        / "legacy"
        / "rust"
        / "aipriceaction"
        / "src"
        / "server"
    )
    rust_router = _active_rust_router_source((rust_root / "mod.rs").read_text(encoding="utf-8"))
    # A chained MethodRouter can register explicit HEAD behavior without a
    # separate route declaration, so include these methods in the inventory.
    rust_call_pattern = re.compile(
        r"\.(route|nest_service|route_service|nest|merge|fallback|fallback_service|head|on|on_service|post|put|patch|delete|options|trace|connect|method_not_allowed_fallback|(?:get|head|post|put|patch|delete|options|trace|connect|method_not_allowed_fallback)_service)\s*\("
    )
    rust_route_pattern = re.compile(
        r'\.route\s*\(\s*"([^"]+)"\s*,\s*axum::routing::(get|post|put|patch|delete)\b'
    )
    rust_calls = list(rust_call_pattern.finditer(rust_router))
    rust_route_calls = [match for match in rust_calls if match.group(1) == "route"]
    assert len(rust_route_pattern.findall(rust_router)) == len(rust_route_calls)

    def collect_routes(block, prefix=""):
        return [(method, prefix + path) for path, method in rust_route_pattern.findall(block)]

    main_start = rust_router.index("let main_routes =")
    upload_start = rust_router.index("let upload_routes =", main_start)
    public_start = rust_router.index("let public_routes =", upload_start)
    router_start = rust_router.index("let router =", public_start)
    analysis_start = rust_router.index("fn analysis_routes()")
    analysis_end = rust_router.index("\n}", analysis_start) + 2
    analysis_body_start = rust_router.index("{", analysis_start) + 1
    rust_route_blocks = (
        (main_start, upload_start, "", _rust_delimiter_depth_at(rust_router, main_start)),
        (upload_start, public_start, "", _rust_delimiter_depth_at(rust_router, upload_start)),
        (public_start, router_start, "", _rust_delimiter_depth_at(rust_router, public_start)),
        (
            analysis_start,
            analysis_end,
            "/analysis",
            _rust_delimiter_depth_at(rust_router, analysis_body_start),
        ),
    )
    active_rust_route_calls = [
        match
        for match in rust_route_calls
        if any(
            start <= match.start() < end
            and _rust_delimiter_depth_at(rust_router, match.start()) == route_depth
            for start, end, _prefix, route_depth in rust_route_blocks
        )
    ]
    assert len(active_rust_route_calls) == len(rust_route_calls)
    rust_route_declarations = [
        declaration
        for start, end, prefix, _route_depth in rust_route_blocks
        for declaration in collect_routes(rust_router[start:end], prefix)
    ]
    assert Counter(rust_route_declarations) == Counter(expected_routes | {("get", "/explorer")})

    main_routes_block = rust_router[main_start:upload_start]
    upload_routes_block = rust_router[upload_start:public_start]
    public_routes_block = rust_router[public_start:router_start]
    router_block = rust_router[router_start:analysis_start]
    analysis_routes_block = rust_router[analysis_start:analysis_end]
    for binding_block, expected_semicolon_count in (
        (main_routes_block, 1),
        # The public_dir declaration sits after upload_routes and before
        # public_routes, so this lexical slice includes its terminator too.
        (upload_routes_block, 2),
        (public_routes_block, 1),
    ):
        # These declarations are each one Router::new() method chain. Extra
        # discarded router statements in the lexical block must not satisfy
        # the route manifest while the bound router stays incomplete.
        assert len(re.findall(r"\b(?:axum::)?Router::new\s*\(\s*\)", binding_block)) == 1
        assert binding_block.count(";") == expected_semicolon_count
    assert len(re.findall(r"\b(?:axum::)?Router::new\s*\(\s*\)", analysis_routes_block)) == 1
    assert ";" not in analysis_routes_block
    assert re.findall(
        r'\.nest\s*\(\s*"([^"]+)"\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*\)',
        main_routes_block,
    ) == [("/analysis", "analysis_routes")]
    assert len([match for match in rust_calls if match.group(1) == "nest"]) == 1
    public_service_calls = re.findall(
        r'\.nest_service\s*\(\s*"([^"]+)"\s*,\s*ServeDir::new\(public_dir\)\.precompressed_br\(\)\s*\)',
        public_routes_block,
    )
    assert public_service_calls == ["/public"]
    assert len([match for match in rust_calls if match.group(1) == "nest_service"]) == 1
    assert not [
        match
        for match in rust_calls
        if match.group(1)
        in {
            "route_service",
            "head",
            "on",
            "on_service",
            "post",
            "put",
            "patch",
            "delete",
            "options",
            "trace",
            "connect",
            "method_not_allowed_fallback",
            "get_service",
            "head_service",
            "post_service",
            "put_service",
            "patch_service",
            "delete_service",
            "options_service",
            "trace_service",
            "connect_service",
            "method_not_allowed_fallback_service",
        }
    ]
    assert not re.search(
        r"axum::routing::(?:get|post|put|patch|delete)\s*\([^;]*?\)\s*\.\s*"
        r"(?:get|post|put|patch|delete|head|options|trace|connect|on|on_service|"
        r"(?:get|head|post|put|patch|delete|options|trace|connect|"
        r"method_not_allowed_fallback)_service|method_not_allowed_fallback)\s*\(",
        rust_router,
    )

    merge_calls = [match for match in rust_calls if match.group(1) == "merge"]
    assert all(router_start <= match.start() < analysis_start for match in merge_calls)
    router_expression = router_block[: router_block.index(".with_state(state)")]
    assert len(re.findall(r"\b(?:axum::)?Router::new\s*\(\s*\)", router_expression)) == 1
    assert ";" not in router_expression
    assert re.findall(r"\.merge\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", router_expression) == [
        "main_routes",
        "upload_routes",
        "public_routes",
    ]
    assert len(merge_calls) == 3

    fallback_calls = [match for match in rust_calls if match.group(1) == "fallback"]
    assert len(fallback_calls) == 1
    assert main_start <= fallback_calls[0].start() < upload_start
    assert re.findall(r"\.fallback\s*\(\s*api::not_found_handler\s*\)", main_routes_block) == [
        ".fallback(api::not_found_handler)"
    ]
    assert not [match for match in rust_calls if match.group(1) == "fallback_service"]

    explorer = next(route for route, path in all_routes if path == "/explorer")
    assert explorer.methods == {"GET"}


def test_fastapi_documentation_routes_are_not_public(client):
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        for method in ("GET", "HEAD", "POST"):
            response = client.request(method, path, json={} if method == "POST" else None)
            assert response.status_code == 404
            if method == "HEAD":
                # HTTP clients suppress HEAD bodies, so this checks the
                # client-visible response rather than the internal ASGI body.
                assert response.content == b""
            else:
                assert response.json() == {"error": "not found"}


def test_explorer_and_public_assets_match_rust_bundle(client):
    workspace = Path(__file__).resolve().parents[2]
    rust_public = workspace / "legacy" / "rust" / "aipriceaction" / "public"
    python_public = workspace / "aipriceaction-api" / "src" / "aipriceaction_api" / "public"
    rust_files = sorted(
        path.relative_to(rust_public)
        for path in rust_public.rglob("*")
        if path.is_file()
    )
    python_files = sorted(
        path.relative_to(python_public)
        for path in python_public.rglob("*")
        if path.is_file()
    )

    assert python_files == rust_files
    for relative_path in rust_files:
        rust_bytes = (rust_public / relative_path).read_bytes()
        python_bytes = (python_public / relative_path).read_bytes()
        assert python_bytes == rust_bytes

        route = (
            "/explorer"
            if relative_path == Path("index.html")
            else f"/public/{relative_path.as_posix()}"
        )
        response = client.get(route)
        assert response.status_code == 200
        assert response.content == rust_bytes


def test_openapi_documents_mode_algorithm_and_integer_query_bounds(client):
    openapi_document = client.app_instance.openapi()
    paths = openapi_document["paths"]

    def parameters(path):
        return {parameter["name"]: parameter for parameter in paths[path]["get"]["parameters"]}

    mode_values = ["vn", "stock", "stocks", "crypto", "cryptos", "yahoo", "all"]
    assert parameters("/tickers")["mode"]["schema"]["enum"] == mode_values
    assert parameters("/tickers/group")["mode"]["schema"]["enum"] == mode_values
    assert parameters("/analysis/rrg")["mode"]["schema"]["enum"] == mode_values
    assert parameters("/analysis/rrg")["algorithm"]["schema"]["enum"] == ["jdk", "mascore"]
    profile_mode = parameters("/analysis/volume-profile")["mode"]
    assert "enum" not in profile_mode["schema"]
    assert "fall back to vn" in profile_mode["description"]

    for path in (
        "/tickers",
        "/analysis/top-performers",
        "/analysis/ma-scores-by-sector",
        "/analysis/rrg",
    ):
        assert "Rust-compatible Redis response snapshots" in parameters(path)["snap"]["description"]
        assert "REDIS_URL" in parameters(path)["snap"]["description"]

    unsigned_limit = parameters("/analysis/top-performers")["limit"]["schema"]
    assert unsigned_limit["minimum"] == 0
    assert unsigned_limit["maximum"] == (1 << 64) - 1

    signed_limit = parameters("/tickers")["limit"]["schema"]
    assert signed_limit["minimum"] == -(1 << 63)
    assert signed_limit["maximum"] == (1 << 63) - 1


def test_openapi_query_parameter_names_match_rust_query_field_manifest(client):
    paths = client.app_instance.openapi()["paths"]
    for path, rust_fields in RUST_QUERY_FIELDS.items():
        documented = {
            parameter["name"]
            for parameter in paths[path]["get"]["parameters"]
            if parameter["in"] == "query"
        }
        assert documented == set(rust_fields), path


def test_python_query_field_manifest_matches_rust_query_structs(client):
    workspace = Path(__file__).resolve().parents[2]
    rust_root = workspace / "legacy" / "rust" / "aipriceaction" / "src" / "server"
    query_routes = {
        ("/tickers", "get"): ("api/mod.rs", "tickers", "TickersQuery"),
        ("/tickers/group", "get"): ("api/mod.rs", "tickers_group", "GroupQuery"),
        ("/tickers/name", "get"): ("api/mod.rs", "tickers_name", "GroupQuery"),
        ("/tickers/info", "get"): ("api/mod.rs", "tickers_info", "InfoQuery"),
        ("/analysis/top-performers", "get"): (
            "analysis/performers.rs",
            "top_performers_handler",
            "TopPerformersQuery",
        ),
        ("/analysis/ma-scores-by-sector", "get"): (
            "analysis/ma_scores.rs",
            "ma_scores_by_sector_handler",
            "MaScoresBySectorQuery",
        ),
        ("/analysis/volume-profile", "get"): (
            "analysis/volume_profile.rs",
            "volume_profile_handler",
            "VolumeProfileQuery",
        ),
        ("/analysis/rrg", "get"): ("analysis/rrg.rs", "rrg_handler", "RrgQuery"),
        ("/sync/{key}", "get"): ("sync.rs", "sync_get", "SyncGetQuery"),
        (
            "/upload/markdown",
            "post",
        ): ("upload.rs", "upload_markdown_handler", "UploadQuery"),
        ("/uploads/{session_id}/markdown/{filename}", "get"): (
            "upload.rs",
            "serve_markdown_handler",
            "RetrieveQuery",
        ),
        (
            "/uploads/{session_id}",
            "delete",
        ): ("upload.rs", "delete_session_handler", "DeleteQuery"),
    }

    def rust_struct_fields(source_path, struct_name):
        source = (rust_root / source_path).read_text(encoding="utf-8")
        declaration = re.search(rf"\bpub\s+struct\s+{struct_name}\s*\{{", source)
        assert declaration is not None, (source_path, struct_name)
        opening = source.index("{", declaration.start())
        depth = 1
        closing = opening + 1
        while depth and closing < len(source):
            depth += (source[closing] == "{") - (source[closing] == "}")
            closing += 1
        assert depth == 0, (source_path, struct_name)
        body = source[opening + 1 : closing - 1]
        fields = {}
        previous_end = 0
        field_pattern = re.compile(
            r"^\s*pub\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^,\n]+?)\s*,?\s*$",
            re.MULTILINE,
        )
        for field in field_pattern.finditer(body):
            attributes = body[previous_end : field.start()]
            serde_attributes = re.findall(
                r"#\s*\[\s*serde\(([^]]*)\)\s*\]",
                attributes,
                re.DOTALL,
            )
            rename = None
            aliases = []
            has_default = False
            for serde_attribute in serde_attributes:
                rename_match = re.search(r'\brename\s*=\s*"([^"]+)"', serde_attribute)
                if rename_match:
                    rename = rename_match.group(1)
                aliases.extend(re.findall(r'\balias\s*=\s*"([^"]+)"', serde_attribute))
                has_default = has_default or bool(
                    re.search(r"(?:^|,)\s*default(?:\s*=|,|$)", serde_attribute)
                )
            fields[field.group(1)] = {
                "type": re.sub(r"\s+", "", field.group(2)),
                "default": has_default,
                "name": rename or field.group(1),
                "aliases": tuple(aliases),
            }
            previous_end = field.end()
        return fields

    def rust_query_kind(field_type, has_default):
        optional = field_type.startswith("Option<") and field_type.endswith(">")
        base_type = field_type[7:-1] if optional else field_type
        if base_type == "Vec<String>":
            assert optional
            return "list_string"
        if base_type == "String":
            return "optional_string" if optional else "string" if has_default else "required_string"
        if base_type in {"Mode", "RrgAlgorithm"}:
            return "mode" if base_type == "Mode" else "rrg_algorithm"
        if base_type == "bool":
            assert not optional
            return "bool"
        if base_type in {"i64", "u64", "usize", "u32", "f64"}:
            return f"optional_{base_type}" if optional else base_type
        raise AssertionError(f"unmapped Rust query field type: {field_type}")

    def rust_openapi_shape(field_type):
        base_type = field_type[7:-1] if field_type.startswith("Option<") else field_type
        if base_type == "Vec<String>":
            return "array", "string"
        primitive_types = {
            "String": "string",
            "Mode": "string",
            "RrgAlgorithm": "string",
            "bool": "boolean",
            "i64": "integer",
            "u64": "integer",
            "usize": "integer",
            "u32": "integer",
            "f64": "number",
        }
        assert base_type in primitive_types, field_type
        return primitive_types[base_type], None

    def resolve_openapi_schema(schema):
        while "$ref" in schema:
            schema = schema_components[schema["$ref"].rsplit("/", 1)[-1]]
        if "anyOf" in schema:
            variants = [variant for variant in schema["anyOf"] if variant.get("type") != "null"]
            assert len(variants) == 1, schema
            return resolve_openapi_schema(variants[0])
        return schema

    openapi_document = client.app_instance.openapi()
    paths = openapi_document["paths"]
    schema_components = openapi_document.get("components", {}).get("schemas", {})
    mapped_query_structs = {
        struct_name for _source_path, _handler_name, struct_name in query_routes.values()
    }
    rust_query_structs = {}
    for source_path in rust_root.rglob("*.rs"):
        source = source_path.read_text(encoding="utf-8")
        for struct_name in re.findall(r"\bpub\s+struct\s+(\w+Query)\s*\{", source):
            assert struct_name not in rust_query_structs, (
                "duplicate Rust query type name",
                struct_name,
                rust_query_structs.get(struct_name),
                source_path,
            )
            rust_query_structs[struct_name] = source_path
    # RefreshQuery is a JSON body extractor rather than an axum Query<T>.
    assert set(rust_query_structs) == mapped_query_structs | {"RefreshQuery"}

    router_source = _active_rust_router_source(
        (rust_root / "mod.rs").read_text(encoding="utf-8")
    )
    main_start = router_source.index("let main_routes =")
    upload_start = router_source.index("let upload_routes =", main_start)
    public_start = router_source.index("let public_routes =", upload_start)
    analysis_start = router_source.index("fn analysis_routes()")
    analysis_end = router_source.index("\n}", analysis_start) + 2
    main_routes_block = router_source[main_start:upload_start]
    upload_routes_block = router_source[upload_start:public_start]
    analysis_routes_block = router_source[analysis_start:analysis_end]

    # Discover every active route-to-handler-to-Query<T> edge instead of
    # relying only on the hand-maintained query_routes table. This catches a
    # newly added route that reuses an existing query struct.
    active_route_blocks = (
        ("", main_routes_block),
        ("", upload_routes_block),
        ("/analysis", analysis_routes_block),
    )
    route_registration = re.compile(
        r'\.route\(\s*"([^"]+)"\s*,\s*axum::routing::'
        r'(get|post|put|patch|delete)\s*\(\s*'
        r'([A-Za-z_][A-Za-z0-9_]*::[A-Za-z_][A-Za-z0-9_]*)\s*\)\s*,?\s*\)',
        re.DOTALL,
    )
    handler_sources = {}
    for source_path in rust_root.rglob("*.rs"):
        source = source_path.read_text(encoding="utf-8")
        for match in re.finditer(r"\bpub\s+async\s+fn\s+([A-Za-z_]\w*)\s*\(", source):
            handler_sources.setdefault(match.group(1), []).append((source_path, source, match.start()))

    discovered_query_routes = {}
    for route_prefix, routes_block in active_route_blocks:
        for registration in route_registration.finditer(routes_block):
            route_path = route_prefix + registration.group(1)
            method = registration.group(2)
            handler_name = registration.group(3).rsplit("::", 1)[-1]
            declarations = handler_sources.get(handler_name, ())
            assert len(declarations) == 1, (route_path, method, handler_name, len(declarations))
            _source_path, handler_source, handler_start = declarations[0]
            opening = handler_source.index("(", handler_start)
            depth = 1
            closing = opening + 1
            while depth and closing < len(handler_source):
                depth += (handler_source[closing] == "(") - (handler_source[closing] == ")")
                closing += 1
            assert depth == 0, (route_path, method, handler_name)
            signature = handler_source[opening + 1 : closing - 1]
            query_aliases = {"Query", "AxumQuery"}
            query_aliases.update(
                re.findall(r"\bQuery\s+as\s+([A-Za-z_]\w*)", handler_source)
            )
            query_types = []
            for query_alias in query_aliases:
                extractor_start = re.compile(
                    rf"\b{re.escape(query_alias)}\s*<"
                )
                for extractor in extractor_start.finditer(signature):
                    type_start = extractor.end()
                    depth = 1
                    type_end = type_start
                    while depth and type_end < len(signature):
                        depth += (signature[type_end] == "<") - (signature[type_end] == ">")
                        type_end += 1
                    assert depth == 0, (route_path, method, handler_name, query_alias)
                    query_type = signature[type_start : type_end - 1].strip()
                    assert re.fullmatch(
                        r"(?:[A-Za-z_]\w*::)*[A-Za-z_]\w*",
                        query_type,
                    ), (route_path, method, handler_name, query_type)
                    query_types.append(query_type.rsplit("::", 1)[-1])
            assert len(query_types) <= 1, (route_path, method, handler_name, query_types)
            if query_types:
                route_key = (route_path, method)
                assert route_key not in discovered_query_routes, route_key
                discovered_query_routes[route_key] = (handler_name, query_types[0])

    expected_query_routes = {
        route: (handler_name, struct_name)
        for route, (_source_path, handler_name, struct_name) in query_routes.items()
    }
    assert discovered_query_routes == expected_query_routes, (
        discovered_query_routes,
        expected_query_routes,
    )

    for (route_path, method), (source_path, handler_name, struct_name) in query_routes.items():
        handler_source = (rust_root / source_path).read_text(encoding="utf-8")
        handler_match = re.search(rf"\bpub\s+async\s+fn\s+{handler_name}\s*\(", handler_source)
        assert handler_match is not None, (route_path, handler_name)
        opening = handler_source.index("(", handler_match.start())
        depth = 1
        closing = opening + 1
        while depth and closing < len(handler_source):
            depth += (handler_source[closing] == "(") - (handler_source[closing] == ")")
            closing += 1
        assert depth == 0, (route_path, handler_name)
        signature = handler_source[opening + 1 : closing - 1]
        assert re.search(rf"\b(?:AxumQuery|Query)\s*<\s*{struct_name}\s*>", signature), (
            route_path,
            handler_name,
            struct_name,
        )

        route_segment = route_path.removeprefix("/analysis") if route_path.startswith("/analysis/") else route_path
        module = "analysis" if route_path.startswith("/analysis/") else Path(source_path).parts[0].removesuffix(".rs")
        active_routes_block = (
            analysis_routes_block
            if route_path.startswith("/analysis/")
            else upload_routes_block
            if Path(source_path).name == "upload.rs"
            else main_routes_block
        )
        registration = re.compile(
            rf"\.route\(\s*\x22{re.escape(route_segment)}\x22\s*,\s*"
            rf"axum::routing::{method}\({re.escape(module + '::' + handler_name)}\)\s*,?\s*\)"
        )
        assert registration.search(active_routes_block), (route_path, method, handler_name)

        declaration_path = rust_query_structs[struct_name].relative_to(rust_root).as_posix()
        rust_fields = rust_struct_fields(declaration_path, struct_name)
        if route_path in RUST_QUERY_FIELDS:
            assert set(RUST_QUERY_FIELDS[route_path]) == set(rust_fields), route_path
        query_parameters = {
            parameter["name"]: parameter
            for parameter in paths[route_path][method].get("parameters", ())
            if parameter["in"] == "query"
        }
        expected_names = {field["name"] for field in rust_fields.values()}
        assert set(query_parameters) == expected_names, (route_path, method)
        for rust_name, field in rust_fields.items():
            assert not field["aliases"], (
                "Rust query aliases need explicit compatibility coverage",
                route_path,
                rust_name,
                field["aliases"],
            )
            if route_path in RUST_QUERY_FIELDS:
                assert RUST_QUERY_FIELDS[route_path][rust_name] == rust_query_kind(
                    field["type"], field["default"]
                ), (route_path, rust_name, field)
            parameter = query_parameters[field["name"]]
            expected_type, item_type = rust_openapi_shape(field["type"])
            schema = resolve_openapi_schema(parameter["schema"])
            assert schema["type"] == expected_type, (
                route_path,
                field["name"],
                field["type"],
                schema,
            )
            if item_type:
                assert schema["items"]["type"] == item_type, (route_path, field["name"], schema)
            expected_required = not field["type"].startswith("Option<") and not field["default"]
            assert parameter["required"] is expected_required, (
                route_path,
                field["name"],
                field,
                parameter,
            )

    refresh_source = (rust_root / "api" / "mod.rs").read_text(encoding="utf-8")
    refresh_match = re.search(r"\bpub\s+async\s+fn\s+tickers_refresh\s*\(", refresh_source)
    assert refresh_match is not None
    opening = refresh_source.index("(", refresh_match.start())
    depth = 1
    closing = opening + 1
    while depth and closing < len(refresh_source):
        depth += (refresh_source[closing] == "(") - (refresh_source[closing] == ")")
        closing += 1
    assert depth == 0
    refresh_signature = refresh_source[opening + 1 : closing - 1]
    assert re.search(r"\bJson\s*<\s*RefreshQuery\s*>", refresh_signature)
    assert re.search(
        r"\.route\(\s*\"/tickers/refresh\"\s*,\s*"
        r"axum::routing::post\(api::tickers_refresh\)\s*,?\s*\)",
        main_routes_block,
    )


def test_analysis_routes_forward_snap_flag_to_analysis_methods(client):
    calls = []
    analysis = client.app_instance.state.analysis
    analysis.performers = lambda *args: calls.append(("performers", args)) or {"ok": True}
    analysis.sectors = lambda *args: calls.append(("sectors", args)) or {"ok": True}

    performers = client.get("/analysis/top-performers?snap=false")
    sectors = client.get("/analysis/ma-scores-by-sector?snap=false")

    assert performers.status_code == sectors.status_code == 200
    assert calls == [
        ("performers", ("vn", None, False, "close_changed", "desc", 10, 10000, None, False)),
        ("sectors", ("vn", None, False, 20, 0, False, 10, False)),
    ]


def test_archive_only_ticker_discovery_survives_index_reconstruction(client, tmp_path):
    app = client.app_instance
    repo = app.state.repo
    repo.register("vn", "OLDVN", "Historical fixture company")
    rows = [
        Candle("vn", "OLDVN", "1D", parse_time(day), 100, 101, 99, 100, 1000)
        for day in ("2017-01-03", "2017-01-04")
    ]
    app.state.archive.publish(rows)
    assert repo.state("vn", "OLDVN", "1D") is None
    assert "OLDVN" not in client.get("/tickers/name").json()
    assert "OLDVN" not in client.get("/tickers/name?mode=crypto").json()
    assert "OLDVN" not in client.get("/tickers/name?mode=all").json()
    query = {"symbol": "OLDVN", "start_date": "2017-01-03", "end_date": "2017-01-04", "ma": "false"}
    before = client.get("/tickers", params=query)
    assert before.status_code == 200 and len(before.json()["OLDVN"]) == 2

    settings = replace(app.state.settings, database=tmp_path / "restored-index.sqlite3")
    fresh_app = create_app(settings)
    with TestClient(fresh_app) as restored:
        fresh_repo = fresh_app.state.repo
        assert Archive(fresh_repo, settings).restore_index() == 1
        # An index carries candle identities, not invented company metadata.
        assert "OLDVN" not in restored.get("/tickers/name").json()
        assert restored.get("/tickers", params=query).json() == before.json()
        health = restored.get("/health").json()
        status = fresh_repo.status()
        assert health["daily_records_count"] == health["active_tickers_count"] == 0
        row = next(r for r in status["series"] if r["symbol"] == "OLDVN")
        assert row["status"] == "archived" and not row["enabled"]
        assert row["rows"] == 0 and row["archive_rows"] == 2 and row["archive_objects"] == 1
        assert row["pending_archive_repairs"] == 0
        assert (
            datetime.fromtimestamp(row["archive_first"], UTC).isoformat().startswith("2017-01-03")
        )
        assert datetime.fromtimestamp(row["archive_last"], UTC).isoformat().startswith("2017-01-04")
        assert row["last"] is None and row["last_ingest_ns"] is None
        assert row["successful_at_ns"] is None and not row["verification_current"]
        assert row["latest_verification"] == "unverified"
        assert fresh_repo.status()["jobs"] == []


def test_name_endpoint_uses_rust_catalog_universe_not_discovered_names(client):
    repo = client.app_instance.state.repo
    for source, symbol, name in (
        ("vn", "FPT", "Fixture override"),
        ("vn", "OLDVN", "Historical VN fixture"),
        ("yahoo", "OLDGLOBAL", "Historical global fixture"),
        ("sjc", "OLDGOLD", "Historical gold fixture"),
        ("crypto", "OLDCOIN", "Historical crypto fixture"),
        ("vn", "COLLISION", "VN fixture"),
        ("yahoo", "COLLISION", "Global fixture"),
    ):
        repo.register(source, symbol, name)
    assert client.get("/tickers/name").json()["FPT"] != "Fixture override"
    vn = client.get("/tickers/name?mode=stocks").json()
    assert "OLDVN" not in vn and "OLDGLOBAL" not in vn
    global_names = client.get("/tickers/name?mode=yahoo").json()
    assert "OLDGLOBAL" not in global_names
    assert "OLDGOLD" not in global_names and "OLDVN" not in global_names
    assert "OLDCOIN" not in client.get("/tickers/name?mode=crypto").json()
    all_names = client.get("/tickers/name?mode=all").json()
    assert "COLLISION" not in all_names
    assert list(all_names) == sorted(all_names)
    assert not any(r["enabled"] for r in repo.tickers())


def test_health_separates_published_pending_and_superseded_history(client):
    app = client.app_instance
    repo = app.state.repo
    rows = [
        Candle("vn", "OLDVN", "1D", parse_time(day), 100, 101, 99, 100, 1000)
        for day in ("2017-01-03", "2017-01-04", "2017-01-05")
    ]
    repo.put(rows)
    rows = repo.read("vn", "OLDVN", "1D")
    app.state.archive.publish(rows[:1], prune=True)
    pending = app.state.archive.prepare([replace(rows[1], revision="pending-basis")])
    repo.publish_archive(pending | {"status": "pending_repair"})
    obsolete = app.state.archive.prepare([replace(rows[2], revision="superseded-basis")])
    repo.publish_archive(obsolete | {"status": "superseded"})
    pending_only = app.state.archive.prepare([replace(rows[0], symbol="PENDINGVN")])
    repo.publish_archive(pending_only | {"status": "pending_repair"})
    app.state.health_snapshot.update(repo.health_summary())
    health = client.get("/health").json()
    status = repo.status()
    assert health["daily_records_count"] == 722
    row = next(r for r in status["series"] if r["symbol"] == "OLDVN")
    assert row["rows"] == 2 and row["archive_rows"] == 1 and row["archive_objects"] == 1
    assert row["status"] == "ready" and row["revision"] == rows[0].revision
    assert row["pending_archive_repairs"] == 1
    assert row["archive_first"] == row["archive_last"]
    assert datetime.fromtimestamp(row["archive_last"], UTC).isoformat().startswith("2017-01-03")
    pending_row = next(r for r in status["series"] if r["symbol"] == "PENDINGVN")
    assert (
        pending_row["status"] == "archive_pending" and pending_row["pending_archive_repairs"] == 1
    )
    assert pending_row["rows"] == pending_row["archive_rows"] == pending_row["archive_objects"] == 0
    assert pending_row["archive_first"] is None
    assert pending_row["archive_last"] is None
    assert not pending_row["verification_current"] and not pending_row["enabled"]


def test_analysis_envelopes_and_rust_date_cutoff(client):
    result = client.get("/analysis/top-performers?limit=1&date=2024-02-01").json()
    assert result["analysis_type"] == "top_performers"
    assert result["total_analyzed"] == 0
    assert result["data"]["performers"] == []
    sectors = client.get("/analysis/ma-scores-by-sector?ma_period=20").json()
    assert sectors["data"]["sectors"]
    assert client.get("/analysis/ma-scores-by-sector?ma_period=7").status_code == 400
    rrg = client.get("/analysis/rrg?algorithm=mascore&trails=0").json()
    assert rrg["data"]["algorithm"] == "mascore"
    assert len(rrg["data"]["tickers"]) == 2
    jdk = client.get("/analysis/rrg?algorithm=jdk&benchmark=VNINDEX").json()
    assert jdk["data"]["tickers"]


@pytest.mark.parametrize(
    "route,field,bits,signed",
    [
        ("top-performers", "limit", 64, False),
        ("top-performers", "min_volume", 64, False),
        ("ma-scores-by-sector", "ma_period", 32, False),
        ("ma-scores-by-sector", "top_per_sector", 64, False),
        ("volume-profile", "bins", 64, False),
        ("rrg", "period", 64, False),
        ("rrg", "trails", 64, False),
        ("rrg", "min_volume", 64, True),
    ],
)
def test_analysis_integer_query_bounds_reject_before_reading_history(
    client, monkeypatch, route, field, bits, signed
):
    def unexpected(*args, **kwargs):
        raise AssertionError("Invalid query reached market data reads")

    monkeypatch.setattr(client.app_instance.state.history, "query", unexpected)
    monkeypatch.setattr(client.app_instance.state.history, "read", unexpected)
    lower = -(1 << (bits - 1)) if signed else 0
    upper = (1 << (bits - int(signed))) - 1
    for value in (lower - 1, upper + 1):
        params = {field: value}
        if route == "volume-profile":
            params.update(symbol="FPT", date="2024-02-01")
        response = client.get(f"/analysis/{route}", params=params)
        assert response.status_code == 400


@pytest.mark.parametrize(
    "mode,expected", [("CRYPTO", "crypto"), ("YaHoO", "yahoo"), ("unknown", "vn")]
)
def test_volume_profile_legacy_mode_dispatch(client, monkeypatch, mode, expected):
    stamp = parse_time("2024-02-01T02:00:00")
    rows = [Candle(expected, "FPT", "1m", stamp, 100, 110, 90, 105, 1000)]
    calls = []

    def read(
        source,
        symbol,
        interval,
        start=None,
        end=None,
        limit=None,
        forward=False,
        ignore_pending_repairs=False,
        ignore_history_gaps=False,
    ):
        assert not ignore_pending_repairs
        assert ignore_history_gaps
        calls.append((source, symbol, interval, start, end, limit, forward))
        return rows

    monkeypatch.setattr(client.app_instance.state.history, "cache_tail", None)
    monkeypatch.setattr(client.app_instance.state.history, "read", read)
    response = client.get(
        "/analysis/volume-profile", params={"symbol": "FPT", "date": "2024-02-01", "mode": mode}
    )
    assert response.status_code == 200
    assert calls[0][:3] == (expected, "FPT", "1m")
    assert response.json()["data"]["total_minutes"] == 1


def test_volume_profile_empty_symbol_error_precedes_date_validation(client):
    response = client.get("/analysis/volume-profile", params={"symbol": ""})
    assert response.status_code == 400
    assert response.json() == {"error": "symbol parameter is required"}


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            "symbol=FPT&start_date=2024-09-01&end_date=",
            {"error": "Invalid end date format. Use YYYY-MM-DD"},
        ),
        (
            "symbol=FPT&date=&start_date=2024-09-01",
            {"error": "Invalid start date format. Use YYYY-MM-DD"},
        ),
    ],
)
def test_volume_profile_empty_date_options_preserve_rust_precedence(client, query, expected):
    response = client.get(f"/analysis/volume-profile?{query}")

    assert response.status_code == 400
    assert response.json() == expected


def test_analysis_zero_controls_keep_legacy_semantics(client):
    performers = client.get("/analysis/top-performers", params={"limit": 0})
    assert performers.status_code == 200 and len(performers.json()["data"]["performers"]) == 1
    sectors = client.get("/analysis/ma-scores-by-sector", params={"top_per_sector": 0})
    assert sectors.status_code == 200
    assert sectors.json()["data"]["sectors"]
    assert all(not item["top_stocks"] for item in sectors.json()["data"]["sectors"])
    rrg = client.get("/analysis/rrg", params={"period": 0, "trails": 0, "min_volume": -1})
    assert rrg.status_code == 200 and rrg.json()["data"]["period"] == 4
    assert all("trails" not in item for item in rrg.json()["data"]["tickers"])


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ("/analysis/top-performers?ema=1", "ema"),
        ("/analysis/ma-scores-by-sector?above_threshold_only=yes", "above_threshold_only"),
        ("/analysis/rrg?ema=on&snap=0", "ema"),
    ],
)
def test_analysis_boolean_queries_require_rust_json_literals(client, path, field):
    response = client.get(path)

    assert response.status_code == 400
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == (
        f"Failed to deserialize query string: {field}: provided string was not `true` or `false`"
    )


def test_analysis_reports_first_invalid_boolean_in_query_pair_order(client):
    response = client.get("/analysis/rrg?snap=0&ema=on")

    assert response.status_code == 400
    assert response.text == (
        "Failed to deserialize query string: snap: provided string was not `true` or `false`"
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            "ema=on&ema=true",
            "Failed to deserialize query string: ema: provided string was not `true` or `false`",
        ),
        (
            "ema=true&ema=false",
            "Failed to deserialize query string: duplicate field `ema`",
        ),
    ],
)
def test_repeated_boolean_query_fields_match_serde_urlencoded(client, query, expected):
    response = client.get(f"/analysis/rrg?{query}")

    assert response.status_code == 400
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "/tickers?symbol=FPT&limit=1&limit=2",
            "Failed to deserialize query string: limit: unsupported value",
        ),
        (
            "/analysis/ma-scores-by-sector?min_score=1.2&min_score=2.3",
            "Failed to deserialize query string: duplicate field `min_score`",
        ),
        (
            "/analysis/top-performers?sector=Banks&sector=Tech",
            "Failed to deserialize query string: duplicate field `sector`",
        ),
    ],
)
def test_repeated_scalar_query_fields_match_rust_extractors(client, url, expected):
    response = client.get(url)

    assert response.status_code == 400
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == expected


def test_query_rejections_follow_pair_order_and_only_run_on_matching_method(client):
    response = client.get("/analysis/rrg?period=bad&ema=on")

    assert response.status_code == 400
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == (
        "Failed to deserialize query string: period: invalid digit found in string"
    )

    response = client.post("/analysis/rrg?ema=on")
    assert response.status_code == 405

    response = client.get("/analysis/rrg?period=")
    assert response.status_code == 400
    assert response.text == (
        "Failed to deserialize query string: period: cannot parse integer from empty string"
    )

    response = client.get("/analysis/rrg?period=%2B")
    assert response.status_code == 400
    assert response.text == (
        "Failed to deserialize query string: period: invalid digit found in string"
    )

    response = client.get("/analysis/rrg?period=18446744073709551616x")
    assert response.status_code == 400
    assert response.text == (
        "Failed to deserialize query string: period: number too large to fit in target type"
    )


def test_query_enum_errors_match_serde_alias_list(client):
    response = client.get("/tickers/group?mode=bad")

    assert response.status_code == 400
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == (
        "Failed to deserialize query string: mode: unknown variant `bad`, expected one of "
        "`stock`, `stocks`, `vn`, `crypto`, `cryptos`, `yahoo`, `all`"
    )


def test_sync_get_query_extraction_matches_serde_html_form(client):
    key = "00000000-0000-0000-0000-000000000001"
    missing = client.get(f"/sync/{key}")
    assert missing.status_code == 400
    assert missing.headers["content-type"] == "text/plain; charset=utf-8"
    assert missing.text == "Failed to deserialize query string: missing field `secret`"

    repeated = client.get(f"/sync/{key}?secret=a&secret=b")
    assert repeated.status_code == 400
    assert repeated.text == "Failed to deserialize query string: secret: unsupported value"


def test_blank_optional_query_values_use_rust_defaults(client):
    tickers = client.get("/tickers?symbol=FPT&limit=&ma=false")
    assert tickers.status_code == 200
    assert len(tickers.json()["FPT"]) == 240

    performers = client.get("/analysis/top-performers?limit=")
    assert performers.status_code == 400
    assert performers.headers["content-type"] == "text/plain; charset=utf-8"
    assert performers.text == (
        "Failed to deserialize query string: limit: cannot parse integer from empty string"
    )

    blank_optional_profile = client.get(
        "/analysis/volume-profile?symbol=FPT&date=2024-08-27&bins=&value_area_pct="
    )
    assert blank_optional_profile.status_code == 400
    assert blank_optional_profile.headers["content-type"] == "text/plain; charset=utf-8"
    assert blank_optional_profile.text == (
        "Failed to deserialize query string: bins: cannot parse integer from empty string"
    )

    blank_date = client.get("/analysis/rrg?date=")
    assert blank_date.status_code == 400
    assert blank_date.json() == {"error": "Invalid date format ''. Use YYYY-MM-DD"}

    blank_benchmark = client.get("/analysis/rrg?benchmark=")
    assert blank_benchmark.status_code == 400
    assert blank_benchmark.json() == {"error": "Benchmark '' not found"}


def test_query_float_parser_matches_rust_and_nonfinite_json_is_null(client):
    malformed = client.get("/analysis/ma-scores-by-sector?min_score=%201")
    assert malformed.status_code == 400
    assert malformed.headers["content-type"] == "text/plain; charset=utf-8"
    assert malformed.text == (
        "Failed to deserialize query string: min_score: invalid float literal"
    )

    empty = client.get("/analysis/ma-scores-by-sector?min_score=")
    assert empty.status_code == 400
    assert empty.text == (
        "Failed to deserialize query string: min_score: cannot parse float from empty string"
    )

    nonfinite = client.get("/analysis/ma-scores-by-sector?min_score=nan")
    assert nonfinite.status_code == 200
    assert nonfinite.json()["data"]["threshold"] is None

    non_ascii_infinity = client.get("/analysis/ma-scores-by-sector?min_score=%C4%B0nf")
    assert non_ascii_infinity.status_code == 400
    assert non_ascii_infinity.headers["content-type"] == "text/plain; charset=utf-8"
    assert non_ascii_infinity.text == (
        "Failed to deserialize query string: min_score: invalid float literal"
    )


@pytest.mark.parametrize("value", ["1.0", " 1", "1 ", "1e0", "１", "--1"])
def test_integer_query_lexical_validation(client, value):
    for path, params in (
        ("/tickers", {"symbol": "FPT", "limit": value}),
        ("/analysis/top-performers", {"limit": value}),
        ("/analysis/rrg", {"min_volume": value}),
    ):
        assert client.get(path, params=params).status_code == 400


def test_unsigned_negative_zero_and_signed_integer_forms(client):
    assert client.get("/analysis/top-performers", params={"limit": "-0"}).status_code == 400
    assert client.get("/analysis/top-performers", params={"limit": "+01"}).status_code == 200
    assert client.get("/tickers", params={"symbol": "FPT", "limit": "+01"}).status_code == 200
    rrg = client.get("/analysis/rrg", params={"min_volume": "-0", "trails": "0"})
    assert rrg.status_code == 200 and rrg.json()["data"]["tickers"]


def test_archive_backed_web_response(client):
    app = client.app_instance
    before = client.get(
        "/tickers?symbol=FPT&start_date=2024-01-01&end_date=2024-08-20&limit=1000&cache=false"
    ).json()
    old = app.state.repo.read(
        "vn", "FPT", "1D", end=int(datetime(2024, 7, 1, tzinfo=UTC).timestamp())
    )
    app.state.archive.publish(old, prune=True)
    after = client.get(
        "/tickers?symbol=FPT&start_date=2024-01-01&end_date=2024-08-20&limit=1000&cache=false"
    ).json()
    assert before == after


def test_sync_and_refresh_auth(client):
    key = str(uuid.uuid4())
    headers = {"Authorization": "Bearer test-token"}
    response = client.post(
        f"/sync/{key}", json={"secret": "s", "value": {"watchlists": []}}, headers=headers
    )
    assert response.status_code == 200
    assert client.get(f"/sync/{key}?secret=s", headers=headers).json() == response.json()
    assert client.get(f"/sync/{key}?secret=x", headers=headers).status_code == 403
    assert client.get(f"/sync/{key}?secret=s").status_code == 401
    assert client.post("/tickers/refresh", json={"interval": "1D"}).status_code == 401

    repo = client.app_instance.state.repo
    repo.put([Candle("vn", "READY_DISABLED", "1D", parse_time("2024-09-01"), 10, 11, 9, 10, 100)])
    repo.register("vn", "ENABLED_WITHOUT_READY_SERIES", enabled=True)
    repo.register("vn", "ONLY_PENDING_SERIES", enabled=True)
    with repo.connect() as con:
        con.execute(
            "INSERT INTO series(source,symbol,interval,provider,revision,status) "
            "VALUES ('vn','ONLY_PENDING_SERIES','1D','fixture','v1','pending')"
        )
        con.execute(
            "UPDATE tickers SET next_1d=123 WHERE source='vn' "
            "AND symbol IN ('READY_DISABLED','ENABLED_WITHOUT_READY_SERIES','ONLY_PENDING_SERIES')"
        )
    requested_at = int(time.time())
    refreshed = client.post(
        "/tickers/refresh", json={"interval": "1D", "key": "refresh-test"}
    ).json()
    assert refreshed["interval"] == "next_1d"
    assert refreshed["updated"] == 4
    with repo.connect() as con:
        assert (
            con.execute(
                "SELECT next_1d FROM tickers WHERE source='vn' AND symbol='ENABLED_WITHOUT_READY_SERIES'"
            ).fetchone()[0]
            == 123
        )
        assert (
            con.execute(
                "SELECT next_1d FROM tickers WHERE source='vn' AND symbol='ONLY_PENDING_SERIES'"
            ).fetchone()[0]
            == 123
        )
        scheduled_at = con.execute(
            "SELECT next_1d FROM tickers WHERE source='vn' AND symbol='READY_DISABLED'"
        ).fetchone()[0]
        assert requested_at <= scheduled_at <= int(time.time())


SYNC_KEY = "550e8400-e29b-41d4-a716-446655440000"


@pytest.mark.parametrize(
    "key",
    [
        "uuid:" + SYNC_KEY,
        "urn:" + SYNC_KEY,
        "{{" + SYNC_KEY + "}}",
        "{" + SYNC_KEY.replace("-", "") + "}",
        "urn:uuid:" + SYNC_KEY.replace("-", ""),
        SYNC_KEY.replace("-", "", 1),
        SYNC_KEY.replace("-", "--", 1),
        "-" + SYNC_KEY.replace("-", ""),
    ],
)
def test_sync_rejects_uuid_shapes_rejected_by_legacy_parser(client, key):
    headers = {"Authorization": "Bearer test-token"}
    expected = {"success": False, "error": "Key must be a valid UUID"}
    get = client.get(f"/sync/{key}", params={"secret": "s"}, headers=headers)
    post = client.post(f"/sync/{key}", json={"secret": "s", "value": {}}, headers=headers)
    assert get.status_code == post.status_code == 400
    assert get.json() == post.json() == expected
    with client.app.state.repo.connect() as con:
        assert con.execute("SELECT count(*) FROM sync_kv").fetchone()[0] == 0


@pytest.mark.parametrize(
    "key",
    [SYNC_KEY.upper(), SYNC_KEY.replace("-", ""), "{" + SYNC_KEY + "}", "urn:uuid:" + SYNC_KEY],
)
def test_sync_valid_uuid_formats_share_one_canonical_record_and_secret(client, key):
    headers = {"Authorization": "Bearer test-token"}
    created = client.post(
        f"/sync/{key}", json={"secret": "s", "value": {"watchlists": []}}, headers=headers
    )
    assert created.status_code == 200 and created.json()["id"] == SYNC_KEY
    assert (
        client.get(f"/sync/{SYNC_KEY}", params={"secret": "s"}, headers=headers).json()
        == created.json()
    )
    rejected = client.post(
        f"/sync/{SYNC_KEY}", json={"secret": "wrong", "value": {}}, headers=headers
    )
    assert rejected.status_code == 403
    updated = client.post(
        f"/sync/{SYNC_KEY}",
        json={"secret": "s", "value": {"watchlists": ["VN30"]}},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["created_at"] == created.json()["created_at"]
    assert (
        client.get(f"/sync/{key}", params={"secret": "s"}, headers=headers).json() == updated.json()
    )
    with client.app.state.repo.connect() as con:
        assert con.execute("SELECT count(*) FROM sync_kv").fetchone()[0] == 1


def test_cors_and_rust_response_cache_semantics(client):
    response = client.options(
        "/sync/example",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    client.get("/tickers?symbol=FPT&limit=1")
    cached = client.get("/tickers?symbol=FPT&limit=1")
    assert cached.headers["x-data-source"] == "in-memory"
    repo = client.app_instance.state.repo
    row = repo.read("vn", "FPT", "1D", limit=1)[0]
    repo.put([replace(row, high=row.high + 10, close=row.close + 5)])
    # Rust's response cache is query-keyed and keeps serving the old row until
    # its TTL expires; writes do not invalidate the cached response.
    still_cached = client.get("/tickers?symbol=FPT&limit=1")
    assert still_cached.headers["x-data-source"] == "in-memory"
    assert still_cached.json()["FPT"][0]["close"] == row.close
    fresh = client.get("/tickers?symbol=FPT&limit=1&cache=false")
    assert fresh.json()["FPT"][0]["close"] == row.close + 5

    mode_all_first = client.get("/tickers?symbol=FPT&mode=all&limit=1&ma=false")
    mode_all_cached = client.get("/tickers?symbol=FPT&mode=all&limit=1&ma=false")
    assert "x-data-source" in mode_all_first.headers
    assert "x-data-source" not in mode_all_cached.headers

    zero_first = client.get("/tickers?symbol=FPT&limit=0")
    zero_cached = client.get("/tickers?symbol=FPT&limit=0")
    assert zero_first.headers["x-data-source"] == "database"
    assert zero_cached.headers["x-data-source"] == "in-memory"
    zero_all = client.get("/tickers?symbol=FPT&limit=0&mode=all&cache=false")
    assert zero_all.headers["x-data-source"] == "empty"


def test_tick_cache_expires_after_configured_ttl(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "cache-ttl.sqlite3",
        cache_dir=tmp_path / "cache-ttl",
        object_dir=tmp_path / "objects-ttl",
        archive_backend="filesystem",
        cache_ttl=0.5,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        repo = app.state.repo
        row = Candle("vn", "FPT", "1D", parse_time("2024-01-01"), 10, 11, 9, 10, 100)
        repo.put([row])
        url = "/tickers?symbol=FPT&limit=1&ma=false"
        assert test_client.get(url).json()["FPT"][0]["close"] == 10
        repo.put([replace(row, close=12, high=13)])
        assert test_client.get(url).json()["FPT"][0]["close"] == 10
        time.sleep(0.55)
        expired = test_client.get(url)
        assert expired.headers["x-data-source"] != "in-memory"
        assert expired.json()["FPT"][0]["close"] == 12


@pytest.mark.parametrize(
    ("headers", "allowed_origin"),
    [
        ({}, None),
        ({"Origin": "http://localhost:5173"}, "http://localhost:5173"),
        ({"Origin": "https://not-allowed.example"}, None),
        (
            {
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "HEAD",
                "Access-Control-Request-Headers": "x-not-allowed",
            },
            "http://localhost:5173",
        ),
    ],
)
def test_every_options_request_matches_tower_cors_transport(client, headers, allowed_origin):
    response = client.options("/sync/example", headers=headers)

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["vary"] == (
        "origin, access-control-request-method, access-control-request-headers"
    )
    assert response.headers["access-control-allow-methods"] == "GET,POST,DELETE,OPTIONS"
    assert (
        response.headers["access-control-allow-headers"] == "content-type,authorization,user-agent"
    )
    assert response.headers.get("access-control-allow-origin") == allowed_origin
    assert response.headers["allow"] == "GET,HEAD,POST"
    assert "access-control-max-age" not in response.headers
    assert response.headers["content-length"] == "0"
    assert "transfer-encoding" not in response.headers


def test_raw_route_matching_and_path_utf8_rejections_match_axum(client):
    for path in (
        "/%68ealth",
        "/tickers%2Fgroup",
        "/analysis%2Frrg",
        "/public%2Fjs/app.js",
    ):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json() == {"error": "not found"}, path

    sync_headers = {"Authorization": "Bearer test-token"}
    response = client.get("/sync/not%2Fa-key?secret=sync-test", headers=sync_headers)
    assert response.status_code == 400
    assert response.json() == {"success": False, "error": "Key must be a valid UUID"}

    response = client.get("/sync/%FF?secret=sync-test", headers=sync_headers)
    assert response.status_code == 400
    assert response.text == "Invalid URL: Invalid UTF-8 in `key`"
    assert response.headers["content-type"].startswith("text/plain")

    session_id = "550e8400-e29b-41d4-a716-446655440000"
    for path, message in (
        (
            f"/uploads/{session_id}/markdown/%FF",
            "Invalid URL: Invalid UTF-8 in `filename`",
        ),
        (
            "/uploads/%FF/markdown/content.md",
            "Invalid URL: Invalid UTF-8 in `session_id`",
        ),
    ):
        response = client.get(path)
        assert response.status_code == 400, path
        assert response.text == message, path
        assert response.headers["content-type"].startswith("text/plain"), path

    response = client.delete("/uploads/%FF?secret=sync-test")
    assert response.status_code == 400
    assert response.text == "Invalid URL: Invalid UTF-8 in `session_id`"
    assert response.headers["content-type"].startswith("text/plain")


def test_options_allow_uses_axum_raw_dynamic_segments(client):
    session_id = "550e8400-e29b-41d4-a716-446655440000"
    cases = (
        ("/%73ync/foo", None),
        ("/sync/foo/bar", None),
        ("/sync/foo%2Fbar", "GET,HEAD,POST"),
        ("/%75ploads/foo", None),
        (f"/%75ploads/{session_id}/markdown/file.md", None),
        ("/uploads/foo/bar", None),
        ("/uploads/foo%2Fbar", "DELETE"),
        (f"/uploads/{session_id}/markdown/file/extra", None),
        (f"/uploads/{session_id}/%6darkdown/file.md", None),
        (f"/uploads/{session_id}/markdown/file%2Fextra", "GET,HEAD"),
    )
    for path, expected_allow in cases:
        response = client.options(path)
        assert response.status_code == 200, path
        assert response.headers.get("allow") == expected_allow, path


def test_encoded_static_parent_path_is_rejected_like_serve_dir(client):
    for path in (
        "/public/js/%2e%2e/index.html",
        "/public/js/%2e%2e%2findex.html",
        "/public/js/%252e%252e/index.html",
        "/public/js/%252e%252e%252findex.html",
        "/public/js/%25252e%25252e/index.html",
    ):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.content == b"", path


def test_static_percent_encoded_filename_survives_single_decode_asgi_scope(tmp_path):
    public_dir = tmp_path / "public"
    javascript_dir = public_dir / "js"
    javascript_dir.mkdir(parents=True)
    (javascript_dir / "%2e%2e.txt").write_bytes(b"literal percent filename")
    settings = replace(
        Settings(),
        database=tmp_path / "db.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        uploads_dir=tmp_path / "uploads",
        public_dir=public_dir,
        archive_backend="filesystem",
    )
    app = create_app(settings)

    class SingleDecodeAsgiPath:
        """Model a server that decodes raw_path exactly once for scope.path."""

        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                scope = dict(scope)
                raw_path = scope.get("raw_path", b"").split(b"?", 1)[0]
                scope["path"] = unquote_to_bytes(raw_path).decode("utf-8")
            await self.inner(scope, receive, send)

    with TestClient(SingleDecodeAsgiPath(app)) as client:
        parent_alias = client.get("/public/js/%252e%252e/index.html")
        encoded_filename = client.get("/public/js/%252e%252e.txt")

    assert parent_alias.status_code == 404
    assert parent_alias.content == b""
    assert encoded_filename.status_code == 200
    assert encoded_filename.content == b"literal percent filename"


def test_cors_simple_responses_and_existing_vary_match_tower(client):
    allowed = client.get("/health", headers={"Origin": "http://localhost:5173"})
    without_origin = client.get("/health")
    disallowed = client.get("/health", headers={"Origin": "https://not-allowed.example"})

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert without_origin.headers.get("access-control-allow-origin") is None
    assert disallowed.headers.get("access-control-allow-origin") is None
    exposed = (
        "x-data-source,x-redis-base,x-redis-raw-count,x-redis-aligned,x-redis-limit,"
        "cf-cache-status,cf-ray,x-ratelimit-limit,x-ratelimit-remaining,retry-after"
    )
    assert allowed.headers["access-control-expose-headers"] == exposed
    assert without_origin.headers["access-control-expose-headers"] == exposed
    assert disallowed.headers["access-control-expose-headers"] == exposed
    cors_vary = "origin, access-control-request-method, access-control-request-headers"
    assert (
        allowed.headers["vary"]
        == without_origin.headers["vary"]
        == (f"accept-encoding, {cors_vary}")
    )
    assert disallowed.headers["vary"] == f"accept-encoding, {cors_vary}"

    client.app_instance.add_api_route(
        "/vary-test",
        lambda: Response("ok", headers={"vary": "Accept-Encoding"}),
        methods=["GET"],
    )
    existing_vary = client.get("/vary-test")
    assert existing_vary.headers["vary"] == f"Accept-Encoding, {cors_vary}"


@pytest.mark.parametrize(
    ("accept_encoding", "expected_encoding"),
    [
        ("gzip", "gzip"),
        ("deflate", "deflate"),
        ("gzip;q=0.3, deflate;q=0.8", "deflate"),
        ("gzip;q=0, deflate;q=0, br;q=0, identity;q=1", None),
    ],
)
def test_compression_negotiation_matches_tower(client, accept_encoding, expected_encoding):
    response = client.get("/health", headers={"Accept-Encoding": accept_encoding})

    assert response.status_code == 200
    assert response.headers.get("content-encoding") == expected_encoding
    assert "accept-encoding" in response.headers["vary"].lower()
    assert response.json()["trading_hours_timezone"] == "Asia/Ho_Chi_Minh"


@pytest.mark.parametrize("encoding", ["gzip", "br"])
def test_large_compressed_responses_round_trip_after_worker_offload(client, encoding):
    body = b"OHLCV,2026-10-21,100,110,90,105,123456\n" * 2048
    client.app_instance.add_api_route(
        "/compression-large",
        lambda: Response(body, media_type="text/csv"),
        methods=["GET"],
    )

    response = client.get("/compression-large", headers={"Accept-Encoding": encoding})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == encoding
    assert response.content == body


@pytest.mark.skipif(
    importlib.util.find_spec("brotli") is None, reason="Brotli dependency is not installed"
)
def test_brotli_tie_preference_matches_tower(client):
    response = client.get("/health", headers={"Accept-Encoding": "gzip, deflate, br"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "br"
    assert "accept-encoding" in response.headers["vary"].lower()
    assert response.json()["trading_hours_timezone"] == "Asia/Ho_Chi_Minh"


def test_small_responses_are_not_compressed_or_vary_by_encoding(client):
    client.app_instance.add_api_route(
        "/compression-small",
        lambda: Response("small", media_type="text/plain"),
        methods=["GET"],
    )

    response = client.get("/compression-small", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.text == "small"
    assert "content-encoding" not in response.headers
    assert "accept-encoding" not in response.headers.get("vary", "").lower()


def test_compressed_static_asset_drops_accept_ranges(client):
    response = client.get("/public/js/app.js", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert "accept-ranges" not in response.headers


def test_image_svg_content_type_is_compressible_like_tower(client):
    body = "<svg>" + ("x" * 40) + "</svg>"
    client.app_instance.add_api_route(
        "/compression-svg",
        lambda: Response(body, media_type="image/svg+xml"),
        methods=["GET"],
    )

    response = client.get("/compression-svg", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in response.headers["vary"].lower()
    assert response.text == body


def test_image_svg_with_parameters_is_not_compressible_like_tower(client):
    body = "<svg>" + ("x" * 40) + "</svg>"
    client.app_instance.add_api_route(
        "/compression-svg-charset",
        lambda: Response(body, headers={"content-type": "image/svg+xml; charset=utf-8"}),
        methods=["GET"],
    )

    response = client.get("/compression-svg-charset", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml; charset=utf-8"
    assert "content-encoding" not in response.headers
    assert response.content.decode() == body


@pytest.mark.parametrize(
    ("headers", "expected_compressible"),
    [
        ([(b"content-type", b"IMAGE/PNG")], True),
        (
            [
                (b"content-type", b"image/png"),
                (b"content-type", b"text/plain"),
            ],
            False,
        ),
        (
            [
                (b"content-type", b"text/plain"),
                (b"content-type", b"image/png"),
            ],
            True,
        ),
    ],
)
def test_compression_predicate_uses_raw_first_content_type(headers, expected_compressible):
    from aipriceaction_api.transport import _compressible

    assert _compressible(headers) is expected_compressible


@pytest.mark.parametrize("cors_origins", [("*",), ("\x00",)])
def test_permissive_cors_matches_tower_defaults(client, tmp_path, cors_origins):
    settings = replace(
        client.app_instance.state.settings,
        database=tmp_path / "permissive.sqlite3",
        cache_dir=tmp_path / "permissive-cache",
        object_dir=tmp_path / "permissive-objects",
        cors_origins=cors_origins,
    )
    app = create_app(settings)

    with TestClient(app) as permissive:
        options = permissive.options("/health")
        health = permissive.get("/health")

    assert options.status_code == 200 and options.content == b""
    assert options.headers["access-control-allow-origin"] == "*"
    assert options.headers["access-control-allow-methods"] == "*"
    assert options.headers["access-control-allow-headers"] == "*"
    assert health.headers["access-control-allow-origin"] == "*"
    assert health.headers["access-control-expose-headers"] == "*"


@pytest.mark.parametrize("cors_origins", [(), ("",)])
def test_empty_cors_origins_do_not_enable_permissive_mode(client, tmp_path, cors_origins):
    settings = replace(
        client.app_instance.state.settings,
        database=tmp_path / "empty-origin.sqlite3",
        cache_dir=tmp_path / "empty-origin-cache",
        object_dir=tmp_path / "empty-origin-objects",
        cors_origins=cors_origins,
    )
    app = create_app(settings)

    with TestClient(app) as configured:
        options = configured.options("/health", headers={"Origin": "https://example.com"})
        health = configured.get("/health", headers={"Origin": "https://example.com"})

    assert options.status_code == 200 and options.content == b""
    assert options.headers.get("access-control-allow-origin") is None
    assert options.headers["access-control-allow-methods"] == "GET,POST,DELETE,OPTIONS"
    assert (
        options.headers["access-control-allow-headers"] == "content-type,authorization,user-agent"
    )
    assert health.headers.get("access-control-allow-origin") is None


@pytest.mark.parametrize(
    "cors_origins",
    [
        ("*", "https://aipriceaction.com"),
        ("*", ""),
        ("\x00", "*"),
    ],
)
def test_mixed_wildcard_cors_config_is_rejected(client, cors_origins):
    settings = replace(
        client.app_instance.state.settings,
        cors_origins=cors_origins,
    )

    with pytest.raises(ValueError, match=r"cannot combine '\*' with specific origins"):
        create_app(settings)


def test_settings_preserve_empty_cors_entries(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "*, ")

    assert Settings.from_env().cors_origins == ("*", "")


def test_settings_keep_rust_fixed_request_timeout(monkeypatch):
    monkeypatch.setenv("API_QUERY_TIMEOUT", "1")

    assert Settings.from_env().query_timeout == 180


def test_settings_use_rust_whitespace_for_cors_entries(monkeypatch):
    monkeypatch.setenv(
        "CORS_ORIGINS", "\x1chttps://aipriceaction.com, https://api.aipriceaction.com "
    )

    assert Settings.from_env().cors_origins == (
        "\x1chttps://aipriceaction.com",
        "https://api.aipriceaction.com",
    )


@pytest.mark.skipif(
    not hasattr(os, "environb"), reason="byte environment is only available on Unix"
)
def test_non_utf8_cors_env_uses_rust_fallback(monkeypatch):
    monkeypatch.setitem(os.environb, b"CORS_ORIGINS", b"\xff")

    assert Settings.from_env().cors_origins == RUST_DEFAULT_CORS_ORIGINS


def test_unset_cors_env_uses_rust_default(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    assert Settings.from_env().cors_origins == RUST_DEFAULT_CORS_ORIGINS


def test_utf8_cors_value_matches_rust_header_bytes():
    from starlette.applications import Starlette
    from starlette.routing import Route

    from aipriceaction_api.app import RustCompatibleCORSMiddleware, rust_header_value

    async def endpoint(_request):
        return Response("ok")

    app = Starlette(routes=[Route("/", endpoint)])
    app.add_middleware(
        RustCompatibleCORSMiddleware,
        allow_origins=[rust_header_value("Ā")],
        allow_methods=["GET"],
        allow_headers=[],
    )
    messages = []

    async def invoke():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"origin", b"\xc4\x80")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        await app(scope, receive, send)

    asyncio.run(invoke())

    response_headers = dict(
        next(m["headers"] for m in messages if m["type"] == "http.response.start")
    )
    assert response_headers[b"access-control-allow-origin"] == b"\xc4\x80"


def test_rust_does_not_trim_ascii_file_separator_from_cors(client, tmp_path):
    settings = replace(
        client.app_instance.state.settings,
        database=tmp_path / "rust-trim-cors.sqlite3",
        cache_dir=tmp_path / "rust-trim-cors-cache",
        object_dir=tmp_path / "rust-trim-cors-objects",
        cors_origins=("\x1c*", "https://aipriceaction.com"),
    )

    # Rust leaves U+001C in place, rejects that HeaderValue, and keeps the valid
    # origin. Python's ordinary str.strip() would incorrectly make this a wildcard.
    app = create_app(settings)
    with TestClient(app) as configured:
        response = configured.get("/health", headers={"Origin": "https://aipriceaction.com"})

    assert response.headers["access-control-allow-origin"] == "https://aipriceaction.com"
