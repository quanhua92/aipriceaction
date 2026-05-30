from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import AIPriceAction
from .fundamental import CompanyInfo, FinancialRatioEntry, FinancialRatios
from .performers import _SortAscNoneLast, _SortDescNoneLast

_RANKABLE_FIELDS: dict[str, str] = {
    "pe": "pe",
    "pb": "pb",
    "ps": "ps",
    "ev_to_ebitda": "ev_to_ebitda",
    "price_to_cash_flow": "price_to_cash_flow",
    "dividend_yield": "dividend_yield",
    "market_cap": "market_cap",
    "roe": "roe",
    "roa": "roa",
    "roic": "roic",
    "gross_margin": "gross_margin",
    "after_tax_profit_margin": "after_tax_profit_margin",
    "pre_tax_profit_margin": "pre_tax_profit_margin",
    "ebit_margin": "ebit_margin",
    "net_interest_margin": "net_interest_margin",
    "ebit": "ebit",
    "ebitda": "ebitda",
    "asset_turnover": "asset_turnover",
    "fixed_asset_turnover": "fixed_asset_turnover",
    "debt_to_equity": "debt_to_equity",
    "debt_per_equity": "debt_per_equity",
    "financial_leverage": "financial_leverage",
    "equity_to_liabilities": "equity_to_liabilities",
    "equity_to_loans": "equity_to_loans",
    "total_equity_total_asset": "total_equity_total_asset",
    "owners_equity": "owners_equity",
    "equity": "equity",
    "current_ratio": "current_ratio",
    "quick_ratio": "quick_ratio",
    "cash_ratio": "cash_ratio",
    "cash_cycle": "cash_cycle",
    "day_sale_outstanding": "day_sale_outstanding",
    "days_inventory_outstanding": "days_inventory_outstanding",
    "days_payable_outstanding": "days_payable_outstanding",
    "npl": "npl",
    "ldr_loan_deposit_ratio": "ldr_loan_deposit_ratio",
    "car": "car",
    "casa_ratio": "casa_ratio",
    "cir": "cir",
    "cost_to_income": "cost_to_income",
    "non_and_interest_income": "non_and_interest_income",
    "deposit_growth": "deposit_growth",
    "loans_growth": "loans_growth",
    "loans_loss_reserve_to_loans": "loans_loss_reserve_to_loans",
    "loans_loss_reserves_to_npl": "loans_loss_reserves_to_npl",
    "provision_to_outstanding_loans": "provision_to_outstanding_loans",
    "average_cost_of_financing": "average_cost_of_financing",
    "average_yield_on_earning_assets": "average_yield_on_earning_assets",
    "outstanding_shares": "outstanding_shares",
    "employees": "employees",
    "current_price": "current_price",
}

_COMPANY_INFO_FIELDS: frozenset[str] = frozenset({
    "outstanding_shares", "employees", "current_price", "market_cap",
})


@dataclass
class FundamentalRankEntry:
    ticker: str
    industry: str | None = None
    company_info: CompanyInfo | None = None
    financial_ratios: FinancialRatios | None = None
    latest_ratio: FinancialRatioEntry | None = None
    rank_value: float | None = None
    rank_field: str | None = None
    rank: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "industry": self.industry,
            "company_info": self.company_info.to_dict() if self.company_info else None,
            "financial_ratios": self.financial_ratios.to_dict() if self.financial_ratios else None,
            "latest_ratio": self.latest_ratio.to_dict() if self.latest_ratio else None,
            "rank_value": self.rank_value,
            "rank_field": self.rank_field,
            "rank": self.rank,
        }


def _latest_yearly(fr: FinancialRatios) -> FinancialRatioEntry | None:
    yearly = [r for r in fr.ratios if r.length_report == 12]
    if not yearly:
        return fr.ratios[-1] if fr.ratios else None
    return max(yearly, key=lambda r: r.year_report)


