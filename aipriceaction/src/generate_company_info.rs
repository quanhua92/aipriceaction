use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::fs;

use crate::constants::vci_worker::INDEX_TICKERS;
use crate::db;
use crate::providers::vci::{CompanyInfo, VciProvider};

/// Serializable container for a single ticker's company info + financial ratios.
#[derive(Debug, Serialize, Deserialize)]
struct TickerData {
    ticker: String,
    name: Option<String>,
    company_info: Option<CompanyInfo>,
    financial_ratios: Vec<HashMap<String, Value>>,
}

pub async fn run(ticker_filter: Option<String>, rate_limit: u32, save: bool) {
    // 1. Connect to PostgreSQL
    let database_url = std::env::var("DATABASE_URL").unwrap_or_else(|_| String::new());
    if database_url.is_empty() {
        tracing::error!("DATABASE_URL not set");
        return;
    }

    let pool = match db::connect(&database_url).await {
        Ok(pool) => {
            tracing::info!("Connected to PostgreSQL");
            pool
        }
        Err(e) => {
            tracing::error!("Failed to connect to database: {e}");
            return;
        }
    };

    // 2. Fetch all VN tickers
    let tickers = match crate::queries::ohlcv::list_tickers(&pool, "vn").await {
        Ok(t) => t,
        Err(e) => {
            tracing::error!("Failed to fetch tickers: {e}");
            return;
        }
    };

    // 3. Filter out index tickers
    let mut tickers: Vec<_> = tickers
        .into_iter()
        .filter(|t| !INDEX_TICKERS.iter().any(|idx| idx.eq_ignore_ascii_case(&t.ticker)))
        .collect();

    // 4. If --ticker provided, filter to that single ticker
    if let Some(ref filter) = ticker_filter {
        tickers.retain(|t| t.ticker.eq_ignore_ascii_case(filter));
        if tickers.is_empty() {
            tracing::error!("Ticker '{}' not found in database", filter);
            return;
        }
    }

    let total = tickers.len();
    tracing::info!("Will fetch company info for {total} ticker(s)");

    // 5. Create VCI provider
    let provider = match VciProvider::new(rate_limit) {
        Ok(p) => p,
        Err(e) => {
            tracing::error!("Failed to create VCI provider: {e}");
            return;
        }
    };
    tracing::info!("VCI provider ready ({} client(s), rate_limit={}/min)", provider.client_count(), rate_limit);

    let mut success_count = 0usize;
    let mut error_count = 0usize;

    // When --save, read existing file first so we merge into it
    let output_path = "company_info.json";
    let mut existing_data: Vec<TickerData> = if save && std::path::Path::new(output_path).exists() {
        match fs::read_to_string(output_path) {
            Ok(content) => match serde_json::from_str::<Vec<TickerData>>(&content) {
                Ok(data) => {
                    tracing::info!("Loaded {} existing entries from {output_path}", data.len());
                    data
                }
                Err(e) => {
                    tracing::warn!("Failed to parse existing {output_path}, starting fresh: {e}");
                    Vec::new()
                }
            },
            Err(e) => {
                tracing::warn!("Failed to read existing {output_path}, starting fresh: {e}");
                Vec::new()
            }
        }
    } else {
        Vec::new()
    };

    for (i, ticker) in tickers.iter().enumerate() {
        tracing::info!("[{}/{total}] Fetching {}{}...", i + 1, ticker.ticker, ticker.name.as_ref().map(|n| format!(" ({n})")).unwrap_or_default());

        let mut ticker_data = TickerData {
            ticker: ticker.ticker.clone(),
            name: ticker.name.clone(),
            company_info: None,
            financial_ratios: Vec::new(),
        };

        // Company info
        match provider.company_info(&ticker.ticker).await {
            Ok(info) => {
                tracing::info!(
                    "  exchange: {} | industry: {} | type: {}",
                    info.exchange.as_deref().unwrap_or("-"),
                    info.industry.as_deref().unwrap_or("-"),
                    info.company_type.as_deref().unwrap_or("-"),
                );
                if let Some(year) = info.established_year {
                    tracing::info!("  established: {year}");
                }
                if let Some(employees) = info.employees {
                    tracing::info!("  employees: {employees}");
                }
                if let Some(mcap) = info.market_cap {
                    tracing::info!("  market_cap: {mcap}");
                }
                if let Some(price) = info.current_price {
                    tracing::info!("  price: {price}");
                }
                if let Some(shares) = info.outstanding_shares {
                    tracing::info!("  outstanding_shares: {shares}");
                }
                if let Some(ref profile) = info.company_profile {
                    let truncated = if profile.len() > 200 { &profile[..200] } else { profile };
                    tracing::info!("  profile: {truncated}{}", if profile.len() > 200 { "..." } else { "" });
                }
                if !info.shareholders.is_empty() {
                    tracing::info!("  shareholders ({}):", info.shareholders.len());
                    for sh in info.shareholders.iter().take(5) {
                        tracing::info!("    - {} ({:.2}%)", sh.name, sh.percentage);
                    }
                    if info.shareholders.len() > 5 {
                        tracing::info!("    ... ({} more)", info.shareholders.len() - 5);
                    }
                }
                if !info.officers.is_empty() {
                    tracing::info!("  officers ({}):", info.officers.len());
                    for of in info.officers.iter().take(5) {
                        let pct = of.percentage.map(|p| format!(" ({p:.2}%)")).unwrap_or_default();
                        tracing::info!("    - {} — {}{}", of.name, of.position, pct);
                    }
                    if info.officers.len() > 5 {
                        tracing::info!("    ... ({} more)", info.officers.len() - 5);
                    }
                }

                ticker_data.company_info = Some(info);
            }
            Err(e) => {
                tracing::warn!("  company_info error: {e}");
                error_count += 1;
            }
        }

        // Financial ratios (quarter)
        match provider.financial_ratios(&ticker.ticker, "quarter").await {
            Ok(ratios) if !ratios.is_empty() => {
                let latest = &ratios[0];
                tracing::info!("  financial_ratios (latest quarter):");
                if let Some(year) = latest.get("yearReport") {
                    tracing::info!("    yearReport: {year}");
                }
                if let Some(length) = latest.get("lengthReport") {
                    tracing::info!("    lengthReport: {length}");
                }
                for key in &[
                    "revenue", "revenueGrowth", "netProfit", "netProfitGrowth",
                    "roe", "roa", "roic", "pe", "pb", "eps", "epsTTM",
                    "netProfitMargin", "grossMargin", "ebitMargin",
                    "currentRatio", "de", "le",
                    "ev", "bvps", "ps", "pcf",
                    "dividend", "charterCapital",
                ] {
                    if let Some(val) = latest.get(*key) {
                        tracing::info!("    {key}: {val}");
                    }
                }

                ticker_data.financial_ratios = ratios;
            }
            Ok(_) => {
                tracing::info!("  financial_ratios: no quarter data available");
            }
            Err(e) => {
                tracing::warn!("  financial_ratios error: {e}");
            }
        }

        success_count += 1;

        // Merge into existing data (upsert by ticker symbol)
        if save {
            if let Some(existing) = existing_data.iter_mut().find(|e| e.ticker.eq_ignore_ascii_case(&ticker.ticker)) {
                existing.name = ticker_data.name;
                existing.company_info = ticker_data.company_info;
                existing.financial_ratios = ticker_data.financial_ratios;
            } else {
                existing_data.push(ticker_data);
            }
        }

        tracing::info!("{}", "─".repeat(60));

        // Small delay between tickers to be gentle on the API
        if i + 1 < total {
            tokio::time::sleep(std::time::Duration::from_secs(1)).await;
        }
    }

    // 6. Save to JSON if requested
    if save {
        match serde_json::to_string_pretty(&existing_data) {
            Ok(json) => match fs::write(output_path, &json) {
                Ok(_) => tracing::info!("Saved {} ticker(s) to {output_path}", existing_data.len()),
                Err(e) => tracing::error!("Failed to write {output_path}: {e}"),
            },
            Err(e) => tracing::error!("Failed to serialize JSON: {e}"),
        }
    }

    // 7. Summary
    tracing::info!("{}", "═".repeat(60));
    tracing::info!("Done — total: {total}, success: {success_count}, errors: {error_count}");
}
