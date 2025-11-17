use std::path::{Path, PathBuf};
use std::fs::{OpenOptions, rename, metadata};
use std::io::{Write, Error as IoError};
use chrono::{DateTime, NaiveDate, Utc};
use crate::error::Error;
use crate::services::vci::OhlcvData;
use crate::models::StockData;

/// Get market data directory from environment variable or use default
pub fn get_market_data_dir() -> PathBuf {
    std::env::var("MARKET_DATA_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("market_data"))
}

/// Get crypto data directory from environment variable or use default
pub fn get_crypto_data_dir() -> PathBuf {
    std::env::var("CRYPTO_DATA_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("crypto_data"))
}

/// Get public directory from environment variable or use default
pub fn get_public_dir() -> PathBuf {
    std::env::var("PUBLIC_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("public"))
}

/// Maximum log file size in bytes (100MB)
const MAX_LOG_SIZE_BYTES: u64 = 100 * 1024 * 1024;

/// Rotate log file if it exceeds the maximum size
///
/// If the log file exceeds MAX_LOG_SIZE_BYTES, it will be moved to .backup
/// If a backup file already exists, it will be overwritten
pub fn rotate_log_if_needed(log_path: &Path) -> Result<(), IoError> {
    // Check if current log file exists and get its size
    if log_path.exists() {
        let file_size = metadata(log_path)?.len();

        if file_size > MAX_LOG_SIZE_BYTES {
            let backup_path = log_path.with_extension("log.backup");

            // Move current file to backup (overwrites existing backup)
            rename(log_path, &backup_path)?;

            tracing::info!(
                old_size_bytes = file_size,
                backup_file = ?backup_path,
                "Rotated log file due to size limit"
            );
        }
    }

    Ok(())
}

/// Write content to log file with automatic rotation
///
/// This function checks the log file size before writing and rotates if necessary.
/// It ensures atomic operations and graceful error handling.
pub fn write_with_rotation(log_path: &Path, content: &str) -> Result<(), IoError> {
    // Rotate if needed before writing
    if let Err(e) = rotate_log_if_needed(log_path) {
        tracing::error!(
            error = %e,
            log_file = ?log_path,
            "Failed to rotate log file, continuing with current file"
        );
        // Continue with writing even if rotation fails
    }

    // Open file in append mode (creates if doesn't exist)
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path)?;

    file.write_all(content.as_bytes())?;

    Ok(())
}

/// Parse timestamp from string, supporting multiple formats:
/// - RFC3339: "2025-01-15T10:30:00Z"
/// - ISO 8601: "2025-01-15T10:30:00"
/// - Legacy space format: "2025-01-15 10:30:00"
/// - Date only: "2025-01-15"
///
/// This is the centralized datetime parsing function used across the codebase.
pub fn parse_timestamp(time_str: &str) -> Result<DateTime<Utc>, Error> {
    // Try RFC3339 first (with timezone)
    if let Ok(dt) = DateTime::parse_from_rfc3339(time_str) {
        return Ok(dt.with_timezone(&Utc));
    }

    // Try ISO 8601 datetime format "YYYY-MM-DDTHH:MM:SS"
    if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(time_str, "%Y-%m-%dT%H:%M:%S") {
        return Ok(dt.and_utc());
    }

    // Try legacy datetime format "YYYY-MM-DD HH:MM:SS" (for backward compatibility)
    if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(time_str, "%Y-%m-%d %H:%M:%S") {
        return Ok(dt.and_utc());
    }

    // Try date only format "YYYY-MM-DD"
    let date = NaiveDate::parse_from_str(time_str, "%Y-%m-%d")
        .map_err(|e| Error::Parse(format!("Invalid date format '{}': {}", time_str, e)))?;

    Ok(date
        .and_hms_opt(0, 0, 0)
        .ok_or_else(|| Error::Parse("Failed to set time".to_string()))?
        .and_utc())
}

/// Format timestamp for daily interval (date only): "YYYY-MM-DD"
pub fn format_date(time: &DateTime<Utc>) -> String {
    time.format("%Y-%m-%d").to_string()
}

/// Format timestamp for intraday intervals (ISO 8601): "YYYY-MM-DDTHH:MM:SS"
pub fn format_timestamp(time: &DateTime<Utc>) -> String {
    time.format("%Y-%m-%dT%H:%M:%S").to_string()
}

/// Deduplicate OHLCV data by timestamp, favoring the last occurrence
///
/// When duplicates exist (same timestamp), this function keeps the LAST duplicate
/// and removes earlier ones. This is the correct behavior because:
/// - Latest data is most accurate (corrections/adjustments)
/// - CSV enhancement may have been run multiple times
/// - API may have returned updated data for same timestamp
///
/// Algorithm:
/// 1. Iterate through sorted data
/// 2. Keep only records where timestamp differs from previous
/// 3. When duplicates found, the last one is kept
///
/// Time complexity: O(n)
/// Space complexity: O(1) in-place deduplication
///
/// # Arguments
/// * `data` - Mutable reference to vector of OHLCV data (must be sorted by time)
///
/// # Returns
/// Number of duplicates removed
///
/// # Example
/// ```
/// let mut data = vec![
///     OhlcvData { time: t1, ... },
///     OhlcvData { time: t1, ... },  // duplicate - will be removed
///     OhlcvData { time: t2, ... },
/// ];
/// let removed = deduplicate_ohlcv_by_time(&mut data);
/// assert_eq!(removed, 1);
/// assert_eq!(data.len(), 2);
/// ```
pub fn deduplicate_ohlcv_by_time(data: &mut Vec<OhlcvData>) -> usize {
    let original_len = data.len();

    if original_len <= 1 {
        return 0;  // No duplicates possible
    }

    // Deduplicate in-place: keep last occurrence of each timestamp
    // Since data is sorted, we scan forward and keep the LAST of consecutive duplicates
    let mut write_idx = 0;

    for read_idx in 0..data.len() {
        // Check if this is the last occurrence of this timestamp
        let is_last_occurrence = if read_idx + 1 < data.len() {
            // Not last element - check if next is different
            data[read_idx].time != data[read_idx + 1].time
        } else {
            // Last element - always keep
            true
        };

        if is_last_occurrence {
            if write_idx != read_idx {
                data[write_idx] = data[read_idx].clone();
            }
            write_idx += 1;
        }
    }

    // Truncate to remove duplicates
    data.truncate(write_idx);

    let removed = original_len - data.len();
    removed
}

/// Deduplicate StockData by timestamp, favoring the last occurrence
///
/// Similar to deduplicate_ohlcv_by_time() but works with StockData (enhanced data).
/// This is used by the API serving layer to ensure deduplicated responses.
///
/// # Arguments
/// * `data` - Mutable reference to vector of StockData (must be sorted by time)
///
/// # Returns
/// Number of duplicates removed
pub fn deduplicate_stock_data_by_time(data: &mut Vec<StockData>) -> usize {
    let original_len = data.len();

    if original_len <= 1 {
        return 0;  // No duplicates possible
    }

    // Deduplicate in-place: keep last occurrence of each timestamp
    let mut write_idx = 0;

    for read_idx in 0..data.len() {
        // Check if this is the last occurrence of this timestamp
        let is_last_occurrence = if read_idx + 1 < data.len() {
            // Not last element - check if next is different
            data[read_idx].time != data[read_idx + 1].time
        } else {
            // Last element - always keep
            true
        };

        if is_last_occurrence {
            if write_idx != read_idx {
                data[write_idx] = data[read_idx].clone();
            }
            write_idx += 1;
        }
    }

    // Truncate to remove duplicates
    data.truncate(write_idx);

    let removed = original_len - data.len();
    removed
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    #[test]
    fn test_log_rotation_needed() {
        let temp_dir = tempdir().unwrap();
        let log_path = temp_dir.path().join("test.log");

        // Create a small log file
        fs::write(&log_path, "small content").unwrap();

        // Should not rotate (file is small)
        rotate_log_if_needed(&log_path).unwrap();
        assert!(log_path.exists());
        assert!(!log_path.with_extension("log.backup").exists());
    }

    #[test]
    fn test_write_with_rotation() {
        let temp_dir = tempdir().unwrap();
        let log_path = temp_dir.path().join("test.log");

        // Write content
        write_with_rotation(&log_path, "test content\n").unwrap();

        assert!(log_path.exists());
        let content = fs::read_to_string(&log_path).unwrap();
        assert_eq!(content, "test content\n");
    }

    #[test]
    fn test_parse_timestamp_iso8601() {
        let result = parse_timestamp("2025-01-15T10:30:00").unwrap();
        assert_eq!(result.format("%Y-%m-%dT%H:%M:%S").to_string(), "2025-01-15T10:30:00");
    }

    #[test]
    fn test_parse_timestamp_legacy_space() {
        let result = parse_timestamp("2025-01-15 10:30:00").unwrap();
        assert_eq!(result.format("%Y-%m-%dT%H:%M:%S").to_string(), "2025-01-15T10:30:00");
    }

    #[test]
    fn test_parse_timestamp_date_only() {
        let result = parse_timestamp("2025-01-15").unwrap();
        assert_eq!(result.format("%Y-%m-%d").to_string(), "2025-01-15");
    }

    #[test]
    fn test_format_functions() {
        let dt = parse_timestamp("2025-01-15T10:30:00").unwrap();
        assert_eq!(format_date(&dt), "2025-01-15");
        assert_eq!(format_timestamp(&dt), "2025-01-15T10:30:00");
    }
}
