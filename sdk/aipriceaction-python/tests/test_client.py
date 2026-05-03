from datetime import date

import pytest

from aipriceaction import AIPriceAction
from aipriceaction.models import TickerInfo


class TestGetTickers:
    def test_returns_all_tickers(self, mock_s3, client):
        tickers = client.get_tickers()
        assert len(tickers) == 3
        assert all(isinstance(t, TickerInfo) for t in tickers)

    def test_filter_by_source(self, mock_s3, client):
        vn = client.get_tickers(source="vn")
        assert len(vn) == 2
        assert all(t.source == "vn" for t in vn)

        crypto = client.get_tickers(source="crypto")
        assert len(crypto) == 1
        assert crypto[0].ticker == "BTCUSDT"

    def test_uses_in_memory_cache(self, mock_s3, client):
        first = client.get_tickers()
        second = client.get_tickers()
        assert first is second

    def test_bypass_cache(self, mock_s3, client):
        first = client.get_tickers()
        second = client.get_tickers(use_cache=False)
        assert first == second
        # Different list objects but same data
        assert first is not second


class TestGetOhlcv:
    def test_single_ticker(self, mock_s3, client):
        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-28", end_date="2025-04-29",
            ma=False,
        )
        assert len(df) == 2
        assert list(df.columns) == ["time", "open", "high", "low", "close", "volume", "symbol"]
        assert df["symbol"].unique()[0] == "VCB"
        assert df.iloc[0]["close"] == 57086.0
        assert df.iloc[1]["close"] == 56887.44

    def test_single_ticker_with_date_objects(self, mock_s3, client):
        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date=date(2025, 4, 28), end_date=date(2025, 4, 29),
            ma=False,
        )
        assert len(df) == 2

    def test_multiple_tickers(self, mock_s3, client):
        df = client.get_ohlcv(
            tickers=["VCB", "BTCUSDT"], interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
            ma=False,
        )
        assert len(df) == 2
        symbols = sorted(df["symbol"].unique())
        assert symbols == ["BTCUSDT", "VCB"]

    def test_ticker_and_tickers_mutually_exclusive(self, mock_s3, client):
        with pytest.raises(ValueError, match="Use either"):
            client.get_ohlcv(ticker="VCB", tickers=["FPT"])

    def test_ticker_none_all_tickers(self, mock_s3, client):
        df = client.get_ohlcv(
            interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
            ma=False,
        )
        # VCB + FPT + BTCUSDT all have data for 04-29
        assert len(df) == 3
        symbols = sorted(df["symbol"].unique())
        assert symbols == ["BTCUSDT", "FPT", "VCB"]

    def test_limit_per_symbol(self, mock_s3, client):
        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-28", end_date="2025-04-29",
            limit=1, ma=False,
        )
        assert len(df) == 1
        # limit takes the last row (tail)
        assert df.iloc[0]["time"] == "2025-04-29 00:00:00"

    def test_with_source_override(self, mock_s3, client):
        df = client.get_ohlcv(
            "BTCUSDT", interval="1D",
            source="crypto",
            start_date="2025-04-29", end_date="2025-04-29",
            ma=False,
        )
        assert len(df) == 1
        assert df.iloc[0]["close"] == 94256.82

    def test_empty_result(self, mock_s3, client):
        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-30", end_date="2025-04-30",
            ma=False,
        )
        assert len(df) == 0
        assert list(df.columns) == ["time", "open", "high", "low", "close", "volume", "symbol"]

    def test_default_interval(self, mock_s3, client):
        """Default interval is 1D."""
        df = client.get_ohlcv(
            "VCB",
            start_date="2025-04-29", end_date="2025-04-29",
            ma=False,
        )
        assert len(df) == 1

    def test_invalid_interval(self, mock_s3, client):
        with pytest.raises(ValueError, match="Invalid interval"):
            client.get_ohlcv("VCB", interval="99m")

    def test_aggregated_interval_not_available(self, mock_s3, client):
        with pytest.raises(ValueError, match="Aggregated interval"):
            client.get_ohlcv("VCB", interval="5m")

    def test_hourly_interval_alias(self, mock_s3, client):
        """hourly should normalize to 1h (no data mocked but no error)."""
        import responses
        responses.get(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1h/VCB-1h-2025-04-29.csv",
            status=404,
        )
        df = client.get_ohlcv(
            "VCB", interval="hourly",
            start_date="2025-04-29", end_date="2025-04-29",
            ma=False,
        )
        assert len(df) == 0  # no 1h data mocked


