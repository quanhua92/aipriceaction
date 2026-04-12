/// Calculate Simple Moving Average for a given period.
pub fn calculate_sma(closes: &[f64], period: usize) -> Vec<f64> {
    let mut ma_values = vec![0.0; closes.len()];

    if period == 0 || closes.len() < period {
        return ma_values;
    }

    for i in (period - 1)..closes.len() {
        let start_idx = i + 1 - period;
        let sum: f64 = closes[start_idx..=i].iter().sum();
        ma_values[i] = sum / period as f64;
    }

    ma_values
}

/// Calculate Weighted Moving Average for a given period.
/// Linear weights: oldest = 1, newest = period.
/// Positions before `period-1` are 0.0.
pub fn calculate_wma(closes: &[f64], period: usize) -> Vec<f64> {
    let mut ma_values = vec![0.0; closes.len()];

    if period == 0 || closes.len() < period {
        return ma_values;
    }

    let weight_sum = period * (period + 1) / 2;

    for i in (period - 1)..closes.len() {
        let start_idx = i + 1 - period;
        let sum: f64 = closes[start_idx..=i]
            .iter()
            .enumerate()
            .map(|(j, &v)| v * (j as f64 + 1.0))
            .sum();
        ma_values[i] = sum / weight_sum as f64;
    }

    ma_values
}

/// Calculate Exponential Moving Average for a given period.
/// Seed: SMA of first `period` closes.
/// Then: EMA[i] = close[i] * K + EMA[i-1] * (1-K),  K = 2/(period+1)
pub fn calculate_ema(closes: &[f64], period: usize) -> Vec<f64> {
    let mut ma_values = vec![0.0; closes.len()];
    if period == 0 || closes.len() < period {
        return ma_values;
    }
    // Seed with SMA
    let sum: f64 = closes[..period].iter().sum();
    ma_values[period - 1] = sum / period as f64;
    let k = 2.0 / (period as f64 + 1.0);
    for i in period..closes.len() {
        ma_values[i] = closes[i] * k + ma_values[i - 1] * (1.0 - k);
    }
    ma_values
}

/// Calculate MA score: ((close - ma) / ma) * 100
pub fn calculate_ma_score(close: f64, ma: f64) -> f64 {
    if ma == 0.0 {
        0.0
    } else {
        ((close - ma) / ma) * 100.0
    }
}
