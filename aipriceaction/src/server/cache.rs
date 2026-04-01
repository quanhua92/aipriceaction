use std::collections::BTreeMap;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::server::types::StockDataResponse;

struct CacheEntry {
    data: Vec<u8>,
    inserted_at: Instant,
}

pub struct TickersCache {
    store: HashMap<String, CacheEntry>,
    max_entries: usize,
    ttl: Duration,
}

impl TickersCache {
    pub fn new(max_entries: usize, ttl: Duration) -> Self {
        Self {
            store: HashMap::with_capacity(max_entries),
            max_entries,
            ttl,
        }
    }

    /// Get cached data. Returns None on miss or expired (lazy eviction).
    pub fn get(&mut self, key: &str) -> Option<BTreeMap<String, Vec<StockDataResponse>>> {
        let entry = self.store.get(key)?;
        if entry.inserted_at.elapsed() > self.ttl {
            self.store.remove(key);
            return None;
        }
        serde_json::from_slice(&entry.data).ok()
    }

    /// Insert into cache, evicting oldest entries if at capacity.
    pub fn put(&mut self, key: String, data: &BTreeMap<String, Vec<StockDataResponse>>) {
        // Evict expired entries first
        self.sweep();

        // If still at capacity, evict oldest entries until there's room
        if self.store.len() >= self.max_entries {
            let mut oldest_key: Option<String> = None;
            let mut oldest_time = Instant::now();
            for (k, v) in &self.store {
                if v.inserted_at < oldest_time {
                    oldest_time = v.inserted_at;
                    oldest_key = Some(k.clone());
                }
            }
            if let Some(k) = oldest_key {
                self.store.remove(&k);
            }
        }

        if let Ok(serialized) = serde_json::to_vec(data) {
            self.store.insert(
                key,
                CacheEntry {
                    data: serialized,
                    inserted_at: Instant::now(),
                },
            );
        }
    }

    /// Remove all expired entries.
    fn sweep(&mut self) {
        self.store
            .retain(|_, entry| entry.inserted_at.elapsed() <= self.ttl);
    }

    /// Spawn a background task that sweeps expired entries periodically.
    pub fn spawn_sweep_task(cache: Arc<tokio::sync::RwLock<Self>>, interval: Duration) {
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(interval).await;
                match cache.try_write() {
                    Ok(mut guard) => {
                        let before = guard.store.len();
                        guard.sweep();
                        let after = guard.store.len();
                        if before != after {
                            tracing::debug!(
                                evicted = before - after,
                                remaining = after,
                                "cache sweep"
                            );
                        }
                    }
                    Err(_) => {
                        tracing::trace!("cache sweep skipped: lock busy");
                    }
                }
            }
        });
    }
}
