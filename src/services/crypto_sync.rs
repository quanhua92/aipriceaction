use crate::error::Error;
use crate::models::{Interval, SyncConfig, SyncStats};
use crate::services::crypto_fetcher::CryptoFetcher;
use crate::services::vci::OhlcvData;
use crate::services::csv_enhancer::{enhance_data, save_enhanced_csv_to_dir_with_changes};
use crate::services::mpsc::TickerUpdate;
use crate::utils::get_crypto_data_dir;
use chrono::Utc;
use std::collections::HashMap;
use std::sync::mpsc::SyncSender;
use std::time::Instant;

/// High-level cryptocurrency data synchronization orchestrator
pub struct CryptoSync {
    config: SyncConfig,
    fetcher: CryptoFetcher,
    #[allow(dead_code)]
    stats: SyncStats,
    /// Optional MPSC channel sender for real-time ticker updates
    channel_sender: Option<SyncSender<TickerUpdate>>,
}

impl CryptoSync {
    /// Get appropriate chunk size for different intervals
    pub fn get_chunk_size_for_interval(&self, interval: Interval) -> usize {
        match interval {
            Interval::Minute => 10,  // Process 10 cryptos per batch for 1m
            Interval::Hourly => 25,  // Process 25 cryptos per batch for 1H
            Interval::Daily => 50,   // Process 50 cryptos per batch for 1D
        }
    }

    /// Split crypto list into chunks for batch processing
    fn split_cryptos_into_chunks(&self, cryptos: &[String], chunk_size: usize) -> Vec<Vec<String>> {
        cryptos.chunks(chunk_size)
            .map(|chunk| chunk.to_vec())
            .collect()
    }

    /// Create new crypto sync orchestrator
    pub fn new(config: SyncConfig, api_key: Option<String>) -> Result<Self, Error> {
        let fetcher = CryptoFetcher::new(api_key)?;

        Ok(Self {
            config,
            fetcher,
            stats: SyncStats::new(),
            channel_sender: None,
        })
    }

    /// Create new CryptoSync with MPSC channel support
    pub fn new_with_channel(
        config: SyncConfig,
        api_key: Option<String>,
        channel_sender: Option<SyncSender<TickerUpdate>>,
    ) -> Result<Self, Error> {
        let fetcher = CryptoFetcher::new(api_key)?;

        Ok(Self {
            config,
            fetcher,
            stats: SyncStats::new(),
            channel_sender,
        })
    }

    /// Synchronize all intervals for all crypto symbols
    pub async fn sync_all_intervals(&mut self, symbols: &[String]) -> Result<(), Error> {
        let start_time = Instant::now();

        println!("\nSYNC::CRYPTO::🚀 Starting crypto data sync: {} cryptos, {} intervals",
            symbols.len(),
            self.config.intervals.len()
        );

        println!("SYNC::CRYPTO::📅 Date range: {} to {}", self.config.start_date, self.config.end_date);
        println!("SYNC::CRYPTO::📊 Mode: {}", if self.config.force_full { "FULL DOWNLOAD" } else { "RESUME (incremental)" });

        // Process each interval
        for interval in &self.config.intervals.clone() {
            println!("\nSYNC::CRYPTO::{}", "=".repeat(70));
            println!("SYNC::CRYPTO::📊 Interval: {}", interval.to_filename());
            println!("SYNC::CRYPTO::{}", "=".repeat(70));

            self.sync_interval(symbols, *interval).await?;
        }

        let total_time = start_time.elapsed();

        // Print final summary
        self.print_final_summary(total_time);

        Ok(())
    }

