"""Ticker data tab: grouped live data with detail panel."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Input, Static, Tree
from textual.widgets._tree import TreeNode

from aipriceaction_terminal.chart import OHLCVChart

_MODE_LABELS = {
    "vn": "VN Tickers",
    "crypto": "Crypto",
    "global": "Global",
}

# Map tab mode to SDK source name.
_MODE_TO_SOURCE = {
    "vn": "vn",
    "crypto": "crypto",
    "global": "yahoo",
}


@dataclass
class TickerRow:
    """A single ticker with live data for display."""

    symbol: str
    name: str = ""
    group: str = ""
    time: str = ""
    close: float = 0.0
    volume: float = 0.0
    open_: float = 0.0
    high: float = 0.0
    low: float = 0.0
    change: float | None = None
    volume_changed: float | None = None
    money_changed: float | None = None
    ma10_score: float | None = None
    ma20_score: float | None = None
    ma50_score: float | None = None
    ma100_score: float | None = None
    ma200_score: float | None = None

    @property
    def value(self) -> float:
        return self.close * self.volume


@dataclass
class GroupData:
    """A group of tickers."""

    name: str
    tickers: list[TickerRow] = field(default_factory=list)

    @property
    def total_value(self) -> float:
        return sum(t.value for t in self.tickers)


class TickerGroupTree(Tree[TickerRow]):
    """Left panel: tree of ticker groups with ticker children."""

    DEFAULT_CSS = """
    TickerGroupTree {
        padding: 0 1;
    }
    """

    class TickerSelected(Message):
        """Emitted when a ticker leaf is selected."""

        def __init__(self, ticker: TickerRow) -> None:
            super().__init__()
            self.ticker = ticker

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[TickerRow]) -> None:
        data = event.node.data
        if data is not None:
            self.post_message(self.TickerSelected(data))


class TickerDetailPanel(VerticalScroll):
    """Right panel: detailed view of a selected ticker."""

    DEFAULT_CSS = """
    TickerDetailPanel {
        padding: 1 2;
    }
    """

    def __init__(self, source: str = "vn", **kwargs):
        super().__init__(**kwargs)
        self.source = source
        self._chart = OHLCVChart(id="td-chart")
        self._content = Static(id="td-detail")
        self._content.border_title = ""

    def compose(self) -> ComposeResult:
        yield self._chart
        yield self._content

    def update_ticker(self, ticker: TickerRow) -> None:
        lines: list[Text] = []

        # Header
        header = Text()
        header.append(ticker.symbol, style="bold")
        if ticker.name:
            header.append(f"  {ticker.name}", style="dim")
        if ticker.group:
            header.append(f"\n{ticker.group}", style="dim italic")
        lines.append(header)
        lines.append(Text(""))

        # OHLCV
        ohlcv = Text()
        if ticker.time:
            ohlcv.append("Time:    ", style="bold")
            ohlcv.append(ticker.time)
            ohlcv.append("\n")
        ohlcv.append("Close:   ", style="bold")
        ohlcv.append(self._fmt_number(ticker.close))
        ohlcv.append("\nOpen:    ", style="bold")
        ohlcv.append(self._fmt_number(ticker.open_))
        ohlcv.append("\nHigh:    ", style="bold")
        ohlcv.append(self._fmt_number(ticker.high))
        ohlcv.append("\nLow:     ", style="bold")
        ohlcv.append(self._fmt_number(ticker.low))
        ohlcv.append("\nVolume:  ", style="bold")
        ohlcv.append(self._fmt_volume(ticker.volume))
        ohlcv.append("\nValue:   ", style="bold")
        ohlcv.append(self._fmt_money(ticker.value))
        lines.append(ohlcv)
        lines.append(Text(""))

        # Change metrics
        changes = Text()
        if ticker.change is not None:
            changes.append("Change:    ", style="bold")
            changes.append(self._fmt_pct(ticker.change))
            changes.append("\n")
        if ticker.volume_changed is not None:
            changes.append("Vol Chg:   ", style="bold")
            changes.append(self._fmt_pct(ticker.volume_changed))
            changes.append("\n")
        if ticker.money_changed is not None:
            changes.append("Money Chg: ", style="bold")
            changes.append(self._fmt_money(ticker.money_changed))
            changes.append("\n")
        if changes.plain:
            lines.append(changes)
            lines.append(Text(""))

        # MA Scores
        scores = Text()
        scores.append("MA Scores\n", style="bold underline")
        for label, val in [
            ("MA10", ticker.ma10_score),
            ("MA20", ticker.ma20_score),
            ("MA50", ticker.ma50_score),
            ("MA100", ticker.ma100_score),
            ("MA200", ticker.ma200_score),
        ]:
            if val is not None:
                scores.append(f"  {label:>6}: ", style="bold")
                scores.append(self._fmt_pct(val))
                scores.append("\n")
        lines.append(scores)

        self._content.border_title = f" {ticker.symbol} "
        self._content.update(Text("\n").join(lines))
        self._load_chart(ticker.symbol)

    def show_placeholder(self, label: str) -> None:
        self._content.border_title = ""
        self._content.update(
            Text(f"Select a ticker from the {label} tree to view details.", style="dim italic")
        )
        self._chart.show_loading()

    @work(exclusive=True)
    async def _load_chart(self, symbol: str) -> None:
        """Fetch OHLCV data and render chart asynchronously."""
        self._chart.show_loading()
        try:
            client = self.app.client
            df = await asyncio.to_thread(
                client.get_ohlcv, ticker=symbol, interval="1D", limit=30, source=self.source,
            )
            if df is not None and not df.empty:
                self._chart.build_chart(df)
            else:
                self._chart.show_error("No data")
        except Exception as e:
            self._chart.show_error(f"Chart error: {e}")

    @staticmethod
    def _fmt_number(v: float) -> Text:
        if v == 0:
            return Text("0", style="dim")
        if v >= 1_000_000:
            return Text(f"{v:,.0f}")
        if v >= 1_000:
            return Text(f"{v:,.1f}")
        return Text(f"{v:,.2f}")

    @staticmethod
    def _fmt_volume(v: float) -> Text:
        if v >= 1_000_000_000:
            return Text(f"{v / 1_000_000_000:.2f}B")
        if v >= 1_000_000:
            return Text(f"{v / 1_000_000:.1f}M")
        if v >= 1_000:
            return Text(f"{v / 1_000:.1f}K")
        return Text(f"{v:,.0f}")

    @staticmethod
    def _fmt_money(v: float) -> Text:
        if abs(v) >= 1_000_000_000_000:
            return Text(f"{v / 1_000_000_000_000:.2f}T")
        if abs(v) >= 1_000_000_000:
            return Text(f"{v / 1_000_000_000:.2f}B")
        if abs(v) >= 1_000_000:
            return Text(f"{v / 1_000_000:.1f}M")
        if abs(v) >= 1_000:
            return Text(f"{v / 1_000:.1f}K")
        return Text(f"{v:,.0f}")

    @staticmethod
    def _fmt_pct(v: float) -> Text:
        sign = "+" if v >= 0 else ""
        color = "green" if v >= 0 else "red"
        return Text(f"{sign}{v:.2f}%", style=color)


class TickerDataTab(Vertical):
    """Two-pane ticker data view: grouped tree + detail panel."""

    DEFAULT_CSS = """
    TickerDataTab {
        height: 1fr;
    }
    #td-status {
        height: 1;
        padding: 0 1;
        color: $text-disabled;
    }
    #td-main {
        height: 1fr;
    }
    #td-left {
        width: 20%;
        min-width: 30;
        border-right: solid $primary;
    }
    #td-filter {
        padding: 0 1;
        height: 3;
    }
    """

    loading: reactive[bool] = reactive(False)

    def __init__(self, mode: str = "vn", **kwargs):
        super().__init__(**kwargs)
        self.mode = mode
        self.source = _MODE_TO_SOURCE.get(mode, mode)
        self._groups: list[GroupData] = []
        self._label = _MODE_LABELS.get(mode, mode)

    def compose(self) -> ComposeResult:
        yield Static(f" {self._label}", id="td-status")
        with Horizontal(id="td-main"):
            with Vertical(id="td-left"):
                yield Input(placeholder="Filter tickers...", id="td-filter")
                yield TickerGroupTree(f" {self._label}", id="td-tree")
            yield TickerDetailPanel(source=self.source, id="td-detail")

    def on_mount(self) -> None:
        self.query_one(TickerDetailPanel).show_placeholder(self._label)
        # Defer load: child on_mount fires before the app's on_mount, so
        # self.app.client may not exist yet.
        self.call_after_refresh(self._load_data)

    def on_ticker_group_tree_ticker_selected(self, event: TickerGroupTree.TickerSelected) -> None:
        self.query_one(TickerDetailPanel).update_ticker(event.ticker)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "td-filter":
            self.query_one(TickerGroupTree).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "td-filter":
            self._populate_tree(event.value.strip())

    @staticmethod
    def _fuzzy_match(query: str, symbol: str, name: str) -> bool:
        q = query.lower()
        return q in symbol.lower() or q in name.lower()

    def _populate_tree(self, query: str = "") -> None:
        tree = self.query_one(TickerGroupTree)
        tree.root.remove_children()
        tree.clear()
        tree.show_root = False
        tree.root.expand()

        first_ticker_node: TreeNode[TickerRow] | None = None

        for group in self._groups:
            if query:
                matched = [
                    t for t in group.tickers
                    if self._fuzzy_match(query, t.symbol, t.name)
                ]
                if not matched:
                    continue
            else:
                matched = group.tickers

            label = f"{group.name} ({len(matched)})"
            group_node: TreeNode[TickerRow] = tree.root.add(label, data=None)
            group_node.expand()

            for ticker in matched:
                close_str = self._format_close(ticker.close)
                vol_str = self._format_volume_short(ticker.volume)
                child_label = f"{ticker.symbol:<8} {close_str:>12} {vol_str:>8}"
                node = group_node.add(child_label, data=ticker)
                if first_ticker_node is None:
                    first_ticker_node = node

        if first_ticker_node is not None:
            tree.select_node(first_ticker_node)
            self.query_one(TickerDetailPanel).update_ticker(first_ticker_node.data)

    @work(exclusive=True)
    async def _load_data(self) -> None:
        self.loading = True
        status = self.query_one("#td-status", Static)
        status.update(f" {self._label} — Loading...")
        try:
            client = self.app.client
            # Fetch tickers metadata (group, name) in a thread
            tickers_info = await asyncio.to_thread(client.get_tickers, source=self.source)
            # Fetch live data in a thread
            live_data = await asyncio.to_thread(client.fetch_live_data, "1D")
            if live_data is None:
                status.update(f" {self._label} — Failed to load data")
                self.loading = False
                return

            # Build symbol→TickerInfo lookup
            info_map: dict[str, tuple[str | None, str | None]] = {}
            for t in tickers_info:
                info_map[t.ticker] = (t.group, t.name)

            # Build TickerRow list from live data, filtered to this source
            rows: list[TickerRow] = []
            for symbol, candles in live_data.items():
                if symbol not in info_map:
                    continue
                if not candles:
                    continue
                c = candles[-1]  # latest candle
                group, name = info_map[symbol]
                rows.append(
                    TickerRow(
                        symbol=symbol,
                        name=name or "",
                        group=group or "",
                        time=str(c.get("time", ""))[:10],
                        close=c.get("close", 0.0) or 0.0,
                        volume=c.get("volume", 0.0) or 0.0,
                        open_=c.get("open", 0.0) or 0.0,
                        high=c.get("high", 0.0) or 0.0,
                        low=c.get("low", 0.0) or 0.0,
                        change=c.get("close_changed"),
                        volume_changed=c.get("volume_changed"),
                        money_changed=c.get("total_money_changed"),
                        ma10_score=c.get("ma10_score"),
                        ma20_score=c.get("ma20_score"),
                        ma50_score=c.get("ma50_score"),
                        ma100_score=c.get("ma100_score"),
                        ma200_score=c.get("ma200_score"),
                    )
                )

            # Group tickers
            group_map: dict[str, list[TickerRow]] = {}
            for r in rows:
                g = r.group or "Other"
                group_map.setdefault(g, []).append(r)

            # Sort within each group by value descending
            for g in group_map.values():
                g.sort(key=lambda t: t.value, reverse=True)

            # Build sorted groups (by total value descending)
            groups = [
                GroupData(name=name, tickers=tickers)
                for name, tickers in group_map.items()
            ]
            groups.sort(key=lambda g: g.total_value, reverse=True)
            self._groups = groups

            # Populate tree
            self._populate_tree()

            total = len(rows)
            group_count = len(groups)
            status.update(f" {self._label} — {total} tickers in {group_count} groups")
        except Exception as e:
            status.update(f" {self._label} — Error: {e}")
        finally:
            self.loading = False

    def action_refresh(self) -> None:
        if not self.loading:
            self._load_data()

    @staticmethod
    def _format_close(v: float) -> str:
        if v >= 10_000:
            return f"{v:,.0f}"
        if v >= 100:
            return f"{v:,.1f}"
        return f"{v:,.2f}"

    @staticmethod
    def _format_volume_short(v: float) -> str:
        if v >= 1_000_000_000:
            return f"{v / 1_000_000_000:.1f}B"
        if v >= 1_000_000:
            return f"{v / 1_000_000:.0f}M"
        if v >= 1_000:
            return f"{v / 1_000:.0f}K"
        return str(int(v))
