# AIPA Terminal

**Live site:** [aipriceaction.com](https://aipriceaction.com) | **GitHub:** [aipriceaction](https://github.com/quanhua92/aipriceaction) | **Frontend:** [aipriceaction-web](https://github.com/quanhua92/aipriceaction-web) | **Docker image:** [`quanhua92/aipriceaction:latest`](https://hub.docker.com/r/quanhua92/aipriceaction) | **Python SDK:** [`aipriceaction` on PyPI](https://pypi.org/project/aipriceaction/) | **AIPA Terminal:** [`aipa-cli` on PyPI](https://pypi.org/project/aipa-cli/)

Textual-based terminal interface for AI-powered ticker analysis. Features streaming chat with thinking/reasoning display, autocomplete, slash commands, and workflow tabs.

## Install

```bash
# Run directly (no install)
uvx aipa-cli

# Or install as a standalone tool
uv tool install aipa-cli

# Use either command
aipa
aipa-cli
```

## Requirements

- Python 3.13+
- An OpenAI-compatible API key (`OPENAI_API_KEY`)
- Optional: set `OPENAI_BASE_URL` for custom providers like OpenRouter

## Usage

```
aipa              # Launch the TUI
aipa analyze      # Run ticker analysis from CLI
aipa get-ohlcv-data  # Fetch OHLCV data from CLI
aipa deep-research   # Run deep research from CLI
```

### TUI

The interface has three tabs:

- **Chat** — AI-powered chat with streaming responses, thinking/reasoning display, slash commands (`/analyze`, `/export`, `/clear`, `/exit`), and arrow-key history navigation
- **Workflows** — Structured analysis forms for ticker analysis and deep research
- **Tickers** — Browse and search available tickers

Press `Ctrl+O` in the Chat tab to view thinking/reasoning history.

## License

MIT
