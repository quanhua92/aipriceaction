"""Quick OHLCV report for any ticker across 1D, 1h, 1m intervals.

Usage:
    python report.py VCB
    python report.py BTCUSDT --no-live
    python report.py VNINDEX --limit 5
    python report.py VCB --utc-offset 0
"""

import argparse

from aipriceaction import AIPriceAction


def report(client: AIPriceAction, ticker: str, interval: str, limit: int) -> None:
    df = client.get_ohlcv(ticker, interval=interval, limit=limit, ma=False)
    print(f"=== {ticker} {interval} (last {len(df)} bars) ===")
    if df.empty:
        print("  No data\n")
        return
    for _, row in df.iterrows():
        print(
            f"  {row['time']:>24s}  "
            f"O={row['open']:>12,.2f}  "
            f"H={row['high']:>12,.2f}  "
            f"L={row['low']:>12,.2f}  "
            f"C={row['close']:>12,.2f}  "
            f"V={row['volume']:>14,}"
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="OHLCV report for a ticker")
    parser.add_argument("ticker", help="Ticker symbol (e.g. VCB, BTCUSDT, VNINDEX)")
    parser.add_argument("--no-live", action="store_true", help="Disable live data overlay")
    parser.add_argument("--limit", type=int, default=3, help="Bars per interval (default: 3)")
    parser.add_argument("--intervals", nargs="+", default=["1D", "1h", "1m"],
                        help="Intervals to report (default: 1D 1h 1m)")
    parser.add_argument("--utc-offset", type=int, default=7,
                        help="UTC offset in hours (default: 7). Pass 0 for raw UTC.")
    args = parser.parse_args()

    client = AIPriceAction(use_live=not args.no_live, utc_offset=args.utc_offset)

    for interval in args.intervals:
        report(client, args.ticker, interval, args.limit)


if __name__ == "__main__":
    main()
