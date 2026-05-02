"""Demo: Single-ticker analysis with reference ticker (market context).

Fetches a primary ticker plus a reference (e.g. VNINDEX) and builds
context with both, so the AI can compare the ticker against the
broader market.

Usage:
    cd sdk/aipriceaction-python
    uv run python scripts/demo_reference_ticker.py
    uv run python scripts/demo_reference_ticker.py FPT --ref VCB
    uv run python scripts/demo_reference_ticker.py BTCUSDT --ref ETHUSDT --ema
"""

from __future__ import annotations

import argparse
import sys

from aipriceaction import AIPriceAction, AIContextBuilder


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build AI context with a reference ticker for market comparison"
    )
    parser.add_argument("ticker", default="VCB", help="Primary ticker to analyze")
    parser.add_argument("--ref", default="VNINDEX", help="Reference ticker (default: VNINDEX)")
    parser.add_argument("--lang", default="en", choices=["en", "vn"], help="Language")
    parser.add_argument("--ema", action="store_true", help="Use EMA instead of SMA")
    parser.add_argument("--template", type=int, default=0, help="Template index (0-5)")
    args = parser.parse_args()

    client = AIPriceAction()
    builder = AIContextBuilder(
        lang=args.lang,
        ma_type="ema" if args.ema else "sma",
    )

    # Fetch primary ticker
    df = client.get_ohlcv(args.ticker, interval="1D", limit=30, ma=True, ema=args.ema)
    if df.empty:
        print(f"No data found for {args.ticker}")
        sys.exit(1)
    records = client.to_ticker_records(df)
    builder.set_market_data(records)
    builder.set_interval("1D")

    # Fetch reference ticker
    ref_df = client.get_ohlcv(args.ref, interval="1D", limit=30, ma=True, ema=args.ema)
    if ref_df.empty:
        print(f"Warning: No data for reference ticker {args.ref}, skipping reference")
    else:
        ref_records = client.to_ticker_records(ref_df)
        ref_sym = list(ref_records.keys())[0]
        builder.set_reference_ticker(ref_sym, ref_records[ref_sym])
        print(f"Primary: {args.ticker} ({len(records[args.ticker])} records)")
        print(f"Reference: {ref_sym} ({len(ref_records[ref_sym])} records)")

    # Fetch ticker info for richer context
    try:
        all_tickers = client.get_tickers()
        primary_info = [t for t in all_tickers if t.ticker == args.ticker]
        if primary_info:
            info = vars(primary_info[0])
            builder.set_tickers_info([info])
            name = primary_info[0].name or ""
            print(f"Info: {args.ticker} — {name}")
    except Exception:
        pass

    print()

    # Build context
    ctx = builder.build_context_with_single_template(args.ticker, args.template)
    print("=" * 60)
    print(ctx)
    print("=" * 60)
    print(f"\nContext length: {len(ctx)} chars")


if __name__ == "__main__":
    main()
