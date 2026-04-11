use fred::prelude::*;

pub type RedisClient = Client;

/// Connect to Redis if REDIS_URL is set. Returns None if not configured.
/// If initial connection times out (3s), returns Some(client) anyway — fred
/// reconnects in the background via the connection handle.
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

    // Keep the handle alive — it manages automatic reconnection in the background.
    // If we drop it, fred cannot reconnect when Redis comes back.
    let handle = client.connect();

    // Wait for connection with 3s timeout to avoid blocking startup
    match tokio::time::timeout(std::time::Duration::from_secs(3), client.wait_for_connect()).await
    {
        Ok(Ok(())) => {
            tracing::info!("Connected to Redis");
        }
        Ok(Err(e)) => {
            tracing::warn!("Redis initial connect failed: {e} (fred will retry in background)");
        }
        Err(_) => {
            tracing::warn!("Redis initial connect timed out after 3s (fred will retry in background)");
        }
    }

    // Spawn a lightweight health loop that logs reconnection events.
    // This doesn't modify the client — fred handles reconnection internally.
    let health_client = client.clone();
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(std::time::Duration::from_secs(60)).await;
            if health_client.is_connected() {
                tracing::debug!("Redis health: connected");
            } else {
                tracing::warn!("Redis health: disconnected (fred reconnecting...)");
            }
        }
    });

    // Intentionally leak the handle so the reconnection task stays alive.
    // It's a small, lightweight struct (~few bytes) that manages a background task.
    std::mem::forget(handle);

    Some(client)
}
