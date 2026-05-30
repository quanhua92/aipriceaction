"""Fundamental data demo — company info, financial ratios, screening & advanced analysis.

Run:
    uv run python examples/fundamental_demo.py

Covers every scenario:
  1. Basic: fetch company info for a single ticker
  2. Basic: fetch financial ratios for a single ticker
  3. Combined: get_fundamental() returns both at once
  4. Missing data: tickers with no fundamental data (indices, etc.)
  5. Company info deep dive: shareholders, officers, profile
  6. Financial ratios deep dive: all typed fields + extra legacy fields
  7. Year-over-year trend analysis for a single metric
  8. Bank-specific ratios: NPL, CIR, CAR, CASA, LDR
  9. Multi-ticker PE/PB/ROE screening & ranking
 10. Sector-wide fundamental comparison
 11. Dividend yield ranking
 12. Valuation scatter: PE vs ROE to find undervalued stocks
 13. Serialization roundtrip: to_dict / from_dict
 14. Caching behavior: second call is instant (no network)
"""

from aipriceaction import (
    AIPriceAction,
    CompanyInfo,
    FinancialRatioEntry,
    FinancialRatios,
    OfficerInfo,
    ShareholderInfo,
)

SEPARATOR = "=" * 72
THIN = "-" * 72

BANK_TICKERS = ["VCB", "BID", "CTG", "TCB", "MBB", "ACB", "VPB", "HDB", "SHB", "TPB"]
TECH_TICKERS = ["FPT", "CMG", "ELC", "ITD", "VGI"]
BLUECHIP_TICKERS = ["VCB", "FPT", "HPG", "VIC", "VNM", "GAS", "MWG", "MSN", "PLX", "SAB"]


def section(num: int, title: str) -> None:
    print(f"\n{SEPARATOR}")
    print(f"  Section {num}: {title}")
    print(SEPARATOR)


def _fmt(v: float | None, fmt: str = ",.2f") -> str:
    if v is None:
        return "N/A"
    return f"{v:{fmt}}"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v * 100:.2f}%"


def _latest_yearly(fr: FinancialRatios) -> FinancialRatioEntry | None:
    yearly = [r for r in fr.ratios if r.length_report == 12]
    if not yearly:
        return fr.ratios[0] if fr.ratios else None
    return max(yearly, key=lambda r: r.year_report)


# ── 1. Basic: Company Info ──────────────────────────────────────────────────


def demo_1_basic_company_info(client: AIPriceAction) -> None:
    section(1, "Basic: Company Info for VCB")

    ci = client.get_company_info("VCB", source="vn")
    if ci is None:
        print("  No data found")
        return

    print(f"  Symbol:             {ci.symbol}")
    print(f"  Exchange:           {ci.exchange}")
    print(f"  Industry:           {ci.industry}")
    print(f"  Employees:          {_fmt(ci.employees, ',')}")
    print(f"  Market Cap:         {_fmt(ci.market_cap, ',.0f')} VND")
    print(f"  Current Price:      {_fmt(ci.current_price, ',.0f')} VND")
    print(f"  Outstanding Shares: {_fmt(ci.outstanding_shares, ',')}")
    print(f"  Website:            {ci.website or 'N/A'}")


# ── 2. Basic: Financial Ratios ──────────────────────────────────────────────


def demo_2_basic_financial_ratios(client: AIPriceAction) -> None:
    section(2, "Basic: Financial Ratios for VCB")

    fr = client.get_financial_ratios("VCB", source="vn")
    if fr is None:
        print("  No data found")
        return

    print(f"  Ticker:     {fr.ticker}")
    print(f"  Updated:    {fr.updated_at}")
    print(f"  Entries:    {fr.count}")

    latest = _latest_yearly(fr)
    if latest is None:
        return

    print(f"\n  Latest yearly report: year={latest.year_report} length={latest.length_report}")
    print(f"    PE:   {_fmt(latest.pe)}")
    print(f"    PB:   {_fmt(latest.pb)}")
    print(f"    PS:   {_fmt(latest.ps)}")
    print(f"    ROE:  {_fmt_pct(latest.roe)}")
    print(f"    ROA:  {_fmt_pct(latest.roa)}")
    print(f"    Div Yield: {_fmt_pct(latest.dividend_yield)}")


