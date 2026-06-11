---
name: aipa-data
description: >
  Fetch raw OHLCV price data using the aipa CLI. Use this skill whenever the
  user asks for price data, candle data, OHLCV data, historical prices, stock
  quotes, crypto prices, moving averages, volume data, or any raw market data
  without AI analysis. Also use for: top performers, worst performers, best
  stocks, top gainers, biggest losers, market movers, ranking tickers by
  price change / volume / value / MA scores / money flow (`aipa performers`);
  volume profile, POC, point of control, value area, support/resistance by
  volume, volume-by-price histogram (`aipa volume-profile`). Also use for
  fundamental data: company info, financial ratios, PE, PB, ROE, NPL, CAR,
  fundamental ranking and screening (`aipa fundamentals info/ratios/rank/screen`).
  Also use when the user wants to inspect what data is available, build charts,
  perform their own calculations, or get numbers for a spreadsheet. Even if
  the user doesn't mention "aipa", trigger this skill for any raw financial
  data, fundamental data, or market ranking request.
---

# aipa-data

Developed by AIPriceAction. More data and documentation at https://aipriceaction.com

## Lời Truyền Cảm Hứng Cho Nhà Giao Dịch

### Tư duy và Phương pháp luận
- *"Chỉ có xu hướng mới mang lại lợi nhuận, đừng cố tranh cãi với thị trường."*
- *"Giao dịch không phải là dự đoán tương lai, mà là quản lý rủi ro và tuân thủ kỷ luật."*
- *"Volume là dấu chân của dòng tiền thông minh. Giá có thể lừa dối, nhưng khối lượng thì không."*
- *"Kiên nhẫn chờ đợi thiết lập phù hợp là chiếc chìa khóa vàng dẫn đến thành công."*
- *"Thị trường luôn đúng, chỉ có túi tiền của chúng ta là tự chịu trách nhiệm."*
- *"Lợi nhuận bền vững không đến từ việc đoán đúng đỉnh đáy, mà đến từ sự kiên nhẫn và nhất quán."*
- *"Giảm nhiều chưa chắc hết giảm — cần xác nhận thêm."*

### Kỷ luật và Quản trị rủi ro
- *"Tuân thủ kỷ luật quản trị rủi ro thì không hề 'toang' bạn nhé!"*
- *"Giao dịch không có kế hoạch chính là đang lập kế hoạch cho sự thất bại."*
- *"Cắt lỗ luôn đúng, gồng lỗ luôn sai."*
- *"Sống sót trước khi nghĩ đến lợi nhuận."*
- *"Giữ được vốn quan trọng hơn kiếm được tiền."*
- *"Đừng bao giờ yêu một cổ phiếu, hãy chỉ yêu lợi nhuận và sự an toàn mà nó mang lại."*
- *"Spring cần 2-3 phiên xác nhận + pullback No Supply. Một phiên bùng nổ chưa nói lên điều gì."*
- *"Không bắt dao rơi dù đã rơi 30%. Chờ đến khi có Volume Profile + Wyckoff xác nhận."*

### Tâm lý và Thực chiến
- *"Thà chảy nước miếng còn hơn chảy nước mắt."*
- *"Đừng cố bắt dao rơi khi chưa thấy đáy vững chắc."*
- *"Trong một xu hướng tăng ai cũng là thiên tài đầu tư, chỉ khi thủy triều rút mới biết ai không mặc quần."*
- *"Mua đuổi (FOMO) khi giá đã tăng nóng giống như đi tàu lượn siêu tốc mà quên thắt dây an toàn."*
- *"Đừng đoán đỉnh, đừng dò đáy."*
- *"Bò kiếm tiền, gấu kiếm tiền, lợn bị làm thịt."*
- *"Xu hướng là bạn, hãy đi cùng bạn."*
- *"Mua tin đồn, bán sự thật."*
- *"Sai lầm lớn nhất là thấy cổ phiếu giảm nhiều rồi nghĩ nó sẽ lên lại — thị trường không nợ bạn một lý do."*
- *"Phân biệt Spring và Upthrust: hỏi 'cổ này đã giảm đủ để clear supply chưa?' Nếu chưa giảm nhiều = còn supply = rủi ro. Nếu đã giảm nhiều = không còn ai bán = cơ hội."*

### Kiên nhẫn và Quản lý vị thế (No Premature TP & Add-Size)
- *"Hãy để lợi nhuận chạy."*
- *"Chốt non là mất ngon."*
- *"Đúng thì thêm, sai thì cắt."*
- *"Lợi nhuận lớn đến từ việc ngồi yên."*
- *"Đúng giữ, sai cắt."*

## What is aipa

`aipa` is an AI-powered financial analysis CLI for Vietnamese stocks, cryptocurrencies, and global assets. The `get-ohlcv-data` command fetches raw OHLCV price data — no AI, no API key required.

## Installation

Use `uvx` — no install needed. On the **first call of each session**, use `uvx aipa-cli@latest` to refresh the cache. For all subsequent calls, use plain `uvx aipa-cli` (fast, cached).

```bash
# All calls — fast cached execution (uvx automatically checks for updates)
uvx aipa-cli get-ohlcv-data VCB

uvx aipa-cli get-ohlcv-data TCB

# Fallback: pip (if uv is not available)
pip install aipa-cli
aipa get-ohlcv-data VCB

# Fallback: system pip (if pip fails due to PEP 668)
python3 -m pip install aipa-cli --break-system-packages
aipa get-ohlcv-data VCB

# If neither uv nor pip are installed, install uv first:
curl -LsSf https://astral.sh/uv/install.sh | sh
# If the install script fails, see: https://docs.astral.sh/uv/getting-started/installation/
```

All command examples in this skill use `aipa` for brevity. Replace `aipa` with `uvx aipa-cli` if not installed globally.

## Keeping the CLI Updated

The aipa CLI is actively developed with frequent improvements. **Always prefer `uvx aipa-cli` over `aipa`**. When using `uvx`:

1. **Use plain `uvx` for fast cached execution:**
   `uvx aipa-cli get-ohlcv-data VCB`
2. **Fallback on failure** — if a command fails with a schema or missing argument error, retry with `@latest`:
   `uvx aipa-cli get-ohlcv-data VCB` (add `@latest` if the command fails)