    /// Synchronize a single interval for all crypto symbols
    async fn sync_interval(&mut self, symbols: &[String], interval: Interval) -> Result<(), Error> {
        let interval_start_time = Instant::now();

        // Check if proxy mode for batch API optimization
        let is_proxy_mode = self.fetcher.is_proxy_mode();
        let delay_ms = if is_proxy_mode { 50 } else { 200 };

        // Categorize cryptos (resume vs full history vs partial)
        let disable_partial = self.config.force_full;
        let category = self.fetcher.categorize_cryptos(symbols, interval, disable_partial)?;

        // Process resume cryptos
        let mut success_count = 0;
        let mut failed_cryptos: Vec<String> = Vec::new();

        // If force_full, treat all cryptos as full history cryptos
        let (resume_list, partial_list, full_list) = if self.config.force_full {
            // force_full=true: all cryptos get full history download
            let mut all_full = category.full_history_cryptos.clone();
            all_full.extend(category.resume_cryptos.iter().map(|(s, _)| s.clone()));
            all_full.extend(category.partial_history_cryptos.iter().map(|(s, _)| s.clone()));
            (Vec::new(), Vec::new(), all_full)
        } else {
            // Normal mode: respect categorization
            (category.resume_cryptos.clone(),
             category.partial_history_cryptos.clone(),
             category.full_history_cryptos.clone())
        };

        // For log verbosity: show first 3 and last 2
        let show_first = 3;
        let show_last = 2;

        if !resume_list.is_empty() {
            println!("\nSYNC::CRYPTO::🔄 Processing {} resume cryptos...", resume_list.len());
            let total = resume_list.len();

            if is_proxy_mode {
                // API Proxy mode: Use chunked batch API for resume cryptos
                let chunk_size = self.get_chunk_size_for_interval(interval);
                let resume_symbols: Vec<String> = resume_list.iter().map(|(s, _)| s.clone()).collect();
                let crypto_chunks = self.split_cryptos_into_chunks(&resume_symbols, chunk_size);

                println!("   SYNC::CRYPTO::🚀 Batch API mode: fetching {} cryptos in {} chunks of {} cryptos each",
                    resume_symbols.len(), crypto_chunks.len(), chunk_size);

                // Find minimum last_date across all resume cryptos
                let min_last_date = resume_list.iter()
                    .map(|(_, date)| date.as_str())
                    .min()
                    .unwrap_or("2010-07-17"); // Fallback to BTC inception

                // Process each chunk
                for (chunk_idx, chunk) in crypto_chunks.iter().enumerate() {
                    let chunk_start_time = Instant::now();
                    println!("   SYNC::CRYPTO::📦 Processing chunk {}/{} ({} cryptos)...",
                        chunk_idx + 1, crypto_chunks.len(), chunk.len());

                    match self.fetcher.fetch_batch(chunk, min_last_date, interval).await {
                        Ok(batch_data) => {
                            let _chunk_success = 0;
                            let chunk_time = chunk_start_time.elapsed();

                            // Process each crypto's data in this chunk
                            for symbol in chunk {
                                if let Some(last_date) = resume_list.iter().find(|(s, _)| s == symbol).map(|(_, d)| d) {
                                    if let Some(data) = batch_data.get(symbol) {
                                        match self.process_crypto(symbol, Some(data.clone()), interval, last_date).await {
                                            Ok(_) => {
                                                success_count += 1;
                                                println!("   SYNC::CRYPTO::✅ {} ({} records)", symbol, data.len());
                                            }
                                            Err(e) => {
                                                eprintln!("   SYNC::CRYPTO::❌ {} Save failed: {}", symbol, e);
                                                failed_cryptos.push(symbol.clone());
                                            }
                                        }
                                    } else {
                                        eprintln!("   SYNC::CRYPTO::❌ {} No data in batch response", symbol);
                                        failed_cryptos.push(symbol.clone());
                                    }
                                }
                            }

                            println!("   SYNC::CRYPTO::📦 Chunk {}/{} completed in {:.1}s",
                                chunk_idx + 1, crypto_chunks.len(), chunk_time.as_secs_f64());

                            // Small delay between chunks to prevent overwhelming server
                            if chunk_idx < crypto_chunks.len() - 1 {
                                tokio::time::sleep(tokio::time::Duration::from_millis(500)).await;
                            }
                        }
                        Err(e) => {
                            let chunk_time = chunk_start_time.elapsed();
                            if matches!(e, Error::RateLimit) {
                                eprintln!("SYNC::CRYPTO::❌ Rate limit hit in batch mode - aborting sync and waiting for next interval");
                                // Return rate limit error immediately to abort entire sync
                                return Err(Error::RateLimit);
                            }
                            eprintln!("SYNC::CRYPTO::❌ Chunk {}/{} failed after {:.1}s: {}",
                                chunk_idx + 1, crypto_chunks.len(), chunk_time.as_secs_f64(), e);
                            failed_cryptos.extend(chunk.iter().cloned());
                        }
                    }
                }
            } else {
                // CryptoCompare mode: Sequential fetching (one by one)
                for (idx, (symbol, last_date)) in resume_list.iter().enumerate() {
                    let should_print = idx < show_first || idx >= total.saturating_sub(show_last);
                    if idx == show_first && total > show_first + show_last {
                        println!("   SYNC::CRYPTO::... ({} more cryptos) ...", total - show_first - show_last);
                    }

                    match self.fetch_recent(symbol, last_date, interval).await {
                        Ok(data) => {
                            match self.process_crypto(symbol, Some(data), interval, last_date).await {
                                Ok(_) => {
                                    success_count += 1;
                                    if should_print {
                                        println!("   SYNC::CRYPTO::[{}/{}] {} ✅", idx + 1, total, symbol);
                                    }
                                }
                                Err(e) => {
                                    eprintln!("   SYNC::CRYPTO::[{}/{}] {} ❌ Save failed: {}", idx + 1, total, symbol, e);
                                    failed_cryptos.push(symbol.clone());
                                }
                            }
                        }
                        Err(e) => {
                            if matches!(e, Error::RateLimit) {
                                eprintln!("SYNC::CRYPTO::❌ Rate limit hit - aborting sync and waiting for next interval");
                                // Return rate limit error immediately to abort entire sync
                                return Err(Error::RateLimit);
                            }
                            eprintln!("   SYNC::CRYPTO::[{}/{}] {} ❌ {}", idx + 1, total, symbol, e);
                            failed_cryptos.push(symbol.clone());
                        }
                    }

                    tokio::time::sleep(tokio::time::Duration::from_millis(delay_ms)).await;
                }
            }
        }

        // Process partial history cryptos (gap > 3 days)
        if !partial_list.is_empty() {
            println!("\nSYNC::CRYPTO::🔄 Processing {} partial history cryptos...", partial_list.len());
            let total = partial_list.len();

            // Process in batch mode if available
            if is_proxy_mode {
                // Group partial cryptos into chunks
                let batch_size = 50; // Partial history chunks
                let crypto_chunks: Vec<Vec<String>> = partial_list
                    .iter()
                    .map(|(s, _)| s.clone())
                    .collect::<Vec<String>>()
                    .chunks(batch_size)
                    .map(|chunk| chunk.to_vec())
                    .collect();

                println!("   SYNC::CRYPTO::🚀 Batch API mode: fetching {} partial cryptos in {} chunks of {} cryptos each",
                    total, crypto_chunks.len(), batch_size);

                // Process each chunk
                for (chunk_idx, chunk) in crypto_chunks.iter().enumerate() {
                    println!("   SYNC::CRYPTO::📦 Processing partial chunk {}/{} ({} cryptos)...",
                        chunk_idx + 1, crypto_chunks.len(), chunk.len());

                    // Use minimum start date for this chunk
                    let min_last_date = partial_list.iter()
                        .filter(|(s, _)| chunk.contains(s))
                        .map(|(_, date)| date.as_str())
                        .min()
                        .unwrap_or("2010-07-17");

                    match self.fetcher.fetch_batch(chunk, min_last_date, interval).await {
                        Ok(batch_data) => {
                            // Process each crypto's data in this chunk
                            for symbol in chunk {
                                if let Some(last_date) = partial_list.iter().find(|(s, _)| s == symbol).map(|(_, d)| d) {
                                    if let Some(data) = batch_data.get(symbol) {
                                        match self.process_crypto(symbol, Some(data.clone()), interval, last_date).await {
                                            Ok(_) => {
                                                success_count += 1;
                                                println!("   SYNC::CRYPTO::✅ {} ({} records) [partial]", symbol, data.len());
                                            }
                                            Err(e) => {
                                                eprintln!("   SYNC::CRYPTO::❌ {} Partial save failed: {}", symbol, e);
                                                failed_cryptos.push(symbol.clone());
                                            }
                                        }
                                    } else {
                                        eprintln!("   SYNC::CRYPTO::❌ {} No data in partial batch response", symbol);
                                        failed_cryptos.push(symbol.clone());
                                    }
                                }
                            }
                        }
                        Err(e) => {
                            eprintln!("SYNC::CRYPTO::❌ Partial chunk {}/{} failed: {}",
                                chunk_idx + 1, crypto_chunks.len(), e);
                            failed_cryptos.extend(chunk.iter().cloned());
                        }
                    }

                    // Delay between chunks
                    if chunk_idx < crypto_chunks.len() - 1 {
                        tokio::time::sleep(tokio::time::Duration::from_millis(500)).await;
                    }
                }
            } else {
                // CryptoCompare mode: Sequential fetching for partial history
                for (idx, (symbol, last_date)) in partial_list.iter().enumerate() {
                    let should_print = idx < show_first || idx >= total.saturating_sub(show_last);
                    if idx == show_first && total > show_first + show_last {
                        println!("   SYNC::CRYPTO::... ({} more partial cryptos) ...", total - show_first - show_last);
                    }

                    // For partial history, we can use fetch_recent (it will fetch from the gap date)
                    match self.fetch_recent(symbol, last_date, interval).await {
                        Ok(data) => {
                            match self.process_crypto(symbol, Some(data), interval, last_date).await {
                                Ok(_) => {
                                    success_count += 1;
                                    if should_print {
                                        println!("   SYNC::CRYPTO::[{}/{}] {} ✅ [partial]", idx + 1, total, symbol);
                                    }
                                }
                                Err(e) => {
                                    eprintln!("   SYNC::CRYPTO::[{}/{}] {} ❌ Partial save failed: {}", idx + 1, total, symbol, e);
                                    failed_cryptos.push(symbol.clone());
                                }
                            }
                        }
                        Err(e) => {
                            if matches!(e, Error::RateLimit) {
                                eprintln!("SYNC::CRYPTO::❌ Rate limit hit - aborting sync and waiting for next interval");
                                // Return rate limit error immediately to abort entire sync
                                return Err(Error::RateLimit);
                            }
                            eprintln!("   SYNC::CRYPTO::[{}/{}] {} ❌ Partial fetch failed: {}", idx + 1, total, symbol, e);
                            failed_cryptos.push(symbol.clone());
                        }
                    }

                    // Rate limiting
                    tokio::time::sleep(tokio::time::Duration::from_millis(delay_ms)).await;
                }
            }
        }

        // Process full history cryptos
        if !full_list.is_empty() {
            println!("\nSYNC::CRYPTO::📥 Processing {} full history cryptos...", full_list.len());
            let total = full_list.len();
            let start_date = self.get_start_date_for_interval(interval);

            if is_proxy_mode {
                // API Proxy mode: Use chunked batch API for full history cryptos
                let chunk_size = self.get_chunk_size_for_interval(interval);
                let crypto_chunks = self.split_cryptos_into_chunks(&full_list, chunk_size);

                println!("   SYNC::CRYPTO::🚀 Batch API mode: fetching {} cryptos in {} chunks of {} cryptos each",
                    full_list.len(), crypto_chunks.len(), chunk_size);

                // Process each chunk
                for (chunk_idx, chunk) in crypto_chunks.iter().enumerate() {
                    let chunk_start_time = Instant::now();
                    println!("   SYNC::CRYPTO::📦 Processing chunk {}/{} ({} cryptos)...",
                        chunk_idx + 1, crypto_chunks.len(), chunk.len());

                    match self.fetcher.fetch_batch(chunk, &start_date, interval).await {
                        Ok(batch_data) => {
                            // Process each crypto's data in this chunk
                            for symbol in chunk {
                                if let Some(data) = batch_data.get(symbol) {
                                    match self.process_crypto(symbol, Some(data.clone()), interval, "").await {
                                        Ok(_) => {
                                            success_count += 1;
                                            println!("   SYNC::CRYPTO::✅ {} ({} records)", symbol, data.len());
                                        }
                                        Err(e) => {
                                            eprintln!("   SYNC::CRYPTO::❌ {} Save failed: {}", symbol, e);
                                            failed_cryptos.push(symbol.clone());
                                        }
                                    }
                                } else {
                                    eprintln!("   SYNC::CRYPTO::❌ {} No data in batch response", symbol);
                                    failed_cryptos.push(symbol.clone());
                                }
                            }

                            let chunk_time = chunk_start_time.elapsed();
                            println!("   SYNC::CRYPTO::📦 Chunk {}/{} completed in {:.1}s",
                                chunk_idx + 1, crypto_chunks.len(), chunk_time.as_secs_f64());

                            // Small delay between chunks to prevent overwhelming server
                            if chunk_idx < crypto_chunks.len() - 1 {
                                tokio::time::sleep(tokio::time::Duration::from_millis(500)).await;
                            }
                        }
                        Err(e) => {
                            let chunk_time = chunk_start_time.elapsed();
                            if matches!(e, Error::RateLimit) {
                                eprintln!("SYNC::CRYPTO::❌ Rate limit hit in batch mode - aborting sync and waiting for next interval");
                                // Return rate limit error immediately to abort entire sync
                                return Err(Error::RateLimit);
                            }
                            eprintln!("SYNC::CRYPTO::❌ Chunk {}/{} failed after {:.1}s: {}",
                                chunk_idx + 1, crypto_chunks.len(), chunk_time.as_secs_f64(), e);
                            failed_cryptos.extend(chunk.iter().cloned());
                        }
                    }
                }
            } else {
                // CryptoCompare mode: Sequential fetching (one by one)
                for (idx, symbol) in full_list.iter().enumerate() {
                    let should_print = idx < show_first || idx >= total.saturating_sub(show_last);
                    if idx == show_first && total > show_first + show_last {
                        println!("   SYNC::CRYPTO::... ({} more cryptos) ...", total - show_first - show_last);
                    }

                    match self.fetch_full_history(symbol, &start_date, interval).await {
                        Ok(data) => {
                            match self.process_crypto(symbol, Some(data), interval, "").await {
                                Ok(_) => {
                                    success_count += 1;
                                    if should_print {
                                        println!("   SYNC::CRYPTO::[{}/{}] {} ✅", idx + 1, total, symbol);
                                    }
                                }
                                Err(e) => {
                                    eprintln!("   SYNC::CRYPTO::[{}/{}] {} ❌ Save failed: {}", idx + 1, total, symbol, e);
                                    failed_cryptos.push(symbol.clone());
                                }
                            }
                        }
                        Err(e) => {
                            if matches!(e, Error::RateLimit) {
                                eprintln!("SYNC::CRYPTO::❌ Rate limit hit - aborting sync and waiting for next interval");
                                // Return rate limit error immediately to abort entire sync
                                return Err(Error::RateLimit);
                            }
                            eprintln!("   SYNC::CRYPTO::[{}/{}] {} ❌ {}", idx + 1, total, symbol, e);
                            failed_cryptos.push(symbol.clone());
                        }
                    }

                    tokio::time::sleep(tokio::time::Duration::from_millis(delay_ms)).await;
                }
            }
        }

        let interval_time = interval_start_time.elapsed();

        // Print interval summary
        println!("\nSYNC::CRYPTO::📊 {} Summary:", interval.to_filename());
        println!("SYNC::CRYPTO::   ✅ Successful: {}/{}", success_count, symbols.len());
        if !failed_cryptos.is_empty() {
            println!("SYNC::CRYPTO::   ❌ Failed: {} ({:?})", failed_cryptos.len(), failed_cryptos);
        }
        println!("SYNC::CRYPTO::   ⏱️  Duration: {:.1}s", interval_time.as_secs_f64());

        Ok(())
    }

