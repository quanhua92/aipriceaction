import json
import re

import pandas as pd
import pytest
import responses

from aipriceaction import AIPriceAction


# ── Fixtures ──

@pytest.fixture
def mock_live():
    """Mock live API endpoint for 1D interval."""
    responses.get(
        "http://localhost:9000/tickers?interval=1D&mode=all&format=json&limit=1&ma=false",
        json={
            "VCB": [
                {
                    "time": "2025-04-29",
                    "open": 58000,
                    "high": 58500,
                    "low": 57500,
                    "close": 58200,
                    "volume": 2000000,
                    "symbol": "VCB",
                    "close_changed": 2.3,
                    "volume_changed": 1.1,
                    "total_money_changed": 50000,
                }
            ],
            "FPT": [
                {
                    "time": "2025-04-29",
                    "open": 147000,
                    "high": 148000,
                    "low": 146000,
                    "close": 147500,
                    "volume": 900000,
                    "symbol": "FPT",
                    "close_changed": 1.0,
                    "volume_changed": 0.5,
                    "total_money_changed": 10000,
                }
            ],
        },
    )


@pytest.fixture
def client_live(tmp_path):
    return AIPriceAction(
        "http://localhost:9000/aipriceaction-archive",
        cache_dir=str(tmp_path),
        use_live=True,
        live_url="http://localhost:9000",
        utc_offset=0,
    )


@pytest.fixture
def mock_s3_base():
    """Minimal S3 mock for tests that only need tickers.json."""
    responses.start()
    responses.get(
        "http://localhost:9000/aipriceaction-archive/meta/tickers.json",
        json=[
            {"source": "vn", "ticker": "VCB", "name": "Vietcombank", "group": "BANK"},
            {"source": "vn", "ticker": "FPT", "name": "FPT Corp", "group": "TECH"},
            {"source": "crypto", "ticker": "BTCUSDT", "name": "Bitcoin", "group": "CRYPTO_TOP_100"},
        ],
    )
    responses.head(
        "http://localhost:9000/aipriceaction-archive/meta/tickers.json",
        headers={"x-amz-meta-content-hash": "tickers-hash-live"},
    )
    # Yearly files: 404
    responses.get(
        re.compile(r"http://localhost:9000/aipriceaction-archive/ohlcv/.*/yearly/.*\.csv"),
        status=404,
    )
    responses.head(
        re.compile(r"http://localhost:9000/aipriceaction-archive/ohlcv/.*/yearly/.*\.csv"),
        status=404,
    )
    yield
    responses.stop()
    responses.reset()


# ── Init tests ──

class TestLiveInit:
    def test_init_use_live_default_false(self, tmp_path):
        c = AIPriceAction("http://example.com", cache_dir=str(tmp_path))
        assert c.use_live is False
        assert c._live_cache == {}

    def test_init_use_live_true(self, tmp_path):
        c = AIPriceAction(
            "http://example.com", cache_dir=str(tmp_path), use_live=True
        )
        assert c.use_live is True
        assert c._live_url == "https://api.aipriceaction.com"
        assert c._live_cache == {}

    def test_init_custom_live_url(self, tmp_path):
        c = AIPriceAction(
            "http://example.com",
            cache_dir=str(tmp_path),
            use_live=True,
            live_url="https://my-api.com/",
        )
        assert c._live_url == "https://my-api.com"


# ── _fetch_live_data tests ──

