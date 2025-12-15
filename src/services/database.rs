use sqlx::{sqlite::SqliteConnectOptions, Row, SqlitePool};
use std::path::PathBuf;
use std::time::Duration;
use chrono::{DateTime, Utc};
use crate::models::{StockData, Interval};
use crate::services::data_store::QueryParameters;
use crate::error::AppError;
use std::collections::HashMap;
use tracing::info;

/// SQLite database for market data storage
#[derive(Debug)]
pub struct SQLiteDatabaseStore {
    pool: SqlitePool,
    database_path: PathBuf,
}

/// Database schema version for migrations
const DB_SCHEMA_VERSION: &str = "1";

impl SQLiteDatabaseStore {
    /// Create new SQLite database with optimized settings
    pub async fn new(database_path: PathBuf) -> Result<Self, sqlx::Error> {
        info!("Initializing SQLite database at: {:?}", database_path);

        // Ensure parent directory exists
        if let Some(parent) = database_path.parent() {
            tokio::fs::create_dir_all(parent).await
                .map_err(|e| sqlx::Error::Io(e))?;
        }

        // Configure SQLite for optimal performance
        let connect_options = SqliteConnectOptions::new()
            .filename(&database_path)
            .create_if_missing(true)
            .journal_mode(sqlx::sqlite::SqliteJournalMode::Wal)  // Enable concurrent reads/writes
            .synchronous(sqlx::sqlite::SqliteSynchronous::Normal)  // Balanced durability
            .page_size(4096)  // Standard page size
            .busy_timeout(Duration::from_secs(30))  // Wait 30s for locked DB
            .foreign_keys(true)  // Enable foreign key constraints
            .auto_vacuum(sqlx::sqlite::SqliteAutoVacuum::Incremental);  // Automatic cleanup

        // Create connection pool
        let pool = SqlitePool::connect_with(connect_options).await?;

        // Run migrations and optimizations
        let db_store = Self { pool, database_path };
        db_store.initialize_database().await?;

        info!("SQLite database initialized successfully");
        Ok(db_store)
    }

    /// Initialize database schema and run migrations
    async fn initialize_database(&self) -> Result<(), sqlx::Error> {
        // Create market_data table
        let create_table_query = r#"
            CREATE TABLE IF NOT EXISTS market_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                interval TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL,
                ma10 REAL,
                ma20 REAL,
                ma50 REAL,
                ma100 REAL,
                ma200 REAL,
                ma10_score REAL,
                ma20_score REAL,
                ma50_score REAL,
                ma100_score REAL,
                ma200_score REAL,
                close_changed REAL,
                volume_changed REAL,
                total_money_changed REAL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        "#;

        sqlx::query(create_table_query)
            .execute(&self.pool)
            .await?;

        // Create critical indexes for performance
        let indexes = vec![
            // Unique constraint to prevent duplicates
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_market_data_unique ON market_data(ticker, interval, timestamp)",

            // Primary query pattern: ticker + interval + time
            "CREATE INDEX IF NOT EXISTS idx_market_data_ticker_interval_time ON market_data(ticker, interval, timestamp DESC)",

            // Time-based queries
            "CREATE INDEX IF NOT EXISTS idx_market_data_time ON market_data(timestamp DESC)",

            // Interval-based queries
            "CREATE INDEX IF NOT EXISTS idx_market_data_interval_time ON market_data(interval, timestamp DESC)",

            // Covering index for recent data queries (most common)
            "CREATE INDEX IF NOT EXISTS idx_market_data_recent ON market_data(ticker, interval, timestamp DESC, close, volume)",
        ];

        for index in indexes {
            sqlx::query(index).execute(&self.pool).await?;
        }

        // Create metadata table for version tracking
        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            "#
        ).execute(&self.pool).await?;

        // Store schema version
        sqlx::query(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('schema_version', ?1)"
        )
        .bind(DB_SCHEMA_VERSION)
        .execute(&self.pool)
        .await?;

