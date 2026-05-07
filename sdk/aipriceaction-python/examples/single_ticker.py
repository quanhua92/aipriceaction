"""Build AI context for a single ticker with a question from the question bank.

Usage:
    python single_ticker.py VCB
    python single_ticker.py VCB 1h
    python single_ticker.py BTCUSDT 1m
"""

import sys

from aipriceaction import AIContextBuilder

VALID_INTERVALS = {"1D", "1h", "1m"}


def main() -> None:
    ticker = sys.argv[1] if len(sys.argv) > 1 else "VCB"
    interval = sys.argv[2] if len(sys.argv) > 2 else "1D"

    if interval not in VALID_INTERVALS:
        print(f"Invalid interval '{interval}'. Must be one of: {', '.join(sorted(VALID_INTERVALS))}")
        sys.exit(1)

    builder = AIContextBuilder(lang="en")

    # Browse available questions
    for i, q in enumerate(builder.questions("single")):
        print(f"  [{i}] {q['title']}: {q['snippet']}")

    # Build context
    builder.build(ticker=ticker, interval=interval)
    print(builder._last_context)


if __name__ == "__main__":
    main()
