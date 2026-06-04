---
name: aipa-research
description: >
  Multi-agent deep research for comprehensive market analysis using the aipa
  CLI. Use this skill when the user asks for deep research, thorough market
  analysis, sector-wide investigation, comprehensive stock comparison, or
  detailed financial report. This runs a supervisor → parallel workers →
  aggregator → reviewer pipeline that takes longer but produces more thorough
  results than a simple analyze. Trigger for requests like "research banking
  sector", "deep dive into real estate stocks", or "comprehensive market
  overview". Can also incorporate fundamental analysis (PE, ROE, NPL, CAR,
  financial ratios) via `aipa fundamentals` when the user asks for fundamental
  context alongside technical research.
---

# aipa-research

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

`aipa` is an AI-powered financial analysis CLI for Vietnamese stocks, cryptocurrencies, and global assets. The `deep-research` command runs a multi-agent pipeline that produces comprehensive market reports far more thorough than single-ticker analysis.

## Installation

Use `uvx` — no install needed. On the **first call of each session**, use `uvx aipa-cli@latest` to refresh the cache. For all subsequent calls, use plain `uvx aipa-cli` (fast, cached).

```bash
# First call of the session — refresh cache
uvx aipa-cli@latest deep-research

# All subsequent calls — fast cached execution
uvx aipa-cli deep-research

# Fallback: pip (if uv is not available)
pip install aipa-cli
aipa deep-research

# Fallback: system pip (if pip fails due to PEP 668)
python3 -m pip install aipa-cli --break-system-packages
aipa deep-research

# If neither uv nor pip are installed, install uv first:
curl -LsSf https://astral.sh/uv/install.sh | sh
# If the install script fails, see: https://docs.astral.sh/uv/getting-started/installation/
```

All command examples in this skill use `aipa` for brevity. Replace `aipa` with `uvx aipa-cli` if not installed globally.

## Keeping the CLI Updated

The aipa CLI is actively developed with frequent improvements. **Always prefer `uvx aipa-cli` over `aipa`**. When using `uvx`:

1. **Use plain `uvx` for fast cached execution:**
   `uvx aipa-cli deep-research`
2. **Fallback on failure** — if a command fails with a schema or missing argument error, retry with `@latest`:
   `uvx aipa-cli@latest deep-research`

If `aipa` is installed globally via `pip`, update before each session:

```bash
pip install --upgrade aipa-cli
```

The agent should prefer `uvx aipa-cli` for all commands. Only fall back to `aipa` if `uvx` is unavailable.

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENAI_API_KEY` | Only with `--run` | — | API key for the LLM provider |
| `OPENAI_BASE_URL` | No | OpenRouter | Base URL for OpenAI-compatible API |
| `OPENAI_MODEL` | No | `openrouter/owl-alpha` | Model to use for analysis |

Run `aipa setup` for interactive first-run configuration. Settings are saved to `~/.aipriceaction/settings.json`.

---

## Pre-Execution: Ask the User

**STOP. Do NOT run any commands yet. You MUST call `AskUserQuestion` (or `question` / QuestionTool in compatible agents) first to ask the user which mode they want.**

If you run any `aipa` command before asking the user, you have failed. Always ask first.

Ask the user this question using the `AskUserQuestion` tool (or equivalent `question` tool):

1. **Fast Research (Recommended)** — Run `aipa deep-research` to get the market snapshot, then you (the AI agent) replicate the multi-agent pipeline using subagents + `aipa get-ohlcv-data`. No API key needed. This is the recommended default — it produces thorough results without the 5-10 minute wait.
2. **Full pipeline (`--run`)** — Run `aipa deep-research --run` to use the actual CLI multi-agent pipeline. Takes 5-10 minutes and requires an API key configured via `aipa setup`.

Only skip asking if the user's request explicitly indicates they want the full CLI pipeline (e.g., "run the full pipeline", "use --run").

- If the user chooses **Fast Research**: follow the [Fast Research: Agent-Driven Pipeline](#fast-research-agent-driven-pipeline) section below.
- If the user chooses **Full pipeline**: run `aipa deep-research --run [QUESTION]` and present the stdout output.

---

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

# Using watchlist tickers in deep-research worker assignments
aipa watchlist get VN30  # use these tickers for the VN30 sector worker
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
| `2W` | 2 weeks |

---

## `aipa deep-research` — Multi-Agent Research Pipeline

The default behavior (`aipa deep-research` without flags) fetches and prints a market snapshot. Add `--run` to execute the full multi-agent pipeline. Use `--source` to target different markets — the supervisor, workers, prompts, and default question all adapt to the selected source.

```bash
aipa deep-research [QUESTION] [options]
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `QUESTION` | market overview | Research question (optional) |
| `--run` | off | Run the full multi-agent pipeline (5-10 min). Without this, only dumps market snapshot. |
| `--source` | `vn` | Data source: `vn`, `crypto`, `global`, `sjc` |
| `--resume ID` | — | Resume from a checkpoint session ID |
| `--output FILE` | — | Save final report to file |
| `--lang` | saved setting | Language: `en` or `vn` |

