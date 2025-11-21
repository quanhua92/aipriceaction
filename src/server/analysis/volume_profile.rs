//! Volume Profile analysis endpoint
//!
//! Provides volume distribution across price levels for a specific trading session.
//! Uses minute-level OHLCV data with uniform distribution (smearing) method.

use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Json},
};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use chrono::{DateTime, Utc, NaiveDate};
use crate::{
    models::{Interval, StockData, Mode},
    server::AppState,
};
use super::AnalysisResponse;

/// Query parameters for volume profile analysis
#[derive(Debug, Deserialize)]
pub struct VolumeProfileQuery {
    /// Ticker symbol (required)
    pub symbol: String,

    /// Date to analyze (YYYY-MM-DD format, required)
    pub date: String,

    /// Market mode: vn or crypto (default: vn)
    #[serde(default = "default_mode")]
    pub mode: String,

    /// Number of price bins for aggregation (default: 50, range: 10-200)
    pub bins: Option<usize>,

    /// Value area percentage (default: 70.0, range: 60-90)
    pub value_area_pct: Option<f64>,
}

fn default_mode() -> String {
    "vn".to_string()
}

/// Volume profile response structure
#[derive(Debug, Serialize)]
pub struct VolumeProfileResponse {
    pub symbol: String,
    pub total_volume: u64,
    pub total_minutes: usize,
    pub price_range: PriceRange,
    pub poc: PointOfControl,
    pub value_area: ValueArea,
    pub profile: Vec<PriceLevelVolume>,
    pub statistics: VolumeStatistics,
}

/// Price range for the session
#[derive(Debug, Serialize)]
pub struct PriceRange {
    pub low: f64,
    pub high: f64,
    pub spread: f64,
}

/// Point of Control (price with highest volume)
#[derive(Debug, Serialize)]
pub struct PointOfControl {
    pub price: f64,
    pub volume: f64,
    pub percentage: f64,
}

/// Value Area (price range containing specified % of volume)
#[derive(Debug, Serialize)]
pub struct ValueArea {
    pub low: f64,
    pub high: f64,
    pub volume: f64,
    pub percentage: f64,
}

/// Individual price level with volume
#[derive(Debug, Clone, Serialize)]
pub struct PriceLevelVolume {
    pub price: f64,
    pub volume: f64,
    pub percentage: f64,
    pub cumulative_percentage: f64,
}

/// Statistical measures of volume distribution
#[derive(Debug, Serialize)]
pub struct VolumeStatistics {
    pub mean_price: f64,
    pub median_price: f64,
    pub std_deviation: f64,
    pub skewness: f64,
}

/// Volume Profile Builder - core algorithm
pub struct VolumeProfileBuilder {
    profile_map: HashMap<i64, f64>,
    tick_size: f64,
    session_low: f64,
    session_high: f64,
}

impl VolumeProfileBuilder {
    /// Create new builder with specified tick size
    pub fn new(tick_size: f64) -> Self {
        Self {
            profile_map: HashMap::new(),
            tick_size,
            session_low: f64::MAX,
            session_high: f64::MIN,
        }
    }

    /// Add a minute candle to the volume profile
    pub fn add_candle(&mut self, candle: &StockData) {
        // Skip zero-volume candles
        if candle.volume == 0 {
            return;
        }

        // Update session range
        self.session_low = self.session_low.min(candle.low);
        self.session_high = self.session_high.max(candle.high);

        // Calculate price indices (convert to integers for HashMap safety)
        let low_idx = (candle.low / self.tick_size).round() as i64;
        let high_idx = (candle.high / self.tick_size).round() as i64;

        // Calculate number of ticks (add 1 because even doji has 1 price level)
        let num_steps = (high_idx - low_idx) + 1;
        if num_steps <= 0 {
            return;
        }

        // Distribute volume uniformly across price levels
        let vol_per_step = candle.volume as f64 / num_steps as f64;

        for idx in low_idx..=high_idx {
            *self.profile_map.entry(idx).or_insert(0.0) += vol_per_step;
        }
    }