    /// Fetch full history for a crypto
    async fn fetch_full_history(
        &mut self,
        symbol: &str,
        start_date: &str,
        interval: Interval,
    ) -> Result<Vec<OhlcvData>, Error> {
        self.fetcher.fetch_full_history(symbol, start_date, interval).await
    }

    /// Fetch recent data for a crypto (resume mode)
    async fn fetch_recent(
        &mut self,
        symbol: &str,
        last_date: &str,
        interval: Interval,
    ) -> Result<Vec<OhlcvData>, Error> {
        self.fetcher.fetch_recent(symbol, last_date, interval).await
    }

    /// Process a single crypto (enhance and save)
    async fn process_crypto(
        &self,
        symbol: &str,
        data: Option<Vec<OhlcvData>>,
        interval: Interval,
        last_date: &str,
    ) -> Result<(), Error> {
        if let Some(ohlcv_data) = data {
            if ohlcv_data.is_empty() {
                return Ok(()); // Skip empty data
            }

            self.enhance_and_save_crypto_data(symbol, &ohlcv_data, interval, last_date).await?;
        }

        Ok(())
    }

    /// Enhance and save crypto data (mirrors stock sync optimization)
    async fn enhance_and_save_crypto_data(
        &self,
        symbol: &str,
        new_data: &[OhlcvData],
        interval: Interval,
        _last_date: &str,  // Parameter kept for API compatibility but not used
    ) -> Result<(), Error> {
        if new_data.is_empty() {
            return Ok(());
        }

        let crypto_data_dir = get_crypto_data_dir();
        let file_path = crypto_data_dir.join(symbol).join(interval.to_filename());
        
        // Merge existing CSV data with new data (like stock sync does)
        // IMPORTANT: Always merge when file exists, regardless of is_resume status
        // This preserves historical data for partial history cases
        let (existing, merged_data) = if file_path.exists() {
            let existing_data = self.read_existing_ohlcv(&file_path)?;
            let merged = self.merge_ohlcv_data(existing_data.clone(), new_data.to_vec());
            (Some(existing_data), merged)
        } else {
            (None, new_data.to_vec())
        };

        if merged_data.is_empty() {
            return Ok(());
        }

        // Calculate cutoff date - use different logic for gaps vs resume
        // If there's a large gap between existing and new data (partial history), preserve all existing data
        let cutoff_datetime = if let (Some(ref existing_data), Some(first_new)) = (existing.as_ref(), new_data.first()) {
            if let Some(last_existing) = existing_data.last() {
                let gap_days = (first_new.time - last_existing.time).num_days();
                if gap_days > 3 {
                    // Large gap detected (partial history): use epoch to preserve all existing data
                    chrono::DateTime::from_timestamp(0, 0).unwrap_or_else(|| Utc::now())
                } else {
                    // Small gap or no gap (resume): use 2-day cutoff to refresh recent data
                    let resume_days = 2i64;
                    last_existing.time - chrono::Duration::days(resume_days)
                }
            } else {
                chrono::DateTime::from_timestamp(0, 0).unwrap_or_else(|| Utc::now())
            }
        } else {
            chrono::DateTime::from_timestamp(0, 0).unwrap_or_else(|| Utc::now())
        };

        // Find cutoff index
        let cutoff_index = merged_data.iter()
            .position(|d| d.time >= cutoff_datetime)
            .unwrap_or(merged_data.len());

        // Optimization: Take 200 records before cutoff for MA200 context
        const MA_BUFFER: usize = 200;
        let start_index = cutoff_index.saturating_sub(MA_BUFFER);
        let sliced_data = &merged_data[start_index..];

        // Enhance the sliced data (with proper MA context)
        let mut data_map: HashMap<String, Vec<OhlcvData>> = HashMap::new();
        data_map.insert(symbol.to_string(), sliced_data.to_vec());
        let enhanced_data = enhance_data(data_map);

        let stock_data = enhanced_data.get(symbol)
            .ok_or_else(|| Error::Other("Failed to enhance data".to_string()))?;

        // Save with cutoff strategy and change detection
        let (change_type, record_count) = save_enhanced_csv_to_dir_with_changes(
            symbol,
            stock_data,
            interval,
            cutoff_datetime,
            false, // rewrite_all - always false like stock sync
            &crypto_data_dir,
            self.channel_sender.clone(),
        ).await?;

        tracing::info!(
            symbol = symbol,
            interval = ?interval,
            record_count = record_count,
            change_type = %change_type,
            "Enhanced and saved crypto data with change detection"
        );

        Ok(())
    }