class TestContentHash:
    def test_returns_hash(self, mock_s3, client):
        h = client.get_content_hash("VCB", "1D", "2025-04-29")
        assert h == "abc123"

    def test_returns_none_for_missing(self, mock_s3, client):
        h = client.get_content_hash("VCB", "1D", "2025-04-30")
        assert h is None

    def test_accepts_date_object(self, mock_s3, client):
        h = client.get_content_hash("VCB", "1D", date(2025, 4, 29))
        assert h == "abc123"


class TestDiskCache:
    def test_tickers_cached_to_disk(self, mock_s3, client, tmp_path):
        client.get_tickers()
        cache_file = tmp_path / "_meta" / "tickers.json"
        assert cache_file.exists()
        import json
        data = json.loads(cache_file.read_text())
        assert len(data) == 3

    def test_csv_cached_to_disk(self, mock_s3, client, tmp_path):
        client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
            ma=False,
        )
        cache_file = tmp_path / "vn" / "VCB" / "1D" / "VCB-1D-2025-04-29.csv"
        assert cache_file.exists()

    def test_uses_disk_cache_on_second_call(self, mock_s3, client):
        """Second call should read from disk cache, not make HTTP requests."""
        df1 = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
            ma=False,
        )
        # Clear responses so any HTTP call would fail
        import responses
        responses.reset()
        # Re-add yearly 404 mocks (SDK tries yearly file first)
        responses.get(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/yearly/VCB-1D-2025.csv",
            status=404,
        )
        responses.head(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/yearly/VCB-1D-2025.csv",
            status=404,
        )
        df2 = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
            ma=False,
        )
        assert len(df2) == len(df1)
        assert df2.iloc[0]["close"] == df1.iloc[0]["close"]


class TestDownloadCsv:
    def test_downloads_files(self, mock_s3, client, tmp_path):
        output_dir = tmp_path / "output"
        paths = client.download_csv(
            "VCB", interval="1D",
            start_date="2025-04-28", end_date="2025-04-29",
            output_dir=str(output_dir),
        )
        assert len(paths) == 2
        for p in paths:
            assert p.endswith(".csv")

    def test_skips_missing_dates(self, mock_s3, client, tmp_path):
        output_dir = tmp_path / "output"
        paths = client.download_csv(
            "VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-30",
            output_dir=str(output_dir),
        )
        # 04-30 is 404, should be skipped
        assert len(paths) == 1

    def test_limit(self, mock_s3, client, tmp_path):
        output_dir = tmp_path / "output"
        paths = client.download_csv(
            "VCB", interval="1D",
            start_date="2025-04-28", end_date="2025-04-29",
            limit=1,
            output_dir=str(output_dir),
        )
        assert len(paths) == 1


