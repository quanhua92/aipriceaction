use crate::models::Interval;
use crate::services::{DataStore, HealthStats};
use crate::worker;
use crate::server;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;
use tokio::sync::Mutex;

pub async fn run(port: u16) {
    println!("🚀 Starting aipriceaction server on port {}", port);

    // Create data store
    let market_data_dir = PathBuf::from("market_data");
    let data_store = DataStore::new(market_data_dir);
    let shared_data_store = Arc::new(data_store);

    // Initialize health stats
    let start_time = Instant::now();
    let health_stats = HealthStats {
        uptime_secs: 0,
        ..HealthStats::default()
    };
    let shared_health_stats = Arc::new(Mutex::new(health_stats));

    // Load only daily data into memory (hourly/minute read from disk on-demand)
    println!("📊 Loading daily data into memory (hourly/minute served from disk)...");
    let load_intervals = vec![Interval::Daily];

    match shared_data_store.load_last_year(load_intervals).await {
        Ok(_) => {
            let (daily_count, hourly_count, minute_count) = shared_data_store.get_record_counts().await;
            let active_tickers = shared_data_store.get_active_ticker_count().await;
            let memory_mb = shared_data_store.estimate_memory_usage().await as f64 / (1024.0 * 1024.0);

            println!("✅ Data loaded successfully:");
            println!("   📈 Active tickers: {}", active_tickers);
            println!("   📅 Daily records:  {}", daily_count);
            println!("   ⏰ Hourly records: {}", hourly_count);
            println!("   ⏱️  Minute records: {}", minute_count);
            println!("   💾 Memory usage:   {:.2} MB", memory_mb);

            // Update initial health stats
            let mut health = shared_health_stats.lock().await;
            health.active_tickers_count = active_tickers;
            health.daily_records_count = daily_count;
            health.hourly_records_count = hourly_count;
            health.minute_records_count = minute_count;
            health.memory_usage_bytes = (memory_mb * 1024.0 * 1024.0) as usize;
            health.memory_usage_mb = memory_mb;
            health.total_tickers_count = active_tickers;
        }
        Err(e) => {
            eprintln!("⚠️  Warning: Failed to load data into memory: {}", e);
            eprintln!("   Server will start with empty cache. Workers will populate data.");
        }
    }

    // Spawn daily worker (fast: 15 seconds)
    println!("⚡ Spawning daily worker (every 15 seconds)...");
    let daily_data_store = DataStore::new(PathBuf::from("market_data"));
    let daily_health_stats = shared_health_stats.clone();
    tokio::spawn(async move {
        worker::run_daily_worker(daily_data_store, daily_health_stats).await;
    });

    // Spawn slow worker (hourly + minute: 5 minutes)
    println!("🐌 Spawning slow worker (every 5 minutes)...");
    let slow_data_store = DataStore::new(PathBuf::from("market_data"));
    let slow_health_stats = shared_health_stats.clone();
    tokio::spawn(async move {
        worker::run_slow_worker(slow_data_store, slow_health_stats).await;
    });

    // Spawn uptime tracker
    let uptime_health_stats = shared_health_stats.clone();
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
            let mut health = uptime_health_stats.lock().await;
            health.uptime_secs = start_time.elapsed().as_secs();
        }
    });

    // Start axum server (blocking)
    println!("🌐 Starting HTTP server...");
    println!();
    if let Err(e) = server::serve(shared_data_store, shared_health_stats, port).await {
        eprintln!("❌ Server error: {}", e);
        std::process::exit(1);
    }
}