class TestFetchLiveData:
    def test_fetch_live_data_success(self, client_live):
        responses.start()
        data = {"VCB": [{"time": "2025-04-29", "open": 58000, "high": 58500, "low": 57500, "close": 58200, "volume": 2000000, "symbol": "VCB"}]}
        responses.get(
            "http://localhost:9000/tickers?interval=1D&mode=all&format=json&limit=1&ma=false",
            json=data,
        )
        result = client_live._fetch_live_data("1D")
        assert result is not None
        assert "VCB" in result
        assert client_live._live_cache["1D"]["data"] is result
        responses.stop()
        responses.reset()

    def test_fetch_live_data_cached(self, client_live):
        responses.start()
        data = {"VCB": [{"time": "2025-04-29", "open": 58000, "high": 58500, "low": 57500, "close": 58200, "volume": 2000000, "symbol": "VCB"}]}
        call_count = 0

        def callback(request):
            nonlocal call_count
            call_count += 1
            return (200, {}, json.dumps(data))

        responses.add_callback(
            responses.GET,
            "http://localhost:9000/tickers?interval=1D&mode=all&format=json&limit=1&ma=false",
            callback=callback,
            content_type="application/json",
        )
        result1 = client_live._fetch_live_data("1D")
        result2 = client_live._fetch_live_data("1D")
        assert call_count == 1
        assert result1 is result2
        responses.stop()
        responses.reset()

    def test_fetch_live_data_stale_fallback(self, client_live):
        """API error returns stale cached data."""
        responses.start()
        data = {"VCB": [{"time": "2025-04-29", "open": 58000, "high": 58500, "low": 57500, "close": 58200, "volume": 2000000, "symbol": "VCB"}]}
        responses.get(
            "http://localhost:9000/tickers?interval=1D&mode=all&format=json&limit=1&ma=false",
            json=data,
        )
        client_live._fetch_live_data("1D")

        # Expire cache
        client_live._live_cache["1D"]["fetched_at"] = 0

        # Second call fails
        responses.reset()
        responses.get(
            "http://localhost:9000/tickers?interval=1D&mode=all&format=json&limit=1&ma=false",
            status=500,
        )
        stale = client_live._fetch_live_data("1D")
        assert stale is not None
        assert "VCB" in stale
        responses.stop()
        responses.reset()

    def test_fetch_live_data_no_cache_on_error(self, client_live):
        """No cache + API error returns None."""
        responses.start()
        responses.get(
            "http://localhost:9000/tickers?interval=1D&mode=all&format=json&limit=1&ma=false",
            status=500,
        )
        result = client_live._fetch_live_data("1D")
        assert result is None
        responses.stop()
        responses.reset()

    def test_fetch_live_data_correct_limit_per_interval(self, client_live):
        """Verify URL contains correct limit for each interval."""
        responses.start()
        urls_seen = []

        def callback(request):
            urls_seen.append(request.url)
            return (200, {}, json.dumps({}))

        responses.add_callback(
            responses.GET,
            re.compile(r"http://localhost:9000/tickers\?"),
            callback=callback,
            content_type="application/json",
        )

        for interval in ("1D", "1h", "1m"):
            client_live._fetch_live_data(interval)

        assert len(urls_seen) == 3
        assert "limit=1" in urls_seen[0]
        assert "interval=1D" in urls_seen[0]
        assert "limit=5" in urls_seen[1]
        assert "interval=1h" in urls_seen[1]
        assert "limit=60" in urls_seen[2]
        assert "interval=1m" in urls_seen[2]
        responses.stop()
        responses.reset()


# ── _merge_live_data tests ──