    /// Read existing OHLCV data from CSV file
    fn read_existing_ohlcv(&self, file_path: &std::path::Path) -> Result<Vec<OhlcvData>, Error> {
        if !file_path.exists() {
            return Ok(Vec::new());
        }

        let mut reader = csv::ReaderBuilder::new()
            .flexible(true)
            .from_path(file_path)
            .map_err(|e| Error::Io(format!("Failed to read CSV: {}", e)))?;

        let mut data = Vec::new();
        for result in reader.records() {
            let record = match result {
                Ok(r) => r,
                Err(_) => continue,
            };

            if record.len() < 7 {
                continue;
            }

            let symbol = record.get(0).unwrap_or("").to_string();
            let time_str = record.get(1).unwrap_or("");
            let open: f64 = record.get(2).and_then(|s| s.parse().ok()).unwrap_or(0.0);
            let high: f64 = record.get(3).and_then(|s| s.parse().ok()).unwrap_or(0.0);
            let low: f64 = record.get(4).and_then(|s| s.parse().ok()).unwrap_or(0.0);
            let close: f64 = record.get(5).and_then(|s| s.parse().ok()).unwrap_or(0.0);
            let volume: u64 = record.get(6).and_then(|s| s.parse().ok()).unwrap_or(0);

            // Parse time
            let time = crate::utils::parse_timestamp(time_str)
                .unwrap_or_else(|_| Utc::now());

            data.push(OhlcvData {
                time,
                open,
                high,
                low,
                close,
                volume,
                symbol: Some(symbol),
            });
        }

        data.sort_by_key(|d| d.time);
        Ok(data)
    }

