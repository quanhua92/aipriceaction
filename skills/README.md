# Install AIPriceAction skills

Install the skills with:

```sh
npx skills add quanhua92/aipriceaction
```

Choose one or more skills and the AI agent that should use them. Restart the
agent after installation.

| Skill | Purpose |
| --- | --- |
| `aipa-data` | Fetch market data and calculate data-based metrics |
| `aipa-analyze` | Analyze tickers and compare market behavior |
| `aipa-fundamentals` | Review company profiles and financial ratios |
| `aipa-research` | Research a market or sector using multiple workers |

For a no-install setup, copy the repository's [`AGENTS.md`](../AGENTS.md) into
your project. It describes how to use the `aipa` CLI and validate market data.

Python 3.13+ is required by the terminal CLI. Data commands do not require an
LLM key. Direct LLM analysis and the full research pipeline require an
OpenAI-compatible API key.

See the [terminal guide](../aipriceaction-terminal/README.md) for commands and
the [market-data guide](aipa-data/SKILL.md) for data examples.
