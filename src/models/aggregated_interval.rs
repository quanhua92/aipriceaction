use super::Interval;
use serde::{Deserialize, Serialize};
use std::fmt;

/// Aggregated interval types for market data
///
/// These intervals are computed by aggregating base intervals (1m or 1D):
/// - Minute-based aggregations (5m, 15m, 30m) are computed from 1m data
/// - Day-based aggregations (1W, 2W, 1M) are computed from 1D data
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum AggregatedInterval {
    /// 5-minute candles (aggregated from 1m)
    Minutes5,
    /// 15-minute candles (aggregated from 1m)
    Minutes15,
    /// 30-minute candles (aggregated from 1m)
    Minutes30,
    /// Weekly candles - Monday to Sunday (aggregated from 1D)
    Week,
    /// Bi-weekly candles (aggregated from 1D)
    Week2,
    /// Monthly candles - calendar month (aggregated from 1D)
    Month,
}

impl AggregatedInterval {
    /// Parse from string representation
    ///
    /// # Arguments
    /// * `s` - String like "5m", "15m", "30m", "1W", "2W", "1M"
    ///
    /// # Returns
    /// Some(AggregatedInterval) if valid, None otherwise
    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "5m" => Some(AggregatedInterval::Minutes5),
            "15m" => Some(AggregatedInterval::Minutes15),
            "30m" => Some(AggregatedInterval::Minutes30),
            "1W" => Some(AggregatedInterval::Week),
            "2W" => Some(AggregatedInterval::Week2),
            "1M" => Some(AggregatedInterval::Month),
            _ => None,
        }
    }

    /// Get the base interval needed to compute this aggregated interval
    ///
    /// # Returns
    /// - Interval::Minute for minute-based aggregations (5m, 15m, 30m)
    /// - Interval::Daily for day-based aggregations (1W, 2W, 1M)
    pub fn base_interval(&self) -> Interval {
        match self {
            AggregatedInterval::Minutes5
            | AggregatedInterval::Minutes15
            | AggregatedInterval::Minutes30 => Interval::Minute,
            AggregatedInterval::Week | AggregatedInterval::Week2 | AggregatedInterval::Month => {
                Interval::Daily
            }
        }
    }

    /// Convert to string representation
    ///
    /// # Returns
    /// String like "5m", "15m", "30m", "1W", "2W", "1M"
    pub fn to_string(&self) -> &'static str {
        match self {
            AggregatedInterval::Minutes5 => "5m",
            AggregatedInterval::Minutes15 => "15m",
            AggregatedInterval::Minutes30 => "30m",
            AggregatedInterval::Week => "1W",
            AggregatedInterval::Week2 => "2W",
            AggregatedInterval::Month => "1M",
        }
    }

    /// Get the aggregation bucket size in minutes (for minute-based intervals)
    ///
    /// # Returns
    /// Number of minutes per bucket, or None for day-based intervals
    pub fn bucket_minutes(&self) -> Option<i64> {
        match self {
            AggregatedInterval::Minutes5 => Some(5),
            AggregatedInterval::Minutes15 => Some(15),
            AggregatedInterval::Minutes30 => Some(30),
            _ => None,
        }
    }
}

impl fmt::Display for AggregatedInterval {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_aggregated_interval() {
        assert_eq!(
            AggregatedInterval::from_str("5m"),
            Some(AggregatedInterval::Minutes5)
        );
        assert_eq!(
            AggregatedInterval::from_str("15m"),
            Some(AggregatedInterval::Minutes15)
        );
        assert_eq!(
            AggregatedInterval::from_str("30m"),
            Some(AggregatedInterval::Minutes30)
        );
        assert_eq!(
            AggregatedInterval::from_str("1W"),
            Some(AggregatedInterval::Week)
        );
        assert_eq!(
            AggregatedInterval::from_str("2W"),
            Some(AggregatedInterval::Week2)
        );
        assert_eq!(
            AggregatedInterval::from_str("1M"),
            Some(AggregatedInterval::Month)
        );
        assert_eq!(AggregatedInterval::from_str("invalid"), None);
    }

    #[test]
    fn test_base_interval() {
        assert_eq!(
            AggregatedInterval::Minutes5.base_interval(),
            Interval::Minute
        );
        assert_eq!(
            AggregatedInterval::Minutes15.base_interval(),
            Interval::Minute
        );
        assert_eq!(AggregatedInterval::Week.base_interval(), Interval::Daily);
        assert_eq!(AggregatedInterval::Month.base_interval(), Interval::Daily);
    }

    #[test]
    fn test_bucket_minutes() {
        assert_eq!(AggregatedInterval::Minutes5.bucket_minutes(), Some(5));
        assert_eq!(AggregatedInterval::Minutes15.bucket_minutes(), Some(15));
        assert_eq!(AggregatedInterval::Minutes30.bucket_minutes(), Some(30));
        assert_eq!(AggregatedInterval::Week.bucket_minutes(), None);
        assert_eq!(AggregatedInterval::Month.bucket_minutes(), None);
    }
}
