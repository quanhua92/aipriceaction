"""Demo: DataFrame -> Ticker records conversion.

Shows how to fetch OHLCV data and convert it to Ticker objects
for use with AIContextBuilder.

Usage:
    cd sdk/aipriceaction-python
    uv run python scripts/demo_ticker_records.py
    uv run python scripts/demo_ticker_records.py VCB
    uv run python scripts scripts/demo_ticker_records.py VCB FPT VNM
"""

from __future__ import annotations

import sys

from aipriceaction import AIPriceAction, Ticker


def main(tickers: list[str]) -> None:
    client = AIPriceAction()

    for sym in tickers:
        print(f"\n--- {sym} (last 5 days) ---")
        df = client.get_ohlcv(sym, interval="1D", limit=5, ma=True)
        if df.empty:
            print("  No data found.")
            continue

        # Convert DataFrame -> dict[str, list[Ticker]]
        records = client.to_ticker_records(df)
        for sym_name, tlist in records.items():
            print(f"  {len(tlist)} Ticker records for {sym_name}")
            for t in tlist:
                parts = [
                    f"time={t.time}",
                    f"close={t.close:.2f}",
                    f"vol={t.volume:,}",
                ]
                if t.ma20 is not None:
                    parts.append(f"ma20={t.ma20:.2f}")
                if t.ma20_score is not None:
                    parts.append(f"ma20_score={t.ma20_score:+.2f}%")
                if t.close_changed is not None:
                    parts.append(f"chg={t.close_changed:+.2f}%")
                print(f"    {t.symbol}: {'  '.join(parts)}")


if __name__ == "__main__":
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["VCB"]
    main(tickers)
