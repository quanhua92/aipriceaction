use crate::models::Interval;
use crate::services::{DataStore, HealthStats};
use crate::utils::{get_market_data_dir, get_crypto_data_dir};
use crate::worker;
use crate::server;
use std::sync::Arc;
use std::time::Instant;
use tokio::sync::RwLock;

pub async fn run(port: u16) -> Result<(), crate::error::Error> {
    println!("🚀 Starting aipriceaction server on port {}", port);

    // Create VN stock data store
    let market_data_dir = get_market_data_dir();
    println!("📁 VN stocks directory: {}", market_data_dir.display());
    let data_store_vn = DataStore::new(market_data_dir.clone()).await
        .map_err(|e| crate::error::Error::Other(format!("Failed to create VN data store: {}", e)))?;
    let shared_data_store_vn = Arc::new(data_store_vn);

    // Create crypto data store
    let crypto_data_dir = get_crypto_data_dir();
    println!("📁 Crypto directory: {}", crypto_data_dir.display());
    let data_store_crypto = DataStore::new(crypto_data_dir.clone()).await
        .map_err(|e| crate::error::Error::Other(format!("Failed to create crypto data store: {}", e)))?;
    let shared_data_store_crypto = Arc::new(data_store_crypto);

    // Initialize health stats
    let start_time = Instant::now();
    let health_stats = HealthStats {
        uptime_secs: 0,
        ..HealthStats::default()
    };
    let shared_health_stats = Arc::new(RwLock::new(health_stats));

    // Load daily data only, skip 1H and 1m (background workers will handle it)
    println!("📊 Loading VN daily data into memory (128 days for fast startup, 1H/1m handled by background workers)...");
    let load_intervals = vec![Interval::Daily];
    let skip_intervals = vec![Interval::Hourly, Interval::Minute];

    match shared_data_store_vn.load_startup_data(load_intervals.clone(), Some(skip_intervals.clone())).await {
        Ok(_) => {
            let (daily_count, hourly_count, minute_count) = shared_data_store_vn.get_record_counts().await;
            let active_tickers = shared_data_store_vn.get_active_ticker_count().await;
            let memory_mb = shared_data_store_vn.estimate_memory_usage() as f64 / (1024.0 * 1024.0);

            println!("✅ VN data loaded successfully:");
            println!("   📈 Active tickers: {}", active_tickers);
            println!("   📅 Daily records:  {}", daily_count);
            println!("   ⏰ Hourly records: {}", hourly_count);
            println!("   ⏱️  Minute records: {}", minute_count);
            println!("   💾 Memory usage:   {:.2} MB", memory_mb);

            // Update initial VN health stats (using available fields)
            let mut health = shared_health_stats.write().await;
            health.daily_iteration_count = daily_count as u64;
            health.slow_iteration_count = hourly_count as u64; // Reusing for hourly
            health.crypto_iteration_count = minute_count as u64; // Reusing for minute
        }
        Err(e) => {
            eprintln!("⚠️  Warning: Failed to load VN data into memory: {}", e);
            eprintln!("   Server will start with empty cache. Workers will populate data.");
        }
    }

    // Load crypto daily data only, skip 1H and 1m (background workers will handle it)
    println!("📊 Loading crypto daily data into memory (128 days for fast startup, 1H/1m handled by background workers)...");
    match shared_data_store_crypto.load_startup_data(load_intervals, Some(skip_intervals)).await {
        Ok(_) => {
            let (daily_count, hourly_count, minute_count) = shared_data_store_crypto.get_record_counts().await;
            let active_tickers = shared_data_store_crypto.get_active_ticker_count().await;
            let memory_mb = shared_data_store_crypto.estimate_memory_usage() as f64 / (1024.0 * 1024.0);

            println!("✅ Crypto data loaded successfully:");
            println!("   📈 Active cryptos: {}", active_tickers);
            println!("   📅 Daily records:  {}", daily_count);
            println!("   ⏰ Hourly records: {}", hourly_count);
            println!("   ⏱️  Minute records: {}", minute_count);
            println!("   💾 Memory usage:   {:.2} MB", memory_mb);

            // Note: Crypto stats are tracked via crypto_iteration_count during runtime
        }
        Err(e) => {
            eprintln!("⚠️  Warning: Failed to load crypto data into memory: {}", e);
            eprintln!("   Server will start with empty crypto cache. Crypto worker will populate data.");
        }
    }

    // Spawn background auto-reload tasks for in-memory cache in dedicated runtime
    use crate::services::data_store::CACHE_TTL_SECONDS;
    use crate::services::data_store::DATA_RETENTION_RECORDS;

    println!("🔄 Starting auto-reload tasks (TTL: {}s, limit: {} records)...", CACHE_TTL_SECONDS, DATA_RETENTION_RECORDS);

    // Create dedicated runtime for auto-reload tasks (separate from HTTP server runtime)
    let auto_reload_data_vn = shared_data_store_vn.clone();
    let auto_reload_data_crypto = shared_data_store_crypto.clone();

    std::thread::spawn(move || {
        let auto_reload_runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(3)  // Fixed small number for auto-reload tasks
            .thread_name("auto-reload")
            .enable_all()
            .build()
            .expect("Failed to create auto-reload runtime");

        auto_reload_runtime.block_on(async {
            println!("🔄 Auto-reload runtime started with 3 worker threads");

            // VN auto-reload tasks (Daily + Hourly + Minute)
            let vn_daily_reload = auto_reload_data_vn.spawn_auto_reload_task(Interval::Daily);
            let vn_hourly_reload = auto_reload_data_vn.spawn_auto_reload_task(Interval::Hourly);
            let vn_minute_reload = auto_reload_data_vn.spawn_auto_reload_task(Interval::Minute);

            // Crypto auto-reload tasks (Daily + Hourly + Minute)
            let crypto_daily_reload = auto_reload_data_crypto.spawn_auto_reload_task(Interval::Daily);
            let crypto_hourly_reload = auto_reload_data_crypto.spawn_auto_reload_task(Interval::Hourly);
            let crypto_minute_reload = auto_reload_data_crypto.spawn_auto_reload_task(Interval::Minute);

            // Store handles to keep them alive
            let _handles = vec![
                vn_daily_reload, vn_hourly_reload, vn_minute_reload,
                crypto_daily_reload, crypto_hourly_reload, crypto_minute_reload,
            ];

            println!("✅ Auto-reload tasks started in dedicated runtime:");
            println!("   🔄 VN Daily reload:    Every {}s", CACHE_TTL_SECONDS);
            println!("   🔄 VN Hourly reload:   Every {}s", CACHE_TTL_SECONDS);
            println!("   🔄 VN Minute reload:   Every {}s", CACHE_TTL_SECONDS);
            println!("   🔄 Crypto Daily reload:  Every {}s", CACHE_TTL_SECONDS);
            println!("   🔄 Crypto Hourly reload: Every {}s", CACHE_TTL_SECONDS);
            println!("   🔄 Crypto Minute reload: Every {}s", CACHE_TTL_SECONDS);

            // Keep runtime alive
            tokio::signal::ctrl_c().await.ok();
        });
    });

    // CPU auto-detection for optimal performance
    println!();
    println!("🔧 CPU Configuration (auto-detected):");
    let cpu_cores = num_cpus::get();
    let worker_threads = crate::utils::get_worker_threads();
    let concurrent_batches = crate::utils::get_concurrent_batches();
    println!("   💻 CPU cores detected: {}", cpu_cores);
    println!("   🔧 Worker threads:     {} (1-2 cores→1, 3-4 cores→2, 5+ cores→4)", worker_threads);
    println!("   ⚡ Concurrent batches: {} (1-2 cores→1, 3-4 cores→2, 5+ cores→3)", concurrent_batches);
    println!("   📝 Workers will use {} concurrent API batch requests", concurrent_batches);
    println!();

    // Create dedicated runtime for workers (auto-detected based on CPU cores)
    // Workers only write CSVs to disk, don't touch memory cache
    println!("⚙️  Creating dedicated worker runtime ({} threads)...", worker_threads);
    let worker_health_daily = shared_health_stats.clone();
    let worker_health_slow = shared_health_stats.clone();
    let worker_health_crypto = shared_health_stats.clone();

    std::thread::spawn(move || {
        let worker_runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(worker_threads)  // Auto-detected based on CPU cores
            .thread_name("worker-pool")
            .enable_all()
            .build()
            .expect("Failed to create worker runtime");

        worker_runtime.block_on(async {
            println!("⚡ Spawning daily worker (every 15 seconds)...");
            tokio::spawn(async move {
                worker::run_daily_worker(worker_health_daily).await;
            });

            println!("🐌 Spawning slow worker (every 5 minutes)...");
            tokio::spawn(async move {
                worker::run_slow_worker(worker_health_slow).await;
            });

            println!("🪙 Spawning crypto worker (every 15 minutes)...");
            tokio::spawn(async move {
                worker::run_crypto_worker(worker_health_crypto).await;
            });

            // Keep runtime alive
            tokio::signal::ctrl_c().await.ok();
        });
    });

    // Spawn uptime tracker
    let uptime_health_stats = shared_health_stats.clone();
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
            let mut health = uptime_health_stats.write().await;
            health.uptime_secs = start_time.elapsed().as_secs();
        }
    });

    // Start axum server in main runtime (dedicated to HTTP requests)
    println!("🌐 Starting HTTP server in dedicated main runtime...");
    println!("   ℹ️  Main runtime handles HTTP API requests only");
    println!("   ℹ️  Auto-reload tasks run in separate runtime (3 threads)");
    println!("   ℹ️  Background workers run in dedicated runtime ({} threads)", worker_threads);
    println!();
    server::serve(shared_data_store_vn, shared_data_store_crypto, shared_health_stats, port).await
        .map_err(|e| crate::error::Error::Other(format!("Server error: {}", e)))?;

    Ok(())
}
