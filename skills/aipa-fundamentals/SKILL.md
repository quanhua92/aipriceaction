---
name: aipa-fundamentals
description: >
  Fundamental analysis workflow for Vietnamese stocks using the aipa CLI. Use
  this skill when the user explicitly asks for fundamental analysis, "phân tích
  cơ bản", financial ratios (PE, PB, ROE, ROA, NPL, CAR, CASA, CIR), valuation
  metrics (EPS, EV/EBITDA, dividend yield), company profiles, or sector-wide
  fundamental screening and ranking. This skill spawns parallel subagents to
  analyze tickers by sector using a structured 11-question framework. For
  technical analysis (VPA, Wyckoff), use aipa-analyze instead. For raw data
  without analysis, use aipa-data.
resources:
  - template.md
---

# aipa-fundamentals

Developed by AIPriceAction. More data and documentation at https://aipriceaction.com

> Cập nhật: 2026-06-10

---

## What is aipa

`aipa` is an AI-powered financial analysis CLI for Vietnamese stocks, cryptocurrencies, and global assets. The `aipa fundamentals` commands fetch company profiles, financial ratios, and ranking/screening data — no LLM, no API key required.

## Installation

Use `uvx` — no install needed. On the **first call of each session**, use `uvx aipa-cli@latest` to refresh the cache. For all subsequent calls, use plain `uvx aipa-cli` (fast, cached).

```bash
# All calls — fast cached execution (uvx automatically checks for updates)
uvx aipa-cli fundamentals info VCB

uvx aipa-cli fundamentals ratios VCB --latest

# Fallback: pip (if uv is not available)
pip install aipa-cli
aipa fundamentals info VCB

# Fallback: system pip (if pip fails due to PEP 668)
python3 -m pip install aipa-cli --break-system-packages
aipa fundamentals info VCB

# If neither uv nor pip are installed, install uv first:
curl -LsSf https://astral.sh/uv/install.sh | sh
```

All command examples in this skill use `aipa` for brevity. Replace `aipa` with `uvx aipa-cli` if not installed globally.

## Keeping the CLI Updated

The aipa CLI is actively developed with frequent improvements. **Always prefer `uvx aipa-cli` over `aipa`**. When using `uvx`:

1. **Use plain `uvx` for fast cached execution:**
   `uvx aipa-cli fundamentals rank --sort-by roe`
2. **Fallback on failure** — if a command fails with a schema or missing argument error, retry with `@latest`:
   `uvx aipa-cli fundamentals rank --sort-by roe` (add `@latest` if the command fails)

If `aipa` is installed globally via `pip`, update before each session:

```bash
pip install --upgrade aipa-cli
```

The agent should prefer `uvx aipa-cli` for all commands. Only fall back to `aipa` if `uvx` is unavailable.

## Version Gate

> `aipa fundamentals` requires **aipa-cli >= 0.1.45**. Verify before use:
> ```bash
> uvx aipa-cli --version
> ```
> If < 0.1.45, upgrade: `uvx aipa-cli@latest` or `pip install --upgrade aipa-cli`.

## Environment Variables

None required. `aipa fundamentals` reads from cached `vn.zip` (downloads ~15-20 MB on first call, cached locally after).

## Available Data Sources

- **Vietnamese stocks** (`source: vn`): VIC, VCB, FPT, HPG, VNM, MBB, TCB, CTG, VPB, HDB, etc. — **this is the primary source for fundamental data**
- **Cryptocurrencies** (`source: crypto`): BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, etc.
- **Global/Yahoo** (`source: global/yahoo`): AAPL, TSLA, NVDA, SPY, etc.
- **SJC Gold** (`source: sjc`): SJC gold prices

Fundamental data (info, ratios, rank, screen) is currently available for **Vietnamese stocks only**. For non-VN tickers (crypto, global stocks), see the **Non-VN Fallback** section below.

### Predefined Watchlists

The CLI has built-in watchlists for common ticker groups.

