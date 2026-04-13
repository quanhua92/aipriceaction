use fred::prelude::*;
use std::sync::atomic::{AtomicUsize, Ordering};

static PASS_COUNT: AtomicUsize = AtomicUsize::new(0);
static FAIL_COUNT: AtomicUsize = AtomicUsize::new(0);

fn log_result(test_name: &str, passed: bool, detail: &str) {
    if passed {
        PASS_COUNT.fetch_add(1, Ordering::Relaxed);
        tracing::info!("  [PASS] {test_name} — {detail}");
    } else {
        FAIL_COUNT.fetch_add(1, Ordering::Relaxed);
        tracing::error!("  [FAIL] {test_name} — {detail}");
    }
}

pub fn run(ticker: String) {
    let rt = tokio::runtime::Runtime::new().expect("Failed to create Tokio runtime");
    rt.block_on(async {
        tracing::info!("=== Redis ZSET Test Suite ===");
        tracing::info!("Ticker: {ticker}");
        tracing::info!("{}", "-".repeat(60));

        // 1. Read REDIS_URL
        let redis_url = match std::env::var("REDIS_URL") {
            Ok(url) if !url.is_empty() => url,
            _ => {
                tracing::error!("REDIS_URL not set. Set it to test Redis ZSET.");
                tracing::error!("Example: REDIS_URL=redis://default:helloaipriceaction@localhost:6379/0");
                return;
            }
        };
        log_result("REDIS_URL", true, &format!("configured ({redis_url})"));

        // 2. Connect
        tracing::info!("{}", "-".repeat(60));
        let client: crate::redis::RedisClient = match crate::redis::connect().await {
            Some((c, _handle)) => c,
            None => {
                log_result("connect", false, "failed to connect");
                return;
            }
        };
        log_result("connect", true, "connected successfully");

        // 2b. Live data query — 1 year of real backfilled data (before any test data)
        tracing::info!("{}", "-".repeat(60));
        tracing::info!("=== Live Data Query: {ticker} (1 year, all intervals) ===");

        for &interval in &["1D", "1h", "1m"] {
            let key = crate::workers::redis_worker::zset_key("vn", &ticker, interval);
            match client.zcard::<i64, _>(&key).await {
                Ok(count) => {
                    log_result(
                        &format!("live zcard ({ticker} {interval})"),
                        count >= 0,
                        &format!("key={key}, count={count}"),
                    );
                }
                Err(e) => log_result(
                    &format!("live zcard ({ticker} {interval})"),
                    false,
                    &format!("{e}"),
                ),
            }
        }

        let source = "test";
        let interval = "1D";
        let key = crate::workers::redis_worker::zset_key(source, &ticker, interval);
        let _max_size = crate::constants::redis_ts::daily_max_size();

        // Cleanup first (in case of previous failed test)
        let _: Result<Value, Error> = client.del(&key).await;

        // 3. ZADD — add 10 synthetic OHLCV bars
        tracing::info!("{}", "-".repeat(60));
        let now_ts = chrono::Utc::now().timestamp_millis();
        let bar_count = 10usize;
        let mut values: Vec<(f64, String)> = Vec::with_capacity(bar_count);

        for i in 0..bar_count {
            let ts = now_ts - (bar_count as i64 - i as i64) * 24 * 60 * 60 * 1000;
            let open = 1000.0 + i as f64 * 10.0;
            let high = open + 10.0;
            let low = open - 5.0;
            let close = open + 5.0;
            let volume = 100000 + i as i64 * 1000;
            let member = format!("{ts}|{open}|{high}|{low}|{close}|{volume}");
            values.push((ts as f64, member));
        }

        match client
            .zadd::<Value, _, _>(&key, None, None, false, false, values)
            .await
        {
            Ok(_) => log_result(
                "zadd (10 bars)",
                true,
                &format!("key={key}, bars={bar_count}"),
            ),
            Err(e) => log_result("zadd (10 bars)", false, &format!("{e}")),
        }

        // 4. ZCARD — check count
        tracing::info!("{}", "-".repeat(60));
        match client.zcard::<i64, _>(&key).await {
            Ok(count) => log_result(
                "zcard",
                count == bar_count as i64,
                &format!("expected={bar_count}, actual={count}"),
            ),
            Err(e) => log_result("zcard", false, &format!("{e}")),
        }

        // 5. ZREVRANGE — read back all bars
        tracing::info!("{}", "-".repeat(60));
        match client
            .zrevrange::<Vec<Value>, _>(&key, 0, -1, false)
            .await
        {
            Ok(members) => {
                let count = members.len();
                log_result(
                    "zrevrange (all)",
                    count == bar_count,
                    &format!("returned {count} members"),
                );
                // Parse first member and verify fields
                if let Some(first) = members.last() {
                    let member_str = match first {
                        Value::Bytes(b) => std::str::from_utf8(b).ok(),
                        Value::String(s) => std::str::from_utf8(s.as_bytes()).ok(),
                        _ => None,
                    };
                    if let Some(member_str) = member_str {
                        tracing::info!("    First (oldest): {member_str}");
                        if let Some((row, _)) =
                            crate::workers::redis_worker::parse_member(member_str, "1D")
                        {
                            log_result(
                                "parse_member (first)",
                                true,
                                &format!(
                                    "ts={}, open={}, close={}, volume={}",
                                    row.time.timestamp_millis(),
                                    row.open,
                                    row.close,
                                    row.volume
                                ),
                            );
                        } else {
                            log_result("parse_member (first)", false, "parse failed");
                        }
                    }
                }
                // Parse last member
                if let Some(last) = members.first() {
                    let member_str = match last {
                        Value::Bytes(b) => std::str::from_utf8(b).ok(),
                        Value::String(s) => std::str::from_utf8(s.as_bytes()).ok(),
                        _ => None,
                    };
                    if let Some(member_str) = member_str {
                        tracing::info!("    Last (newest): {member_str}");
                    }
                }
            }
            Err(e) => log_result("zrevrange (all)", false, &format!("{e}")),
        }

        // 6. ZREVRANGE with limit — read last 5 bars
        tracing::info!("{}", "-".repeat(60));
        match client
            .zrevrange::<Vec<Value>, _>(&key, 0, 4, false)
            .await
        {
            Ok(members) => {
                let count = members.len();
                log_result(
                    "zrevrange (limit 5)",
                    count == 5,
                    &format!("returned {count} members"),
                );
            }
            Err(e) => log_result("zrevrange (limit 5)", false, &format!("{e}")),
        }

        // 7. ZREVRANGE with scores — verify scores match timestamps
        tracing::info!("{}", "-".repeat(60));
        match client
            .zrevrange::<Vec<Value>, _>(&key, 0, -1, true)
            .await
        {
            Ok(result) => {
                // With withscores=true, result is flat: [member1, score1, member2, score2, ...]
                let pairs = result.len() / 2;
                log_result(
                    "zrevrange withscores",
                    pairs == bar_count,
                    &format!("returned {pairs} member+score pairs"),
                );
                // Verify scores are descending (newest first)
                let mut scores_descending = true;
                let mut prev_score: f64 = f64::MAX;
                for chunk in result.chunks(2) {
                    if chunk.len() == 2 {
                        if let Value::Double(score) = chunk[1] {
                            if score > prev_score {
                                scores_descending = false;
                                break;
                            }
                            prev_score = score;
                        }
                    }
                }
                log_result(
                    "scores descending",
                    scores_descending,
                    "scores are in descending order (newest first)",
                );
            }
            Err(e) => log_result("zrevrange withscores", false, &format!("{e}")),
        }

        // 8. ZREMRANGEBYRANK — trim to keep only 5 entries
        tracing::info!("{}", "-".repeat(60));
        match client
            .zremrangebyrank::<i64, _>(&key, 0, -(6))
            .await
        {
            Ok(removed) => {
                log_result(
                    "zremrangebyrank (trim to 5)",
                    removed == 5,
                    &format!("removed {removed} members"),
                );
                // Verify count is now 5
                match client.zcard::<i64, _>(&key).await {
                    Ok(count) => log_result(
                        "zcard after trim",
                        count == 5,
                        &format!("expected=5, actual={count}"),
                    ),
                    Err(e) => log_result("zcard after trim", false, &format!("{e}")),
                }
            }
            Err(e) => log_result("zremrangebyrank (trim to 5)", false, &format!("{e}")),
        }

        // 9. Overwrite with ZADD — verify last-write-wins (same score = same timestamp)
        tracing::info!("{}", "-".repeat(60));
        // Use a timestamp within the 5 retained entries (3rd newest = index 2 of original)
        let overwrite_ts = now_ts - 2 * 24 * 60 * 60 * 1000; // 2 days ago, within retained window
        let overwrite_member = format!("{overwrite_ts}|9999|10000|9980|9995|999999");
        match client
            .zadd::<Value, _, _>(
                &key,
                None,
                None,
                false,
                false,
                vec![(overwrite_ts as f64, overwrite_member)],
            )
            .await
        {
            Ok(_) => log_result("zadd overwrite", true, "overwrote existing member"),
            Err(e) => log_result("zadd overwrite", false, &format!("{e}")),
        }

        // Verify the overwrite
        match client
            .zrevrange::<Vec<Value>, _>(&key, 0, -1, false)
            .await
        {
            Ok(members) => {
                // Find the overwritten member and verify new values
                let mut found_overwrite = false;
                for member_val in &members {
                    let s = match member_val {
                        Value::Bytes(b) => std::str::from_utf8(b).ok(),
                        Value::String(s) => std::str::from_utf8(s.as_bytes()).ok(),
                        _ => None,
                    };
                    if let Some(s) = s {
                        if s.contains("9999|10000") {
                            found_overwrite = true;
                            break;
                        }
                    }
                }
                log_result(
                    "overwrite verification",
                    found_overwrite,
                    if found_overwrite {
                        "found overwritten member with new values"
                    } else {
                        "overwritten member not found"
                    },
                );
            }
            Err(e) => log_result("overwrite verification", false, &format!("{e}")),
        }

        // 9b. crawl_ts dedup — same score, different crawl_ts; highest wins
        tracing::info!("{}", "-".repeat(60));
        {
            let dedup_key = format!("{key}:dedup");
            let _: Result<Value, Error> = client.del(&dedup_key).await; // cleanup

            let ts = now_ts;
            let crawl_old = 1000000000000_i64;
            let crawl_new = 2000000000000_i64;

            // Write two members with same score (ts) but different crawl_ts
            let member_old = format!("{ts}|100|110|90|105|50000|{crawl_old}");
            let member_new = format!("{ts}|200|210|190|205|60000|{crawl_new}");

            match client
                .zadd::<Value, _, _>(
                    &dedup_key,
                    None,
                    None,
                    false,
                    false,
                    vec![(ts as f64, member_old.clone()), (ts as f64, member_new.clone())],
                )
                .await
            {
                Ok(_) => log_result("crawl_ts dedup (zadd)", true, "added 2 members with same score"),
                Err(e) => log_result("crawl_ts dedup (zadd)", false, &format!("{e}")),
            }

            // Read back and verify only the member with highest crawl_ts is kept
            // (Redis ZADD with same score replaces the member, so we get the last one written)
            match client
                .zrevrange::<Vec<Value>, _>(&dedup_key, 0, -1, false)
                .await
            {
                Ok(members) => {
                    let count = members.len();
                    if count == 1 {
                        let s = members.first().and_then(|v| match v {
                            Value::Bytes(b) => std::str::from_utf8(b).ok(),
                            Value::String(s) => std::str::from_utf8(s.as_bytes()).ok(),
                            _ => None,
                        });
                        match s {
                            Some(member_str) => {
                                // Parse and check crawl_ts
                                let fields: Vec<&str> = member_str.split('|').collect();
                                if fields.len() >= 7 {
                                    let parsed_crawl: i64 = fields[6].parse().unwrap_or(0);
                                    log_result(
                                        "crawl_ts dedup (highest wins)",
                                        parsed_crawl == crawl_new,
                                        &format!("crawl_ts={parsed_crawl}, expected={crawl_new}"),
                                    );
                                } else {
                                    log_result("crawl_ts dedup (highest wins)", false, &format!("unexpected field count: {}", fields.len()));
                                }
                            }
                            None => log_result("crawl_ts dedup (highest wins)", false, "failed to extract member string"),
                        }
                    } else {
                        log_result(
                            "crawl_ts dedup (highest wins)",
                            false,
                            &format!("expected 1 member, got {count}"),
                        );
                    }
                }
                Err(e) => log_result("crawl_ts dedup (highest wins)", false, &format!("{e}")),
            }

            let _: Result<Value, Error> = client.del(&dedup_key).await; // cleanup
        }

        // 10. SCAN — discover keys matching "ohlcv:*"
        tracing::info!("{}", "-".repeat(60));
        let mut all_keys = Vec::new();
        let mut cursor: u64 = 0;
        let mut scan_ok = true;

        for _ in 0..100 {
            match client
                .scan_page::<(u64, Vec<String>), _, _>(cursor.to_string(), "ohlcv:*", Some(100), None)
                .await
            {
                Ok((next_cursor, keys)) => {
                    all_keys.extend(keys);
                    if next_cursor == 0 {
                        break;
                    }
                    cursor = next_cursor;
                }
                Err(e) => {
                    tracing::error!("SCAN error: {e}");
                    scan_ok = false;
                    break;
                }
            }
        }
        log_result(
            "scan (ohlcv:*)",
            scan_ok && !all_keys.is_empty(),
            &format!("found {} keys", all_keys.len()),
        );

        // Check if our test key is in the scan results
        let found_test_key = all_keys.iter().any(|k| k == &key);
        log_result(
            "scan found test key",
            found_test_key,
            &format!("key={key}"),
        );

        // 11. Cleanup — delete test key
        tracing::info!("{}", "-".repeat(60));
        match client.del(&key).await {
            Ok(Value::Integer(n)) if n == 1 => {
                log_result("cleanup (DEL)", true, "deleted test key");
            }
            Ok(v) => log_result("cleanup (DEL)", false, &format!("unexpected response: {v:?}")),
            Err(e) => log_result("cleanup (DEL)", false, &format!("{e}")),
        }

        // Verify deleted
        match client.zcard::<i64, _>(&key).await {
            Ok(count) => log_result("verify deletion", count == 0, &format!("zcard={count}")),
            Err(e) => log_result("verify deletion", false, &format!("{e}")),
        }

        // 12. Summary
        tracing::info!("{}", "-".repeat(60));
        let pass = PASS_COUNT.load(Ordering::Relaxed);
        let fail = FAIL_COUNT.load(Ordering::Relaxed);
        tracing::info!("Results: {pass} passed, {fail} failed");
        if fail == 0 {
            tracing::info!("All tests passed!");
        } else {
            tracing::error!("{fail} test(s) failed!");
        }
    });
}