# ── 3. Combined: get_fundamental() ──────────────────────────────────────────


def demo_3_combined(client: AIPriceAction) -> None:
    section(3, "Combined: get_fundamental() returns both")

    ci, fr = client.get_fundamental("FPT", source="vn")
    print(f"  CompanyInfo:     {'OK — ' + ci.symbol if ci else 'None'}")
    print(f"  FinancialRatios: {'OK — ' + fr.ticker + ' (' + str(fr.count) + ' entries)' if fr else 'None'}")

    if ci and fr:
        latest = _latest_yearly(fr)
        print(f"\n  {ci.symbol} ({ci.industry})")
        print(f"  Price: {_fmt(ci.current_price, ',.0f')} VND")
        if latest:
            print(f"  PE: {_fmt(latest.pe)}  |  ROE: {_fmt_pct(latest.roe)}  |  Margin: {_fmt_pct(latest.gross_margin)}")


# ── 4. Missing Data ─────────────────────────────────────────────────────────


def demo_4_missing_data(client: AIPriceAction) -> None:
    section(4, "Missing Data: indices, unknown tickers")

    for ticker in ["VNINDEX", "VN30", "ZZZZZ"]:
        ci, fr = client.get_fundamental(ticker, source="vn")
        print(f"  {ticker:10s}  company_info={type(ci).__name__ if ci else 'None':>11s}  ratios={type(fr).__name__ if fr else 'None':>11s}")


# ── 5. Company Info Deep Dive ───────────────────────────────────────────────


def demo_5_company_deep_dive(client: AIPriceAction) -> None:
    section(5, "Company Info Deep Dive: Shareholders & Officers")

    ci = client.get_company_info("ACB", source="vn")
    if ci is None:
        print("  No data")
        return

    print(f"  {ci.symbol} — {ci.industry}")
    print(THIN)

    print(f"\n  Top 10 Shareholders ({len(ci.shareholders)} total):")
    for s in sorted(ci.shareholders, key=lambda x: x.percentage or 0, reverse=True)[:10]:
        pct = f"{s.percentage * 100:.2f}%" if s.percentage is not None else "N/A"
        print(f"    {s.name:45s} {pct:>8s}")

    print(f"\n  Officers ({len(ci.officers)} total):")
    for o in ci.officers:
        pct = f" ({o.percentage * 100:.2f}% ownership)" if o.percentage else ""
        print(f"    {o.name:35s}  {o.position}{pct}")

    if ci.company_profile:
        clean = ci.company_profile.replace("\n", " ").replace("\r", " ")
        snippet = clean[:200].strip()
        print(f"\n  Profile snippet: {snippet}...")


# ── 6. Financial Ratios Deep Dive: All Fields ───────────────────────────────


