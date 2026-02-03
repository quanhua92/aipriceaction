use aipriceaction::services::vci::VciClient;
use tokio;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("=== VCI Client Examples ===\n");

    // Example 1: Default (direct connection + proxies from HTTP_PROXIES env var)
    println!("--- Example 1: Direct connection enabled (default) ---");
    {
        // Note: If HTTP_PROXIES env var is set, it will be used alongside direct connection
        let mut client = VciClient::new_async(true, 30, None).await?;
        match client.get_history("VCB", "2024-12-01", Some("2024-12-05"), "1D").await {
            Ok(data) => println!("✅ Got {} records\n", data.len()),
            Err(e) => println!("✗ Failed: {}\n", e),
        }
    }

    // Example 2: Proxy-only mode (no direct connection)
    println!("--- Example 2: Proxy-only mode ---");
    {
        // Set HTTP_PROXIES env var before creating client
        // Usage: HTTP_PROXIES="socks5://user:pass@host:port" cargo run --example test_vci
        if std::env::var("HTTP_PROXIES").is_ok() {
            let mut client = VciClient::new_async_with_options(true, 30, None, false).await?;
            match client.get_history("VCB", "2024-12-01", Some("2024-12-05"), "1D").await {
                Ok(data) => println!("✅ Got {} records (proxy-only)\n", data.len()),
                Err(e) => println!("✗ Failed: {}\n", e),
            }
        } else {
            println!("⚠️  Skipped - HTTP_PROXIES env var not set");
            println!("   Usage: HTTP_PROXIES=\"socks5://host:port\" cargo run --example test_vci\n");
        }
    }

    // Example 3: Multiple proxies
    println!("--- Example 3: Multiple proxies with fallback ---");
    {
        // Set HTTP_PROXIES to comma-separated list
        // Usage: HTTP_PROXIES="socks5://proxy1:1080,http://proxy2:8080" cargo run --example test_vci
        if std::env::var("HTTP_PROXIES").is_ok() {
            let mut client = VciClient::new_async(true, 30, None).await?;
            match client.get_history("VIC", "2024-12-01", Some("2024-12-05"), "1D").await {
                Ok(data) => println!("✅ Got {} records (with proxy fallback)\n", data.len()),
                Err(e) => println!("✗ Failed: {}\n", e),
            }
        } else {
            println!("⚠️  Skipped - HTTP_PROXIES env var not set");
            println!("   Usage: HTTP_PROXIES=\"socks5://proxy1:1080,http://proxy2:8080\" cargo run --example test_vci\n");
        }
    }

    println!("=== Examples completed ===");
    Ok(())
}