If `aipa` is installed globally via `pip`, update before each session:

```bash
pip install --upgrade aipa-cli
```

The agent should prefer `uvx aipa-cli` for all commands. Only fall back to `aipa` if `uvx` is unavailable.

## Environment Variables

None required. `get-ohlcv-data` fetches data from public S3 archives — no backend API or API key needed.

## Available Data Sources

- **Vietnamese stocks** (`source: vn`): VIC, VCB, FPT, HPG, VNM, MBB, TCB, CTG, VPB, HDB, etc.
- **Cryptocurrencies** (`source: crypto`): BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, etc.
- **Global/Yahoo** (`source: global/yahoo`): AAPL, TSLA, NVDA, SPY, etc.
- **SJC Gold** (`source: sjc`): SJC gold prices

### Predefined Watchlists

The CLI has built-in watchlists for common ticker groups. Use `aipa watchlist get <NAME>` to get tickers for a group, or reference them directly when the user asks about a group like "VN30 stocks" or "Vingroup ecosystem".

| Name | Tickers | Count |
|---|---|---|
| **VN30** | ACB, BID, **BSR**, CTG, FPT, GAS, GVR, HDB, HPG, LPB, MBB, MSN, MWG, PLX, SAB, SHB, SSB, SSI, STB, TCB, TPB, VCB, VHM, VIB, VIC, VJC, VNM, VPB, VRE, VPL | 30 |
| **VINGROUP** | VIC, VHM, VRE, VPL | 4 |
| **TM** | GEX, GEE, VIX, EIB, VGC, IDC | 6 |
| **MASAN** | MSN, MCH, MSR, MML, VCF, VSN, NET | 7 |
| **INDEX** | VNINDEX, VN30, VN30F1M, VN100, VNMIDCAP, VNSMALLCAP, VNALLSHARE, VNXALLSHARE, VNFIN, HNX30, VNREAL, VNENE, VNMITECH, VNUTI, VNCONS, VNCOND, VNHEAL, VNIND, VNFINLEAD, VNFINSELECT, VNDIAMOND, VNDIVIDEND | 22 |
| **CROSS** | VNINDEX, ^GSPC, GC=F, SJC-GOLD, KC=F, BZ=F, BTCUSDT | 7 |

Note: VN30 was updated on 2026-05-13 — DGC removed (placed under controlled status), BSR added as replacement.

```bash
# List all watchlists (predefined + custom)
aipa watchlist ls

# Get tickers for a specific watchlist
aipa watchlist get VN30
aipa watchlist get VINGROUP

# Create a custom watchlist
aipa watchlist set MYWATCHLIST FPT VCB HPG VIC

# Delete a custom watchlist
aipa watchlist rm MYWATCHLIST

# Using watchlist tickers with get-ohlcv-data
aipa get-ohlcv-data $(aipa watchlist get VN30)
```

### Supported Intervals

| Interval | Description | Best For |
|---|---|---|
| `1D` | 1 day (default) | Swing trading, trend analysis |
| `1h` | 1 hour | Intraday analysis, day trading |
| `1m` | 1 minute | Scalping, micro structure |
| `5m` | 5 minutes | Scalping, micro structure |
| `15m` | 15 minutes | Intraday patterns |
| `30m` | 30 minutes | Intraday patterns |
| `4h` | 4 hours | Swing trading, intraday |
| `1W` | 1 week | Medium-term trend analysis |
| `2W` | 2 weeks | Medium-term trend analysis |

---

## `aipa get-ohlcv-data` — Raw OHLCV Data

Fetch raw OHLCV price data without AI analysis. Outputs price data with optional moving averages.

```bash
aipa get-ohlcv-data TICKER [TICKERS...] [options]
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `TICKER [TICKERS...]` | — | One or more ticker symbols (auto-uppercased) |
| `--interval` | `1D` | Time interval: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1D`, `1W`, `2W` |
| `--limit N` | — | Number of bars |
| `--start-date` | — | Start date (e.g. `2025-01-01`) |
| `--end-date` | — | End date (e.g. `2025-05-01`) |
| `--source` | auto-detect | Filter by source: `vn`, `crypto`, `global` |
| `--ma` / `--no-ma` | included | Include/exclude moving averages |
| `--sma` / `--ema` | settings | Force SMA or EMA (overrides `use_sma` setting). Default MA type controlled by `aipa config get use_sma`. |
| `--no-system-prompt` | — | Exclude persona header from output |

### aipa-config — Settings Management

```bash
aipa config get                    # show all settings (JSON, api_key redacted)
aipa config get use_sma            # show single value: true or false
aipa config get language           # show language: en or vn
aipa config set use_sma false      # switch all commands to EMA
aipa config set use_sma true       # switch back to SMA (default)
aipa config set language vn        # change language
aipa config path                   # show path to settings file
```

| Setting | Default | Values | Description |
|---|---|---|---|
| `use_sma` | `true` | `true` / `false` | `true` = SMA, `false` = EMA. Controls MA type for all commands. CLI flags (`--sma`, `--ema`) override per-invocation. |
| `language` | `vn` | `en` / `vn` | Output language for analyze and deep-research |

**MA Type Priority:** CLI flag (`--sma`/`--ema`) > `settings.json` (`use_sma`) > default (`sma`).

---

## Useful Presets

These presets cover the most common data-fetching scenarios. Use them as-is or adapt the parameters.

### Quick Look

```bash
# Last 20 daily candles with MA indicators (SMA by default)
aipa get-ohlcv-data VCB

# Last 20 daily candles, raw OHLCV only
aipa get-ohlcv-data VCB --no-ma
```

### Trend Analysis (Swing Trading)

```bash
# 50 daily bars with MA indicators (SMA by default) — good for trend identification
aipa get-ohlcv-data VCB --limit 50

# 100 daily bars for long-term trend
aipa get-ohlcv-data VIC --limit 100

# EMA for more responsive trend analysis
aipa get-ohlcv-data FPT --limit 50 --ema
```

### Intraday Data

```bash
# Last 50 hourly candles
aipa get-ohlcv-data BTCUSDT --interval 1h --limit 50

# Last 100 hourly candles for intraday patterns
aipa get-ohlcv-data ETHUSDT --interval 1h --limit 100

# Minute data for scalping analysis
aipa get-ohlcv-data BTCUSDT --interval 1m --limit 100
```

