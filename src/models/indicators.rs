//! Technical indicators and calculations for stock data
//!
//! # Price Format Convention
//! **CRITICAL**: All price values use **full format** (not CSV short form).
//!
//! ## Stock Tickers (VCB, FPT, HPG, etc.)
//! - CSV stores: 23.2 (price/1000)
//! - **Pass to functions**: 23200.0 (multiply by 1000)
//!
//! ## Market Indices (VNINDEX, VN30)
//! - CSV stores: 1250.5 (actual value)
//! - **Pass to functions**: 1250.5 (no conversion)
//!
//! **Rule**: Multiply by 1000 ONLY for stock tickers, NOT for indices.
//!
//! ## Vietnamese Market Specifics
//! - Daily price limit: ±6.5%
//! - Stock prices stored in full VND in memory
//! - CSV files use short format for stocks only

/// Calculate Simple Moving Average for a given period
///
/// # Price Format
/// **IMPORTANT**: Input prices must be in **full format**.
/// - Stock tickers: 23200.0 (not 23.2)
/// - Indices: 1250.5 (actual value)
///
/// # Arguments
/// * `closes` - Slice of closing prices in full format
/// * `period` - Period for the moving average (e.g., 10, 20, 50)
///
/// # Returns
/// * Vector of MA values in same format as input (early values are 0.0)
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
///
/// Returns the percentage difference between close price and moving average.
///
/// # Price Format
/// **IMPORTANT**: Both `close` and `ma` must be in the same format.
/// - Stock tickers: Both should be in full VND (e.g., 23200, not 23.2)
/// - Indices: Both should be actual values (e.g., 1250.5)
/// - The returned score is a percentage (format-independent)
///
/// # Example
/// ```
/// // Stock: close = 23700, ma = 22500
/// // score = ((23700 - 22500) / 22500) * 100 = 5.33%
/// ```
pub fn calculate_ma_score(close: f64, ma: f64) -> f64 {
    if ma == 0.0 {
        0.0
    } else {
        ((close - ma) / ma) * 100.0
    }
}

/// Calculate money flow multiplier (Vietnamese market specific)
///
/// # Price Format
/// **IMPORTANT**: All prices must be in the same consistent format.
/// - Stock tickers: Use full VND (e.g., 23200, not 23.2)
/// - Indices: Use actual values (e.g., 1250.5)
/// - Since this calculates a ratio, the format cancels out, but consistency is critical
///
/// # Arguments
/// * `open` - Opening price
/// * `high` - High price
/// * `low` - Low price
/// * `close` - Closing price
/// * `prev_close` - Previous closing price (optional)
///
/// # Returns
/// * Multiplier value between -1.0 and 1.0 (format-independent ratio)
pub fn calculate_money_flow_multiplier(
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    prev_close: Option<f64>,
) -> f64 {
    let effective_high = high.max(open);
    let effective_low = low.min(open);
    let effective_range = effective_high - effective_low;

    // Handle limit move case (Vietnamese market: 6.5% threshold)
    if effective_range == 0.0 || effective_range.abs() < f64::EPSILON {
        if let Some(prev) = prev_close {
            if prev != 0.0 {
                let price_change = (close - prev) / prev;
                if price_change > 0.065 {
                    return 1.0;
                } else if price_change < -0.065 {
                    return -1.0;
                }
            }
        }
        return 0.0;
    }

    // Normal case
    (close - effective_low - (effective_high - close)) / effective_range
}

/// Calculate money flow (activity flow)
///
/// # Arguments
/// * `multiplier` - Money flow multiplier
/// * `volume` - Trading volume
///
/// # Returns
/// * Money flow value
pub fn calculate_money_flow(multiplier: f64, volume: u64) -> f64 {
    multiplier * volume as f64
}

/// Calculate dollar flow
///
/// # Arguments
/// * `multiplier` - Money flow multiplier
/// * `close` - Closing price
/// * `volume` - Trading volume
///
/// # Returns
/// * Dollar flow value
pub fn calculate_dollar_flow(multiplier: f64, close: f64, volume: u64) -> f64 {
    multiplier * close * volume as f64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_calculate_sma() {
        let closes = vec![10.0, 11.0, 12.0, 13.0, 14.0, 15.0];
        let ma3 = calculate_sma(&closes, 3);

        assert_eq!(ma3[0], 0.0); // Not enough data
        assert_eq!(ma3[1], 0.0); // Not enough data
        assert_eq!(ma3[2], 11.0); // (10+11+12)/3
        assert_eq!(ma3[3], 12.0); // (11+12+13)/3
        assert_eq!(ma3[4], 13.0); // (12+13+14)/3
        assert_eq!(ma3[5], 14.0); // (13+14+15)/3
    }

    #[test]
    fn test_calculate_ma_score() {
        let score = calculate_ma_score(110.0, 100.0);
        assert!((score - 10.0).abs() < 0.01);

        let score = calculate_ma_score(90.0, 100.0);
        assert!((score - (-10.0)).abs() < 0.01);
    }

    #[test]
    fn test_money_flow_multiplier_normal() {
        let multiplier = calculate_money_flow_multiplier(100.0, 110.0, 95.0, 108.0, Some(100.0));
        // effective_high = 110, effective_low = 95, range = 15
        // (108 - 95 - (110 - 108)) / 15 = (13 - 2) / 15 = 11/15 ≈ 0.733
        assert!((multiplier - 0.733).abs() < 0.01);
    }

    #[test]
    fn test_money_flow_multiplier_limit_up() {
        let multiplier = calculate_money_flow_multiplier(100.0, 100.0, 100.0, 107.0, Some(100.0));
        // Limit move case: (107-100)/100 = 7% > 6.5%
        assert_eq!(multiplier, 1.0);
    }

    #[test]
    fn test_money_flow_multiplier_limit_down() {
        let multiplier = calculate_money_flow_multiplier(100.0, 100.0, 100.0, 93.0, Some(100.0));
        // Limit move case: (93-100)/100 = -7% < -6.5%
        assert_eq!(multiplier, -1.0);
    }
}
