import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aipriceaction_api.analysis import Analysis, _jdk_lookback_limit
from aipriceaction_api.app import create_app
from aipriceaction_api.calculations import enhance, jdk, moving_average, volume_profile
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import (
    Candle,
    DataError,
    date_bounds,
    format_chrono_date,
    parse_time,
)


class FakeHistory:
    def __init__(self, data):
        self.data = data
        self.calls = []
        self.limits = []
        self.query_calls = []
        self.repo = SimpleNamespace(tickers=lambda _sources: [])

    def read(
        self,
        source,
        symbol,
        interval,
        start=None,
        end=None,
        limit=None,
        forward=False,
        ignore_history_gaps=False,
    ):
        del ignore_history_gaps
        self.calls.append((source, symbol, interval))
        self.limits.append(limit)
        rows = list(self.data.get((source, symbol, interval), ()))
        if start is not None:
            rows = [row for row in rows if row.time >= start]
        if end is not None:
            rows = [row for row in rows if row.time <= end]
        rows.sort(key=lambda row: row.time)
        if limit is not None:
            rows = rows[:limit] if forward else rows[-limit:]
        return rows

    def query(
        self,
        source,
        symbol,
        interval,
        start=None,
        end=None,
        limit=252,
        ma=True,
        ema=False,
        strict_source_intervals=False,
    ):
        self.query_calls.append(
            (source, symbol, interval, start, end, limit, ema, strict_source_intervals)
        )
        rows = list(self.data.get((source, symbol, interval), ()))
        if start is not None:
            rows = [row for row in rows if row.time >= start]
        if end is not None:
            rows = [row for row in rows if row.time <= end]
        rows.sort(key=lambda row: row.time)
        if limit is not None:
            rows = rows[-limit:]
        return enhance(rows, interval, ma, ema)


class FakeCatalog:
    def __init__(self, groups, files):
        self.groups_by_source = groups
        self.files = files

    def load(self, name):
        return self.files[name]

    def sector(self, source, symbol):
        for name, symbols in sorted(self.groups_by_source[source].items()):
            if name != "ALL_STOCKS" and symbol in symbols:
                return name
        return None


class FakeSnapshotRedis:
    def __init__(self, hashes=None):
        self.hashes = hashes or {}
        self.hget_calls = []
        self.expiries = {}

    def hget(self, name, field):
        self.hget_calls.append((name, field))
        return self.hashes.get((name, field))

    def hset(self, name, mapping):
        self.hashes.update({(name, field): value for field, value in mapping.items()})
        return len(mapping)

    def expire(self, name, seconds):
        self.expiries[name] = seconds
        return True


def daily_rows(source, symbol, count=140, start=100.0, missing_last=False):
    origin = datetime(2024, 1, 1, tzinfo=UTC)
    rows = []
    for i in range(count):
        close = start + i
        rows.append(
            Candle(
                source,
                symbol,
                "1D",
                int((origin + timedelta(days=i)).timestamp()),
                close,
                close + 1,
                close - 1,
                close,
                10000 + i,
            )
        )
    return rows[:-1] if missing_last else rows


def rrg_catalog():
    return FakeCatalog(
        {
            "vn": {
                "Banks": ["VCB", "VNINDEX", "HNXINDEX"],
                "ALL_STOCKS": ["WIDEVN"],
            },
            "crypto": {"CRYPTO_TOP_100": ["BTCUSDT"]},
            "yahoo": {"Tech": ["AAPL"]},
            "sjc": {"Gold": ["GOLD"]},
        },
        {
            "ticker_group.json": {"Banks": ["VCB", "VNINDEX", "HNXINDEX"]},
            "binance_tickers.json": {"data": [{"symbol": "BTCUSDT"}]},
            "global_tickers.json": {"data": [{"symbol": "AAPL", "category": "Tech"}]},
            "sjc_tickers.json": {"data": [{"symbol": "GOLD", "category": "Gold"}]},
        },
    )


def test_rrg_uses_rust_curated_membership_and_merged_yahoo_groups():
    data = {}
    for source, symbol, start in (
        ("vn", "VCB", 100),
        ("vn", "VNINDEX", 1000),
        ("vn", "HNXINDEX", 2000),
        ("vn", "WIDEVN", 3000),
        ("vn", "UNLISTED", 4000),
        ("crypto", "BTCUSDT", 10000),
        ("yahoo", "AAPL", 200),
        ("yahoo", "GOLD", 300),
        ("sjc", "GOLD", 400),
    ):
        data[(source, symbol, "1D")] = daily_rows(source, symbol, start=start)

    analysis = Analysis(FakeHistory(data), rrg_catalog())
    response = analysis.rrg(mode="all", algorithm="mascore", trails=0, min_volume=0)
    tickers = {row["symbol"]: row for row in response["data"]["tickers"]}

    assert response["analysis_date"] == "latest"
    assert set(tickers) == {"VCB", "HNXINDEX", "BTCUSDT", "AAPL", "GOLD"}
    assert "WIDEVN" not in tickers
    assert "UNLISTED" not in tickers
    assert tickers["GOLD"]["source"] == "yahoo"
    assert tickers["GOLD"]["sector"] == "Gold"
    assert tickers["AAPL"]["sector"] == "Tech"
    assert tickers["VCB"]["sector"] == "Banks"
    assert [(row["symbol"], row["source"]) for row in response["data"]["tickers"]] == sorted(
        (row["symbol"], row["source"]) for row in response["data"]["tickers"]
    )
    assert "GOLD" not in [
        row["symbol"]
        for row in analysis._rrg_universe("all", analysis._rrg_groups_by_source())
        if row["source"] == "sjc"
    ]
    assert any(call[0] == "yahoo" and call[1] == "GOLD" for call in analysis.history.calls)