def demo_6_ratios_deep_dive(client: AIPriceAction) -> None:
    section(6, "Financial Ratios Deep Dive: All Typed Fields + Extra")

    fr = client.get_financial_ratios("ACB", source="vn")
    if fr is None:
        print("  No data")
        return

    latest = _latest_yearly(fr)
    if latest is None:
        print("  No ratio entries")
        return

    print(f"  {latest.ticker} — year={latest.year_report} length={latest.length_report}")
    print(THIN)

    valuation_fields = [
        ("PE", latest.pe), ("PB", latest.pb), ("PS", latest.ps),
        ("EV/EBITDA", latest.ev_to_ebitda), ("Price/CashFlow", latest.price_to_cash_flow),
        ("Dividend Yield", latest.dividend_yield),
        ("Market Cap", latest.market_cap),
    ]
    print("\n  Valuation:")
    for label, val in valuation_fields:
        print(f"    {label:25s} {_fmt(val)}")

    profitability_fields = [
        ("ROE", latest.roe), ("ROA", latest.roa), ("ROIC", latest.roic),
        ("Gross Margin", latest.gross_margin), ("After-Tax Margin", latest.after_tax_profit_margin),
        ("Pre-Tax Margin", latest.pre_tax_profit_margin), ("EBIT Margin", latest.ebit_margin),
        ("Net Interest Margin", latest.net_interest_margin),
        ("EBIT", latest.ebit), ("EBITDA", latest.ebitda),
    ]
    print("\n  Profitability:")
    for label, val in profitability_fields:
        print(f"    {label:25s} {_fmt(val)}")

    efficiency_fields = [
        ("Asset Turnover", latest.asset_turnover), ("Fixed Asset Turnover", latest.fixed_asset_turnover),
        ("Cash Cycle", latest.cash_cycle), ("DSO", latest.day_sale_outstanding),
        ("DIO", latest.days_inventory_outstanding), ("DPO", latest.days_payable_outstanding),
    ]
    print("\n  Efficiency:")
    for label, val in efficiency_fields:
        print(f"    {label:25s} {_fmt(val)}")

    leverage_fields = [
        ("Debt/Equity", latest.debt_to_equity), ("Debt per Equity", latest.debt_per_equity),
        ("Financial Leverage", latest.financial_leverage),
        ("Equity/Liabilities", latest.equity_to_liabilities), ("Equity/Loans", latest.equity_to_loans),
        ("Equity/Total Asset", latest.total_equity_total_asset),
        ("Owners Equity", latest.owners_equity), ("Equity", latest.equity),
    ]
    print("\n  Leverage & Capital:")
    for label, val in leverage_fields:
        print(f"    {label:25s} {_fmt(val)}")

    liquidity_fields = [
        ("Current Ratio", latest.current_ratio), ("Quick Ratio", latest.quick_ratio),
        ("Cash Ratio", latest.cash_ratio),
    ]
    print("\n  Liquidity:")
    for label, val in liquidity_fields:
        print(f"    {label:25s} {_fmt(val)}")

    bank_fields = [
        ("NPL", latest.npl), ("LDR", latest.ldr_loan_deposit_ratio),
        ("CAR", latest.car), ("CASA Ratio", latest.casa_ratio),
        ("CIR", latest.cir), ("Cost/Income", latest.cost_to_income),
        ("Non-Interest Income", latest.non_and_interest_income),
        ("Deposit Growth", latest.deposit_growth), ("Loans Growth", latest.loans_growth),
        ("LLR/Loans", latest.loans_loss_reserve_to_loans),
        ("LLR/NPL", latest.loans_loss_reserves_to_npl),
        ("Provision/Loans", latest.provision_to_outstanding_loans),
        ("Avg Cost of Financing", latest.average_cost_of_financing),
        ("Avg Yield Earning Assets", latest.average_yield_on_earning_assets),
    ]
    has_bank = any(v is not None for _, v in bank_fields)
    if has_bank:
        print("\n  Bank-Specific:")
        for label, val in bank_fields:
            print(f"    {label:25s} {_fmt(val)}")

    if latest.extra:
        print(f"\n  Extra/Legacy fields ({len(latest.extra)}):")
        keys_sample = sorted(latest.extra.keys())[:15]
        for k in keys_sample:
            v = latest.extra[k]
            print(f"    {k:25s} {v}")
        if len(latest.extra) > 15:
            print(f"    ... and {len(latest.extra) - 15} more")


# ── 7. Year-over-Year Trend ─────────────────────────────────────────────────


def demo_7_yoy_trend(client: AIPriceAction) -> None:
    section(7, "Year-over-Year Trend: ROE for VCB")

    fr = client.get_financial_ratios("VCB", source="vn")
    if fr is None:
        print("  No data")
        return

    yearly = sorted(
        [r for r in fr.ratios if r.length_report == 12 and r.roe is not None],
        key=lambda r: r.year_report,
    )
    if not yearly:
        yearly = sorted(
            [r for r in fr.ratios if r.roe is not None],
            key=lambda r: r.year_report,
        )

    print(f"  VCB ROE trend ({len(yearly)} yearly reports):")
    print(THIN)
    print(f"  {'Year':>6s}  {'ROE':>8s}  {'Change':>8s}  Bar")
    print(THIN)

    prev_roe = None
    for r in yearly:
        roe_pct = r.roe * 100 if r.roe else 0
        change = ""
        if prev_roe is not None and r.roe is not None:
            delta = (r.roe - prev_roe) * 100
            change = f"{'+' if delta >= 0 else ''}{delta:.1f}%"
        bar = "#" * int(roe_pct)
        print(f"  {r.year_report:>6d}  {roe_pct:>7.1f}%  {change:>8s}  {bar}")
        prev_roe = r.roe