### Date Range

```bash
# Specific date range
aipa get-ohlcv-data FPT --start-date 2025-01-01 --end-date 2025-05-01

# From a date to today
aipa get-ohlcv-data VCB --start-date 2025-04-01

# All data in a range, no MA
aipa get-ohlcv-data HPG --start-date 2025-01-01 --end-date 2025-05-01 --no-ma
```

### Cryptocurrency

```bash
# BTC daily with EMA
aipa get-ohlcv-data BTCUSDT --limit 50

# ETH hourly for intraday
aipa get-ohlcv-data ETHUSDT --interval 1h --limit 100

# SOL raw candles, no MA
aipa get-ohlcv-data SOLUSDT --limit 30 --no-ma

# BNB daily with EMA
aipa get-ohlcv-data BNBUSDT --limit 50 --ema
```

### Vietnamese Stocks

```bash
# Banking sector — all in one call
aipa get-ohlcv-data VCB TCB MBB CTG --limit 30

# Blue chips
aipa get-ohlcv-data VIC FPT VNM --limit 50

# Market index
aipa get-ohlcv-data VNINDEX --limit 50
```

### Global Stocks

```bash
# US tech stocks
aipa get-ohlcv-data AAPL --limit 50
aipa get-ohlcv-data NVDA --limit 50
aipa get-ohlcv-data TSLA --limit 50

# Market index
aipa get-ohlcv-data SPY --limit 100
```

### Minimal Output (for parsing / spreadsheets)

```bash
# Strip persona header for clean data output
aipa get-ohlcv-data VCB --no-system-prompt

# Raw OHLCV only, no MA, no header — cleanest output
aipa get-ohlcv-data VCB --no-ma --no-system-prompt
```

---

## `aipa ticker-list` — List Available Tickers

List available ticker symbols with metadata (name, group, exchange, source). No LLM involved, no API key needed.

Use this to discover what tickers are available before fetching data.

```bash
aipa ticker-list [--source vn|crypto|global|sjc] [--group GROUP] [--compact]
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `--source` | — | Filter by source: `vn`, `crypto`, `global`, `sjc` |
| `--group` | — | Filter by group (e.g. `NGAN_HANG`, `CHUNG_KHOAN`, `BAT_DONG_SAN`) |
| `--compact` | — | Output symbols only, comma-separated |

### Usage Examples

```bash
# All tickers
aipa ticker-list

# VN stocks only
aipa ticker-list --source vn

# Banking sector
aipa ticker-list --source vn --group NGAN_HANG

# Crypto symbols only (for passing to other commands)
aipa ticker-list --source crypto --compact
```

### Data Fields

Each row includes: ticker, name, group, exchange, source.

---

## `aipa live-data` — Top Tickers by Trading Value

Fetch the latest candle for all tickers or specific tickers. No LLM involved, no API key needed. When no tickers are specified, returns top N tickers sorted by trading value (close × volume) descending.

Use this to quickly identify the most actively traded tickers and get a market overview.

```bash
aipa live-data [TICKERS...] [--top 50] [--interval 1D]
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `TICKERS...` | — | Optional ticker symbols (auto-uppercased). Omit for top N by trading value. |
| `--top N` | `50` | Number of top tickers to show when no tickers specified |
| `--interval` | `1D` | Time interval: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1D`, `1W`, `2W` |
| `--source` | — | Filter by source: `vn`, `crypto`, `global`, `sjc` |

### Usage Examples

```bash
# Top 50 by trading value (broad market overview)
aipa live-data

# Top 10 only
aipa live-data --top 10

# Top 20 hourly
aipa live-data --interval 1h --top 20

# Filter by source: SJC gold
aipa live-data --source sjc

# Filter by source: crypto top 10
aipa live-data --source crypto --top 10

# Specific tickers only
aipa live-data VCB TCB MBB
```

### Data Fields

Each row includes: ticker, time, open, high, low, close, volume, close_changed (%), volume_changed (%), ma10_score, ma50_score.

---

## `aipa performers` — Top/Worst Performers

Rank top and worst performers from live daily data by any metric. No LLM involved, no API key needed. Defaults to VN stocks.

```bash
aipa performers [--sort-by close_changed] [--direction desc] [--limit 10] [--source vn] [--group NGAN_HANG]
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `--sort-by` | `close_changed` | Metric: `close_changed`, `volume`, `value`, `volume_changed`, `ma10_score`, `ma20_score`, `ma50_score`, `ma100_score`, `ma200_score`, `total_money_changed` |
| `--direction` | `desc` | Sort direction: `desc` (strongest first) or `asc` (weakest first) |
| `--limit N` | `10` | Number of entries per list |
| `--min-volume N` | `10000` | Minimum volume for VN tickers |
| `--source` | `vn` | Data source: `vn`, `crypto`, `global`, `sjc` |
| `--group` | — | Filter by sector: `NGAN_HANG`, `CHUNG_KHOAN`, `BAT_DONG_SAN`, `CONG_NGHE`, `DAU_KHI`, etc. |

### Usage Examples

```bash
# Top 10 VN stocks by price change (default)
aipa performers

# Top 5 by volume, ascending
aipa performers --sort-by volume --direction asc --limit 5

# Top 20 by MA50 score
aipa performers --sort-by ma50_score --limit 20

# Crypto performers
aipa performers --source crypto --limit 5

# Top 10 by trading value (close × volume)
aipa performers --sort-by value --limit 10

# By money flow
aipa performers --sort-by total_money_changed --limit 15

# Banking sector only, sorted by value
aipa performers --group NGAN_HANG --sort-by value

# Securities sector top gainers
aipa performers --group CHUNG_KHOAN --sort-by close_changed --limit 5

# Real estate sector by MA50 trend
aipa performers --group BAT_DONG_SAN --sort-by ma50_score
```

---

## `aipa volume-profile` — Volume-by-Price Histogram

Volume profile analysis from 1-minute data showing Point of Control (POC), Value Area, and volume-weighted statistics. No LLM involved, no API key needed.

