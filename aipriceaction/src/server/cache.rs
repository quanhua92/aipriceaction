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

#[cfg(test)]
mod tests {
    use super::TickersCache;
    use crate::server::types::StockDataResponse;
    use std::collections::BTreeMap;
    use std::time::Duration;

    #[test]
    fn cached_ticker_floats_survive_json_round_trip_exactly() {
        let expected = -0.12187979802776039_f64;
        let mut cache = TickersCache::new(1, Duration::from_secs(30));
        let response = StockDataResponse {
            time: "2026-10-20".into(),
            open: 1.0,
            high: 1.0,
            low: 1.0,
            close: 1.0,
            volume: 1,
            symbol: "DUPL".into(),
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
            close_changed: Some(expected),
            volume_changed: None,
            total_money_changed: None,
        };
        let values = BTreeMap::from([("DUPL".to_owned(), vec![response])]);

        cache.put("mode-all".into(), &values);

        let actual = cache
            .get("mode-all")
            .expect("the just-inserted entry should be present")["DUPL"][0]
            .close_changed
            .expect("close_changed should be present");
        assert_eq!(actual.to_bits(), expected.to_bits());
    }
}
