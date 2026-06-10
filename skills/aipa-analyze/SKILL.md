---
name: aipa-analyze
description: >
  AI-powered stock and crypto analysis using the aipa CLI. Use this skill
  whenever the user asks to analyze a ticker, compare stocks, get technical
  analysis, or answer any financial market question about Vietnamese stocks
  (VIC, VCB, FPT...), cryptocurrencies (BTC, ETH...), or global assets.
  Also use for price action analysis, moving average analysis, support/resistance
  questions, sector comparison, Wyckoff analysis, or trading insights.
  Also handles fundamental analysis when the user explicitly asks for
  fundamentals, PE, ROE, NPL, CAR, valuation, or "phân tích cơ bản" —
  use `aipa fundamentals` commands to enrich technical analysis with
  financial ratios, company info, and fundamental screening/ranking.
  For raw price data without AI, use the aipa-data skill instead.
---

# aipa-analyze

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

`aipa` is an AI-powered financial analysis CLI for Vietnamese stocks, cryptocurrencies, and global assets. It combines OHLCV price data with LLM analysis to produce actionable trading insights using frameworks like Wyckoff, VPA (Volume Price Analysis), and MA momentum scoring.

## Installation

Use `uvx` — no install needed. On the **first call of each session**, use `uvx aipa-cli@latest` to refresh the cache. For all subsequent calls, use plain `uvx aipa-cli` (fast, cached).

```bash
# First call of the session — refresh cache
uvx aipa-cli@latest analyze VCB

# All subsequent calls — fast cached execution
uvx aipa-cli analyze TCB

# Fallback: pip (if uv is not available)
pip install aipa-cli
aipa analyze VCB

# Fallback: system pip (if pip fails due to PEP 668)
python3 -m pip install aipa-cli --break-system-packages
aipa analyze VCB

# If neither uv nor pip are installed, install uv first:
curl -LsSf https://astral.sh/uv/install.sh | sh
# If the install script fails, see: https://docs.astral.sh/uv/getting-started/installation/
```

All command examples in this skill use `aipa` for brevity. Replace `aipa` with `uvx aipa-cli` if not installed globally.

## Keeping the CLI Updated

The aipa CLI is actively developed with frequent improvements. **Always prefer `uvx aipa-cli` over `aipa`**. When using `uvx`:

1. **Use plain `uvx` for fast cached execution:**
   `uvx aipa-cli analyze VCB`
2. **Fallback on failure** — if a command fails with a schema or missing argument error, retry with `@latest`:
   `uvx aipa-cli@latest analyze VCB`

If `aipa` is installed globally via `pip`, update before each session:

```bash
pip install --upgrade aipa-cli
```

The agent should prefer `uvx aipa-cli` for all commands. Only fall back to `aipa` if `uvx` is unavailable.

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENAI_API_KEY` | Yes (for AI) | — | API key for the LLM provider |
| `OPENAI_BASE_URL` | No | OpenRouter | Base URL for OpenAI-compatible API |
| `OPENAI_MODEL` | No | `openrouter/owl-alpha` | Model to use for analysis |

Run `aipa setup` for interactive first-run configuration. Settings are saved to `~/.aipriceaction/settings.json`.

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

# Using watchlist tickers in analyze
aipa analyze $(aipa watchlist get VN30)
```

### Supported Intervals

| Interval | Description |
|---|---|
| `1m` | 1 minute |
| `5m` | 5 minutes |
| `15m` | 15 minutes |
| `30m` | 30 minutes |
| `1h` | 1 hour |
| `4h` | 4 hours |
| `1D` | 1 day (default) |
| `1W` | 1 week |

Note: All intervals work natively — `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1D`, `1W`, `2W`. Non-native intervals are aggregated client-side from base data.

---

## `aipa analyze` — AI Analysis

Run AI-powered technical analysis on one or more tickers.