def test_rrg_mascore_all_includes_postgres_fallback_sjc_tickers():
    data = {
        ("yahoo", "GOLD", "1D"): daily_rows("yahoo", "GOLD", count=140, start=300),
        ("sjc", "GOLD", "1D"): daily_rows("sjc", "GOLD", count=140, start=400),
        ("sjc", "SJC_ONLY", "1D"): daily_rows("sjc", "SJC_ONLY", count=140, start=500),
    }
    history = FakeHistory(data)
    history.repo.tickers = lambda sources: (
        [
            {"source": "sjc", "symbol": "GOLD"},
            {"source": "sjc", "symbol": "SJC_ONLY"},
        ]
        if sources == ["sjc"]
        else []
    )

    response = Analysis(history, rrg_catalog()).rrg(
        mode="all", algorithm="mascore", trails=0, min_volume=0
    )
    identities = {(row["symbol"], row["source"]) for row in response["data"]["tickers"]}

    assert ("GOLD", "yahoo") in identities
    assert ("GOLD", "sjc") in identities
    assert ("SJC_ONLY", "sjc") in identities
    assert (
        next(row for row in response["data"]["tickers"] if row["source"] == "sjc")["sector"] is None
    )


def test_rrg_overlapping_sector_membership_uses_lexicographic_group_order():
    catalog = rrg_catalog()
    catalog.files["ticker_group.json"] = {"Zulu": ["VCB"], "Alpha": ["VCB"]}
    analysis = Analysis(FakeHistory({}), catalog)

    assert analysis._rrg_sector("vn", "VCB", analysis._rrg_groups_by_source()) == "Alpha"


def test_performer_overlapping_sector_membership_uses_lexicographic_group_order():
    catalog = rrg_catalog()
    catalog.files["ticker_group.json"] = {"Zulu": ["VCB"], "Alpha": ["VCB"]}
    analysis = Analysis(FakeHistory({}), catalog)
    analysis.latest = lambda *_args: [
        (
            "vn",
            {
                "symbol": "VCB",
                "time": "2024-01-01",
                "close": 10,
                "volume": 100,
                "close_changed": 1,
            },
        )
    ]

    response = analysis.performers(min_volume=0)

    assert response["data"]["performers"][0]["sector"] == "Alpha"


@pytest.mark.parametrize("trails", [0, 5])
def test_mascore_rrg_emits_a_source_ticker_once_for_overlapping_groups(trails):
    catalog = rrg_catalog()
    catalog.files["ticker_group.json"]["Second bank group"] = ["VCB"]
    history = FakeHistory({("vn", "VCB", "1D"): daily_rows("vn", "VCB")})

    response = Analysis(history, catalog).rrg(
        mode="vn", algorithm="mascore", trails=trails, min_volume=0
    )

    assert [row["symbol"] for row in response["data"]["tickers"]].count("VCB") == 1


def test_ma_scores_do_not_emit_python_only_all_stocks_sector():
    catalog = rrg_catalog()
    catalog.groups_by_source["vn"]["ALL_STOCKS"] = ["VCB", "WIDEVN"]
    catalog.files["ticker_group.json"]["ALL_STOCKS"] = ["VCB", "WIDEVN"]
    analysis = Analysis(FakeHistory({}), catalog)
    analysis.latest = lambda *_args: [
        (
            "vn",
            {
                "symbol": "VCB",
                "close": 100,
                "volume": 10000,
                "ma20": 90,
                "ma20_score": 11,
                "close_changed": 1,
                "volume_changed": 2,
            },
        )
    ]

    result = analysis.sectors(mode="vn")

    assert [sector["sector_name"] for sector in result["data"]["sectors"]] == ["Banks"]


def test_ma_sector_ties_use_average_then_name_and_source_and_stock_order():
    catalog = rrg_catalog()
    catalog.files["ticker_group.json"] = {
        "Zulu": ["VCB", "FPT"],
        "Same": ["VCB"],
    }
    catalog.files["global_tickers.json"] = {
        "data": [
            {"symbol": "AAPL", "category": "Same"},
            {"symbol": "DUPL", "category": "Alpha"},
        ]
    }
    analysis = Analysis(FakeHistory({}), catalog)

    def row(symbol):
        return {
            "symbol": symbol,
            "close": 10,
            "volume": 100,
            "ma20": 9,
            "ma20_score": 7,
            "close_changed": 0,
            "volume_changed": 0,
        }

    analysis.latest = lambda *_args: [
        ("vn", row("VCB")),
        ("vn", row("FPT")),
        ("yahoo", row("AAPL")),
        ("yahoo", row("DUPL")),
    ]

    response = analysis.sectors(mode="all", ma_period=20)
    sectors = response["data"]["sectors"]

    assert [(sector["sector_name"], sector["top_stocks"][0]["source"]) for sector in sectors] == [
        ("Alpha", "yahoo"),
        ("Same", "vn"),
        ("Same", "yahoo"),
        ("Zulu", "vn"),
    ]
    assert [stock["symbol"] for stock in sectors[-1]["top_stocks"]] == ["FPT", "VCB"]


@pytest.mark.parametrize(
    "row",
    [
        {
            "symbol": "VCB",
            "close": 100,
            "volume": 10000,
            "ma20": 90,
            "ma20_score": None,
        },
        {"symbol": "VCB", "close": 100, "volume": 10000, "ma20_score": 10},
    ],
)
def test_ma_scores_skip_rows_with_null_or_missing_ma_indicators(row):
    analysis = Analysis(FakeHistory({}), rrg_catalog())
    analysis.latest = lambda *_args: [("vn", row)]

    response = analysis.sectors(mode="vn", ma_period=20)

    assert response["total_analyzed"] == 0
    assert response["data"]["sectors"] == []


