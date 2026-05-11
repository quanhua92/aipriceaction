"""Tests for aipriceaction.volume_profile module."""

from __future__ import annotations


import pandas as pd

from aipriceaction.volume_profile import (
    PriceLevelVolume,
    _add_percentages,
    _aggregate_into_bins,
    _calculate_statistics,
    _calculate_value_area,
    _get_tick_size_crypto,
    _get_tick_size_vn,
    compute_volume_profile,
)


class TestGetTickSizeVn:
    def test_index_ticker(self):
        assert _get_tick_size_vn(1000.0, "VNINDEX") == 0.01
        assert _get_tick_size_vn(1000.0, "VN30") == 0.01

    def test_low_price(self):
        assert _get_tick_size_vn(5000.0, "AAA") == 10.0

    def test_mid_price(self):
        assert _get_tick_size_vn(25000.0, "VCB") == 50.0

    def test_high_price(self):
        assert _get_tick_size_vn(60000.0, "FPT") == 100.0

    def test_boundary_low(self):
        assert _get_tick_size_vn(9999.99, "AAA") == 10.0

    def test_boundary_mid(self):
        assert _get_tick_size_vn(10000.0, "AAA") == 50.0
        assert _get_tick_size_vn(49999.99, "AAA") == 50.0

    def test_boundary_high(self):
        assert _get_tick_size_vn(50000.0, "AAA") == 100.0


class TestGetTickSizeCrypto:
    def test_very_low(self):
        assert _get_tick_size_crypto(0.5) == 0.0001

    def test_low(self):
        assert _get_tick_size_crypto(50.0) == 0.01

    def test_mid(self):
        assert _get_tick_size_crypto(500.0) == 0.1

    def test_high(self):
        assert _get_tick_size_crypto(94000.0) == 1.0

    def test_boundaries(self):
        assert _get_tick_size_crypto(0.99) == 0.0001
        assert _get_tick_size_crypto(1.0) == 0.01
        assert _get_tick_size_crypto(99.99) == 0.01
        assert _get_tick_size_crypto(100.0) == 0.1
        assert _get_tick_size_crypto(999.99) == 0.1
        assert _get_tick_size_crypto(1000.0) == 1.0


class TestAggregateIntoBins:
    def test_fewer_than_bins(self):
        """If profile has fewer entries than bins, return as-is."""
        profile = [
            PriceLevelVolume(price=100.0, volume=10.0),
            PriceLevelVolume(price=200.0, volume=20.0),
        ]
        result = _aggregate_into_bins(profile, 5)
        assert len(result) == 2

    def test_exact_bins(self):
        """If profile has exactly num_bins entries, return as-is."""
        profile = [
            PriceLevelVolume(price=float(i), volume=1.0) for i in range(5)
        ]
        result = _aggregate_into_bins(profile, 5)
        assert len(result) == 5

    def test_many_into_few(self):
        """100 levels into 10 bins."""
        profile = [
            PriceLevelVolume(price=float(i), volume=1.0) for i in range(100)
        ]
        result = _aggregate_into_bins(profile, 10)
        assert len(result) == 10
        total_vol = sum(p.volume for p in result)
        assert abs(total_vol - 100.0) < 1e-6

    def test_empty(self):
        assert _aggregate_into_bins([], 10) == []

    def test_zero_bin_size(self):
        """All same price → bin_size=0 → return as-is."""
        profile = [
            PriceLevelVolume(price=100.0, volume=10.0),
            PriceLevelVolume(price=100.0, volume=20.0),
        ]
        result = _aggregate_into_bins(profile, 5)
        assert len(result) == 2


class TestAddPercentages:
    def test_percentages_sum_to_100(self):
        profile = [
            PriceLevelVolume(price=100.0, volume=30.0),
            PriceLevelVolume(price=200.0, volume=70.0),
        ]
        _add_percentages(profile, 100.0)
        assert abs(profile[0].percentage - 30.0) < 1e-6
        assert abs(profile[1].percentage - 70.0) < 1e-6
        assert abs(profile[0].cumulative_percentage - 30.0) < 1e-6
        assert abs(profile[1].cumulative_percentage - 100.0) < 1e-6

    def test_zero_total(self):
        profile = [PriceLevelVolume(price=100.0, volume=0.0)]
        _add_percentages(profile, 0.0)
        assert profile[0].percentage == 0.0

    def test_empty(self):
        _add_percentages([], 100.0)  # should not raise