### Usage Examples

```bash
# Market snapshot only (default, fast, no API key needed)
aipa deep-research

# Crypto market snapshot
aipa deep-research --source crypto

# Market snapshot with a custom question in the output
aipa deep-research "Research banking sector"

# Run the FULL multi-agent pipeline (slow, 5-10 min, requires API key)
aipa deep-research --run

# Full pipeline for global stocks
aipa deep-research --source global --run

# Full pipeline for crypto
aipa deep-research --source crypto --run

# Full pipeline with a custom research question
aipa deep-research --run "Deep research on the banking sector: analyze top 10 banks by market cap, assess trend direction, VPA signals, MA momentum, and identify leaders vs laggards"

# Save full pipeline report to file
aipa deep-research --run --output banking_report.md

# Vietnamese output (full pipeline)
aipa deep-research --run "Phân tích toàn diện thị trường chứng khoán Việt Nam" --lang vn

# Resume an interrupted research session
aipa deep-research --run --resume abc123
```

---

## How the Pipeline Works

The deep-research pipeline uses a multi-agent architecture with quality control:

```
Supervisor
    │  Decomposes question into 3-5 sector subtasks
    │  Selects top 10 tickers per sector
    ▼
Parallel Workers (fan-out)
    │  Each worker analyzes one sector
    │  Fetches OHLCV data for each ticker
    │  Produces sector-specific report
    ▼
Aggregator
    │  Synthesizes all sector reports
    │  Cross-references findings
    │  Builds unified ranking table
    ▼
Reviewer
    │  Checks data integrity
    │  Verifies MA scores and ticker coverage
    │  Approves or rejects with feedback
    ▼
Final Report
```

### Pipeline Stages

1. **Market Snapshot**: Fetches latest OHLCV data for all tickers from the selected source (`--source`, default `vn`) to identify active sectors and tickers.

2. **Supervisor**: Analyzes the research question and market snapshot. Decomposes into 3-5 sector-specific subtasks. Mandatory sectors depend on the source:
   - **VN**: Banking, Securities, Real Estate
   - **Crypto**: Layer 1 (BTC, ETH, SOL), DeFi, AI tokens
   - **Global/Yahoo**: Technology, Financials, Energy
   - **SJC**: Gold / Precious Metals

   Adds 0-2 additional sectors based on market activity.

3. **Parallel Workers**: Each worker handles one sector:
    - Fetches OHLCV data for each assigned ticker (limit=50 bars)
    - Runs volume profile for the top 3 most important tickers (30+ trading day range)
    - Fetches intraday data (1h) for tickers showing breakout/reversal patterns
    - Analyzes trend direction, VPA signals, MA score momentum, volume, and S/R
    - Cross-references volume profile levels with price action
    - Provides per-ticker assessment, sector ranking, and key risk factors
    - Workers run concurrently for efficiency

