//! Crypto sync info tracker - persists last sync times to disk
//!
//! This prevents the crypto worker from hammering the API after restarts
//! by tracking when each interval was last synced.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use tracing::{debug, warn};

/// Sync info for tracking last sync times per interval type
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CryptoSyncInfo {
    /// Last time priority cryptos daily was synced
    pub priority_daily_last_sync: Option<DateTime<Utc>>,
    /// Last time priority cryptos hourly was synced
    pub priority_hourly_last_sync: Option<DateTime<Utc>>,
    /// Last time priority cryptos minute was synced
    pub priority_minute_last_sync: Option<DateTime<Utc>>,

    /// Last time regular cryptos daily was synced
    pub regular_daily_last_sync: Option<DateTime<Utc>>,
    /// Last time regular cryptos hourly was synced
    pub regular_hourly_last_sync: Option<DateTime<Utc>>,
    /// Last time regular cryptos minute was synced
    pub regular_minute_last_sync: Option<DateTime<Utc>>,

    /// Total iteration count
    pub iteration_count: u64,
}

impl Default for CryptoSyncInfo {
    fn default() -> Self {
        Self {
            priority_daily_last_sync: None,
            priority_hourly_last_sync: None,
            priority_minute_last_sync: None,
            regular_daily_last_sync: None,
            regular_hourly_last_sync: None,
            regular_minute_last_sync: None,
            iteration_count: 0,
        }
    }
}

impl CryptoSyncInfo {
    /// Load sync info from file, or create new if doesn't exist
    pub fn load(info_path: &Path) -> Self {
        match fs::read_to_string(info_path) {
            Ok(content) => {
                match serde_json::from_str::<CryptoSyncInfo>(&content) {
                    Ok(info) => {
                        debug!("Loaded crypto sync info from {}", info_path.display());
                        info
                    }
                    Err(e) => {
                        warn!(
                            "Failed to parse crypto sync info from {}: {}. Using defaults.",
                            info_path.display(),
                            e
                        );
                        Self::default()
                    }
                }
            }
            Err(_) => {
                debug!("No existing crypto sync info at {}, using defaults", info_path.display());
                Self::default()
            }
        }
    }

    /// Save sync info to file
    pub fn save(&self, info_path: &Path) -> Result<(), std::io::Error> {
        let content = serde_json::to_string_pretty(self)?;
        fs::write(info_path, content)?;
        debug!("Saved crypto sync info to {}", info_path.display());
        Ok(())
    }

    /// Check if enough time has elapsed since last sync
    pub fn should_sync(&self, last_sync: Option<DateTime<Utc>>, interval_secs: u64) -> bool {
        match last_sync {
            None => {
                debug!("No previous sync found, should sync");
                true
            }
            Some(last) => {
                let elapsed = Utc::now().signed_duration_since(last);
                let elapsed_secs = elapsed.num_seconds();
                let should_sync = elapsed_secs >= interval_secs as i64;

                if should_sync {
                    debug!(
                        "Elapsed {}s >= {}s, should sync",
                        elapsed_secs, interval_secs
                    );
                } else {
                    debug!(
                        "Elapsed {}s < {}s, skipping sync (wait {}s more)",
                        elapsed_secs,
                        interval_secs,
                        interval_secs as i64 - elapsed_secs
                    );
                }

                should_sync
            }
        }
    }

    /// Update last sync time for priority daily
    pub fn update_priority_daily(&mut self) {
        self.priority_daily_last_sync = Some(Utc::now());
    }

    /// Update last sync time for priority hourly
    pub fn update_priority_hourly(&mut self) {
        self.priority_hourly_last_sync = Some(Utc::now());
    }

    /// Update last sync time for priority minute
    pub fn update_priority_minute(&mut self) {
        self.priority_minute_last_sync = Some(Utc::now());
    }

    /// Update last sync time for regular daily
    pub fn update_regular_daily(&mut self) {
        self.regular_daily_last_sync = Some(Utc::now());
    }

    /// Update last sync time for regular hourly
    pub fn update_regular_hourly(&mut self) {
        self.regular_hourly_last_sync = Some(Utc::now());
    }

    /// Update last sync time for regular minute
    pub fn update_regular_minute(&mut self) {
        self.regular_minute_last_sync = Some(Utc::now());
    }

    /// Increment iteration count
    pub fn increment_iteration(&mut self) {
        self.iteration_count += 1;
    }
}

/// Get the path to the crypto sync info file
pub fn get_crypto_sync_info_path(crypto_data_dir: &Path) -> PathBuf {
    crypto_data_dir.join("info.log")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread::sleep;
    use std::time::Duration as StdDuration;

    #[test]
    fn test_should_sync_no_previous() {
        let info = CryptoSyncInfo::default();
        assert!(info.should_sync(None, 900));
    }

    #[test]
    fn test_should_sync_elapsed() {
        let info = CryptoSyncInfo::default();
        let past = Utc::now() - chrono::Duration::seconds(1000);
        assert!(info.should_sync(Some(past), 900));
    }

    #[test]
    fn test_should_not_sync_too_soon() {
        let info = CryptoSyncInfo::default();
        let recent = Utc::now() - chrono::Duration::seconds(100);
        assert!(!info.should_sync(Some(recent), 900));
    }

    #[test]
    fn test_save_and_load() {
        use tempfile::tempdir;

        let dir = tempdir().unwrap();
        let info_path = dir.path().join("info.log");

        let mut info = CryptoSyncInfo::default();
        info.update_priority_daily();
        info.update_regular_hourly();
        info.iteration_count = 42;

        // Save
        info.save(&info_path).unwrap();

        // Load
        let loaded = CryptoSyncInfo::load(&info_path);
        assert_eq!(loaded.iteration_count, 42);
        assert!(loaded.priority_daily_last_sync.is_some());
        assert!(loaded.regular_hourly_last_sync.is_some());
        assert!(loaded.priority_hourly_last_sync.is_none());
    }

    #[test]
    fn test_load_nonexistent() {
        let info = CryptoSyncInfo::load(Path::new("/nonexistent/path/info.log"));
        assert_eq!(info.iteration_count, 0);
        assert!(info.priority_daily_last_sync.is_none());
    }
}