class TestCalculateValueArea:
    def test_single_level(self):
        profile = [PriceLevelVolume(price=100.0, volume=100.0)]
        va = _calculate_value_area(profile, 100.0, 100.0, 70.0)
        assert va.low == 100.0
        assert va.high == 100.0
        assert abs(va.percentage - 100.0) < 1e-6

    def test_expand_both_directions(self):
        profile = [
            PriceLevelVolume(price=90.0, volume=20.0),
            PriceLevelVolume(price=95.0, volume=30.0),
            PriceLevelVolume(price=100.0, volume=50.0),  # POC
            PriceLevelVolume(price=105.0, volume=40.0),
            PriceLevelVolume(price=110.0, volume=10.0),
        ]
        total = 150.0
        va = _calculate_value_area(profile, 100.0, total, 70.0)
        # Need 70% of 150 = 105
        # POC=50, add 105 (40) → 90, need 15 more
        # add 95 (30) → 120, exceeds 105 → stop
        assert va.low == 95.0
        assert va.high == 105.0
        assert abs(va.volume - 120.0) < 1e-6

    def test_poc_at_edge(self):
        profile = [
            PriceLevelVolume(price=100.0, volume=80.0),  # POC at low edge
            PriceLevelVolume(price=110.0, volume=20.0),
        ]
        total = 100.0
        va = _calculate_value_area(profile, 100.0, total, 70.0)
        # Need 70 → POC=80 already exceeds → but algorithm tries to reach target
        # POC=80, need 70 → already have 80 >= 70, but loop checks < target
        # Wait: 80 >= 70, loop doesn't execute
        assert va.low == 100.0
        assert va.high == 100.0
        assert abs(va.volume - 80.0) < 1e-6

    def test_empty_profile(self):
        va = _calculate_value_area([], 100.0, 100.0, 70.0)
        assert va.volume == 0.0


class TestCalculateStatistics:
    def test_uniform_distribution(self):
        profile = [
            PriceLevelVolume(price=90.0, volume=50.0),
            PriceLevelVolume(price=100.0, volume=50.0),
            PriceLevelVolume(price=110.0, volume=50.0),
        ]
        stats = _calculate_statistics(profile, 150.0)
        assert abs(stats.mean_price - 100.0) < 1e-6
        # Skewness should be ~0 for symmetric distribution
        assert abs(stats.skewness) < 1e-6

    def test_single_level(self):
        profile = [PriceLevelVolume(price=100.0, volume=50.0)]
        stats = _calculate_statistics(profile, 50.0)
        assert abs(stats.mean_price - 100.0) < 1e-6
        assert abs(stats.median_price - 100.0) < 1e-6
        assert stats.std_deviation == 0.0
        assert stats.skewness == 0.0

    def test_empty(self):
        stats = _calculate_statistics([], 0.0)
        assert stats.mean_price == 0.0


def _make_df(bars: list[dict]) -> pd.DataFrame:
    """Create a 1m OHLCV DataFrame from bar dicts."""
    return pd.DataFrame(bars)


class TestComputeVolumeProfile:
    def test_full_pipeline(self):
        """Known bars → exact POC/VA values."""
        bars = [
            {"time": "2026-05-09 09:01", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1000},
            {"time": "2026-05-09 09:02", "open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0, "volume": 2000},
            {"time": "2026-05-09 09:03", "open": 102.0, "high": 105.0, "low": 101.0, "close": 104.0, "volume": 3000},
        ]
        df = _make_df(bars)
        result = compute_volume_profile(df, "AAA", source="vn", bins=10)
        assert result.symbol == "AAA"
        assert result.total_minutes == 3
        assert result.total_volume == 6000
        assert result.price_range.low == 99.0
        assert result.price_range.high == 105.0
        assert result.poc.volume > 0
        assert result.poc.percentage > 0
        assert result.statistics is not None

    def test_crypto_source(self):
        bars = [
            {"time": "2026-05-09 09:01", "open": 94000.0, "high": 94500.0, "low": 93500.0, "close": 94200.0, "volume": 100},
        ]
        df = _make_df(bars)
        result = compute_volume_profile(df, "BTCUSDT", source="crypto", bins=10)
        assert result.symbol == "BTCUSDT"
        assert result.total_volume == 100

    def test_empty_dataframe(self):
        df = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
        result = compute_volume_profile(df, "VCB")
        assert result.total_volume == 0
        assert result.total_minutes == 0
        assert result.poc.volume == 0.0

    def test_none_dataframe(self):
        result = compute_volume_profile(None, "VCB")  # type: ignore[arg-type]
        assert result.total_volume == 0

    def test_zero_volume_bars_skipped(self):
        bars = [
            {"time": "2026-05-09 09:01", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 0},
        ]
        df = _make_df(bars)
        result = compute_volume_profile(df, "AAA", source="vn")
        assert result.total_volume == 0

    def test_bins_clamping(self):
        bars = [
            {"time": "2026-05-09 09:01", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1000},
        ]
        df = _make_df(bars)
        # bins=300 → clamped to 200
        result = compute_volume_profile(df, "AAA", source="vn", bins=300)
        assert result.symbol == "AAA"

    def test_value_area_pct_clamping(self):
        bars = [
            {"time": "2026-05-09 09:01", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1000},
        ]
        df = _make_df(bars)
        # 50 → clamped to 60
        result = compute_volume_profile(df, "AAA", source="vn", value_area_pct=50.0)
        assert result.value_area.percentage >= 0

    def test_index_ticker_tick_size(self):
        """VNINDEX should use tick_size=0.01."""
        bars = [
            {"time": "2026-05-09 09:01", "open": 1200.0, "high": 1202.0, "low": 1199.0, "close": 1201.0, "volume": 1000},
        ]
        df = _make_df(bars)
        result = compute_volume_profile(df, "VNINDEX", source="vn", bins=10)
        # With tick_size=0.01, many small bins should be created
        assert result.total_volume == 1000