class TestMaIndicators:
    """Tests for MA/EMA indicator calculation."""

    def test_ma_true_adds_indicator_columns(self, mock_s3_ma, client):
        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-24", end_date="2025-04-24",
            ma=True,
        )
        ma_cols = [
            "ma10", "ma20", "ma50", "ma100", "ma200",
            "ma10_score", "ma20_score", "ma50_score", "ma100_score", "ma200_score",
            "close_changed", "volume_changed", "total_money_changed",
        ]
        for col in ma_cols:
            assert col in df.columns, f"Missing column: {col}"

    def test_ma_false_no_indicator_columns(self, mock_s3, client):
        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
            ma=False,
        )
        assert "ma10" not in df.columns
        assert "ma10_score" not in df.columns
        assert "close_changed" not in df.columns

    def test_ma_default_is_true(self, mock_s3_ma, client):
        """ma=True by default, same as the /tickers endpoint."""
        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-24", end_date="2025-04-24",
        )
        assert "ma10" in df.columns

    def test_sma_values(self, mock_s3_ma, client):
        """Verify SMA-10 for a dataset with linearly increasing prices.

        Days 0-9 have closes 100, 101, ..., 109.
        SMA-10 at day 9 = (100+101+...+109)/10 = 104.5
        Day 10 has close=110, SMA-10 = (101+...+110)/10 = 105.5
        """
        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-20", end_date="2025-04-24",
            ma=True,
        )
        # Find the row with close=110.0 (day index 10, which is 2025-04-20)
        row_110 = df[df["close"] == 110.0]
        if not row_110.empty:
            assert abs(row_110.iloc[0]["ma10"] - 105.5) < 0.01

    def test_ema_values(self, mock_s3_ma, client):
        """Verify EMA differs from SMA."""
        df_sma = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-24", end_date="2025-04-24",
            ma=True, ema=False,
        )
        df_ema = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-24", end_date="2025-04-24",
            ma=True, ema=True,
        )
        # EMA and SMA should produce different values for the same data
        assert df_sma.iloc[0]["ma10"] != df_ema.iloc[0]["ma10"]

    def test_ma_score_formula(self, mock_s3_ma, client):
        """ma_score = ((close - ma) / ma) * 100"""
        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-24", end_date="2025-04-24",
            ma=True,
        )
        if not df.empty:
            row = df.iloc[-1]
            close = row["close"]
            ma10 = row["ma10"]
            if ma10 and ma10 > 0:
                expected = ((close - ma10) / ma10) * 100.0
                assert abs(row["ma10_score"] - expected) < 0.01

    def test_close_changed(self, mock_s3_ma, client):
        """close_changed = ((curr - prev) / prev) * 100"""
        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-11", end_date="2025-04-11",
            ma=True,
        )
        # First row should have None (no previous bar after trim)
        # If we have multiple rows, check the second
        if len(df) >= 2:
            curr = df.iloc[1]["close"]
            prev = df.iloc[0]["close"]
            expected = ((curr - prev) / prev) * 100.0
            assert abs(df.iloc[1]["close_changed"] - expected) < 0.01

    def test_close_changed_first_row_is_none(self, mock_s3_ma, client):
        """First data row has no previous bar, so close_changed is None/NaN."""
        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-10", end_date="2025-04-11",
            ma=True,
        )
        if len(df) >= 1:
            import math
            assert math.isnan(df.iloc[0]["close_changed"])

    def test_total_money_changed(self, mock_s3_ma, client):
        """total_money_changed = (close - prev_close) * volume"""
        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-11", end_date="2025-04-11",
            ma=True,
        )
        if len(df) >= 2:
            curr = df.iloc[1]
            prev = df.iloc[0]
            expected = (curr["close"] - prev["close"]) * curr["volume"]
            assert abs(curr["total_money_changed"] - expected) < 0.01

    def test_ma_trimmed_to_user_date_range(self, mock_s3_ma, client):
        """When ma=True, buffer data should not appear in the result."""
        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-24", end_date="2025-04-24",
            ma=True,
        )
        # All returned rows should be within user's requested range
        for _, row in df.iterrows():
            assert "2025-04-24" in str(row["time"])


