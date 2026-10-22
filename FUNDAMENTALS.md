# Fundamentals workflow

Fundamental data and analysis are available through the `aipa` CLI/SDK workflow;
the current Python REST API does not expose a fundamentals endpoint. Use the
commands below for ratios, company profiles, ranking, and screening, and read
[AGENTS.md](AGENTS.md) for the full data-quality and reporting policy.

```sh
aipa fundamentals info VCB
aipa fundamentals ratios VCB --latest
aipa fundamentals rank VCB BID CTG TCB MBB --sort-by roe
aipa fundamentals rank VCB BID CTG TCB MBB --sort-by pe --direction asc
aipa fundamentals screen --npl-max 0.015 --roe-min 0.15 --sort-by roe
```

For comparisons, rank multiple tickers together before fetching individual
ratio detail. Do not infer unavailable fields or mix financial years. The
11-question review framework and report template are in
[`skills/aipa-fundamentals/`](skills/aipa-fundamentals/SKILL.md).

The old Rust fundamentals publisher and public S3 ZIP/JSON implementation were
retired with the Rust service; its documentation is preserved in
[`legacy/docs/root/FUNDAMENTALS.md`](legacy/docs/root/FUNDAMENTALS.md). Do not
use that implementation document as a current API or storage specification.
