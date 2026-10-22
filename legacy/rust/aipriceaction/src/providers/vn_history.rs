use std::collections::BTreeMap;
use std::fmt;
use std::time::Duration;

use chrono::{Duration as ChronoDuration, Utc};
use tokio::time::timeout;

use super::ohlcv::OhlcvData;
use super::udf::{UdfError, UdfProvider};
use super::vci::{VciError, VciProvider};

const VPS_TIMEOUT: Duration = Duration::from_secs(12);
const VCI_TIMEOUT: Duration = Duration::from_secs(20);

pub struct VnHistoryProvider {
    vps: UdfProvider,
    vci: VciProvider,
    simulate_vps_failure: bool,
}

pub struct HistoryPage {
    pub bars: Vec<OhlcvData>,
    pub source: &'static str,
    pub fallback_reason: Option<String>,
}

#[derive(Debug)]
pub enum VnHistoryError {
    InvalidRequest(String),
    NoData,
    SourceFailed {
        source: &'static str,
        reason: String,
    },
    BothFailed {
        vps: String,
        vci: String,
    },
}

impl fmt::Display for VnHistoryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidRequest(reason) => write!(f, "invalid history request: {reason}"),
            Self::NoData => write!(f, "no historical bars from VPS or VCI"),
            Self::SourceFailed { source, reason } => write!(f, "{source} failed: {reason}"),
            Self::BothFailed { vps, vci } => {
                write!(f, "VPS failed ({vps}); VCI failed ({vci})")
            }
        }
    }
}

impl VnHistoryError {
    pub fn is_rate_limited(&self) -> bool {
        let reason = self.to_string();
        reason.contains("Rate limit exceeded") || reason.contains("429")
    }
}

impl VnHistoryProvider {
    /// `allow_direct=false` requires HTTP_PROXIES and routes both sources
    /// through the existing SOCKS5/HTTP proxy clients.
    pub fn new(requests_per_minute: u32, allow_direct: bool) -> Result<Self, String> {
        if requests_per_minute == 0 {
            return Err("rate limit must be greater than zero".to_string());
        }
        let vps = UdfProvider::vps_with_options(requests_per_minute, allow_direct)
            .map_err(|e| format!("VPS initialization failed: {e}"))?;
        let vci = VciProvider::with_options(requests_per_minute, allow_direct)
            .map_err(|e| format!("VCI initialization failed: {e}"))?;
        Ok(Self {
            vps,
            vci,
            simulate_vps_failure: false,
        })
    }

    /// Lets the CLI exercise the same VCI fallback branch without relying on
    /// a real VPS outage. Never used by production workers.
    pub fn simulate_vps_failure(mut self) -> Self {
        self.simulate_vps_failure = true;
        self
    }

    pub fn client_count(&self) -> usize {
        self.vps.client_count()
    }

    /// Fetch only from the named source. Full dividend rebuilds use this to
    /// keep every daily page on one corporate-action adjustment series.
    pub async fn get_history_from(
        &self,
        source: &'static str,
        symbol: &str,
        interval: &str,
        count_back: u32,
        before_timestamp: Option<i64>,
    ) -> Result<Vec<OhlcvData>, VnHistoryError> {
        if count_back == 0 {
            return Err(VnHistoryError::InvalidRequest(
                "count_back must be greater than zero".to_string(),
            ));
        }
        let response = match source {
            "vps" if self.simulate_vps_failure => {
                return Err(VnHistoryError::SourceFailed {
                    source,
                    reason: "simulated VPS failure".to_string(),
                });
            }
            "vps" => match timeout(
                VPS_TIMEOUT,
                self.vps
                    .get_history(symbol, interval, count_back, before_timestamp),
            )
            .await
            {
                Ok(Ok(bars)) => Ok(bars),
                Ok(Err(UdfError::NoData)) => return Err(VnHistoryError::NoData),
                Ok(Err(e)) => Err(e.to_string()),
                Err(_) => Err(format!("timed out after {}s", VPS_TIMEOUT.as_secs())),
            },
            "vci" => match timeout(
                VCI_TIMEOUT,
                self.vci
                    .get_history(symbol, interval, count_back, before_timestamp),
            )
            .await
            {
                Ok(Ok(bars)) => Ok(bars),
                Ok(Err(VciError::NoData)) => return Err(VnHistoryError::NoData),
                Ok(Err(e)) => Err(e.to_string()),
                Err(_) => Err(format!("timed out after {}s", VCI_TIMEOUT.as_secs())),
            },
            _ => {
                return Err(VnHistoryError::InvalidRequest(format!(
                    "unsupported history source: {source}"
                )));
            }
        };
        let bars = response.map_err(|reason| VnHistoryError::SourceFailed { source, reason })?;
        normalize_page(bars, count_back, before_timestamp, source).map_err(|reason| {
            if reason == "no data" {
                VnHistoryError::NoData
            } else {
                VnHistoryError::SourceFailed {
                    source,
                    reason: format!("invalid page: {reason}"),
                }
            }
        })
    }

