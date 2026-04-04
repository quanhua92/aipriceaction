use crate::models::aggregated_interval::AggregatedInterval;
use crate::models::indicators::{calculate_ma_score, calculate_sma};
use crate::models::ohlcv::OhlcvRow;
use chrono::{DateTime, Datelike, Duration, TimeZone, Timelike, Utc};
use std::collections::HashMap;
use tracing::debug;

/// Service for aggregating OHLCV data into different timeframes.
pub struct Aggregator;

/// Intermediate structure used during aggregation before converting to OhlcvJoined.
#[derive(Debug, Clone)]
pub struct AggregatedOhlcv {
    pub ticker: String,
    pub time: DateTime<Utc>,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: i64,
    pub ma10: Option<f64>,
    pub ma20: Option<f64>,
    pub ma50: Option<f64>,
    pub ma100: Option<f64>,
    pub ma200: Option<f64>,
    pub ma10_score: Option<f64>,
    pub ma20_score: Option<f64>,
    pub ma50_score: Option<f64>,
    pub ma100_score: Option<f64>,
    pub ma200_score: Option<f64>,
    pub close_changed: Option<f64>,
    pub volume_changed: Option<f64>,
    pub total_money_changed: Option<f64>,
}

impl Aggregator {
    /// Aggregate minute data (1m → 5m/15m/30m).
    pub fn aggregate_minute_data(
        ticker: &str,
        data: Vec<OhlcvRow>,
        interval: AggregatedInterval,
    ) -> Vec<AggregatedOhlcv> {
        if data.is_empty() {
            return vec![];
        }

        let bucket_minutes = match interval.bucket_minutes() {
            Some(minutes) => minutes,
            None => return vec![],
        };

        debug!(
            "Aggregating {} minute records into {} buckets",
            data.len(),
            interval
        );

        let buckets = Self::group_by_minute_bucket(data, bucket_minutes);

        let mut result: Vec<AggregatedOhlcv> = buckets
            .into_iter()
            .map(|(bucket_time, records)| Self::aggregate_ohlcv(ticker, records, bucket_time))
            .collect();

        result.sort_by_key(|r| r.time);
        result
    }

    /// Aggregate hourly data (1h → 4h).
    ///
    /// `offset_hours` shifts bucket alignment. For VN stocks use 2 (market open
    /// 09:00 ICT = 02:00 UTC), for crypto use 0 (midnight UTC alignment).
    pub fn aggregate_hourly_data(
        ticker: &str,
        data: Vec<OhlcvRow>,
        interval: AggregatedInterval,
        offset_hours: i64,
    ) -> Vec<AggregatedOhlcv> {
        if data.is_empty() {
            return vec![];
        }

        let bucket_hours = match interval.bucket_hours() {
            Some(hours) => hours,
            None => return vec![],
        };

        debug!(
            "Aggregating {} hourly records into {} buckets (offset {}h)",
            data.len(),
            interval,
            offset_hours
        );

        let buckets = Self::group_by_hour_bucket(data, bucket_hours, offset_hours);

        let mut result: Vec<AggregatedOhlcv> = buckets
            .into_iter()
            .map(|(bucket_time, records)| Self::aggregate_ohlcv(ticker, records, bucket_time))
            .collect();

        result.sort_by_key(|r| r.time);
        result
    }

    /// Aggregate daily data (1D → 1W/2W/1M).
    pub fn aggregate_daily_data(
        ticker: &str,
        data: Vec<OhlcvRow>,
        interval: AggregatedInterval,
    ) -> Vec<AggregatedOhlcv> {
        if data.is_empty() {
            return vec![];
        }

        debug!(
            "Aggregating {} daily records into {} buckets",
            data.len(),
            interval
        );

        let buckets = match interval {
            AggregatedInterval::Week => Self::group_by_week(data),
            AggregatedInterval::Week2 => Self::group_by_2week(data),
            AggregatedInterval::Month => Self::group_by_month(data),
            _ => return vec![],
        };

        let mut result: Vec<AggregatedOhlcv> = buckets
            .into_iter()
            .map(|(bucket_time, records)| Self::aggregate_ohlcv(ticker, records, bucket_time))
            .collect();

        result.sort_by_key(|r| r.time);
        result
    }

