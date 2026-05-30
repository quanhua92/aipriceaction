from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ShareholderInfo:
    name: str
    percentage: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "percentage": self.percentage}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ShareholderInfo:
        return cls(
            name=d.get("name", ""),
            percentage=d.get("percentage"),
        )


@dataclass
class OfficerInfo:
    name: str
    position: str
    percentage: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "position": self.position, "percentage": self.percentage}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OfficerInfo:
        return cls(
            name=d.get("name", ""),
            position=d.get("position", ""),
            percentage=d.get("percentage"),
        )


@dataclass
class CompanyInfo:
    symbol: str
    exchange: str | None = None
    industry: str | None = None
    company_type: str | None = None
    established_year: int | None = None
    employees: int | None = None
    market_cap: float | None = None
    current_price: float | None = None
    outstanding_shares: int | None = None
    company_profile: str | None = None
    website: str | None = None
    shareholders: list[ShareholderInfo] = field(default_factory=list)
    officers: list[OfficerInfo] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "industry": self.industry,
            "company_type": self.company_type,
            "established_year": self.established_year,
            "employees": self.employees,
            "market_cap": self.market_cap,
            "current_price": self.current_price,
            "outstanding_shares": self.outstanding_shares,
            "company_profile": self.company_profile,
            "website": self.website,
            "shareholders": [s.to_dict() for s in self.shareholders],
            "officers": [o.to_dict() for o in self.officers],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CompanyInfo:
        shareholders = [
            ShareholderInfo.from_dict(s) for s in d.get("shareholders", [])
        ]
        officers = [OfficerInfo.from_dict(o) for o in d.get("officers", [])]
        return cls(
            symbol=d.get("symbol", ""),
            exchange=d.get("exchange"),
            industry=d.get("industry"),
            company_type=d.get("company_type"),
            established_year=d.get("established_year"),
            employees=d.get("employees"),
            market_cap=d.get("market_cap"),
            current_price=d.get("current_price"),
            outstanding_shares=d.get("outstanding_shares"),
            company_profile=d.get("company_profile"),
            website=d.get("website"),
            shareholders=shareholders,
            officers=officers,
        )


_KNOWN_RATIO_KEYS: frozenset[str] = frozenset({
    "yearReport", "lengthReport", "ticker", "organCode",
    "ratioType", "ratioTTMId", "ratioYearId",
    "pe", "pb", "ps", "evToEbitda", "priceToCashFlow", "dividendYield",
    "marketCap", "numberOfSharesMktCap",
    "roe", "roa", "roic",
    "grossMargin", "afterTaxProfitMargin", "preTaxProfitMargin",
    "ebitMargin", "netInterestMargin", "ebit", "ebitda",
    "assetTurnover", "fixedAssetTurnover",
    "debtToEquity", "debtPerEquity", "financialLeverage",
    "equityToLiabilities", "equityToLoans", "totalEquityTotalAsset",
    "ownersEquity",
    "currentRatio", "quickRatio", "cashRatio",
    "cashCycle", "daySaleOutstanding",
    "daysInventoryOutstanding", "daysPayableOutstanding",
    "npl", "ldrLoanDepositRatio", "car", "casaRatio",
    "cir", "costToIncome", "nonAndInterestIncome",
    "depositGrowth", "loansGrowth",
    "loansLossReserveToLoans", "loansLossReservesToNPLs",
    "provisionToOutstandingLoans",
    "averageCostOfFinancing", "averageYieldOnEarningAssets",
    "bsb113", "nob66", "nob69", "nob70",
    "equity",
})

_CAMEL_TO_SNAKE: dict[str, str] = {
    "yearReport": "year_report",
    "lengthReport": "length_report",
    "evToEbitda": "ev_to_ebitda",
    "priceToCashFlow": "price_to_cash_flow",
    "dividendYield": "dividend_yield",
    "marketCap": "market_cap",
    "numberOfSharesMktCap": "number_of_shares_mkt_cap",
    "grossMargin": "gross_margin",
    "afterTaxProfitMargin": "after_tax_profit_margin",
    "preTaxProfitMargin": "pre_tax_profit_margin",
    "ebitMargin": "ebit_margin",
    "netInterestMargin": "net_interest_margin",
    "assetTurnover": "asset_turnover",
    "fixedAssetTurnover": "fixed_asset_turnover",
    "debtToEquity": "debt_to_equity",
    "debtPerEquity": "debt_per_equity",
    "financialLeverage": "financial_leverage",
    "equityToLiabilities": "equity_to_liabilities",
    "equityToLoans": "equity_to_loans",
    "totalEquityTotalAsset": "total_equity_total_asset",
    "ownersEquity": "owners_equity",
    "currentRatio": "current_ratio",
    "quickRatio": "quick_ratio",
    "cashRatio": "cash_ratio",
    "cashCycle": "cash_cycle",
    "daySaleOutstanding": "day_sale_outstanding",
    "daysInventoryOutstanding": "days_inventory_outstanding",
    "daysPayableOutstanding": "days_payable_outstanding",
    "ldrLoanDepositRatio": "ldr_loan_deposit_ratio",
    "casaRatio": "casa_ratio",
    "costToIncome": "cost_to_income",
    "nonAndInterestIncome": "non_and_interest_income",
    "depositGrowth": "deposit_growth",
    "loansGrowth": "loans_growth",
    "loansLossReserveToLoans": "loans_loss_reserve_to_loans",
    "loansLossReservesToNPLs": "loans_loss_reserves_to_npl",
    "provisionToOutstandingLoans": "provision_to_outstanding_loans",
    "averageCostOfFinancing": "average_cost_of_financing",
    "averageYieldOnEarningAssets": "average_yield_on_earning_assets",
    "organCode": "organ_code",
    "ratioType": "ratio_type",
    "ratioTTMId": "ratio_ttm_id",
    "ratioYearId": "ratio_year_id",
}


