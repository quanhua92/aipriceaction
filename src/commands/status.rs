use crate::services;

pub fn run() {
    println!("📊 Market Data Status\n");

    match show_status() {
        Ok(()) => {}
        Err(e) => {
            eprintln!("❌ Error: {}", e);
            std::process::exit(1);
        }
    }
}

fn show_status() -> Result<(), Box<dyn std::error::Error>> {
    // Get overall market stats
    let stats = services::get_market_stats()?;

    if !stats.has_data {
        println!("⚠️  No market data found. Run 'import-legacy' first.");
        return Ok(());
    }

    println!("📈 Total Tickers: {}\n", stats.total_tickers);

    // Show summary for key tickers
    println!("═══════════════════════════════════════════════════════════\n");

    // VNINDEX (Index example)
    if let Err(e) = show_ticker("VNINDEX") {
        eprintln!("⚠️  Could not read VNINDEX: {}", e);
    }

    println!("\n═══════════════════════════════════════════════════════════\n");

    // VIC (Stock example)
    if let Err(e) = show_ticker("VIC") {
        eprintln!("⚠️  Could not read VIC: {}", e);
    }

    println!("\n═══════════════════════════════════════════════════════════\n");
    println!("💡 Tip: All {} tickers stored in market_data/ directory", stats.total_tickers);
    println!("   Each ticker has: 1D.csv, 1H.csv, 1m.csv");

    Ok(())
}

fn show_ticker(ticker: &str) -> Result<(), Box<dyn std::error::Error>> {
    let info = services::get_ticker_info(ticker)?;
    let ticker_type = if services::is_index(ticker) { "Market Index" } else { "Stock" };

    println!("🔹 {} ({})", ticker, ticker_type);

    // Daily data
    if let Some(daily) = &info.daily {
        println!("   Daily:  {:>8} records  ({} → {})",
            format_number(daily.record_count),
            daily.first_date,
            daily.last_date
        );
        println!("           Latest: {}", format_price(daily.last_close, ticker_type));
    }

    // Hourly data
    if let Some(hourly) = &info.hourly {
        println!("   Hourly: {:>8} records  ({} → {})",
            format_number(hourly.record_count),
            hourly.first_date,
            hourly.last_date
        );
    }

    // Minute data
    if let Some(minute) = &info.minute {
        println!("   Minute: {:>8} records  ({} → {})",
            format_number(minute.record_count),
            minute.first_date,
            minute.last_date
        );
    }

    Ok(())
}

fn format_number(n: usize) -> String {
    let s = n.to_string();
    let mut result = String::new();
    for (i, c) in s.chars().rev().enumerate() {
        if i > 0 && i % 3 == 0 {
            result.insert(0, ',');
        }
        result.insert(0, c);
    }
    result
}

fn format_price(price: f64, ticker_type: &str) -> String {
    if ticker_type == "Market Index" {
        format!("{:.2}", price)
    } else {
        format!("{:.0} VND", price)
    }
}