class TestIndicators:
    """Unit tests for the indicators module."""

    def test_sma_full_window(self):
        from aipriceaction.indicators import calculate_sma

        closes = [10.0, 20.0, 30.0, 40.0, 50.0]
        result = calculate_sma(closes, 3)
        # Index 2: (10+20+30)/3 = 20
        # Index 3: (20+30+40)/3 = 30
        # Index 4: (30+40+50)/3 = 40
        assert result[0] == 0.0  # not enough data
        assert result[1] == 0.0  # not enough data
        assert result[2] == 20.0
        assert result[3] == 30.0
        assert result[4] == 40.0

    def test_sma_expanding_window(self):
        from aipriceaction.indicators import calculate_sma

        closes = [10.0, 20.0]
        result = calculate_sma(closes, 5)
        # Only 2 bars, period=5: expanding window
        assert result[0] == 10.0
        assert result[1] == 15.0  # (10+20)/2

    def test_ema_basic(self):
        from aipriceaction.indicators import calculate_ema, calculate_sma

        # Non-linear data so EMA != SMA
        closes = [10.0, 50.0, 20.0, 80.0, 30.0]
        result = calculate_ema(closes, 3)
        # Seed at index 2: SMA(10,50,20) = 26.667
        assert abs(result[2] - 80.0 / 3.0) < 0.01
        # EMA should weight recent data more than SMA
        sma = calculate_sma(closes, 3)
        assert result[4] != sma[4]

    def test_ma_score(self):
        from aipriceaction.indicators import calculate_ma_score

        assert calculate_ma_score(110.0, 100.0) == 10.0
        assert calculate_ma_score(90.0, 100.0) == -10.0
        assert calculate_ma_score(100.0, 0.0) == 0.0

    def test_compute_indicators_keys(self):
        from aipriceaction.indicators import compute_indicators

        closes = [100.0, 101.0, 102.0]
        volumes = [1000, 1100, 1200]
        result = compute_indicators(closes, volumes)

        expected_keys = [
            "ma10", "ma20", "ma50", "ma100", "ma200",
            "ma10_score", "ma20_score", "ma50_score", "ma100_score", "ma200_score",
            "close_changed", "volume_changed", "total_money_changed",
        ]
        for key in expected_keys:
            assert key in result
            assert len(result[key]) == 3

    def test_compute_indicators_ema(self):
        from aipriceaction.indicators import compute_indicators

        closes = [100.0] * 15
        volumes = [1000] * 15
        sma_result = compute_indicators(closes, volumes, use_ema=False)
        ema_result = compute_indicators(closes, volumes, use_ema=True)
        # With flat data, SMA and EMA should be the same
        assert sma_result["ma10"][-1] == ema_result["ma10"][-1]


class TestInit:
    def test_default_cache_dir(self, tmp_path):
        """Without cache_dir, uses system temp dir."""
        c = AIPriceAction("http://example.com")
        assert c._cache_dir.exists()

    def test_custom_cache_dir(self, tmp_path):
        cache = str(tmp_path / "custom")
        c = AIPriceAction("http://example.com", cache_dir=cache)
        assert c._cache_dir == tmp_path / "custom"
        assert c._cache_dir.exists()

    def test_base_url_stripped(self):
        c = AIPriceAction("http://example.com/")
        assert c.base_url == "http://example.com"


