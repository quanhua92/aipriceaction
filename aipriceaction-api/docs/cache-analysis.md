# API Cache and Availability Analysis

**Checked:** 2026-10-24

**Production:** Python API behind Nginx, two API containers, one SQLite database

**Commits:** `4a8ee3d` (Nginx and two API replicas), `3e74f2c` (pre-pull Nginx before rolling updates)

## Summary

The API uses in-process Python caches backed by SQLite version markers. Redis is
not configured in production. The SQLite database is authoritative; the worker
and both API containers use the same `/data` mount. Each API container keeps
its own copy of the in-memory caches.

The new Nginx setup routes port `8899` to two API containers. A rolling update
was run while probing the public API listener: **216 health requests succeeded
and none failed**. This protects API requests while one API container is being
replaced. It does not protect against failure of the host or Nginx itself.

The cache does not make every request an in-memory response hit. The `/tickers`
response cache has a 60-second maximum age, but any market-data write advances
one global SQLite epoch and invalidates every cached ticker response. The
history and indicator caches are more targeted and can still serve most rows.
Recent logs show those caches were warm, but concurrent worker writes caused
response and RRG cache misses. RRG recomputation and worker load were the main
latency spikes in the measured bursts.

## Cache layers

The limits below apply **per API process**, not to the whole deployment.

The implementations are in `src/aipriceaction_api/response_cache.py`,
`data_loading.py`, `indicator_cache.py`, and `analysis_cache.py`. SQLite epoch
and series-version handling is in `src/aipriceaction_api/storage.py`.

| Layer | What it holds | Capacity and expiry | How it stays current |
|---|---|---|---|
| Ticker response cache | Prepared `/tickers` response data | 64 MiB, up to 500 entries, 60-second TTL; responses over 20,000 rows are not retained | Keyed by the global SQLite `market_epoch`. Any committed market-data write invalidates a cached response, including a write to an unrelated market or symbol. |
| SQLite history loader | Recent candle tails and related archive/quality metadata | 256 MiB LRU; no time-based expiry | Checks SQLite `history_versions` for the requested source, symbols, and interval. Changed symbols are reloaded. It can reuse a wider cached batch for subset requests. |
| Latest indicator cache | Latest response row with requested MA/EMA fields | 64 MiB LRU; no time-based expiry | Keyed by source, symbol, interval, MA/EMA options, and that series' SQLite version. Only changed or missing symbols need calculation. |
| RRG result cache | Completed RRG analysis payloads | 32 MiB and up to 32 entries; no time-based TTL | Validated against the ticker-universe membership and versions of its VN daily inputs. A changed version removes the old result. |

These caches are local to each API process. Nginx does not share cache entries
between replicas. The startup warmup loads the same useful data into each
process; a restart clears that process's cache and repeats the warmup.

Redis is optional in the code, but the production container reported
`redis_configured=False`. Reads therefore use SQLite and the Python caches;
there is no Redis cache in this deployment.

## Startup warmup and memory

After the rolling update, both API containers logged the same warmup results:

| Warmed data | Per API replica |
|---|---:|
| Latest indicator rows / entries | 5,247 / 5,247 |
| Indicator cache size | 8.7 MiB |
| Recent market history entries | 8 |
| Recent market history cache size | 139.6 MiB |
| Homepage RRG result entries | 3 |

Docker reported about 414 MiB and 421 MiB of resident memory for the two API
containers after the rollout and requests. The worker used about 110 MiB at
that sample. These are observed process totals, not just cache sizes; Python,
SQLite, request buffers, and other objects also use memory.

## Production checks

The host listener and public API were checked after deployment:

- `http://127.0.0.1:8899/health` through Nginx returned HTTP 200 in about 7 ms.
- `https://api.aipriceaction.com/health` returned HTTP 200 in about 54 ms.
- Both `aipriceaction-api` and `aipriceaction-api-2` reported healthy.
- Nginx configuration passed `nginx -t`.
- Logs showed homepage requests reaching both API containers.

The exact 19 homepage GETs were sent concurrently through the Nginx service
inside the production Docker network. This measures Nginx plus the API without
the external internet or Cloudflare in the request path. All 19 returned HTTP
200 in every recorded burst.

| Burst | Total time | Slowest request | Failed requests |
|---|---:|---:|---:|
| First burst after initial deployment | 821 ms | 820 ms | 0 of 19 |
| Immediate repeat | 1,019 ms | 1,008 ms | 0 of 19 |
| After rolling-update verification | 1,201 ms | 1,053 ms | 0 of 19 |
| Immediate repeat after that | 1,720 ms | 1,547 ms | 0 of 19 |

The latest detailed burst is below. The browser sends duplicate RRG and
no-MA ticker requests, so those duplicates are listed separately.