        info!("Database schema initialized successfully");
        Ok(())
    }

    /// Apply performance optimizations to SQLite
    async fn apply_performance_optimizations(&self) -> Result<(), sqlx::Error> {
        let optimizations = vec![
            // Enable memory mapping for large datasets (256GB)
            "PRAGMA mmap_size = 268435456",

            // Optimize for SSD storage
            "PRAGMA journal_size_limit = 67108864",  // 64MB journal limit

            // Enable query planner optimizations
            "PRAGMA optimize",

            // Set temp store to memory for better performance
            "PRAGMA temp_store = 2",
        ];

        for pragma in optimizations {
            sqlx::query(pragma).execute(&self.pool).await?;
        }

        info!("Database performance optimizations applied");
        Ok(())
    }

    /// Get market data using smart query parameters (compatible with DataStore interface)
    pub async fn get_data_smart(&self, params: QueryParameters) -> Result<HashMap<String, Vec<StockData>>, AppError> {
        let mut results = HashMap::new();

        // Build query based on parameters
        let (query_sql, query_params) = self.build_smart_query(&params)?;

        // Execute query
        let rows = sqlx::query(&query_sql)
            .bind(query_params.ticker_pattern)
            .bind(params.interval.to_string())
            .bind(query_params.limit.unwrap_or(i64::MAX))
            .fetch_all(&self.pool)
            .await
            .map_err(|e| AppError::Database(e.to_string()))?;

        // Convert rows to StockData and group by ticker
        for row in rows {
            let stock_data = self.row_to_stock_data(row)?;
            results
                .entry(stock_data.ticker.clone())
                .or_insert_with(Vec::new)
                .push(stock_data);
        }

        Ok(results)
    }

    /// Insert or update market data records
    pub async fn upsert_market_data(&self, data: &[StockData]) -> Result<usize, sqlx::Error> {
        if data.is_empty() {
            return Ok(0);
        }

        let mut transaction = self.pool.begin().await?;

        // Use prepared statement for bulk insert
        let mut affected_rows = 0;

        for stock_data in data {
            let result = sqlx::query(
                r#"
                INSERT OR REPLACE INTO market_data
                (ticker, interval, timestamp, open, high, low, close, volume,
                 ma10, ma20, ma50, ma100, ma200,
                 ma10_score, ma20_score, ma50_score, ma100_score, ma200_score,
                 close_changed, volume_changed, total_money_changed)
                VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8,
                        ?9, ?10, ?11, ?12, ?13,
                        ?14, ?15, ?16, ?17, ?18,
                        ?19, ?20, ?21)
                "#
            )
            .bind(&stock_data.ticker)
            .bind("1D") // TODO: Get from interval field
            .bind(&stock_data.time)
            .bind(stock_data.open)
            .bind(stock_data.high)
            .bind(stock_data.low)
            .bind(stock_data.close)
            .bind(stock_data.volume as i64)
            .bind(stock_data.ma10)
            .bind(stock_data.ma20)
            .bind(stock_data.ma50)
            .bind(stock_data.ma100)
            .bind(stock_data.ma200)
            .bind(stock_data.ma10_score)
            .bind(stock_data.ma20_score)
            .bind(stock_data.ma50_score)
            .bind(stock_data.ma100_score)
            .bind(stock_data.ma200_score)
            .bind(stock_data.close_changed)
            .bind(stock_data.volume_changed)
            .bind(stock_data.total_money_changed)
            .execute(&mut *transaction)
            .await?;

            affected_rows += result.rows_affected() as usize;
        }

        transaction.commit().await?;
        Ok(affected_rows)
    }

    /// Get count of records in database
    pub async fn get_record_count(&self) -> Result<i64, sqlx::Error> {
        let result = sqlx::query_scalar("SELECT COUNT(*) FROM market_data")
            .fetch_one(&self.pool)
            .await?;
        Ok(result)
    }

    /// Check if database has recent data after the specified date
    pub async fn has_recent_data(&self, since: DateTime<Utc>) -> Result<bool, sqlx::Error> {
        let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM market_data WHERE timestamp > ?")
            .bind(since.naive_utc())
            .fetch_one(&self.pool)
            .await?;

        Ok(count > 0)
    }

    /// Check if database has data for a specific ticker
    pub async fn has_ticker_data(&self, ticker: &str) -> Result<bool, sqlx::Error> {
        let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM market_data WHERE ticker = ?")
            .bind(ticker)
            .fetch_one(&self.pool)
            .await?;

        Ok(count > 0)
    }

    /// Get count of records by ticker and interval
    pub async fn get_record_count_by_ticker_interval(&self, ticker: &str, interval: Interval) -> Result<i64, sqlx::Error> {
        let result = sqlx::query_scalar(
            "SELECT COUNT(*) FROM market_data WHERE ticker = ?1 AND interval = ?2"
        )
        .bind(ticker)
        .bind(interval.to_string())
        .fetch_one(&self.pool)
        .await?;
        Ok(result)
    }

    /// Delete records for specific ticker and interval (for updates)
    pub async fn delete_ticker_interval(&self, ticker: &str, interval: Interval) -> Result<i64, sqlx::Error> {
        let result = sqlx::query(
            "DELETE FROM market_data WHERE ticker = ?1 AND interval = ?2"
        )
        .bind(ticker)
        .bind(interval.to_string())
        .execute(&self.pool)
        .await?;
        Ok(result.rows_affected() as i64)
    }

    /// Close the database connection pool
    pub async fn close(&self) {
        self.pool.close().await;
        info!("SQLite database connection pool closed");
    }

    // Private helper methods

    /// Build smart query based on parameters
    fn build_smart_query(&self, params: &QueryParameters) -> Result<(String, QueryParams), AppError> {
        let mut query = String::from(
            r#"
            SELECT ticker, interval, timestamp, open, high, low, close, volume,
                   ma10, ma20, ma50, ma100, ma200,
                   ma10_score, ma20_score, ma50_score, ma100_score, ma200_score,
                   close_changed, volume_changed, total_money_changed
            FROM market_data
            WHERE ticker IN (
        "#
        );

        // Build IN clause for tickers
        for (i, _) in params.tickers.iter().enumerate() {
            if i > 0 { query.push_str(", "); }
            query.push('?');  // We'll bind these individually
        }

        query.push_str(") AND interval = ?");

        // Add date filters if present
        if let Some(start_date) = params.start_date {
            query.push_str(&format!(" AND timestamp >= '{}'", start_date.format("%Y-%m-%d %H:%M:%S")));
        }

        if let Some(end_date) = params.end_date {
            query.push_str(&format!(" AND timestamp <= '{}'", end_date.format("%Y-%m-%d %H:%M:%S")));
        }

        // Order by timestamp DESC for recent data first
        query.push_str(" ORDER BY timestamp DESC");

        // Add limit if specified
        query.push_str(&format!(" LIMIT {}", params.limit));

        // For now, we'll use a simpler approach and handle multiple tickers in separate queries
        let simplified_query = format!(
            r#"
            SELECT ticker, interval, timestamp, open, high, low, close, volume,
                   ma10, ma20, ma50, ma100, ma200,
                   ma10_score, ma20_score, ma50_score, ma100_score, ma200_score,
                   close_changed, volume_changed, total_money_changed
            FROM market_data
            WHERE ticker = ?1 AND interval = ?2
            ORDER BY timestamp DESC
            LIMIT ?3
            "#
        );

        Ok((
            simplified_query,
            QueryParams {
                ticker_pattern: params.tickers.first().unwrap_or(&"UNKNOWN".to_string()).clone(),
                limit: Some(params.limit as i64),
            }
        ))
    }

    /// Convert SQL row to StockData
    fn row_to_stock_data(&self, row: sqlx::sqlite::SqliteRow) -> Result<StockData, AppError> {
        let stock_data = StockData {
            ticker: row.try_get("ticker").map_err(|e| AppError::Database(e.to_string()))?,
            time: row.try_get("timestamp").map_err(|e| AppError::Database(e.to_string()))?,
            open: row.try_get("open").map_err(|e| AppError::Database(e.to_string()))?,
            high: row.try_get("high").map_err(|e| AppError::Database(e.to_string()))?,
            low: row.try_get("low").map_err(|e| AppError::Database(e.to_string()))?,
            close: row.try_get("close").map_err(|e| AppError::Database(e.to_string()))?,
            volume: row.try_get::<i64, _>("volume").map_err(|e| AppError::Database(e.to_string()))? as u64,
            ma10: row.try_get("ma10").ok(),
            ma20: row.try_get("ma20").ok(),
            ma50: row.try_get("ma50").ok(),
            ma100: row.try_get("ma100").ok(),
            ma200: row.try_get("ma200").ok(),
            ma10_score: row.try_get("ma10_score").ok(),
            ma20_score: row.try_get("ma20_score").ok(),
            ma50_score: row.try_get("ma50_score").ok(),
            ma100_score: row.try_get("ma100_score").ok(),
            ma200_score: row.try_get("ma200_score").ok(),
            close_changed: row.try_get("close_changed").ok(),
            volume_changed: row.try_get("volume_changed").ok(),
            total_money_changed: row.try_get("total_money_changed").ok(),
        };

        Ok(stock_data)
    }
}