# ── 8. Bank-Specific Ratios ─────────────────────────────────────────────────


def demo_8_bank_ratios(client: AIPriceAction) -> None:
    section(8, "Bank-Specific Ratios: NPL, CIR, CAR, CASA")

    print(f"  {'Ticker':>6s}  {'PE':>7s}  {'PB':>7s}  {'ROE':>7s}  {'NPL':>7s}  {'CIR':>7s}  {'CAR':>7s}  {'CASA':>7s}  {'LDR':>7s}")
    print(THIN)

    for ticker in BANK_TICKERS:
        fr = client.get_financial_ratios(ticker, source="vn")
        if fr is None:
            print(f"  {ticker:>6s}  {'—':>7s}  {'—':>7s}  {'—':>7s}  {'—':>7s}  {'—':>7s}  {'—':>7s}  {'—':>7s}  {'—':>7s}")
            continue
        latest = _latest_yearly(fr)
        if latest is None:
            continue
        print(
            f"  {ticker:>6s}  {_fmt(latest.pe):>7s}  {_fmt(latest.pb):>7s}  "
            f"{_fmt_pct(latest.roe):>7s}  {_fmt_pct(latest.npl):>7s}  "
            f"{_fmt_pct(latest.cir):>7s}  {_fmt_pct(latest.car):>7s}  "
            f"{_fmt_pct(latest.casa_ratio):>7s}  {_fmt(latest.ldr_loan_deposit_ratio):>7s}"
        )


# ── 9. Multi-Ticker PE/PB/ROE Screening ─────────────────────────────────────


def demo_9_screening(client: AIPriceAction) -> None:
    section(9, "Multi-Ticker Screening: Top PE, PB, ROE")

    tickers = BANK_TICKERS + TECH_TICKERS + ["HPG", "VIC", "VNM", "GAS", "MWG"]
    print(f"  Scanning {len(tickers)} tickers...")
    rows = []
    for i, ticker in enumerate(tickers):
        ci, fr = client.get_fundamental(ticker, source="vn")
        if ci is None or fr is None:
            print(f"    [{i + 1}/{len(tickers)}] {ticker} — no data")
            continue
        latest = _latest_yearly(fr)
        if latest is None:
            print(f"    [{i + 1}/{len(tickers)}] {ticker} — no ratios")
            continue
        print(f"    [{i + 1}/{len(tickers)}] {ticker} — PE={_fmt(latest.pe)}")
        rows.append({
            "ticker": ticker,
            "industry": ci.industry or "",
            "pe": latest.pe,
            "pb": latest.pb,
            "roe": latest.roe,
            "roa": latest.roa,
            "dividend_yield": latest.dividend_yield,
            "debt_to_equity": latest.debt_to_equity,
            "market_cap": ci.market_cap,
        })

    def table(title: str, key: str, asc: bool = True, limit: int = 10) -> None:
        sorted_rows = sorted(rows, key=lambda r: r.get(key) or float("inf"), reverse=not asc)
        print(f"\n  {title} (top {limit}):")
        print(f"  {'Ticker':>6s}  {'Industry':>20s}  {key:>15s}")
        print(THIN)
        for r in sorted_rows[:limit]:
            val = r[key]
            print(f"  {r['ticker']:>6s}  {r['industry']:>20s}  {_fmt(val):>15s}")

    table("Lowest PE (cheapest by earnings)", "pe", asc=True)
    table("Highest PE (expensive by earnings)", "pe", asc=False)
    table("Lowest PB (cheapest by book value)", "pb", asc=True)
    table("Highest ROE (most profitable)", "roe", asc=False)
    table("Highest Dividend Yield", "dividend_yield", asc=False)