def test_snap_false_skips_joined_snapshot_reads_but_refreshes_the_snapshot():
    catalog = rrg_catalog()
    catalog.files["ticker_group.json"] = {"Banks": ["VCB"]}
    history = FakeHistory({("vn", "VCB", "1D"): daily_rows("vn", "VCB", count=3)})
    stale = {
        "ticker": "VCB",
        "time": "2024-01-03T00:00:00Z",
        "open": 999,
        "high": 1000,
        "low": 998,
        "close": 999,
        "volume": 10002,
    }
    redis = FakeSnapshotRedis({("snap:vn:VCB:1D", "201:sma:joined"): json.dumps([stale])})
    history.redis_client = redis
    analysis = Analysis(history, catalog)

    fresh = analysis.latest("vn", snap=False)

    assert fresh[0][1]["close"] == 102
    assert redis.hget_calls == []
    refreshed = json.loads(redis.hashes[("snap:vn:VCB:1D", "201:sma:joined")])
    assert refreshed[0]["close"] == 102
    assert redis.expiries["snap:vn:VCB:1D"] == 30

    query_count = len(history.query_calls)
    cached = analysis.latest("vn", snap=True)

    assert cached[0][1]["close"] == 102
    assert len(history.query_calls) == query_count
    assert redis.hget_calls == [("snap:vn:VCB:1D", "201:sma:joined")]


def test_jdk_rrg_prefers_live_redis_tail_over_durable_history():
    catalog = rrg_catalog()
    catalog.files["ticker_group.json"] = {"Banks": ["VCB", "VNINDEX"]}
    durable = {
        ("vn", "VCB", "1D"): daily_rows("vn", "VCB", count=140, start=200),
        ("vn", "VNINDEX", "1D"): daily_rows("vn", "VNINDEX", count=140, start=1000),
    }
    history = FakeHistory(durable)
    history.redis_client = object()
    redis_rows = {
        "VCB": daily_rows("vn", "VCB", count=140, start=500),
        "VNINDEX": daily_rows("vn", "VNINDEX", count=140, start=1000),
    }

    def cache_tail(source, symbol, interval, end=None, limit=252):
        assert source == "vn" and interval == "1D"
        return redis_rows.get(symbol, [])[-limit:]

    history.cache_tail = cache_tail
    response = Analysis(history, catalog).rrg(
        mode="vn", benchmark="VNINDEX", algorithm="jdk", period=4, trails=0, min_volume=0
    )

    ticker = response["data"]["tickers"][0]
    assert ticker["symbol"] == "VCB"
    assert ticker["close"] == 639
    assert history.calls == []


def test_latest_mascore_rrg_uses_the_101_row_joined_snapshot_key_and_redis_tail():
    catalog = rrg_catalog()
    catalog.files["ticker_group.json"] = {"Banks": ["VCB", "VNINDEX"]}
    redis = FakeSnapshotRedis()
    history = FakeHistory(
        {
            ("vn", "VCB", "1D"): daily_rows("vn", "VCB", count=140, start=100),
            ("vn", "VNINDEX", "1D"): daily_rows("vn", "VNINDEX", count=140, start=1000),
        }
    )
    history.redis_client = redis
    cache_calls = []

    def cache_tail(source, symbol, interval, end=None, limit=252):
        cache_calls.append((source, symbol, interval, limit))
        return history.data.get((source, symbol, interval), [])[-limit:]

    history.cache_tail = cache_tail
    analysis = Analysis(history, catalog)
    response = analysis.rrg(mode="vn", algorithm="mascore", trails=0, min_volume=0, snap=False)

    assert response["data"]["tickers"][0]["close"] == 239
    assert cache_calls and all(call[3] == 101 for call in cache_calls)
    assert redis.hget_calls == []
    snapshot = json.loads(redis.hashes[("snap:vn:VCB:1D", "101:sma:joined")])
    assert snapshot[0]["close"] == 239

    reads_before_cache_hit = len(cache_calls)
    cached_response = analysis.rrg(
        mode="vn", algorithm="mascore", trails=0, min_volume=0, snap=True
    )

    assert cached_response["data"]["tickers"][0]["close"] == 239
    assert len(cache_calls) == reads_before_cache_hit
    assert redis.hget_calls == [
        ("snap:vn:VCB:1D", "101:sma:joined"),
        ("snap:vn:VNINDEX:1D", "101:sma:joined"),
    ]


def test_all_mode_mascore_uses_durable_fallback_for_sjc_tickers():
    catalog = rrg_catalog()
    history = FakeHistory(
        {
            ("sjc", "SJC_ONLY", "1D"): daily_rows("sjc", "SJC_ONLY", count=140, start=500),
            ("sjc", "SJC_STALE", "1D"): daily_rows("sjc", "SJC_STALE", count=140, start=900),
        }
    )
    history.repo.tickers = lambda sources: (
        [{"source": "sjc", "symbol": "SJC_ONLY"}] if sources == ["sjc"] else []
    )
    redis = FakeSnapshotRedis()
    history.redis_client = redis
    cache_calls = []

    def cache_tail(source, symbol, interval, end=None, limit=252):
        cache_calls.append((source, symbol, interval, limit))
        return history.data.get((source, symbol, interval), [])[-limit:]

    history.cache_tail = cache_tail
    response = Analysis(history, catalog).rrg(
        mode="all", algorithm="mascore", trails=0, min_volume=0, snap=False
    )

    sjc_only = next(row for row in response["data"]["tickers"] if row["symbol"] == "SJC_ONLY")
    assert sjc_only["close"] == 639
    assert not any(source == "sjc" for source, _symbol, _interval, _limit in cache_calls)


