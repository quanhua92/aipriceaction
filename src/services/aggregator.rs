use crate::models::{AggregatedInterval, StockData};
use crate::models::indicators::{calculate_sma, calculate_ma_score};
use chrono::{DateTime, Datelike, Duration, TimeZone, Timelike, Utc};
use std::collections::HashMap;
use tracing::debug;

/// Service for aggregating stock data into different timeframes
pub struct Aggregator;

impl Aggregator {
    /// Aggregate minute data (1m → 5m/15m/30m)
    ///
    /// # Arguments
    /// * `data` - Vector of 1-minute stock data records
    /// * `interval` - Target aggregated interval (Minutes5, Minutes15, or Minutes30)
    ///
    /// # Returns
    /// Vector of aggregated stock data
    pub fn aggregate_minute_data(
        data: Vec<StockData>,
        interval: AggregatedInterval,
    ) -> Vec<StockData> {
        if data.is_empty() {
            return vec![];
        }

        let bucket_minutes = match interval.bucket_minutes() {
            Some(minutes) => minutes,
            None => {
                debug!("Invalid interval for minute aggregation: {}", interval);
                return data;
            }
        };

        debug!(
            "Aggregating {} minute records into {} buckets",
            data.len(),
            interval
        );

        // Group records by time bucket
        let buckets = Self::group_by_minute_bucket(data, bucket_minutes);

        // Aggregate each bucket
        let mut result: Vec<StockData> = buckets
            .into_iter()
            .map(|(bucket_time, records)| Self::aggregate_ohlcv(records, bucket_time))
            .collect();

        // Sort by time
        result.sort_by_key(|r| r.time);

        debug!("Aggregated into {} records", result.len());
        result
    }

    /// Aggregate daily data (1D → 1W/2W/1M)
    ///
    /// # Arguments
    /// * `data` - Vector of daily stock data records
    /// * `interval` - Target aggregated interval (Week, Week2, or Month)
    ///
    /// # Returns
    /// Vector of aggregated stock data
    pub fn aggregate_daily_data(
        data: Vec<StockData>,
        interval: AggregatedInterval,
    ) -> Vec<StockData> {
        if data.is_empty() {
            return vec![];
        }

        debug!(
            "Aggregating {} daily records into {} buckets",
            data.len(),
            interval
        );

        // Group records by time bucket
        let buckets = match interval {
            AggregatedInterval::Week => Self::group_by_week(data),
            AggregatedInterval::Week2 => Self::group_by_2week(data),
            AggregatedInterval::Month => Self::group_by_month(data),
            _ => {
                debug!("Invalid interval for daily aggregation: {}", interval);
                return data;
            }
        };

        // Aggregate each bucket
        let mut result: Vec<StockData> = buckets
            .into_iter()
            .map(|(bucket_time, records)| Self::aggregate_ohlcv(records, bucket_time))
            .collect();

        // Sort by time
        result.sort_by_key(|r| r.time);

        debug!("Aggregated into {} records", result.len());
        result
    }

    /// Group minute records by time bucket (5m, 15m, 30m)
    fn group_by_minute_bucket(
        data: Vec<StockData>,
        bucket_minutes: i64,
    ) -> HashMap<DateTime<Utc>, Vec<StockData>> {
        let mut buckets: HashMap<DateTime<Utc>, Vec<StockData>> = HashMap::new();

        for record in data {
            let bucket_time = Self::bucket_minute(record.time, bucket_minutes);
            buckets.entry(bucket_time).or_default().push(record);
        }

        buckets
    }

    /// Group daily records by week (Monday-Sunday)
    fn group_by_week(data: Vec<StockData>) -> HashMap<DateTime<Utc>, Vec<StockData>> {
        let mut buckets: HashMap<DateTime<Utc>, Vec<StockData>> = HashMap::new();

        for record in data {
            let bucket_time = Self::bucket_week(record.time);
            buckets.entry(bucket_time).or_default().push(record);
        }

        buckets
    }

    /// Group daily records by 2-week period
    fn group_by_2week(data: Vec<StockData>) -> HashMap<DateTime<Utc>, Vec<StockData>> {
        let mut buckets: HashMap<DateTime<Utc>, Vec<StockData>> = HashMap::new();

        for record in data {
            let bucket_time = Self::bucket_2week(record.time);
            buckets.entry(bucket_time).or_default().push(record);
        }

        buckets
    }

    /// Group daily records by calendar month
    fn group_by_month(data: Vec<StockData>) -> HashMap<DateTime<Utc>, Vec<StockData>> {
        let mut buckets: HashMap<DateTime<Utc>, Vec<StockData>> = HashMap::new();

        for record in data {
            let bucket_time = Self::bucket_month(record.time);
            buckets.entry(bucket_time).or_default().push(record);
        }

        buckets
    }