    /// Enhance aggregated data with technical indicators.
    pub fn enhance_aggregated_data(
        mut data: HashMap<String, Vec<AggregatedOhlcv>>,
    ) -> HashMap<String, Vec<AggregatedOhlcv>> {
        for stock_data in data.values_mut() {
            if stock_data.is_empty() {
                continue;
            }

            let closes: Vec<f64> = stock_data.iter().map(|d| d.close).collect();
            let ma10_values = calculate_sma(&closes, 10);
            let ma20_values = calculate_sma(&closes, 20);
            let ma50_values = calculate_sma(&closes, 50);
            let ma100_values = calculate_sma(&closes, 100);
            let ma200_values = calculate_sma(&closes, 200);

            for (i, stock) in stock_data.iter_mut().enumerate() {
                if ma10_values[i] > 0.0 {
                    stock.ma10 = Some(ma10_values[i]);
                    stock.ma10_score = Some(calculate_ma_score(stock.close, ma10_values[i]));
                }
                if ma20_values[i] > 0.0 {
                    stock.ma20 = Some(ma20_values[i]);
                    stock.ma20_score = Some(calculate_ma_score(stock.close, ma20_values[i]));
                }
                if ma50_values[i] > 0.0 {
                    stock.ma50 = Some(ma50_values[i]);
                    stock.ma50_score = Some(calculate_ma_score(stock.close, ma50_values[i]));
                }
                if ma100_values[i] > 0.0 {
                    stock.ma100 = Some(ma100_values[i]);
                    stock.ma100_score = Some(calculate_ma_score(stock.close, ma100_values[i]));
                }
                if ma200_values[i] > 0.0 {
                    stock.ma200 = Some(ma200_values[i]);
                    stock.ma200_score = Some(calculate_ma_score(stock.close, ma200_values[i]));
                }
            }

            // Calculate change indicators
            for i in 1..stock_data.len() {
                let prev_close = stock_data[i - 1].close;
                let prev_volume = stock_data[i - 1].volume;
                let curr = &mut stock_data[i];

                if prev_close > 0.0 {
                    curr.close_changed = Some(((curr.close - prev_close) / prev_close) * 100.0);
                }

                if prev_volume > 0 {
                    curr.volume_changed =
                        Some(((curr.volume as f64 - prev_volume as f64) / prev_volume as f64) * 100.0);
                }

                let price_change = curr.close - prev_close;
                curr.total_money_changed = Some(price_change * curr.volume as f64);
            }
        }

        data
    }

    // ── Private helpers ──

    fn group_by_minute_bucket(
        data: Vec<OhlcvRow>,
        bucket_minutes: i64,
    ) -> HashMap<DateTime<Utc>, Vec<OhlcvRow>> {
        let mut buckets: HashMap<DateTime<Utc>, Vec<OhlcvRow>> = HashMap::new();
        for record in data {
            let bucket_time = Self::bucket_minute(record.time, bucket_minutes);
            buckets.entry(bucket_time).or_default().push(record);
        }
        buckets
    }

    fn group_by_hour_bucket(
        data: Vec<OhlcvRow>,
        bucket_hours: i64,
        offset_hours: i64,
    ) -> HashMap<DateTime<Utc>, Vec<OhlcvRow>> {
        let mut buckets: HashMap<DateTime<Utc>, Vec<OhlcvRow>> = HashMap::new();
        for record in data {
            let bucket_time = Self::bucket_hour(record.time, bucket_hours, offset_hours);
            buckets.entry(bucket_time).or_default().push(record);
        }
        buckets
    }

    fn group_by_week(data: Vec<OhlcvRow>) -> HashMap<DateTime<Utc>, Vec<OhlcvRow>> {
        let mut buckets: HashMap<DateTime<Utc>, Vec<OhlcvRow>> = HashMap::new();
        for record in data {
            let bucket_time = Self::bucket_week(record.time);
            buckets.entry(bucket_time).or_default().push(record);
        }
        buckets
    }

