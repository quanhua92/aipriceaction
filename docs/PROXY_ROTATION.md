# HTTP Proxy Rotation

## Overview
The VCI API client supports automatic proxy rotation with fallback to direct connection. This provides:
- **Load balancing** across multiple proxies
- **Fault tolerance** - if one proxy fails, automatically try the next
- **Always fallback** - direct connection is always available as option 0

## Configuration

Set the `HTTP_PROXIES` environment variable with comma-separated proxy URLs:

```bash
# No proxy (direct connection only)
unset HTTP_PROXIES

# Single proxy
export HTTP_PROXIES="socks5://127.0.0.1:1080"

# Multiple proxies (will randomize + fallback)
export HTTP_PROXIES="socks5://127.0.0.1:1080,http://proxy1.com:8080,socks5://proxy2.com:1080"
```

## Supported Proxy Types

- **HTTP**: `http://proxy.example.com:8080`
- **HTTPS**: `https://proxy.example.com:8080`
- **SOCKS5**: `socks5://127.0.0.1:1080` (requires `socks` feature in reqwest)

## How It Works

1. **Client initialization**:
   - Index 0: Always direct connection (no proxy)
   - Index 1..N: Proxies from `HTTP_PROXIES` env var
   - Each proxy is tested with `https://example.com/` before adding (failed proxies are skipped)

2. **Request flow**:
   - Randomize client order for load balancing
   - Try each client with full retry logic (5 attempts with exponential backoff)
   - Network errors (5xx, connection failures) trigger next client
   - Client errors (4xx) don't trigger fallback (request problem, not connection)
   - Return success as soon as any client succeeds
   - Return error only if all clients fail

3. **Logging**:
   - `📊 VciClient initialized with N client(s) (1 direct + M proxies)` - Startup summary
   - `✅ Added proxy: <url> (connectivity: OK)` - Proxy passed connectivity test
   - `⚠️ Proxy <url> connectivity test failed: <error>` - Proxy unreachable
   - `❌ Skipped proxy: <url> (connectivity failed)` - Proxy not added to pool
   - `❌ Failed to create client for proxy <url>: <error>` - Invalid URL or client build failed
   - `🔄 Attempt N succeeded via direct/proxy-N` - Successful API request
   - `⚠️ Client N failed, trying next client...` - Fallback triggered during request

## Examples

### CLI Mode
```bash
# Use proxy for data sync
export HTTP_PROXIES="socks5://127.0.0.1:1080"
cargo run -- pull

# Use multiple proxies for redundancy
export HTTP_PROXIES="http://proxy1.com:8080,socks5://proxy2.com:1080"
./target/release/aipriceaction pull --intervals 1D,1H,1m
```

### Server Mode
```bash
# Workers will use proxy rotation
export HTTP_PROXIES="socks5://127.0.0.1:1080"
./target/release/aipriceaction serve --port 3000
```

### Docker
```bash
# In docker-compose.yml
environment:
  - HTTP_PROXIES=socks5://proxy-service:1080,http://backup-proxy:8080

# Or override at runtime
docker run -e HTTP_PROXIES="socks5://host.docker.internal:1080" aipriceaction
```

## Troubleshooting

### Proxy not working
1. Check proxy URL format: `socks5://host:port` or `http://host:port`
2. Test proxy manually: `curl --proxy socks5://127.0.0.1:1080 https://example.com`
3. Check logs for connectivity test failures during startup
4. Verify proxy is running and accepting connections
5. Check for authentication requirements (use `socks5://user:pass@host:port`)

### All requests failing
1. Direct connection (index 0) should always work
2. Check network connectivity
3. Check VCI API status: `https://trading.vietcap.com.vn`

### High failure rate
1. Reduce number of proxies (remove unreliable ones)
2. Check proxy health independently
3. Consider using direct connection only (unset `HTTP_PROXIES`)

## Performance Impact

- **No proxy**: ~100-200ms per request (direct)
- **With proxy**: Adds proxy latency (typically +50-200ms)
- **Fallback**: If proxy fails, adds retry delay before trying direct
- **Rate limiting**: Still enforced at 30 req/min regardless of proxy

## Security Notes

- Proxy URLs are logged to stderr on startup
- Don't commit proxy URLs to git
- Use environment variables or secrets management
- SOCKS5 proxies can handle authentication: `socks5://user:pass@host:port`