/// Helper struct for query parameters
struct QueryParams {
    ticker_pattern: String,
    limit: Option<i64>,
}

/// Check if database exists
pub async fn database_exists(database_path: &PathBuf) -> bool {
    database_path.exists() && database_path.is_file()
}

/// Get database statistics
pub async fn get_database_stats(pool: &SqlitePool) -> Result<DatabaseStats, sqlx::Error> {
    let total_records: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM market_data")
        .fetch_one(pool)
        .await?;

    let unique_tickers: i64 = sqlx::query_scalar("SELECT COUNT(DISTINCT ticker) FROM market_data")
        .fetch_one(pool)
        .await?;

    let date_range: Option<(String, String)> = sqlx::query(
        "SELECT MIN(timestamp), MAX(timestamp) FROM market_data"
    )
        .fetch_one(pool)
        .await
        .ok()
        .and_then(|row| {
            let min: Option<String> = row.try_get(0).ok();
            let max: Option<String> = row.try_get(1).ok();
            match (min, max) {
                (Some(min), Some(max)) => Some((min, max)),
                _ => None,
            }
        });

    Ok(DatabaseStats {
        total_records,
        unique_tickers,
        date_range,
    })
}

/// Database statistics
#[derive(Debug)]
pub struct DatabaseStats {
    pub total_records: i64,
    pub unique_tickers: i64,
    pub date_range: Option<(String, String)>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;
    use crate::models::Interval;