```bash
aipa volume-profile TICKER [--date YYYY-MM-DD] [--source vn] [--bins 50] [--value-area-pct 70]
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `TICKER` | — | Ticker symbol (required) |
| `--date` | today | Single date (YYYY-MM-DD) |
| `--start-date` / `--end-date` | — | Date range |
| `--source` | auto-detect | Source for tick size: `vn`, `crypto`, `global`, `sjc` |
| `--bins N` | `50` | Number of price bins (2–200) |
| `--value-area-pct` | `70` | Value area target % (60–90) |

### Usage Examples

**Prefer multi-day ranges** over single-day profiles — they produce more reliable support/resistance levels and smooth out intraday noise. Use `--start-date` and `--end-date` covering at least 20 trading days as the default approach. Only use a single `--date` when the user explicitly asks for one specific day.

```bash
# 1-month range for VCB (preferred default)
aipa volume-profile VCB --start-date 2026-04-14 --end-date 2026-05-09

# 2-week range
aipa volume-profile VCB --start-date 2026-04-28 --end-date 2026-05-09 --bins 30

# Specific date (only when user asks for one day)
aipa volume-profile VCB --date 2026-05-09

# Crypto multi-day range
aipa volume-profile BTCUSDT --source crypto --bins 30 --start-date 2026-05-05 --end-date 2026-05-09

# Full options: date range with wider value area
aipa volume-profile FPT --start-date 2026-05-01 --end-date 2026-05-09 --bins 30 --value-area-pct 80
```

### Output

- **POC** (Point of Control): price level with the highest volume
- **Value Area**: price range containing the target % of total volume (default 70%)
- **Statistics**: volume-weighted mean, median, standard deviation, skewness
- **Profile**: binned price levels with volume, percentage, and visual bar chart

---

## Interpreting Output

The CLI outputs to two streams:

- **stdout**: The OHLCV data table. This is what you should present to the user.
- **stderr**: Status messages with structured markers.

### Status Markers (stderr)

| Marker | Meaning |
|---|---|
| `[build]` | Data fetching status and timing |
| `[error]` | Error message |
| `[done]` | Fetch complete, includes total time |

### Data Fields

Each row includes: date/time, open, high, low, close, volume. When `--ma` is enabled (default), moving average columns are also included.

### Attribution

When presenting data or any derived analysis to the user, always include an attribution line at the end of your response:
- **English**: "_Data by [AIPriceAction](https://aipriceaction.com/) | AI-powered analysis — may contain errors. Verify before trading._"
- **Vietnamese**: "_Dữ liệu bởi [AIPriceAction](https://aipriceaction.com/) | Phân tích bởi AI — có thể chứa sai sót. Vui lòng kiểm chứng trước khi giao dịch._"

Do NOT say "analysis provided by AIPriceAction" or "phân tích được cung cấp bởi AIPriceAction". AIPriceAction provides the **data**; the **analysis** is AI-generated and may be inaccurate.

---

## `aipa fundamentals` — Fundamental Data (requires aipa-cli >= 0.1.48)

> **Version gate:** `aipa fundamentals` requires **aipa-cli >= 0.1.48**. Verify before use:
> ```bash
> aipa --version
> # or
> uvx aipa-cli --version
> ```
> If the version is < 0.1.48, upgrade: `uvx aipa-cli@latest fundamentals info ACB` or `pip install --upgrade aipa-cli`.

No LLM involved, no API key needed. Reads from cached `vn.zip` (downloads ~15-20 MB on first call, cached locally after).

> **IMPORTANT:** Fundamentals commands only accept the flags documented below.
>
> **When to fetch:** Do NOT automatically run fundamentals. Technical analysis (VPA, Wyckoff, MA) is the default. When the user says "report" or "báo cáo", they may want fundamentals — if unclear, ask to confirm.

---

## `aipa fundamentals info` — Company Profile

Show company profile, shareholders, and officers for a ticker.

```bash
aipa fundamentals info TICKER [--source vn]
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `TICKER` | — | Ticker symbol (required) |
| `--source` | auto | Data source |

### Usage Examples

```bash
# Company profile for ACB
aipa fundamentals info ACB

# With explicit source
aipa fundamentals info FPT --source vn
```

### Output Fields

Industry, market cap, current price, outstanding shares, top shareholders with ownership %, officers with positions.

---

## `aipa fundamentals ratios` — Financial Ratios

Show financial ratios for a ticker, organized by category. No LLM involved, no API key needed.

```bash
aipa fundamentals ratios TICKER [options]
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `TICKER` | — | Ticker symbol (required) |
| `--latest` | off | Show latest period only (quarterly or yearly) — fastest, single result |
| `--no-yearly` | off | Include quarterly reports |
| `--yearly` | off | Yearly reports only |
| `--year YEAR` | — | Show specific year (e.g. `2024`) |
| `--period PERIOD` | — | Specific period like `"2024"` or `"2024 Q2"` |
| `--category` | all | `valuation`, `profitability`, `leverage`, `liquidity`, `bank`, `efficiency` |
| `--json` | off | Raw JSON output |
| `--source` | auto | Data source |

### Usage Examples

```bash
# All periods (yearly + quarterly) — default
aipa fundamentals ratios VCB

# Latest period only (quarterly or yearly) — quickest, single result
aipa fundamentals ratios VCB --latest

# Specific year
aipa fundamentals ratios VCB --year 2024

# Specific quarter
aipa fundamentals ratios VCB --period "2024 Q2"

# Include quarterly reports (same as default)
aipa fundamentals ratios VCB --no-yearly

# Yearly reports only
aipa fundamentals ratios VCB --yearly

# Only bank-specific fields
aipa fundamentals ratios VCB --category bank