def test_top_performers_uses_rust_curated_universe_not_registered_tickers():
    data = {}
    for symbol, start in (
        ("VCB", 100),
        ("VNINDEX", 1000),
        ("HNXINDEX", 2000),
        ("WIDEVN", 3000),
        ("UNLISTED", 4000),
    ):
        data[("vn", symbol, "1D")] = daily_rows("vn", symbol, count=3, start=start)
    history = FakeHistory(data)
    history.repo.tickers = lambda _sources: [
        {"source": "vn", "symbol": "WIDEVN"},
        {"source": "vn", "symbol": "UNLISTED"},
    ]
    analysis = Analysis(history, rrg_catalog())

    response = analysis.performers(mode="vn", min_volume=0)

    symbols = {
        row["symbol"]
        for group in (response["data"]["performers"], response["data"]["worst_performers"])
        for row in group
    }
    assert symbols == {"VCB", "HNXINDEX"}
    assert not any(call[1] in {"WIDEVN", "UNLISTED"} for call in history.query_calls)


def test_performers_fetch_latest_then_apply_rust_date_cutoff_and_invalid_date_fallback():
    rows = daily_rows("vn", "FPT", count=3)
    catalog = rrg_catalog()
    catalog.files["ticker_group.json"] = {"Banks": ["FPT"]}
    catalog.groups_by_source["vn"] = {"Banks": ["FPT"]}
    history = FakeHistory({("vn", "FPT", "1D"): rows})
    analysis = Analysis(history, catalog)

    older_date = analysis.performers(mode="vn", date="2024-01-02", min_volume=0)
    latest_date = analysis.performers(mode="vn", date="2024-01-03", min_volume=0)
    invalid_date = analysis.performers(mode="vn", date="not-a-date", min_volume=0)

    assert older_date["data"]["performers"] == []
    assert latest_date["data"]["performers"][0]["symbol"] == "FPT"
    assert invalid_date["analysis_date"] == "not-a-date"
    assert invalid_date["data"]["performers"][0]["symbol"] == "FPT"
    assert history.query_calls
    assert all(call[4] is None and call[5] == 1 for call in history.query_calls)


def test_latest_analysis_omits_one_unavailable_ticker_and_keeps_other_rows():
    catalog = rrg_catalog()
    catalog.files["ticker_group.json"] = {"Banks": ["VCB", "FPT"]}
    history = FakeHistory({("vn", "FPT", "1D"): daily_rows("vn", "FPT", count=3)})
    query = history.query

    def query_with_gap(source, symbol, *args, **kwargs):
        if symbol == "VCB":
            raise DataError("unresolved history gap")
        return query(source, symbol, *args, **kwargs)

    history.query = query_with_gap

    rows = Analysis(history, catalog).latest("vn")

    assert [(source, row["symbol"]) for source, row in rows] == [("vn", "FPT")]


def test_top_performer_ties_preserve_input_order_in_both_directions():
    analysis = Analysis(FakeHistory({}), rrg_catalog())
    analysis.latest = lambda *_args: [
        (
            "vn",
            {"symbol": "VCB", "time": "2024-01-01", "close": 10, "volume": 100, "close_changed": 1},
        ),
        (
            "vn",
            {"symbol": "FPT", "time": "2024-01-01", "close": 10, "volume": 100, "close_changed": 1},
        ),
    ]

    response = analysis.performers(mode="vn", min_volume=0)

    assert [row["symbol"] for row in response["data"]["performers"]] == ["FPT", "VCB"]
    assert [row["symbol"] for row in response["data"]["worst_performers"]] == ["FPT", "VCB"]


def test_indicator_arithmetic_matches_rust_operation_order():
    earlier_close = 84743.37369372326
    later_close = 13436.424411240123
    earlier_volume = 9_007_199_254_740_992
    later_volume = earlier_volume + 1
    rows = [
        Candle("vn", "FPT", "1D", 1704067200, 1, 1, 1, earlier_close, earlier_volume),
        Candle("vn", "FPT", "1D", 1704153600, 1, 1, 1, later_close, later_volume),
    ]

    latest = enhance(rows, "1D")[1]

    assert latest["close_changed"] == ((later_close - earlier_close) / earlier_close) * 100
    assert latest["volume_changed"] == 0.0
    assert moving_average([1e16, 1.0, -1e16], 3)[-1] == 0.0


def test_sector_analysis_uses_latest_rows_even_when_date_is_historical():
    rows = daily_rows("vn", "VCB", count=3)
    history = FakeHistory({("vn", "VCB", "1D"): rows})
    analysis = Analysis(history, rrg_catalog())

    response = analysis.sectors(mode="vn", date="2024-01-01")

    assert response["analysis_date"] == "2024-01-01"
    assert response["total_analyzed"] == 1
    assert [row["symbol"] for row in response["data"]["sectors"][0]["top_stocks"]] == ["VCB"]
    assert all(call[4] is None and call[5] == 1 for call in history.query_calls)


def test_performers_min_volume_uses_rust_vn_ticker_shape_not_source():
    analysis = Analysis(FakeHistory({}), rrg_catalog())
    analysis.latest = lambda *_args: [
        ("yahoo", {"symbol": "ABC", "time": "2024-01-01", "close": 10, "volume": 999}),
        ("vn", {"symbol": "A1A", "time": "2024-01-01", "close": 10, "volume": 999}),
    ]

    response = analysis.performers(mode="all", min_volume=1000)

    symbols = {
        row["symbol"]
        for group in (response["data"]["performers"], response["data"]["worst_performers"])
        for row in group
    }
    assert symbols == {"A1A"}