    /// Build the final volume profile (sorted by price)
    pub fn build(self) -> (Vec<PriceLevelVolume>, f64, f64) {
        let mut profile: Vec<_> = self.profile_map
            .into_iter()
            .map(|(idx, vol)| PriceLevelVolume {
                price: idx as f64 * self.tick_size,
                volume: vol,
                percentage: 0.0,      // Will be calculated later
                cumulative_percentage: 0.0,  // Will be calculated later
            })
            .collect();

        // Sort by price ascending
        profile.sort_by(|a, b| a.price.partial_cmp(&b.price).unwrap_or(std::cmp::Ordering::Equal));

        (profile, self.session_low, self.session_high)
    }
}

/// Get tick size based on price level (Vietnamese stock market rules)
pub fn get_tick_size_vn(avg_price: f64, symbol: &str) -> f64 {
    use crate::constants::INDEX_TICKERS;

    // Market indices use tick size of 0.01
    if INDEX_TICKERS.contains(&symbol) {
        return 0.01;
    }

    if avg_price < 10_000.0 {
        10.0
    } else if avg_price < 50_000.0 {
        50.0
    } else {
        100.0
    }
}

/// Get tick size for crypto (much finer granularity)
pub fn get_tick_size_crypto(avg_price: f64) -> f64 {
    if avg_price < 1.0 {
        0.0001  // Sub-dollar cryptos
    } else if avg_price < 100.0 {
        0.01    // Small-cap cryptos
    } else if avg_price < 1000.0 {
        0.1     // Mid-cap cryptos
    } else {
        1.0     // Large-cap cryptos (BTC, ETH)
    }
}

/// Calculate Point of Control (price with highest volume)
fn calculate_poc(profile: &[PriceLevelVolume], total_volume: f64) -> PointOfControl {
    let poc = profile.iter()
        .max_by(|a, b| a.volume.partial_cmp(&b.volume).unwrap_or(std::cmp::Ordering::Equal))
        .cloned()
        .unwrap_or(PriceLevelVolume {
            price: 0.0,
            volume: 0.0,
            percentage: 0.0,
            cumulative_percentage: 0.0,
        });

    PointOfControl {
        price: poc.price,
        volume: poc.volume,
        percentage: if total_volume > 0.0 {
            (poc.volume / total_volume) * 100.0
        } else {
            0.0
        },
    }
}

/// Calculate Value Area (price range containing target % of volume)
fn calculate_value_area(
    profile: &[PriceLevelVolume],
    poc_price: f64,
    total_volume: f64,
    target_percentage: f64,
) -> ValueArea {
    if profile.is_empty() || total_volume == 0.0 {
        return ValueArea {
            low: 0.0,
            high: 0.0,
            volume: 0.0,
            percentage: 0.0,
        };
    }

    let target_volume = total_volume * (target_percentage / 100.0);

    // Find POC index
    let poc_idx = profile.iter()
        .position(|p| (p.price - poc_price).abs() < 0.01)
        .unwrap_or(0);

    let mut va_low_idx = poc_idx;
    let mut va_high_idx = poc_idx;
    let mut accumulated_volume = profile[poc_idx].volume;

    // Expand value area by adding adjacent levels with higher volume
    while accumulated_volume < target_volume {
        let vol_below = if va_low_idx > 0 {
            profile[va_low_idx - 1].volume
        } else {
            0.0
        };

        let vol_above = if va_high_idx < profile.len() - 1 {
            profile[va_high_idx + 1].volume
        } else {
            0.0
        };

        if vol_below == 0.0 && vol_above == 0.0 {
            break;  // No more levels to add
        }

        if vol_below > vol_above && va_low_idx > 0 {
            va_low_idx -= 1;
            accumulated_volume += profile[va_low_idx].volume;
        } else if va_high_idx < profile.len() - 1 {
            va_high_idx += 1;
            accumulated_volume += profile[va_high_idx].volume;
        } else if va_low_idx > 0 {
            va_low_idx -= 1;
            accumulated_volume += profile[va_low_idx].volume;
        } else {
            break;
        }
    }

    ValueArea {
        low: profile[va_low_idx].price,
        high: profile[va_high_idx].price,
        volume: accumulated_volume,
        percentage: if total_volume > 0.0 {
            (accumulated_volume / total_volume) * 100.0
        } else {
            0.0
        },
    }
}

