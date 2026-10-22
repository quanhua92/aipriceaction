use std::collections::HashMap;
use std::fs::File;
use std::io::BufReader;
use std::path::Path;

use chrono::Utc;
use flate2::read::GzDecoder;
use flate2::write::GzEncoder;
use flate2::Compression;
use sqlx::PgPool;

use crate::models::checkpoint::*;
use crate::models::ohlcv::OhlcvRow;
use crate::queries::import::bulk_upsert_ohlcv;
use crate::queries::ohlcv::{get_ohlcv_batch_raw, upsert_ticker};
use crate::services::ohlcv::list_all_tickers as svc_list_all_tickers;

/// Create a checkpoint file from the current database state.
///
/// Fetches the last `candles` rows per ticker for each base interval (1D, 1h, 1m)
/// across all sources and writes a gzip-compressed JSON file.
pub async fn create_checkpoint(
    pool: &PgPool,
    candles: u32,
    output_path: &Path,
) -> Result<(), CheckpointError> {
    tracing::info!("Creating checkpoint: candles={}, output={}", candles, output_path.display());

    let tickers = svc_list_all_tickers(pool)
        .await
        .map_err(CheckpointError::Db)?;

    if tickers.is_empty() {
        tracing::warn!("No tickers found in database, checkpoint will be empty");
    }

    // Group tickers by source
    let mut by_source: HashMap<String, Vec<_>> = HashMap::new();
    for t in &tickers {
        by_source
            .entry(t.source.clone())
            .or_default()
            .push((t.ticker.clone(), t.name.clone()));
    }

    let intervals = ["1D", "1h", "1m"];
    let mut total_rows: u64 = 0;
    let mut total_tickers: u32 = 0;
    let mut sources_out = Vec::new();

    for (source, entries) in &by_source {
        let mut ticker_checkpoints = Vec::new();

        for (ticker, name) in entries {
            let mut data = std::collections::BTreeMap::new();

            for interval in &intervals {
                let mut map = get_ohlcv_batch_raw(
                    pool,
                    source,
                    &[ticker.clone()],
                    interval,
                    Some(candles as i64),
                    None,
                    None,
                )
                .await
                .map_err(CheckpointError::Db)?;

                let rows = map.remove(ticker).unwrap_or_default();
                total_rows += rows.len() as u64;

                let ohlcv_entries: Vec<OhlcvEntry> = rows
                    .into_iter()
                    .map(|r| OhlcvEntry {
                        time: r.time,
                        open: r.open,
                        high: r.high,
                        low: r.low,
                        close: r.close,
                        volume: r.volume,
                    })
                    .collect();

                if !ohlcv_entries.is_empty() {
                    data.insert(interval.to_string(), ohlcv_entries);
                }
            }

            if !data.is_empty() {
                ticker_checkpoints.push(TickerCheckpoint {
                    ticker: ticker.clone(),
                    name: name.clone(),
                    data,
                });
                total_tickers += 1;
            }
        }

        ticker_checkpoints.sort_by(|a, b| a.ticker.cmp(&b.ticker));

        sources_out.push(SourceCheckpoint {
            source: source.clone(),
            tickers: ticker_checkpoints,
        });
    }

    sources_out.sort_by(|a, b| a.source.cmp(&b.source));

    let checkpoint = Checkpoint {
        meta: CheckpointMeta {
            created_at: Utc::now(),
            candles,
            total_tickers,
            total_rows,
        },
        sources: sources_out,
    };

    // Write gzip JSON — streams to disk without full string in memory
    let file = File::create(output_path).map_err(CheckpointError::Io)?;
    let encoder = GzEncoder::new(file, Compression::default());
    serde_json::to_writer(encoder, &checkpoint).map_err(CheckpointError::Json)?;

    tracing::info!(
        "Checkpoint written: {} tickers, {} rows, size={}",
        checkpoint.meta.total_tickers,
        checkpoint.meta.total_rows,
        output_path.display(),
    );

    Ok(())
}