4. **Aggregator**: Collects all worker reports and synthesizes a unified analysis:
   - Cross-references findings across sectors
   - Builds multi-sector ranking table
   - Identifies cross-sector rotation patterns
   - Highlights key opportunities and risks

5. **Reviewer**: Quality assurance check:
   - Verifies no phantom stocks (tickers not in worker reports)
   - Spot-checks MA score fidelity (3-5 reported scores)
   - Confirms table completeness
   - Approves or rejects with specific feedback
   - Maximum 3 review rounds

6. **Final Output**: Approved report is output. If `--output` is specified, saved to file.

### Checkpoint & Resume

Research sessions are checkpointed to disk. Each session gets a unique ID. Intermediate outputs are saved as markdown files:

```
/tmp/aipriceaction-checkpoints/{session_id}/
├── worker_Technology.md
├── worker_Financials.md
├── worker_Energy.md
├── worker_Additional_Sector.md
├── aggregator_output.md
└── final_report.md
```

Use `--resume ID` to continue an interrupted or previously completed session.

---

## Fast Research: Agent-Driven Pipeline

This is the **recommended** approach for research. Run `aipa deep-research` to get the market snapshot, then you (the AI agent) orchestrate the multi-agent pipeline using subagents. This produces equally thorough results and does not require an API key or `aipa setup`.

### Step 1 — Fetch market snapshot
Run `aipa deep-research` (without `--run`) to fetch the latest market snapshot. Use `--source` to select the market (default: `vn`). This gives you the market context to distribute to workers.

```bash
# VN stocks (default)
aipa deep-research

# Crypto
aipa deep-research --source crypto

# Global stocks
aipa deep-research --source global
```

### Step 2 — Multi-perspective performers scan (optional but recommended)
Run `aipa performers` with multiple `--sort-by` values to cross-reference market movers. This enriches your sector analysis.

```bash
aipa performers                                          # top gainers / worst losers
aipa performers --sort-by value                          # where the money flows
aipa performers --sort-by ma50_score                     # trend strength
# For sector-specific:
aipa performers --group NGAN_HANG --sort-by value
# For crypto:
aipa performers --source crypto --sort-by value
```

### Step 3 — Supervisor (you)
Using the market snapshot and performers results, decompose the research question into 3-5 sector-specific subtasks. Mandatory sectors depend on the source:
- **VN**: Banking, Securities, Real Estate
- **Crypto**: Layer 1, DeFi, AI tokens
- **Global**: Technology, Financials, Energy
- **SJC**: Gold / Precious Metals

Add 0-2 more sectors based on market activity. For each sector, pick ~10 tickers.

### Step 3.5 — Fundamental Context (optional, VN only)

> **Version gate:** `aipa fundamentals` requires **aipa-cli >= 0.1.45**. Verify with `aipa --version` before use.

> **NOTE:** `--lang` and `--no-system-prompt` are NOT valid for `aipa fundamentals` commands. Do NOT add them.

For VN stock research, fundamentals add critical context. **Only include if the user asked for fundamental analysis or if valuation/financial health is relevant to the research question.** Do NOT automatically run fundamentals for purely technical research. When the user says "report" or "báo cáo", they may want fundamentals — if unclear, ask to confirm.

When relevant, add fundamental screening to the supervisor step. **Follow this 3-step workflow — do NOT just call `aipa fundamentals ratios TICKER --latest` for each ticker individually.** That produces N separate outputs that are hard to compare. Use `rank` and `screen` first.

**Step 1: Side-by-side ranking (mandatory)**

Use `aipa fundamentals rank` with the specific tickers to get a comparative table in a single call. Run at least 2 perspectives relevant to the sector:

```bash
# Profitability comparison
aipa fundamentals rank VCB BID CTG TCB MBB --sort-by roe

# Historical year
aipa fundamentals rank --year 2023 --sort-by roe

# Specific quarter
aipa fundamentals rank --period "2016 Q4" --sort-by roe

# Valuation comparison
aipa fundamentals rank VCB BID CTG TCB MBB --sort-by pe --direction asc

# Bank health: asset quality + capital adequacy
aipa fundamentals rank VCB BID CTG TCB MBB --sort-by npl --direction asc
aipa fundamentals rank VCB BID CTG TCB MBB --sort-by car --direction desc

# Top stocks by ROE across all VN
aipa fundamentals rank --sort-by roe --limit 20
```

**Step 2: Screen for quality (optional but recommended)**

Use `aipa fundamentals screen` to filter by quality criteria. This eliminates weak candidates immediately:

```bash
# Only banks with acceptable asset quality AND profitability
aipa fundamentals screen --industry "ngân hàng" --npl-max 0.02 --car-min 0.10 --sort-by roe --limit 15

# Value screen: low PE + high ROE
aipa fundamentals screen --pe-max 15 --roe-min 0.15 --sort-by roe --limit 20

# Screen specific tickers by quality
aipa fundamentals screen VCB BID CTG TCB MBB --npl-max 0.015 --roe-min 0.15 --sort-by roe
```

**Step 3: Individual deep dive (only for shortlisted tickers)**

Only after Steps 1-2, use `ratios --latest` for tickers that ranked at the top or need further investigation. Use `info` for company context:

```bash
aipa fundamentals ratios VCB --latest                # full ratios for top candidate
aipa fundamentals ratios VCB --category bank --latest # bank-specific deep dive
aipa fundamentals info VCB                            # company profile context
```

**Why this matters:** `rank` and `screen` return all tickers in a single comparative table — far more efficient than calling `ratios` N times for N tickers. The ranking shows relative position immediately, and the screen eliminates unsuitable candidates before wasting tokens on deep dives.

Pass the fundamental ranking data to workers so they can cross-reference technical signals with fundamental strength/weakness.

### Step 4 — Spawn worker subagents (in parallel)
For each sector subtask, spawn a separate subagent (use the Task tool) that:
- Receives the sector name, ticker list, the research question, and the market snapshot
- Fetches detailed OHLCV data for its assigned tickers (`aipa get-ohlcv-data` with `--limit 50`)
- Runs `aipa volume-profile` for the **top 3 most important tickers** in its sector (highest trading value, most interesting price action, or portfolio/watchlist tickers). Use a multi-day range covering at least 30 trading days ending on today
- For tickers showing breakout/reversal patterns or key S/R levels, also fetch **intraday data** (`--interval 1h --limit 50`) to assess entry timing
- Analyzes trend direction, VPA signals, MA momentum, volume patterns, and support/resistance
- Cross-references volume profile levels (POC, Value Area High/Low) with OHLCV analysis
- Ranks tickers within the sector
- Returns a structured sector report with per-ticker assessment, ranking, and key risk factors

**Spawn all worker subagents concurrently in a single message** — do not run them sequentially.

### Step 5 — Aggregate (you)
Once all workers return, synthesize the sector reports into a unified analysis:
- Cross-reference findings across sectors
- Build a multi-sector ranking table
- Identify cross-sector rotation patterns
- Highlight key opportunities and risks

### Step 6 — Review (you)
Quality-check your own aggregated report:
- Verify no phantom stocks (tickers not in worker reports)
- Spot-check MA score plausibility
- Confirm table completeness
- Fix any issues and produce the final report

Present the final report to the user. Use the research question and the user's language to determine focus and output language.

---

## When to Use Research vs Analyze

