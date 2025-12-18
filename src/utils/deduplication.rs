//! Interval-Aware Deduplication Utilities
//!
//! Provides centralized, interval-appropriate deduplication logic to prevent
//! data loss in intraday intervals where date_naive() is the wrong granularity.

use crate::models::{Interval, StockData};
use std::collections::HashSet;
use chrono::Timelike;

/// Centralized deduplication with interval-aware granularity
pub struct IntervalDeduplicator {
    seen_keys: HashSet<String>,
}

impl IntervalDeduplicator {
    /// Create a new deduplicator
    pub fn new() -> Self {
        Self {
            seen_keys: HashSet::new(),
        }
    }

    /// Get appropriate deduplication key for interval
    ///
    /// Returns different granularity based on interval:
    /// - Minute: Full timestamp (unique for each minute)
    /// - Hourly: YYYY-MM-DD-HH format (one key per hour)
    /// - Daily: YYYY-MM-DD format (one key per day)
    pub fn get_key(record: &StockData, interval: Interval) -> String {
        match interval {
            // For minute intervals, use full timestamp - no two records have same timestamp
            Interval::Minute => record.time.to_rfc3339(),

            // For hourly intervals, use YYYY-MM-DD-HH format
            Interval::Hourly => format!("{}-{:02}",
                record.time.date_naive(),
                record.time.hour(),
            ),

            // For daily intervals, use date only (current behavior)
            Interval::Daily => record.time.date_naive().to_string(),
        }
    }

    /// Check if record is duplicate
    pub fn is_duplicate(&mut self, record: &StockData, interval: Interval) -> bool {
        let key = Self::get_key(record, interval);
        !self.seen_keys.insert(key)
    }

    /// Filter duplicates from vector, keeping last occurrence
    ///
    /// Returns filtered references to unique records. The original vector is not modified.
    /// Use `keep_last=true` to keep the last occurrence of duplicates, `false` to keep first.
    pub fn filter_duplicates<'a>(
        records: &'a [StockData],
        interval: Interval,
        keep_last: bool,
    ) -> Vec<&'a StockData> {
        let mut seen_keys = HashSet::new();
        let mut filtered = Vec::new();

        // Process in reverse if we want to keep last occurrence
        let iter: Box<dyn Iterator<Item = &StockData>> = if keep_last {
            Box::new(records.iter().rev())
        } else {
            Box::new(records.iter())
        };

        for record in iter {
            let key = Self::get_key(record, interval);
            if seen_keys.insert(key) {
                filtered.push(record);
            }
        }

        // Restore original order if we processed in reverse
        if keep_last {
            filtered.reverse();
        }

        filtered
    }

    /// Filter duplicates and return owned records
    ///
    /// Convenience function that returns owned StockData instead of references.
    /// This clones the filtered records.
    pub fn filter_duplicates_owned(
        records: &[StockData],
        interval: Interval,
        keep_last: bool,
    ) -> Vec<StockData> {
        let filtered_refs = Self::filter_duplicates(records, interval, keep_last);
        filtered_refs.into_iter().cloned().collect()
    }

    /// Count duplicates in dataset
    pub fn count_duplicates(records: &[StockData], interval: Interval) -> usize {
        let mut seen_keys = HashSet::new();
        let mut duplicate_count = 0;

        for record in records {
            let key = Self::get_key(record, interval);
            if !seen_keys.insert(key) {
                duplicate_count += 1;
            }
        }

        duplicate_count
    }

    /// Get duplicate information for debugging
    pub fn get_duplicate_info(records: &[StockData], interval: Interval) -> Vec<(String, usize)> {
        let mut key_counts = std::collections::HashMap::new();

        for record in records {
            let key = Self::get_key(record, interval);
            *key_counts.entry(key).or_insert(0) += 1;
        }

        key_counts
            .into_iter()
            .filter(|(_, count)| *count > 1)
            .collect()
    }
}