    /// Merge existing and new OHLCV data (like stock sync)
    fn merge_ohlcv_data(&self, existing: Vec<OhlcvData>, new: Vec<OhlcvData>) -> Vec<OhlcvData> {
        if existing.is_empty() {
            return new;
        }
        if new.is_empty() {
            return existing;
        }

        // Find the latest timestamp in existing data
        let latest_existing_time = existing.iter()
            .map(|d| d.time)
            .max()
            .unwrap_or_else(|| Utc::now());

        // Keep existing data before latest time
        let mut merged: Vec<OhlcvData> = existing
            .into_iter()
            .filter(|d| d.time < latest_existing_time)
            .collect();

        // Add new data >= latest time (includes update to last row)
        let new_rows: Vec<OhlcvData> = new
            .into_iter()
            .filter(|d| d.time >= latest_existing_time)
            .collect();

        merged.extend(new_rows);
        merged.sort_by(|a, b| a.time.cmp(&b.time));
        merged
    }

    /// Get start date for interval (accounts for data retention limits)
    fn get_start_date_for_interval(&self, interval: Interval) -> String {
        match interval {
            Interval::Daily | Interval::Hourly => {
                // Daily and hourly go back to BTC inception
                "2010-07-17".to_string()
            }
            Interval::Minute => {
                // CryptoCompare only keeps 7 days of minute data
                let seven_days_ago = chrono::Utc::now() - chrono::Duration::days(7);
                seven_days_ago.format("%Y-%m-%d").to_string()
            }
        }
    }

    /// Print final summary of sync operation
    fn print_final_summary(&self, total_time: std::time::Duration) {
        println!("\nSYNC::CRYPTO::{}", "=".repeat(70));
        println!("SYNC::CRYPTO::📊 CRYPTO SYNC SUMMARY");
        println!("SYNC::CRYPTO::{}", "=".repeat(70));
        println!("SYNC::CRYPTO::⏱️  Total time: {:.1}s", total_time.as_secs_f64());
        println!("SYNC::CRYPTO::{}", "=".repeat(70));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_get_start_date_for_interval() {
        let config = SyncConfig::default();
        let sync = CryptoSync::new(config, None).unwrap();

        assert_eq!(sync.get_start_date_for_interval(Interval::Daily), "2010-07-17");
        assert_eq!(sync.get_start_date_for_interval(Interval::Hourly), "2010-07-17");

        let minute_date = sync.get_start_date_for_interval(Interval::Minute);
        // Should be a recent date (within last 7 days)
        assert!(minute_date.starts_with("20")); // Starts with year 20XX
    }
}
