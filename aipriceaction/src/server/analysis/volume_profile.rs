use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Json},
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use chrono::NaiveDate;

use crate::models::ohlcv::OhlcvJoined;
use crate::queries::ohlcv;
use crate::server::types::Mode;
use crate::server::AppState;
use crate::workers::redis_worker;

use super::{try_redis_batch, AnalysisResponse};

#[derive(Debug, Deserialize)]
pub struct VolumeProfileQuery {
    pub symbol: String,
    pub date: Option<String>,
    pub start_date: Option<String>,
    pub end_date: Option<String>,
    #[serde(default = "default_mode")]
    pub mode: String,
    pub bins: Option<usize>,
    pub value_area_pct: Option<f64>,
}

fn default_mode() -> String { "vn".to_string() }

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

#[derive(Debug, Serialize)]
pub struct PriceRange { pub low: f64, pub high: f64, pub spread: f64 }

#[derive(Debug, Serialize)]
pub struct PointOfControl { pub price: f64, pub volume: f64, pub percentage: f64 }

#[derive(Debug, Serialize)]
pub struct ValueArea { pub low: f64, pub high: f64, pub volume: f64, pub percentage: f64 }

#[derive(Debug, Clone, Serialize)]
pub struct PriceLevelVolume {
    pub price: f64,
    pub volume: f64,
    pub percentage: f64,
    pub cumulative_percentage: f64,
}

#[derive(Debug, Serialize)]
pub struct VolumeStatistics {
    pub mean_price: f64,
    pub median_price: f64,
    pub std_deviation: f64,
    pub skewness: f64,
}

fn get_tick_size_vn(avg_price: f64, symbol: &str) -> f64 {
    if crate::constants::vci_worker::INDEX_TICKERS.contains(&symbol) {
        return 0.01;
    }
    if avg_price < 10_000.0 { 10.0 }
    else if avg_price < 50_000.0 { 50.0 }
    else { 100.0 }
}

fn get_tick_size_crypto(avg_price: f64) -> f64 {
    if avg_price < 1.0 { 0.0001 }
    else if avg_price < 100.0 { 0.01 }
    else if avg_price < 1000.0 { 0.1 }
    else { 1.0 }
}

