use fred::prelude::*;

pub type RedisClient = Client;

/// Connect to Redis if REDIS_URL is set. Returns None if not configured.
pub async fn connect() -> Option<RedisClient> {
    let redis_url = match std::env::var("REDIS_URL") {
        Ok(url) if !url.is_empty() => url,
        _ => {
            tracing::info!("REDIS_URL not set, Redis ZSET cache disabled");
            return None;
        }
    };

    tracing::info!("Connecting to Redis at {redis_url}...");

    let config = match Config::from_url(&redis_url) {
        Ok(c) => c,
        Err(e) => {
            tracing::error!("Invalid REDIS_URL: {e}");
            return None;
        }
    };
    let client = RedisClient::new(config, None, None, None);

    let _handle = client.connect();

    // Wait for connection with 3s timeout to avoid blocking startup
    match tokio::time::timeout(std::time::Duration::from_secs(3), client.wait_for_connect()).await
    {
        Ok(Ok(())) => {
            tracing::info!("Connected to Redis");
            Some(client)
        }
        Ok(Err(e)) => {
            tracing::error!("Failed to connect to Redis: {e}");
            None
        }
        Err(_) => {
            tracing::error!("Redis connection timed out after 3s (will reconnect in background)");
            None
        }
    }
}