| # | Request | Latency |
|---:|---|---:|
| 1 | `/tickers?limit=1&ma=true&ema=true&mode=vn&format=csv` | 845 ms |
| 2 | `/tickers?limit=1&ma=true&ema=true&mode=crypto&format=csv` | 332 ms |
| 3 | `/tickers?limit=1&ma=true&ema=true&mode=yahoo&format=csv` | 229 ms |
| 4 | `/analysis/rrg?algorithm=jdk&mode=vn&trails=5&min_volume=1000&benchmark=VNINDEX&period=10&date=2026-10-21&ema=true` | 1,053 ms |
| 5 | `/tickers?interval=1D&limit=1&mode=vn&ema=true&format=csv` | 692 ms |
| 6 | `/health` | 130 ms |
| 7 | `/tickers/group` | 131 ms |
| 8 | `/tickers?limit=1&ma=false&mode=vn&format=csv` | 685 ms |
| 9 | `/tickers/group?mode=crypto` | 133 ms |
| 10 | `/tickers?limit=1&ma=false&mode=crypto&format=csv` | 276 ms |
| 11 | `/tickers/group?mode=yahoo` | 147 ms |
| 12 | `/tickers?limit=1&ma=false&mode=yahoo&format=csv` | 327 ms |
| 13 | `/tickers/name` | 64 ms |
| 14 | `/tickers/name?mode=crypto` | 150 ms |
| 15 | `/tickers/name?mode=yahoo` | 139 ms |
| 16 | Duplicate RRG request from row 4 | 1,048 ms |
| 17 | Duplicate VN no-MA request from row 8 | 766 ms |
| 18 | Duplicate crypto no-MA request from row 10 | 299 ms |
| 19 | Duplicate Yahoo no-MA request from row 12 | 266 ms |

In these URLs, `limit=1` requests one row per ticker; it does not restrict the
response to one ticker. The VN requests returned 1,559 ticker rows. The
MA+EMA CSV was about 239 kB, and the no-MA CSV was about 129 kB.

## What the request timings show

The request timing logs split `/tickers` work into response-cache lookup, data
loading, rendering, and CSV output. Recent production samples showed:

- Crypto's 60 requested symbols had a complete history-loader hit. The loader
  stage took about 12–22 ms, and the whole handler took about 113–228 ms during
  those concurrent bursts.
- Yahoo's 133-symbol history batch was cached. Its loader stage was about
  37 ms; all 130 returned ticker rows had indicator-cache hits. The handler was
  about 116–161 ms in those samples.
- VN returned 1,559 rows. The loader checked 1,566 market identities, with
  1,551–1,556 indicator hits and 3–8 history misses. On sampled requests the
  history-loader stage was about 99–333 ms, and the whole handler was about
  394–761 ms.
- During the latest recorded high-latency sample, VN `/tickers` handlers took
  about 1.0 seconds. RRG calculations took about 0.8–1.3 seconds.
- At that same sample, the worker used about 49% CPU. The two API containers
  were each below 2% CPU at the instant Docker sampled them. Worker activity
  and concurrent requests can change between samples.

The raw-history and indicator caches were serving most unchanged symbols. The
remaining work included SQLite version checks, copying/merging response data,
building the enhanced rows, formatting CSV, and concurrent worker activity.
That is why a history-cache hit does not mean the entire HTTP response is a
cache hit.

## Why responses still miss

### `/tickers` response cache

The response cache uses a 60-second TTL **and** a global `market_epoch` value.
The TTL is only the maximum lifetime when the epoch stays unchanged. A write to
any market-data series increments that epoch, so a BTC update can invalidate a
VN response and a VN update can invalidate a Yahoo response. Logs explicitly
showed `response_cache_miss_reason="market_epoch_changed"` during worker
activity.

With two replicas, each process has its own response cache. Requests sent to a
replica for the first time will miss there even if the other replica has
already built that response. This is expected for an in-memory cache without a
shared cache service.

### RRG cache

RRG results are cached in each process and checked against the versions of the
VN daily series used as inputs. Logs showed
`cache_miss_reason="input_version_changed"`; recalculation took roughly
700–1,333 ms. The cache key can be present and still miss when the input
version has changed since it was stored.

The current version check tracks each series as a whole. A newer bar can
therefore invalidate an RRG result requested for an earlier date, even when
that later bar is outside the requested analysis date. A future optimization
should use a date-aware input snapshot while still invalidating corrections to
bars at or before that date.

## Deployment behavior

Nginx on host port `8899` uses `least_conn` across `api:8899` and `api2:8899`.
It retries a failed or timed-out upstream request on the other replica. The
APIs share the SQLite data directory; their caches remain separate. One worker
continues to ingest and publish data.

The first Nginx installation had to move host port `8899` from the original API
container to Nginx. The original API released the port before Docker had pulled
and started Nginx, so the initial cutover had a temporary gap; its duration was
not measured. The deployment script now pulls the Nginx image before it changes
either API, then replaces one replica at a time, waits for health, and reloads
Nginx to refresh Docker DNS. During the tested follow-up rolling update, 216
health probes succeeded with no failures.

Use this from `aipriceaction-api/` for future updates:

```sh
./scripts/deploy-api-rolling.sh
```

The two replicas and Nginx are on one host. This avoids an API gap while one
replica is updated; it is not host-level redundancy. If the host or Nginx
container is unavailable, both replicas are unreachable.

## Recommended next cache work

1. Replace the global response-cache epoch with a version based on the exact
   source, symbols, and intervals used by a response. This should prevent
   unrelated market writes from clearing all cached responses.
2. Make RRG's version check date-aware. Changes after the requested date should
   not evict historical results; corrections within the date range still must.
3. Keep the stage timing logs and benchmark the same 19 requests both while the
   worker is idle and while it is actively ingesting. Track response-cache hit
   reasons, history cache misses, RRG calculation time, and worker CPU together.
4. Account for about 400 MiB of resident memory per API process in deployment
   sizing. Two replicas duplicate their in-memory caches.

The Nginx change improves deployment continuity. The production measurements
do **not** show a stable sub-second result for every burst; cache invalidation,
RRG recomputation, worker load, and duplicate in-flight requests remain the
main performance work.
