# Performers

The performers helper ranks the latest daily candle for each ticker. It returns
two lists: the strongest and weakest entries for the selected metric.

## What it includes

The helper skips market indices. It applies `min_volume` only to Vietnamese
stock symbols; crypto and global symbols are not filtered by that threshold.
Missing metric values sort last.

Supported metrics:

| Metric | Meaning |
| --- | --- |
| `close_changed` | Price change from the previous candle, as provided by the API |
| `volume` | Latest candle volume |
| `value` | Close multiplied by volume |
| `volume_changed` | Volume change from the previous candle |
| `ma10_score` … `ma200_score` | Price distance from each moving average |
| `total_money_changed` | API-provided money-flow change |

The requested `limit` is clamped to 1–100. With `direction="desc"`, the
top list is highest first and the worst list is lowest first. `direction="asc"`
reverses those lists.

## Python example

```python
from aipriceaction import AIPriceAction, build_performers

client = AIPriceAction(live_mode="latest")
data = client.fetch_live_data("1D", ma=True)
tickers = client.get_tickers(source="vn")
sectors = {item.ticker: item.group for item in tickers if item.group}

top, worst = build_performers(
    data,
    sectors,
    sort_by="value",
    limit=10,
    min_volume=10_000,
)
```

## CLI examples

```sh
aipa performers
aipa performers --sort-by value --limit 10
aipa performers --sort-by ma50_score --source vn
aipa performers --source crypto
```

Run `aipa performers --help` for all filters. The output ranks data; it is not
a buy or sell recommendation.

## Implementation

The function is `build_performers()` in
`sdk/aipriceaction-python/src/aipriceaction/performers.py`.