@dataclass
class FinancialRatioEntry:
    year_report: int
    length_report: int
    ticker: str
    pe: float | None = None
    pb: float | None = None
    ps: float | None = None
    ev_to_ebitda: float | None = None
    price_to_cash_flow: float | None = None
    dividend_yield: float | None = None
    market_cap: float | None = None
    number_of_shares_mkt_cap: float | None = None
    roe: float | None = None
    roa: float | None = None
    roic: float | None = None
    gross_margin: float | None = None
    after_tax_profit_margin: float | None = None
    pre_tax_profit_margin: float | None = None
    ebit_margin: float | None = None
    net_interest_margin: float | None = None
    ebit: float | None = None
    ebitda: float | None = None
    asset_turnover: float | None = None
    fixed_asset_turnover: float | None = None
    debt_to_equity: float | None = None
    debt_per_equity: float | None = None
    financial_leverage: float | None = None
    equity_to_liabilities: float | None = None
    equity_to_loans: float | None = None
    total_equity_total_asset: float | None = None
    owners_equity: float | None = None
    current_ratio: float | None = None
    quick_ratio: float | None = None
    cash_ratio: float | None = None
    cash_cycle: float | None = None
    day_sale_outstanding: float | None = None
    days_inventory_outstanding: float | None = None
    days_payable_outstanding: float | None = None
    npl: float | None = None
    ldr_loan_deposit_ratio: float | None = None
    car: float | None = None
    casa_ratio: float | None = None
    cir: float | None = None
    cost_to_income: float | None = None
    non_and_interest_income: float | None = None
    deposit_growth: float | None = None
    loans_growth: float | None = None
    loans_loss_reserve_to_loans: float | None = None
    loans_loss_reserves_to_npl: float | None = None
    provision_to_outstanding_loans: float | None = None
    average_cost_of_financing: float | None = None
    average_yield_on_earning_assets: float | None = None
    bsb113: float | None = None
    nob66: float | None = None
    nob69: float | None = None
    nob70: float | None = None
    equity: float | None = None
    organ_code: str | None = None
    ratio_type: str | None = None
    ratio_ttm_id: float | None = None
    ratio_year_id: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        _SNAKE_TO_CAMEL = {v: k for k, v in _CAMEL_TO_SNAKE.items()}
        result: dict[str, Any] = {}
        for f_name in (
            "year_report", "length_report", "ticker",
            "pe", "pb", "ps", "ev_to_ebitda", "price_to_cash_flow",
            "dividend_yield", "market_cap", "number_of_shares_mkt_cap",
            "roe", "roa", "roic", "gross_margin", "after_tax_profit_margin",
            "pre_tax_profit_margin", "ebit_margin", "net_interest_margin",
            "ebit", "ebitda", "asset_turnover", "fixed_asset_turnover",
            "debt_to_equity", "debt_per_equity", "financial_leverage",
            "equity_to_liabilities", "equity_to_loans", "total_equity_total_asset",
            "owners_equity", "current_ratio", "quick_ratio", "cash_ratio",
            "cash_cycle", "day_sale_outstanding", "days_inventory_outstanding",
            "days_payable_outstanding", "npl", "ldr_loan_deposit_ratio",
            "car", "casa_ratio", "cir", "cost_to_income",
            "non_and_interest_income", "deposit_growth", "loans_growth",
            "loans_loss_reserve_to_loans", "loans_loss_reserves_to_npl",
            "provision_to_outstanding_loans", "average_cost_of_financing",
            "average_yield_on_earning_assets", "bsb113", "nob66", "nob69",
            "nob70", "equity", "organ_code", "ratio_type",
            "ratio_ttm_id", "ratio_year_id",
        ):
            val = getattr(self, f_name)
            if val is not None and val != [] and val != {}:
                camel = _SNAKE_TO_CAMEL.get(f_name, f_name)
                result[camel] = val
        if self.extra:
            result.update(self.extra)
        return result

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FinancialRatioEntry:
        known: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for k, v in d.items():
            if k in _KNOWN_RATIO_KEYS:
                snake = _CAMEL_TO_SNAKE.get(k, k)
                known[snake] = v
            else:
                extra[k] = v
        return cls(
            year_report=known.get("year_report", known.get("yearReport", 0)),
            length_report=known.get("length_report", known.get("lengthReport", 0)),
            ticker=known.get("ticker", ""),
            **{k: v for k, v in known.items() if k not in ("year_report", "length_report", "ticker", "yearReport", "lengthReport")},
            extra=extra,
        )


@dataclass
class FinancialRatios:
    ticker: str
    updated_at: str
    count: int
    ratios: list[FinancialRatioEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "updated_at": self.updated_at,
            "count": self.count,
            "ratios": [r.to_dict() for r in self.ratios],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FinancialRatios:
        ratios = [FinancialRatioEntry.from_dict(r) for r in d.get("ratios", [])]
        return cls(
            ticker=d.get("ticker", ""),
            updated_at=d.get("updated_at", ""),
            count=d.get("count", 0),
            ratios=ratios,
        )
