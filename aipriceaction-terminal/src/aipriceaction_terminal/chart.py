"""OHLCV candlestick chart widget using plotext."""

from __future__ import annotations

import pandas as pd
from textual_plotext import PlotextPlot


class OHLCVChart(PlotextPlot):
    """Candlestick chart for OHLCV data rendered via plotext."""

    DEFAULT_CSS = """
    OHLCVChart {
        height: 18;
        margin: 0 1;
    }
    """

    def build_chart(self, df: pd.DataFrame) -> None:
        self.plt.clear_figure()

        if df.empty or not all(c in df.columns for c in ("open", "high", "low", "close")):
            self.plt.title("No data")
            self.refresh()
            return

        df = df.tail(30).reset_index(drop=True)

        symbol = df["symbol"].iloc[-1] if "symbol" in df.columns else ""
        self.plt.title(f"{symbol} — 30D" if symbol else "30D")

        dates = df["time"].astype(str).str[:10].tolist()
        data = {
            "Open": df["open"].astype(float).tolist(),
            "Close": df["close"].astype(float).tolist(),
            "High": df["high"].astype(float).tolist(),
            "Low": df["low"].astype(float).tolist(),
        }

        self.plt.date_form("Y-m-d")
        self.plt.candlestick(dates, data)
        self.refresh()

    def show_loading(self) -> None:
        self.plt.clear_figure()
        self.plt.title("Loading chart...")
        self.refresh()

    def show_error(self, msg: str = "Error") -> None:
        self.plt.clear_figure()
        self.plt.title(msg)
        self.refresh()
