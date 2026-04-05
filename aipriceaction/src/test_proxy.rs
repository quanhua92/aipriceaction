use std::time::Duration as StdDuration;

use isahc::config::Configurable;
use isahc::prelude::*;

const USER_AGENT: &str =
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36";
const COOKIE_URL: &str = "https://fc.yahoo.com";
const CRUMB_URL: &str = "https://query1.finance.yahoo.com/v1/test/getcrumb";
const CHART_BASE: &str =
    "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?symbol=GC%3DF&interval=1d&range=5d&events=div%7Csplit%7CcapitalGains";

/// Fetch Yahoo cookie + crumb using a reqwest client.
/// Returns `(cookie_str, crumb_str)`.
async fn fetch_crumb(client: &reqwest::Client) -> Result<(String, String), String> {
    // Step 1: GET fc.yahoo.com to obtain cookie
    let cookie_resp = client
        .get(COOKIE_URL)
        .send()
        .await
        .map_err(|e| format!("cookie fetch: {}", e))?;
    let cookie_header = cookie_resp
        .headers()
        .get("set-cookie")
        .ok_or_else(|| "no set-cookie header in response".to_string())?
        .to_str()
        .map_err(|e| format!("cookie header parse: {}", e))?
        .to_string();

    // Step 2: GET crumb with cookie
    let crumb_resp = client
        .get(CRUMB_URL)
        .header("Cookie", &cookie_header)
        .send()
        .await
        .map_err(|e| format!("crumb fetch: {}", e))?;
    let crumb = crumb_resp
        .text()
        .await
        .map_err(|e| format!("crumb read: {}", e))?;
    let crumb = crumb.trim().to_string();
    if crumb.is_empty()
        || crumb.contains("Too Many Requests")
        || crumb.contains("Invalid Cookie")
    {
        return Err(format!("bad crumb response: {}", crumb));
    }
    Ok((cookie_header, crumb))
}

pub fn run() {
    let rt = tokio::runtime::Runtime::new().expect("Failed to create Tokio runtime");
    rt.block_on(async {
        run_inner().await;
    });
}

