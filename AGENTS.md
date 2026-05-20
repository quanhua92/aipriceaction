# Market Analysis Workflow with aipa-cli

Self-contained reference for using the `aipa` CLI with any AI agent. Works with Claude Code, Gemini CLI, Cursor, Codex, and others.

**Language:** Use `--lang vn` on any command to get Vietnamese output.

## 1. Agent Role
- **Senior Market Analyst:** Use real data to produce objective analysis.
- **Surgical Editor:** Update reports precisely without disrupting file structure.

## 2. Tool: aipa-cli

`aipa` is an AI-powered financial analysis CLI for Vietnamese stocks, cryptocurrencies, and global assets.

### Install & Caching

```bash
# Preferred: uvx (no install needed, always up-to-date)
uvx aipa-cli <command>

# Fallback: pip (if uv is not available)
pip install aipa-cli
aipa <command>

# Last fallback: system pip (if pip fails due to PEP 668)
python3 -m pip install aipa-cli --break-system-packages
aipa <command>
```

**Always prefer `uvx aipa-cli` over `aipa`.** When using `uvx`:

1. **First invocation of the session** — add `--refresh-package` to pull the newest version once:
   `uvx --refresh-package aipa-cli <command>`
2. **Subsequent invocations** — use plain `uvx` for fast cached execution:
   `uvx aipa-cli <command>`
3. **Fallback on failure** — if a command fails with a schema or missing argument error, retry with `@latest`:
   `uvx aipa-cli@latest <command>`

### Data Sources

| Source | Example tickers | Flag |
|---|---|---|
| **Vietnamese stocks** | VIC, VCB, FPT, HPG, VNM, MBB, TCB... | `--source vn` (auto-detect) |
| **Crypto** | BTCUSDT, ETHUSDT, SOLUSDT... | `--source crypto` |
| **Global** | AAPL, TSLA, NVDA, SPY... | `--source global` |
| **SJC Gold** | SJC gold prices | `--source sjc` |

### Built-in Watchlists

| Name | Tickers | Count |
|---|---|---|
| **VN30** | ACB, BID, **BSR**, CTG, FPT, GAS, GVR, HDB, HPG, LPB, MBB, MSN, MWG, PLX, SAB, SHB, SSB, SSI, STB, TCB, TPB, VCB, VHM, VIB, VIC, VJC, VNM, VPB, VRE, VPL | 30 |
| **VINGROUP** | VIC, VHM, VRE, VPL | 4 |
| **INDEX** | VNINDEX, VN30, VN30F1M, VN100, VNMIDCAP... | 22 |

```bash
aipa watchlist ls                    # list all
aipa watchlist get VN30              # get tickers
aipa watchlist set MYWATCH FPT VCB   # create custom
```

### aipa-data — Raw OHLCV Data (no API key needed)

#### `aipa get-ohlcv-data`

```bash
aipa get-ohlcv-data VCB                               # last 20 candles with EMA
aipa get-ohlcv-data VCB --limit 50                    # 50 candles
aipa get-ohlcv-data VCB TCB MBB --limit 30            # multi-ticker
aipa get-ohlcv-data BTCUSDT --interval 1h --limit 50  # crypto hourly
aipa get-ohlcv-data FPT --start-date 2025-01-01       # from date
aipa get-ohlcv-data VCB --no-ma --no-system-prompt    # cleanest raw output
```

| Flag | Default | Description |
|---|---|---|
| `--interval` | `1D` | `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1D`, `1W`, `2W` |
| `--limit N` | 20 | Number of bars |
| `--start-date` / `--end-date` | — | Date range |
| `--source` | auto-detect | `vn`, `crypto`, `global` |
| `--ma` / `--no-ma` | included | Include/exclude moving averages |
| `--no-system-prompt` | — | Strip header for clean output |

#### `aipa live-data`

```bash
aipa live-data                        # top 50 by trading value
aipa live-data --top 10               # top 10
aipa live-data VCB TCB MBB            # specific tickers
aipa live-data --source crypto --top 10
```

#### `aipa performers`

Rank tickers by any metric. **Always run at least 2 perspectives**: price change + value.

```bash
aipa performers                                          # top gainers / losers
aipa performers --sort-by value                          # where money flows
aipa performers --sort-by ma50_score                     # medium-term trend
aipa performers --sort-by ma20_score                     # short-term trend
aipa performers --sort-by total_money_changed            # unusual money flow
aipa performers --group NGAN_HANG --sort-by value        # banking sector
aipa performers --group CHUNG_KHOAN --sort-by close_changed  # securities sector
aipa performers --source crypto --sort-by value          # crypto
```

| Flag | Default | Description |
|---|---|---|
| `--sort-by` | `close_changed` | `close_changed`, `volume`, `value`, `ma10_score`, `ma20_score`, `ma50_score`, `ma100_score`, `ma200_score`, `total_money_changed` |
| `--direction` | `desc` | `desc` (strongest first) or `asc` (weakest first) |
| `--limit N` | `10` | Number of results |
| `--source` | `vn` | `vn`, `crypto`, `global`, `sjc` |
| `--group` | — | `NGAN_HANG`, `CHUNG_KHOAN`, `BAT_DONG_SAN`, `CONG_NGHE`, `DAU_KHI`... |

#### `aipa volume-profile`

**Prefer multi-day ranges** (`--start-date` + `--end-date`, at least 20 trading days) over single day — produces more reliable support/resistance levels.