# Raw JSON output
aipa fundamentals ratios VCB --json
```

### Categories

| Category | Fields |
|---|---|
| Valuation | PE, PB, PS, EV/EBITDA, Price/CashFlow, Dividend Yield, Market Cap |
| Profitability | ROE, ROA, ROIC, Gross Margin, After-Tax Margin, Pre-Tax Margin, EBIT Margin, Net Interest Margin |
| Efficiency | Asset Turnover, Fixed Asset Turnover, Cash Cycle, DSO, DIO, DPO |
| Leverage | Debt/Equity, Financial Leverage, Equity/Liabilities, Equity/Loans, Equity/Total Asset |
| Liquidity | Current Ratio, Quick Ratio, Cash Ratio |
| Bank | NPL, LDR, CAR, CASA, CIR, Non-Interest Income, Deposit/Loans Growth, LLR ratios |

---

## `aipa fundamentals rank` — Rank by Fundamental Field

Rank tickers by any of 50+ fundamental fields. No LLM involved, no API key needed.

```bash
aipa fundamentals rank [TICKERS...] [options]
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `tickers` | all VN | Positional ticker symbols |
| `--sort-by` | `roe` | Field to rank by (50+ fields, see below) |
| `--direction` | `desc` | `desc` (highest first) or `asc` (lowest first) |
| `--limit` | `10` | Max results |
| `--latest` | off | Show latest period only (quarterly or yearly) |
| `--yearly` | off | Yearly reports only |
| `--year YEAR` | — | Specific year (e.g. `2024`) |
| `--period PERIOD` | — | Specific period like `"2024"` or `"2024 Q2"` |
| `--watchlist` | — | Use watchlist as ticker source (VN30, VINGROUP, TM, MASAN, custom...) |
| `--source` | auto | Data source |

### Usage Examples

```bash
# Top 10 VN stocks by ROE (default)
aipa fundamentals rank

# Cheapest 20 by PE
aipa fundamentals rank --sort-by pe --direction asc --limit 20

# Banking tickers ranked by CAR
aipa fundamentals rank VCB BID CTG TCB MBB --sort-by car --direction desc

# VN30 watchlist ranked by ROE
aipa fundamentals rank --watchlist VN30 --sort-by roe --limit 15

# Best asset quality (lowest NPL)
aipa fundamentals rank --sort-by npl --direction asc --limit 10

# Highest dividend yield
aipa fundamentals rank --sort-by dividend_yield --direction desc

# Largest by market cap
aipa fundamentals rank --sort-by market_cap --direction desc --limit 20

# Historical year
aipa fundamentals rank --year 2023 --sort-by roe

# Specific quarter
aipa fundamentals rank --period "2016 Q4" --sort-by roe
```

### Sortable Fields (50+)

`pe`, `pb`, `ps`, `ev_to_ebitda`, `price_to_cash_flow`, `dividend_yield`, `market_cap`, `roe`, `roa`, `roic`, `gross_margin`, `after_tax_profit_margin`, `pre_tax_profit_margin`, `ebit_margin`, `net_interest_margin`, `ebit`, `ebitda`, `asset_turnover`, `fixed_asset_turnover`, `debt_to_equity`, `debt_per_equity`, `financial_leverage`, `equity_to_liabilities`, `equity_to_loans`, `total_equity_total_asset`, `owners_equity`, `equity`, `current_ratio`, `quick_ratio`, `cash_ratio`, `cash_cycle`, `day_sale_outstanding`, `days_inventory_outstanding`, `days_payable_outstanding`, `npl`, `ldr_loan_deposit_ratio`, `car`, `casa_ratio`, `cir`, `cost_to_income`, `non_and_interest_income`, `deposit_growth`, `loans_growth`, `loans_loss_reserve_to_loans`, `loans_loss_reserves_to_npl`, `provision_to_outstanding_loans`, `average_cost_of_financing`, `average_yield_on_earning_assets`, `outstanding_shares`, `employees`, `current_price`.

### Ticker Source Resolution (rank and screen)

1. `--watchlist NAME` — resolve from predefined (VN30, VINGROUP...) or custom watchlists
2. Positional `tickers` — explicit list
3. Default — all VN tickers from ticker metadata

---

## `aipa fundamentals screen` — Multi-Criteria Screening

Filter tickers by fundamental criteria, then rank by a field. No LLM involved, no API key needed.

```bash
aipa fundamentals screen [TICKERS...] [options]
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `tickers` | all VN | Positional ticker symbols |
| `--sort-by` | `roe` | Field to rank by (same as rank) |
| `--direction` | `desc` | Sort direction |
| `--limit` | `50` | Max results (1–500) |
| `--latest` | off | Show latest period only (quarterly or yearly) |
| `--yearly` | off | Yearly reports only |
| `--year YEAR` | — | Specific year (e.g. `2024`) |
| `--period PERIOD` | — | Specific period like `"2024"` or `"2024 Q2"` |
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

### Usage Examples

```bash
# Value stocks: low PE + high ROE
aipa fundamentals screen --pe-max 15 --roe-min 0.15 --sort-by roe

# Banking sector only
aipa fundamentals screen --industry "ngân hàng" --sort-by roe

# Safe banks: low NPL + high CAR
aipa fundamentals screen --npl-max 0.015 --car-min 0.10 --sort-by npl --direction asc

# Dividend stocks
aipa fundamentals screen --dividend-yield-min 0.03 --sort-by dividend_yield

# Screen VN30 watchlist
aipa fundamentals screen --watchlist VN30 --pe-max 20 --roe-min 0.10

# Specific tickers
aipa fundamentals screen VCB FPT HPG VNM --roe-min 0.15 --sort-by pe --direction asc

# Historical year
aipa fundamentals screen --year 2024 --sort-by roe

# Specific quarter
aipa fundamentals screen --period "2024 Q3" --sort-by roe
```

### Filter Behavior

- All filters are optional — pass only what you need
- Tickers with missing data for a filtered field are excluded
- Range filters are inclusive: `--roe-min 0.15` matches `roe >= 0.15`
- `--industry` is case-insensitive substring match (e.g. `"ngân hàng"` matches `"Ngân hàng"`)

---

### Fundamental Comparison Workflow

When comparing fundamentals across multiple tickers (e.g., "compare VCB TCB MBB fundamentals", "which bank is healthiest", "rank banks by NPL"), follow this workflow. **Do NOT just call `aipa fundamentals ratios TICKER --latest` for each ticker individually** — that produces N separate outputs that are hard to compare. Use `rank` and `screen` first.

**Step 1: Side-by-side ranking (mandatory)**

Use `aipa fundamentals rank` with the specific tickers to get a comparative table in a single call. Run at least 2 perspectives relevant to the sector:

```bash
# Profitability comparison
aipa fundamentals rank VCB BID CTG TCB MBB --sort-by roe

# Valuation comparison
aipa fundamentals rank VCB BID CTG TCB MBB --sort-by pe --direction asc

# Bank health: asset quality + capital adequacy
aipa fundamentals rank VCB BID CTG TCB MBB --sort-by npl --direction asc
aipa fundamentals rank VCB BID CTG TCB MBB --sort-by car --direction desc

