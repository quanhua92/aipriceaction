"""Tool registry and built-in tools for the agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    from aipriceaction import AIPriceAction, AIContextBuilder


@dataclass
class ToolDef:
    """Wraps a LangChain BaseTool with metadata."""

    tool: object  # BaseTool
    name: str
    description: str
    category: str = "general"


class ToolRegistry:
    """Registry for agent tools."""

    def __init__(self) -> None:
        self._tools: list[ToolDef] = []

    def register(self, tool_def: ToolDef) -> None:
        self._tools.append(tool_def)

    def unregister(self, name: str) -> None:
        self._tools = [t for t in self._tools if t.name != name]

    def get_tools(self, category: str | None = None) -> list[object]:
        if category is None:
            return [t.tool for t in self._tools]
        return [t.tool for t in self._tools if t.category == category]

    def get_tool_names(self, category: str | None = None) -> list[str]:
        if category is None:
            return [t.name for t in self._tools]
        return [t.name for t in self._tools if t.category == category]


# -- Lazy singletons (mirrors langchain_agent.py pattern) --

_client: AIPriceAction | None = None
_builder: AIContextBuilder | None = None


def _ensure_clients(lang: str = "en") -> tuple[AIPriceAction, AIContextBuilder]:
    global _client, _builder
    if _client is None:
        from aipriceaction import AIPriceAction
        _client = AIPriceAction()
    if _builder is None or _builder._lang != lang:
        from aipriceaction import AIContextBuilder
        _builder = AIContextBuilder(lang=lang)
    return _client, _builder


def _reset_clients() -> None:
    global _client, _builder
    _client = None
    _builder = None


# -- Tool factories --


def create_ohlcv_tool(lang: str = "en") -> ToolDef:
    """Factory: creates the get_ohlcv_data tool."""

    @tool
    def get_ohlcv_data(ticker: str, interval: str = "1D", limit: int = 30) -> str:
        """Fetch OHLCV data for a ticker. Returns formatted context with MA indicators and scores.

        Args:
            ticker: Ticker symbol (e.g. VCB, FPT, BTCUSDT).
            interval: Time interval — "1D" (default), "1h", or "1m".
            limit: Number of bars to return (default 30).
        """
        _, builder = _ensure_clients(lang)
        try:
            ctx = builder.build(
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

    return ToolDef(
        tool=get_ohlcv_data,
        name="get_ohlcv_data",
        description="Fetch OHLCV data for a ticker with MA indicators and scores.",
        category="market_data",
    )


def create_ticker_list_tool(lang: str = "en") -> ToolDef:
    """Factory: creates the get_ticker_list tool."""

    @tool
    def get_ticker_list(source: str | None = None) -> str:
        """List available ticker symbols and metadata.

        Args:
            source: Filter by source — "vn", "yahoo", "crypto", "sjc". None = all.
        """
        client, _ = _ensure_clients(lang)
        tickers = client.get_tickers(source=source)
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

    return ToolDef(
        tool=get_ticker_list,
        name="get_ticker_list",
        description="List available ticker symbols and metadata.",
        category="market_data",
    )


def get_default_tools(lang: str = "en") -> ToolRegistry:
    """Return a ToolRegistry pre-loaded with the built-in market data tools."""
    registry = ToolRegistry()
    registry.register(create_ohlcv_tool(lang))
    registry.register(create_ticker_list_tool(lang))
    return registry
