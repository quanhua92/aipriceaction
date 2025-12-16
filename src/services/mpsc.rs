//! MPSC (Multiple Producer Single Consumer) Channel Service
//!
//! Provides bounded channels for real-time ticker updates between workers and DataStore.
//! Uses capacity=1 to prevent OOM while ensuring real-time updates.
//! Uses std::sync::mpsc to work across multiple tokio runtimes.

use crate::models::{Interval, StockData};
use chrono::{DateTime, Utc};
use std::sync::mpsc::{SyncSender, Receiver};

/// Change types for efficient ticker updates
#[derive(Debug, Clone)]
pub enum ChangeType {
    /// No changes detected
    NoChange,
    /// Only new records were added
    NewRecords { records: Vec<StockData> },
    /// File was truncated from a specific record and new records added
    Truncated {
        /// Record index where truncation occurred
        from_record: usize,
        /// New records that were added
        new_records: Vec<StockData>
    },
    /// Entire file was replaced
    FullFile { records: Vec<StockData> },
}

impl ChangeType {
    /// Get the number of records in this change
    pub fn record_count(&self) -> usize {
        match self {
            ChangeType::NoChange => 0,
            ChangeType::NewRecords { records } => records.len(),
            ChangeType::Truncated { new_records, .. } => new_records.len(),
            ChangeType::FullFile { records } => records.len(),
        }
    }

    /// Check if this change represents significant changes
    pub fn has_changes(&self) -> bool {
        !matches!(self, ChangeType::NoChange)
    }
}

/// MPSC message for ticker updates sent from workers to DataStore
#[derive(Debug, Clone)]
pub struct TickerUpdate {
    /// Ticker symbol (e.g., "VCB", "BTC")
    pub ticker: String,
    /// Data interval (Daily, Hourly, Minute)
    pub interval: Interval,
    /// Type of change that occurred
    pub change_type: ChangeType,
    /// When this update was generated
    pub timestamp: DateTime<Utc>,
}

impl TickerUpdate {
    /// Create a new ticker update
    pub fn new(
        ticker: String,
        interval: Interval,
        change_type: ChangeType,
    ) -> Self {
        Self {
            ticker,
            interval,
            change_type,
            timestamp: Utc::now(),
        }
    }

    /// Get the number of records in this update
    pub fn record_count(&self) -> usize {
        self.change_type.record_count()
    }

    /// Check if this update represents significant changes
    pub fn has_changes(&self) -> bool {
        self.change_type.has_changes()
    }
}

/// Channel manager for creating bounded MPSC channels
pub struct ChannelManager {
    /// Sender for VN stock updates
    pub vn_sender: SyncSender<TickerUpdate>,
    /// Sender for crypto updates
    pub crypto_sender: SyncSender<TickerUpdate>,
}

impl ChannelManager {
    /// Create new channel manager with bounded channels (capacity=1)
    pub fn new() -> (Self, Receiver<TickerUpdate>, Receiver<TickerUpdate>) {
        // Create bounded channels with capacity 1 to prevent OOM
        let (vn_tx, vn_rx) = std::sync::mpsc::sync_channel(1);
        let (crypto_tx, crypto_rx) = std::sync::mpsc::sync_channel(1);

        let manager = Self {
            vn_sender: vn_tx,
            crypto_sender: crypto_tx,
        };

        (manager, vn_rx, crypto_rx)
    }

    /// Send a VN ticker update (non-blocking)
    pub fn send_vn_update(&self, update: TickerUpdate) -> Result<(), std::sync::mpsc::TrySendError<TickerUpdate>> {
        self.vn_sender.try_send(update)
    }

    /// Send a crypto ticker update (non-blocking)
    pub fn send_crypto_update(&self, update: TickerUpdate) -> Result<(), std::sync::mpsc::TrySendError<TickerUpdate>> {
        self.crypto_sender.try_send(update)
    }

    /// Clone sender for VN updates
    pub fn clone_vn_sender(&self) -> SyncSender<TickerUpdate> {
        self.vn_sender.clone()
    }

    /// Clone sender for crypto updates
    pub fn clone_crypto_sender(&self) -> SyncSender<TickerUpdate> {
        self.crypto_sender.clone()
    }
}

/// Convenience function for creating bounded channels directly
pub fn create_bounded_channels() -> (SyncSender<TickerUpdate>, Receiver<TickerUpdate>) {
    std::sync::mpsc::sync_channel(1) // Capacity 1 to prevent OOM
}

/// Send update with retry mechanism - waits and retries instead of skipping
pub fn send_with_retry(
    sender: &SyncSender<TickerUpdate>,
    update: TickerUpdate,
    max_retries: usize,
) -> Result<(), String> {
    let mut retries = 0;

    while retries < max_retries {
        match sender.try_send(update.clone()) {
            Ok(()) => return Ok(()),
            Err(std::sync::mpsc::TrySendError::Full(_)) => {
                // Channel is full, wait and retry
                retries += 1;
                if retries < max_retries {
                    std::thread::sleep(std::time::Duration::from_millis(10));
                }
            }
            Err(std::sync::mpsc::TrySendError::Disconnected(_)) => {
                return Err("Channel disconnected".to_string());
            }
        }
    }

    Err(format!("Failed to send after {} retries", max_retries))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread;
    use std::time::Duration;

    #[test]
    fn test_ticker_update_creation() {
        let update = TickerUpdate::new(
            "VCB".to_string(),
            Interval::Daily,
            ChangeType::NewRecords { records: vec![] },
        );

        assert_eq!(update.ticker, "VCB");
        assert_eq!(update.interval, Interval::Daily);
        assert_eq!(update.record_count(), 0);
        assert!(update.has_changes());
    }

    #[test]
    fn test_no_change_update() {
        let update = TickerUpdate::new(
            "BTC".to_string(),
            Interval::Hourly,
            ChangeType::NoChange,
        );

        assert_eq!(update.record_count(), 0);
        assert!(!update.has_changes());
    }

    #[test]
    fn test_channel_manager() {
        let (manager, mut _vn_rx, _crypto_rx) = ChannelManager::new();

        let update = TickerUpdate::new(
            "TEST".to_string(),
            Interval::Minute,
            ChangeType::NoChange,
        );

        // Should succeed since channel is empty
        assert!(manager.send_vn_update(update).is_ok());
    }

    #[test]
    fn test_bounded_channels() {
        let (tx, rx) = create_bounded_channels();

        let update = TickerUpdate::new(
            "TEST".to_string(),
            Interval::Daily,
            ChangeType::NoChange,
        );

        // First send should succeed
        assert!(tx.try_send(update.clone()).is_ok());

        // Second send should fail because channel is full (capacity=1)
        assert!(tx.try_send(update).is_err());

        // Receive the message
        assert!(rx.recv().is_ok());

        // Now send should succeed again
        assert!(tx.try_send(update).is_ok());
    }
}