/// Import a checkpoint file into the database.
///
/// Reads the gzip-compressed JSON checkpoint, resolves ticker IDs via
/// `source + ticker`, and bulk-upserts OHLCV rows in 10k batches.
pub async fn import_checkpoint(
    pool: &PgPool,
    checkpoint_path: &Path,
) -> Result<(), CheckpointError> {
    tracing::info!(
        "Importing checkpoint from {}",
        checkpoint_path.display()
    );

    let file = File::open(checkpoint_path).map_err(CheckpointError::Io)?;
    let decoder = GzDecoder::new(BufReader::new(file));
    let checkpoint: Checkpoint =
        serde_json::from_reader(decoder).map_err(CheckpointError::Json)?;

    tracing::info!(
        "Checkpoint loaded: created_at={}, candles={}, {} tickers, {} rows",
        checkpoint.meta.created_at,
        checkpoint.meta.candles,
        checkpoint.meta.total_tickers,
        checkpoint.meta.total_rows,
    );

    let mut total_imported: u64 = 0;

    for source_cp in &checkpoint.sources {
        for ticker_cp in &source_cp.tickers {
            // Resolve ticker ID (insert if missing)
            let ticker_id = upsert_ticker(
                pool,
                &source_cp.source,
                &ticker_cp.ticker,
                ticker_cp.name.as_deref(),
            )
            .await
            .map_err(CheckpointError::Db)?;

            // Flatten all intervals into a single Vec<OhlcvRow>
            let mut rows: Vec<OhlcvRow> = Vec::new();
            for (interval, entries) in &ticker_cp.data {
                for entry in entries {
                    rows.push(OhlcvRow {
                        ticker_id,
                        interval: interval.to_string(),
                        time: entry.time,
                        open: entry.open,
                        high: entry.high,
                        low: entry.low,
                        close: entry.close,
                        volume: entry.volume,
                    });
                }
            }

            // Upsert in 10k batches
            for chunk in rows.chunks(10_000) {
                bulk_upsert_ohlcv(pool, chunk)
                    .await
                    .map_err(CheckpointError::Db)?;
            }

            total_imported += rows.len() as u64;
        }
    }

    tracing::info!(
        "Checkpoint import complete: {} total rows imported",
        total_imported
    );

    Ok(())
}

/// Check whether the database already has data by querying VNINDEX daily rows.
pub async fn has_existing_data(pool: &PgPool) -> Result<bool, CheckpointError> {
    let result: (i64,) = sqlx::query_as(
        "SELECT COUNT(*) FROM ohlcv WHERE ticker_id = (SELECT id FROM tickers WHERE source='vn' AND ticker='VNINDEX')",
    )
    .fetch_one(pool)
    .await
    .map_err(CheckpointError::Db)?;

    Ok(result.0 > 0)
}

#[derive(Debug)]
pub enum CheckpointError {
    Db(sqlx::Error),
    Io(std::io::Error),
    Json(serde_json::Error),
}

impl std::fmt::Display for CheckpointError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CheckpointError::Db(e) => write!(f, "Database error: {e}"),
            CheckpointError::Io(e) => write!(f, "IO error: {e}"),
            CheckpointError::Json(e) => write!(f, "JSON error: {e}"),
        }
    }
}

impl std::error::Error for CheckpointError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            CheckpointError::Db(e) => Some(e),
            CheckpointError::Io(e) => Some(e),
            CheckpointError::Json(e) => Some(e),
        }
    }
}

impl From<sqlx::Error> for CheckpointError {
    fn from(e: sqlx::Error) -> Self {
        CheckpointError::Db(e)
    }
}

impl From<std::io::Error> for CheckpointError {
    fn from(e: std::io::Error) -> Self {
        CheckpointError::Io(e)
    }
}

impl From<serde_json::Error> for CheckpointError {
    fn from(e: serde_json::Error) -> Self {
        CheckpointError::Json(e)
    }
}