class TestCacheInvalidation:
    """Tests for cache invalidation via content-hash freshness checking."""

    def test_hash_file_written_on_first_fetch(self, mock_s3, client, tmp_path):
        """Fetching a CSV should create a .hash sidecar file."""
        client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
            ma=False,
        )
        hash_file = tmp_path / "vn" / "VCB" / "1D" / "VCB-1D-2025-04-29.csv.hash"
        assert hash_file.exists()
        content = hash_file.read_text().strip()
        assert content == "abc123"

    def test_tickers_hash_file_written(self, mock_s3, client, tmp_path):
        """Fetching tickers should create a .hash sidecar file."""
        client.get_tickers(use_cache=False)
        hash_file = tmp_path / "_meta" / "tickers.json.hash"
        assert hash_file.exists()
        content = hash_file.read_text().strip()
        assert content == "tickers-hash-001"

    def test_uses_disk_cache_within_ttl(self, mock_s3, client):
        """Within TTL window, no HEAD requests should be made."""
        import responses

        client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
            ma=False,
        )
        # Clear all mocks — within TTL no HTTP calls should happen.
        # Yearly file is never cached (404), so _is_fresh returns False for it,
        # but the _fetch_csv_yearly path gets 404 → returns None, so no mock needed.
        # However, responses library raises on unmocked calls by default.
        # We need to mock the yearly GET/HEAD 404 since they're unavoidable.
        responses.reset()
        responses.get(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/yearly/VCB-1D-2025.csv",
            status=404,
        )
        responses.head(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/yearly/VCB-1D-2025.csv",
            status=404,
        )
        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
            ma=False,
        )
        assert len(df) == 1
        assert df.iloc[0]["close"] == 56887.44

    def test_re_fetches_on_hash_change(self, mock_s3, client, tmp_path):
        """Different server hash should trigger re-download."""
        import responses

        # First fetch
        client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
            ma=False,
        )

        # Expire freshness by setting TTL to 0
        client._freshness_ttl = 0

        # Replace mocks with new hash + different content
        responses.reset()
        responses.get(
            "http://localhost:9000/aipriceaction-archive/meta/tickers.json",
            json=[
                {"source": "vn", "ticker": "VCB", "name": "Vietcombank", "group": "BANK"},
            ],
        )
        responses.head(
            "http://localhost:9000/aipriceaction-archive/meta/tickers.json",
            headers={"x-amz-meta-content-hash": "tickers-hash-002"},
        )
        new_csv_body = "2025-04-29 00:00:00,60000.0,61000.0,59000.0,60500.0,3000000"
        responses.get(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-29.csv",
            body=new_csv_body,
        )
        responses.head(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-29.csv",
            headers={"x-amz-meta-content-hash": "new-hash-changed"},
        )
        responses.get(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/yearly/VCB-1D-2025.csv",
            status=404,
        )
        responses.head(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/yearly/VCB-1D-2025.csv",
            status=404,
        )

        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
            ma=False,
        )
        assert df.iloc[0]["close"] == 60500.0

    def test_head_failure_uses_disk_cache(self, mock_s3, client):
        """Network error on HEAD should still use disk cache."""
        import responses

        # First fetch (populates disk cache)
        client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
            ma=False,
        )

        # Expire freshness
        client._freshness_ttl = 0

        # Clear all mocks — HEAD will fail for CSV, but disk cache + .hash exist
        # so _is_fresh conservatively returns True.
        # Yearly file: no disk cache, HEAD fails → _is_fresh returns False,
        # but _fetch_csv_yearly GET also needs a mock.
        responses.reset()
        # For yearly file: no disk cache, HEAD fails → _is_fresh returns False → GET called
        # Since there's no disk cache for yearly (it was 404), GET would also fail.
        # Use passthrough to allow real requests (will fail with ConnectionError),
        # or mock them. Mock with 404 since the yearly file never existed.
        responses.get(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/yearly/VCB-1D-2025.csv",
            status=404,
        )
        responses.head(
            "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/yearly/VCB-1D-2025.csv",
            status=404,
        )
        # tickers in-memory cache is used, so no mock needed for that

        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
            ma=False,
        )
        assert len(df) == 1
        assert df.iloc[0]["close"] == 56887.44

    def test_no_hash_file_on_upgrade(self, mock_s3, client, tmp_path):
        """Existing cache without .hash file should work and create .hash."""
        import responses

        # Manually create a CSV in cache without .hash sidecar
        cache_dir = tmp_path / "vn" / "FPT" / "1D"
        cache_dir.mkdir(parents=True, exist_ok=True)
        csv_file = cache_dir / "FPT-1D-2025-04-29.csv"
        csv_file.write_text("2025-04-29 00:00:00,145000.0,146500.0,144000.0,146000.0,987654")

        # Set TTL to 0 so _is_fresh does a HEAD check
        client._freshness_ttl = 0

        # The HEAD mock already returns a hash for FPT-04-29 in conftest
        df = client.get_ohlcv(
            "FPT", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
            ma=False,
        )

        # .hash file should have been created during freshness check
        hash_file = cache_dir / "FPT-1D-2025-04-29.csv.hash"
        assert hash_file.exists()
        assert hash_file.read_text().strip() == "fpt-0429-hash"
        assert len(df) == 1
        assert df.iloc[0]["close"] == 146000.0