```bash
# 1 month (recommended default)
aipa volume-profile VCB --start-date 2026-04-14 --end-date 2026-05-09

# 2 weeks
aipa volume-profile VCB --start-date 2026-04-28 --end-date 2026-05-09 --bins 30

# Crypto
aipa volume-profile BTCUSDT --source crypto --bins 30 --start-date 2026-05-05 --end-date 2026-05-09
```

| Flag | Default | Description |
|---|---|---|
| `--date` | today | Single date (only when user explicitly asks) |
| `--start-date` / `--end-date` | — | Date range |
| `--source` | auto-detect | `vn`, `crypto`, `global`, `sjc` |
| `--bins N` | `50` | Number of price bins (2–200) |
| `--value-area-pct` | `70` | Value area % (60–90) |

#### `aipa ticker-list`

```bash
aipa ticker-list                            # all tickers
aipa ticker-list --source vn                # VN stocks only
aipa ticker-list --source vn --group NGAN_HANG   # banking sector
aipa ticker-list --source crypto --compact  # comma-separated
```

### aipa-analyze — AI Analysis (OPENAI_API_KEY optional)

```bash
aipa analyze VCB                                      # single ticker
aipa analyze VCB TCB MBB CTG VPB                      # multi-ticker comparison
aipa analyze BTCUSDT --interval 4h --limit 50         # crypto 4h
aipa analyze FPT --start-date 2025-01-01 --end-date 2025-05-01
aipa analyze VCB --lang vn                            # Vietnamese output
aipa analyze HPG --question "Wyckoff analysis with phases and price targets"
aipa analyze VCB --context-only                       # dump context, no LLM call
```

| Flag | Default | Description |
|---|---|---|
| `--interval` | `1D` | `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1D`, `1W`, `2W` |
| `--limit N` | `20` | Number of bars |
| `--source` | auto-detect | `vn`, `crypto`, `global` |
| `--start-date` / `--end-date` | — | Date range |
| `--lang` | saved setting | `en` or `vn` |
| `--question TEXT` | template 0 | Custom analysis question |
| `--context-only` | — | Dump raw context, no API key needed |
| `--verbose` | — | Show thinking tokens |

**No API key fallback:** `aipa analyze` automatically dumps context to stdout. The agent should read it and perform analysis itself.

### aipa-research — Multi-Agent Deep Research

```bash
aipa deep-research                          # market snapshot (fast, no API key)
aipa deep-research --source crypto          # crypto snapshot
aipa deep-research --run                    # full pipeline (5-10 min, needs API key)
aipa deep-research --run --lang vn          # Vietnamese output
aipa deep-research --run --output report.md # save to file
```

### When to Use Which Command

| Request | Use |
|---|---|
| Top gainers / losers | `aipa performers` |
| Where is money flowing | `aipa performers --sort-by value` |
| Market snapshot | `aipa live-data` |
| Get price data for VCB | `aipa get-ohlcv-data VCB` |
| Analyze VCB | `aipa analyze VCB` |
| Compare VCB, TCB, MBB | `aipa analyze VCB TCB MBB` |
| Volume profile / POC | `aipa volume-profile VCB` |
| List banking stocks | `aipa ticker-list --source vn --group NGAN_HANG` |
| Comprehensive research | `aipa deep-research` + agent pipeline |

**Rule:** raw numbers → `get-ohlcv-data` / `performers` / `live-data`, AI insights → `analyze`, comprehensive report → `deep-research`.

## 3. Workflow

### Step 1: Research Context
- Review the most recent report (`YYYY-MM-DD.md`) to understand layout style, tracked sectors, and portfolio state.
- Check `DANH_MUC.md` or `research.sh` (if present) for priority tickers.

### Step 2: Broad Market Data
Use `aipa-cli` to build a market overview:
- `performers`: Top movers by price, volume, trading value, and MA Score.
- `live-data`: Check index status (VNINDEX, VN30).
- `performers --group SECTOR`: Check sector-specific movements.

### Step 3: Deep Analysis
Use `analyze` to read Smart Money behavior:
- Identify signals: SOS (Sign of Strength), SOW (Sign of Weakness), Buying Climax, Test for Supply.
- Evaluate momentum via MA Score (EMA10, 20, 50, 200).
- Use `--lang vn` for Vietnamese output when the user writes in Vietnamese.

### Step 4: Draft Report
- Create a new report file for the current date.
- Standard layout:
    1. Market Overview (Index, Liquidity, State).
    2. Money Flow & Sector Analysis (Highlights, Warnings).
    3. Action Journal & Risk Management (Hold, Sell, New opportunities).
    4. Strategy for next session.

### Step 5: Refine & Update
- Accept specific user requests about tickers or sectors.
- Use `replace` to update report sections, keeping structure intact and avoiding repetition.

## 4. Attribution

When presenting data or analysis, always include:

- **Vietnamese:** "_Dữ liệu bởi [AIPriceAction](https://aipriceaction.com/) | Phân tích bởi AI — có thể chứa sai sót. Vui lòng kiểm chứng trước khi giao dịch._"
- **English:** "_Data by [AIPriceAction](https://aipriceaction.com/) | AI-powered analysis — may contain errors. Verify before trading._"

---
_Developed by [AIPriceAction](https://aipriceaction.com/). More data and documentation at https://aipriceaction.com_
