"""Tests for aipriceaction.performers module."""

from __future__ import annotations


from aipriceaction.performers import (
    _is_index_ticker,
    _is_vn_ticker,
    build_performers,
)


def _make_candle(
    close: float = 100.0,
    volume: int = 50000,
    close_changed: float | None = 2.5,
    volume_changed: float | None = 10.0,
    ma10_score: float | None = 80.0,
    ma20_score: float | None = 70.0,
    ma50_score: float | None = 60.0,
    ma100_score: float | None = 50.0,
    ma200_score: float | None = 40.0,
    total_money_changed: float | None = 1000000.0,
) -> dict:
    return {
        "time": "2026-05-09",
        "open": close - 1,
        "high": close + 2,
        "low": close - 2,
        "close": close,
        "volume": volume,
        "close_changed": close_changed,
        "volume_changed": volume_changed,
        "ma10_score": ma10_score,
        "ma20_score": ma20_score,
        "ma50_score": ma50_score,
        "ma100_score": ma100_score,
        "ma200_score": ma200_score,
        "total_money_changed": total_money_changed,
    }


class TestIsVnTicker:
    def test_vn_ticker(self):
        assert _is_vn_ticker("VCB") is True
        assert _is_vn_ticker("FPT") is True

    def test_crypto_ticker(self):
        assert _is_vn_ticker("BTCUSDT") is False

    def test_index_ticker(self):
        assert _is_vn_ticker("VNINDEX") is False  # 7 chars

    def test_lowercase(self):
        assert _is_vn_ticker("vcb") is False


class TestIsIndexTicker:
    def test_index(self):
        assert _is_index_ticker("VNINDEX") is True
        assert _is_index_ticker("VN30") is True
        assert _is_index_ticker("VN100") is True

    def test_not_index(self):
        assert _is_index_ticker("VCB") is False
        assert _is_index_ticker("BTCUSDT") is False

    def test_case_insensitive(self):
        assert _is_index_ticker("vnindex") is True


class TestBuildPerformers:
    def _sample_data(self) -> dict[str, list[dict]]:
        return {
            "VCB": [_make_candle(close=100.0, close_changed=5.0, volume=100000)],
            "FPT": [_make_candle(close=200.0, close_changed=3.0, volume=80000)],
            "MBB": [_make_candle(close=50.0, close_changed=-2.0, volume=60000)],
            "BTCUSDT": [_make_candle(close=94000.0, close_changed=1.5, volume=5000)],
        }

    def test_sort_by_close_changed_desc(self):
        data = self._sample_data()
        top, worst = build_performers(data, {})
        assert top[0].symbol == "VCB"
        assert top[0].close_changed == 5.0
        assert worst[0].symbol == "MBB"
        assert worst[0].close_changed == -2.0

    def test_sort_by_close_changed_asc(self):
        data = self._sample_data()
        top, worst = build_performers(data, {}, direction="asc")
        # asc direction: top = ascending (weakest first)
        assert top[0].symbol == "MBB"
        # worst = descending (strongest first)
        assert worst[0].symbol == "VCB"

    def test_sort_by_volume(self):
        data = self._sample_data()
        top, _ = build_performers(data, {}, sort_by="volume")
        assert top[0].symbol == "VCB"
        assert top[0].volume == 100000

    def test_sort_by_ma_score(self):
        data = self._sample_data()
        top, _ = build_performers(data, {}, sort_by="ma10_score")
        # All have ma10_score=80.0, so order depends on sort stability
        assert all(p.ma10_score == 80.0 for p in top)

    def test_none_handling(self):
        """None values should sort last in both directions."""
        data = {
            "AAA": [_make_candle(close_changed=5.0)],
            "BBB": [_make_candle(close_changed=None)],
            "CCC": [_make_candle(close_changed=-3.0)],
        }
        top, worst = build_performers(data, {}, sort_by="close_changed")
        # desc: 5.0, -3.0, None → BBB (None) is last
        assert top[-1].close_changed is None
        # asc (worst): -3.0, 5.0, None → BBB (None) is last
        assert worst[-1].close_changed is None

    def test_index_tickers_excluded(self):
        data = {
            "VCB": [_make_candle()],
            "VNINDEX": [_make_candle(close_changed=10.0)],
            "VN30": [_make_candle()],
        }
        top, worst = build_performers(data, {})
        symbols = {p.symbol for p in top + worst}
        assert "VNINDEX" not in symbols
        assert "VN30" not in symbols
        assert "VCB" in symbols

    def test_min_volume_filter_vn_only(self):
        """min_volume filter applies only to VN tickers."""
        data = {
            "AAA": [_make_candle(volume=5000)],   # VN ticker, below min_volume
            "BTCUSDT": [_make_candle(volume=100)],  # crypto, should pass
        }
        top, _ = build_performers(data, {}, min_volume=10000)
        symbols = {p.symbol for p in top}
        assert "AAA" not in symbols
        assert "BTCUSDT" in symbols

    def test_sector_enrichment(self):
        sector_map = {"VCB": "Banking", "FPT": "Technology"}
        data = {
            "VCB": [_make_candle()],
            "FPT": [_make_candle()],
        }
        top, _ = build_performers(data, sector_map)
        assert top[0].sector in ("Banking", "Technology")

    def test_source_display(self):
        data = {"VCB": [_make_candle()]}
        top, _ = build_performers(data, {}, source="vn")
        assert top[0].source == "vn"

    def test_limit_clamping(self):
        data = {f"T{i:02d}": [_make_candle(close_changed=float(i))] for i in range(50)}
        top, worst = build_performers(data, {}, limit=200)
        # Clamped to 100
        assert len(top) == 50  # Only 50 tickers in data
        assert len(worst) == 50

    def test_limit_small(self):
        data = {
            "A": [_make_candle(close_changed=5.0)],
            "B": [_make_candle(close_changed=3.0)],
            "C": [_make_candle(close_changed=1.0)],
        }
        top, worst = build_performers(data, {}, limit=2)
        assert len(top) == 2
        assert len(worst) == 2

    def test_empty_data(self):
        top, worst = build_performers({}, {})
        assert top == []
        assert worst == []

    def test_empty_candles(self):
        data = {"VCB": []}
        top, worst = build_performers(data, {})
        assert top == []

    def test_total_money_changed_sort(self):
        data = {
            "AAA": [_make_candle(total_money_changed=5000000)],
            "BBB": [_make_candle(total_money_changed=1000000)],
            "CCC": [_make_candle(total_money_changed=3000000)],
        }
        top, _ = build_performers(data, {}, sort_by="total_money_changed")
        assert top[0].symbol == "AAA"
        assert top[1].symbol == "CCC"
        assert top[2].symbol == "BBB"

    def test_value_sort(self):
        """Sort by trading value (close * volume)."""
        data = {
            "AAA": [_make_candle(close=100.0, volume=100000)],   # value=10,000,000
            "BBB": [_make_candle(close=200.0, volume=200000)],   # value=40,000,000
            "CCC": [_make_candle(close=50.0, volume=500000)],    # value=25,000,000
        }
        top, _ = build_performers(data, {}, sort_by="value")
        assert top[0].symbol == "BBB"  # highest value
        assert top[1].symbol == "CCC"
        assert top[2].symbol == "AAA"
        assert top[0].value == 40_000_000
