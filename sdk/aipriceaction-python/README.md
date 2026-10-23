# AIPriceAction Python SDK

The `aipriceaction` package reads OHLCV data, builds analysis context, and
provides market-ranking helpers. It supports Vietnamese stocks, global assets,
crypto, and SJC gold. Raw data access does not need an API key.

## Install

```sh
pip install aipriceaction
```

Python 3.10 or newer is required.

## Read market data

```python
from aipriceaction import AIPriceAction

client = AIPriceAction()

tickers = client.get_tickers(source="vn")
df = client.get_ohlcv("VCB", interval="1D", limit=100)
df = client.get_ohlcv("BTCUSDT", interval="1h", limit=48, ema=True)
df = client.get_ohlcv(
    tickers=["VCB", "FPT", "BTCUSDT"], interval="1D", limit=20
)
```

`get_ohlcv()` returns a pandas DataFrame. Use `ticker` for one symbol or
`tickers` for several. You can also pass `start_date`, `end_date`, `source`,
`ma`, and `ema`.

## Choose how to read current data

`AIPriceAction` defaults to `live_mode="range"`:

- `range` requests the full requested range and indicators from the API. If the
  API is unavailable or empty, it falls back to that ticker's S3 history. An API
  history-consistency error is returned instead of being hidden by stale data.
- `latest` reads history from public S3 and requests one latest candle per
  ticker from the API. The terminal app uses this mode to avoid downloading the
  full history on every request.
- `use_live=False` reads from S3 only.

```python
client = AIPriceAction(live_mode="latest")
df = client.get_ohlcv("VCB", interval="1D", limit=100, ma=False)
```

The public archive base URL defaults to `https://s3.aipriceaction.com`. Native
daily and intraday CSV paths are under `ohlcv/{source}/{ticker}/`. Daily and
hourly data may also use yearly files. The SDK reads those paths without S3
credentials. Set `base_url` to use another public archive endpoint.

Data is cached under `~/.aipriceaction/s3-cache` by default. Pass `cache_dir`
to choose another location. Timestamps are displayed in Vietnam time (UTC+7)
by default; pass `utc_offset=0` to keep UTC.

## Fetch the latest API response directly

Use `fetch_live_data()` when you need the latest API response without reading
historical S3 data:

```python
data = client.fetch_live_data("1D", ma=True)
# {"VCB": [{"time": ..., "open": ..., "high": ..., ...}], ...}
```

The response is cached in memory for 120 seconds. Set `live_url` when using a
self-hosted API:

```python
client = AIPriceAction(live_url="http://localhost:8899")
```

## Build AI context

`AIContextBuilder` formats market data for an LLM. Calling an LLM requires your
own provider key; fetching the underlying data does not.

```python
from aipriceaction import AIContextBuilder

builder = AIContextBuilder(lang="en")
context = builder.build(ticker="VCB", interval="1D", limit=50)
```

## Analysis helpers

The package also exports local calculation helpers:

```python
from aipriceaction import build_performers, compute_volume_profile

top, worst = build_performers(live_data, sector_map, sort_by="value", limit=10)
profile = compute_volume_profile(candles, "VCB", source="vn", bins=50)
```

See the function docstrings for input fields and result types. The terminal
guide has command examples: [AIPA Terminal](../../aipriceaction-terminal/README.md).

## Development

From this directory:

```sh
uv sync --locked --group dev
uv run pytest
```