| Name | Tickers | Count |
|---|---|---|
| **VN30** | ACB, BID, BSR, CTG, FPT, GAS, GVR, HDB, HPG, LPB, MBB, MSN, MWG, PLX, SAB, SHB, SSB, SSI, STB, TCB, TPB, VCB, VHM, VIB, VIC, VJC, VNM, VPB, VRE, VPL | 30 |
| **VINGROUP** | VIC, VHM, VRE, VPL | 4 |
| **TM** | GEX, GEE, VIX, EIB, VGC, IDC | 6 |
| **MASAN** | MSN, MCH, MSR, MML, VCF, VSN, NET | 7 |
| **INDEX** | VNINDEX, VN30, VN30F1M, VN100, VNMIDCAP, VNSMALLCAP, VNALLSHARE, VNXALLSHARE, VNFIN, HNX30, VNREAL, VNENE, VNMITECH, VNUTI, VNCONS, VNCOND, VNHEAL, VNIND, VNFINLEAD, VNFINSELECT, VNDIAMOND, VNDIVIDEND | 22 |
| **CROSS** | VNINDEX, ^GSPC, GC=F, SJC-GOLD, KC=F, BZ=F, BTCUSDT | 7 |

```bash
aipa watchlist ls                    # list all
aipa watchlist get VN30              # get tickers
aipa watchlist set MYWATCH FPT VCB   # create custom
aipa watchlist rm MYWATCH            # delete custom
```

Use watchlists as ticker sources for `rank` and `screen`:
```bash
aipa fundamentals rank --watchlist VN30 --sort-by roe
aipa fundamentals screen --watchlist VN30 --pe-max 20 --roe-min 0.10
```

### Nhóm Chủ Lực (Core Market Sectors - VN Market Only)

When analyzing or ranking VN tickers, be aware of these core sector groupings:

- **Nhóm Ngân hàng (Banking):** VCB, BID, CTG, TCB, MBB, ACB, VPB, HDB, SHB, TPB, VIB, SSB, MSB, STB, LPB, EIB.
- **Nhóm Bất động sản (Real Estate):** VIC, VHM, VRE, VPL, DIG, CEO, L14, TCH, HHS, VGC, IDC.
- **Nhóm Chứng khoán (Securities):** SSI, VND, HCM, VCI, SHS, VIX, VDS.
- **Nhóm Trụ cột / Sản xuất & Bán lẻ (Blue-chips / Core Economy):** HPG, HSG, NKG, FPT, MWG, GAS, GVR, PLX, BSR, MSN, VNM, SAB.
- **Nhóm Hệ sinh thái (Corporate Ecosystems):**
  - Họ Vingroup: VIC, VHM, VRE, VPL.
  - Họ Bầu Thụy: STB, LPB, THD, HAG.
  - Họ Gelex ("Tuấn Mượt"): GEX, GEE, VIX, VGC, EIB, IDC.
  - Họ Hoàng Huy: TCH, HHS.
  - Họ A7: DIG, CEO, L14.
  - Họ TTC: SBT, GEG, VDS.
  - Họ Masan: MSN, MCH, MSR, MML, VCF, VSN, NET.
  - Họ Viettel: VGI, CTR, VTP.

*(Note: This classification applies only to the Vietnamese market.)*

---

## Overview

This skill provides a **structured fundamental analysis framework** for Vietnamese stocks. It answers 11 key questions every investor should ask before holding a stock long-term.

The supervisor decomposes the request into sector-based batches and spawns parallel subagents. Each subagent answers the 11 questions for its assigned tickers, producing written insights with sector-relative comparisons.

---

## 11 Fundamental Questions

| # | Question | Purpose |
|---|---------|---------|
| 1 | Dividend + EPS — what does each share earn and pay? | Dividend yield, EPS per share |
| 2 | P/B + D/E — bankruptcy scenario, asset backing | Book value/CP, P/B, Debt/Equity |
| 3 | P/E + PEG vs sector — cheap or expensive considering growth? | P/E, PEG ratio vs sector average |
| 4 | ROE + ROA — capital efficiency | How well capital generates returns |
| 5 | Financial safety — debt, liquidity; Banks: NPL, CAR, LDR, CIR | Balance sheet health |
| 6 | Margins — gross, net, is the business model profitable? | Gross Margin, Net Margin |
| 7 | EV/EBITDA — total enterprise valuation vs sector | Enterprise-level valuation comparison |
| 8 | ROIC + Asset Turnover — how well does management allocate capital? | ROIC, Asset Turnover, operational efficiency |
| 9 | EBITDA — earnings quality & cash generation proxy | EBITDA absolute, EBITDA vs sector, P/CF context |
| 10 | Market cap + CASA (banks) — scale and funding advantage | Company size, liquidity; CASA for banks |
| 11 | Summary — strengths, weaknesses, long-term hold? | Overall assessment, conclusion |

