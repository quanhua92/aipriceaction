"""Demo: Multi-ticker AI context building.

Builds a full AI prompt for comparing multiple tickers. Uses
multi-ticker templates that rank and compare tickers.

Usage:
    cd sdk/aipriceaction-python
    uv run python scripts/demo_multi_context.py
    uv run python scripts/demo_multi_context.py VCB FPT VNM HPG
    uv run python scripts/demo_multi_context.py VCB FPT --lang vn --template 1
"""

from __future__ import annotations

import argparse
import sys

from aipriceaction import AIPriceAction, AIContextBuilder


def main() -> None:
    parser = argparse.ArgumentParser(description="Build AI context for multiple tickers")
    parser.add_argument("tickers", nargs="+", default=["VCB", "FPT", "VNM"],
                        help="Ticker symbols to compare")
    parser.add_argument("--lang", default="en", choices=["en", "vn"], help="Language")
    parser.add_argument("--ema", action="store_true", help="Use EMA instead of SMA")
    parser.add_argument("--template", type=int, default=0, help="Template index (0-6)")
    args = parser.parse_args()

    client = AIPriceAction()
    builder = AIContextBuilder(lang=args.lang, ma_type="ema" if args.ema else "sma")

    # Fetch data for all tickers
    df = client.get_ohlcv(tickers=args.tickers, interval="1D", limit=30, ma=True, ema=args.ema)
    if df.empty:
        print("No data found for given tickers")
        sys.exit(1)

    records = client.to_ticker_records(df)
    print(f"Fetched data for: {', '.join(sorted(records.keys()))}")
    for sym, tlist in records.items():
        print(f"  {sym}: {len(tlist)} records")

    builder.set_market_data(records)
    builder.set_interval("1D")

    # Show available templates
    templates = builder.get_multi_templates()
    print(f"\nAvailable templates ({len(templates)}):")
    for i, tpl in enumerate(templates):
        marker = " <-- selected" if i == args.template else ""
        print(f"  [{i}] {tpl['title']}: {tpl['snippet'][:80]}...{marker}")
    print()

    # Build context
    ctx = builder.build_context_with_multi_template(args.template)
    print("=" * 60)
    print(ctx)
    print("=" * 60)
    print(f"\nContext length: {len(ctx)} chars")


if __name__ == "__main__":
    main()