/// Convenience functions for common use cases

/// Filter duplicate records from a vector, keeping the last occurrence
pub fn filter_duplicate_records(
    records: Vec<StockData>,
    interval: Interval,
) -> Vec<StockData> {
    let filtered_refs = IntervalDeduplicator::filter_duplicates(&records, interval, true);
    filtered_refs.into_iter().cloned().collect()
}

/// Filter duplicate records from a vector, keeping the first occurrence
pub fn filter_duplicate_records_first(
    records: Vec<StockData>,
    interval: Interval,
) -> Vec<StockData> {
    let filtered_refs = IntervalDeduplicator::filter_duplicates(&records, interval, false);
    filtered_refs.into_iter().cloned().collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{DateTime, Utc};

    fn create_test_record(time_str: &str, ticker: &str, price: f64) -> StockData {
        let time = DateTime::parse_from_rfc3339(time_str).unwrap();
        StockData::new(time, ticker.to_string(), price, price, price, price, 1000)
    }

    #[test]
    fn test_minute_deduplication() {
        let records = vec![
            create_test_record("2025-12-18T09:30:00Z", "VCB", 100.0),
            create_test_record("2025-12-18T09:31:00Z", "VCB", 101.0),
            create_test_record("2025-12-18T09:30:00Z", "VCB", 100.5), // Duplicate timestamp
        ];

        let filtered = IntervalDeduplicator::filter_duplicates(&records, Interval::Minute, true);
        assert_eq!(filtered.len(), 3); // All have unique timestamps except the duplicate

        // Should have 1 duplicate (the duplicate timestamp)
        let duplicate_count = IntervalDeduplicator::count_duplicates(&records, Interval::Minute);
        assert_eq!(duplicate_count, 1);
    }

    #[test]
    fn test_hourly_deduplication() {
        let records = vec![
            create_test_record("2025-12-18T09:00:00Z", "VCB", 100.0),
            create_test_record("2025-12-18T09:30:00Z", "VCB", 101.0), // Same hour
            create_test_record("2025-12-18T09:45:00Z", "VCB", 102.0), // Same hour
            create_test_record("2025-12-18T10:00:00Z", "VCB", 103.0), // Different hour
        ];

        let filtered = IntervalDeduplicator::filter_duplicates(&records, Interval::Hourly, true);
        assert_eq!(filtered.len(), 2); // One from 09:xx, one from 10:xx

        let duplicate_count = IntervalDeduplicator::count_duplicates(&records, Interval::Hourly);
        assert_eq!(duplicate_count, 2);
    }

    #[test]
    fn test_daily_deduplication() {
        let records = vec![
            create_test_record("2025-12-18T09:30:00Z", "VCB", 100.0),
            create_test_record("2025-12-18T14:15:00Z", "VCB", 101.0), // Same day
            create_test_record("2025-12-19T09:30:00Z", "VCB", 102.0), // Different day
        ];

        let filtered = IntervalDeduplicator::filter_duplicates(&records, Interval::Daily, true);
        assert_eq!(filtered.len(), 2); // One from each day

        let duplicate_count = IntervalDuplicator::count_duplicates(&records, Interval::Daily);
        assert_eq!(duplicate_count, 1);
    }

    #[test]
    fn test_filter_keep_first() {
        let records = vec![
            create_test_record("2025-12-18T09:00:00Z", "VCB", 100.0), // First
            create_test_record("2025-12-18T09:30:00Z", "VCB", 101.0), // Middle
            create_test_record("2025-12-18T09:00:00Z", "VCB", 100.5), // Duplicate (first)
        ];

        let filtered = IntervalDeduplicator::filter_duplicates(&records, Interval::Minute, false);
        assert_eq!(filtered.len(), 2); // First and middle, last duplicate removed
        assert_eq!(filtered[0].close, 100.0); // First occurrence kept
        assert_eq!(filtered[1].close, 101.0); // Middle occurrence kept
    }
}