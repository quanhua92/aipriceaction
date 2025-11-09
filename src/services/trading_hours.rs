use chrono::{Datelike, Timelike, Utc, Weekday};
use chrono_tz::Tz;
use std::time::Duration;

/// Trading hours configuration for Vietnam stock market
pub struct TradingHours {
    pub start_hour: u32,      // 9 for 9am
    pub end_hour: u32,        // 15 for 3pm
    pub timezone: &'static str, // "Asia/Ho_Chi_Minh"
    pub weekdays_only: bool,  // true for Monday-Friday only
}

impl Default for TradingHours {
    fn default() -> Self {
        Self {
            start_hour: 9,   // 9:00 AM
            end_hour: 15,    // 3:00 PM
            timezone: "Asia/Ho_Chi_Minh",
            weekdays_only: true,
        }
    }
}

/// Check if current time is within trading hours
pub fn is_trading_hours() -> bool {
    let config = TradingHours::default();

    // Parse timezone
    let tz: Tz = match config.timezone.parse() {
        Ok(tz) => tz,
        Err(e) => {
            tracing::warn!("Failed to parse timezone '{}': {}", config.timezone, e);
            return false; // Default to non-trading hours if timezone parsing fails
        }
    };

    // Get current time in Vietnam timezone
    let now_utc = Utc::now();
    let now_local = now_utc.with_timezone(&tz);

    // Check weekday if weekdays_only is true
    if config.weekdays_only {
        let weekday = now_local.weekday();
        match weekday {
            Weekday::Mon | Weekday::Tue | Weekday::Wed | Weekday::Thu | Weekday::Fri => {
                // Continue to hour check
            }
            Weekday::Sat | Weekday::Sun => {
                return false; // Weekend - not trading hours
            }
        }
    }

    // Check hour range (9:00 AM to 3:00 PM)
    let current_hour = now_local.hour();
    current_hour >= config.start_hour && current_hour < config.end_hour
}

/// Get appropriate sync interval based on trading hours
///
/// During trading hours:
/// - Daily interval: 15 seconds (frequent updates for active trading)
/// - Slow interval: 5 minutes (hourly/minute data needs frequent refresh)
///
/// Outside trading hours:
/// - Daily interval: 5 minutes (relaxed, market is closed)
/// - Slow interval: 30 minutes (very relaxed, no active trading)
pub fn get_sync_interval(trading_interval: Duration, non_trading_interval: Duration) -> Duration {
    if is_trading_hours() {
        trading_interval
    } else {
        non_trading_interval
    }
}

/// Get appropriate cache control max-age based on trading hours
///
/// During trading hours: 30 seconds (fresh data for active trading)
/// Outside trading hours: 120 seconds (reduced server load when market is closed)
pub fn get_cache_max_age() -> u32 {
    if is_trading_hours() {
        30  // 30 seconds during trading hours
    } else {
        120 // 2 minutes during off-trading hours
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_trading_hours_config() {
        let config = TradingHours::default();
        assert_eq!(config.start_hour, 9);
        assert_eq!(config.end_hour, 15);
        assert_eq!(config.timezone, "Asia/Ho_Chi_Minh");
        assert!(config.weekdays_only);
    }
}
