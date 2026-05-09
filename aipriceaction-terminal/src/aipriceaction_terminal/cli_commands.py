"""CLI subcommand implementations — thin wrappers around SDK methods."""


def cmd_analyze(args) -> None:
    from aipriceaction import AIContextBuilder

    builder = AIContextBuilder(lang=args.lang, ma_type=args.ma_type)
    if len(args.tickers) == 1:
        context = builder.build(
            ticker=args.tickers[0],
            interval=args.interval,
            limit=args.limit,
            source=args.source,
            start_date=args.start_date,
            end_date=args.end_date,
            reference_ticker=args.reference_ticker,
            include_system_prompt=False,
        )
    else:
        context = builder.build(
            tickers=args.tickers,
            interval=args.interval,
            limit=args.limit,
            source=args.source,
            start_date=args.start_date,
            end_date=args.end_date,
            reference_ticker=args.reference_ticker,
            include_system_prompt=False,
        )
    print(context)


def cmd_get_ohlcv(args) -> None:
    from aipriceaction import AIPriceAction

    client = AIPriceAction()
    df = client.get_ohlcv(
        ticker=args.ticker,
        interval=args.interval,
        limit=args.limit,
        start_date=args.start_date,
        end_date=args.end_date,
        source=args.source,
        ma=args.ma,
        ema=args.ema,
    )
    print(df.to_string(index=False))


def cmd_deep_research(question: str) -> None:
    print("deep-research is not yet implemented.")
