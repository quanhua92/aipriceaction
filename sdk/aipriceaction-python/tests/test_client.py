from datetime import date

import pandas as pd
import pytest

from aipriceaction import AIPriceAction
from aipriceaction.exceptions import AIPriceActionError
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
        )
        assert len(df) == 2

    def test_multiple_tickers(self, mock_s3, client):
        df = client.get_ohlcv(
            tickers=["VCB", "BTCUSDT"], interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
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
        )
        # VCB + FPT + BTCUSDT all have data for 04-29
        assert len(df) == 3
        symbols = sorted(df["symbol"].unique())
        assert symbols == ["BTCUSDT", "FPT", "VCB"]

    def test_limit_per_symbol(self, mock_s3, client):
        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-28", end_date="2025-04-29",
            limit=1,
        )
        assert len(df) == 1
        # limit takes the last row (tail)
        assert df.iloc[0]["time"] == "2025-04-29 00:00:00"

    def test_with_source_override(self, mock_s3, client):
        df = client.get_ohlcv(
            "BTCUSDT", interval="1D",
            source="crypto",
            start_date="2025-04-29", end_date="2025-04-29",
        )
        assert len(df) == 1
        assert df.iloc[0]["close"] == 94256.82

    def test_empty_result(self, mock_s3, client):
        df = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-30", end_date="2025-04-30",
        )
        assert len(df) == 0
        assert list(df.columns) == ["time", "open", "high", "low", "close", "volume", "symbol"]

    def test_default_interval(self, mock_s3, client):
        """Default interval is 1D."""
        df = client.get_ohlcv(
            "VCB",
            start_date="2025-04-29", end_date="2025-04-29",
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
        )
        cache_file = tmp_path / "vn" / "VCB" / "1D" / "VCB-1D-2025-04-29.csv"
        assert cache_file.exists()

    def test_uses_disk_cache_on_second_call(self, mock_s3, client):
        """Second call should read from disk cache, not make HTTP requests."""
        df1 = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
        )
        # Clear responses so any HTTP call would fail
        import responses
        responses.reset()
        df2 = client.get_ohlcv(
            "VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
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
