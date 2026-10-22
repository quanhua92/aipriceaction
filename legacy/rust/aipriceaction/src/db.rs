use sqlx::postgres::PgPoolOptions;
use sqlx::PgPool;

/// Connect to PostgreSQL, run pending migrations, return the pool.
pub async fn connect(database_url: &str) -> sqlx::Result<PgPool> {
    let pool = PgPoolOptions::new()
        .max_connections(10)
        .acquire_timeout(std::time::Duration::from_secs(3))
        .connect(database_url)
        .await?;

    sqlx::migrate!().run(&pool).await?;
    Ok(pool)
}

/// Simple health check — returns Ok(()) if the database is reachable.
pub async fn health_check(pool: &PgPool) -> sqlx::Result<()> {
    sqlx::query_scalar!("SELECT 1")
        .fetch_one(pool)
        .await?;
    Ok(())
}