# General stocks: dividend + valuation
aipa fundamentals rank FPT VNM HPG MWG --sort-by dividend_yield --direction desc
aipa fundamentals rank FPT VNM HPG MWG --sort-by pe --direction asc
```

**Step 2: Screen for quality (optional but recommended)**

Use `aipa fundamentals screen` with the tickers to filter by quality criteria. This eliminates weak candidates immediately:

```bash
# Only banks with acceptable asset quality AND profitability
aipa fundamentals screen VCB BID CTG TCB MBB --npl-max 0.015 --roe-min 0.15 --sort-by roe

# Only stocks with reasonable valuation
aipa fundamentals screen VCB FPT HPG VNM --pe-max 20 --roe-min 0.10 --sort-by pe --direction asc

# Entire sector with quality filter
aipa fundamentals screen --industry "ngân hàng" --npl-max 0.02 --car-min 0.09 --sort-by roe
```

**Step 3: Individual deep dive (only for shortlisted tickers)**

Only after Steps 1-2, use `ratios --latest` for individual tickers that ranked at the top or need further investigation. Use `info` for company context:

```bash
aipa fundamentals ratios VCB --latest                # full ratios for top candidate
aipa fundamentals ratios VCB --category bank --latest # bank-specific deep dive
aipa fundamentals info VCB                            # company profile context
```

**Why this matters:** `rank` and `screen` return all tickers in a single comparative table — far more efficient than calling `ratios` N times for N tickers and trying to manually compare across outputs. The ranking shows relative position immediately, and the screen eliminates unsuitable candidates before wasting tokens on deep dives.

---

## When to Use This Skill vs Others

| User Request | Use |
|---|---|
| "Get price data for VCB" | `aipa-data` (this skill) |
| "Show me OHLCV candles for BTC" | `aipa-data` (this skill) |
| "What's the moving average for FPT?" | `aipa-data` (this skill) |
| "Historical prices for VNINDEX" | `aipa-data` (this skill) |
| "What are the top stocks today?" | `aipa live-data` (this skill) |
| "Most active tickers" | `aipa live-data` (this skill) |
| "Show me market overview" | `aipa live-data` (this skill) |
| "What tickers are available?" | `aipa ticker-list` (this skill) |
| "List banking stocks" | `aipa ticker-list --source vn --group NGAN_HANG` (this skill) |
| "Top gainers / losers" | `aipa performers` (this skill) |
| "Best performing stocks" | `aipa performers --sort-by close_changed` (this skill) |
| "Rank by MA score" | `aipa performers --sort-by ma50_score` (this skill) |
| "Volume profile for VCB" | `aipa volume-profile VCB` (this skill) |
| "Where is the POC?" | `aipa volume-profile TICKER` (this skill) |
| "Support/resistance by volume" | `aipa volume-profile TICKER` (this skill) |
| "Company profile for ACB" | `aipa fundamentals info ACB` (this skill) |
| "PE ratio for VCB" | `aipa fundamentals ratios VCB --latest` (this skill) |
| "Top stocks by ROE" | `aipa fundamentals rank --sort-by roe` (this skill) |
| "Screen for low PE banks" | `aipa fundamentals screen --industry "ngân hàng" --pe-max 10` (this skill) |
| "Bank NPL comparison" | `aipa fundamentals rank --sort-by npl --direction asc` (this skill) |
| "Analyze VCB" | `aipa-analyze` (AI analysis) |
| "Compare FPT and VNM" | `aipa-analyze` (AI comparison) |
| "Research the banking sector" | `aipa-research` (multi-agent pipeline) |

Key rule: **raw numbers → `aipa-data`, AI insights → `aipa-analyze`, comprehensive report → `aipa-research`**.

---

## Nhóm Chủ Lực (Core Market Sectors - VN Market Only)

When fetching data or ranking VN tickers, be aware of these core sector groupings for contextual reference:

*   **Nhóm Ngân hàng (Banking):** VCB, BID, CTG, TCB, MBB, ACB, VPB, HDB, SHB, TPB, VIB, SSB, MSB, STB, LPB, EIB.
*   **Nhóm Bất động sản (Real Estate):** VIC, VHM, VRE, VPL, DIG, CEO, L14, TCH, HHS, VGC, IDC.
*   **Nhóm Chứng khoán (Securities):** SSI, VND, HCM, VCI, SHS, VIX, VDS.
*   **Nhóm Trụ cột / Sản xuất & Bán lẻ (Blue-chips / Core Economy):** HPG, HSG, NKG, FPT, MWG, GAS, GVR, PLX, BSR, MSN, VNM, SAB.
*   **Nhóm Hệ sinh thái (Corporate Ecosystems):**
    *   Họ Vingroup: VIC, VHM, VRE, VPL.
    *   Họ Bầu Thụy: STB, LPB, THD, HAG.
    *   Họ Gelex ("Tuấn Mượt"): GEX, GEE, VIX, VGC, EIB, IDC.
    *   Họ Hoàng Huy: TCH, HHS.
    *   Họ A7: DIG, CEO, L14.
    *   Họ TTC (Thành Thành Công): SBT, GEG, VDS.
    *   Họ Masan: MSN, MCH, MSR, MML, VCF, VSN, NET.
    *   Họ Viettel: VGI, CTR, VTP.

*(Note: This classification applies only to the Vietnamese market. Crypto and Global markets do not use this specific grouping yet.)*

---

## Data Usage Policy (CRITICAL)

1. **NEVER generate, guess, estimate, or hallucinate any numbers** — prices, volumes, MA values, MA scores, percentages, dates, or any financial data. Only use data from tool results or user-provided context
2. **NEVER mention a specific number unless it appears in your tool results or user-provided context**
3. **Use tools proactively** — call `aipa get-ohlcv-data` and/or `aipa performers` BEFORE answering price-related questions. Only fall back to asking the user if tools fail
4. **When researching news or events, ALWAYS include the source name** (e.g., "Source: CafeF", "Source: VNExpress")
5. **Trading Hours**: VN market trades 09:00–15:00 ICT (UTC+7), Mon–Fri. Crypto 24/7. If the latest bar shows unusually low volume, the session may still be in progress

## Strict Data Reading & Validation (CRITICAL)

**Symptom:** Misreading or hallucinating the relationship between Price and Moving Averages (e.g., stating a stock is "below EMA20" when it is actually above), or misclassifying a technical event (e.g., calling a failed breakout a "healthy pullback").

**Rules:**

- **Row-by-Row Verification:** When reading OHLCV data output from the CLI, you MUST strictly read the exact row for the exact date requested. Do not accidentally read data from an adjacent row or a different ticker's block in multi-ticker outputs.
- **Precision Filter with Grep:** To minimize reading errors and context volume, always use `grep -E` to isolate your **target dates** across one or multiple tickers. Use `"time"` as your header anchor.
  - *Surgical view (Header + Today + Breakout Day):*
    `uvx aipa-cli get-ohlcv-data TCB MSB STB | grep -E "time|2026-05-27|2026-05-07"`
  - *Comparing recent days:*
    `uvx aipa-cli get-ohlcv-data VND | grep -E "time|2026-05-27|2026-05-26"`
- **Explicit Value Comparison:** Before concluding whether a trend is broken or intact, explicitly state the values being compared: `[Close Price]` vs `[MA/EMA Value]`.
  - *Example:* "Close is 17.750, EMA20 is 16.881. 17.750 > 16.881 → Price is ABOVE EMA20 (Trend intact)."
- **Breakout Validation:** A breakout (significant positive price change + high volume) creates a critical support at the **structural breakout level** — the top of the pre-breakout base/range, the prior swing high, or the pattern's neckline. The breakout candle's **Low** is NOT a reliable invalidation point: it can extend well below the structural level due to gap opens, intraday noise, or volatile entry bars.
  - The correct invalidation is a fall back **below the structural breakout level**, not below the candle's Low.
  - If price pulls back but stays above the structural level, the breakout is intact — this is a healthy pullback.
  - If price falls **below the structural breakout level**, it is a **Failed Breakout / Structural Violation**.
  - *Action:* Always identify the pre-breakout structure first. Only then assess whether a pullback is healthy (above structure) or a failure (below structure).

---

## Precheck Mandatory — Safety Gate Before Every Entry Decision

**Symptom:** Analyzing entry on low timeframe (15m/1h) without checking higher timeframe structure — missing historical supply zones (Buying Climax, UTAD) that invalidate the thesis.

**Rule:** Before proposing ANY entry, trade decision, or price target, you MUST run:

```bash
# 1. Daily 60 — detect BC/UTAD/historical structure
aipa get-ohlcv-data TICKER --limit 60 --no-ma