class TestMergeLiveData:
    def test_merge_live_overwrites_last_candle(self, client_live):
        s3_df = pd.DataFrame({
            "time": ["2025-04-28 00:00:00", "2025-04-29 00:00:00"],
            "open": [57284.56, 57284.56],
            "high": [57880.24, 58078.80],
            "low": [57086.00, 56887.44],
            "close": [57086.00, 56887.44],
            "volume": [1657552, 2437717],
            "symbol": ["VCB", "VCB"],
        })
        live_data = {
            "VCB": [
                {
                    "time": "2025-04-29",
                    "open": 58000,
                    "high": 58500,
                    "low": 57500,
                    "close": 58200,
                    "volume": 2000000,
                }
            ]
        }
        result = client_live._merge_live_data(s3_df, live_data, [])
        assert len(result) == 2
        assert result.iloc[0]["close"] == 57086.00  # S3 row preserved
        assert result.iloc[1]["close"] == 58200  # Live row overwrites

    def test_merge_live_appends_new_candle(self, client_live):
        s3_df = pd.DataFrame({
            "time": ["2025-04-28 00:00:00"],
            "open": [57284.56],
            "high": [57880.24],
            "low": [57086.00],
            "close": [57086.00],
            "volume": [1657552],
            "symbol": ["VCB"],
        })
        live_data = {
            "VCB": [
                {
                    "time": "2025-04-29",
                    "open": 58000,
                    "high": 58500,
                    "low": 57500,
                    "close": 58200,
                    "volume": 2000000,
                }
            ]
        }
        result = client_live._merge_live_data(s3_df, live_data, [])
        assert len(result) == 2
        assert result.iloc[0]["time"] == "2025-04-28 00:00:00"
        assert result.iloc[1]["time"] == "2025-04-29"

    def test_merge_live_drops_extra_columns(self, client_live):
        s3_df = pd.DataFrame({
            "time": ["2025-04-29 00:00:00"],
            "open": [57284.56],
            "high": [58078.80],
            "low": [56887.44],
            "close": [56887.44],
            "volume": [2437717],
            "symbol": ["VCB"],
        })
        live_data = {
            "VCB": [
                {
                    "time": "2025-04-29",
                    "open": 58000,
                    "high": 58500,
                    "low": 57500,
                    "close": 58200,
                    "volume": 2000000,
                    "symbol": "VCB",
                    "close_changed": 2.3,
                    "volume_changed": 1.1,
                    "total_money_changed": 50000,
                }
            ]
        }
        result = client_live._merge_live_data(s3_df, live_data, [])
        assert "close_changed" not in result.columns
        assert "volume_changed" not in result.columns
        assert "total_money_changed" not in result.columns

    def test_merge_live_missing_ticker_unchanged(self, client_live):
        s3_df = pd.DataFrame({
            "time": ["2025-04-29 00:00:00"],
            "open": [57284.56],
            "high": [58078.80],
            "low": [56887.44],
            "close": [56887.44],
            "volume": [2437717],
            "symbol": ["VCB"],
        })
        live_data = {
            "FPT": [
                {
                    "time": "2025-04-29",
                    "open": 147000,
                    "high": 148000,
                    "low": 146000,
                    "close": 147500,
                    "volume": 900000,
                }
            ]
        }
        result = client_live._merge_live_data(s3_df, live_data, [])
        assert len(result) == 1
        assert result.iloc[0]["close"] == 56887.44  # Unchanged

    def test_merge_empty_s3_uses_live(self, client_live):
        s3_df = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume", "symbol"])
        live_data = {
            "VCB": [
                {
                    "time": "2025-04-29",
                    "open": 58000,
                    "high": 58500,
                    "low": 57500,
                    "close": 58200,
                    "volume": 2000000,
                }
            ]
        }
        result = client_live._merge_live_data(s3_df, live_data, [])
        assert len(result) == 0

    def test_merge_live_no_duplicate_with_t_separator(self, client_live):
        """S3 uses space format, live API uses T-format for same timestamp → dedup."""
        s3_df = pd.DataFrame({
            "time": ["2026-05-04 09:00:00"],
            "open": [49000],
            "high": [49500],
            "low": [48800],
            "close": [49200],
            "volume": [500000],
            "symbol": ["VIC"],
        })
        live_data = {
            "VIC": [
                {
                    "time": "2026-05-04T09:00:00",
                    "open": 49100,
                    "high": 49600,
                    "low": 48900,
                    "close": 49300,
                    "volume": 550000,
                }
            ]
        }
        result = client_live._merge_live_data(s3_df, live_data, [])
        # Only one row: live overwrites S3, no duplicate
        assert len(result) == 1
        assert result.iloc[0]["close"] == 49300

    def test_merge_live_1h_t_separator_dedup(self, client_live):
        """Multiple 1h bars, last bar overlaps between S3 (space) and live (T)."""
        s3_df = pd.DataFrame({
            "time": ["2026-05-04 07:00:00", "2026-05-04 08:00:00", "2026-05-04 09:00:00"],
            "open": [48000, 48500, 49000],
            "high": [48400, 48900, 49500],
            "low": [47900, 48400, 48800],
            "close": [48300, 48800, 49200],
            "volume": [300000, 400000, 500000],
            "symbol": ["VIC", "VIC", "VIC"],
        })
        live_data = {
            "VIC": [
                {
                    "time": "2026-05-04T09:00:00",
                    "open": 49100,
                    "high": 49600,
                    "low": 48900,
                    "close": 49300,
                    "volume": 550000,
                }
            ]
        }
        result = client_live._merge_live_data(s3_df, live_data, [])
        # 3 unique bars: first two from S3, last replaced by live
        assert len(result) == 3
        times = result["time"].tolist()
        assert len(times) == len(set(times)), "No duplicate times"
        assert result.iloc[2]["close"] == 49300  # Live value


