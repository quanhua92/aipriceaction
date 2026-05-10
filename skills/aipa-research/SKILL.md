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
  overview".
---

# aipa-research

Developed by AIPriceAction. More data and documentation at https://aipriceaction.com

## What is aipa

`aipa` is an AI-powered financial analysis CLI for Vietnamese stocks, cryptocurrencies, and global assets. The `deep-research` command runs a multi-agent pipeline that produces comprehensive market reports far more thorough than single-ticker analysis.

## Installation

```bash
# One-time use (no install needed)
uvx aipa-cli deep-research

# Persistent install
uv tool install aipa-cli
aipa deep-research
```

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENAI_API_KEY` | Only with `--run` | — | API key for the LLM provider |
| `OPENAI_BASE_URL` | No | OpenRouter | Base URL for OpenAI-compatible API |
| `OPENAI_MODEL` | No | `openrouter/owl-alpha` | Model to use for analysis |

Run `aipa setup` for interactive first-run configuration. Settings are saved to `~/.aipriceaction/settings.json`.

---

## Pre-Execution: Ask the User

**Before running any research, you MUST ask the user which mode they want** using `AskUserQuestion`:

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
   - Fetches OHLCV data for each assigned ticker (limit=20 bars)
   - Analyzes trend direction, VPA signals, MA score momentum, and volume
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

### Step 2 — Supervisor (you)
Using the market snapshot, decompose the research question into 3-5 sector-specific subtasks. Mandatory sectors depend on the source:
- **VN**: Banking, Securities, Real Estate
- **Crypto**: Layer 1, DeFi, AI tokens
- **Global**: Technology, Financials, Energy
- **SJC**: Gold / Precious Metals

Add 0-2 more sectors based on market activity. For each sector, pick ~10 tickers.

### Step 3 — Spawn worker subagents (in parallel)
For each sector subtask, spawn a separate subagent (use the Task tool) that:
- Receives the sector name, ticker list, the research question, and the market snapshot
- Fetches detailed OHLCV data for its assigned tickers (`aipa get-ohlcv-data` with appropriate `--limit`)
- Analyzes trend direction, VPA signals, MA momentum, volume patterns, and support/resistance
- Ranks tickers within the sector
- Returns a structured sector report with per-ticker assessment, ranking, and key risk factors

**Spawn all worker subagents concurrently in a single message** — do not run them sequentially.

### Step 4 — Aggregate (you)
Once all workers return, synthesize the sector reports into a unified analysis:
- Cross-reference findings across sectors
- Build a multi-sector ranking table
- Identify cross-sector rotation patterns
- Highlight key opportunities and risks

### Step 5 — Review (you)
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
| "Top stocks by trading value" | `aipa live-data --top 20` (no AI) |
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