```bash
aipa analyze TICKER [TICKERS...] [options]
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `TICKER [TICKERS...]` | — | One or more ticker symbols (auto-uppercased) |
| `--interval` | `1D` | Time interval: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1D`, `1W`, `2W` |
| `--limit N` | `20` | Number of bars/candles to fetch |
| `--source` | auto-detect | Filter by source: `vn`, `crypto`, `global` |
| `--start-date` | — | Start date (e.g. `2025-04-01`) |
| `--end-date` | — | End date (e.g. `2025-04-30`) |
| `--reference-ticker` | auto-detect | Reference ticker: `VNINDEX` (VN stocks), `BTCUSDT` (crypto), `^GSPC` (global) |
| `--lang` | saved setting | Language: `en` or `vn` |
| `--ma-type` | `ema` | Moving average type: `ema` or `sma` |
| `--question TEXT` | template 0 | Custom analysis question (overrides templates) |
| `--questions` | — | List available question templates and exit |
| `--context-only` | — | Dump raw context without calling LLM (no API key needed) |
| `--no-system-prompt` | — | Exclude system prompt from context output |
| `--verbose` | — | Show thinking tokens during analysis |

### Usage Examples

```bash
# Basic single-ticker analysis
aipa analyze VCB

# Multi-ticker comparison
aipa analyze VCB TCB MBB CTG VPB

# Specific interval and bar count
aipa analyze BTCUSDT --interval 4h --limit 50

# Custom date range
aipa analyze FPT --start-date 2025-01-01 --end-date 2025-05-01

# Custom analysis question
aipa analyze VIC --question "What is the Wyckoff phase and what are the key price targets?"

# Vietnamese language output
aipa analyze VNM --lang vn

# Dump raw data context without AI analysis (no API key needed)
aipa analyze VCB --context-only

# Show thinking tokens during analysis
aipa analyze HPG --verbose

# Override auto-detected reference ticker
aipa analyze VCB --reference-ticker VN30

# Specific source
aipa analyze BTCUSDT --source crypto
```

---

## Analysis Workflow

Follow this multi-step workflow for every analysis request. Do NOT just run `aipa analyze` and stop.

### Step 1: Daily Timeframe Analysis

Run `aipa analyze` on the daily chart first. Use `--limit 50` minimum for sufficient context. For Wyckoff phase identification or TP setting, use `--limit 60` or higher.

```bash
aipa analyze VCB --limit 50
```

### Step 2: Volume Profile for Support/Resistance

Run `aipa volume-profile` to get structural price levels (POC, Value Area, high-volume nodes). Use a multi-day range covering at least 20 trading days.

```bash
aipa volume-profile VCB --start-date 2026-04-14 --end-date 2026-05-27
```

**Note:** The dates above are examples. Always use a range covering at least **30 trading days** (roughly 6 calendar weeks) ending on today. Calculate `--start-date` dynamically.

Cross-reference the volume profile levels with the daily analysis — key S/R levels with high volume clusters are more significant.

### Step 3: Intraday Deep Dive (If Needed)

Based on the daily analysis, decide whether an intraday look adds value:

| Trigger | Action |
|---|---|
| Daily shows breakout/reversal forming NOW | Run `--interval 1h --limit 50` to see entry timing |
| Daily shows tight consolidation near key level | Run `--interval 4h --limit 50` to check for patterns |
| User asks about entry/exit timing or scalping | Run `--interval 15m --limit 50` for micro structure |
| Daily chart is clear and no timing ambiguity | Skip intraday — daily analysis is sufficient |

```bash
# 1h for entry/exit timing
aipa analyze VCB --interval 1h --limit 50

# 15m for scalping or micro structure
aipa analyze VCB --interval 15m --limit 50
```

### Step 4: Present Combined Analysis

Synthesize all steps into a single response:
- **Daily chart**: Trend, Wyckoff phase, key levels
- **Volume profile**: Structural S/R with volume confirmation
- **Intraday** (if run): Entry/exit timing, short-term patterns

Do NOT present each step as a separate section. Combine insights into a coherent analysis.

---