    /// Calculate minute bucket start time
    ///
    /// # Arguments
    /// * `time` - Original timestamp
    /// * `bucket_minutes` - Bucket size in minutes (5, 15, or 30)
    ///
    /// # Returns
    /// Bucket start time (rounded down to nearest bucket boundary)
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

    /// Calculate week bucket start time (Monday 00:00:00)
    ///
    /// Uses ISO 8601 week definition: weeks start on Monday
    fn bucket_week(time: DateTime<Utc>) -> DateTime<Utc> {
        let weekday = time.weekday();
        let days_from_monday = weekday.num_days_from_monday();

        let monday = if days_from_monday == 0 {
            time.date_naive()
        } else {
            time.date_naive() - Duration::days(days_from_monday as i64)
        };

        Utc.from_utc_datetime(&monday.and_hms_opt(0, 0, 0).unwrap())
    }

    /// Calculate 2-week bucket start time
    ///
    /// Uses ISO week numbers and groups by even/odd weeks
    fn bucket_2week(time: DateTime<Utc>) -> DateTime<Utc> {
        let week_start = Self::bucket_week(time);
        let iso_week = time.iso_week().week();

        // Group by even/odd week numbers
        if iso_week % 2 == 0 {
            week_start
        } else {
            // If odd week, return previous week's Monday
            week_start - Duration::weeks(1)
        }
    }

    /// Calculate month bucket start time (1st day of month at 00:00:00)
    fn bucket_month(time: DateTime<Utc>) -> DateTime<Utc> {
        Utc.with_ymd_and_hms(time.year(), time.month(), 1, 0, 0, 0)
            .unwrap()
    }

    /// Aggregate OHLCV data for a time bucket
    ///
    /// # Arguments
    /// * `records` - Vector of stock data records in the same bucket
    /// * `bucket_time` - Start time of the bucket
    ///
    /// # Returns
    /// Aggregated stock data with:
    /// - open = first record's open
    /// - high = maximum high
    /// - low = minimum low
    /// - close = last record's close
    /// - volume = sum of volumes
    /// - time = bucket start time
    /// - MA indicators = last record's values (represents end-of-period state)
    fn aggregate_ohlcv(mut records: Vec<StockData>, bucket_time: DateTime<Utc>) -> StockData {
        // Sort by time to ensure correct order
        records.sort_by_key(|r| r.time);

        let first = &records[0];
        let last = &records[records.len() - 1];

        let open = first.open;
        let close = last.close;
        let high = records.iter().map(|r| r.high).fold(f64::NEG_INFINITY, f64::max);
        let low = records.iter().map(|r| r.low).fold(f64::INFINITY, f64::min);
        let volume = records.iter().map(|r| r.volume).sum();

        StockData {
            ticker: first.ticker.clone(),
            time: bucket_time,
            open,
            high,
            low,
            close,
            volume,
            // Technical indicators will be calculated after aggregation
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
            // Change indicators will be calculated after aggregation
            close_changed: None,
            volume_changed: None,
        }
    }

    /// Calculate close_changed and volume_changed for aggregated data
    ///
    /// Computes percentage changes between consecutive aggregated records:
    /// - close_changed = ((curr_close - prev_close) / prev_close) * 100
    /// - volume_changed = ((curr_volume - prev_volume) / prev_volume) * 100
    ///
    /// # Arguments
    /// * `data` - Vector of aggregated stock data (must be sorted by time)
    ///
    /// # Returns
    /// Same vector with close_changed and volume_changed calculated
    ///
    /// # Note
    /// - First record keeps None (no previous record)
    /// - Division by zero returns None
    pub fn calculate_changes(mut data: Vec<StockData>) -> Vec<StockData> {
        // Calculate changes for records starting from index 1
        for i in 1..data.len() {
            let prev_close = data[i - 1].close;
            let prev_volume = data[i - 1].volume;
            let curr = &mut data[i];

            // Close changed: ((curr - prev) / prev) * 100
            if prev_close > 0.0 {
                curr.close_changed = Some(((curr.close - prev_close) / prev_close) * 100.0);
            }

            // Volume changed: ((curr - prev) / prev) * 100
            if prev_volume > 0 {
                curr.volume_changed = Some(((curr.volume as f64 - prev_volume as f64) / prev_volume as f64) * 100.0);
            }
        }

        data
    }

