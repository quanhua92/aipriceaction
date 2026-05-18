"""CLI dispatcher: routes to TUI or subcommands."""

import argparse
import asyncio


def _ensure_setup() -> None:
    """Run interactive setup if setup_done is not set."""
    from .user_settings import load_settings
    if not load_settings().get("setup_done"):
        from .cli_setup import cmd_setup
        cmd_setup()


def run():
    from .user_settings import apply_settings_to_env
    apply_settings_to_env()

    parser = argparse.ArgumentParser(prog="aipa", description="AIPriceAction terminal")
    sub = parser.add_subparsers(dest="command")

    # aipa analyze VCB [tickers...] [--interval 1D] [--limit N]
    #   [--source vn] [--start-date] [--end-date] [--reference-ticker VNINDEX]
    #   [--lang en] [--ma-type ema] [--question TEXT] [--questions] [--context-only]
    p_analyze = sub.add_parser("analyze", help="AI analysis for ticker(s)")
    p_analyze.add_argument("tickers", nargs="+", help="Ticker symbol(s)")
    p_analyze.add_argument("--interval", default="1D")
    p_analyze.add_argument("--limit", type=int, default=None)
    p_analyze.add_argument("--source", default=None)
    p_analyze.add_argument("--start-date", default=None)
    p_analyze.add_argument("--end-date", default=None)
    p_analyze.add_argument("--reference-ticker", default=None, help="Override auto-detected reference ticker (e.g. BTCUSDT, VNINDEX, ^GSPC)")
    p_analyze.add_argument("--lang", default=None, choices=["en", "vn"])
    p_analyze.add_argument("--ma-type", default="ema", choices=["ema", "sma"])
    p_analyze.add_argument("--question", default=None, help="Custom analysis question")
    p_analyze.add_argument("--questions", action="store_true", help="List available question templates and exit")
    p_analyze.add_argument("--context-only", action="store_true", help="Dump raw context without calling LLM (no API key needed)")
    p_analyze.add_argument("--no-system-prompt", action="store_true", help="Exclude system prompt from context output")
    p_analyze.add_argument("--verbose", action="store_true", help="Show thinking tokens during analysis")

    # aipa get-ohlcv-data TICKER [--interval 1D] [--limit N]
    #   [--start-date] [--end-date] [--source] [--ma] [--ema]
    p_ohlcv = sub.add_parser("get-ohlcv-data", help="Fetch raw OHLCV data")
    p_ohlcv.add_argument("tickers", nargs="+", help="Ticker symbol(s)")
    p_ohlcv.add_argument("--interval", default="1D")
    p_ohlcv.add_argument("--limit", type=int, default=None)
    p_ohlcv.add_argument("--start-date", default=None)
    p_ohlcv.add_argument("--end-date", default=None)
    p_ohlcv.add_argument("--source", default=None)
    p_ohlcv.add_argument("--ma", action="store_true", default=True)
    p_ohlcv.add_argument("--no-ma", dest="ma", action="store_false")
    p_ohlcv.add_argument("--ema", action="store_true", default=False)
    p_ohlcv.add_argument("--no-system-prompt", action="store_true", help="Exclude persona header from output")

    # aipa deep-research [question]
    p_deep = sub.add_parser("deep-research", help="Multi-agent deep research")
    p_deep.add_argument("question", nargs="*", help="Research question")
    p_deep.add_argument("--resume", default=None, help="Resume from checkpoint session ID")
    p_deep.add_argument("--output", default=None, help="Save final report to file")
    p_deep.add_argument("--lang", default=None, choices=["en", "vn"], help="Override language")
    p_deep.add_argument("--source", default=None, choices=["vn", "crypto", "global", "yahoo", "sjc"], help="Filter by data source (default: vn)")
    p_deep.add_argument("--run", action="store_true", help="Run the full multi-agent pipeline (5-10 min). Default is context-only (market snapshot).")

    # aipa live-data [tickers...] [--interval 1D] [--top 50]
    p_live = sub.add_parser("live-data", help="Latest candle with top tickers by trading value")
    p_live.add_argument("tickers", nargs="*", help="Ticker symbol(s) (omit for top N by trading value)")
    p_live.add_argument("--top", type=int, default=50, help="Number of top tickers when no tickers specified (default: 50)")
    p_live.add_argument("--interval", default="1D", choices=["1D", "1h", "1m", "5m", "15m", "30m", "4h", "1W", "2W"])
    p_live.add_argument("--source", default=None, choices=["vn", "crypto", "global", "yahoo", "sjc"], help="Filter by data source")

    # aipa ticker-list [--source vn] [--group NGAN_HANG]
    p_tlist = sub.add_parser("ticker-list", help="List available ticker symbols and metadata")
    p_tlist.add_argument("--source", default=None, choices=["vn", "crypto", "global", "yahoo", "sjc"], help="Filter by data source")
    p_tlist.add_argument("--group", default=None, help="Filter by group (e.g. NGAN_HANG, CHUNG_KHOAN)")
    p_tlist.add_argument("--compact", action="store_true", help="Output symbols only, comma-separated")

    # aipa performers [--sort-by close_changed] [--direction desc] [--limit 10]
    #   [--min-volume 10000] [--source vn] [--group NGAN_HANG]
    p_perf = sub.add_parser("performers", help="Top/worst performers ranked by a chosen metric")
    p_perf.add_argument("--sort-by", default="close_changed",
        choices=["close_changed", "volume", "value", "volume_changed",
                 "ma10_score", "ma20_score", "ma50_score", "ma100_score", "ma200_score",
                 "total_money_changed"])
    p_perf.add_argument("--direction", default="desc", choices=["desc", "asc"])
    p_perf.add_argument("--limit", type=int, default=10)
    p_perf.add_argument("--min-volume", type=int, default=10000)
    p_perf.add_argument("--source", default="vn", choices=["vn", "crypto", "global", "yahoo", "sjc"])
    p_perf.add_argument("--group", default=None, help="Filter by group/sector (e.g. NGAN_HANG, CHUNG_KHOAN, BAT_DONG_SAN)")

    # aipa volume-profile TICKER [--date YYYY-MM-DD] [--start-date] [--end-date]
    #   [--source vn] [--bins 50] [--value-area-pct 70]
    p_vp = sub.add_parser("volume-profile", help="Volume-by-price histogram analysis")
    p_vp.add_argument("ticker", help="Ticker symbol")
    p_vp.add_argument("--date", default=None, help="Single date (YYYY-MM-DD)")
    p_vp.add_argument("--start-date", default=None, help="Start date (YYYY-MM-DD)")
    p_vp.add_argument("--end-date", default=None, help="End date (YYYY-MM-DD)")
    p_vp.add_argument("--source", default=None, choices=["vn", "crypto", "global", "yahoo", "sjc"])
    p_vp.add_argument("--bins", type=int, default=50, help="Number of price bins (2-200)")
    p_vp.add_argument("--value-area-pct", type=float, default=70.0, help="Value area target percentage (60-90)")

    # aipa setup
    sub.add_parser("setup", help="Interactive first-run setup")

    # aipa watchlist [ls|get|set]
    p_wl = sub.add_parser("watchlist", help="Manage watchlists (predefined + custom)")
    wl_sub = p_wl.add_subparsers(dest="watchlist_command")
    wl_sub.add_parser("ls", help="List all watchlists")
    p_wl_get = wl_sub.add_parser("get", help="Show tickers for a watchlist")
    p_wl_get.add_argument("name", help="Watchlist name")
    p_wl_set = wl_sub.add_parser("set", help="Create/update a custom watchlist")
    p_wl_set.add_argument("name", help="Custom watchlist name")
    p_wl_set.add_argument("tickers", nargs="+", help="Ticker symbols")

    # aipa resume [session_id|index]
    p_resume = sub.add_parser("resume", help="Open TUI with a resumed chat session")
    p_resume.add_argument("session", nargs="?", default=None, help="Session ID prefix or list index (default: most recent)")

    args = parser.parse_args()

    if args.command == "setup":
        from .cli_setup import cmd_setup
        cmd_setup()
    elif args.command == "analyze":
        if not getattr(args, "context_only", False) and not getattr(args, "questions", False):
            _ensure_setup()
        from .cli_commands import cmd_analyze
        asyncio.run(cmd_analyze(args))
    elif args.command == "get-ohlcv-data":
        from .cli_commands import cmd_get_ohlcv
        cmd_get_ohlcv(args)
    elif args.command == "live-data":
        from .cli_commands import cmd_live_data
        cmd_live_data(args)
    elif args.command == "ticker-list":
        from .cli_commands import cmd_ticker_list
        cmd_ticker_list(args)
    elif args.command == "performers":
        from .cli_commands import cmd_performers
        cmd_performers(args)
    elif args.command == "volume-profile":
        from .cli_commands import cmd_volume_profile
        cmd_volume_profile(args)
    elif args.command == "deep-research":
        if getattr(args, "run", False):
            _ensure_setup()
        from .cli_commands import cmd_deep_research
        cmd_deep_research(
            question=" ".join(args.question) if args.question else "",
            resume=args.resume,
            output=args.output,
            lang=args.lang,
            run_pipeline=args.run,
            source=args.source,
        )
    elif args.command == "resume":
        from .cli_commands import cmd_resume
        cmd_resume(args)
    elif args.command == "watchlist":
        from .cli_commands import cmd_watchlist
        cmd_watchlist(args)
    else:
        _ensure_setup()
        from .app import main
        main()


if __name__ == "__main__":
    run()
