from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


from aipriceaction.fundamental_ranking import (
    FundamentalRankEntry,
    _get_field_value,
    _latest_yearly,
    build_fundamental_ranking,
    screen_fundamentals,
)
from aipriceaction.fundamental import (
    CompanyInfo,
    FinancialRatioEntry,
    FinancialRatios,
)


def _make_ratios(ticker: str, **overrides: Any) -> FinancialRatios:
    entry = FinancialRatioEntry(
        year_report=2025,
        length_report=12,
        ticker=ticker,
        **overrides,
    )
    return FinancialRatios(
        ticker=ticker,
        updated_at="2026-01-01T00:00:00Z",
        count=1,
        ratios=[entry],
    )


def _make_client() -> MagicMock:
    client = MagicMock()

    def get_fundamental(ticker: str, source: str | None = None) -> tuple:
        data = {
            "VCB": (
                CompanyInfo(symbol="VCB", industry="Ngân hàng", current_price=62000.0, market_cap=500_000e9),
                _make_ratios("VCB", pe=14.0, pb=2.3, roe=0.20, roa=0.018, npl=0.005, dividend_yield=0.03),
            ),
            "FPT": (
                CompanyInfo(symbol="FPT", industry="Công nghệ", current_price=74000.0, market_cap=120_000e9),
                _make_ratios("FPT", pe=13.0, pb=3.3, roe=0.28, gross_margin=0.37, dividend_yield=0.028),
            ),
            "HPG": (
                CompanyInfo(symbol="HPG", industry="Thép", current_price=25000.0, market_cap=60_000e9),
                _make_ratios("HPG", pe=12.0, pb=1.4, roe=0.13, debt_to_equity=0.97, dividend_yield=0.02),
            ),
            "VIC": (
                CompanyInfo(symbol="VIC", industry="Bất động sản", current_price=50000.0, market_cap=200_000e9),
                _make_ratios("VIC", pe=144.0, pb=11.0, roe=0.08, debt_to_equity=6.38),
            ),
            "EMPTY": (None, None),
            "NO_RATIOS": (
                CompanyInfo(symbol="NO_RATIOS", industry="Test"),
                None,
            ),
        }
        return data.get(ticker, (None, None))

    client.get_fundamental = MagicMock(side_effect=get_fundamental)
    return client


class TestLatestYearly:
    def test_prefers_yearly(self) -> None:
        fr = _make_ratios("X")
        fr.ratios.insert(0, FinancialRatioEntry(year_report=2024, length_report=3, ticker="X", pe=99.0))
        result = _latest_yearly(fr)
        assert result is not None
        assert result.length_report == 12

    def test_fallback_to_last(self) -> None:
        fr = FinancialRatios(ticker="X", updated_at="", count=2, ratios=[
            FinancialRatioEntry(year_report=2025, length_report=3, ticker="X", pe=10.0),
            FinancialRatioEntry(year_report=2025, length_report=6, ticker="X", pe=20.0),
        ])
        result = _latest_yearly(fr)
        assert result is not None
        assert result.pe == 20.0  # fallback uses ratios[-1]

    def test_empty_ratios(self) -> None:
        fr = FinancialRatios(ticker="X", updated_at="", count=0, ratios=[])
        assert _latest_yearly(fr) is None


class TestGetFieldValue:
    def test_ratio_field(self) -> None:
        entry = FundamentalRankEntry(
            ticker="VCB",
            latest_ratio=FinancialRatioEntry(year_report=2025, length_report=12, ticker="VCB", pe=14.0),
        )
        assert _get_field_value(entry, "pe") == 14.0

    def test_company_info_field(self) -> None:
        entry = FundamentalRankEntry(
            ticker="VCB",
            company_info=CompanyInfo(symbol="VCB", market_cap=500e9),
        )
        assert _get_field_value(entry, "market_cap") == 500e9

    def test_none_entry(self) -> None:
        entry = FundamentalRankEntry(ticker="EMPTY")
        assert _get_field_value(entry, "pe") is None

    def test_none_value(self) -> None:
        entry = FundamentalRankEntry(
            ticker="VCB",
            latest_ratio=FinancialRatioEntry(year_report=2025, length_report=12, ticker="VCB"),
        )
        assert _get_field_value(entry, "npl") is None


