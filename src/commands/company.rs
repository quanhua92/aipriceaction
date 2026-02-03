use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::models::TickerGroups;
use crate::services::vci::{VciClient, CompanyInfo};

const COMPANY_DATA_DIR: &str = "company_data";
const CACHE_DAYS: i64 = 7;

#[derive(Debug, Clone, Serialize, Deserialize)]
struct CachedCompanyInfo {
    #[serde(flatten)]
    data: CompanyInfo,
    created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct CachedFinancialInfo {
    symbol: String,
    period: String,
    #[serde(flatten)]
    data: HashMap<String, Value>,
    created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct CuratedData {
    symbol: String,
    processed_by: String,
    data_source: String,
    created_at: DateTime<Utc>,

    // Basic Company Info
    company_name: Option<String>,
    exchange: Option<String>,
    industry: Option<String>,
    company_profile: Option<String>,
    established_year: Option<u32>,
    employees: Option<u32>,
    website: Option<String>,

    // Market Data
    current_price: Option<f64>,
    market_cap: Option<f64>,
    outstanding_shares: Option<u64>,

    // Key Financial Metrics
    pe_ratio: Option<f64>,
    pb_ratio: Option<f64>,
    roe: Option<f64>,
    roa: Option<f64>,
    revenue: Option<f64>,
    net_profit: Option<f64>,
    dividend: Option<f64>,
    eps: Option<f64>,
}

pub fn run(tickers: Option<Vec<String>>, force: bool, cache_days: Option<i64>) {
    println!("🚀 AIPriceAction Company Information Collector: START");
    println!("⏰ Started at: {}", Utc::now().format("%Y-%m-%d %H:%M:%S"));

    let cache_days = cache_days.unwrap_or(CACHE_DAYS);
    println!("📅 Cache expires after: {} days", cache_days);
    println!("🗂️  Force refresh: {}", if force { "YES" } else { "NO" });

    // Setup directories
    setup_directories();

    // Load tickers
    let tickers = if let Some(ticker_list) = tickers {
        println!("🎯 Processing specified tickers: {:?}", ticker_list);
        ticker_list
    } else {
        println!("📋 Loading tickers from ticker_group.json...");
        load_all_tickers()
    };

    println!("📊 Found {} total tickers", tickers.len());

    // Initialize VCI client
    println!("\n🔗 Initializing VCI API client...");
    let rt = tokio::runtime::Runtime::new().expect("Failed to create Tokio runtime");

    let result = rt.block_on(async {
        let mut vci_client = VciClient::new_async(true, 60, None)
            .await
            .expect("Failed to create VCI client");
        println!("   ✅ VCI client: 60 calls/minute");

        let mut successful = 0;
        let mut failed = 0;
        let mut skipped = 0;

        println!("\n🔄 Starting processing of {} tickers...", tickers.len());

        for (i, ticker) in tickers.iter().enumerate() {
            println!("\n{} [{:3}/{:3}] {} {}", "=".repeat(20), i + 1, tickers.len(), ticker, "=".repeat(20));
            println!("⏰ Time: {}", Utc::now().format("%H:%M:%S"));

            match process_ticker(ticker, &mut vci_client, force, cache_days).await {
                Ok(ProcessResult::Success) => {
                    successful += 1;
                }
                Ok(ProcessResult::Skipped) => {
                    skipped += 1;
                }
                Err(e) => {
                    println!("   ❌ Error processing {}: {}", ticker, e);
                    failed += 1;
                }
            }

            // Progress update every 10 tickers
            if (i + 1) % 10 == 0 || i + 1 == tickers.len() {
                let progress = ((i + 1) as f64 / tickers.len() as f64) * 100.0;
                println!("\n📈 Progress: {:.1}% ({}/{})", progress, i + 1, tickers.len());
                println!("📊 Status: ✅{} ❌{} 💾{}", successful, failed, skipped);
            }

            // Rate limiting delay between tickers
            if i + 1 < tickers.len() {
                println!("⏸️  Rate limiting delay (2s)...");
                tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;
            }
        }

        (successful, failed, skipped)
    });

    let (successful, failed, skipped) = result;

    // Final summary
    println!("\n{}", "=".repeat(70));
    println!("🎉 PROCESSING COMPLETE!");
    println!("{}", "=".repeat(70));
    println!("⏰ Finished at: {}", Utc::now().format("%Y-%m-%d %H:%M:%S"));
    println!("📊 Results: ✅{} successful, ❌{} failed, 💾{} cached", successful, failed, skipped);

    // Show file count
    if let Ok(entries) = fs::read_dir(COMPANY_DATA_DIR) {
        let json_files: Vec<_> = entries
            .filter_map(|e| e.ok())
            .filter(|e| e.path().extension().and_then(|s| s.to_str()) == Some("json"))
            .collect();
        println!("\n📁 Files created:");
        println!("   Total JSON files: {}", json_files.len());
        println!("   Data directory: {}/", COMPANY_DATA_DIR);
    }

    println!("\n🏁 AIPriceAction Company Information Collector: FINISHED");
}

enum ProcessResult {
    Success,
    Skipped,
}

async fn process_ticker(
    ticker: &str,
    vci_client: &mut VciClient,
    force: bool,
    cache_days: i64,
) -> Result<ProcessResult, String> {
    let file_paths = get_file_paths(ticker);

    // Check cache validity
    if !force {
        let all_valid = file_paths.iter().all(|(_, path)| is_cache_valid(path, cache_days));
        if all_valid {
            println!("💾 Cache hit - all files valid for {}", ticker);
            return Ok(ProcessResult::Skipped);
        }
    }

    println!("   - Fetching company info from VCI...");

    // Fetch company info
    let company_info = match vci_client.company_info(ticker).await {
        Ok(info) => {
            println!("   - ✅ VCI company info success");
            Some(info)
        }
        Err(e) => {
            println!("   - ❌ VCI company info failed: {:?}", e);
            None
        }
    };

    // Small delay between requests
    tokio::time::sleep(tokio::time::Duration::from_millis(500)).await;

    // Fetch financial ratios
    println!("   - Fetching financial ratios from VCI...");
    let financial_ratios = match vci_client.financial_ratios(ticker, "quarter").await {
        Ok(ratios) => {
            println!("   - ✅ VCI financial ratios success ({} periods)", ratios.len());
            Some(ratios)
        }
        Err(e) => {
            println!("   - ⚠️  VCI financial ratios failed: {:?}", e);
            None
        }
    };

    // Save results if we got company data
    if let Some(company_data) = company_info {
        // Save company info
        let cached_company = CachedCompanyInfo {
            data: company_data.clone(),
            created_at: Utc::now(),
        };
        save_json(&cached_company, &file_paths["company_info"])?;

        // Save financial ratios if available
        if let Some(ref ratios) = financial_ratios {
            let cached_financial = CachedFinancialInfo {
                symbol: ticker.to_string(),
                period: "quarter".to_string(),
                data: {
                    let mut map = HashMap::new();
                    map.insert("ratios".to_string(), serde_json::to_value(ratios).unwrap_or(Value::Null));
                    map
                },
                created_at: Utc::now(),
            };
            save_json(&cached_financial, &file_paths["financial_info"])?;
        }

        // Create curated data
        let curated = extract_curated_data(&company_data, &financial_ratios);
        save_json(&curated, &file_paths["curated"])?;

        println!("   - ✅ Completed {} (processed by: VCI)", ticker);
        Ok(ProcessResult::Success)
    } else {
        Err(format!("No data available for {}", ticker))
    }
}

fn extract_curated_data(
    company_data: &CompanyInfo,
    financial_ratios: &Option<Vec<HashMap<String, Value>>>,
) -> CuratedData {
    // Extract company name from profile or use symbol
    let company_name = extract_company_name(&company_data.company_profile)
        .or_else(|| Some(company_data.symbol.clone()));

    // Extract latest financial metrics from the most recent quarter
    let (pe_ratio, pb_ratio, roe, roa, revenue, net_profit, dividend, eps) = if let Some(ratios) = financial_ratios {
        if let Some(latest) = ratios.first() {
            (
                latest.get("pe").and_then(|v| v.as_f64()),
                latest.get("pb").and_then(|v| v.as_f64()),
                latest.get("roe").and_then(|v| v.as_f64()),
                latest.get("roa").and_then(|v| v.as_f64()),
                latest.get("revenue").and_then(|v| v.as_f64()),
                latest.get("netProfit").and_then(|v| v.as_f64()),
                latest.get("dividend").and_then(|v| v.as_f64()),
                latest.get("eps").and_then(|v| v.as_f64()),
            )
        } else {
            (None, None, None, None, None, None, None, None)
        }
    } else {
        (None, None, None, None, None, None, None, None)
    };

    CuratedData {
        symbol: company_data.symbol.clone(),
        processed_by: "VCI".to_string(),
        data_source: "VCI".to_string(),
        created_at: Utc::now(),

        // Basic Company Info
        company_name,
        exchange: company_data.exchange.clone(),
        industry: company_data.industry.clone(),
        company_profile: company_data.company_profile.clone(),
        established_year: company_data.established_year,
        employees: company_data.employees,
        website: company_data.website.clone(),

        // Market Data
        current_price: company_data.current_price,
        market_cap: company_data.market_cap,
        outstanding_shares: company_data.outstanding_shares,

        // Financial metrics from financial ratios
        pe_ratio,
        pb_ratio,
        roe,
        roa,
        revenue,
        net_profit,
        dividend,
        eps,
    }
}

fn extract_company_name(company_profile: &Option<String>) -> Option<String> {
    let profile = company_profile.as_ref()?;

    // Simple HTML tag removal - replace <...> with empty string
    let text = profile
        .split('<')
        .flat_map(|s| s.split_once('>').map(|(_, rest)| rest))
        .collect::<String>();

    // Clean up whitespace
    let text = text.trim();

    // Simple pattern matching for common Vietnamese company types
    if let Some(idx) = text.find("Ngân hàng") {
        if let Some(end) = text[idx..].find('(') {
            let name = &text[idx..idx + end];
            return Some(name.trim().to_string());
        }
    }

    if let Some(idx) = text.find("Công ty Cổ phần") {
        if let Some(end) = text[idx..].find('(') {
            let name = &text[idx..idx + end];
            return Some(name.trim().to_string());
        }
    }

    if let Some(idx) = text.find("Công ty TNHH") {
        if let Some(end) = text[idx..].find('(') {
            let name = &text[idx..idx + end];
            return Some(name.trim().to_string());
        }
    }

    None
}

fn setup_directories() {
    if !Path::new(COMPANY_DATA_DIR).exists() {
        fs::create_dir_all(COMPANY_DATA_DIR).expect("Failed to create company_data directory");
        println!("Created directory: {}", COMPANY_DATA_DIR);
    }
}

fn load_all_tickers() -> Vec<String> {
    match TickerGroups::load_default() {
        Ok(groups) => {
            let tickers = groups.all_tickers();
            println!("Loaded {} unique tickers from {} groups", groups.ticker_count(), groups.group_count());
            tickers
        }
        Err(e) => {
            println!("Error loading ticker groups: {}", e);
            println!("Using default test tickers.");
            vec!["VCI".to_string(), "FPT".to_string(), "VCB".to_string()]
        }
    }
}

fn get_file_paths(ticker: &str) -> HashMap<&'static str, PathBuf> {
    let mut paths = HashMap::new();
    paths.insert("company_info", PathBuf::from(COMPANY_DATA_DIR).join(format!("{}_company_info.json", ticker)));
    paths.insert("financial_info", PathBuf::from(COMPANY_DATA_DIR).join(format!("{}_financial_info.json", ticker)));
    paths.insert("curated", PathBuf::from(COMPANY_DATA_DIR).join(format!("{}.json", ticker)));
    paths
}

fn is_cache_valid(file_path: &Path, cache_days: i64) -> bool {
    if !file_path.exists() {
        return false;
    }

    match fs::read_to_string(file_path) {
        Ok(content) => {
            if let Ok(json) = serde_json::from_str::<Value>(&content) {
                if let Some(created_at_str) = json.get("created_at").and_then(|v| v.as_str()) {
                    if let Ok(created_at) = DateTime::parse_from_rfc3339(created_at_str) {
                        let age = Utc::now().signed_duration_since(created_at);
                        let is_valid = age < Duration::days(cache_days);

                        if is_valid {
                            println!("   - Cache valid (age: {} days)", age.num_days());
                        } else {
                            println!("   - Cache expired (age: {} days)", age.num_days());
                        }

                        return is_valid;
                    }
                }
            }
        }
        Err(_) => return false,
    }

    false
}

fn save_json<T: Serialize>(data: &T, file_path: &Path) -> Result<(), String> {
    let json = serde_json::to_string_pretty(data)
        .map_err(|e| format!("Failed to serialize JSON: {}", e))?;

    fs::write(file_path, json)
        .map_err(|e| format!("Failed to write file: {}", e))?;

    println!("   - Saved: {}", file_path.file_name().unwrap().to_string_lossy());
    Ok(())
}