    #[tokio::test]
    async fn test_database_creation() {
        let temp_dir = tempdir().unwrap();
        let db_path = temp_dir.path().join("test.db");

        let db = SQLiteDatabaseStore::new(db_path).await.unwrap();
        assert!(database_exists(&db.database_path).await);
        db.close().await;
    }

    #[tokio::test]
    async fn test_basic_crud_operations() {
        let temp_dir = tempdir().unwrap();
        let db_path = temp_dir.path().join("test.db");

        let db = SQLiteDatabaseStore::new(db_path).await.unwrap();

        // Insert test data
        let test_data = vec![
            StockData {
                ticker: "TEST".to_string(),
                time: Utc::now(),
                open: 100.0,
                high: 105.0,
                low: 95.0,
                close: 102.0,
                volume: 1000000,
                ma10: Some(101.0),
                ma20: Some(100.5),
                ma50: None,
                ma100: None,
                ma200: None,
                ma10_score: Some(1.0),
                ma20_score: Some(1.5),
                ma50_score: None,
                ma100_score: None,
                ma200_score: None,
                close_changed: Some(2.0),
                volume_changed: Some(5.0),
                total_money_changed: Some(2000000.0),
            }
        ];

        let affected = db.upsert_market_data(&test_data).await.unwrap();
        assert_eq!(affected, 1);

        // Verify count
        let count = db.get_record_count().await.unwrap();
        assert_eq!(count, 1);

        db.close().await;
    }
}