# 2. Weekly 52 — confirm overall trend direction
aipa get-ohlcv-data TICKER --interval 1W --limit 52 --no-ma

# 3. Volume Profile 30+ days — identify key price levels
aipa volume-profile TICKER --start-date [30+ days ago] --end-date [today]
```

This is a **safety gate**, not analysis. The purpose is to detect structural red flags before spending tokens on deeper analysis:

| Detection | Meaning | Action |
|---|---|---|
| **Buying Climax (BC)** at current price zone | Historical volume peak = large supply | DO NOT enter here. Lower entry zone or cancel. |
| **Upthrust After Distribution (UTAD)** | False breakout, bull trap | Cancel entry. Wait for return to range. |
| **Weekly trend bearish** (price below MA20/50 weekly) | Overall trend is down | DO NOT go long. Wait for weekly reversal. |
| **SOS confirmed + weekly up** | Genuine breakout | ✅ Proceed with entry analysis. |
| **Spring at POC + weekly support** | Successful bottom test | ✅ Entry is valid. |

**Preflight checklist before every entry:**

```
□ Daily 60 — no BC/UTAD at current price zone
□ Weekly 52 — trend aligned with entry direction
□ VP 30d — TP anchored to swing high/VAH (not round number)
□ SL below POC/VAH/MA20
□ R:R ≥ 1:2 after precheck
```

> **Root cause of the FPT error on June 4, 2026:** Skipped daily 60 + weekly 52, went straight to 15m intraday. Saw pullback from 77,700 to 76,300 with declining volume → incorrectly assumed LPS (Last Point of Support) in a healthy markup. Missed the May 20 Buying Climax (high 77,432, close 76,643, volume 26M — 2.2× average) which was the exact same supply zone price was retesting at 76,300-76,500 on June 4. Precheck (daily 60 + weekly 52 + VP 30d) would have caught the BC immediately and blocked any buy recommendation at that level.

---

## Calculate Metrics with Python — No Hallucinated Numbers

**Symptom:** AI writes "vol 5x TB" or "volume gấp 20x trung bình" based on day-over-day % change (`+557% vs yesterday`) instead of computing the actual volume-vs-average multiplier. The day-over-day % change and the vs-20d-average multiplier are **completely different metrics** — confusing them produces wrong claims.

**Rule:** Before writing ANY numerical claim in analysis (volume multiplier, R:R ratio, average cost, MA distance), you MUST compute it using `aipa-cli | python3` pipe. NEVER estimate or guess.

### Volume vs 20-day Average (the most common mistake)

```bash
uvx aipa-cli get-ohlcv-data TICKER --limit 50 --no-ma --no-system-prompt 2>/dev/null | python3 -c "
import sys
from collections import defaultdict
data = defaultdict(list)
for line in sys.stdin:
    parts = line.split()
    if len(parts) >= 7 and parts[0] != 'time':
        data[parts[-1]].append((parts[0], int(float(parts[5]))))
for sym, rows in data.items():
    if len(rows) >= 21:
        avg20 = sum(r[1] for r in rows[-21:-1]) / 20
        last_vol = rows[-1][1]
        print(f'{sym} {rows[-1][0]}: vol={last_vol/1e6:.1f}M | avg20d={avg20/1e6:.1f}M | ratio={last_vol/avg20:.1f}x')