    /// Enhance aggregated data with technical indicators (MA and scores)
    ///
    /// This function applies the same logic as csv_enhancer.rs but on aggregated data.
    /// It calculates moving averages and scores based on the aggregated data's own history.
    ///
    /// # Arguments
    /// * `data` - HashMap of ticker to aggregated StockData vectors (must be sorted by time)
    ///
    /// # Returns
    /// Same HashMap with technical indicators calculated
    pub fn enhance_aggregated_data(
        mut data: HashMap<String, Vec<StockData>>,
    ) -> HashMap<String, Vec<StockData>> {
        for (_ticker, stock_data) in data.iter_mut() {
            if stock_data.is_empty() {
                continue;
            }

            // Calculate moving averages based on aggregated close prices
            let closes: Vec<f64> = stock_data.iter().map(|d| d.close).collect();
            let ma10_values = calculate_sma(&closes, 10);
            let ma20_values = calculate_sma(&closes, 20);
            let ma50_values = calculate_sma(&closes, 50);
            let ma100_values = calculate_sma(&closes, 100);
            let ma200_values = calculate_sma(&closes, 200);

            // Update StockData with MA values and scores
            for (i, stock) in stock_data.iter_mut().enumerate() {
                // Set MA values and calculate scores
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

            // Calculate close_changed and volume_changed in a second pass
            for i in 1..stock_data.len() {
                let prev_close = stock_data[i - 1].close;
                let prev_volume = stock_data[i - 1].volume;
                let curr = &mut stock_data[i];

                // Close changed: ((curr - prev) / prev) * 100
                if prev_close > 0.0 {
                    curr.close_changed = Some(((curr.close - prev_close) / prev_close) * 100.0);
                }

                // Volume changed: ((curr - prev) / prev) * 100
                if prev_volume > 0 {
                    curr.volume_changed = Some(((curr.volume as f64 - prev_volume as f64) / prev_volume as f64) * 100.0);
                }
            }
        }

        data
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn create_test_data(ticker: &str, timestamps: Vec<DateTime<Utc>>, closes: Vec<f64>) -> Vec<StockData> {
        timestamps
            .into_iter()
            .zip(closes.iter())
            .enumerate()
            .map(|(i, (time, &close))| StockData {
                ticker: ticker.to_string(),
                time,
                open: close - 1.0,
                high: close + 1.0,
                low: close - 2.0,
                close,
                volume: (i + 1) as u64 * 1000,
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
            })
            .collect()
    }

    #[test]
    fn test_bucket_minute_5m() {
        let time = Utc.with_ymd_and_hms(2025, 11, 8, 9, 7, 30).unwrap();
        let bucket = Aggregator::bucket_minute(time, 5);
        assert_eq!(bucket, Utc.with_ymd_and_hms(2025, 11, 8, 9, 5, 0).unwrap());

        let time = Utc.with_ymd_and_hms(2025, 11, 8, 9, 14, 59).unwrap();
        let bucket = Aggregator::bucket_minute(time, 5);
        assert_eq!(bucket, Utc.with_ymd_and_hms(2025, 11, 8, 9, 10, 0).unwrap());
    }

    #[test]
    fn test_bucket_week() {
        // Wednesday Nov 6, 2025
        let time = Utc.with_ymd_and_hms(2025, 11, 6, 15, 30, 0).unwrap();
        let bucket = Aggregator::bucket_week(time);
        // Should return Monday Nov 4, 2025
        assert_eq!(bucket, Utc.with_ymd_and_hms(2025, 11, 3, 0, 0, 0).unwrap());
    }

    #[test]
    fn test_bucket_month() {
        let time = Utc.with_ymd_and_hms(2025, 11, 15, 12, 30, 45).unwrap();
        let bucket = Aggregator::bucket_month(time);
        assert_eq!(bucket, Utc.with_ymd_and_hms(2025, 11, 1, 0, 0, 0).unwrap());
    }

    #[test]
    fn test_aggregate_minute_data_5m() {
        let timestamps = vec![
            Utc.with_ymd_and_hms(2025, 11, 8, 9, 0, 0).unwrap(),
            Utc.with_ymd_and_hms(2025, 11, 8, 9, 1, 0).unwrap(),
            Utc.with_ymd_and_hms(2025, 11, 8, 9, 4, 0).unwrap(),
            Utc.with_ymd_and_hms(2025, 11, 8, 9, 5, 0).unwrap(),
            Utc.with_ymd_and_hms(2025, 11, 8, 9, 9, 0).unwrap(),
        ];
        let closes = vec![100.0, 101.0, 102.0, 103.0, 104.0];
        let data = create_test_data("VCB", timestamps, closes);

        let aggregated = Aggregator::aggregate_minute_data(data, AggregatedInterval::Minutes5);

        assert_eq!(aggregated.len(), 2); // Two 5-minute buckets
        assert_eq!(aggregated[0].time, Utc.with_ymd_and_hms(2025, 11, 8, 9, 0, 0).unwrap());
        assert_eq!(aggregated[0].open, 99.0); // First record's open
        assert_eq!(aggregated[0].close, 102.0); // Last record in bucket's close
        assert_eq!(aggregated[0].volume, 6000); // Sum: 1000+2000+3000
    }
}
