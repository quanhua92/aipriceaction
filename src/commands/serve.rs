use crate::models::Interval;
use crate::services::{DataStore, HealthStats};
use crate::utils::{get_market_data_dir, get_crypto_data_dir};
use crate::worker;
use crate::server;
use std::sync::Arc;
use std::time::Instant;
use tokio::sync::RwLock;

pub async fn run(port: u16) {
    println!("🚀 Starting aipriceaction server on port {}", port);

    // Create VN stock data store
    let market_data_dir = get_market_data_dir();
    println!("📁 VN stocks directory: {}", market_data_dir.display());
    let data_store_vn = DataStore::new(market_data_dir.clone());
    let shared_data_store_vn = Arc::new(data_store_vn);

    // Create crypto data store
    let crypto_data_dir = get_crypto_data_dir();
    println!("📁 Crypto directory: {}", crypto_data_dir.display());
    let data_store_crypto = DataStore::new(crypto_data_dir.clone());
    let shared_data_store_crypto = Arc::new(data_store_crypto);

    // Initialize health stats
    let start_time = Instant::now();
    let health_stats = HealthStats {
        uptime_secs: 0,
        ..HealthStats::default()
    };
    let shared_health_stats = Arc::new(RwLock::new(health_stats));

    // Load daily and hourly data into memory (minute data uses disk cache)
    println!("📊 Loading VN daily and hourly data into memory...");
    let load_intervals = vec![Interval::Daily, Interval::Hourly];

    match shared_data_store_vn.load_last_year(load_intervals.clone()).await {
        Ok(_) => {
            let (daily_count, hourly_count, minute_count) = shared_data_store_vn.get_record_counts().await;
            let active_tickers = shared_data_store_vn.get_active_ticker_count().await;
            let memory_mb = shared_data_store_vn.estimate_memory_usage().await as f64 / (1024.0 * 1024.0);

            println!("✅ VN data loaded successfully:");
            println!("   📈 Active tickers: {}", active_tickers);
            println!("   📅 Daily records:  {}", daily_count);
            println!("   ⏰ Hourly records: {}", hourly_count);
            println!("   ⏱️  Minute records: {}", minute_count);
            println!("   💾 Memory usage:   {:.2} MB", memory_mb);

            // Quick check data integrity
            if let Err(e) = shared_data_store_vn.quick_check_data().await {
                eprintln!("⚠️  Warning: Quick check failed: {}", e);
            }

            // Update initial VN health stats
            let mut health = shared_health_stats.write().await;
            health.active_tickers_count = active_tickers;
            health.daily_records_count = daily_count;
            health.hourly_records_count = hourly_count;
            health.minute_records_count = minute_count;
            health.memory_usage_bytes = (memory_mb * 1024.0 * 1024.0) as usize;
            health.memory_usage_mb = memory_mb;
            health.total_tickers_count = active_tickers;
        }
        Err(e) => {
            eprintln!("⚠️  Warning: Failed to load VN data into memory: {}", e);
            eprintln!("   Server will start with empty cache. Workers will populate data.");
        }
    }

    // Load crypto daily and hourly data into memory
    println!("📊 Loading crypto daily and hourly data into memory...");
    match shared_data_store_crypto.load_last_year(load_intervals).await {
        Ok(_) => {
            let (daily_count, hourly_count, minute_count) = shared_data_store_crypto.get_record_counts().await;
            let active_tickers = shared_data_store_crypto.get_active_ticker_count().await;
            let memory_mb = shared_data_store_crypto.estimate_memory_usage().await as f64 / (1024.0 * 1024.0);

            println!("✅ Crypto data loaded successfully:");
            println!("   📈 Active cryptos: {}", active_tickers);
            println!("   📅 Daily records:  {}", daily_count);
            println!("   ⏰ Hourly records: {}", hourly_count);
            println!("   ⏱️  Minute records: {}", minute_count);
            println!("   💾 Memory usage:   {:.2} MB", memory_mb);

            // Update crypto health stats (we'll add separate fields later)
            let mut health = shared_health_stats.write().await;
            health.total_tickers_count += active_tickers;
        }
        Err(e) => {
            eprintln!("⚠️  Warning: Failed to load crypto data into memory: {}", e);
            eprintln!("   Server will start with empty crypto cache. Crypto worker will populate data.");
        }
    }

    // Spawn background auto-reload tasks for in-memory cache (15s TTL, 730 records)
    use crate::services::data_store::CACHE_TTL_SECONDS;
    use crate::services::data_store::DATA_RETENTION_RECORDS;

    println!("🔄 Starting auto-reload tasks (TTL: {}s, limit: {} records)...", CACHE_TTL_SECONDS, DATA_RETENTION_RECORDS);

    // VN auto-reload tasks (Daily + Hourly)
    let _vn_daily_reload = shared_data_store_vn.clone().spawn_auto_reload_task(Interval::Daily);
    let _vn_hourly_reload = shared_data_store_vn.clone().spawn_auto_reload_task(Interval::Hourly);

    // Crypto auto-reload tasks (Daily + Hourly)
    let _crypto_daily_reload = shared_data_store_crypto.clone().spawn_auto_reload_task(Interval::Daily);
    let _crypto_hourly_reload = shared_data_store_crypto.clone().spawn_auto_reload_task(Interval::Hourly);

    println!("✅ Auto-reload tasks started:");
    println!("   🔄 VN Daily reload:    Every {}s", CACHE_TTL_SECONDS);
    println!("   🔄 VN Hourly reload:   Every {}s", CACHE_TTL_SECONDS);
    println!("   🔄 Crypto Daily reload:  Every {}s", CACHE_TTL_SECONDS);
    println!("   🔄 Crypto Hourly reload: Every {}s", CACHE_TTL_SECONDS);
    println!("   ℹ️  Minute data uses disk cache (for aggregated intervals)");

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

    // Start axum server (blocking)
    println!("🌐 Starting HTTP server...");
    println!();
    if let Err(e) = server::serve(shared_data_store_vn, shared_data_store_crypto, shared_health_stats, port).await {
        eprintln!("❌ Server error: {}", e);
        std::process::exit(1);
    }
}