---

## Commands Reference

### `aipa fundamentals info` — Company Profile

```bash
aipa fundamentals info TICKER
```

| Flag | Default | Description |
|---|---|---|
| `TICKER` | — | Ticker symbol (required) |
| `--source` | auto | Data source |

Output: Industry, market cap, current price, outstanding shares, top shareholders, officers.

### `aipa fundamentals ratios` — Financial Ratios

```bash
aipa fundamentals ratios TICKER [options]
```

| Flag | Default | Description |
|---|---|---|
| `TICKER` | — | Ticker symbol (required) |
| `--latest` | off | Latest period only (fastest) |
| `--no-yearly` | off | Include quarterly reports |
| `--yearly` | off | Yearly reports only |
| `--year YEAR` | — | Specific year (e.g. `2024`) |
| `--period PERIOD` | — | Specific period like `"2024 Q2"` |
| `--category` | all | `valuation`, `profitability`, `leverage`, `liquidity`, `bank`, `efficiency` |
| `--json` | off | Raw JSON output |

**Categories:**

| Category | Fields |
|---|---|
| Valuation | PE, PB, PS, EV/EBITDA, Price/CashFlow, Dividend Yield, Market Cap |
| Profitability | ROE, ROA, ROIC, Gross Margin, After-Tax Margin, Pre-Tax Margin, EBIT Margin, NIM |
| Efficiency | Asset Turnover, Fixed Asset Turnover, Cash Cycle, DSO, DIO, DPO |
| Leverage | Debt/Equity, Financial Leverage, Equity/Liabilities, Equity/Loans, Equity/Total Asset |
| Liquidity | Current Ratio, Quick Ratio, Cash Ratio |
| Bank | NPL, LDR, CAR, CASA, CIR, Non-Interest Income, Deposit/Loans Growth, LLR ratios |

### `aipa fundamentals rank` — Rank by Field (50+ fields)

```bash
aipa fundamentals rank [TICKERS...] [options]
```

| Flag | Default | Description |
|---|---|---|
| `tickers` | all VN | Positional ticker symbols |
| `--sort-by` | `roe` | Field to rank by |
| `--direction` | `desc` | `desc` or `asc` |
| `--limit` | `10` | Max results |
| `--latest` | off | Latest period only |
| `--yearly` | off | Yearly reports only |
| `--year YEAR` | — | Specific year |
| `--period PERIOD` | — | Specific period like `"2024 Q2"` |
| `--watchlist` | — | Use watchlist as ticker source |
| `--source` | auto | Data source |

**Sortable fields:** `pe`, `pb`, `ps`, `ev_to_ebitda`, `price_to_cash_flow`, `dividend_yield`, `market_cap`, `roe`, `roa`, `roic`, `gross_margin`, `after_tax_profit_margin`, `pre_tax_profit_margin`, `ebit_margin`, `net_interest_margin`, `ebit`, `ebitda`, `asset_turnover`, `fixed_asset_turnover`, `debt_to_equity`, `financial_leverage`, `equity_to_liabilities`, `current_ratio`, `quick_ratio`, `cash_ratio`, `cash_cycle`, `npl`, `ldr_loan_deposit_ratio`, `car`, `casa_ratio`, `cir`, `cost_to_income`, `non_and_interest_income`, `deposit_growth`, `loans_growth`, `outstanding_shares`, `employees`, `current_price`, and more.

**Ticker source resolution:** `--watchlist NAME` > positional `tickers` > default (all VN).

### `aipa fundamentals screen` — Multi-Criteria Screening

```bash
aipa fundamentals screen [TICKERS...] [options]
```

