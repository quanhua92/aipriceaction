"""Tool registry and built-in tools for the agent."""

from __future__ import annotations

from dataclasses import dataclass
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
    def get_ohlcv_data(ticker: str, interval: str = "1D", limit: int = 5) -> str:
        """Fetch historical OHLCV data with MA indicators and scores.

        Accepts a single ticker or multiple comma-separated tickers.

        Args:
            ticker: Ticker symbol(s), comma-separated for multiple (e.g. "VCB" or "VHM,VIC,GEX,BID,VRE").
            interval: Time interval — "1D" (default), "1h", "1m", "5m", "15m", "30m", "4h", "1W", "2W".
            limit: Number of bars to return (default 5).
        """
        _, builder = _ensure_clients(lang)
        symbols = [t.strip() for t in ticker.split(",") if t.strip()]
        try:
            if len(symbols) == 1:
                ctx = builder.build(
                    ticker=symbols[0],
                    interval=interval,
                    limit=limit,
                    reference_ticker=None,
                    include_system_prompt=False,
                )
            else:
                ctx = builder.build(
                    tickers=symbols,
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

        from collections import Counter

        source_counts = Counter(t.source for t in tickers)
        group_counts = Counter(t.group for t in tickers if t.group)

        lines = [f"Available tickers (source={source or 'all'}), total: {len(tickers)}"]
        lines.append("Counts by source: " + ", ".join(f"{s}={c}" for s, c in source_counts.most_common()))
        lines.append("Groups: " + ", ".join(f"{g}={c}" for g, c in group_counts.most_common(15)))
        lines.append("")

        # Symbols only, comma-separated for compactness
        symbols = [t.ticker for t in tickers]
        lines.append("Symbols: " + ", ".join(symbols))

        return "\n".join(lines)

    return ToolDef(
        tool=get_ticker_list,
        name="get_ticker_list",
        description="List available ticker symbols and metadata.",
        category="market_data",
    )


def create_live_data_tool(lang: str = "en") -> ToolDef:
    """Factory: creates the get_live_data tool."""

    @tool
    def get_live_data(tickers: str = "", interval: str = "1D", top: int = 50) -> str:
        """Fetch the latest live candle for one or more tickers at once.

        Prefer calling this FIRST with tickers="" (all tickers) to get
        a broad market overview before drilling into specific tickers. This returns
        the top 50 tickers sorted by trading value (close * volume) descending,
        so the most actively traded tickers appear first. Note: this is only the
        top 50 out of all tickers in the market — use get_ticker_list to see
        the full list, and get_ohlcv_data for tickers not shown here.

        Returns one line per ticker in the same key=value format as get_ohlcv_data,
        but only the most recent candle.

        If you need multiple candles (historical context), use get_ohlcv_data instead.

        Args:
            tickers: Comma-separated ticker symbols. Leave empty ("") to get top tickers by trading value.
            interval: Time interval — "1D" (default), "1h", "1m", "5m", "15m", "30m", "4h", "1W", "2W".
            top: Maximum number of tickers to return when tickers="" (default 50). Increase this value if you need more tickers (e.g. top=200).
        """
        client, _ = _ensure_clients(lang)
        try:
            is_native = interval in {"1D", "1h", "1m"}
            data = client.fetch_live_data(interval, ma=is_native)
        except Exception as e:
            return f"Error fetching live data: {e}"
        if data is None:
            return "Failed to fetch live data."

        filter_set = {t.strip() for t in tickers.split(",") if t.strip()} if tickers else None

        # Collect (trading_value, line) pairs sorted by value descending
        entries: list[tuple[float, str]] = []
        for symbol, candles in data.items():
            if filter_set and symbol not in filter_set:
                continue
            if not candles:
                continue
            c = candles[-1]
            fields: list[str] = [f"ticker={symbol}"]
            time = client.convert_time(str(c.get("time", "")), interval)
            if time:
                fields.append(f"time={time}")
            for key in ("open", "high", "low", "close", "volume"):
                val = c.get(key)
                if val is not None:
                    fields.append(f"{key}={val:.2f}" if key != "volume" else f"{key}={val}")
            for key in ("ma10_score", "ma20_score", "ma50_score", "ma100_score", "ma200_score",
                         "close_changed", "volume_changed"):
                val = c.get(key)
                if val is not None:
                    fields.append(f"{key}={val:.2f}")
            line = " ".join(fields)
            close = c.get("close") or 0
            vol = c.get("volume") or 0
            entries.append((close * vol, line))

        if not entries:
            return f"No live data found for tickers={tickers or 'all'} ({interval})."

        # When listing all tickers, sort by trading value and cap
        if not filter_set:
            total = len(entries)
            entries.sort(key=lambda x: x[0], reverse=True)
            entries = entries[:top]
            header = f"Top {top} of {total} tickers by trading value ({interval}), sorted descending:"
            return header + "\n" + "\n".join(line for _, line in entries)

        return "\n".join(line for _, line in entries)

    return ToolDef(
        tool=get_live_data,
        name="get_live_data",
        description="Fetch the latest live candle for one or more tickers at once.",
        category="market_data",
    )


def get_default_tools(lang: str = "en") -> ToolRegistry:
    """Return a ToolRegistry pre-loaded with the built-in market data tools."""
    registry = ToolRegistry()
    registry.register(create_ohlcv_tool(lang))
    registry.register(create_ticker_list_tool(lang))
    registry.register(create_live_data_tool(lang))
    return registry
