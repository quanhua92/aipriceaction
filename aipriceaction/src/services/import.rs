use std::path::Path;

use sqlx::PgPool;
use tracing::{info, warn};

use crate::csv::legacy;
use crate::models::interval::Interval;
use crate::models::ohlcv::{IndicatorRow, OhlcvRow};
use crate::queries;
use crate::services::ohlcv;

const BATCH_SIZE: usize = 500;

#[derive(Debug, Default)]
pub struct ImportStats {
    pub files_processed: usize,
    pub total_rows: usize,
    pub total_batches: usize,
    pub errors: Vec<String>,
}

/// Import CSV files from a market_data directory into PostgreSQL.
///
/// - `market_data_dir`: root of the market_data tree (e.g. `/path/to/market_data`)
/// - `source`: data source label (e.g. "vn")
/// - `ticker_filter`: if Some, only process that ticker directory
/// - `interval_filter`: if Some, only process that interval file (e.g. "1D")
pub async fn import_csv(
    pool: &PgPool,
    market_data_dir: &Path,
    source: &str,
    ticker_filter: Option<&str>,
    interval_filter: Option<&Interval>,
) -> ImportStats {
    let mut stats = ImportStats::default();

    // Determine which ticker directories to process
    let ticker_dirs: Vec<std::path::PathBuf> = match std::fs::read_dir(market_data_dir) {
        Ok(entries) => entries
            .filter_map(|e| e.ok())
            .filter(|e| e.file_type().map(|t| t.is_dir()).unwrap_or(false))
            .map(|e| e.path())
            .filter(|p| {
                if let Some(filter) = ticker_filter {
                    p.file_name()
                        .and_then(|n| n.to_str())
                        .map(|n| n == filter)
                        .unwrap_or(false)
                } else {
                    true
                }
            })
            .collect(),
        Err(e) => {
            stats
                .errors
                .push(format!("cannot read directory {}: {e}", market_data_dir.display()));
            return stats;
        }
    };

    let csv_stems = ["1D.csv", "1H.csv", "1m.csv"];

    for ticker_dir in &ticker_dirs {
        let ticker_name = match ticker_dir.file_name().and_then(|n| n.to_str()) {
            Some(name) => name,
            None => continue,
        };

        for csv_file in &csv_stems {
            let path = ticker_dir.join(csv_file);

            // Apply interval filter
            if let Some(filter) = interval_filter {
                let stem = match Interval::from_filename(&path) {
                    Ok(iv) => iv,
                    Err(_) => continue,
                };
                if stem != *filter {
                    continue;
                }
            }

            if !path.exists() {
                continue;
            }

            match import_single_csv(pool, source, &path).await {
                Ok((rows, batches)) => {
                    stats.files_processed += 1;
                    stats.total_rows += rows;
                    stats.total_batches += batches;
                    info!(
                        "  imported {} ({}, {} rows, {} batches)",
                        ticker_name,
                        csv_file,
                        rows,
                        batches
                    );
                }
                Err(e) => {
                    stats.errors.push(format!("{} {}: {e}", ticker_name, csv_file));
                    warn!("  error importing {} {}: {e}", ticker_name, csv_file);
                }
            }
        }
    }

    stats
}

async fn import_single_csv(
    pool: &PgPool,
    source: &str,
    path: &Path,
) -> Result<(usize, usize), String> {
    let parsed = legacy::parse_csv(path)?;

    if parsed.rows.is_empty() {
        return Ok((0, 0));
    }

    let ticker_id = ohlcv::ensure_ticker(pool, source, &parsed.ticker)
        .await
        .map_err(|e| format!("ensure_ticker failed for {}: {e}", parsed.ticker))?;

    // Set ticker_id on all rows
    let ohlcv_rows: Vec<OhlcvRow> = parsed
        .rows
        .into_iter()
        .map(|mut r| {
            r.ticker_id = ticker_id;
            r
        })
        .collect();

    let indicator_rows: Vec<IndicatorRow> = parsed
        .indicators
        .into_iter()
        .map(|mut r| {
            r.ticker_id = ticker_id;
            r
        })
        .collect();

    // Batch upsert
    let total_rows = ohlcv_rows.len();
    let mut batches = 0;

    for chunk in ohlcv_rows.chunks(BATCH_SIZE) {
        queries::import::bulk_upsert_ohlcv(pool, chunk)
            .await
            .map_err(|e| format!("bulk_upsert_ohlcv failed: {e}"))?;
        batches += 1;
    }

    for chunk in indicator_rows.chunks(BATCH_SIZE) {
        queries::import::bulk_upsert_indicators(pool, chunk)
            .await
            .map_err(|e| format!("bulk_upsert_indicators failed: {e}"))?;
        batches += 1;
    }

    Ok((total_rows, batches))
}