pub async fn volume_profile_handler(
    State(state): State<Arc<AppState>>,
    Query(params): Query<VolumeProfileQuery>,
) -> impl IntoResponse {
    if params.symbol.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({ "error": "symbol parameter is required" })),
        ).into_response();
    }

    let (start_date_str, end_date_str) = if let Some(ref date) = params.date {
        (date.clone(), date.clone())
    } else if let Some(ref start) = params.start_date {
        let end = params.end_date.clone().unwrap_or_else(|| start.clone());
        (start.clone(), end)
    } else {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({ "error": "Either 'date' or 'start_date' parameter is required (YYYY-MM-DD format)" })),
        ).into_response();
    };

    let start_naive = match NaiveDate::parse_from_str(&start_date_str, "%Y-%m-%d") {
        Ok(d) => d,
        Err(_) => return (StatusCode::BAD_REQUEST, Json(serde_json::json!({ "error": "Invalid start date format. Use YYYY-MM-DD" }))).into_response(),
    };

    let end_naive = match NaiveDate::parse_from_str(&end_date_str, "%Y-%m-%d") {
        Ok(d) => d,
        Err(_) => return (StatusCode::BAD_REQUEST, Json(serde_json::json!({ "error": "Invalid end date format. Use YYYY-MM-DD" }))).into_response(),
    };

    if end_naive < start_naive {
        return (StatusCode::BAD_REQUEST, Json(serde_json::json!({ "error": "end_date must be >= start_date" }))).into_response();
    }

    let mode = match params.mode.to_lowercase().as_str() {
        "crypto" => Mode::Crypto,
        "yahoo" => Mode::Yahoo,
        _ => Mode::Vn,
    };

    let source = mode.source_label();
    let num_bins = params.bins.unwrap_or(50).clamp(2, 200);
    let value_area_pct = params.value_area_pct.unwrap_or(70.0).clamp(60.0, 90.0);

    let start_datetime = start_naive.and_hms_opt(0, 0, 0).unwrap().and_utc();
    let end_datetime = end_naive.and_hms_opt(23, 59, 59).unwrap().and_utc();

    // Try Redis first, fall back to PG
    let limit = redis_worker::max_size("1m") as i64;
    let rows: Vec<OhlcvJoined> = if let Some(map) = try_redis_batch(
        &state.redis_client, source, &[params.symbol.clone()], "1m", limit, "volume_profile",
    ).await {
        let redis_rows: Vec<OhlcvJoined> = map.get(&params.symbol)
            .map(|r| r.iter().map(|row| OhlcvJoined {
                ticker: params.symbol.clone(),
                time: row.time,
                open: row.open,
                high: row.high,
                low: row.low,
                close: row.close,
                volume: row.volume,
                ma10: None, ma20: None, ma50: None, ma100: None, ma200: None,
                ma10_score: None, ma20_score: None, ma50_score: None, ma100_score: None, ma200_score: None,
                close_changed: None, volume_changed: None, total_money_changed: None,
            }).collect::<Vec<_>>())
            .unwrap_or_default();
        // If Redis has data but none falls in the requested range, try PG
        if !redis_rows.is_empty() {
            let in_range: Vec<_> = redis_rows.iter()
                .filter(|d| {
                    let date = d.time.date_naive();
                    date >= start_naive && date <= end_naive
                })
                .collect();
            if in_range.is_empty() {
                Vec::new() // Redis data doesn't cover this date range, fall to PG
            } else {
                redis_rows
            }
        } else {
            Vec::new()
        }
    } else {
        Vec::new() // Redis unavailable, fall through to PG
    };

    let rows = if rows.is_empty() {
        // Fetch minute data from DB
        match ohlcv::get_ohlcv_joined_range(
            &state.pool,
            source,
            &params.symbol,
            "1m",
            None, // no limit — get all minute data for the date range
            Some(start_datetime),
            Some(end_datetime),
        ).await {
            Ok(r) => r,
            Err(e) => {
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(serde_json::json!({ "error": format!("Failed to fetch data: {}", e) })),
                ).into_response();
            }
        }
    } else {
        rows
    };

    if rows.is_empty() {
        let date_desc = if start_date_str == end_date_str {
            start_date_str
        } else {
            format!("{} to {}", start_date_str, end_date_str)
        };
        return (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({ "error": format!("No minute data found for {} on {}", params.symbol, date_desc) })),
        ).into_response();
    }

    // Filter to exact date range
    let filtered: Vec<_> = rows.iter()
        .filter(|d| {
            let date = d.time.date_naive();
            date >= start_naive && date <= end_naive
        })
        .collect();

    if filtered.is_empty() {
        let date_desc = if start_date_str == end_date_str {
            start_date_str
        } else {
            format!("{} to {}", start_date_str, end_date_str)
        };
        return (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({ "error": format!("No minute data found for {} on {}", params.symbol, date_desc) })),
        ).into_response();
    }

    // Calculate tick size
    let total_price: f64 = filtered.iter().map(|d| (d.high + d.low) / 2.0).sum();
    let avg_price = total_price / filtered.len() as f64;

    let tick_size = match mode {
        Mode::Vn | Mode::All => get_tick_size_vn(avg_price, &params.symbol),
        Mode::Crypto | Mode::Yahoo => get_tick_size_crypto(avg_price),
    };

    // Build volume profile
    let mut profile_map: std::collections::HashMap<i64, f64> = std::collections::HashMap::new();
    let mut session_low = f64::MAX;
    let mut session_high = f64::MIN;

    for row in &filtered {
        if row.volume == 0 { continue; }
        session_low = session_low.min(row.low);
        session_high = session_high.max(row.high);

        let low_idx = (row.low / tick_size).round() as i64;
        let high_idx = (row.high / tick_size).round() as i64;
        let num_steps = (high_idx - low_idx) + 1;
        if num_steps <= 0 { continue; }

        let vol_per_step = row.volume as f64 / num_steps as f64;
        for idx in low_idx..=high_idx {
            *profile_map.entry(idx).or_insert(0.0) += vol_per_step;
        }
    }

    let mut profile: Vec<PriceLevelVolume> = profile_map.into_iter()
        .map(|(idx, vol)| PriceLevelVolume {
            price: idx as f64 * tick_size,
            volume: vol,
            percentage: 0.0,
            cumulative_percentage: 0.0,
        })
        .collect();

    profile.sort_by(|a, b| a.price.partial_cmp(&b.price).unwrap_or(std::cmp::Ordering::Equal));

    if profile.is_empty() {
        return (
            StatusCode::OK,
            Json(serde_json::json!({ "error": "No volume profile data generated" })),
        ).into_response();
    }

    let total_volume: f64 = profile.iter().map(|p| p.volume).sum();
    let total_volume_u64: u64 = filtered.iter().map(|d| d.volume as u64).sum();

    // Aggregate into bins
    let mut profile = aggregate_into_bins(profile, num_bins);

    // Add percentages
    add_percentages(&mut profile, total_volume);

    // Calculate POC
    let poc = profile.iter().max_by(|a, b| a.volume.partial_cmp(&b.volume).unwrap_or(std::cmp::Ordering::Equal)).cloned().unwrap_or(PriceLevelVolume { price: 0.0, volume: 0.0, percentage: 0.0, cumulative_percentage: 0.0 });
    let poc = PointOfControl {
        price: poc.price,
        volume: poc.volume,
        percentage: if total_volume > 0.0 { (poc.volume / total_volume) * 100.0 } else { 0.0 },
    };

    // Calculate Value Area
    let value_area = calculate_value_area(&profile, poc.price, total_volume, value_area_pct);

    // Calculate statistics
    let statistics = calculate_statistics(&profile, total_volume);

    // Build response
    let response = VolumeProfileResponse {
        symbol: params.symbol.clone(),
        total_volume: total_volume_u64,
        total_minutes: filtered.len(),
        price_range: PriceRange { low: session_low, high: session_high, spread: session_high - session_low },
        poc,
        value_area,
        profile: calculate_poc_and_percentages(profile, total_volume),
        statistics,
    };

    let analysis_date = if start_date_str == end_date_str {
        start_date_str
    } else {
        format!("{} to {}", start_date_str, end_date_str)
    };

    (StatusCode::OK, Json(AnalysisResponse {
        analysis_date,
        analysis_type: "volume_profile".to_string(),
        total_analyzed: filtered.len(),
        data: response,
    })).into_response()
}

