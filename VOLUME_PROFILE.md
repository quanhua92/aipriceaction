# Volume profile

A volume profile estimates how traded volume was distributed across prices. It
uses OHLCV candles; it does not have the actual trade-by-trade order book.

## How it is calculated

1. Estimate a price tick size from the source and average candle price.
2. For each candle, split its volume evenly across the tick prices between its
   low and high. This is an approximation because OHLCV data does not show where
   trades occurred inside the candle.
3. Combine the tick volumes into the requested number of price bins.
4. The **point of control (POC)** is the bin with the most volume.
5. The **value area** expands out from the POC until it includes the target share
   of volume. The default target is 70%; accepted values are clamped to 60–90%.
6. Calculate volume-weighted mean, median, standard deviation, and skewness.

The default bin setting is 50, clamped to 2–200. The function can return fewer
bins when fewer price levels contain volume. Reported total volume is the sum
of the input candle volumes.

## Python example

```python
from aipriceaction import AIPriceAction, compute_volume_profile

client = AIPriceAction()
candles = client.get_ohlcv(
    "VCB",
    interval="1m",
    start_date="2026-05-01",
    end_date="2026-05-30",
    ma=False,
)
profile = compute_volume_profile(candles, "VCB", source="vn", bins=50)

print(profile.poc)
print(profile.value_area)
```

## CLI examples

```sh
aipa volume-profile VCB --start-date 2026-05-01 --end-date 2026-05-30
aipa volume-profile BTCUSDT --source crypto --bins 30
```

Use multiple sessions when looking for broader support or resistance. A
single-session profile describes only that session. Profile levels are
historical estimates, not guaranteed support or resistance.

## Implementation

The function is `compute_volume_profile()` in
`sdk/aipriceaction-python/src/aipriceaction/volume_profile.py`.