| Scenario | Use |
|---|---|
| "Analyze VCB" | `aipa analyze VCB` (single ticker) |
| "Compare FPT and VNM" | `aipa analyze FPT VNM` (quick comparison) |
| "What's the trend for BTCUSDT?" | `aipa analyze BTCUSDT` (focused question) |
| "Research the banking sector" | `aipa-research` skill (multi-agent, sector-wide) |
| "Comprehensive market overview" | `aipa-research` skill (full market scan) |
| "Deep dive into real estate" | `aipa-research` skill (thorough investigation) |
| "Which sectors are leading?" | `aipa-research` skill (cross-sector analysis) |
| "Most active tickers right now?" | `aipa live-data` (no AI, instant top list) |
| "Top gainers / losers" | `aipa performers` (no AI) |
| "Best stocks by trading value" | `aipa performers --sort-by value` (no AI) |
| "Banking sector top movers" | `aipa performers --group NGAN_HANG --sort-by value` (no AI) |
| "Volume profile / POC / value area" | `aipa volume-profile TICKER` (no AI) |
| "What tickers are in the real estate sector?" | `aipa ticker-list --source vn --group BAT_DONG_SAN` |

### Key Differences

- **`aipa analyze`**: Fast, focused on 1-5 tickers, single LLM call, results in seconds.
- **`aipa deep-research --run`**: Covers entire sectors, multiple LLM calls across agents, results in minutes. Produces a comprehensive report with quality review.
- **Fast Research (agent-driven)**: You replicate the pipeline with subagents. Thorough results without the CLI wait time.

Use `deep-research` when:
- The question spans multiple sectors
- The user wants a comprehensive report (not a quick take)
- Cross-sector comparison or rotation analysis is needed
- The user explicitly asks for "research", "deep dive", or "comprehensive analysis"

Use `analyze` when:
- The question is about specific tickers
- Speed matters more than depth
- The user wants a focused answer, not a full report

---

## Interpreting Output

The CLI outputs to two streams:

- **stdout**: The final research report. This is what you should present to the user.
- **stderr**: Status messages with structured markers for tracking pipeline progress.

### Status Markers (stderr)

| Marker | Meaning |
|---|---|
| `[build]` | Context building status and timing |
| `[tool]` | Tool call being executed (e.g., fetching OHLCV data) |
| `[tool-result]` | Tool execution result returned |
| `[thinking]` | Agent reasoning tokens |
| `[error]` | Error message |
| `[done]` | Pipeline complete, includes total time |
| `[result]` | Final research report output follows |

When presenting results to the user, always use the stdout output (the final approved report).

### Attribution

When presenting any research report to the user, always include an attribution line at the end of your response:
- **English**: "_Data by [AIPriceAction](https://aipriceaction.com/) | AI-powered analysis — may contain errors. Verify before trading._"
- **Vietnamese**: "_Dữ liệu bởi [AIPriceAction](https://aipriceaction.com/) | Phân tích bởi AI — có thể chứa sai sót. Vui lòng kiểm chứng trước khi giao dịch._"

Do NOT say "analysis provided by AIPriceAction" or "phân tích được cung cấp bởi AIPriceAction". AIPriceAction provides the **data**; the **analysis** is AI-generated and may be inaccurate.

---

## Analysis Framework

When performing analysis (either via workers or as agent-driven pipeline), follow these priorities:

1. **Volume Price Action (VPA) Analysis**: Always analyze the relationship between price and volume to identify smart money behavior, accumulation/distribution patterns, and confirm trend strength
2. **Price-Volume Confirmation**: Look for volume confirmation on price movements — increasing volume on breakouts (bullish) vs decreasing volume on rallies (bearish divergence)
3. **Wyckoff Phases**: Identify market phases (Accumulation, Markup, Distribution, Markdown) based on price-volume patterns. Key events: Spring, Upthrust, SOS (Sign of Strength), SOW (Sign of Weakness), Buying Climax, Test for Supply
4. **Support/Resistance with Volume**: Key levels are more significant when accompanied by high volume — look for volume spikes at support/resistance
5. **Volume Trends & Spikes**: Compare current volume to recent average volume (e.g. 20-day average), NOT just the previous day's volume. A large day-over-day percentage jump (like `volume_changed` +92%) is a FALSE SIGNAL if the previous day had abnormally low liquidity. Always verify absolute volume against the average before claiming an "explosive", "distribution", or "climax" event.
6. **Extreme Price Changes**: Detect moves exceeding ±6.7%/day (VN market limit) and search recent news/events to find causes
7. **Risk Management**: Every analysis must include both positive (opportunities, strengths, bullish signals) and negative (risks, weaknesses, bearish signals) insights. Quantify downside risk with specific price levels, identify what would invalidate the current thesis, and never present a one-sided view
8. **Nhóm Chủ Lực (Core Market Sectors - VN Market Only)**: When analyzing the Vietnamese market, always contextualize tickers within their respective "Nhóm Chủ Lực" (Core Sectors) to assess systemic flow. The key groups are:
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
3. **Use tools proactively** — fetch OHLCV data before producing any analysis. The market snapshot alone is insufficient — every analysis MUST include at least one `get-ohlcv-data` tool call
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

## Tips for AI Agents

1. **Fast Research is the default**: Unless the user explicitly asks for `--run`, run `aipa deep-research` to get the snapshot, then orchestrate the pipeline yourself (Step 1-5 above).

2. **Be specific with the question**: The default question produces a general market overview. For better results, provide a specific research question targeting sectors or themes of interest.

3. **Save reports**: Use `--output FILE` with `--run` to save the report. This is useful for sharing or later reference.

4. **Resume on failure**: If a `--run` session is interrupted, the session ID is printed in the output. Use `--resume ID` to continue.

5. **Vietnamese analysis**: Add `--lang vn` when the user communicates in Vietnamese or wants Vietnamese output. For Fast Research, write the report in the user's language.

6. **Combine with analyze**: For a complete workflow, use deep research for the broad picture, then `analyze` for deep dives into specific tickers identified in the research.

7. **The pipeline self-corrects**: The reviewer may reject the aggregator's output and request revisions. This is normal and ensures quality. The pipeline retries up to 3 times.

8. **Mandatory sectors depend on source**: Each source has its own set of mandatory sectors (e.g., VN uses Banking/Securities/Real Estate, crypto uses Layer 1/DeFi/AI tokens, global uses Technology/Financials/Energy). The supervisor adds 0-2 more sectors based on market conditions.

9. **Auto-uppercase**: Ticker symbols in questions are automatically processed. The pipeline handles uppercase conversion internally.

10. **Use `--source` for non-VN markets**: Add `--source crypto`, `--source global`, or `--source sjc` to research other markets. The supervisor, workers, tools, and default question all adapt to the selected source automatically.

11. **Use `aipa performers` to identify leaders and laggards — run multiple perspectives**: Before or during research, run `aipa performers` with multiple `--sort-by` values. **Always run at least these two**: default (price change) and value (trading value). Add MA scores for trend context. Cross-referencing the lists helps you pick better tickers for sector workers and provides richer ranking context for the final report.

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

12. **Use `aipa volume-profile` for key price levels**: When workers need support/resistance context for a sector, run `aipa volume-profile TICKER` on the top movers to get POC, value area, and volume-weighted statistics. **Prefer multi-day ranges over single-day profiles** — they produce more reliable support/resistance levels and smooth out intraday noise. Use `--start-date` and `--end-date` covering at least 20 trading days as the default approach. Only use a single `--date` when the user explicitly asks for one specific day. Examples:
    - `aipa volume-profile VCB --start-date 2026-04-14 --end-date 2026-05-09` — 1-month range (preferred default)
    - `aipa volume-profile VCB --start-date 2026-04-28 --end-date 2026-05-09 --bins 30` — 2-week range
    - `aipa volume-profile BTCUSDT --source crypto --bins 30 --start-date 2026-05-05 --end-date 2026-05-09` — crypto multi-day