def test_performer_sectors_come_only_from_rust_vn_groups():
    catalog = rrg_catalog()
    catalog.files["ticker_group.json"] = {"Banks": ["FPT"]}
    catalog.groups_by_source["vn"] = {"Banks": ["FPT"]}
    history = FakeHistory(
        {
            ("vn", "FPT", "1D"): daily_rows("vn", "FPT", count=3),
            ("crypto", "BTCUSDT", "1D"): daily_rows("crypto", "BTCUSDT", count=3),
        }
    )
    response = Analysis(history, catalog).performers(mode="all", min_volume=0)

    performers = {
        row["symbol"]: row
        for group in (response["data"]["performers"], response["data"]["worst_performers"])
        for row in group
    }
    assert performers["FPT"]["sector"] == "Banks"
    assert performers["BTCUSDT"]["sector"] is None


def test_yahoo_analysis_uses_yahoo_rows_for_merged_sjc_group_definitions():
    history = FakeHistory({("sjc", "GOLD", "1D"): daily_rows("sjc", "GOLD", count=30)})
    analysis = Analysis(history, rrg_catalog())

    result = analysis.sectors(mode="yahoo", ma_period=20)

    assert result["total_analyzed"] == 0
    assert result["data"]["sectors"] == []
    assert ("yahoo", "GOLD", "1D", None, None, 1, False, True) in history.query_calls
    assert all(call[0] == "yahoo" for call in history.query_calls)


def test_top_performers_http_filters_the_requested_sector(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "api.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        uploads_dir=tmp_path / "uploads",
        archive_backend="filesystem",
    )
    app = create_app(settings)
    app.state.catalog.load = lambda name: {
        "ticker_group.json": {"Banks": ["VCB"]},
        "binance_tickers.json": {"data": []},
        "global_tickers.json": {"data": []},
        "sjc_tickers.json": {"data": []},
    }[name]
    app.state.catalog.groups_by_source = {
        "vn": {"Banks": ["VCB"]},
        "crypto": {},
        "yahoo": {},
        "sjc": {},
    }
    app.state.catalog.names_by_source = {
        "vn": {"VCB": "Fixture"},
        "crypto": {},
        "yahoo": {},
        "sjc": {},
    }

    with TestClient(app) as client:
        app.state.repo.put(daily_rows("vn", "VCB", count=3))
        baseline = client.get("/analysis/top-performers?min_volume=0")
        with_sector = client.get("/analysis/top-performers?min_volume=0&sector=Banks")
        with_unknown_sector = client.get(
            "/analysis/top-performers?min_volume=0&sector=not-a-real-sector"
        )
        parameters = app.openapi()["paths"]["/analysis/top-performers"]["get"]["parameters"]

    assert baseline.status_code == with_sector.status_code == with_unknown_sector.status_code == 200
    assert baseline.json()["data"]["performers"][0]["symbol"] == "VCB"
    assert with_sector.json() == baseline.json()
    assert with_unknown_sector.json()["data"]["performers"] == []
    assert "sector" in {parameter["name"] for parameter in parameters}


def test_rrg_jdk_defaults_vnindex_latest_and_last_aligned_close():
    security = daily_rows("vn", "VCB", count=180, start=100, missing_last=False)
    benchmark = daily_rows("vn", "VNINDEX", count=180, start=1000, missing_last=True)
    analysis = Analysis(
        FakeHistory({("vn", "VCB", "1D"): security, ("vn", "VNINDEX", "1D"): benchmark}),
        rrg_catalog(),
    )

    response = analysis.rrg(mode="vn", algorithm="jdk", trails=5, min_volume=0)
    ticker = next(row for row in response["data"]["tickers"] if row["symbol"] == "VCB")

    assert response["analysis_date"] == "latest"
    assert response["data"]["benchmark"] == "VNINDEX"
    assert ticker["close"] == security[-2].close
    assert ticker["volume"] == security[-1].volume


@pytest.mark.parametrize("period,trails", [(4, 0), (10, 5), (20, 20), (50, 120)])
def test_jdk_recent_trails_match_full_history_with_finite_lookback(period, trails):
    count = 900
    benchmark = [1000 + i * 0.37 + (i % 11) * 0.19 for i in range(count)]
    security = [100 + i * 0.11 + (i % 7) * 0.23 for i in range(count)]
    full = jdk(security, benchmark, period)
    limit = _jdk_lookback_limit(period, trails, ema=True)

    assert jdk(security[-limit:], benchmark[-limit:], period)[-max(1, trails) :] == full[
        -max(1, trails) :
    ]


def test_jdk_sparse_symbol_expands_both_security_and_benchmark_history():
    benchmark = daily_rows("vn", "VNINDEX", count=100, start=1000)
    security = [
        row
        for index, row in enumerate(daily_rows("vn", "VCB", count=100, start=100))
        if index not in {95, 96, 97, 98, 99}
    ]

    class ReadManyHistory(FakeHistory):
        def __init__(self, data):
            super().__init__(data)
            self.batch_limits = []

        def read_many(
            self,
            source,
            symbols,
            interval,
            start=None,
            end=None,
            limit=None,
            forward=False,
            return_exceptions=False,
        ):
            self.batch_limits.append((source, tuple(symbols), limit))
            result = {}
            for symbol in symbols:
                try:
                    result[symbol] = self.read(
                        source,
                        symbol,
                        interval,
                        start=start,
                        end=end,
                        limit=limit,
                        forward=forward,
                    )
                except Exception as exc:
                    if not return_exceptions:
                        raise
                    result[symbol] = exc
            return result

    history = ReadManyHistory(
        {("vn", "VCB", "1D"): security, ("vn", "VNINDEX", "1D"): benchmark}
    )
    response = Analysis(history, rrg_catalog()).rrg(
        mode="vn",
        algorithm="jdk",
        benchmark="VNINDEX",
        period=10,
        trails=5,
        min_volume=0,
    )

    ticker = next(row for row in response["data"]["tickers"] if row["symbol"] == "VCB")
    aligned = [row for row in security if row.time in {row.time for row in benchmark}]
    expected = jdk(
        [row.close for row in aligned],
        [row.close for row in benchmark if row.time in {item.time for item in aligned}],
        10,
    )
    assert ticker["trails"] == [
        {
            "date": datetime.fromtimestamp(row.time, UTC).strftime("%Y-%m-%d"),
            "rs_ratio": x,
            "rs_momentum": y,
        }
        for row, (x, y) in zip(aligned[-5:], expected[-5:], strict=True)
    ]
    full_rust_limit = 250 + 200
    assert any(
        symbols == ("VNINDEX",) and limit == full_rust_limit
        for _, symbols, limit in history.batch_limits
    ), history.batch_limits
    assert any(
        symbols == ("VCB",) and limit == full_rust_limit
        for _, symbols, limit in history.batch_limits
    )