| Flag | Default | Description |
|---|---|---|
| `tickers` | all VN | Positional ticker symbols |
| `--sort-by` | `roe` | Field to rank by |
| `--direction` | `desc` | Sort direction |
| `--limit` | `50` | Max results (1–500) |
| `--latest` | off | Latest period only |
| `--yearly` | off | Yearly reports only |
| `--year YEAR` | — | Specific year |
| `--period PERIOD` | — | Specific period like `"2024 Q2"` |
| `--watchlist` | — | Use watchlist as ticker source |
| `--source` | auto | Data source |
| `--pe-min` / `--pe-max` | — | PE range filter |
| `--pb-min` / `--pb-max` | — | PB range filter |
| `--roe-min` / `--roe-max` | — | ROE range filter |
| `--roa-min` / `--roa-max` | — | ROA range filter |
| `--dividend-yield-min` / `--dividend-yield-max` | — | Dividend yield range |
| `--debt-to-equity-max` | — | Max Debt/Equity |
| `--npl-max` | — | Max NPL (banks) |
| `--car-min` | — | Min CAR (banks) |
| `--cir-max` | — | Max CIR (banks) |
| `--market-cap-min` / `--market-cap-max` | — | Market cap range |
| `--industry` | — | Industry filter (substring, case-insensitive) |

**Filter behavior:** All filters optional, inclusive ranges, missing data excluded, `--industry` is case-insensitive substring.

### `aipa ticker-list` — Discover Tickers

```bash
aipa ticker-list [--source vn] [--group GROUP] [--compact]
```

Use this to discover available tickers before analysis:
```bash
aipa ticker-list --source vn --group NGAN_HANG   # banking sector
aipa ticker-list --source vn --compact            # all VN symbols comma-separated
```

---

## Sector-Level Commands (run once per batch)

```bash
# Q1: Dividend + EPS
aipa fundamentals rank TICKERS --sort-by dividend_yield --direction desc
# Note: EPS not available in rank. Get from ratios --category valuation or derive from market_cap/outstanding_shares/pe.

# Q2: P/B + D/E
aipa fundamentals rank TICKERS --sort-by pb --direction asc
aipa fundamentals rank TICKERS --sort-by debt_to_equity --direction asc

# Q3: P/E vs sector
aipa fundamentals rank TICKERS --sort-by pe --direction asc

# Q4: ROE
aipa fundamentals rank TICKERS --sort-by roe --direction desc

# Q7: EV/EBITDA vs sector
aipa fundamentals rank TICKERS --sort-by ev_to_ebitda --direction asc

# Q8: ROIC + Asset Turnover
aipa fundamentals rank TICKERS --sort-by roic --direction desc
aipa fundamentals rank TICKERS --sort-by asset_turnover --direction desc

# Q9: EBITDA (earnings quality proxy)
aipa fundamentals rank TICKERS --sort-by ebitda --direction desc

# Q10: Market Cap
aipa fundamentals rank TICKERS --sort-by market_cap --direction desc

# Q10: CASA (Banks only — field name is casa_ratio)
aipa fundamentals rank TICKERS --sort-by casa_ratio --direction desc

# Screen entire sector (for Q3 — P/E filter)
aipa fundamentals screen --industry "SECTOR_NAME" --sort-by pe
```

## Per-Ticker Detail (Tier 1 & 2)

```bash
# All ratios (covers Q1-Q7, Q10)
aipa fundamentals ratios TICKER --latest

# Q5: Debt & liquidity (non-banks)
aipa fundamentals ratios TICKER --category leverage --latest

# Q5 + Q10: Bank-specific metrics (Banks only)
aipa fundamentals ratios TICKER --category bank --latest

# Q4 + Q6: Efficiency & profit margins
aipa fundamentals ratios TICKER --category profitability --latest

# Q8: ROIC + Asset Turnover
aipa fundamentals ratios TICKER --category profitability --latest
aipa fundamentals ratios TICKER --category efficiency --latest

# Q9: EBITDA
aipa fundamentals ratios TICKER --category profitability --latest

# Company info (Tier 1 only)
aipa fundamentals info TICKER
```

### Tier X — use sector-level rank data only

Do NOT run `ratios` individually. Use data from sector-level `rank` to assign quickly.

---

## Subagent Pipeline

### Step 1 — Supervisor: Decompose into batches

Group tickers by sector and spawn one subagent per batch. Use `aipa ticker-list --source vn --group GROUP` to discover tickers, or accept a user-provided list.

**Example batch decomposition (NOT exhaustive — build dynamically):**