# ── 10. Sector-Wide Comparison ──────────────────────────────────────────────


def demo_10_sector_comparison(client: AIPriceAction) -> None:
    section(10, "Sector-Wide Fundamental Comparison")

    sectors = {
        "Banking": BANK_TICKERS,
        "Tech": TECH_TICKERS,
        "Blue-chip": BLUECHIP_TICKERS,
    }

    for sector_name, tickers in sectors.items():
        print(f"\n  {sector_name} sector:")
        print(f"  {'Ticker':>6s}  {'PE':>7s}  {'PB':>7s}  {'ROE':>7s}  {'D/E':>7s}  {'Margin':>7s}  {'DivY':>7s}")
        print(THIN)

        sector_rows = []
        for ticker in tickers:
            ci, fr = client.get_fundamental(ticker, source="vn")
            if fr is None:
                continue
            latest = _latest_yearly(fr)
            if latest is None:
                continue
            margin = latest.gross_margin or latest.after_tax_profit_margin or latest.net_interest_margin
            sector_rows.append({
                "ticker": ticker,
                "pe": latest.pe,
                "pb": latest.pb,
                "roe": latest.roe,
                "de": latest.debt_to_equity,
                "margin": margin,
                "div": latest.dividend_yield,
            })

        for r in sorted(sector_rows, key=lambda x: x.get("roe") or 0, reverse=True):
            print(
                f"  {r['ticker']:>6s}  {_fmt(r['pe']):>7s}  {_fmt(r['pb']):>7s}  "
                f"{_fmt_pct(r['roe']):>7s}  {_fmt(r['de']):>7s}  {_fmt_pct(r['margin']):>7s}  "
                f"{_fmt_pct(r['div']):>7s}"
            )

        valid_roe = [r["roe"] for r in sector_rows if r["roe"] is not None]
        valid_pe = [r["pe"] for r in sector_rows if r["pe"] is not None]
        if valid_roe:
            print(f"  Avg ROE: {sum(valid_roe) / len(valid_roe) * 100:.1f}%  |  Avg PE: {sum(valid_pe) / len(valid_pe):.1f}" if valid_pe else "")


# ── 11. Dividend Yield Ranking ──────────────────────────────────────────────


def demo_11_dividend_ranking(client: AIPriceAction) -> None:
    section(11, "Dividend Yield Ranking")

    all_tickers = client.get_tickers(source="vn")
    scan_count = 30
    vn_tickers = [t.ticker for t in all_tickers[:scan_count]]
    print(f"  Scanning first {scan_count} VN tickers...")

    dividend_data = []
    for i, ticker in enumerate(vn_tickers):
        fr = client.get_financial_ratios(ticker, source="vn")
        if fr is None:
            print(f"    [{i + 1}/{scan_count}] {ticker} — no data")
            continue
        latest = _latest_yearly(fr)
        if latest is None or latest.dividend_yield is None:
            print(f"    [{i + 1}/{scan_count}] {ticker} — no dividend data")
            continue
        print(f"    [{i + 1}/{scan_count}] {ticker} — div={latest.dividend_yield * 100:.1f}%")
        dividend_data.append({
            "ticker": ticker,
            "div_yield": latest.dividend_yield,
            "pe": latest.pe,
            "pb": latest.pb,
            "roe": latest.roe,
        })

    dividend_data.sort(key=lambda x: x["div_yield"], reverse=True)

    print(f"  Scanned {len(vn_tickers)} tickers, {len(dividend_data)} have dividend data")
    print("\n  Top 15 by Dividend Yield:")
    print(f"  {'Ticker':>6s}  {'Div Yield':>10s}  {'PE':>7s}  {'PB':>7s}  {'ROE':>7s}")
    print(THIN)
    for r in dividend_data[:15]:
        print(
            f"  {r['ticker']:>6s}  {_fmt_pct(r['div_yield']):>10s}  "
            f"{_fmt(r['pe']):>7s}  {_fmt(r['pb']):>7s}  {_fmt_pct(r['roe']):>7s}"
        )