## When to Use This Skill vs Others

| User Request | Use |
|---|---|
| "Analyze VCB" | `aipa analyze VCB` |
| "Compare FPT and VNM" | `aipa analyze FPT VNM` |
| "Wyckoff analysis for HPG" | `aipa analyze HPG --question "Wyckoff analysis with phases, events, and price targets"` |
| "Research the banking sector deeply" | Use the `aipa-research` skill instead |
| "Get price data for VCB" | Use the `aipa-data` skill instead |
| "Show me OHLCV candles for BTC" | Use the `aipa-data` skill instead |
| "What are the top stocks today?" | `aipa live-data` (no AI, quick market overview) |
| "Top gainers / losers" | `aipa performers` (no AI) |
| "Best stocks by trading value" | `aipa performers --sort-by value` (no AI) |
| "Volume profile / POC / value area" | `aipa volume-profile TICKER` (no AI) |
| "What banking stocks are available?" | `aipa ticker-list --source vn --group NGAN_HANG` |

Key rule: **AI insights → `aipa-analyze`, raw numbers → `aipa-data`, comprehensive report → `aipa-research`, quick market overview → `aipa live-data`**.

---

## Question Templates

The CLI includes pre-built analysis question templates organized by framework. Use `--questions` to list all available templates.

### Single-Ticker Templates (English)

| Index | Template | Description |
|---|---|---|
| 0 | Trading Opportunity | Wyckoff phases, Smart Money behavior, deployment roadmap, risk management |
| 1 | News & Events Research | Detect extreme moves (>6.7% or Volume >150%), web search for causes |
| 2 | Price Action & Volume | VPA analysis, smart money footprints, supply/demand zones |
| 3 | MA Momentum & Trend | MA alignment, crossover detection, volume confirmation |
| 4 | Wyckoff Method | Wyckoff phases, Spring/Upthrust/SOS events, price targets |
| 5 | Bob Volman Price Action | Micro pullback entries, breakout/fading setups, trade planning |

### Multi-Ticker Templates (English)

| Index | Template | Description |
|---|---|---|
| 0 | Trading Opportunity | Analyze all tickers, rank by opportunity quality |
| 1 | Stock Performance Comparison | Compare price action strength, MA momentum, volume |
| 2 | Market Trend Analysis | Sector rotation via MA scores, accumulation/distribution |
| 3 | Risk & Support/Resistance | Map S/R levels with volume context |
| 4 | News & Events Research | Detect extreme moves across multiple tickers |
| 5 | Bob Volman Price Action | Applied to multiple tickers with ranking |
| 6 | Wyckoff Method | Multi-ticker Wyckoff analysis with ranking |

Vietnamese translations exist for all templates (use `--lang vn`).

### Using Templates

```bash
# List all templates
aipa analyze --questions

# Use a specific template by index (TUI only: /analyze VCB 2)
# In CLI, use --question for custom questions
aipa analyze HPG --question "Phân tích theo phương pháp Wyckoff: xác định pha, sự kiện quan trọng (Spring/Upthrust/SOS), mục tiêu giá và xác nhận khối lượng"
```

---

## Interpreting Output

The CLI outputs to two streams:

- **stdout**: The final analysis result (the AI's response). This is what you should present to the user.
- **stderr**: Status messages with structured markers for tracking progress.

### Status Markers (stderr)

| Marker | Meaning |
|---|---|
| `[build]` | Context building status and timing |
| `[analyze]` | Analysis question being sent to the LLM |
| `[tool]` | Tool call being executed (e.g., fetching OHLCV data) |
| `[tool-result]` | Tool execution result returned |
| `[thinking]` | Agent reasoning tokens (only with `--verbose`) |
| `[error]` | Error message |
| `[done]` | Analysis complete, includes total time |
| `[result]` | Final analysis output follows |

When presenting results to the user, always use the stdout output (after the `[result]` marker in stderr).

### Attribution

When presenting any analysis to the user, always include an attribution line at the end of your response:
- **English**: "_Data by [AIPriceAction](https://aipriceaction.com/) | AI-powered analysis — may contain errors. Verify before trading._"
- **Vietnamese**: "_Dữ liệu bởi [AIPriceAction](https://aipriceaction.com/) | Phân tích bởi AI — có thể chứa sai sót. Vui lòng kiểm chứng trước khi giao dịch._"

Do NOT say "analysis provided by AIPriceAction" or "phân tích được cung cấp bởi AIPriceAction". AIPriceAction provides the **data**; the **analysis** is AI-generated and may be inaccurate.

---

## No API Key — Agent Fallback

When `OPENAI_API_KEY` is not set, `aipa analyze` prints the raw context to stdout and exits with `[info]` messages instead of an error. The context contains all the OHLCV data, MA scores, and market metadata needed for analysis.

**If you are an AI agent and see this fallback output, you should perform the analysis yourself** using the printed context:

1. Read the OHLCV data, MA scores, and market context from the stdout output
2. Apply the same analytical framework the CLI would have used (Wyckoff, VPA, MA momentum, etc.)
3. Produce the analysis result as if the LLM agent had generated it
4. Present the result to the user in the same format the CLI normally would

The context output is identical to `aipa analyze --context-only`, so treat it as your input data and do the reasoning yourself.

---

## Fundamentals: Ask Before Running

**Do NOT automatically run `aipa fundamentals` commands unless the user explicitly asks for fundamental data or fundamental analysis (phân tích cơ bản).** Technical analysis (VPA, Wyckoff, MA) is the default. Only fetch fundamentals when:

- The user explicitly says "fundamentals", "fundamental analysis", "cơ bản", "phân tích cơ bản", "PE", "ROE", "NPL", "CAR", etc.
- The user asks about valuation, profitability, or financial health
- The user asks to compare stocks by financial metrics (not price action)
- The user says "report" or "báo cáo" — these may imply financial reports. If unclear, ask to confirm.

> **Version gate:** `aipa fundamentals` requires **aipa-cli >= 0.1.45**. Verify before use:
> ```bash
> aipa --version
> ```
> If < 0.1.45, upgrade: `uvx aipa-cli@latest` or `pip install --upgrade aipa-cli`.

When fundamentals are relevant, use these commands to enrich your technical analysis:

```bash
# Quick company snapshot
aipa fundamentals info VCB

# Latest financial ratios
aipa fundamentals ratios VCB --latest

# Bank-specific metrics (NPL, CAR, CASA, CIR)
aipa fundamentals ratios VCB --category bank --latest

# Compare ROE across peers
aipa fundamentals rank VCB BID CTG TCB MBB --sort-by roe

# Rank by historical year
aipa fundamentals rank --year 2023 --sort-by roe

# Screen for value stocks
aipa fundamentals screen --pe-max 15 --roe-min 0.15 --sort-by roe

# Screen specific quarter
aipa fundamentals screen --period "2024 Q3" --sort-by roe

# Screen banking sector for asset quality
aipa fundamentals screen --industry "ngân hàng" --npl-max 0.015 --sort-by npl --direction asc
```

### How Fundamentals Enhance Technical Analysis

When the user asks for fundamentals, combine technical and fundamental views:

1. **Valuation context**: Is the stock expensive (high PE/PB) or cheap? A breakout at PE=8 is different from PE=30.
2. **Bank health (VN banks)**: NPL, CAR, CASA, CIR provide critical risk context. High NPL + bearish technicals = strong sell signal.
3. **Profitability confirmation**: High ROE/ROA supports bullish thesis. Declining margins = fundamental weakness beneath technical strength.
4. **Screening for candidates**: Use `rank` and `screen` to find fundamentally strong stocks, then apply technical analysis to time entries.

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

## Analysis Framework

When performing analysis (either via the CLI or as agent fallback), follow these priorities:

1. **Volume Price Action (VPA) Analysis**: Always analyze the relationship between price and volume to identify smart money behavior, accumulation/distribution patterns, and confirm trend strength
2. **Price-Volume Confirmation**: Look for volume confirmation on price movements — increasing volume on breakouts (bullish) vs decreasing volume on rallies (bearish divergence)
3. **Wyckoff Phases**: Identify market phases (Accumulation, Markup, Distribution, Markdown) based on price-volume patterns. Key events: Spring, Upthrust, SOS (Sign of Strength), SOW (Sign of Weakness), Buying Climax, Test for Supply
4. **Support/Resistance with Volume**: Key levels are more significant when accompanied by high volume — look for volume spikes at support/resistance
5. **Volume Trends & Spikes**: Compare current volume to recent average volume (e.g. 20-day average), NOT just the previous day's volume. A large day-over-day percentage jump (like `volume_changed` +92%) is a FALSE SIGNAL if the previous day had abnormally low liquidity. Always verify absolute volume against the average before claiming an "explosive", "distribution", or "climax" event.
6. **Extreme Price Changes**: Detect moves exceeding ±6.7%/day (VN market limit) and search recent news/events to find causes
7. **Risk Management**: Every analysis must include both positive (opportunities, strengths, bullish signals) and negative (risks, weaknesses, bearish signals) insights. Quantify downside risk with specific price levels, identify what would invalidate the current thesis, and never present a one-sided view
8. **Fundamental Context (when requested)**: When the user asks for fundamentals, enrich the technical analysis with PE, PB, ROE, NPL, CAR, and other financial metrics. Fundamentals do NOT replace technical analysis — they add context.
9. **Nhóm Chủ Lực (Core Market Sectors - VN Market Only)**: When analyzing the Vietnamese market, always contextualize tickers within their respective "Nhóm Chủ Lực" (Core Sectors) to assess systemic flow. The key groups are:
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
    *(Note: This classification applies only to the Vietnamese market. Crypto and Global markets do not use this specific grouping yet).*

## Data Usage Policy (CRITICAL)

1. **NEVER generate, guess, estimate, or hallucinate any numbers** — prices, volumes, MA values, MA scores, percentages, dates, or any financial data. Only use data from tool results or user-provided context
2. **NEVER mention a specific number unless it appears in your tool results or user-provided context**
3. **Use tools proactively** — call `aipa analyze`, `aipa get-ohlcv-data`, and/or `aipa performers` BEFORE answering price-related questions
4. **When researching news or events, ALWAYS include the source name** (e.g., "Source: CafeF", "Source: VNExpress")
5. **Trading Hours**: VN market trades 09:00–15:00 ICT (UTC+7), Mon–Fri. Crypto 24/7. If the latest bar shows unusually low volume, the session may still be in progress

## T+2 Settlement Rule (VN stocks only)

> **This rule applies ONLY to Vietnamese stocks (`--source vn`).** Crypto and global stocks are not subject to T+2 settlement.

> **IMPORTANT:** For all stock purchases in the Vietnamese stock market, shares are only settled and available for trading (selling) on the **afternoon of T+2** (specifically at 13:00 / 1:00 PM on day T+2, not T+3).
> - **NEVER** propose or attempt to execute any Stop Loss or Take Profit actions on **T+1** (the first business day after the purchase), as the shares have not yet settled.
> - Always check the purchase date of any stock positions when recommending sell actions.

## Portfolio File

The agent looks for a portfolio file in the working directory to track positions. Accepted file names (checked in order):

1. `DANH_MUC.md`
2. `PORTFOLIO.md`
3. `ACCOUNT.md`

If none exists, the agent should ask the user whether they want to create one.

## Watchlist File

The agent also looks for a watchlist file to track tickers being monitored (no positions yet). Accepted file names (checked in order):

1. `THEO_DOI.md`
2. `WATCHLIST.md`

This tracks potential entry candidates. Include: ticker, sector, watch reason, entry zone, key level, and added date.

## Risk Management Rules (MANDATORY)

1. **Always check settlement status before recommending sell actions (VN stocks only)** — cross-reference buy date with today. For VN stocks, shares are NOT sellable until afternoon of T+2. Does NOT apply to crypto or global stocks
2. **Every analysis must quantify risk** — include specific Stop Loss and Take Profit levels with reasoning, state what invalidates the thesis, calculate risk-reward ratio
3. **Never present one-sided analysis** — every ticker must have both bullish signals and bearish risks
4. **Position sizing awareness** — flag concentration risk when >30% of portfolio is in one sector
5. **Daily portfolio review** — mark settled positions, check SL/TP hits, flag thesis changes

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

## No Premature TP (Chốt Non) — CRITICAL

**Symptom:** Position is up 1-3% and agent recommends partial or full close because "TP1 was hit" — ignoring the Wyckoff thesis (Phase E Markup / SOS / LPS unconfirmed).

**Rules:**

1. **Never recommend TP (partial or full) when P&L < 1× ATR(14) from entry.** If ATR is unavailable: do not TP when profit < 3% for Markup Phase D/E, < 5% for Accumulation breakout.

2. **Always check Wyckoff phase before mentioning "take profit":**
   - **Phase E Markup / SOS breakout:** DO NOT TP — the trend is just starting. This is a trend trade, not a scalp.
   - **Re-accumulation / Spring test:** DO NOT TP — position just formed.
   - **Only consider TP when distribution signs appear** (volume climax, weekly bearish engulfing, Upthrust after extended markup).

3. **Check momentum before suggesting TP:**
   - Is price at a **swing high with volume > 20-day average**?
   - Are there **distribution candles** (shooting star, bearish engulfing, volume spike)?
   - Is there **weekly resistance** above?
   - If all 3 are NO → **TP is forbidden.** Let it run.

4. **Premature TP checklist** (run before every sell/reduce recommendation):

   | Question | Pass/Fail |
   |---|---|
   | P&L > 3%? | |
   | Is Wyckoff phase Distribution / Upthrust? | |
   | Are there distribution signs (volume climax, bearish weekly)? | |
   | Does the weekly chart show strong resistance above? | |

   If **all 4 are NO** → **TP is BLOCKED.** Document the reason in the report.

5. **Controlled exception:** Premature TP is only allowed when the user **explicitly requests it** or needs urgent liquidity. The agent must NEVER autonomously propose premature TP in any form (including "protect profits", "reduce risk", etc.).

---

## Add-Size Rules — Propose Add Instead of Premature TP

**Symptom:** Position is up 2-3% and agent recommends partial TP. Instead, the agent should be looking for **add-size opportunities** — a position proving itself is a candidate for more, not for reducing.

**Shift in mindset:** When a position is working (up 2-5% with Volume Price Action confirmation), the correct response is:
- **Don't:** "Chốt lời 50% tại đây"
- **Do:** "Here's where you can add on pullback / breakout confirmation"

**Rules:**

1. **When a position is in profit and thesis is intact, always check for add-size setups before considering any TP.** The add-size plan must be defined at entry (not invented after the position moves).

2. **Two valid add scenarios (must pick one at entry):**
   - **Pullback add (Zone B):** Price pulls back to a lower value zone with declining volume (No Supply). Example: GAS bought at 84,800 → add at 82,500-83,500 on low volume test.
   - **Breakout add:** Price breaks a clear structural level (range ceiling, swing high) with volume > 1.5× 20-day average. Example: ACB > 25,500 with vol > 40M.

3. **Never add at market price without a structural reason.** "Momentum is strong" is not a reason — waiting for a pullback to a predefined zone is.

4. **Add-size check before every suggestion:**

   | Question | Pass/Fail |
   |---|---|
   | Is the original Wyckoff thesis still intact? | |
   | Is price at a predefined add zone (not no-man's land)? | |
   | Does volume confirm? (pullback = low/declining, breakout = high/surge) | |
   | Is the position NOT testing its SL? | |
   | Would this add lot have R:R ≥ 1:1 as an independent trade? | |

   If all pass → propose the add. If any fail → hold only, no add.

5. **Max 2 adds per position.** After reaching max size, just hold and let the trend run. No further pyramiding.

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

1. **Auto-uppercase**: Ticker symbols are automatically converted to uppercase. You can pass `vcb`, `btcusdt`, etc. — they will be treated as `VCB`, `BTCUSDT`.

2. **Use `--context-only` for data inspection**: If you only need to see what data is available without spending API credits, use `aipa analyze VCB --context-only`.

3. **Default is good enough**: The default template (index 0, Trading Opportunity) is comprehensive. Only specify `--question` when the user has a specific analytical framework in mind.

4. **Multi-ticker for comparisons**: When the user asks to "compare" or "which is better", pass multiple tickers: `aipa analyze VCB TCB MBB`.

5. **Use `--lang vn` for Vietnamese users**: If the user writes in Vietnamese or asks for Vietnamese output, add `--lang vn`.

6. **Use `--verbose` for transparency**: When the user wants to see the reasoning process, add `--verbose`.

7. **`aipa-data` for raw numbers**: If the user asks for "price data", "candle data", or "OHLCV" without wanting AI analysis, use the `aipa-data` skill instead.

8. **Interval matters**: For intraday analysis, use `1h` or `4h`. For swing trading, use `1D`. For scalping, use `15m` or `5m`.

9. **Combine with `--limit`**: More bars = more context. Use `--limit 50` or `--limit 100` for deeper analysis. Default is 20.

10. **Reference ticker**: Auto-detected based on the ticker's source — `VNINDEX` for VN stocks, `BTCUSDT` for crypto, `^GSPC` for global stocks. Use `--reference-ticker` to override.

11. **Use `aipa performers` to find tickers to analyze — run multiple perspectives**: When the user asks "what should I analyze?" or "what's moving today?", run `aipa performers` with multiple `--sort-by` values to get a multi-perspective view. **Always run at least these two**: default (price change) and value (trading value). Add MA scores when the user cares about trends. Cross-referencing the lists reveals more significant tickers. Then analyze the interesting ones with `aipa analyze`.

    ```bash
    aipa performers                                          # price change — top gainers / worst losers
    aipa performers --sort-by value                          # trading value — where the money flows
    aipa performers --sort-by ma50_score                     # MA50 trend — strongest/weakest medium-term trends
    aipa performers --sort-by ma20_score                     # MA20 trend — strongest/weakest short-term trends
    aipa performers --sort-by total_money_changed            # money flow change — unusual capital activity
    aipa performers --source crypto --sort-by value          # crypto by trading value
    aipa performers --group NGAN_HANG --sort-by value        # banking sector by trading value
    aipa performers --group CHUNG_KHOAN --sort-by close_changed  # securities sector top gainers
    ```

12. **Use `aipa volume-profile` for support/resistance context**: When analyzing a ticker and the user asks about key price levels, support, resistance, or "where is the volume?", run `aipa volume-profile TICKER` to get POC (Point of Control), value area, and volume-weighted statistics. **Prefer multi-day ranges over single-day profiles** — they produce more reliable support/resistance levels and smooth out intraday noise. Use `--start-date` and `--end-date` covering at least 20 trading days as the default approach. Only use a single `--date` when the user explicitly asks for one specific day. Examples:
    - `aipa volume-profile VCB --start-date 2026-04-14 --end-date 2026-05-09` — 1-month range (preferred default)
    - `aipa volume-profile VCB --start-date 2026-04-28 --end-date 2026-05-09 --bins 30` — 2-week range
    - `aipa volume-profile VCB --date 2026-05-08` — single date (only when specifically requested)
    - `aipa volume-profile BTCUSDT --source crypto --bins 30 --start-date 2026-05-05 --end-date 2026-05-09` — crypto multi-day
    - `aipa volume-profile FPT --start-date 2026-05-01 --end-date 2026-05-09 --bins 30 --value-area-pct 80` — full options