class TestBuildFundamentalRanking:
    def test_sort_desc(self) -> None:
        client = _make_client()
        result = build_fundamental_ranking(client, ["VCB", "FPT", "HPG"], sort_by="roe", direction="desc")
        assert len(result) == 3
        assert result[0].ticker == "FPT"
        assert result[0].rank == 1
        assert result[1].ticker == "VCB"
        assert result[2].ticker == "HPG"

    def test_sort_asc(self) -> None:
        client = _make_client()
        result = build_fundamental_ranking(client, ["VCB", "FPT", "HPG"], sort_by="pe", direction="asc")
        assert result[0].ticker == "HPG"
        assert result[0].rank_value == 12.0

    def test_limit(self) -> None:
        client = _make_client()
        result = build_fundamental_ranking(client, ["VCB", "FPT", "HPG", "VIC"], sort_by="roe", limit=2)
        assert len(result) == 2

    def test_none_values_sort_last(self) -> None:
        client = _make_client()
        result = build_fundamental_ranking(client, ["VCB", "EMPTY", "NO_RATIOS"], sort_by="roe", direction="desc")
        assert result[0].ticker == "VCB"

    def test_rank_value_populated(self) -> None:
        client = _make_client()
        result = build_fundamental_ranking(client, ["VCB"], sort_by="pe")
        assert result[0].rank_value == 14.0

    def test_company_info_field_sort(self) -> None:
        client = _make_client()
        result = build_fundamental_ranking(client, ["VCB", "FPT"], sort_by="current_price", direction="desc")
        assert result[0].ticker == "FPT"
        assert result[0].rank_value == 74000.0

    def test_to_dict(self) -> None:
        client = _make_client()
        result = build_fundamental_ranking(client, ["VCB"], sort_by="pe")
        d = result[0].to_dict()
        assert d["ticker"] == "VCB"
        assert d["rank"] == 1
        assert d["rank_field"] == "pe"
        assert d["rank_value"] == 14.0
        assert d["company_info"] is not None
        assert d["latest_ratio"] is not None


class TestScreenFundamentals:
    def test_pe_filter(self) -> None:
        client = _make_client()
        result = screen_fundamentals(client, ["VCB", "FPT", "HPG", "VIC"], pe_max=15.0, sort_by="pe")
        tickers = [e.ticker for e in result]
        assert "VIC" not in tickers
        assert all(e.latest_ratio is not None and e.latest_ratio.pe <= 15.0 for e in result)

    def test_roe_min_filter(self) -> None:
        client = _make_client()
        result = screen_fundamentals(client, ["VCB", "FPT", "HPG", "VIC"], roe_min=0.15, sort_by="roe")
        tickers = [e.ticker for e in result]
        assert "HPG" not in tickers
        assert "VIC" not in tickers

    def test_industry_filter_string(self) -> None:
        client = _make_client()
        result = screen_fundamentals(client, ["VCB", "FPT", "HPG"], industry="ngân hàng")
        assert len(result) == 1
        assert result[0].ticker == "VCB"

    def test_industry_filter_list(self) -> None:
        client = _make_client()
        result = screen_fundamentals(client, ["VCB", "FPT", "HPG"], industry=["ngân hàng", "thép"])
        assert len(result) == 2

    def test_combined_filters(self) -> None:
        client = _make_client()
        result = screen_fundamentals(
            client,
            ["VCB", "FPT", "HPG", "VIC"],
            pe_max=20.0,
            roe_min=0.12,
            sort_by="roe",
            direction="desc",
        )
        tickers = [e.ticker for e in result]
        assert "VIC" not in tickers
        for e in result:
            assert e.rank_value is not None
            assert e.rank_value >= 0.12

    def test_require_data_false(self) -> None:
        client = _make_client()
        result = screen_fundamentals(
            client,
            ["VCB", "EMPTY"],
            require_data=False,
            sort_by="roe",
        )
        tickers = [e.ticker for e in result]
        assert "EMPTY" in tickers

    def test_require_data_true_excludes_empty(self) -> None:
        client = _make_client()
        result = screen_fundamentals(
            client,
            ["VCB", "EMPTY"],
            require_data=True,
            sort_by="roe",
        )
        tickers = [e.ticker for e in result]
        assert "EMPTY" not in tickers

    def test_none_value_fails_filter(self) -> None:
        client = _make_client()
        result = screen_fundamentals(
            client,
            ["FPT"],
            npl_max=0.02,
            sort_by="roe",
        )
        tickers = [e.ticker for e in result]
        assert "FPT" not in tickers

    def test_limit(self) -> None:
        client = _make_client()
        result = screen_fundamentals(client, ["VCB", "FPT", "HPG", "VIC"], sort_by="roe", limit=2)
        assert len(result) == 2

    def test_dividend_yield_filter(self) -> None:
        client = _make_client()
        result = screen_fundamentals(
            client,
            ["VCB", "FPT", "HPG", "VIC"],
            dividend_yield_min=0.025,
            sort_by="dividend_yield",
            direction="desc",
        )
        tickers = [e.ticker for e in result]
        assert "VCB" in tickers
        assert "FPT" in tickers
        assert "HPG" not in tickers

    def test_debt_to_equity_max(self) -> None:
        client = _make_client()
        result = screen_fundamentals(
            client,
            ["VCB", "FPT", "HPG", "VIC"],
            debt_to_equity_max=1.0,
            sort_by="roe",
        )
        tickers = [e.ticker for e in result]
        assert "VIC" not in tickers
        assert "HPG" in tickers  # 0.97 < 1.0
        assert "VCB" not in tickers  # None excluded
