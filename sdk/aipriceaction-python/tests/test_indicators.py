from aipriceaction.indicators import (
    calculate_ema,
    calculate_ma_score,
    calculate_sma,
    compute_indicators,
)


class TestCalculateSMA:
    def test_sma_basic(self):
        result = calculate_sma([1, 2, 3, 4, 5], 3)
        assert result[0] == 0.0  # before full window, not set when n >= period
        assert result[1] == 0.0  # before full window
        assert result[2] == 2.0  # full window: (1+2+3)/3
        assert result[3] == 3.0  # (2+3+4)/3
        assert result[4] == 4.0  # (3+4+5)/3

    def test_sma_shorter_than_period(self):
        result = calculate_sma([10, 20, 30, 40, 50], 10)
        # Should use expanding window for all values since n < period
        assert result[0] == 10.0
        assert result[1] == 15.0
        assert result[2] == 20.0
        assert result[3] == 25.0
        assert result[4] == 30.0

    def test_sma_empty(self):
        result = calculate_sma([], 3)
        assert result == []

    def test_sma_period_zero(self):
        result = calculate_sma([1, 2, 3, 4, 5], 0)
        assert result == [0.0, 0.0, 0.0, 0.0, 0.0]


class TestCalculateEMA:
    def test_ema_basic(self):
        result = calculate_ema([1, 2, 3, 4, 5], 3)
        # Seed with SMA of first 3: (1+2+3)/3 = 2.0
        assert result[2] == 2.0
        # k = 2/(3+1) = 0.5
        # result[3] = 4*0.5 + 2.0*0.5 = 3.0
        assert result[3] == 3.0
        # result[4] = 5*0.5 + 3.0*0.5 = 4.0
        assert result[4] == 4.0

    def test_ema_empty(self):
        result = calculate_ema([], 3)
        assert result == []


class TestCalculateMAScore:
    def test_ma_score_positive(self):
        # close > ma → positive score
        score = calculate_ma_score(110.0, 100.0)
        assert score == 10.0

    def test_ma_score_negative(self):
        # close < ma → negative score
        score = calculate_ma_score(90.0, 100.0)
        assert score == -10.0

    def test_ma_score_zero_ma(self):
        score = calculate_ma_score(100.0, 0.0)
        assert score == 0.0


class TestComputeIndicators:
    def test_compute_indicators_keys(self):
        result = compute_indicators([100, 101, 102, 103, 104], [1000, 1100, 1200, 1300, 1400])
        expected_keys = [
            "ma10", "ma20", "ma50", "ma100", "ma200",
            "ma10_score", "ma20_score", "ma50_score", "ma100_score", "ma200_score",
            "close_changed", "volume_changed", "total_money_changed",
        ]
        for key in expected_keys:
            assert key in result

    def test_compute_indicators_ema(self):
        # Need more than 10 data points for MA10 to produce non-None values
        closes = [float(i) for i in range(1, 25)]  # 24 values
        volumes = [1000] * 24
        sma_result = compute_indicators(closes, volumes, use_ema=False)
        ema_result = compute_indicators(closes, volumes, use_ema=True)
        # SMA and EMA should produce different MA values for ma10
        assert sma_result["ma10"] != ema_result["ma10"]

    def test_close_changed(self):
        closes = [100.0, 110.0, 99.0]
        volumes = [1000, 1000, 1000]
        result = compute_indicators(closes, volumes)
        # First row: None (no previous)
        assert result["close_changed"][0] is None
        # Second row: ((110 - 100) / 100) * 100 = 10.0
        assert result["close_changed"][1] == 10.0
        # Third row: ((99 - 110) / 110) * 100 = -10.0
        assert result["close_changed"][2] == -10.0

    def test_total_money_changed(self):
        closes = [100.0, 110.0, 99.0]
        volumes = [1000, 2000, 3000]
        result = compute_indicators(closes, volumes)
        # First row: None
        assert result["total_money_changed"][0] is None
        # Second row: (110 - 100) * 2000 = 20000
        assert result["total_money_changed"][1] == 20000.0
        # Third row: (99 - 110) * 3000 = -33000
        assert result["total_money_changed"][2] == -33000.0

    def test_compute_indicators_length(self):
        n = 5
        closes = [100.0] * n
        volumes = [1000] * n
        result = compute_indicators(closes, volumes)
        for key, values in result.items():
            assert len(values) == n, f"key {key} has length {len(values)}, expected {n}"

    def test_compute_indicators_empty(self):
        result = compute_indicators([], [])
        for key, values in result.items():
            assert values == []
