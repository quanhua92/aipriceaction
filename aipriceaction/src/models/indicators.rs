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

/// Calculate MA score: ((close - ma) / ma) * 100
pub fn calculate_ma_score(close: f64, ma: f64) -> f64 {
    if ma == 0.0 {
        0.0
    } else {
        ((close - ma) / ma) * 100.0
    }
}