/// Calculate volume-weighted statistics
fn calculate_statistics(profile: &[PriceLevelVolume], total_volume: f64) -> VolumeStatistics {
    if profile.is_empty() || total_volume == 0.0 {
        return VolumeStatistics {
            mean_price: 0.0,
            median_price: 0.0,
            std_deviation: 0.0,
            skewness: 0.0,
        };
    }

    // Volume-weighted mean
    let mean_price: f64 = profile.iter()
        .map(|p| p.price * p.volume)
        .sum::<f64>() / total_volume;

    // Find median (50th percentile by volume)
    let mut cumulative = 0.0;
    let mut median_price = profile[0].price;
    for level in profile {
        cumulative += level.volume;
        if cumulative >= total_volume / 2.0 {
            median_price = level.price;
            break;
        }
    }

    // Volume-weighted standard deviation
    let variance: f64 = profile.iter()
        .map(|p| {
            let diff = p.price - mean_price;
            diff * diff * p.volume
        })
        .sum::<f64>() / total_volume;
    let std_deviation = variance.sqrt();

    // Volume-weighted skewness
    let skewness = if std_deviation > 0.0 {
        let m3: f64 = profile.iter()
            .map(|p| {
                let diff = p.price - mean_price;
                diff.powi(3) * p.volume
            })
            .sum::<f64>() / total_volume;
        m3 / std_deviation.powi(3)
    } else {
        0.0
    };

    VolumeStatistics {
        mean_price,
        median_price,
        std_deviation,
        skewness,
    }
}

/// Aggregate profile into bins for visualization
fn aggregate_into_bins(profile: Vec<PriceLevelVolume>, num_bins: usize) -> Vec<PriceLevelVolume> {
    if profile.len() <= num_bins {
        return profile;  // No need to bin
    }

    let price_min = profile.first().unwrap().price;
    let price_max = profile.last().unwrap().price;
    let bin_size = (price_max - price_min) / num_bins as f64;

    if bin_size <= 0.0 {
        return profile;
    }

    let mut bins = vec![PriceLevelVolume {
        price: 0.0,
        volume: 0.0,
        percentage: 0.0,
        cumulative_percentage: 0.0,
    }; num_bins];

    for level in profile {
        let bin_idx = ((level.price - price_min) / bin_size).floor() as usize;
        let bin_idx = bin_idx.min(num_bins - 1);  // Clamp to last bin

        bins[bin_idx].volume += level.volume;
        bins[bin_idx].price = price_min + (bin_idx as f64 + 0.5) * bin_size;  // Bin center
    }

    // Remove empty bins
    bins.retain(|b| b.volume > 0.0);
    bins
}

/// Calculate percentages and cumulative percentages
fn add_percentages(profile: &mut [PriceLevelVolume], total_volume: f64) {
    let mut cumulative = 0.0;
    for level in profile.iter_mut() {
        level.percentage = if total_volume > 0.0 {
            (level.volume / total_volume) * 100.0
        } else {
            0.0
        };
        cumulative += level.percentage;
        level.cumulative_percentage = cumulative;
    }
}

