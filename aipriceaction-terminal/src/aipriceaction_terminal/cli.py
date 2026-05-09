"""CLI dispatcher: routes to TUI or subcommands."""

import argparse


def run():
    parser = argparse.ArgumentParser(prog="aipa", description="AIPriceAction terminal")
    sub = parser.add_subparsers(dest="command")

    # aipa analyze VCB [tickers...] [--interval 1D] [--limit N]
    #   [--source vn] [--start-date] [--end-date] [--reference-ticker VNINDEX]
    #   [--lang en] [--ma-type ema]
    p_analyze = sub.add_parser("analyze", help="Build AI context for a ticker")
    p_analyze.add_argument("tickers", nargs="+", help="Ticker symbol(s)")
    p_analyze.add_argument("--interval", default="1D")
    p_analyze.add_argument("--limit", type=int, default=None)
    p_analyze.add_argument("--source", default=None)
    p_analyze.add_argument("--start-date", default=None)
    p_analyze.add_argument("--end-date", default=None)
    p_analyze.add_argument("--reference-ticker", default="VNINDEX")
    p_analyze.add_argument("--lang", default="en", choices=["en", "vn"])
    p_analyze.add_argument("--ma-type", default="ema", choices=["ema", "sma"])

    # aipa get-ohlcv-data TICKER [--interval 1D] [--limit N]
    #   [--start-date] [--end-date] [--source] [--ma] [--ema]
    p_ohlcv = sub.add_parser("get-ohlcv-data", help="Fetch raw OHLCV data")
    p_ohlcv.add_argument("ticker", help="Ticker symbol")
    p_ohlcv.add_argument("--interval", default="1D")
    p_ohlcv.add_argument("--limit", type=int, default=None)
    p_ohlcv.add_argument("--start-date", default=None)
    p_ohlcv.add_argument("--end-date", default=None)
    p_ohlcv.add_argument("--source", default=None)
    p_ohlcv.add_argument("--ma", action="store_true", default=True)
    p_ohlcv.add_argument("--no-ma", dest="ma", action="store_false")
    p_ohlcv.add_argument("--ema", action="store_true", default=False)

    # aipa deep-research [question]
    p_deep = sub.add_parser("deep-research", help="Multi-agent deep research (not yet implemented)")
    p_deep.add_argument("question", nargs="*", help="Research question")

    args = parser.parse_args()

    if args.command == "analyze":
        from .cli_commands import cmd_analyze
        cmd_analyze(args)
    elif args.command == "get-ohlcv-data":
        from .cli_commands import cmd_get_ohlcv
        cmd_get_ohlcv(args)
    elif args.command == "deep-research":
        from .cli_commands import cmd_deep_research
        cmd_deep_research(" ".join(args.question) if args.question else "")
    else:
        from .app import main
        main()
