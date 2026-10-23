# Analysis workflows

The terminal has two analysis commands:

| Command | What it does | Needs an LLM key? |
| --- | --- | --- |
| `aipa analyze` | Builds context for selected tickers and asks one LLM for analysis | Yes, unless using `--context-only` |
| `aipa deep-research` | Shows a market snapshot by default; `--run` starts a multi-agent research pipeline | Only with `--run` |

## Single-ticker analysis

The `analyze` command builds context from OHLCV data, indicators, and an
optional reference ticker. It sends that context and your question to the
configured LLM. The response may request more data through tools.

```sh
aipa analyze VCB
aipa analyze VCB FPT --interval 1D --limit 50
aipa analyze VCB --context-only
aipa analyze VCB --question "Describe the current trend."
```

Use `--context-only` to print data without calling an LLM. Check the command's
`--help` output for the full option list.

## Multi-agent research

Run `aipa deep-research --run` to start the full pipeline:

1. The supervisor divides the question into market or sector tasks.
2. Workers research separate tasks in parallel and can fetch market data.
3. The aggregator combines the reports.
4. The reviewer checks the draft and can request corrections.

Without `--run`, the command only prints a market snapshot. A full run can take
several minutes and requires an LLM provider key.

```sh
aipa deep-research
aipa deep-research --source crypto
aipa deep-research --run "Research the banking sector"
aipa deep-research --run --source global
```

Use `--resume ID` to continue a saved run. See `aipa deep-research --help`
for all options.

## Data checks

The agent should use tool results for every price, volume, indicator, and date.
It must verify the ticker and date before using a row, show both positive and
negative evidence, and calculate any derived figures with code. Follow the
risk and data-quality rules in [AGENTS.md](AGENTS.md).

## Code map

| File | Role |
| --- | --- |
| `aipriceaction-terminal/src/aipriceaction_terminal/cli_commands.py` | CLI command handlers |
| `aipriceaction-terminal/src/aipriceaction_terminal/deep_research.py` | Research pipeline |
| `aipriceaction-terminal/src/aipriceaction_terminal/agents/tools.py` | Data tools available to agents |
| `sdk/aipriceaction-python/src/aipriceaction/context.py` | Analysis context builder |
| `sdk/aipriceaction-python/src/aipriceaction/system.py` | English and Vietnamese system prompts |
