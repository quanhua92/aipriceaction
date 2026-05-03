from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pandas as pd

from .client import AIPriceAction
from .ticker import Ticker
from .system import (
    _ma_label,
    get_investment_disclaimer,
    get_ma_score_explanation,
    get_system_prompt,
    get_system_prompt_with_ticker_info,
    get_trading_hours_notice,
)
from .single import get_single_templates
from .multi import get_multi_templates

# Vietnam timezone (UTC+7, no DST)
_VN_TZ = timezone(timedelta(hours=7))

# Intervals that display date only (no time)
DATE_ONLY_INTERVALS = {"1D", "1W", "2W", "1M"}


# ---------------------------------------------------------------------------
# Time formatting helpers
# ---------------------------------------------------------------------------


def _parse_utc(utc_string: str) -> datetime | None:
    """Parse a UTC ISO string to a datetime.

    Handles: '2025-11-09T14:00:00Z', '2025-11-09 14:00:00', '2025-11-09'.
    """
    if not utc_string:
        return None
    s = utc_string.strip().replace(" ", "T")
    if not s.endswith("Z"):
        if "+" in s[10:] or "-" in s[10:]:
            # Already has timezone info
            pass
        else:
            s += "Z"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_vn_time(dt: datetime) -> str:
    return dt.astimezone(_VN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _to_vn_date(dt: datetime) -> str:
    return dt.astimezone(_VN_TZ).strftime("%Y-%m-%d")


def _format_time(time_str: str, interval: str) -> str:
    dt = _parse_utc(time_str)
    if dt is None:
        return time_str
    if interval in DATE_ONLY_INTERVALS:
        return _to_vn_date(dt)
    return _to_vn_time(dt)


# ---------------------------------------------------------------------------
# Record formatting
# ---------------------------------------------------------------------------


def _format_record(record: Ticker, interval: str) -> str:
    """Format a single Ticker into key=value line format.

    Only includes non-None optional fields. Omits total_money_changed
    (matches the TypeScript behavior in AIContextTab.tsx).
    """
    fields: list[str] = [
        f"ticker={record.symbol}",
        f"time={_format_time(record.time, interval)}",
        f"open={record.open:.2f}",
        f"high={record.high:.2f}",
        f"low={record.low:.2f}",
        f"close={record.close:.2f}",
        f"volume={record.volume}",
    ]
    for name in ("ma10", "ma20", "ma50", "ma100", "ma200"):
        val = getattr(record, name)
        if val is not None:
            fields.append(f"{name}={val:.2f}")
    for name in (
        "ma10_score",
        "ma20_score",
        "ma50_score",
        "ma100_score",
        "ma200_score",
    ):
        val = getattr(record, name)
        if val is not None:
            fields.append(f"{name}={val:.2f}")
    for name in ("close_changed", "volume_changed"):
        val = getattr(record, name)
        if val is not None:
            fields.append(f"{name}={val:.2f}")
    return " ".join(fields)


def _format_ticker_block(ticker: str, records: list[Ticker], interval: str) -> str:
    """Format a block of records for one ticker."""
    if not records:
        return ""
    sorted_recs = sorted(records, key=lambda r: r.time)
    lines = [f"## {ticker} ({len(sorted_recs)} records)"]
    for rec in sorted_recs:
        lines.append(_format_record(rec, interval))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# AIContextBuilder
# ---------------------------------------------------------------------------


class AIContextBuilder:
    """Builds AI context strings for investment analysis.

    Owns an AIPriceAction client internally and exposes build() + answer() methods.

    Usage:
        builder = AIContextBuilder()  # lang="en", ma_type="sma" by default

        # Browse question bank
        for q in builder.questions("single"):
            print(f"  {q['title']}: {q['snippet']}")

        # Single ticker
        context = builder.build(ticker="VCB", interval="1D", limit=5)

        # Multi ticker
        context = builder.build(tickers=["VCB", "FPT"], interval="1D", limit=5)

        # No data - just system prompt + disclaimer
        context = builder.build()

        # With reference ticker (VNINDEX is included by default)
        context = builder.build(ticker="VCB", limit=5)
        # To omit VNINDEX:
        context = builder.build(ticker="VCB", limit=5, reference_ticker=None)

        # LLM integration
        builder.build(ticker="VCB", interval="1D")
        response = builder.answer("What is the trend?")
        response2 = builder.answer("Follow up")  # same context, KV cache hit
    """

    def __init__(
        self,
        *,
        lang: str = "en",
        ma_type: str = "sma",
        base_url: str | None = None,
        cache_dir: str | None = None,
    ):
        self._client = AIPriceAction(base_url=base_url, cache_dir=cache_dir)
        self._lang = lang
        self._ma_type = ma_type
        self._interval: str = "1D"
        self._is_trading_hours: bool = False
        self._market_data: dict[str, list[Ticker]] | None = None
        self._tickers_info: list[dict] | None = None
        self._ref_ticker: str | None = None
        self._ref_data: list[Ticker] | None = None
        self._ref_info: dict | None = None
        self._last_context: str | None = None
        self._llm = None

    # -- questions --

    def questions(self, mode: str = "multi") -> list[dict]:
        """Browse question bank. mode='single' or 'multi'. Uses builder's lang."""
        if mode == "single":
            return get_single_templates(self._lang)
        return get_multi_templates(self._lang)

    # -- main build method --

    def build(
        self,
        ticker: str | None = None,
        tickers: list[str] | None = None,
        interval: str = "1D",
        limit: int | None = None,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        reference_ticker: str = "VNINDEX",
    ) -> str:
        """Build the complete AI context string.

        Args:
            ticker: Single ticker symbol for single-ticker mode.
            tickers: List of ticker symbols for multi-ticker mode.
            interval: Time interval. Default "1D".
            limit: Max rows per ticker.
            start_date: Start date (inclusive).
            end_date: End date (inclusive).
            reference_ticker: Reference ticker for market context. Default "VNINDEX".
                Pass None to omit.

        Returns:
            The complete formatted context string.
        """
        self._interval = interval
        single_ticker: str | None = None
        ma = self._ma_type == "ema"

        if ticker and tickers:
            raise ValueError("Use either 'ticker' or 'tickers', not both")

        # Default limit: 60 bars (~3 months daily data) for both single and
        # multi-ticker. Unlike get_ohlcv() which defaults to 1 for multi-ticker.
        effective_limit = limit if limit is not None else 60

        # Fetch data
        if ticker:
            single_ticker = ticker
            df = self._client.get_ohlcv(
                ticker,
                interval=interval,
                limit=effective_limit,
                start_date=start_date,
                end_date=end_date,
                ma=ma,
                ema=(self._ma_type == "ema"),
            )
            self._market_data = self._df_to_records(df)
            self._tickers_info = self._build_tickers_info([ticker])
        elif tickers:
            df = self._client.get_ohlcv(
                tickers=tickers,
                interval=interval,
                limit=effective_limit,
                start_date=start_date,
                end_date=end_date,
                ma=ma,
                ema=(self._ma_type == "ema"),
            )
            self._market_data = self._df_to_records(df)
            self._tickers_info = self._build_tickers_info(tickers)

        # Fetch reference ticker if specified
        if reference_ticker:
            ref_df = self._client.get_ohlcv(
                reference_ticker,
                interval=interval,
                limit=effective_limit,
                start_date=start_date,
                end_date=end_date,
                ma=ma,
                ema=(self._ma_type == "ema"),
            )
            ref_records = self._df_to_records(ref_df)
            self._ref_ticker = reference_ticker
            self._ref_data = ref_records.get(reference_ticker, [])
            self._ref_info = self._build_single_ticker_info(reference_ticker)

            # In multi-ticker mode, include reference data in market_data
            # so _build_market_data_multi() renders it.
            if not single_ticker and self._market_data is not None:
                if reference_ticker not in self._market_data and self._ref_data:
                    self._market_data[reference_ticker] = self._ref_data

        self._last_context = self._build_context(single_ticker=single_ticker)
        return self._last_context

    # -- internal helpers --

    @staticmethod
    def _df_to_records(df: pd.DataFrame) -> dict[str, list[Ticker]]:
        """Convert get_ohlcv() DataFrame to dict of Ticker lists.

        Args:
            df: DataFrame from get_ohlcv().

        Returns:
            Dict mapping symbol -> list of Ticker objects.
        """
        _OPTIONAL_COLS = [
            "ma10",
            "ma20",
            "ma50",
            "ma100",
            "ma200",
            "ma10_score",
            "ma20_score",
            "ma50_score",
            "ma100_score",
            "ma200_score",
            "close_changed",
            "volume_changed",
        ]
        result: dict[str, list[Ticker]] = {}
        for sym, group in df.groupby("symbol", sort=False):
            records = []
            for _, row in group.iterrows():
                kwargs: dict = {
                    "symbol": sym,
                    "time": str(row["time"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
                }
                for col in _OPTIONAL_COLS:
                    if col in row.index and pd.notna(row[col]):
                        kwargs[col] = float(row[col])
                records.append(Ticker(**kwargs))
            result[sym] = records
        return result

    def _build_tickers_info(self, symbols: list[str]) -> list[dict]:
        """Build ticker info dicts from get_tickers() for the given symbols."""
        all_tickers = self._client.get_tickers()
        result: list[dict] = []
        for t in all_tickers:
            if t.ticker in symbols:
                info: dict = {"symbol": t.ticker}
                if t.name:
                    info["name"] = t.name
                if t.group:
                    info["groups"] = [t.group]
                result.append(info)
        return result

    def _build_single_ticker_info(self, symbol: str) -> dict:
        """Build a single ticker info dict."""
        all_tickers = self._client.get_tickers()
        for t in all_tickers:
            if t.ticker == symbol:
                info: dict = {"symbol": t.ticker}
                if t.name:
                    info["name"] = t.name
                if t.group:
                    info["groups"] = [t.group]
                return info
        return {"symbol": symbol}

    # -- section builders --

    def _build_ticker_info_section(self, single_ticker: str | None) -> str:
        lang = self._lang
        lines: list[str] = []

        if lang == "en":
            lines.append("")
            lines.append("=== Ticker Info ===")
            lines.append("")
        else:
            lines.append("")
            lines.append("=== Thông Tin Mã CK ===")
            lines.append("")

        has_ref = self._ref_data and len(self._ref_data) > 0 and self._ref_ticker

        if has_ref:
            ref_parts: list[str] = []
            if lang == "en":
                ref_parts.append(
                    f"{self._ref_ticker} — Reference Ticker (use for market context comparison)"
                )
            else:
                ref_parts.append(
                    f"{self._ref_ticker} — Mã Tham Chiếu (dùng để so sánh bối cảnh thị trường)"
                )
            if self._ref_info:
                if self._ref_info.get("name"):
                    ref_parts.append(self._ref_info["name"])
                if self._ref_info.get("groups"):
                    ref_parts.append(f"[{', '.join(self._ref_info['groups'])}]")
            lines.append(" - ".join(ref_parts))

        if single_ticker and self._tickers_info:
            for info in self._tickers_info:
                parts: list[str] = [info["symbol"]]
                if info.get("name"):
                    parts.append(info["name"])
                if info.get("groups"):
                    parts.append(f"[{', '.join(info['groups'])}]")
                if lang == "en":
                    parts.insert(1, "— Primary Ticker (subject of analysis)")
                else:
                    parts.insert(1, "— Mã Chính (đối tượng phân tích)")
                lines.append(" - ".join(parts))
        elif self._tickers_info:
            for info in self._tickers_info:
                parts = [info["symbol"]]
                if info.get("name"):
                    parts.append(info["name"])
                if info.get("groups"):
                    parts.append(f"[{', '.join(info['groups'])}]")
                lines.append(" - ".join(parts))

        return "\n".join(lines)

    def _build_market_data_multi(self) -> str:
        if not self._market_data:
            return ""
        lang = self._lang

        lines: list[str] = []
        if lang == "en":
            lines.append("=== Market Data ===")
            lines.append("")
            lines.append(
                f"Historical OHLCV data with {_ma_label(self._ma_type, lang)} moving "
                "averages and momentum indicators for selected tickers. "
                "Each line represents one trading day with explicit key-value pairs."
            )
            lines.append("")
        else:
            lines.append("=== Dữ Liệu Thị Trường ===")
            lines.append("")
            lines.append(
                f"Dữ liệu OHLCV lịch sử với đường trung bình động {_ma_label(self._ma_type, lang)} "
                "và chỉ báo động lực cho các mã được chọn. "
                "Mỗi dòng đại diện cho một phiên giao dịch với các cặp key-value rõ ràng."
            )
            lines.append("")

        for ticker in sorted(self._market_data.keys()):
            records = self._market_data[ticker]
            if records:
                lines.append(_format_ticker_block(ticker, records, self._interval))
                lines.append("")

        return "\n".join(lines)

    def _build_market_data_single(self, ticker: str) -> str:
        if not self._market_data or not self._market_data.get(ticker):
            return ""
        records = self._market_data[ticker]
        if not records:
            return ""

        lang = self._lang

        has_ref = self._ref_data and len(self._ref_data) > 0 and self._ref_ticker

        lines: list[str] = []
        if lang == "en":
            lines.append("=== Market Data ===")
            lines.append("")
            desc = (
                f"Historical OHLCV data with {_ma_label(self._ma_type, lang)} moving "
                "averages and momentum indicators for "
            )
            if has_ref:
                desc += f"{self._ref_ticker} (reference) and {ticker}"
            else:
                desc += ticker
            desc += ". Each line represents one trading period with explicit key-value pairs."
            lines.append(desc)
            lines.append("")
        else:
            lines.append("=== Dữ Liệu Thị Trường ===")
            lines.append("")
            desc = (
                f"Dữ liệu OHLCV lịch sử với đường trung bình động {_ma_label(self._ma_type, lang)} "
                "và chỉ báo động lực cho "
            )
            if has_ref:
                desc += f"{self._ref_ticker} (tham chiếu) và {ticker}"
            else:
                desc += ticker
            desc += ". Mỗi dòng đại diện cho một phiên giao dịch với các cặp key-value rõ ràng."
            lines.append(desc)
            lines.append("")

        if has_ref and self._ref_ticker and self._ref_data:
            lines.append(
                _format_ticker_block(self._ref_ticker, self._ref_data, self._interval)
            )
            lines.append("")

        lines.append(_format_ticker_block(ticker, records, self._interval))
        return "\n".join(lines)

    # -- internal build_context --

    def _build_context(
        self,
        single_ticker: str | None = None,
    ) -> str:
        """Build the complete AI context string."""
        sections: list[str] = []
        lang = self._lang

        # 1. System Prompt
        if single_ticker:
            sections.append(get_system_prompt_with_ticker_info(lang))
        else:
            sections.append(get_system_prompt(lang))

        # 2. MA Score Explanation
        sections.append(get_ma_score_explanation(self._ma_type, lang))

        # 3. Investment Disclaimer
        sections.append(get_investment_disclaimer(lang))

        # 4. Ticker Info
        has_info = self._tickers_info or (self._ref_ticker and self._ref_data)
        if has_info:
            sections.append(self._build_ticker_info_section(single_ticker))

        # 5. Market Data
        has_data = False
        if single_ticker:
            data = self._build_market_data_single(single_ticker)
            if data:
                has_data = True
                sections.append(data)
        elif self._market_data:
            data = self._build_market_data_multi()
            if data:
                has_data = True
                sections.append(data)

        # 6. Trading Hours Notice
        if self._is_trading_hours and has_data:
            sections.append(get_trading_hours_notice(lang))

        return "\n\n".join(sections)

    # -- LLM integration --

    def answer(self, question: str, *, llm=None) -> str:
        """Call LLM with the current context + question.

        Requires a prior build() call. The same context is reused across
        multiple answer() calls so the LLM can benefit from KV cache.
        """
        if not self._last_context:
            raise ValueError("Call build() before answer()")

        context = f"{self._last_context}\n\n=== Question ===\n{question}"

        if llm is None:
            llm = self._get_default_llm()

        response = llm.invoke(context)
        return response.content

    def _get_default_llm(self):
        """Create and cache a default LLM instance from settings."""
        if self._llm is not None:
            return self._llm
        from .settings import settings
        from langchain_openai import ChatOpenAI
        self._llm = ChatOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.openai_model,
        )
        return self._llm