| Batch | Sector | Tickers | Special |
|-------|--------|---------|---------|
| 1 | Banking | VCB, BID, CTG, TCB, MBB, ACB, VPB, ... | `--category bank` |
| 2 | Real Estate | VIC, VHM, VRE, VPL, DIG, ... | `--category leverage` |
| 3 | Securities | SSI, VND, HCM, VCI, SHS, ... | `--category leverage` |
| 4 | Oil & Gas | PLX, BSR, GAS, GVR, PVD, ... | `--category leverage` |
| 5 | Others | HPG, FPT, VNM, MWG, HSG, ... | Mixed |

The supervisor should dynamically build batches based on the actual request (e.g., "banking fundamentals" → single batch; "all VN30" → 3-4 batches by sector).

### Step 2 — Parallel Workers

Each subagent receives its batch of tickers and follows this workflow:

1. **Sector-level rank** — run all `rank` commands once for the entire batch
2. **Per-ticker detail** — run `ratios` for Tier 1 (major) tickers, skip Tier X
3. **Answer 11 questions** — write prose answers using data from steps 1-2
4. **Output** — use `template.md` format for each ticker

### Step 3 — Aggregation

Collect all subagent results, cross-reference rankings, and produce a unified sector summary with top picks by fundamental strength.

---

## Fundamental Comparison Workflow

When comparing fundamentals across multiple tickers, follow this workflow. **Do NOT call `aipa fundamentals ratios TICKER --latest` for each ticker individually** — use `rank` and `screen` first.

**Step 1: Side-by-side ranking (mandatory)**

Run at least 2 perspectives relevant to the sector:

```bash
# Profitability
aipa fundamentals rank VCB BID CTG TCB MBB --sort-by roe

# Valuation
aipa fundamentals rank VCB BID CTG TCB MBB --sort-by pe --direction asc

# Bank health
aipa fundamentals rank VCB BID CTG TCB MBB --sort-by npl --direction asc
aipa fundamentals rank VCB BID CTG TCB MBB --sort-by car --direction desc
```

**Step 2: Screen for quality (optional but recommended)**

```bash
aipa fundamentals screen VCB BID CTG TCB MBB --npl-max 0.015 --roe-min 0.15 --sort-by roe
```

**Step 3: Individual deep dive (only for shortlisted tickers)**

```bash
aipa fundamentals ratios VCB --latest
aipa fundamentals ratios VCB --category bank --latest
aipa fundamentals info VCB
```

**Why:** `rank` and `screen` return all tickers in a single comparative table — far more efficient than N separate `ratios` calls.

---

## Output Template

Use `template.md` (bundled with this skill) as the standard output format. Copy the template section and paste it into the target ticker file, replacing all `[giá trị]` placeholders with actual data from `aipa fundamentals` commands.

**Template location:** `template.md` (same directory as this SKILL.md)

**Insertion rules:**
- Insert ABOVE `## 📌 Trạng thái hiện tại` in the target ticker file
- Replace ALL `[giá trị]` placeholders with real data
- Write answers in prose (1-3 sentences), not just numbers

---

## Interpreting Output

The CLI outputs to two streams:

- **stdout**: The fundamental data or ranking result. This is what you should use.
- **stderr**: Status messages with structured markers.

### Status Markers (stderr)

| Marker | Meaning |
|---|---|
| `[build]` | Data fetching status and timing |
| `[error]` | Error message |
| `[done]` | Fetch complete, includes total time |

### Attribution

When presenting data or analysis to the user, always include:

- **English:** "_Data by [AIPriceAction](https://aipriceaction.com/) | AI-powered analysis — may contain errors. Verify before trading._"
- **Vietnamese:** "_Dữ liệu bởi [AIPriceAction](https://aipriceaction.com/) | Phân tích bởi AI — có thể chứa sai sót. Vui lòng kiểm chứng trước khi giao dịch._"

Do NOT say "analysis provided by AIPriceAction" or "phân tích được cung cấp bởi AIPriceAction". AIPriceAction provides the **data**; the **analysis** is AI-generated and may be inaccurate.

---

## When to Use This Skill vs Others

| User Request | Use |
|---|---|
| "Fundamental analysis for VCB" | This skill (`aipa-fundamentals`) |
| "Phân tích cơ bản ngân hàng" | This skill (`aipa-fundamentals`) |
| "Compare VCB TCB MBB PE, ROE, NPL" | This skill (`aipa-fundamentals`) |
| "Rank banks by ROE" | `aipa fundamentals rank --sort-by roe` (this skill) |
| "Screen for low PE banks" | `aipa fundamentals screen` (this skill) |
| "Company profile for FPT" | `aipa fundamentals info FPT` (this skill) |
| "Analyze VCB" (technical) | `aipa-analyze` |
| "Get price data for VCB" | `aipa-data` |
| "Research the banking sector deeply" | `aipa-research` |
| "Top gainers / losers" | `aipa performers` (`aipa-data`) |