# ── _parse_csv tests ──

class TestParseCsv:
    def test_parse_csv_normalizes_t_separator(self):
        """CSV with T-separator in time column gets normalized to space."""
        csv_text = "2026-05-04T09:00:00,49100,49600,48900,49300,550000"
        df = AIPriceAction._parse_csv(csv_text)
        assert df is not None
        assert df.iloc[0]["time"] == "2026-05-04 09:00:00"

    def test_parse_csv_space_separator_unchanged(self):
        """CSV with space separator in time column is kept as-is."""
        csv_text = "2026-05-04 09:00:00,49100,49600,48900,49300,550000"
        df = AIPriceAction._parse_csv(csv_text)
        assert df is not None
        assert df.iloc[0]["time"] == "2026-05-04 09:00:00"


# ── Integration tests ──

class TestGetOhlcvLive:
    def test_get_ohlcv_live_disabled_no_call(self, mock_s3_base, tmp_path):
        """use_live=False — live API is never called."""
        client = AIPriceAction(
            "http://localhost:9000/aipriceaction-archive",
            cache_dir=str(tmp_path),
            use_live=False,
        )
        # Add S3 data
        responses.get(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-29.csv",
            body="2025-04-29 00:00:00,57284.56,58078.80,56887.44,56887.44,2437717",
        )
        responses.head(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-29.csv",
            headers={"x-amz-meta-content-hash": "vcb-hash"},
        )
        df = client.get_ohlcv("VCB", interval="1D", start_date="2025-04-29", end_date="2025-04-29", ma=False)
        assert len(df) == 1
        assert df.iloc[0]["close"] == 56887.44

    def test_get_ohlcv_live_overlays_data(self, mock_s3_base, mock_live, client_live):
        """Live data overwrites S3 data when use_live=True."""
        responses.get(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-28.csv",
            body="2025-04-28 00:00:00,57284.56,57880.24,57086.00,57086.00,1657552",
        )
        responses.head(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-28.csv",
            headers={"x-amz-meta-content-hash": "vcb-0428-hash"},
        )
        responses.get(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-29.csv",
            body="2025-04-29 00:00:00,57284.56,58078.80,56887.44,56887.44,2437717",
        )
        responses.head(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-29.csv",
            headers={"x-amz-meta-content-hash": "vcb-0429-hash"},
        )

        df = client_live.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-28", end_date="2025-04-29",
            ma=False,
        )
        assert len(df) == 2
        assert df.iloc[0]["close"] == 57086.00  # S3 row preserved
        assert df.iloc[1]["close"] == 58200  # Live row overwrites

    def test_get_ohlcv_skips_aggregated_interval(self):
        """Aggregated intervals are not in the live native set."""
        from aipriceaction.client import _LIVE_NATIVE_INTERVALS

        assert "5m" not in _LIVE_NATIVE_INTERVALS
        assert "15m" not in _LIVE_NATIVE_INTERVALS
        assert "1D" in _LIVE_NATIVE_INTERVALS
        assert "1h" in _LIVE_NATIVE_INTERVALS
        assert "1m" in _LIVE_NATIVE_INTERVALS


