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
| `OPENAI_API_KEY` | Yes | — | API key for the LLM provider |
| `OPENAI_BASE_URL` | No | OpenRouter | Base URL for OpenAI-compatible API |
| `OPENAI_MODEL` | No | `openrouter/owl-alpha` | Model to use for analysis |
| `DATABASE_URL` | No | `http://localhost:3000` | Backend API URL |

Run `aipa setup` for interactive first-run configuration. Settings are saved to `~/.aipriceaction/settings.json`.

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

Run a comprehensive multi-agent research pipeline. The pipeline analyzes multiple sectors in parallel, cross-references findings, and produces a unified market report with quality review.

```bash
aipa deep-research [QUESTION] [options]
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `QUESTION` | market overview | Research question (optional) |
| `--resume ID` | — | Resume from a checkpoint session ID |
| `--output FILE` | — | Save final report to file |
| `--lang` | saved setting | Language: `en` or `vn` |

### Usage Examples

```bash
# Comprehensive market overview (default question)
aipa deep-research

# Research a specific sector
aipa deep-research "Deep research on the banking sector: analyze top 10 banks by market cap, assess trend direction, VPA signals, MA momentum, and identify leaders vs laggards"

# Real estate deep dive
aipa deep-research "Comprehensive analysis of Vietnamese real estate stocks: identify accumulation patterns, compare relative strength, and assess sector rotation"

# Save report to file
aipa deep-research --output banking_report.md

# Vietnamese output
aipa deep-research "Phân tích toàn diện thị trường chứng khoán Việt Nam" --lang vn

# Resume an interrupted research session
aipa deep-research --resume abc123
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

1. **Market Snapshot**: Fetches latest OHLCV data for all VN tickers to identify active sectors and tickers.

2. **Supervisor**: Analyzes the research question and market snapshot. Decomposes into 3-5 sector-specific subtasks. Always includes Banking, Securities, and Real Estate. Adds 0-2 additional sectors based on market activity.

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
├── worker_Banking.md
├── worker_Securities.md
├── worker_Real_Estate.md
├── worker_Additional_Sector.md
├── aggregator_output.md
└── final_report.md
```

Use `--resume ID` to continue an interrupted or previously completed session.

---

## When to Use Research vs Analyze

| Scenario | Use |
|---|---|
| "Analyze VCB" | `aipa analyze VCB` (single ticker) |
| "Compare FPT and VNM" | `aipa analyze FPT VNM` (quick comparison) |
| "What's the trend for BTCUSDT?" | `aipa analyze BTCUSDT` (focused question) |
| "Research the banking sector" | `aipa deep-research` (multi-agent, sector-wide) |
| "Comprehensive market overview" | `aipa deep-research` (full market scan) |
| "Deep dive into real estate" | `aipa deep-research "..."` (thorough investigation) |
| "Which sectors are leading?" | `aipa deep-research` (cross-sector analysis) |

### Key Differences

- **`aipa analyze`**: Fast, focused on 1-5 tickers, single LLM call, results in seconds.
- **`aipa deep-research`**: Slower, covers entire sectors, multiple LLM calls across agents, results in minutes. Produces a comprehensive report with quality review.

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

1. **Be specific with the question**: The default question produces a general market overview. For better results, provide a specific research question targeting sectors or themes of interest.

2. **Save reports**: Use `--output FILE` to save the report. This is useful for sharing or later reference.

3. **It takes time**: The pipeline runs multiple agents sequentially and in parallel. Expect it to take several minutes. Don't rush it.

4. **Resume on failure**: If a session is interrupted, the session ID is printed in the output. Use `--resume ID` to continue.

5. **Vietnamese analysis**: Add `--lang vn` when the user communicates in Vietnamese or wants Vietnamese output.

6. **Combine with analyze**: For a complete workflow, use `deep-research` for the broad picture, then `analyze` for deep dives into specific tickers identified in the research.

7. **The pipeline self-corrects**: The reviewer may reject the aggregator's output and request revisions. This is normal and ensures quality. The pipeline retries up to 3 times.

8. **Mandatory sectors**: Banking, Securities, and Real Estate are always included. The supervisor adds 0-2 more sectors based on market conditions. You cannot customize which sectors are analyzed.

9. **Auto-uppercase**: Ticker symbols in questions are automatically processed. The pipeline handles uppercase conversion internally.
