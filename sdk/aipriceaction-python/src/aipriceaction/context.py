from __future__ import annotations

from datetime import datetime, timezone, timedelta

from .ticker import Ticker
from .system import (
    _ma_label,
    get_investment_disclaimer,
    get_ma_score_explanation,
    get_system_prompt,
    get_system_prompt_with_ticker_info,
    get_trading_hours_notice,
)
from .single import get_single_template, get_single_templates
from .multi import get_multi_template, get_multi_templates

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
    for name in ("ma10_score", "ma20_score", "ma50_score", "ma100_score", "ma200_score"):
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

    Usage:
        builder = AIContextBuilder(lang="en", ma_type="ema")
        builder.set_market_data({"VCB": [...]})
        builder.set_interval("1D")
        context = builder.build_context()

    Single-ticker with template:
        builder.build_context_with_single_template("VCB", 0)

    Multi-ticker with template:
        builder.build_context_with_multi_template(0)
    """

    def __init__(self, lang: str = "en", ma_type: str = "sma"):
        self._lang = lang
        self._ma_type = ma_type
        self._market_data: dict[str, list[Ticker]] | None = None
        self._interval: str = "1D"
        self._is_trading_hours: bool = False
        self._tickers_info: list[dict] | None = None
        self._ref_ticker: str | None = None
        self._ref_data: list[Ticker] | None = None
        self._ref_info: dict | None = None

    # -- fluent setters --

    def set_lang(self, lang: str) -> AIContextBuilder:
        self._lang = lang
        return self

    def set_ma_type(self, ma_type: str) -> AIContextBuilder:
        self._ma_type = ma_type
        return self

    def set_market_data(self, data: dict[str, list[Ticker]]) -> AIContextBuilder:
        self._market_data = data
        return self

    def set_interval(self, interval: str) -> AIContextBuilder:
        self._interval = interval
        return self

    def set_trading_hours(self, is_trading: bool) -> AIContextBuilder:
        self._is_trading_hours = is_trading
        return self

    def set_tickers_info(self, info: list[dict]) -> AIContextBuilder:
        self._tickers_info = info
        return self

    def set_reference_ticker(
        self,
        ticker: str,
        data: list[Ticker],
        info: dict | None = None,
    ) -> AIContextBuilder:
        self._ref_ticker = ticker
        self._ref_data = data
        self._ref_info = info
        return self

    def clear_reference_ticker(self) -> AIContextBuilder:
        self._ref_ticker = None
        self._ref_data = None
        self._ref_info = None
        return self

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
            lines.append(_format_ticker_block(self._ref_ticker, self._ref_data, self._interval))
            lines.append("")

        lines.append(_format_ticker_block(ticker, records, self._interval))
        return "\n".join(lines)

    # -- main build method --

    def build_context(
        self,
        question: str | None = None,
        template: dict | None = None,
        single_ticker: str | None = None,
    ) -> str:
        """Build the complete AI context string.

        Args:
            question: Custom question to append (=== Question === section).
            template: A template dict from single.py or multi.py.
            single_ticker: If set, uses single-ticker system prompt variant
                           and supports reference ticker. The {ticker} placeholder
                           in single-ticker templates will be replaced.

        Returns:
            The complete formatted context string.
        """
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

        # 7. Question
        if template is not None:
            q_text = template["question"]
            if single_ticker and "{ticker}" in q_text:
                q_text = q_text.replace("{ticker}", single_ticker)
            sections.append(f"=== Question ===\n{q_text}")
        elif question is not None:
            sections.append(f"=== Question ===\n{question}")

        return "\n\n".join(sections)

    # -- convenience methods --

    def build_context_with_single_template(self, ticker: str, template_index: int) -> str:
        template = get_single_template(self._lang, template_index)
        if template is None:
            raise IndexError(f"Single-ticker template index {template_index} out of range")
        return self.build_context(template=template, single_ticker=ticker)

    def build_context_with_multi_template(self, template_index: int) -> str:
        template = get_multi_template(self._lang, template_index)
        if template is None:
            raise IndexError(f"Multi-ticker template index {template_index} out of range")
        return self.build_context(template=template)

    def get_single_templates(self) -> list[dict]:
        return get_single_templates(self._lang)

    def get_multi_templates(self) -> list[dict]:
        return get_multi_templates(self._lang)