def test_rrg_benchmark_lookup_is_limited_to_requested_mode():
    analysis = Analysis(
        FakeHistory(
            {
                ("vn", "VCB", "1D"): daily_rows("vn", "VCB"),
                ("crypto", "BTCUSDT", "1D"): daily_rows("crypto", "BTCUSDT"),
            }
        ),
        rrg_catalog(),
    )

    with pytest.raises(DataError, match="Benchmark 'BTCUSDT' not found") as error:
        analysis.rrg(mode="vn", algorithm="jdk", benchmark="BTCUSDT", min_volume=0)

    assert error.value.status == 400
    assert ("crypto", "BTCUSDT", "1D") not in analysis.history.calls


def test_rrg_mode_all_skips_empty_higher_priority_benchmark_source():
    cutoff = parse_time("2024-04-01")
    vn_benchmark = [
        replace(row, time=cutoff + 86400 + i * 86400)
        for i, row in enumerate(daily_rows("vn", "VNINDEX", count=10))
    ]
    data = {
        ("vn", "VCB", "1D"): daily_rows("vn", "VCB", count=180),
        ("vn", "VNINDEX", "1D"): vn_benchmark,
        ("yahoo", "AAPL", "1D"): daily_rows("yahoo", "AAPL", count=180, start=200),
        ("yahoo", "VNINDEX", "1D"): daily_rows("yahoo", "VNINDEX", count=180, start=1000),
    }

    response = Analysis(FakeHistory(data), rrg_catalog()).rrg(
        mode="all", algorithm="jdk", date="2024-04-01", trails=5, min_volume=0
    )

    assert response["data"]["benchmark"] == "VNINDEX"
    assert any(
        row["symbol"] == "AAPL" and row["source"] == "yahoo" for row in response["data"]["tickers"]
    )


def test_rrg_explicit_empty_benchmark_does_not_use_default():
    analysis = Analysis(FakeHistory({}), rrg_catalog())

    with pytest.raises(DataError, match="Benchmark '' not found") as error:
        analysis.rrg(mode="vn", algorithm="jdk", benchmark="", min_volume=0)

    assert error.value.status == 400
    assert ("vn", "", "1D") in analysis.history.calls


def test_rrg_zero_close_benchmark_rows_return_empty_success():
    security = daily_rows("vn", "VCB")
    benchmark = [replace(row, close=0) for row in daily_rows("vn", "VNINDEX")]
    analysis = Analysis(
        FakeHistory({("vn", "VCB", "1D"): security, ("vn", "VNINDEX", "1D"): benchmark}),
        rrg_catalog(),
    )

    response = analysis.rrg(mode="vn", algorithm="jdk", min_volume=0)

    assert response["data"]["tickers"] == []
    assert response["total_analyzed"] == 0


def test_mascore_backfills_early_missing_scores_from_latest_values():
    rows = daily_rows("vn", "VCB", count=120)
    rows = [replace(row, close=0) for row in rows[:100]] + rows[100:]
    analysis = Analysis(
        FakeHistory({("vn", "VCB", "1D"): rows}),
        rrg_catalog(),
    )

    response = analysis.rrg(mode="vn", algorithm="mascore", trails=120, min_volume=0)
    ticker = next(row for row in response["data"]["tickers"] if row["symbol"] == "VCB")
    first_trail = ticker["trails"][0]

    assert len(ticker["trails"]) == 120
    assert first_trail["rs_ratio"] == ticker["rs_ratio"]
    assert first_trail["rs_momentum"] == ticker["rs_momentum"]


def test_mascore_backfills_latest_missing_scores_from_earlier_rows():
    rows = daily_rows("vn", "VCB", count=110)
    rows = rows[:10] + [replace(row, close=0) for row in rows[10:]]
    history = FakeHistory({("vn", "VCB", "1D"): rows})

    response = Analysis(history, rrg_catalog()).rrg(
        mode="vn", algorithm="mascore", trails=5, min_volume=0
    )
    ticker = next(row for row in response["data"]["tickers"] if row["symbol"] == "VCB")

    assert history.limits and set(history.limits) == {205}
    assert ticker["close"] == 0
    assert ticker["trails"][-1]["rs_ratio"] == ticker["rs_ratio"]
    assert ticker["trails"][-1]["rs_momentum"] == ticker["rs_momentum"]


