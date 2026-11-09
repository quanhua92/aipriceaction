# Vietnamese crawl universe

The active Vietnamese stock catalog contains 650 shares. The screen used the
latest 60 completed daily sessions available on 2026-11-09. It ranks each
share by median daily traded value (`close × volume`), requires at least ten
positive-volume observations and a recent trade, and pins the 57 VN stocks
already in the configured watchlist.

This retained 99.962% of measured turnover and 96.071% of known market value
for the 1,519 listed shares in the source catalog. The remaining 869 listed
shares are removed from the application catalog, API ticker metadata, default
VN API queries, and future worker configuration. Their public CSV objects
under `ohlcv/vn/{symbol}/` are removed as a separate production cleanup.

The selection lives in `src/aipriceaction_api/data/vn_tickers.json`. Normal
catalog refreshes preserve these symbols. The refresh script refuses to
silently remove a retained symbol if Vietcap no longer lists it. Use
`--full-universe` only when deliberately replacing this crawl universe; review
and screen the new catalog before deploying it.

Existing SQLite candles and Parquet archives are retained. They are not part
of the active ticker catalog or public CSV crawl set, and keeping them avoids
destructive database and archive rewrites.

## Public CSV cleanup

On 2026-11-10, production removed 407,052 objects (about 57.6 MB) from the
869 excluded symbol prefixes in `ohlcv/vn/`. Verification found no remaining
objects under any excluded prefix and confirmed that all 650 retained stock
prefixes still contain data. The bucket has versioning disabled. No index,
other market, Parquet, manifest, or backup keys were included in the deletion.
