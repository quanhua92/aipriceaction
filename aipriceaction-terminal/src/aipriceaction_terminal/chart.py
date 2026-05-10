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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._df: pd.DataFrame | None = None

    def _auto_candles(self) -> int:
        try:
            w = self.size.width
        except Exception:
            w = 80
        if w < 60:
            return 15
        if w < 90:
            return 30
        if w < 130:
            return 50
        return 80

    def build_chart(self, df: pd.DataFrame) -> None:
        self._df = df
        self._draw()

    def _draw(self) -> None:
        self.plt.clear_figure()

        if self._df is None or self._df.empty or not all(
            c in self._df.columns for c in ("open", "high", "low", "close")
        ):
            self.plt.title("No data")
            self.refresh()
            return

        n = self._auto_candles()
        df = self._df.tail(n).reset_index(drop=True)

        symbol = df["symbol"].iloc[-1] if "symbol" in df.columns else ""
        self.plt.title(f"{symbol} — {len(df)}D" if symbol else f"{len(df)}D")

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
