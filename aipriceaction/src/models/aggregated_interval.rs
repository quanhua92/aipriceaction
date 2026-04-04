use super::interval::Interval;
use serde::{Deserialize, Serialize};
use std::fmt;

/// Aggregated interval types for market data.
///
/// Minute-based (5m, 15m, 30m) are computed from 1m data.
/// Hourly-based (4h) is computed from 1h data.
/// Day-based (1W, 2W, 1M) are computed from 1D data.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum AggregatedInterval {
    Minutes5,
    Minutes15,
    Minutes30,
    Hours4,
    Week,
    Week2,
    Month,
}

impl AggregatedInterval {
    /// Parse from string: "5m", "15m", "30m", "4h", "1W", "2W", "1M"
    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "5m" => Some(AggregatedInterval::Minutes5),
            "15m" => Some(AggregatedInterval::Minutes15),
            "30m" => Some(AggregatedInterval::Minutes30),
            "4h" => Some(AggregatedInterval::Hours4),
            "1W" => Some(AggregatedInterval::Week),
            "2W" => Some(AggregatedInterval::Week2),
            "1M" => Some(AggregatedInterval::Month),
            _ => None,
        }
    }

    /// Get the base interval needed to compute this aggregated interval.
    pub fn base_interval(&self) -> Interval {
        match self {
            AggregatedInterval::Minutes5
            | AggregatedInterval::Minutes15
            | AggregatedInterval::Minutes30 => Interval::Minute,
            AggregatedInterval::Hours4 => Interval::Hourly,
            AggregatedInterval::Week | AggregatedInterval::Week2 | AggregatedInterval::Month => {
                Interval::Daily
            }
        }
    }

    /// Convert to string representation.
    pub fn to_str(&self) -> &'static str {
        match self {
            AggregatedInterval::Minutes5 => "5m",
            AggregatedInterval::Minutes15 => "15m",
            AggregatedInterval::Minutes30 => "30m",
            AggregatedInterval::Hours4 => "4h",
            AggregatedInterval::Week => "1W",
            AggregatedInterval::Week2 => "2W",
            AggregatedInterval::Month => "1M",
        }
    }

    /// Get the aggregation bucket size in minutes (for minute-based intervals).
    pub fn bucket_minutes(&self) -> Option<i64> {
        match self {
            AggregatedInterval::Minutes5 => Some(5),
            AggregatedInterval::Minutes15 => Some(15),
            AggregatedInterval::Minutes30 => Some(30),
            _ => None,
        }
    }

    /// Get the aggregation bucket size in hours (for hourly-based intervals).
    pub fn bucket_hours(&self) -> Option<i64> {
        match self {
            AggregatedInterval::Hours4 => Some(4),
            _ => None,
        }
    }
}

impl fmt::Display for AggregatedInterval {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.to_str())
    }
}
