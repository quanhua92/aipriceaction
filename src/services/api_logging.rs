use chrono::{DateTime, Utc};
use crate::utils::{get_market_data_dir, write_with_rotation};

/// API request performance metrics
#[derive(Debug, Clone)]
pub struct ApiPerformanceMetrics {
    pub start_time: DateTime<Utc>,
    pub end_time: DateTime<Utc>,
    pub duration_ms: u64,
    pub status: ApiStatus,
    pub endpoint: String,
    pub ticker_count: usize,
    pub interval: String,
    pub cache_used: bool,
    pub response_format: String,
    pub response_size_bytes: usize,
    pub data_source: DataSource,
    pub error_message: Option<String>,
}

#[derive(Debug, Clone)]
pub enum ApiStatus {
    Success,
    Fail,
}

#[derive(Debug, Clone)]
pub enum DataSource {
    Cache,
    Disk,
    Api,
    Mixed,
}

impl ApiPerformanceMetrics {
    pub fn new(start_time: DateTime<Utc>) -> Self {
        Self {
            start_time,
            end_time: Utc::now(),
            duration_ms: 0,
            status: ApiStatus::Success,
            endpoint: String::new(),
            ticker_count: 0,
            interval: String::new(),
            cache_used: false,
            response_format: String::new(),
            response_size_bytes: 0,
            data_source: DataSource::Cache,
            error_message: None,
        }
    }

    pub fn complete(&mut self) {
        self.end_time = Utc::now();
        self.duration_ms = (self.end_time - self.start_time).num_milliseconds() as u64;
    }
}

/// Write compact API performance log entry to api_requests.log
pub fn write_api_log_entry(metrics: &ApiPerformanceMetrics) {
    let log_path = get_market_data_dir().join("api_requests.log");

    let status_str = match metrics.status {
        ApiStatus::Success => "OK",
        ApiStatus::Fail => "FAIL",
    };

    let data_source_str = match metrics.data_source {
        DataSource::Cache => "cache",
        DataSource::Disk => "disk",
        DataSource::Api => "api",
        DataSource::Mixed => "mixed",
    };

    let duration_str = if metrics.duration_ms >= 1000 {
        format!("{}.{:01}s", metrics.duration_ms / 1000, (metrics.duration_ms % 1000) / 100)
    } else {
        format!("{}ms", metrics.duration_ms)
    };

    let response_size_str = if metrics.response_size_bytes >= 1024 {
        format!("{}kb", metrics.response_size_bytes / 1024)
    } else {
        format!("{}b", metrics.response_size_bytes)
    };

    let error_info = if let Some(ref error) = metrics.error_message {
        format!(" error:{}", error)
    } else {
        String::new()
    };

    let log_line = format!(
        "{} | {} | {} | {} | {} | req:1 tickers:{} interval:{} cache:{} format:{} size:{} source:{}{}\n",
        metrics.start_time.format("%Y-%m-%d %H:%M:%S"),
        metrics.end_time.format("%Y-%m-%d %H:%M:%S"),
        duration_str,
        metrics.endpoint,
        status_str,
        metrics.ticker_count,
        metrics.interval,
        metrics.cache_used,
        metrics.response_format,
        response_size_str,
        data_source_str,
        error_info
    );

    // Use log rotation utility
    let _ = write_with_rotation(&log_path, &log_line);
}

/// Helper function to determine data source based on cache hit and file operations
pub fn determine_data_source(cache_hit: bool, disk_read: bool) -> DataSource {
    if cache_hit {
        DataSource::Cache
    } else if disk_read {
        DataSource::Disk
    } else {
        DataSource::Api
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{TimeZone, Utc};

    #[test]
    fn test_api_performance_metrics() {
        let start_time = Utc.with_ymd_and_hms(2024, 12, 1, 15, 30, 45).unwrap();
        let mut metrics = ApiPerformanceMetrics::new(start_time);

        metrics.endpoint = "/tickers".to_string();
        metrics.ticker_count = 5;
        metrics.interval = "1D".to_string();
        metrics.cache_used = true;
        metrics.response_format = "json".to_string();
        metrics.response_size_bytes = 1024;
        metrics.data_source = DataSource::Cache;

        metrics.complete();

        assert_eq!(metrics.status, ApiStatus::Success);
        assert!(metrics.duration_ms > 0);
    }

    #[test]
    fn test_determine_data_source() {
        assert!(matches!(determine_data_source(true, false), DataSource::Cache));
        assert!(matches!(determine_data_source(false, true), DataSource::Disk));
        assert!(matches!(determine_data_source(false, false), DataSource::Api));
    }
}