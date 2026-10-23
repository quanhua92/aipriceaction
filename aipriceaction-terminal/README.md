# AIPA Terminal

AIPA Terminal provides the `aipa` command-line tool and terminal interface for
market data and analysis. It uses the Python SDK. Historical candles come from
the public S3 archive; latest-data commands request only the newest candle from
the API.

## Install

Run without installing:

```sh
uvx aipa-cli ui
```

Or install the command:

```sh
uv tool install aipa-cli
aipa ui
```

Python 3.13 or newer is required. Data commands and market snapshots do not
need an AI key. Set `OPENAI_API_KEY` to run LLM-powered analysis. Set
`OPENAI_BASE_URL` if you use another OpenAI-compatible provider.

## Common commands

| Command | Use |
| --- | --- |
| `aipa get-ohlcv-data VCB --limit 10` | Read OHLCV candles and indicators |
| `aipa live-data --top 10` | Show current high-value tickers |
| `aipa ticker-list --source vn` | List available tickers |
| `aipa performers --sort-by value` | Rank market performers |
| `aipa volume-profile VCB` | Calculate volume by price |
| `aipa analyze VCB` | Analyze a ticker with an LLM |
| `aipa deep-research` | Show a market snapshot |
| `aipa deep-research --run` | Run the full research workflow |
| `aipa fundamentals rank VCB BID CTG --sort-by roe` | Compare financial fields |
| `aipa config` | View or change local settings |
| `aipa watchlist ls` | View saved watchlists |
| `aipa resume` | List saved analysis sessions |

Add `--help` to any command to see its options. Examples:

```sh
aipa get-ohlcv-data VCB --start-date 2026-04-01 --end-date 2026-04-30 --no-ma
aipa get-ohlcv-data VCB TCB MBB --limit 20
aipa analyze VCB --question "Describe the current trend."
aipa deep-research --source crypto
aipa performers --sort-by volume --limit 5
```

Moving averages use the saved setting unless `--sma` or `--ema` is provided.
Run `aipa config get use_sma` to check the current choice.

## Data sources

| Market | Source | Example ticker |
| --- | --- | --- |
| Vietnam | VPS, VNDirect, DNSE | `VCB` |
| Crypto | Binance | `BTCUSDT` |
| Global markets | Yahoo Finance | `AAPL` |
| SJC gold | SJC daily quotes | `SJC-GOLD` |

The terminal reads historical CSV files from the public S3 layout. For current
data, it requests one latest candle per ticker and merges it with history. See
the [Python SDK guide](../sdk/aipriceaction-python/README.md) for details.

## Settings and saved files

Run `aipa setup` on first use, or use `aipa config` to update settings later.
Settings, watchlists, cached S3 files, and saved sessions live under
`~/.aipriceaction/` by default.

## Development

From this directory:

```sh
uv sync --locked --group dev
uv run pytest
```

The terminal entry point is `src/aipriceaction_terminal/cli.py`. The public
Python SDK is in [`../sdk/aipriceaction-python/`](../sdk/aipriceaction-python/README.md).