async fn run_inner() {
    let proxies_str = std::env::var("HTTP_PROXIES").unwrap_or_default();
    let proxies: Vec<&str> = proxies_str
        .split(',')
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .collect();

    if proxies.is_empty() {
        tracing::error!("No proxies found. Set HTTP_PROXIES in .env (comma-separated).");
        return;
    }

    tracing::info!("Found {} proxy(s) to test", proxies.len());
    tracing::info!("User-Agent: {}", USER_AGENT);
    tracing::info!("Chart URL: {}", CHART_BASE);
    tracing::info!("{}", "─".repeat(70));

    // Test 1: reqwest direct + crumb + User-Agent (no proxy, run once)
    {
        let label = "reqwest (direct + crumb)";
        let start = std::time::Instant::now();
        let client = reqwest::Client::builder()
            .timeout(StdDuration::from_secs(15))
            .user_agent(USER_AGENT)
            .build()
            .map_err(|e| format!("{}", e));
        match client {
            Ok(client) => match fetch_crumb(&client).await {
                Ok((cookie, crumb)) => {
                    let chart_url = format!("{}&crumb={}", CHART_BASE, crumb);
                    match client
                        .get(&chart_url)
                        .header("Cookie", &cookie)
                        .send()
                        .await
                    {
                        Ok(resp) if resp.status().is_success() => {
                            tracing::info!("  [PASS] {}: {}ms", label, start.elapsed().as_millis());
                        }
                        Ok(resp) => {
                            tracing::info!("  [FAIL] {}: {}ms — HTTP {}", label, start.elapsed().as_millis(), resp.status());
                        }
                        Err(e) => {
                            tracing::info!("  [FAIL] {}: {}ms — {}", label, start.elapsed().as_millis(), e);
                        }
                    }
                }
                Err(e) => {
                    tracing::info!("  [FAIL] {}: {}ms — crumb error: {}", label, start.elapsed().as_millis(), e);
                }
            },
            Err(e) => {
                tracing::info!("  [FAIL] {}: build error — {}", label, e);
            }
        }
    }

    // Test 2: yahoo_finance_api crate (direct, no proxy, run once)
    {
        let label = "yahoo_finance_api (direct)";
        let start = std::time::Instant::now();
        match yahoo_finance_api::YahooConnector::builder()
            .timeout(StdDuration::from_secs(15))
            .build()
        {
            Ok(connector) => {
                match connector.get_quote_range("GC=F", "5d", "1d").await {
                    Ok(_resp) => {
                        tracing::info!("  [PASS] {}: {}ms", label, start.elapsed().as_millis());
                    }
                    Err(e) => {
                        tracing::info!("  [FAIL] {}: {}ms — {}", label, start.elapsed().as_millis(), e);
                    }
                }
            }
            Err(e) => {
                tracing::info!("  [FAIL] {}: build error — {}", label, e);
            }
        }
    }

    tracing::info!("{}", "─".repeat(70));

    for proxy_url in &proxies {
        tracing::info!("--- Testing proxy: {} ---", proxy_url);

        // Test 1: isahc proxy + crumb + User-Agent
        {
            let label = "isahc (proxy + crumb)";
            let start = std::time::Instant::now();
            match proxy_url.parse::<isahc::http::Uri>() {
                Ok(proxy_uri) => {
                    match isahc::HttpClient::builder()
                        .proxy(Some(proxy_uri))
                        .timeout(StdDuration::from_secs(15))
                        .build()
                    {
                        Ok(client) => {
                            // Step 1: fetch cookie
                            let cookie_req = isahc::Request::builder()
                                .uri(COOKIE_URL)
                                .header("User-Agent", USER_AGENT)
                                .body(())
                                .unwrap();
                            match client.send(cookie_req) {
                                Ok(resp) => {
                                    let cookie_header = resp
                                        .headers()
                                        .get("set-cookie")
                                        .and_then(|v| v.to_str().ok())
                                        .map(|s| s.to_string());
                                    match cookie_header {
                                        Some(cookie) => {
                                            // Step 2: fetch crumb
                                            let crumb_req = isahc::Request::builder()
                                                .uri(CRUMB_URL)
                                                .header("User-Agent", USER_AGENT)
                                                .header("Cookie", &cookie)
                                                .body(())
                                                .unwrap();
                                            match client.send(crumb_req) {
                                                Ok(mut resp) => {
                                                    let crumb = resp.text().unwrap_or_default();
                                                    let crumb = crumb.trim();
                                                    if crumb.is_empty()
                                                        || crumb.contains("Too Many Requests")
                                                        || crumb.contains("Invalid Cookie")
                                                    {
                                                        tracing::info!("  [FAIL] {}: {}ms — bad crumb: {}", label, start.elapsed().as_millis(), crumb);
                                                    } else {
                                                        // Step 3: fetch chart
                                                        let chart_url =
                                                            format!("{}&crumb={}", CHART_BASE, crumb);
                                                        let chart_req = isahc::Request::builder()
                                                            .uri(&chart_url)
                                                            .header("User-Agent", USER_AGENT)
                                                            .header("Cookie", &cookie)
                                                            .body(())
                                                            .unwrap();
                                                        match client.send(chart_req) {
                                                            Ok(resp) if resp.status().is_success() => {
                                                                tracing::info!("  [PASS] {}: {}ms", label, start.elapsed().as_millis());
                                                            }
                                                            Ok(resp) => {
                                                                tracing::info!("  [FAIL] {}: {}ms — HTTP {}", label, start.elapsed().as_millis(), resp.status());
                                                            }
                                                            Err(e) => {
                                                                tracing::info!("  [FAIL] {}: {}ms — {}", label, start.elapsed().as_millis(), e);
                                                            }
                                                        }
                                                    }
                                                }
                                                Err(e) => {
                                                    tracing::info!("  [FAIL] {}: {}ms — crumb fetch: {}", label, start.elapsed().as_millis(), e);
                                                }
                                            }
                                        }
                                        None => {
                                            tracing::info!("  [FAIL] {}: {}ms — no set-cookie from {}", label, start.elapsed().as_millis(), COOKIE_URL);
                                        }
                                    }
                                }
                                Err(e) => {
                                    tracing::info!("  [FAIL] {}: {}ms — cookie fetch: {}", label, start.elapsed().as_millis(), e);
                                }
                            }
                        }
                        Err(e) => {
                            tracing::info!("  [FAIL] {}: build error — {}", label, e);
                        }
                    }
                }
                Err(e) => {
                    tracing::info!("  [FAIL] {}: invalid proxy URI — {}", label, e);
                }
            }
        }

        // Test 2: reqwest SOCKS5 proxy + crumb + User-Agent
        {
            let label = "reqwest (socks5 proxy + crumb)";
            let start = std::time::Instant::now();
            match reqwest::Proxy::all(*proxy_url) {
                Ok(proxy) => {
                    match reqwest::Client::builder()
                        .proxy(proxy)
                        .timeout(StdDuration::from_secs(15))
                        .user_agent(USER_AGENT)
                        .build()
                    {
                        Ok(client) => match fetch_crumb(&client).await {
                            Ok((cookie, crumb)) => {
                                let chart_url = format!("{}&crumb={}", CHART_BASE, crumb);
                                match client
                                    .get(&chart_url)
                                    .header("Cookie", &cookie)
                                    .send()
                                    .await
                                {
                                    Ok(resp) if resp.status().is_success() => {
                                        tracing::info!("  [PASS] {}: {}ms", label, start.elapsed().as_millis());
                                    }
                                    Ok(resp) => {
                                        tracing::info!("  [FAIL] {}: {}ms — HTTP {}", label, start.elapsed().as_millis(), resp.status());
                                    }
                                    Err(e) => {
                                        tracing::info!("  [FAIL] {}: {}ms — {}", label, start.elapsed().as_millis(), e);
                                    }
                                }
                            }
                            Err(e) => {
                                tracing::info!("  [FAIL] {}: {}ms — crumb error: {}", label, start.elapsed().as_millis(), e);
                            }
                        },
                        Err(e) => {
                            tracing::info!("  [FAIL] {}: build error — {}", label, e);
                        }
                    }
                }
                Err(e) => {
                    tracing::info!("  [FAIL] {}: invalid proxy URL — {}", label, e);
                }
            }
        }

        tracing::info!("{}", "─".repeat(70));
    }

    tracing::info!("Proxy testing complete.");
}
