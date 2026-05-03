# aipriceaction

**Live site:** [aipriceaction.com](https://aipriceaction.com) | **GitHub:** [aipriceaction](https://github.com/quanhua92/aipriceaction) | **Frontend:** [aipriceaction-web](https://github.com/quanhua92/aipriceaction-web) | **Docker image:** [`quanhua92/aipriceaction:latest`](https://hub.docker.com/r/quanhua92/aipriceaction) | **Python SDK:** [`aipriceaction` on PyPI](https://pypi.org/project/aipriceaction/)

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

### Timezone

All OHLCV data is stored in UTC+0. By default, the SDK converts timestamps to UTC+7 (ICT, Vietnam timezone) for display. Pass `utc_offset=0` to keep raw UTC, or any integer hour offset:

```python
client = AIPriceAction(utc_offset=0)       # keep raw UTC
client = AIPriceAction(utc_offset=9)       # UTC+9 (JST/KST)
client = AIPriceAction(utc_offset=-5)      # UTC-5 (EST)
```

## Live Data

By default the SDK reads from an S3 archive which may be stale by minutes to hours. Enable `use_live=True` to overlay live data from the REST API on top of S3 data:

```python
client = AIPriceAction(use_live=True)
df = client.get_ohlcv("VCB", interval="1D", limit=5, ma=False)
```

When enabled, for native intervals (`1D`, `1h`, `1m`) the SDK:
- Fetches live data from the REST API (`https://api.aipriceaction.com` by default)
- Overwrites the last candle(s) from S3 with live data
- Appends any newer candles not yet in the archive
- Falls back to S3-only data if the live API is unreachable

Live responses are cached in memory for 120 seconds to avoid redundant API calls. On API failure, stale cached data is returned if available.

Point to a self-hosted instance with `live_url`:

```python
client = AIPriceAction(
    base_url="https://your-s3-endpoint/archive",
    use_live=True,
    live_url="https://your-api-instance.com",
)
```

## AI Context Builder

Build structured context strings for LLM-powered investment analysis. Accepts the same `utc_offset` parameter as `AIPriceAction` (default 7 = UTC+7).

```python
from aipriceaction import AIContextBuilder

builder = AIContextBuilder(lang="en", utc_offset=7)  # default UTC+7

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
| [langchain_agent.py](examples/langchain_agent.py) | LangChain ReAct agent with AIContextBuilder and tool-calling |

## LangChain Agent

Build a ReAct agent with VNINDEX context from `AIContextBuilder` and tool-calling to research tickers. Use `include_system_prompt=False` to get market data only (the system prompt goes in `system_prompt=` to avoid duplication), and tools use the same builder for consistent formatting.

```python
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver

from aipriceaction import AIPriceAction, AIContextBuilder
from aipriceaction.settings import settings
from aipriceaction.system import get_system_prompt

LANG = settings.ai_context_lang
_client = AIPriceAction()
_builder = AIContextBuilder(lang=LANG)

@tool
def get_ohlcv_data(ticker: str, interval: str = "1D", limit: int = 30) -> str:
    """Fetch OHLCV data for a ticker with MA indicators."""
    try:
        return _builder.build(ticker=ticker, interval=interval, limit=limit,
                             reference_ticker=None, include_system_prompt=False)
    except Exception as e:
        return f"Error fetching {ticker}: {e}"

@tool
def get_ticker_list(source: str | None = None) -> str:
    """List available ticker symbols and metadata."""
    tickers = _client.get_tickers(source=source)
    return "\n".join(f"{t.ticker} ({t.source})" for t in tickers)

initial_context = _builder.build(ticker="VNINDEX", interval="1D",
                                limit=10, include_system_prompt=False)

llm = ChatOpenAI(api_key=settings.openai_api_key,
                 base_url=settings.openai_base_url,
                 model=settings.openai_model)

AGENT_INSTRUCTIONS = """
You have tools to fetch OHLCV data and list available tickers.
Research workflow (MANDATORY):
1. Call get_ohlcv_data for each ticker explicitly mentioned in the question.
2. Call get_ticker_list to discover tickers in the same sectors.
3. Call get_ohlcv_data for at least 2-3 additional tickers per sector.
4. Provide per-ticker analysis, sector rotation observations, and ranking table.
"""

agent = create_agent(
    llm,
    [get_ticker_list, get_ohlcv_data],
    checkpointer=MemorySaver(),
    system_prompt=get_system_prompt(LANG) + "\n\n" + AGENT_INSTRUCTIONS,
)

for event in agent.stream(
    {"messages": [{"role": "user",
                   "content": f"{initial_context}\n\nResearch VIC, STB, SSI and related tickers."}]},
    config={"configurable": {"thread_id": "demo"}},
    stream_mode="updates",
):
    ...
```

See [examples/langchain_agent.py](examples/langchain_agent.py) for the full example.

## License

MIT