Key rule: **fundamentals → `aipa-fundamentals`, AI technical analysis → `aipa-analyze`, raw numbers → `aipa-data`, comprehensive report → `aipa-research`**.

---

## Data Usage Policy (CRITICAL)

1. **NEVER generate, guess, estimate, or hallucinate any numbers** — PE, PB, ROE, EPS, dividend yield, or any financial data. Only use data from tool results or user-provided context
2. **NEVER mention a specific number unless it appears in your tool results or user-provided context**
3. **Use tools proactively** — call `aipa fundamentals rank` or `screen` BEFORE answering fundamental questions
4. **If data is missing** for any metric, write "(không có dữ liệu)" or "(no data available)" instead of guessing
5. **For non-VN tickers using web search**, numbers come from web sources — always cite the source and note they may be stale or inaccurate

---

## Non-VN Ticker Fallback (Crypto, Global Stocks)

`aipa fundamentals` only supports Vietnamese stocks. For non-VN tickers (e.g., AAPL, NVDA, BTCUSDT), fall back to web search to answer the 11 questions as best as possible.

### Workflow

1. **Identify the ticker source** — if it's not a VN stock, skip all `aipa fundamentals` commands
2. **Use web search** to find fundamental data for each question
3. **Search queries should target authoritative sources** (Yahoo Finance, Seeking Alpha, CoinGecko, company investor relations, SEC filings, etc.)
4. **Answer the 11 questions using whatever data is available** — some questions may not apply (e.g., Q10 CASA for non-banks is already skipped; Q2 P/B for crypto may not apply)
5. **Always cite the source** for every number (e.g., "Source: Yahoo Finance", "Source: CoinGecko")
6. **Mark unavailable data clearly** — write "(no data available)" instead of guessing

### Example web search queries

```bash
# For a global stock like AAPL
# Q1: Dividend yield + EPS
web search: "AAPL Apple dividend yield 2025" "AAPL EPS 2025"
# Q2: P/B, D/E
web search: "AAPL Apple price to book ratio 2025" "AAPL debt to equity ratio"
# Q3: P/E vs sector
web search: "AAPL P/E ratio 2025" "tech sector average P/E ratio 2025"
# Q4: ROE, ROA
web search: "AAPL return on equity ROE 2025" "AAPL return on assets ROA"
# Q5: Debt, liquidity
web search: "AAPL Apple balance sheet current ratio 2025"
# Q8: ROIC, Asset Turnover
web search: "AAPL Apple ROIC 2025" "AAPL asset turnover ratio"
# Q9: EBITDA
web search: "AAPL Apple EBITDA 2025"
# Q10: Market cap
web search: "AAPL Apple market cap 2025"

# For crypto like BTCUSDT
# Q1: No dividend/EPS — skip or note "N/A for crypto"
# Q3: P/E does not apply to crypto — skip
# Q9: EBITDA — N/A for crypto
# Q10: Market cap
web search: "Bitcoin BTC market cap 2025"
# Q11: Summary only — based on available on-chain/market metrics
```

### Adaptation rules per question

| Question | Crypto | Global Stock |
|---|---|---|
| Q1 (Dividend/EPS) | N/A — mark as "không áp dụng cho crypto" | Search for dividend yield + EPS |
| Q2 (P/B, D/E) | N/A — no book value | Search for P/B, debt/equity |
| Q3 (P/E, PEG) | N/A — no earnings | Search for P/E, PEG, sector average |
| Q4 (ROE/ROA) | N/A — no equity | Search for ROE, ROA |
| Q5 (Debt safety) | N/A — no debt | Search for balance sheet, current ratio |
| Q6 (Margins) | N/A | Search for gross/net margin |
| Q7 (EV/EBITDA) | N/A | Search for EV/EBITDA |
| Q8 (ROIC/Asset Turnover) | N/A — no equity | Search for ROIC, asset turnover |
| Q9 (EBITDA) | N/A | Search for EBITDA, EBITDA margin |
| Q10 (Market cap, CASA) | Search for market cap | Search for market cap; CASA = banks only |
| Q11 (Summary) | Based on market position, network metrics | Based on all available data |