// Helper to both compute POC and add percentages (single pass, returns final profile)
fn calculate_poc_and_percentages(profile: Vec<PriceLevelVolume>, _total_volume: f64) -> Vec<PriceLevelVolume> {
    // Percentages are already added above; just return
    profile
}

fn aggregate_into_bins(profile: Vec<PriceLevelVolume>, num_bins: usize) -> Vec<PriceLevelVolume> {
    if profile.len() <= num_bins { return profile; }

    let price_min = profile.first().unwrap().price;
    let price_max = profile.last().unwrap().price;
    let bin_size = (price_max - price_min) / num_bins as f64;
    if bin_size <= 0.0 { return profile; }

    let mut bins = vec![PriceLevelVolume { price: 0.0, volume: 0.0, percentage: 0.0, cumulative_percentage: 0.0 }; num_bins];

    for level in profile {
        let bin_idx = ((level.price - price_min) / bin_size).floor() as usize;
        let bin_idx = bin_idx.min(num_bins - 1);
        bins[bin_idx].volume += level.volume;
        bins[bin_idx].price = price_min + (bin_idx as f64 + 0.5) * bin_size;
    }

    bins.retain(|b| b.volume > 0.0);
    bins
}

fn add_percentages(profile: &mut [PriceLevelVolume], total_volume: f64) {
    let mut cumulative = 0.0;
    for level in profile.iter_mut() {
        level.percentage = if total_volume > 0.0 { (level.volume / total_volume) * 100.0 } else { 0.0 };
        cumulative += level.percentage;
        level.cumulative_percentage = cumulative;
    }
}

fn calculate_value_area(profile: &[PriceLevelVolume], poc_price: f64, total_volume: f64, target_percentage: f64) -> ValueArea {
    if profile.is_empty() || total_volume == 0.0 {
        return ValueArea { low: 0.0, high: 0.0, volume: 0.0, percentage: 0.0 };
    }

    let target_volume = total_volume * (target_percentage / 100.0);
    let poc_idx = profile.iter().position(|p| (p.price - poc_price).abs() < 0.01).unwrap_or(0);

    let mut va_low_idx = poc_idx;
    let mut va_high_idx = poc_idx;
    let mut accumulated_volume = profile[poc_idx].volume;

    while accumulated_volume < target_volume {
        let vol_below = if va_low_idx > 0 { profile[va_low_idx - 1].volume } else { 0.0 };
        let vol_above = if va_high_idx < profile.len() - 1 { profile[va_high_idx + 1].volume } else { 0.0 };

        if vol_below == 0.0 && vol_above == 0.0 { break; }

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
        percentage: if total_volume > 0.0 { (accumulated_volume / total_volume) * 100.0 } else { 0.0 },
    }
}

fn calculate_statistics(profile: &[PriceLevelVolume], total_volume: f64) -> VolumeStatistics {
    if profile.is_empty() || total_volume == 0.0 {
        return VolumeStatistics { mean_price: 0.0, median_price: 0.0, std_deviation: 0.0, skewness: 0.0 };
    }

    let mean_price: f64 = profile.iter().map(|p| p.price * p.volume).sum::<f64>() / total_volume;

    let mut cumulative = 0.0;
    let mut median_price = profile[0].price;
    for level in profile {
        cumulative += level.volume;
        if cumulative >= total_volume / 2.0 {
            median_price = level.price;
            break;
        }
    }

    let variance: f64 = profile.iter().map(|p| { let diff = p.price - mean_price; diff * diff * p.volume }).sum::<f64>() / total_volume;
    let std_deviation = variance.sqrt();

    let skewness = if std_deviation > 0.0 {
        let m3: f64 = profile.iter().map(|p| { let diff = p.price - mean_price; diff.powi(3) * p.volume }).sum::<f64>() / total_volume;
        m3 / std_deviation.powi(3)
    } else {
        0.0
    };

    VolumeStatistics { mean_price, median_price, std_deviation, skewness }
}