/// Handler for volume profile analysis endpoint
pub async fn volume_profile_handler(
    State(app_state): State<AppState>,
    Query(params): Query<VolumeProfileQuery>,
) -> impl IntoResponse {
    // Validate required parameters
    if params.symbol.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "symbol parameter is required"
            })),
        ).into_response();
    }

    if params.date.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "date parameter is required (YYYY-MM-DD format)"
            })),
        ).into_response();
    }

    // Parse and validate date
    let naive_date = match NaiveDate::parse_from_str(&params.date, "%Y-%m-%d") {
        Ok(date) => date,
        Err(_) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": "Invalid date format. Use YYYY-MM-DD"
                })),
            ).into_response();
        }
    };

    // Parse mode
    let mode = match params.mode.to_lowercase().as_str() {
        "crypto" => Mode::Crypto,
        _ => Mode::Vn,
    };

    // Get DataStore based on mode
    let data_state = app_state.get_data_store(mode);

    // Validate bins parameter
    let num_bins = params.bins.unwrap_or(50).clamp(2, 200);

    // Validate value_area_pct parameter
    let value_area_pct = params.value_area_pct.unwrap_or(70.0).clamp(60.0, 90.0);

    // Create start and end datetime for the specific date
    let start_datetime = naive_date.and_hms_opt(0, 0, 0)
        .map(|dt| DateTime::<Utc>::from_naive_utc_and_offset(dt, Utc))
        .unwrap();

    let end_datetime = naive_date.and_hms_opt(23, 59, 59)
        .map(|dt| DateTime::<Utc>::from_naive_utc_and_offset(dt, Utc))
        .unwrap();

    // Fetch minute-level data for the specific date
    let data = data_state.get_data_with_cache(
        vec![params.symbol.clone()],
        Interval::Minute,
        Some(start_datetime),
        Some(end_datetime),
        None,  // No limit - get all minute data for the day
        true,  // Use cache
    ).await;

    // Get the stock data
    let stock_data = match data.get(&params.symbol) {
        Some(data) if !data.is_empty() => data,
        _ => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({
                    "error": format!("No minute data found for {} on {}", params.symbol, params.date)
                })),
            ).into_response();
        }
    };

    // Filter data to only include the specific date
    let filtered_data: Vec<&StockData> = stock_data.iter()
        .filter(|d| d.time.date_naive() == naive_date)
        .collect();

    if filtered_data.is_empty() {
        return (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({
                "error": format!("No minute data found for {} on {}", params.symbol, params.date)
            })),
        ).into_response();
    }

    // Calculate average price to determine tick size
    let total_price: f64 = filtered_data.iter().map(|d| (d.high + d.low) / 2.0).sum();
    let avg_price = total_price / filtered_data.len() as f64;

    let tick_size = match mode {
        Mode::Vn => get_tick_size_vn(avg_price, &params.symbol),
        Mode::Crypto => get_tick_size_crypto(avg_price),
    };

    // Build volume profile
    let mut builder = VolumeProfileBuilder::new(tick_size);

    for candle in &filtered_data {
        builder.add_candle(candle);
    }

    let (mut profile, session_low, session_high) = builder.build();

    if profile.is_empty() {
        return (
            StatusCode::OK,
            Json(serde_json::json!({
                "error": "No volume profile data generated"
            })),
        ).into_response();
    }

    // Calculate total volume
    let total_volume: f64 = profile.iter().map(|p| p.volume).sum();
    let total_volume_u64: u64 = filtered_data.iter().map(|d| d.volume).sum();

    // Aggregate into bins
    profile = aggregate_into_bins(profile, num_bins);

    // Add percentages
    add_percentages(&mut profile, total_volume);

    // Calculate POC
    let poc = calculate_poc(&profile, total_volume);

    // Calculate Value Area
    let value_area = calculate_value_area(&profile, poc.price, total_volume, value_area_pct);

    // Calculate statistics
    let statistics = calculate_statistics(&profile, total_volume);

    // Build response
    let response = VolumeProfileResponse {
        symbol: params.symbol.clone(),
        total_volume: total_volume_u64,
        total_minutes: filtered_data.len(),
        price_range: PriceRange {
            low: session_low,
            high: session_high,
            spread: session_high - session_low,
        },
        poc,
        value_area,
        profile,
        statistics,
    };

    (
        StatusCode::OK,
        Json(AnalysisResponse {
            analysis_date: params.date,
            analysis_type: "volume_profile".to_string(),
            total_analyzed: filtered_data.len(),
            data: response,
        }),
    ).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;

    fn create_test_candle(low: f64, high: f64, volume: u64) -> StockData {
        StockData::new(
            Utc::now(),
            "TEST".to_string(),
            (low + high) / 2.0,  // open
            high,
            low,
            (low + high) / 2.0,  // close
            volume,
        )
    }

    #[test]
    fn test_volume_distribution_single_candle() {
        let mut builder = VolumeProfileBuilder::new(100.0);
        let candle = create_test_candle(60_000.0, 60_400.0, 1000);

        builder.add_candle(&candle);
        let (profile, _, _) = builder.build();

        // Should have 5 price levels (60000, 60100, 60200, 60300, 60400)
        assert_eq!(profile.len(), 5);

        // Each level should have 200 volume (1000 / 5)
        for level in &profile {
            assert!((level.volume - 200.0).abs() < 0.01);
        }
    }

    #[test]
    fn test_doji_candle_handling() {
        let mut builder = VolumeProfileBuilder::new(100.0);
        let candle = create_test_candle(60_000.0, 60_000.0, 1000);

        builder.add_candle(&candle);
        let (profile, _, _) = builder.build();

        // Should have exactly 1 price level
        assert_eq!(profile.len(), 1);
        assert_eq!(profile[0].volume, 1000.0);
        assert_eq!(profile[0].price, 60_000.0);
    }

    #[test]
    fn test_zero_volume_candle_skipped() {
        let mut builder = VolumeProfileBuilder::new(100.0);
        let candle = create_test_candle(60_000.0, 60_400.0, 0);

        builder.add_candle(&candle);
        let (profile, _, _) = builder.build();

        // Should have no profile data
        assert_eq!(profile.len(), 0);
    }

    #[test]
    fn test_tick_size_selection_vn() {
        // Stock tickers
        assert_eq!(get_tick_size_vn(5_000.0, "VCB"), 10.0);
        assert_eq!(get_tick_size_vn(9_999.0, "VCB"), 10.0);
        assert_eq!(get_tick_size_vn(10_000.0, "VCB"), 50.0);
        assert_eq!(get_tick_size_vn(25_000.0, "VCB"), 50.0);
        assert_eq!(get_tick_size_vn(49_999.0, "VCB"), 50.0);
        assert_eq!(get_tick_size_vn(50_000.0, "VCB"), 100.0);
        assert_eq!(get_tick_size_vn(100_000.0, "VCB"), 100.0);

        // Index tickers use tick size 0.01 regardless of price
        assert_eq!(get_tick_size_vn(1200.0, "VNINDEX"), 0.01);
        assert_eq!(get_tick_size_vn(1300.0, "VN30"), 0.01);
    }

    #[test]
    fn test_tick_size_selection_crypto() {
        assert_eq!(get_tick_size_crypto(0.5), 0.0001);
        assert_eq!(get_tick_size_crypto(50.0), 0.01);
        assert_eq!(get_tick_size_crypto(500.0), 0.1);
        assert_eq!(get_tick_size_crypto(50_000.0), 1.0);
    }

    #[test]
    fn test_poc_calculation() {
        let profile = vec![
            PriceLevelVolume { price: 100.0, volume: 500.0, percentage: 0.0, cumulative_percentage: 0.0 },
            PriceLevelVolume { price: 101.0, volume: 1000.0, percentage: 0.0, cumulative_percentage: 0.0 },
            PriceLevelVolume { price: 102.0, volume: 750.0, percentage: 0.0, cumulative_percentage: 0.0 },
        ];

        let poc = calculate_poc(&profile, 2250.0);

        assert_eq!(poc.price, 101.0);
        assert_eq!(poc.volume, 1000.0);
        assert!((poc.percentage - 44.44).abs() < 0.01);
    }

    #[test]
    fn test_value_area_calculation() {
        let profile = vec![
            PriceLevelVolume { price: 100.0, volume: 100.0, percentage: 0.0, cumulative_percentage: 0.0 },
            PriceLevelVolume { price: 101.0, volume: 200.0, percentage: 0.0, cumulative_percentage: 0.0 },
            PriceLevelVolume { price: 102.0, volume: 500.0, percentage: 0.0, cumulative_percentage: 0.0 },  // POC
            PriceLevelVolume { price: 103.0, volume: 150.0, percentage: 0.0, cumulative_percentage: 0.0 },
            PriceLevelVolume { price: 104.0, volume: 50.0, percentage: 0.0, cumulative_percentage: 0.0 },
        ];

        let total_volume = 1000.0;
        let va = calculate_value_area(&profile, 102.0, total_volume, 70.0);

        // Should contain at least POC + adjacent high volume levels
        assert!(va.volume >= 700.0);
        assert!(va.percentage >= 70.0);
        assert!(va.low <= 102.0);
        assert!(va.high >= 102.0);
    }

    #[test]
    fn test_statistics_calculation() {
        let profile = vec![
            PriceLevelVolume { price: 100.0, volume: 200.0, percentage: 0.0, cumulative_percentage: 0.0 },
            PriceLevelVolume { price: 110.0, volume: 600.0, percentage: 0.0, cumulative_percentage: 0.0 },
            PriceLevelVolume { price: 120.0, volume: 200.0, percentage: 0.0, cumulative_percentage: 0.0 },
        ];

        let stats = calculate_statistics(&profile, 1000.0);

        // Volume-weighted mean should be (100*200 + 110*600 + 120*200) / 1000 = 110
        assert!((stats.mean_price - 110.0).abs() < 0.01);
        assert!(stats.std_deviation > 0.0);
    }

    #[test]
    fn test_binning_aggregation() {
        let profile = vec![
            PriceLevelVolume { price: 100.0, volume: 100.0, percentage: 0.0, cumulative_percentage: 0.0 },
            PriceLevelVolume { price: 101.0, volume: 150.0, percentage: 0.0, cumulative_percentage: 0.0 },
            PriceLevelVolume { price: 102.0, volume: 200.0, percentage: 0.0, cumulative_percentage: 0.0 },
            PriceLevelVolume { price: 103.0, volume: 180.0, percentage: 0.0, cumulative_percentage: 0.0 },
            PriceLevelVolume { price: 104.0, volume: 170.0, percentage: 0.0, cumulative_percentage: 0.0 },
        ];

        let binned = aggregate_into_bins(profile, 2);

        // Should aggregate into 2 bins
        assert!(binned.len() <= 2);

        // Total volume should be preserved
        let total_binned: f64 = binned.iter().map(|b| b.volume).sum();
        assert!((total_binned - 800.0).abs() < 0.01);
    }

    #[test]
    fn test_add_percentages() {
        let mut profile = vec![
            PriceLevelVolume { price: 100.0, volume: 300.0, percentage: 0.0, cumulative_percentage: 0.0 },
            PriceLevelVolume { price: 101.0, volume: 700.0, percentage: 0.0, cumulative_percentage: 0.0 },
        ];

        add_percentages(&mut profile, 1000.0);

        assert!((profile[0].percentage - 30.0).abs() < 0.01);
        assert!((profile[1].percentage - 70.0).abs() < 0.01);
        assert!((profile[0].cumulative_percentage - 30.0).abs() < 0.01);
        assert!((profile[1].cumulative_percentage - 100.0).abs() < 0.01);
    }
}