def _get_field_value(
    entry: FundamentalRankEntry,
    field: str,
) -> float | None:
    if field in _COMPANY_INFO_FIELDS:
        if entry.company_info is None:
            return None
        raw = getattr(entry.company_info, field, None)
    elif entry.latest_ratio is not None:
        raw = getattr(entry.latest_ratio, field, None)
    else:
        return None

    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def build_fundamental_ranking(
    client: AIPriceAction,
    tickers: list[str],
    *,
    sort_by: str = "roe",
    direction: str = "desc",
    limit: int = 10,
    source: str | None = None,
    yearly_only: bool = True,
) -> list[FundamentalRankEntry]:
    limit = max(1, min(limit, 200))

    attr = _RANKABLE_FIELDS.get(sort_by, "roe")

    entries: list[FundamentalRankEntry] = []
    for ticker in tickers:
        ci, fr = client.get_fundamental(ticker, source=source)

        latest = None
        if fr is not None and fr.ratios:
            if yearly_only:
                latest = _latest_yearly(fr)
            else:
                latest = fr.ratios[-1]

        entry = FundamentalRankEntry(
            ticker=ticker,
            industry=ci.industry if ci else None,
            company_info=ci,
            financial_ratios=fr,
            latest_ratio=latest,
            rank_field=attr,
        )
        entry.rank_value = _get_field_value(entry, attr)
        entries.append(entry)

    sort_cls = _SortDescNoneLast if direction == "desc" else _SortAscNoneLast
    entries.sort(key=lambda e: sort_cls(e.rank_value))

    for i, entry in enumerate(entries):
        entry.rank = i + 1

    return entries[:limit]


def screen_fundamentals(
    client: AIPriceAction,
    tickers: list[str],
    *,
    sort_by: str = "roe",
    direction: str = "desc",
    limit: int = 50,
    source: str | None = None,
    yearly_only: bool = True,
    pe_max: float | None = None,
    pe_min: float | None = None,
    pb_max: float | None = None,
    pb_min: float | None = None,
    roe_min: float | None = None,
    roe_max: float | None = None,
    roa_min: float | None = None,
    roa_max: float | None = None,
    dividend_yield_min: float | None = None,
    dividend_yield_max: float | None = None,
    debt_to_equity_max: float | None = None,
    debt_to_equity_min: float | None = None,
    current_ratio_min: float | None = None,
    current_ratio_max: float | None = None,
    gross_margin_min: float | None = None,
    gross_margin_max: float | None = None,
    npl_max: float | None = None,
    npl_min: float | None = None,
    cir_max: float | None = None,
    cir_min: float | None = None,
    car_min: float | None = None,
    car_max: float | None = None,
    market_cap_min: float | None = None,
    market_cap_max: float | None = None,
    industry: str | list[str] | None = None,
    require_data: bool = True,
) -> list[FundamentalRankEntry]:
    limit = max(1, min(limit, 500))

    attr = _RANKABLE_FIELDS.get(sort_by, "roe")

    filter_ranges: dict[str, tuple[float | None, float | None]] = {
        "pe": (pe_min, pe_max),
        "pb": (pb_min, pb_max),
        "roe": (roe_min, roe_max),
        "roa": (roa_min, roa_max),
        "dividend_yield": (dividend_yield_min, dividend_yield_max),
        "debt_to_equity": (debt_to_equity_min, debt_to_equity_max),
        "current_ratio": (current_ratio_min, current_ratio_max),
        "gross_margin": (gross_margin_min, gross_margin_max),
        "npl": (npl_min, npl_max),
        "cir": (cir_min, cir_max),
        "car": (car_min, car_max),
        "market_cap": (market_cap_min, market_cap_max),
    }

    industries: set[str] | None = None
    if industry is not None:
        if isinstance(industry, str):
            industries = {industry.lower()}
        else:
            industries = {i.lower() for i in industry}

    entries: list[FundamentalRankEntry] = []
    for ticker in tickers:
        ci, fr = client.get_fundamental(ticker, source=source)

        if require_data and ci is None and fr is None:
            continue

        latest = None
        if fr is not None and fr.ratios:
            if yearly_only:
                latest = _latest_yearly(fr)
            else:
                latest = fr.ratios[-1]

        entry = FundamentalRankEntry(
            ticker=ticker,
            industry=ci.industry if ci else None,
            company_info=ci,
            financial_ratios=fr,
            latest_ratio=latest,
            rank_field=attr,
        )
        entry.rank_value = _get_field_value(entry, attr)

        if industries is not None:
            if entry.industry is None or entry.industry.lower() not in industries:
                continue

        passed = True
        for field_name, (lo, hi) in filter_ranges.items():
            if lo is None and hi is None:
                continue
            val = _get_field_value(entry, field_name)
            if val is None:
                passed = False
                break
            if lo is not None and val < lo:
                passed = False
                break
            if hi is not None and val > hi:
                passed = False
                break

        if not passed:
            continue

        entries.append(entry)

    sort_cls = _SortDescNoneLast if direction == "desc" else _SortAscNoneLast
    entries.sort(key=lambda e: sort_cls(e.rank_value))

    for i, entry in enumerate(entries):
        entry.rank = i + 1

    return entries[:limit]