# ── 12. Valuation Scatter: PE vs ROE ────────────────────────────────────────


def demo_12_pe_vs_roe(client: AIPriceAction) -> None:
    section(12, "Valuation Scatter: PE vs ROE (find undervalued)")

    tickers = BANK_TICKERS + TECH_TICKERS + BLUECHIP_TICKERS
    data = []
    for ticker in tickers:
        ci, fr = client.get_fundamental(ticker, source="vn")
        if ci is None or fr is None:
            continue
        latest = _latest_yearly(fr)
        if latest is None or latest.pe is None or latest.roe is None:
            continue
        data.append({
            "ticker": ticker,
            "industry": ci.industry or "",
            "pe": latest.pe,
            "roe": latest.roe,
        })

    if not data:
        print("  No data available")
        return

    avg_pe = sum(d["pe"] for d in data) / len(data)
    avg_roe = sum(d["roe"] for d in data) / len(data)

    print(f"  Screened {len(data)} tickers  |  Avg PE={avg_pe:.1f}  Avg ROE={avg_roe * 100:.1f}%")
    print(THIN)

    undervalued = [d for d in data if d["pe"] < avg_pe and d["roe"] > avg_roe]
    overvalued = [d for d in data if d["pe"] > avg_pe and d["roe"] < avg_roe]

    print(f"\n  Undervalued (PE < avg AND ROE > avg): {len(undervalued)} tickers")
    for d in sorted(undervalued, key=lambda x: x["roe"], reverse=True):
        print(f"    {d['ticker']:>6s}  PE={d['pe']:.1f}  ROE={d['roe'] * 100:.1f}%  ({d['industry']})")

    print(f"\n  Overvalued (PE > avg AND ROE < avg): {len(overvalued)} tickers")
    for d in sorted(overvalued, key=lambda x: x["roe"]):
        print(f"    {d['ticker']:>6s}  PE={d['pe']:.1f}  ROE={d['roe'] * 100:.1f}%  ({d['industry']})")

    print("\n  Full map (text-based scatter):")
    print(f"  {'':>6s}  ROE →")
    print("  PE ↓    0%    5%   10%   15%   20%   25%   30%")
    print(THIN)
    for d in sorted(data, key=lambda x: x["pe"]):
        roe_pos = int((d["roe"] or 0) * 100 / 2.5)
        roe_pos = min(roe_pos, 35)
        line = " " * (8 + roe_pos) + d["ticker"]
        print(f"  {d['pe']:>5.1f}  {line}")


# ── 13. Serialization Roundtrip ─────────────────────────────────────────────


def demo_13_roundtrip(client: AIPriceAction) -> None:
    section(13, "Serialization Roundtrip: to_dict / from_dict")

    ci, fr = client.get_fundamental("ACB", source="vn")

    if ci:
        d = ci.to_dict()
        ci2 = CompanyInfo.from_dict(d)
        assert ci2.symbol == ci.symbol
        assert len(ci2.shareholders) == len(ci.shareholders)
        assert len(ci2.officers) == len(ci.officers)
        print("  CompanyInfo roundtrip OK:")
        print(f"    symbol={ci2.symbol}  shareholders={len(ci2.shareholders)}  officers={len(ci2.officers)}")
        print(f"    to_dict() keys: {sorted(d.keys())}")

        s = ci.shareholders[0]
        sd = s.to_dict()
        s2 = ShareholderInfo.from_dict(sd)
        assert s2.name == s.name
        print(f"    ShareholderInfo roundtrip: name={s2.name}")

        o = ci.officers[0]
        od = o.to_dict()
        o2 = OfficerInfo.from_dict(od)
        assert o2.name == o.name and o2.position == o.position
        print(f"    OfficerInfo roundtrip: name={o2.name} position={o2.position}")

    if fr:
        d = fr.to_dict()
        fr2 = FinancialRatios.from_dict(d)
        assert fr2.ticker == fr.ticker
        assert fr2.count == fr.count
        assert len(fr2.ratios) == len(fr.ratios)
        print("\n  FinancialRatios roundtrip OK:")
        print(f"    ticker={fr2.ticker}  count={fr2.count}  ratio entries={len(fr2.ratios)}")

        r = fr.ratios[0]
        rd = r.to_dict()
        r2 = FinancialRatioEntry.from_dict(rd)
        assert r2.pe == r.pe
        assert r2.year_report == r.year_report
        print(f"    FinancialRatioEntry roundtrip: year={r2.year_report} PE={r2.pe}")

        legacy = [r for r in fr.ratios if r.extra]
        if legacy:
            el = legacy[0]
            ed = el.to_dict()
            el2 = FinancialRatioEntry.from_dict(ed)
            assert el2.extra == el.extra
            print(f"    Extra fields roundtrip: {len(el2.extra)} fields preserved")
            print(f"      keys: {sorted(el2.extra.keys())[:10]}...")

    print("\n  All roundtrip assertions passed!")