    fn group_by_2week(data: Vec<OhlcvRow>) -> HashMap<DateTime<Utc>, Vec<OhlcvRow>> {
        let mut buckets: HashMap<DateTime<Utc>, Vec<OhlcvRow>> = HashMap::new();
        for record in data {
            let bucket_time = Self::bucket_2week(record.time);
            buckets.entry(bucket_time).or_default().push(record);
        }
        buckets
    }

    fn group_by_month(data: Vec<OhlcvRow>) -> HashMap<DateTime<Utc>, Vec<OhlcvRow>> {
        let mut buckets: HashMap<DateTime<Utc>, Vec<OhlcvRow>> = HashMap::new();
        for record in data {
            let bucket_time = Self::bucket_month(record.time);
            buckets.entry(bucket_time).or_default().push(record);
        }
        buckets
    }

    fn bucket_minute(time: DateTime<Utc>, bucket_minutes: i64) -> DateTime<Utc> {
        let minutes_since_hour = time.minute() as i64;
        let bucket_start_minute = (minutes_since_hour / bucket_minutes) * bucket_minutes;

        Utc.with_ymd_and_hms(
            time.year(),
            time.month(),
            time.day(),
            time.hour(),
            bucket_start_minute as u32,
            0,
        )
        .unwrap()
    }

    fn bucket_hour(time: DateTime<Utc>, bucket_hours: i64, offset_hours: i64) -> DateTime<Utc> {
        // Shift time back by offset, bucket to midnight-aligned boundaries, then shift forward.
        let shifted = time - Duration::hours(offset_hours);
        let hours_since_midnight = shifted.hour() as i64;
        let bucket_start_hour = (hours_since_midnight / bucket_hours) * bucket_hours;

        Utc.with_ymd_and_hms(shifted.year(), shifted.month(), shifted.day(), bucket_start_hour as u32, 0, 0)
            .unwrap()
            + Duration::hours(offset_hours)
    }

    fn bucket_week(time: DateTime<Utc>) -> DateTime<Utc> {
        let days_from_monday = time.weekday().num_days_from_monday();
        let monday = if days_from_monday == 0 {
            time.date_naive()
        } else {
            time.date_naive() - Duration::days(days_from_monday as i64)
        };

        Utc.from_utc_datetime(&monday.and_hms_opt(0, 0, 0).unwrap())
    }

    fn bucket_2week(time: DateTime<Utc>) -> DateTime<Utc> {
        let week_start = Self::bucket_week(time);
        let iso_week = time.iso_week().week();

        if iso_week % 2 == 0 {
            week_start
        } else {
            week_start - Duration::weeks(1)
        }
    }

    fn bucket_month(time: DateTime<Utc>) -> DateTime<Utc> {
        Utc.with_ymd_and_hms(time.year(), time.month(), 1, 0, 0, 0)
            .unwrap()
    }

    fn aggregate_ohlcv(ticker: &str, mut records: Vec<OhlcvRow>, bucket_time: DateTime<Utc>) -> AggregatedOhlcv {
        records.sort_by_key(|r| r.time);

        let first = &records[0];
        let last = &records[records.len() - 1];

        let open = first.open;
        let close = last.close;
        let high = records
            .iter()
            .map(|r| r.high)
            .fold(f64::NEG_INFINITY, f64::max);
        let low = records.iter().map(|r| r.low).fold(f64::INFINITY, f64::min);
        let volume: i64 = records.iter().map(|r| r.volume).sum();

        AggregatedOhlcv {
            ticker: ticker.to_string(),
            time: bucket_time,
            open,
            high,
            low,
            close,
            volume,
            ma10: None,
            ma20: None,
            ma50: None,
            ma100: None,
            ma200: None,
            ma10_score: None,
            ma20_score: None,
            ma50_score: None,
            ma100_score: None,
            ma200_score: None,
            close_changed: None,
            volume_changed: None,
            total_money_changed: None,
        }
    }
}
