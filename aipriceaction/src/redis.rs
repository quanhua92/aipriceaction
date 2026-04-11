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

    // Wait for connection
    match client.wait_for_connect().await {
        Ok(()) => {
            tracing::info!("Connected to Redis");
            Some(client)
        }
        Err(e) => {
            tracing::error!("Failed to connect to Redis: {e}");
            None
        }
    }
}