def test_mascore_without_trails_skips_when_latest_joined_scores_are_missing():
    catalog = rrg_catalog()
    catalog.files["global_tickers.json"] = {"data": [{"symbol": "CL=F", "category": "Futures"}]}
    rows = daily_rows("yahoo", "CL=F", count=205)
    rows = rows[:-100] + [replace(row, close=-100) for row in rows[-100:]]
    history = FakeHistory({("yahoo", "CL=F", "1D"): rows})

    response = Analysis(history, catalog).rrg(
        mode="yahoo", algorithm="mascore", trails=0, min_volume=0
    )

    assert response["data"]["tickers"] == []
    assert history.limits and set(history.limits) == {101}
    assert history.query_calls == []


def test_mascore_latest_ema_uses_rust_101_row_seed_window():
    rows = daily_rows("vn", "VCB", count=260, start=100)
    # A nonlinear sequence makes the 101-row and 260-row EMA seeds observably
    # different while keeping the latest score valid.
    rows = [replace(row, close=100 + i * i / 10) for i, row in enumerate(rows)]
    history = FakeHistory({("vn", "VCB", "1D"): rows})

    response = Analysis(history, rrg_catalog()).rrg(
        mode="vn", algorithm="mascore", trails=0, ema=True, min_volume=0, snap=False
    )
    ticker = next(row for row in response["data"]["tickers"] if row["symbol"] == "VCB")
    expected = enhance(rows[-101:], "1D", True, True)[-1]
    full_history = enhance(rows, "1D", True, True)[-1]

    assert history.limits and set(history.limits) == {101}
    assert ticker["rs_ratio"] == expected["ma20_score"]
    assert ticker["rs_momentum"] == expected["ma100_score"]
    assert expected["ma100_score"] != full_history["ma100_score"]


def test_mascore_ema_trails_use_the_same_600_row_seed_on_cache_and_database_paths():
    rows = daily_rows("vn", "VCB", count=900, start=100)
    rows = [replace(row, close=100 + i * i / 10) for i, row in enumerate(rows)]
    history = FakeHistory({("vn", "VCB", "1D"): rows})

    response = Analysis(history, rrg_catalog()).rrg(
        mode="vn", algorithm="mascore", trails=5, ema=True, min_volume=0, snap=False
    )
    ticker = next(row for row in response["data"]["tickers"] if row["symbol"] == "VCB")
    expected = enhance(rows[-605:], "1D", True, True)[-5:]

    assert history.limits and set(history.limits) == {605}
    assert len(ticker["trails"]) == 5
    assert [point["rs_ratio"] for point in ticker["trails"]] == [
        point["ma20_score"] for point in expected
    ]
    assert [point["rs_momentum"] for point in ticker["trails"]] == [
        point["ma100_score"] for point in expected
    ]


def test_jdk_uses_rust_redis_lookback_by_ema_setting():
    history = FakeHistory(
        {
            ("vn", "VCB", "1D"): daily_rows("vn", "VCB", count=500),
            ("vn", "VNINDEX", "1D"): daily_rows("vn", "VNINDEX", count=500, start=1000),
        }
    )

    Analysis(history, rrg_catalog()).rrg(
        mode="vn", algorithm="jdk", period=50, trails=120, ema=True, min_volume=0
    )

    assert history.limits == [850, 850, 850]


def test_jdk_dated_request_reads_historical_rows_before_cutoff():
    history = FakeHistory(
        {
            ("vn", "VCB", "1D"): daily_rows("vn", "VCB", count=1000),
            ("vn", "VNINDEX", "1D"): daily_rows("vn", "VNINDEX", count=1000, start=1000),
        }
    )
    old_date = (datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=100)).strftime("%Y-%m-%d")

    response = Analysis(history, rrg_catalog()).rrg(
        mode="vn", algorithm="jdk", date=old_date, ema=True, min_volume=0
    )

    assert history.limits == [850, 850, 850]
    assert [row["symbol"] for row in response["data"]["tickers"]] == ["VCB"]
    assert all(
        point["date"] <= old_date for row in response["data"]["tickers"] for point in row["trails"]
    )


@pytest.mark.parametrize(
    ("mode", "expected_source"),
    [("all", "vn"), ("unknown", "vn"), ("YAHOO", "yahoo"), ("CrYpTo", "crypto")],
)
def test_volume_profile_uses_rust_mode_fallback(mode, expected_source):
    row = Candle(
        expected_source, "FPT", "1m", parse_time("2024-02-01T02:30:00Z"), 100, 101, 99, 100, 100
    )
    history = FakeHistory({(expected_source, "FPT", "1m"): [row]})
    result = Analysis(history, rrg_catalog()).profile("FPT", mode=mode, date="2024-02-01")

    assert history.calls == [(expected_source, "FPT", "1m")]
    assert result["analysis_type"] == "volume_profile"


def test_volume_profile_uses_partial_overlapping_retained_tail_like_rust():
    day = parse_time("2024-02-01T02:30:00Z")
    rows = [
        Candle("vn", "FPT", "1m", day, 100, 101, 99, 100, 100),
        Candle("vn", "FPT", "1m", day + 60, 100, 101, 99, 100, 100),
    ]
    history = FakeHistory({("vn", "FPT", "1m"): rows})
    history.settings = SimpleNamespace(redis_minute_max_size=1)
    history.cache_tail = lambda *_args, **kwargs: rows[-kwargs["limit"] :]

    result = Analysis(history, rrg_catalog()).profile("FPT", date="2024-02-01")

    assert result["total_analyzed"] == 1
    assert result["data"]["total_minutes"] == 1
    assert history.calls == []


def test_chrono_date_parser_accepts_unpadded_month_and_day():
    assert date_bounds("2024-2-1") == date_bounds("2024-02-01")
    assert date_bounds("2024-2-1") == parse_time("2024-02-01T00:00:00Z")
    assert date_bounds("2024-2-1", end=True) == date_bounds("2024-02-01", end=True)
    assert format_chrono_date("2024-2-1") == "2024-02-01"
    assert format_chrono_date("0000-1-1") == "+0000-01-01"
    with pytest.raises(DataError, match="Invalid date format"):
        date_bounds("2024-2-30")


