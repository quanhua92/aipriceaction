"""Demo: Single-ticker AI context building.

Builds a full AI prompt for analyzing one ticker using pre-defined
templates. Shows the complete context string that would be sent to
Claude for investment analysis.

Usage:
    cd sdk/aipriceaction-python
    uv run python scripts/demo_single_context.py
    uv run python scripts/demo_single_context.py FPT
    uv run python scripts/demo_single_context.py VNM --lang vn
    uv run python scripts/demo_single_context.py BTCUSDT --ema
"""

from __future__ import annotations

import argparse
import sys

from aipriceaction import AIPriceAction, AIContextBuilder


def main() -> None:
    parser = argparse.ArgumentParser(description="Build AI context for a single ticker")
    parser.add_argument("ticker", nargs="?", default="VCB", help="Ticker symbol")
    parser.add_argument("--lang", default="en", choices=["en", "vn"], help="Language")
    parser.add_argument("--ema", action="store_true", help="Use EMA instead of SMA")
    parser.add_argument("--template", type=int, default=0, help="Template index (0-5)")
    args = parser.parse_args()

    client = AIPriceAction()
    builder = AIContextBuilder(lang=args.lang, ma_type="ema" if args.ema else "sma")

    # Fetch data
    df = client.get_ohlcv(args.ticker, interval="1D", limit=30, ma=True, ema=args.ema)
    if df.empty:
        print(f"No data found for {args.ticker}")
        sys.exit(1)

    records = client.to_ticker_records(df)
    builder.set_market_data(records)
    builder.set_interval("1D")

    # Show available templates
    templates = builder.get_single_templates()
    print(f"Available templates ({len(templates)}):")
    for i, tpl in enumerate(templates):
        marker = " <-- selected" if i == args.template else ""
        print(f"  [{i}] {tpl['title']}: {tpl['snippet'][:80]}...{marker}")
    print()

    # Build context
    ctx = builder.build_context_with_single_template(args.ticker, args.template)
    print("=" * 60)
    print(ctx)
    print("=" * 60)
    print(f"\nContext length: {len(ctx)} chars")


if __name__ == "__main__":
    main()