# ── UTC offset tests ──

class TestUtcOffset:
    def test_utc_offset_default_converts_hourly(self, mock_s3_base, tmp_path):
        """Default utc_offset=7 converts hourly UTC+0 time to UTC+7."""
        responses.start()
        responses.get(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1h/VCB-1h-2025-04-29.csv",
            body="2025-04-29T04:00:00,58000,58500,57500,58200,2000000",
        )
        responses.head(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1h/VCB-1h-2025-04-29.csv",
            headers={"x-amz-meta-content-hash": "vcb-1h-hash"},
        )
        client = AIPriceAction(
            "http://localhost:9000/aipriceaction-archive",
            cache_dir=str(tmp_path),
            use_live=False,
            utc_offset=7,
        )
        df = client.get_ohlcv("VCB", interval="1h", start_date="2025-04-29", end_date="2025-04-29", ma=False)
        assert len(df) == 1
        assert df.iloc[0]["time"] == "2025-04-29 11:00:00"
        responses.stop()
        responses.reset()

    def test_utc_offset_0_keeps_utc(self, mock_s3_base, tmp_path):
        """utc_offset=0 keeps raw UTC time strings unchanged."""
        responses.start()
        responses.get(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1h/VCB-1h-2025-04-29.csv",
            body="2025-04-29T04:00:00,58000,58500,57500,58200,2000000",
        )
        responses.head(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1h/VCB-1h-2025-04-29.csv",
            headers={"x-amz-meta-content-hash": "vcb-1h-hash-utc0"},
        )
        client = AIPriceAction(
            "http://localhost:9000/aipriceaction-archive",
            cache_dir=str(tmp_path),
            use_live=False,
            utc_offset=0,
        )
        df = client.get_ohlcv("VCB", interval="1h", start_date="2025-04-29", end_date="2025-04-29", ma=False)
        assert len(df) == 1
        assert df.iloc[0]["time"] == "2025-04-29 04:00:00"
        responses.stop()
        responses.reset()

    def test_utc_offset_date_only(self, mock_s3_base, tmp_path):
        """1D interval shows date only in configured offset."""
        responses.start()
        responses.get(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-29.csv",
            body="2025-04-29 00:00:00,58000,58500,57500,58200,2000000",
        )
        responses.head(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-29.csv",
            headers={"x-amz-meta-content-hash": "vcb-1d-hash-utc9"},
        )
        client = AIPriceAction(
            "http://localhost:9000/aipriceaction-archive",
            cache_dir=str(tmp_path),
            use_live=False,
            utc_offset=9,
        )
        df = client.get_ohlcv("VCB", interval="1D", start_date="2025-04-29", end_date="2025-04-29", ma=False)
        assert len(df) == 1
        assert df.iloc[0]["time"] == "2025-04-29"
        responses.stop()
        responses.reset()

    def test_utc_offset_intraday(self, mock_s3_base, tmp_path):
        """1h interval shows full datetime in configured offset."""
        responses.start()
        responses.get(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1h/VCB-1h-2025-04-29.csv",
            body="2025-04-29T14:30:00,58000,58500,57500,58200,2000000",
        )
        responses.head(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1h/VCB-1h-2025-04-29.csv",
            headers={"x-amz-meta-content-hash": "vcb-1h-hash-v2"},
        )
        client = AIPriceAction(
            "http://localhost:9000/aipriceaction-archive",
            cache_dir=str(tmp_path),
            use_live=False,
            utc_offset=5,
        )
        df = client.get_ohlcv("VCB", interval="1h", start_date="2025-04-29", end_date="2025-04-29", ma=False)
        assert len(df) == 1
        assert df.iloc[0]["time"] == "2025-04-29 19:30:00"
        responses.stop()
        responses.reset()
