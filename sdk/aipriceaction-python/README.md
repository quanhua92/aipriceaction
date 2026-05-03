# aipriceaction

Python SDK for [AIPriceAction](https://aipriceaction.com/) — OHLCV data access and AI context builder for multi-market investment analysis. Reads from a public S3 archive (no API credentials needed).

## Install

```bash
pip install aipriceaction
```

## Data Sources

The SDK reads OHLCV data from an S3-compatible archive. All sources are auto-detected from ticker metadata — no need to specify which market a ticker belongs to.

| Source | Examples | Intervals |
|---|---|---|
| Vietnamese stocks (VCI) | `VCB`, `FPT`, `VNINDEX` | `1m`, `1h`, `1D` |
| US / international stocks (Yahoo) | `AAPL`, `GOOGL`, `GC=F` | `1m`, `1h`, `1D` |
| Cryptocurrency (Binance) | `BTCUSDT`, `ETHUSDT` | `1m`, `1h`, `1D` |
| SJC gold | `SJC-GOLD` | `1D` |

## Quick Start

```python
from aipriceaction import AIPriceAction

client = AIPriceAction()

# Ticker metadata
tickers = client.get_tickers()            # all tickers
tickers = client.get_tickers(source="vn") # filter by source

# OHLCV data as DataFrame
df = client.get_ohlcv("VCB", interval="1D")                         # VN stock
df = client.get_ohlcv("AAPL", interval="1D")                        # US stock
df = client.get_ohlcv("BTCUSDT", interval="1D")                     # crypto
df = client.get_ohlcv(tickers=["VCB", "FPT", "BTCUSDT"], interval="1D")  # mixed

# Date range, limit, MA indicators
df = client.get_ohlcv("VCB", start_date="2025-01-01", end_date="2025-04-30", ma=True)
df = client.get_ohlcv("VCB", interval="1D", limit=100, ema=True)    # EMA instead of SMA
```

Override the S3 endpoint if self-hosting:

```python
client = AIPriceAction(base_url="https://your-s3-endpoint/archive")
```

Data is cached to disk by default (temp dir). Set `cache_dir` for persistent caching:

```python
client = AIPriceAction(cache_dir="./cache")
```

## AI Context Builder

Build structured context strings for LLM-powered investment analysis.

```python
from aipriceaction import AIContextBuilder

builder = AIContextBuilder(lang="en")

# Single ticker (VNINDEX included as reference by default)
context = builder.build(ticker="VCB", interval="1D")

# Multi ticker
context = builder.build(tickers=["VCB", "FPT", "TCB"], interval="1D")

# No data — system prompt + disclaimer only
context = builder.build()

# Omit VNINDEX reference
context = builder.build(ticker="VCB", interval="1D", reference_ticker=None)
```

### Browse Question Bank

```python
for q in builder.questions("single"):
    print(f"{q['title']}: {q['snippet']}")
```

### Ask LLM

Requires `OPENAI_API_KEY`. The context is built once and reused across `answer()` calls for KV cache efficiency.

```python
builder.build(ticker="VCB", interval="1D")

response = builder.answer("What is the current trend?")
follow_up = builder.answer("What is the support level?")  # faster, KV cache hit
```

### Configuration

Set via environment variables or `.env` file:

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | `""` | API key for LLM calls |
| `OPENAI_BASE_URL` | `https://openrouter.ai/api/v1` | LLM API endpoint |
| `OPENAI_MODEL` | `openai/gpt-oss-20b` | Default LLM model |
| `ANTHROPIC_API_KEY` | `""` | Anthropic API key |
| `AI_CONTEXT_LANG` | `en` | Context language (`en` or `vi`) |

### OpenRouter Models

Curated free-tier models available via `OpenRouter`:

```python
from aipriceaction.llm_models import OpenRouter

for m in OpenRouter.FREE:
    print(f"{m.id} — {m.label}")
```

## Examples

### Build context

```python
from aipriceaction import AIContextBuilder

builder = AIContextBuilder(lang="en")

# Single ticker — prints questions, then full context
builder.build(ticker="VCB", interval="1D")
print(builder._last_context)
```

### Multi-ticker context

```python
builder.build(tickers=["VCB", "FPT", "TCB"], interval="1D")
print(builder._last_context)
```

### System prompt only (no market data)

```python
context = builder.build()
print(context)
```

### Build context + call LLM

Build once, ask multiple questions — the second call is faster due to LLM KV cache.

```python
from aipriceaction import AIContextBuilder

builder = AIContextBuilder(lang="en")

# Build context once
builder.build(ticker="VCB", interval="1D")

# First question (cold)
response1 = builder.answer("What is the current trend?")

# Follow-up (warm — same context prefix, KV cache hit)
response2 = builder.answer("What is the support level?")
```

### Multi-timeframe analysis

Switch timeframe between questions. Pass previous responses as `history` so the LLM can cross-reference timeframes.

```python
from aipriceaction import AIContextBuilder

builder = AIContextBuilder(lang="en")

# Daily context — big picture
builder.build(ticker="VIC", interval="1D")
daily_response = builder.answer("What is the weekly trend?")

# Hourly context — intraday detail, with daily analysis as history
builder.build(ticker="VIC", interval="1h")
hourly_response = builder.answer(
    "Confirm or reject the daily trend using intraday data.",
    history=[daily_response],
)
```

More examples in [examples/](examples/):

| Example | Description |
|---|---|
| [single_ticker.py](examples/single_ticker.py) | Build context for one ticker |
| [multi_ticker.py](examples/multi_ticker.py) | Build context for multiple tickers |
| [multi_timeframe.py](examples/multi_timeframe.py) | Multi-timeframe: daily + hourly with history |
| [reference_ticker.py](examples/reference_ticker.py) | Context with VNINDEX reference |
| [llm_question.py](examples/llm_question.py) | Build context + call LLM |
| [system_prompt_only.py](examples/system_prompt_only.py) | System prompt without ticker data |

## License

MIT