def test_analysis_date_parsing_matches_chrono_padding_and_echo_rules():
    minute = Candle("vn", "FPT", "1m", parse_time("2024-02-01T02:30:00Z"), 100, 101, 99, 100, 100)
    profile = Analysis(FakeHistory({("vn", "FPT", "1m"): [minute]}), rrg_catalog()).profile(
        "FPT", date="2024-2-1"
    )
    assert profile["analysis_date"] == "2024-2-1"

    daily = daily_rows("vn", "VCB", 140)
    benchmark = daily_rows("vn", "VNINDEX", 140)
    rrg = Analysis(
        FakeHistory({("vn", "VCB", "1D"): daily, ("vn", "VNINDEX", "1D"): benchmark}),
        rrg_catalog(),
    ).rrg(
        mode="vn",
        date="2024-2-1",
        benchmark="VNINDEX",
        period=10,
        trails=1,
        min_volume=0,
    )
    assert rrg["analysis_date"] == "2024-02-01"


def test_sma_matches_rust_full_period_and_short_series_behavior():
    assert moving_average([10, 20, 30], 5) == [10, 15, 20]
    assert moving_average([10, 20, 30, 40, 50], 3) == [0, 0, 20, 30, 40]


def test_volume_profile_negative_prices_round_half_away_from_zero():
    row = Candle(
        "yahoo",
        "CL=F",
        "1m",
        parse_time("2024-02-01T02:30:00Z"),
        -0.0001,
        -1 * 0.00005,
        -3 * 0.00005,
        -0.0001,
        100,
    )

    result = volume_profile([row], "CL=F", "yahoo")

    assert [level["price"] for level in result["profile"]] == pytest.approx([-0.0002, -0.0001])


def test_volume_profile_normalizes_rows_to_rust_newest_first_accumulation():
    origin = parse_time("2024-02-01T02:30:00Z")
    prices = [1e16, 10023, -1e16, 29977]
    rows = [
        Candle("vn", "FPT", "1m", origin + i * 60, price, price, price, price, 1)
        for i, price in enumerate(prices)
    ]

    chronological = volume_profile(rows, "FPT", "vn", bins=200)
    newest_first = volume_profile(list(reversed(rows)), "FPT", "vn", bins=200)

    assert chronological == newest_first
    prices_in_profile = {level["price"] for level in chronological["profile"]}
    assert 10000 in prices_in_profile


def test_volume_profile_uses_rust_vn_index_tick_list():
    row = Candle(
        "vn", "HNXINDEX", "1m", parse_time("2024-02-01T02:30:00Z"), 1002, 1002, 1002, 1002, 100
    )
    index_row = replace(row, symbol="VNINDEX")

    ordinary_alias = volume_profile([row], "HNXINDEX", "vn")
    rust_index = volume_profile([index_row], "VNINDEX", "vn")

    assert ordinary_alias["profile"][0]["price"] == 1000
    assert rust_index["profile"][0]["price"] == 1002


@pytest.fixture
def api_client(tmp_path):
    from aipriceaction_api.app import create_app

    settings = replace(
        Settings(),
        database=tmp_path / "analysis.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, app


def test_volume_profile_no_rows_matches_rust_not_found_body(api_client):
    client, _app = api_client

    response = client.get("/analysis/volume-profile?symbol=FPT&date=2024-02-01")

    assert response.status_code == 404
    assert response.json() == {"error": "No minute data found for FPT on 2024-02-01"}


def test_volume_profile_all_zero_rows_matches_rust_ok_error_body(api_client):
    client, app = api_client
    app.state.repo.put(
        [
            Candle(
                "vn",
                "FPT",
                "1m",
                parse_time("2024-02-01T02:30:00Z"),
                100,
                101,
                99,
                100,
                0,
            )
        ]
    )

    response = client.get("/analysis/volume-profile?symbol=FPT&date=2024-02-01")

    assert response.status_code == 200
    assert response.json() == {"error": "No volume profile data generated"}


def test_volume_profile_rejects_extreme_per_bar_price_range():
    row = Candle("vn", "FPT", "1m", parse_time("2024-02-01T02:30:00Z"), 1, 200_000_000, 0, 1, 100)

    with pytest.raises(DataError, match="resource limit") as error:
        volume_profile([row], "FPT", "vn")

    assert error.value.status == 400


def test_volume_profile_matches_rust_empty_profile_error_for_invalid_range():
    row = Candle("vn", "FPT", "1m", parse_time("2024-02-01T02:30:00Z"), 100, 80, 120, 100, 100)
    analysis = Analysis(FakeHistory({("vn", "FPT", "1m"): [row]}), rrg_catalog())

    assert analysis.profile("FPT", date="2024-02-01") == {
        "error": "No volume profile data generated"
    }


def test_volume_profile_nan_value_area_keeps_only_the_poc_bin(api_client):
    client, app = api_client
    origin = parse_time("2024-02-01T02:30:00Z")
    app.state.repo.put(
        [
            Candle("vn", "FPT", "1m", origin, 100, 100, 100, 100, 100),
            Candle("vn", "FPT", "1m", origin + 60, 200, 200, 200, 200, 50),
        ]
    )

    response = client.get("/analysis/volume-profile?symbol=FPT&date=2024-02-01&value_area_pct=nan")

    assert response.status_code == 200
    area = response.json()["data"]["value_area"]
    assert area["low"] == area["high"] == 100
    assert area["volume"] == 100