# ── 14. Caching Behavior ────────────────────────────────────────────────────


def demo_14_caching(client: AIPriceAction) -> None:
    section(14, "Caching Behavior: second call is instant")

    import time

    ticker = "VCB"
    print(f"  First call: get_company_info('{ticker}')...")
    t0 = time.monotonic()
    ci1 = client.get_company_info(ticker, source="vn")
    t1 = time.monotonic()
    first_ms = (t1 - t0) * 1000
    print(f"    Took {first_ms:.0f}ms  →  symbol={ci1.symbol if ci1 else 'None'}")

    print(f"\n  Second call (cached): get_company_info('{ticker}')...")
    t0 = time.monotonic()
    ci2 = client.get_company_info(ticker, source="vn")
    t1 = time.monotonic()
    second_ms = (t1 - t0) * 1000
    print(f"    Took {second_ms:.0f}ms  →  symbol={ci2.symbol if ci2 else 'None'}")

    if first_ms > 0:
        speedup = first_ms / max(second_ms, 0.01)
        print(f"\n  Speedup: {speedup:.0f}x faster on cache hit")

    print("\n  First call also caches 404s:")
    t0 = time.monotonic()
    client.get_company_info("VNINDEX", source="vn")
    t1 = time.monotonic()
    print(f"    VNINDEX (not found): {_fmt(t1 - t0, '.0f')}ms")

    t0 = time.monotonic()
    client.get_company_info("VNINDEX", source="vn")
    t1 = time.monotonic()
    print(f"    VNINDEX (cached 404): {_fmt((t1 - t0) * 1000, '.0f')}ms")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    print("AIPriceAction Python SDK — Fundamental Data Demo")
    print("================================================")
    print("Covers: company info, financial ratios, screening, YoY trends,")
    print("        bank ratios, sector comparison, valuation scatter, and more.")
    print("\n  Tip: Set S3 cache for speed:")
    print("  client = AIPriceAction(cache_dir='~/.aipriceaction/cache')")

    client = AIPriceAction()

    demos = [
        lambda: demo_1_basic_company_info(client),
        lambda: demo_2_basic_financial_ratios(client),
        lambda: demo_3_combined(client),
        lambda: demo_4_missing_data(client),
        lambda: demo_5_company_deep_dive(client),
        lambda: demo_6_ratios_deep_dive(client),
        lambda: demo_7_yoy_trend(client),
        lambda: demo_8_bank_ratios(client),
        lambda: demo_9_screening(client),
        lambda: demo_10_sector_comparison(client),
        lambda: demo_11_dividend_ranking(client),
        lambda: demo_12_pe_vs_roe(client),
        lambda: demo_13_roundtrip(client),
        lambda: demo_14_caching(client),
    ]

    for demo in demos:
        try:
            demo()
        except Exception as e:
            print(f"\n  Error: {e}")

    print(f"\n{SEPARATOR}")
    print("  Done! Each section function is self-contained — copy into your project.")
    print(SEPARATOR)


if __name__ == "__main__":
    main()