    pub async fn get_history(
        &self,
        symbol: &str,
        interval: &str,
        count_back: u32,
        before_timestamp: Option<i64>,
    ) -> Result<HistoryPage, VnHistoryError> {
        let (vps_reason, vps_no_data) = match self
            .get_history_from("vps", symbol, interval, count_back, before_timestamp)
            .await
        {
            Ok(bars) => {
                return Ok(HistoryPage {
                    bars,
                    source: "vps",
                    fallback_reason: None,
                });
            }
            Err(VnHistoryError::InvalidRequest(reason)) => {
                return Err(VnHistoryError::InvalidRequest(reason));
            }
            Err(VnHistoryError::NoData) => ("no data".to_string(), true),
            Err(reason) => (reason.to_string(), false),
        };

        tracing::warn!(symbol, interval, reason = %vps_reason, "VPS history unavailable; trying VCI");
        match self
            .get_history_from("vci", symbol, interval, count_back, before_timestamp)
            .await
        {
            Ok(bars) => Ok(HistoryPage {
                bars,
                source: "vci",
                fallback_reason: Some(vps_reason),
            }),
            Err(VnHistoryError::NoData) if vps_no_data => Err(VnHistoryError::NoData),
            Err(vci_reason) => Err(VnHistoryError::BothFailed {
                vps: vps_reason,
                vci: vci_reason.to_string(),
            }),
        }
    }
}

fn normalize_page(
    bars: Vec<OhlcvData>,
    count_back: u32,
    before_timestamp: Option<i64>,
    source: &'static str,
) -> Result<Vec<OhlcvData>, String> {
    let mut by_time = BTreeMap::new();
    let old_bar_cutoff = Utc::now() - ChronoDuration::days(30);
    let mut inconsistent_old_bars = 0usize;
    let mut first_inconsistent_time = None;
    let mut omitted_old_bars = 0usize;
    for bar in bars {
        if before_timestamp.is_some_and(|before| bar.time.timestamp() >= before) {
            continue;
        }
        let positive_prices = [bar.open, bar.high, bar.low, bar.close]
            .iter()
            .all(|price| price.is_finite() && *price > 0.0);
        let consistent_range = bar.high >= bar.low
            && bar.high >= bar.open.max(bar.close)
            && bar.low <= bar.open.min(bar.close);
        if !positive_prices || !consistent_range {
            if bar.time >= old_bar_cutoff {
                return Err(format!("invalid OHLC at {}", bar.time));
            }
            if !positive_prices {
                // A zero/non-finite price is unusable by the rest of the
                // pipeline. Leave a dated gap rather than invent a price.
                tracing::warn!(source, symbol = ?bar.symbol, time = %bar.time,
                    "omitting old VN history candle with non-positive or non-finite price");
                omitted_old_bars += 1;
                continue;
            }
            // Brokers sometimes publish old adjusted bars whose reported
            // open/close fall outside the reported high/low. Keep the exact
            // source values, as the previous VCI ingestion did.
            inconsistent_old_bars += 1;
            first_inconsistent_time.get_or_insert(bar.time);
        }
        by_time.insert(bar.time, bar);
    }
    if inconsistent_old_bars > 0 {
        tracing::warn!(source, count = inconsistent_old_bars,
            first_time = ?first_inconsistent_time,
            "retained provider-reported old VN history candles with inconsistent OHLC");
    }
    if by_time.is_empty() {
        return Err(if omitted_old_bars > 0 {
            "all returned bars have unusable prices".to_string()
        } else {
            "no data".to_string()
        });
    }
    let limit = count_back as usize;
    Ok(by_time
        .into_values()
        .rev()
        .take(limit)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect())
}