"
```

**Anti-pattern:** NEVER do this:
- `volume_changed` is +557% → write "vol 5x TB" ❌ (557% ≠ 5x, and it's vs yesterday, not vs 20d avg)
- `volume_changed` is +216% → write "vol 2x TB" ❌ (same mistake)

**Correct pattern:**
- `volume_changed` +557% means today's volume is 6.57× yesterday's volume (1 + 5.57)
- To get vs-20d-average multiplier, you MUST compute: `today_vol / average(last_20_days_vol)`

### Batch Volume Verification (multiple tickers + specific dates)

```bash
uvx aipa-cli get-ohlcv-data VCB TCB MBB --limit 50 --no-ma --no-system-prompt 2>/dev/null | python3 -c "
import sys
from collections import defaultdict
data = defaultdict(list)
for line in sys.stdin:
    parts = line.split()
    if len(parts) >= 7 and parts[0] != 'time':
        data[parts[-1]].append((parts[0], int(float(parts[5]))))
checks = [
    ('VCB', '2026-06-10', 20),
    ('TCB', '2026-06-10', 20),
    ('MBB', '2026-06-10', 20),
]
for sym, date, ndays in checks:
    for i, (d, v) in enumerate(data.get(sym, [])):
        if d == date:
            start = max(0, i - ndays)
            avg = sum(r[1] for r in data[sym][start:i]) / len(data[sym][start:i])
            print(f'{sym} {date}: vol={v/1e6:.1f}M avg{ndays}d={avg/1e6:.1f}M ratio={v/avg:.1f}x')
            break
"
```

### R:R Calculation

```bash
python3 -c "
entry, sl, tp = 8400, 7700, 9500
risk = entry - sl
reward = tp - entry
rr = reward / risk
print(f'Risk={risk} Reward={reward} R:R={rr:.1f}:1 {\"OK\" if rr >= 2 else \"BLOCKED - R:R < 1:2\"}')"
```

### Average Cost (multi-lot)

```bash
python3 -c "
lots = [(8400, 1000), (8600, 500)]
total_qty = sum(q for _, q in lots)
avg = sum(p * q for p, q in lots) / total_qty
print(f'Avg cost: {avg:.0f} | Qty: {total_qty}')"
```

### MA Distance %

```bash
uvx aipa-cli get-ohlcv-data TICKER --limit 50 --no-ma --no-system-prompt 2>/dev/null | python3 -c "
import sys
rows = []
for line in sys.stdin:
    parts = line.split()
    if len(parts) >= 7 and parts[0] != 'time':
        rows.append({'date': parts[0], 'close': float(parts[4]), 'ticker': parts[-1]})
if rows:
    closes = [r['close'] for r in rows]
    ma20 = sum(closes[-21:-1]) / 20
    pct = (rows[-1]['close'] - ma20) / ma20 * 100
    print(f\"{rows[-1]['ticker']} close={rows[-1]['close']:.0f} MA20={ma20:.0f} dist={pct:+.1f}%\")"
```

**Mandatory rule:** If you cannot verify a number with a pipe command, do NOT write it in any file. Use the actual computed value, rounded to 1 decimal place for multipliers and 0.1% for percentages.

---

## Tips for AI Agents

1. **No API key or backend needed**: `get-ohlcv-data` fetches from public S3 archives. Works without `OPENAI_API_KEY` or a running backend.

2. **Auto-uppercase**: Ticker symbols are automatically uppercased. `vcb`, `btcusdt`, `spy` all work.

3. **Default is 20 bars**: If the user doesn't specify a count, they get 20 bars. Use `--limit 50` or `--limit 100` when more context is needed.

4. **`--no-ma --no-system-prompt` for clean data**: When the user wants raw numbers for their own analysis or a spreadsheet, strip everything except OHLCV with these flags.

5. **For AI analysis, use `aipa analyze`**: If the user wants insights, patterns, or recommendations, use the `aipa-analyze` skill instead. This skill is for raw data only.

6. **Date range vs limit**: Use `--start-date`/`--end-date` for specific periods. Use `--limit` for "last N bars". Don't combine both — the CLI handles conflicts gracefully but the intent is clearer with one approach.

7. **MA type controlled by setting**: By default, SMA is shown (`use_sma: true` in `~/.aipriceaction/settings.json`). Use `--sma` to force SMA or `--ema` to force EMA. Change the default with `aipa config set use_sma false`.

8. **Multi-ticker support**: Pass multiple space-separated tickers to fetch them in one call (e.g. `aipa get-ohlcv-data VCB TCB MBB`). The output table includes a `symbol` column to distinguish rows.

9. **Use `aipa live-data` for market overview**: When you need to identify the most active tickers or get a broad market snapshot, use `aipa live-data` instead of fetching individual tickers. It returns the latest candle sorted by trading value. Call it first with no arguments to discover what's moving, then drill into specific tickers with `get-ohlcv-data`.

10. **Use `aipa ticker-list` to discover tickers**: When you need to know what tickers are available or find tickers in a specific sector, use `aipa ticker-list`. Add `--group` to filter by sector (e.g. `NGAN_HANG` for banking) and `--compact` to get a comma-separated list for passing to other commands.

11. **Use `aipa performers` for ranking — run multiple perspectives**: When the user asks about market movers, top stocks, or "what's happening", run `aipa performers` with multiple `--sort-by` values to get a multi-perspective view. **Always run at least these two**: default (price change) and value (trading value). Add MA scores when the user cares about trends. Run them all — do not pick just one:

    ```bash
    aipa performers                                          # price change — top gainers / worst losers
    aipa performers --sort-by value                          # trading value — where the money flows
    aipa performers --sort-by ma50_score                     # MA50 trend — strongest/weakest medium-term trends
    aipa performers --sort-by ma20_score                     # MA20 trend — strongest/weakest short-term trends
    aipa performers --sort-by total_money_changed            # money flow change — unusual capital activity
    aipa performers --group NGAN_HANG --sort-by value        # banking sector by trading value
    aipa performers --group CHUNG_KHOAN --sort-by close_changed  # securities sector top gainers
    ```

    Cross-referencing these lists gives much richer insight than any single sort. A ticker appearing in both the top gainers AND top value lists is more significant than one appearing in only one. The AI agent can also call the `get_performers` tool directly.

12. **Use `aipa volume-profile` for volume analysis**: When you need to identify key price levels based on traded volume, use `aipa volume-profile`. It shows where the most volume was traded (POC), the value area, and volume-weighted statistics from 1-minute data. **Prefer multi-day ranges** (`--start-date` + `--end-date`, at least 20 trading days) over single-day profiles — multi-day ranges produce more reliable support/resistance levels and smooth out intraday noise. The AI agent can also call the `get_volume_profile` tool directly.
