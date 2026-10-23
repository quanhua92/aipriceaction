---
name: aipa-fundamentals
description: >
  Fundamental analysis for Vietnamese stocks. Use when the user asks about
  company profiles, financial ratios, valuation, or fundamental screening.
  For technical analysis use aipa-analyze; for raw market data use aipa-data.
resources:
  - template.md
---

# Fundamental analysis

Use the `aipa fundamentals` commands for Vietnamese stocks. They do not need an
LLM API key. Fundamental data is not available for crypto or global tickers.

## Rules

- Run this workflow only when the user asks for fundamental analysis.
- Check the CLI version first. This feature requires `aipa-cli` 0.1.48 or newer.
- Use real command output only. Mark missing values as unavailable; do not infer them.
- Compare companies using the same reporting period. State the period and data date.
- For comparisons, rank the requested tickers on at least two measures before
  opening individual ratio reports. Screen next if useful; use individual reports
  for shortlisted companies.
- Keep bank metrics (such as NPL, CAR, CASA, and CIR) separate from non-bank metrics.

## Commands

```sh
uvx aipa-cli@latest --version
uvx aipa-cli@latest fundamentals info VCB
uvx aipa-cli@latest fundamentals ratios VCB --latest
uvx aipa-cli@latest fundamentals rank VCB BID CTG TCB MBB --sort-by roe
uvx aipa-cli@latest fundamentals rank VCB BID CTG TCB MBB --sort-by pe --direction asc
uvx aipa-cli@latest fundamentals screen --npl-max 0.015 --roe-min 0.15 --sort-by roe
```

Use `uvx aipa-cli ...` after the first run for cached execution. Use `--year`,
`--period`, `--category`, or `--json` when the request needs a specific period,
ratio group, or machine-readable output. Check `--help` if a flag is rejected.

For a full report, use the [fundamental analysis template](template.md). Cover
valuation, profitability, financial safety, and material strengths and risks.
Do not call a company cheap or safe from one ratio alone.
