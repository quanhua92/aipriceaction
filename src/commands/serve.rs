use crate::models::Interval;
use crate::services::{DataStore, HealthStats};
use crate::services::mpsc::create_bounded_channels;
use crate::utils::{get_market_data_dir, get_crypto_data_dir};
use crate::worker;
use crate::server;
use std::sync::Arc;
use std::time::Instant;
use tokio::sync::RwLock;

pub async fn run(port: u16) {
    println!("🚀 Starting aipriceaction server on port {}", port);

    // Create bounded MPSC channels (capacity=100 to handle message volume)
    println!("🔗 Creating bounded MPSC channels (capacity=100)...");
    let (vn_tx, vn_rx) = create_bounded_channels();
    let (crypto_tx, crypto_rx) = create_bounded_channels();

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

    // Start update listeners for real-time cache updates
    println!("🔄 Starting real-time update listeners...");
    shared_data_store_vn.start_update_listener(vn_rx);
    shared_data_store_crypto.start_update_listener(crypto_rx);

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

    // Load crypto daily data only, skip 1H and 1m (background workers will handle it)
    println!("📊 Loading crypto daily data into memory (128 days for fast startup, 1H/1m handled by background workers)...");
    match shared_data_store_crypto.load_startup_data(load_intervals, Some(skip_intervals)).await {
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

    // Auto-reload removed - MPSC handles real-time cache updates efficiently
    // Memory cache updated via MPSC messages from workers (no periodic disk scanning needed)

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

    // Clone channel senders for workers
    let vn_tx_daily = vn_tx.clone();
    let vn_tx_slow = vn_tx.clone(); // Use MPSC channel for slow worker
    let crypto_tx_worker = crypto_tx.clone(); // Use MPSC channel for crypto worker

    // Create dedicated runtime for VN daily worker
    std::thread::spawn(move || {
        let daily_runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)  // 2 threads for daily worker
            .thread_name("vn-daily-worker")
            .enable_all()
            .build()
            .expect("Failed to create daily worker runtime");

        daily_runtime.block_on(async {
            println!("⚡ Spawning VN daily worker in dedicated runtime...");
            worker::run_daily_worker_with_channel(worker_health_daily, Some(vn_tx_daily)).await;
        });
    });

    // Create dedicated runtime for VN slow worker (hourly only)
    std::thread::spawn(move || {
        let slow_runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)  // 2 threads for hourly worker
            .thread_name("vn-slow-worker-hourly")
            .enable_all()
            .build()
            .expect("Failed to create slow worker runtime");

        slow_runtime.block_on(async {
            println!("🐌 Spawning VN HOURLY worker in dedicated runtime...");
            worker::run_slow_worker_with_channel(worker_health_slow, Some(vn_tx_slow)).await;
        });
    });

    // Create dedicated runtime for VN minute worker (separate to avoid API overload)
    let worker_health_minute = shared_health_stats.clone();
    let vn_tx_minute = vn_tx.clone(); // Use MPSC channel for minute worker
    std::thread::spawn(move || {
        let minute_runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)  // 2 threads for minute worker
            .thread_name("vn-slow-worker-minute")
            .enable_all()
            .build()
            .expect("Failed to create minute worker runtime");

        minute_runtime.block_on(async {
            println!("🐌 Spawning VN MINUTE worker in separate dedicated runtime...");
            worker::slow_worker::run_minute_worker_separate(worker_health_minute, Some(vn_tx_minute)).await;
        });
    });

    // Create dedicated runtime for crypto worker
    std::thread::spawn(move || {
        let crypto_runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)  // 2 threads for crypto worker
            .thread_name("crypto-worker")
            .enable_all()
            .build()
            .expect("Failed to create crypto worker runtime");

        crypto_runtime.block_on(async {
            println!("🪙 Spawning crypto worker in dedicated runtime...");
            worker::run_crypto_worker_with_channel(worker_health_crypto, Some(crypto_tx_worker)).await;
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
    if let Err(e) = server::serve(shared_data_store_vn, shared_data_store_crypto, shared_health_stats, port).await {
        eprintln!("❌ Server error: {}", e);
        std::process::exit(1);
    }
}
