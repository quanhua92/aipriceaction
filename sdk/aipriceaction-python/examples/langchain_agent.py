"""LangChain ReAct agent that fetches ticker data via tools during analysis.

Builds initial context for VIC, STB, SSI using AIContextBuilder, then uses
tools to fetch additional ticker data before producing a final multi-ticker
comparison.

Requires OPENAI_API_KEY in .env or environment.

Usage:
    python examples/langchain_agent.py
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver

from aipriceaction import AIPriceAction, AIContextBuilder
from aipriceaction.settings import settings
from aipriceaction.system import get_system_prompt

# ── Shared client (reuses disk cache across tool calls) ──

_client = AIPriceAction()
_builder = AIContextBuilder(lang=settings.ai_context_lang)

# ── Tools ──


@tool
def get_ohlcv_data(ticker: str, interval: str = "1D", limit: int = 30) -> str:
    """Fetch OHLCV data for a ticker. Returns formatted context with MA indicators.

    Args:
        ticker: Ticker symbol (e.g. VCB, FPT, BTCUSDT).
        interval: Time interval — "1D" (default), "1h", or "1m".
        limit: Number of bars to return (default 30).
    """
    try:
        ctx = _builder.build(
            ticker=ticker,
            interval=interval,
            limit=limit,
            reference_ticker=None,
            include_system_prompt=False,
        )
    except Exception as e:
        return f"Error fetching {ticker}: {e}"
    if not ctx.strip():
        return f"No data found for {ticker} ({interval})."
    return ctx


@tool
def get_ticker_list(source: str | None = None) -> str:
    """List available ticker symbols and metadata.

    Args:
        source: Filter by source — "vn", "yahoo", "crypto", "sjc". None = all.
    """
    tickers = _client.get_tickers(source=source)
    if not tickers:
        return "No tickers found."

    lines = [f"=== Available tickers (source={source or 'all'}) ===\n"]
    lines.append(f"{'symbol':<12s}  {'name':<40s}  {'group':<30s}  {'source'}")
    lines.append("-" * 100)
    for t in tickers:
        name = (t.name or "")[:38]
        group = (t.group or "")[:28]
        lines.append(f"{t.ticker:<12s}  {name:<40s}  {group:<30s}  {t.source}")
    lines.append(f"\nTotal: {len(tickers)} tickers")
    return "\n".join(lines)


# ── System prompt ──

LANG = settings.ai_context_lang

AGENT_INSTRUCTIONS = """
## Tool Usage

You have tools to fetch OHLCV data and list available tickers:
- `get_ohlcv_data`: Fetch price data for any ticker with MA indicators and scores.
- `get_ticker_list`: Discover available tickers grouped by sector/industry.

### Research Workflow (MANDATORY)
1. First, call `get_ohlcv_data` for each ticker explicitly mentioned in the user question.
2. Then, call `get_ticker_list` to discover other tickers in the same sectors/industries.
3. Call `get_ohlcv_data` for at least 2-3 additional tickers per sector to enable
   meaningful comparison. Do NOT skip this step — a comparison with only the
   explicitly named tickers is insufficient.
4. For each ticker, assess: trend direction, VPA signals (accumulation/distribution),
   MA score momentum across timeframes, volume confirmation, and support/resistance.
5. Structure your final answer with:
   - Per-ticker analysis with specific data points from the tool results
   - Sector rotation observations (which sectors are leading/lagging)
   - Multi-ticker ranking table
6. Include the investment disclaimer at the end.
"""

SYSTEM_PROMPT = get_system_prompt(LANG) + "\n\n" + AGENT_INSTRUCTIONS

# ── LLM ──

if not settings.openai_api_key:
    raise ValueError(
        "OPENAI_API_KEY is not set. Set it via environment variable or .env file."
    )

llm = ChatOpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url,
    model=settings.openai_model,
)

# ── Build initial context (market data only, no system prompt) ──

print("[1] Building initial context for VNINDEX...")
initial_context = _builder.build(
    ticker="VNINDEX",
    interval="1D",
    limit=10,
    include_system_prompt=False,
)
print(f"    Market data: {len(initial_context):,} chars")
print(f"    Agent system prompt: {len(SYSTEM_PROMPT):,} chars\n")

# ── Create agent ──

tools = [get_ticker_list, get_ohlcv_data]
agent = create_agent(
    llm,
    tools,
    checkpointer=MemorySaver(),
    system_prompt=SYSTEM_PROMPT,
)

# ── Run ──

QUESTION = (
    "Based on the VNINDEX context, research VIC, STB, and SSI. "
    "Fetch their data and other related tickers to compare — identify sector leaders and laggards, "
    "and rank all tickers by trend strength."
)

print(f"[2] Invoking agent with: {QUESTION}\n")
print("=" * 60)

config = {"configurable": {"thread_id": "langchain-agent-demo"}}

for event in agent.stream(
    {"messages": [{"role": "user", "content": f"{initial_context}\n\n---\n\n{QUESTION}"}]},
    config=config,
    stream_mode="updates",
):
    for node, update in event.items():
        for msg in update.get("messages", []):
            msg_type = type(msg).__name__
            if msg_type == "AIMessage" and msg.tool_calls:
                for tc in msg.tool_calls:
                    print(f"\n[tool call] {tc['name']}({tc['args']})")
            elif msg_type == "ToolMessage":
                preview = msg.content[:200].replace("\n", " ")
                print(f"[tool result] {preview}...")
            elif msg_type == "AIMessage" and msg.content:
                print(f"\n[final answer]\n{msg.content}")

print("\n" + "=" * 60)
print("[3] Done.")