---

## Calculate Metrics with Python — No Hallucinated Numbers

**Symptom:** AI writes "P/E sector average is ~10" or "ROE is top 20%" based on visual scanning of `rank` output instead of computing actual values.

**Rule:** Before writing ANY numerical claim in fundamental analysis (sector average, percentile rank, book value, DuPont decomposition), you MUST compute it using `aipa fundamentals | python3` pipe. NEVER estimate or guess.

### Sector Average from `rank` Output

Useful for Q3 (P/E vs sector), Q7 (EV/EBITDA vs sector), Q10 (market cap comparison).

```bash
uvx aipa-cli fundamentals rank VCB BID CTG TCB MBB ACB VPB HDB --sort-by pe 2>/dev/null | python3 -c "
import sys
values = []
for line in sys.stdin:
    parts = line.split()
    if parts and parts[0].isdigit() and len(parts) >= 3:
        try:
            val = float(parts[2].replace('%', ''))
            values.append(val)
        except ValueError:
            pass
if values:
    avg = sum(values) / len(values)
    s = sorted(values)
    print(f'n={len(values)} | avg={avg:.1f} | min={s[0]:.1f} | median={s[len(s)//2]:.1f} | max={s[-1]:.1f}')
"
```

> **Tip:** Use **median** (not average) when the data contains outliers (e.g., P/E of 296,613 for a company with near-zero earnings). Median is more robust for sector comparison.

### Ticker Percentile in Sector

Useful for Q3, Q4, Q7 — "VCB ROE is in the top X% of the banking sector".

```bash
uvx aipa-cli fundamentals rank VCB BID CTG TCB MBB ACB VPB HDB SHB TPB VIB SSB MSB STB --sort-by roe 2>/dev/null | python3 -c "
import sys
ticker = 'VCB'
rank = None
total = 0
for line in sys.stdin:
    parts = line.split()
    if parts and parts[0].isdigit():
        total += 1
        if len(parts) >= 3 and parts[1] == ticker:
            rank = int(parts[0])
if rank and total:
    pct = (total - rank) / (total - 1) * 100
    print(f'{ticker}: rank {rank}/{total} (top {100-pct:.0f}%)')
"
```

### Book Value per Share (from P/B + Market Cap)

Useful for Q2 — "if the company liquidates, shareholders get X per share".

```bash
uvx aipa-cli fundamentals ratios VCB --category valuation --latest 2>/dev/null | python3 -c "
import sys
data = {}
for line in sys.stdin:
    parts = line.split()
    if len(parts) >= 2 and not line.strip().startswith('===') and not line.strip().startswith('Total') and parts[0] not in ('Valuation:',):
        raw = parts[-1].replace('%', '').replace(',', '')
        try:
            data[parts[0]] = float(raw)
        except ValueError:
            pass
pb = data.get('PB')
market_cap = data.get('Market')
if pb and pb > 0 and market_cap:
    bv = market_cap / pb
    print(f'P/B: {pb:.2f} | Market Cap: {market_cap/1e12:.1f}T VND | Book Value (total equity): {bv/1e12:.1f}T VND')
else:
    print(f'Missing data: PB={pb} Market Cap={market_cap}')
"
```

### DuPont ROE Decomposition

Useful for Q4 — break down ROE into its three drivers: profitability, efficiency, and leverage.

