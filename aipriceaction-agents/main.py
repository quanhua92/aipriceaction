from aipriceaction import AIPriceAction, AIContextBuilder


def main():
    client = AIPriceAction()

    tickers = client.get_tickers()
    print(f"{len(tickers)} tickers")

    vn_stocks = [t for t in tickers if t.source == "vn"]
    print(f"{len(vn_stocks)} VN stocks")

    # Fetch OHLCV data
    df = client.get_ohlcv("VCB", interval="1D", limit=5, ma=True)
    print(df[["symbol", "time", "close", "ma20", "ma20_score"]].to_string(index=False))

    # Build AI context
    records = client.to_ticker_records(df)
    ticker_info = [{"symbol": t.ticker, "name": t.name, "group": t.group} for t in tickers if t.ticker == "VCB"]

    builder = (
        AIContextBuilder(lang="en")
        .set_interval("1D")
        .set_market_data(records)
        .set_tickers_info(ticker_info)
    )
    context = builder.build_context(single_ticker="VCB")
    print(context)


if __name__ == "__main__":
    main()
