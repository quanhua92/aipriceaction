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
        from ..user_settings import resolve_ma_type
        _builder = AIContextBuilder(lang=lang, ma_type=resolve_ma_type())
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


def create_performers_tool(lang: str = "en") -> ToolDef:
    """Factory: creates the get_performers tool."""

    @tool
    def get_performers(
        sort_by: str = "close_changed",
        direction: str = "desc",
        limit: int = 10,
        source: str | None = None,
        group: str | None = None,
    ) -> str:
        """Rank top and worst performers from live daily data by a chosen metric.

        Returns two ranked lists (top and worst) of performers. Useful for
        identifying market leaders, laggards, and sector trends.

        Args:
            sort_by: Metric to rank by — "close_changed" (default), "volume",
                "value" (close × volume), "volume_changed",
                "ma10_score", "ma20_score", "ma50_score",
                "ma100_score", "ma200_score", "total_money_changed".
            direction: Sort direction — "desc" (default, strongest first in top)
                or "asc" (weakest first in top).
            limit: Number of entries per list (default 10, max 100).
            source: Filter by source — "vn" (default), "crypto", "yahoo". None = vn.
            group: Filter by group/sector — e.g. "NGAN_HANG", "CHUNG_KHOAN",
                "BAT_DONG_SAN", "CONG_NGHE", "DAU_KHI". None = all sectors.
        """
        from aipriceaction.performers import build_performers
        from ..user_settings import resolve_ma_type

        client, _ = _ensure_clients(lang)
        ma_type = resolve_ma_type()
        try:
            data = client.fetch_live_data("1D", ma=True, ema=(ma_type == "ema"))
        except Exception as e:
            return f"Error fetching live data: {e}"
        if not data:
            return "No live data available."

        sector_map: dict[str, str] = {}
        if source:
            tickers_meta = client.get_tickers(source=source)
            sector_map = {t.ticker: t.group for t in tickers_meta if t.group}
            source_symbols = {t.ticker for t in tickers_meta}
            data = {k: v for k, v in data.items() if k in source_symbols}

        # Filter by group/sector if specified
        if group:
            group_upper = group.upper()
            data = {k: v for k, v in data.items() if sector_map.get(k, "").upper() == group_upper}

        top, worst = build_performers(
            data, sector_map,
            sort_by=sort_by,
            direction=direction,
            limit=limit,
            source=source,
        )

        lines = [f"Top {len(top)} performers (by {sort_by}, {direction}):"]
        for i, p in enumerate(top, 1):
            chg = f"{p.close_changed:+.2f}%" if p.close_changed is not None else "N/A"
            sector = f" [{p.sector}]" if p.sector else ""
            lines.append(f"  {i}. {p.symbol}: close={p.close:.2f} change={chg} vol={p.volume:,} value={p.value:,.0f}{sector}")

        lines.append(f"\nWorst {len(worst)} performers (by {sort_by}):")
        for i, p in enumerate(worst, 1):
            chg = f"{p.close_changed:+.2f}%" if p.close_changed is not None else "N/A"
            sector = f" [{p.sector}]" if p.sector else ""
            lines.append(f"  {i}. {p.symbol}: close={p.close:.2f} change={chg} vol={p.volume:,} value={p.value:,.0f}{sector}")

        return "\n".join(lines)

    return ToolDef(
        tool=get_performers,
        name="get_performers",
        description="Rank top and worst performers by price change, volume, or MA scores.",
        category="market_data",
    )


def create_volume_profile_tool(lang: str = "en") -> ToolDef:
    """Factory: creates the get_volume_profile tool."""

    @tool
    def get_volume_profile(
        ticker: str,
        date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        bins: int = 50,
        value_area_pct: float = 70.0,
    ) -> str:
        """Compute volume-by-price histogram for a ticker using 1-minute data.

        Returns the Point of Control (POC), Value Area, volume-weighted statistics,
        and the binned profile. Useful for identifying key support/resistance levels
        based on where the most volume traded.

        Args:
            ticker: Ticker symbol (e.g. "VCB", "BTCUSDT").
            date: Single date in YYYY-MM-DD format. Defaults to today.
            start_date: Start date (YYYY-MM-DD). Alternative to --date.
            end_date: End date (YYYY-MM-DD). Defaults to start_date.
            bins: Number of price bins (default 50, range 2-200).
            value_area_pct: Value area target percentage (default 70, range 60-90).
        """
        from datetime import date as date_type

        from aipriceaction.volume_profile import compute_volume_profile

        client, _ = _ensure_clients(lang)
        ticker = ticker.upper()

        # Resolve date range
        if date:
            sd = ed = date
        elif start_date:
            sd = start_date
            ed = end_date or start_date
        else:
            today = date_type.today().isoformat()
            sd = ed = today

        # Resolve source for tick size
        source = "vn"
        try:
            tickers_meta = client.get_tickers()
            for t in tickers_meta:
                if t.ticker == ticker:
                    source = t.source or "vn"
                    break
        except Exception:
            pass

        try:
            df = client.get_ohlcv(ticker, interval="1m", start_date=sd, end_date=ed, ma=False)
        except Exception as e:
            return f"Error fetching 1m data for {ticker}: {e}"

        if df is None or df.empty:
            return f"No 1m data found for {ticker} on {sd}."

        result = compute_volume_profile(df, ticker, source=source, bins=bins, value_area_pct=value_area_pct)

        lines = [
            f"Volume Profile: {result.symbol} ({sd})",
            f"Volume: {result.total_volume:,}  Minutes: {result.total_minutes}",
            f"Range: {result.price_range.low:.2f} - {result.price_range.high:.2f}",
            f"POC: {result.poc.price:.2f} ({result.poc.percentage:.1f}%)",
            f"Value Area: {result.value_area.low:.2f} - {result.value_area.high:.2f} ({result.value_area.percentage:.1f}%)",
        ]

        if result.statistics:
            s = result.statistics
            lines.append(f"Mean: {s.mean_price:.2f}  Median: {s.median_price:.2f}  "
                         f"StdDev: {s.std_deviation:.2f}  Skew: {s.skewness:.4f}")

        if result.profile:
            lines.append(f"\nProfile ({len(result.profile)} bins):")
            max_vol = max(p.volume for p in result.profile)
            for level in result.profile:
                bar_len = int(level.volume / max_vol * 25) if max_vol > 0 else 0
                bar = "\u2588" * bar_len
                lines.append(f"  {level.price:>10.2f}  vol={level.volume:>10,.0f}  "
                             f"{level.percentage:>5.1f}%  {bar}")

        return "\n".join(lines)

    return ToolDef(
        tool=get_volume_profile,
        name="get_volume_profile",
        description="Volume-by-price histogram with POC, value area, and statistics.",
        category="market_data",
    )


def get_default_tools(lang: str = "en") -> ToolRegistry:
    """Return a ToolRegistry pre-loaded with the built-in market data tools."""
    registry = ToolRegistry()
    registry.register(create_ohlcv_tool(lang))
    registry.register(create_ticker_list_tool(lang))
    registry.register(create_live_data_tool(lang))
    registry.register(create_performers_tool(lang))
    registry.register(create_volume_profile_tool(lang))
    return registry
