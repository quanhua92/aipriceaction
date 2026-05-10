"""Tests for agent tools (get_live_data, get_ohlcv_data, get_ticker_list)."""

from unittest.mock import MagicMock, patch

import pytest

from aipriceaction_terminal.agents.tools import (
    _reset_clients,
    create_live_data_tool,
)


@pytest.fixture(autouse=True)
def reset_tool_clients():
    """Reset lazy singletons before each test so patching works."""
    _reset_clients()
    yield
    _reset_clients()


@pytest.fixture()
def mock_clients():
    """Patch the lazy singletons used by tool factories."""
    mock_client = MagicMock()
    mock_builder = MagicMock()

    with (
        patch("aipriceaction_terminal.agents.tools._client", mock_client),
        patch("aipriceaction_terminal.agents.tools._builder", mock_builder),
    ):
        yield mock_client, mock_builder


# -- get_live_data tests --


class TestGetLiveData:
    def test_all_tickers_sorted_by_value(self, mock_clients):
        mock_client = mock_clients[0]
        mock_client.fetch_live_data.return_value = {
            "VCB": [{"time": "2026-05-10 09:15", "open": 95.0, "high": 96.5, "low": 94.0,
                     "close": 96.0, "volume": 1000000, "close_changed": 1.05}],
            "FPT": [{"time": "2026-05-10 09:15", "open": 100.0, "high": 102.0, "low": 99.0,
                     "close": 101.0, "volume": 500000}],
        }

        tool_def = create_live_data_tool()
        result = tool_def.tool.invoke({"tickers": "", "interval": "1D"})

        assert "ticker=VCB" in result
        assert "ticker=FPT" in result
        assert "close_changed=1.05" in result
        # VCB (96*1M=96M) should appear before FPT (101*500K=50.5M)
        assert result.index("ticker=VCB") < result.index("ticker=FPT")

    def test_filtered_tickers(self, mock_clients):
        mock_client = mock_clients[0]
        mock_client.fetch_live_data.return_value = {
            "VCB": [{"time": "2026-05-10", "open": 95.0, "high": 96.0, "low": 94.0,
                     "close": 95.5, "volume": 800000}],
            "FPT": [{"time": "2026-05-10", "open": 100.0, "high": 102.0, "low": 99.0,
                     "close": 101.0, "volume": 500000}],
        }

        tool_def = create_live_data_tool()
        result = tool_def.tool.invoke({"tickers": "VCB", "interval": "1D"})

        assert "ticker=VCB" in result
        assert "ticker=FPT" not in result

    def test_with_ma_scores(self, mock_clients):
        mock_client = mock_clients[0]
        mock_client.fetch_live_data.return_value = {
            "BTCUSDT": [{"time": "2026-05-10 12:00", "open": 60000, "high": 61000, "low": 59500,
                         "close": 60500, "volume": 1200, "ma10_score": 2.5, "ma50_score": -1.3}],
        }

        tool_def = create_live_data_tool()
        result = tool_def.tool.invoke({"tickers": "BTCUSDT", "interval": "1D"})

        assert "ticker=BTCUSDT" in result
        assert "ma10_score=2.50" in result
        assert "ma50_score=-1.30" in result

    def test_empty_candles_skipped(self, mock_clients):
        mock_client = mock_clients[0]
        mock_client.fetch_live_data.return_value = {
            "VCB": [],
            "FPT": [{"time": "2026-05-10", "open": 100.0, "high": 102.0, "low": 99.0,
                     "close": 101.0, "volume": 500000}],
        }

        tool_def = create_live_data_tool()
        result = tool_def.tool.invoke({"tickers": "", "interval": "1D"})

        assert "ticker=VCB" not in result
        assert "ticker=FPT" in result

    def test_none_return(self, mock_clients):
        mock_client = mock_clients[0]
        mock_client.fetch_live_data.return_value = None

        tool_def = create_live_data_tool()
        result = tool_def.tool.invoke({"tickers": "", "interval": "1D"})

        assert "Failed to fetch" in result

    def test_api_error(self, mock_clients):
        mock_client = mock_clients[0]
        mock_client.fetch_live_data.side_effect = ConnectionError("timeout")

        tool_def = create_live_data_tool()
        result = tool_def.tool.invoke({"tickers": "", "interval": "1D"})

        assert "Error" in result
        assert "timeout" in result

    def test_no_match_after_filter(self, mock_clients):
        mock_client = mock_clients[0]
        mock_client.fetch_live_data.return_value = {
            "VCB": [{"time": "2026-05-10", "open": 95, "high": 96, "low": 94, "close": 95.5, "volume": 800}],
        }

        tool_def = create_live_data_tool()
        result = tool_def.tool.invoke({"tickers": "NONEXISTENT", "interval": "1D"})

        assert "No live data found" in result

    def test_top_param_caps_all_tickers(self, mock_clients):
        mock_client = mock_clients[0]
        mock_client.fetch_live_data.return_value = {
            f"T{i}": [{"time": "2026-05-10", "open": 100, "high": 100, "low": 100,
                        "close": 100 + i, "volume": 1000 - i * 50, "close_changed": float(i)}]
            for i in range(10)
        }

        tool_def = create_live_data_tool()
        result = tool_def.tool.invoke({"tickers": "", "interval": "1D", "top": 3})

        # Header line + 3 ticker lines
        lines = [line for line in result.split("\n") if line]
        assert lines[0].startswith("Top 3 of 10")
        ticker_lines = [line for line in lines if line.startswith("ticker=")]
        assert len(ticker_lines) == 3
        # Highest trading value first: T0 (100*1000=100K), T1 (101*950=95.95K), T2 (102*900=91.8K)
        assert ticker_lines[0].startswith("ticker=T0")
        assert ticker_lines[1].startswith("ticker=T1")
        assert ticker_lines[2].startswith("ticker=T2")

    def test_top_ignored_when_specific_tickers(self, mock_clients):
        mock_client = mock_clients[0]
        mock_client.fetch_live_data.return_value = {
            "VCB": [{"time": "2026-05-10", "open": 95, "high": 96, "low": 94,
                     "close": 95.5, "volume": 800, "close_changed": 1.0}],
            "FPT": [{"time": "2026-05-10", "open": 100, "high": 102, "low": 99,
                     "close": 101.0, "volume": 500, "close_changed": 0.5}],
        }

        tool_def = create_live_data_tool()
        result = tool_def.tool.invoke({"tickers": "VCB,FPT", "interval": "1D", "top": 1})

        # When specific tickers are requested, top cap is not applied
        assert "ticker=VCB" in result
        assert "ticker=FPT" in result