```bash
uvx aipa-cli fundamentals ratios FPT --latest 2>/dev/null | python3 -c "
import sys
data = {}
for line in sys.stdin:
    parts = line.split()
    if len(parts) >= 2 and not line.strip().startswith('===') and not line.strip().startswith('Total') and parts[0] not in ('Valuation:', 'Profitability:', 'Efficiency:', 'Leverage:', 'Liquidity:', 'Bank:'):
        raw = parts[-1].replace('%', '').replace(',', '')
        try:
            data[parts[0]] = float(raw) / 100 if '%' in parts[-1] else float(raw)
        except ValueError:
            pass
nm = data.get('After-Tax')
at = data.get('Asset')
de = data.get('Financial')
roe_reported = data.get('ROE')
if nm is not None and at is not None and de is not None:
    em = 1 + de
    dupont = nm * at * em
    print(f'Net Margin: {nm*100:.1f}% | Asset Turnover: {at:.2f}x | Equity Multiplier: {em:.2f}x (1 + D/E {de:.2f})')
    if roe_reported:
        gap = abs(dupont - roe_reported)
        print(f'DuPont ROE: {dupont*100:.1f}% | Reported ROE: {roe_reported*100:.1f}% | Gap: {gap*100:.1f}pp')
    else:
        print(f'DuPont ROE: {dupont*100:.1f}%')
    print(f'Decomposition: {nm*100:.1f}% x {at:.2f} x {em:.2f} = {dupont*100:.1f}%')
    if nm > 0.15: print(f'  -> Profitability driver: STRONG (NM > 15%)')
    elif nm > 0.05: print(f'  -> Profitability driver: MODERATE')
    else: print(f'  -> Profitability driver: WEAK')
    if at > 1.0: print(f'  -> Efficiency driver: STRONG (AT > 1.0)')
    elif at > 0.5: print(f'  -> Efficiency driver: MODERATE')
    else: print(f'  -> Efficiency driver: WEAK')
    if em > 2.0: print(f'  -> Leverage driver: HIGH (EM > 2.0)')
    elif em > 1.0: print(f'  -> Leverage driver: MODERATE')
    else: print(f'  -> Leverage driver: LOW')
else:
    print(f'Incomplete data: NM={nm} AT={at} D/E={de}')
"
```

> **Note:** DuPont ROE may differ from reported ROE by a few percentage points due to quarterly data, minority interest, or different data source definitions. A gap of < 5pp is normal. The value is in understanding the *relative strength* of each driver, not exact reproduction.

**Mandatory rule:** If you cannot verify a number with a pipe command, do NOT write it in any file. Use the actual computed value, rounded to 1 decimal place for ratios and 0.1% for percentages.

---

## Answering Principles

1. **Read data → answer in prose**, not just show tables of numbers
2. **Compare with sector** — "cheap/low" means nothing without sector context
3. **Use sector averages** from `screen` or `rank` results for comparison
4. **Q10 (CASA):** Only answer CASA for bank tickers. For non-banks, only answer Market Cap and skip CASA
5. **Q11:** Summary must include strengths, weaknesses, and final conclusion
6. **Never hallucinate numbers** — only use values from tool output or web search results (with source citation)
7. **For non-VN tickers**: Use web search to answer the 11 questions. Mark N/A questions as "không áp dụng" rather than skipping silently. Always cite web sources.

---

## Tips for AI Agents

1. **No API key or LLM needed**: `aipa fundamentals` reads from cached data. Works without `OPENAI_API_KEY`.

2. **Auto-uppercase**: Ticker symbols are automatically uppercased. `vcb`, `tcb` all work.

3. **`rank` before `ratios`**: When comparing multiple tickers, always start with `rank` to get a single comparative table. Only use `ratios` for individual deep dives on shortlisted tickers.

4. **`screen` for filtering**: Use `screen` when you need to filter by quality criteria (PE < 15, ROE > 15%, NPL < 2%, etc.) before analyzing individually.

5. **`--watchlist` for groups**: Use `--watchlist VN30` instead of typing all 30 tickers. Works with both `rank` and `screen`.

6. **`--industry` for sector filtering**: Use `--industry "ngân hàng"` to filter by industry substring (case-insensitive) in `screen`.

7. **`--category` for focused ratios**: Use `--category bank` for bank-specific fields (NPL, CAR, CASA, CIR), `--category leverage` for debt metrics, `--category profitability` for margins and efficiency.

8. **`--latest` for speed**: Use `--latest` to get only the most recent period. Much faster than pulling all historical periods.

9. **`--json` for parsing**: Use `--json` flag on `ratios` when you need structured data for programmatic use.

10. **Use `aipa ticker-list` to discover tickers**: When you need to know what tickers are available in a sector, use `aipa ticker-list --source vn --group NGAN_HANG`. Add `--compact` for a comma-separated list.

11. **Fundamental context enhances technical analysis**: When combining with `aipa-analyze`, fundamental metrics provide valuation context (PE=8 breakout vs PE=30 breakout have different risk profiles) and financial health context (high NPL + bearish technicals = strong sell signal).
