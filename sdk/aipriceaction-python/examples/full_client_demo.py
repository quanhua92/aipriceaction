"""Full AIPriceAction client demo — every major feature, section by section.

Run:
    uv run python examples/full_client_demo.py

Each section is a standalone function you can copy into your own project.
"""

from aipriceaction import (
    AIPriceAction,
    build_performers,
    compute_volume_profile,
)
from aipriceaction.aggregator import aggregate_ohlcv

SEPARATOR = "=" * 72


def section(num: int, title: str) -> None:
    print(f"\n{SEPARATOR}")
    print(f"  Section {num}: {title}")
    print(SEPARATOR)


# ── 1. Client Setup ──────────────────────────────────────────────────────────


def demo_client_setup() -> AIPriceAction:
    section(1, "Client Setup")

    print("# Default: live data enabled, UTC+7 (Vietnam timezone)")
    client = AIPriceAction()
    print(f"  base_url   = {client.base_url}")
    print(f"  use_live   = {client.use_live}")

    print("\n# Options:")
    print("  AIPriceAction(use_live=False)        # S3 archive only, no live overlay")
    print("  AIPriceAction(utc_offset=0)          # keep raw UTC timestamps")
    print("  AIPriceAction(utc_offset=9)          # JST/KST")
    print("  AIPriceAction(cache_dir='./cache')   # persistent disk cache")

    return client


# ── 2. Ticker Metadata ───────────────────────────────────────────────────────


def demo_tickers(client: AIPriceAction) -> None:
    section(2, "Ticker Metadata")

    all_tickers = client.get_tickers()
    print(f"# Total tickers: {len(all_tickers)}")
    print("  Fields: ticker, source, name, exchange, group")
    for t in all_tickers[:3]:
        print(f"  {t.ticker:10s} source={t.source:6s} name={t.name}")

    vn_tickers = client.get_tickers(source="vn")
    crypto_tickers = client.get_tickers(source="crypto")
    yahoo_tickers = client.get_tickers(source="yahoo")
    print(f"\n# By source: vn={len(vn_tickers)}, crypto={len(crypto_tickers)}, yahoo={len(yahoo_tickers)}")


# ── 3. Single Ticker OHLCV ──────────────────────────────────────────────────


def demo_single_ticker(client: AIPriceAction) -> None:
    section(3, "Single Ticker OHLCV")

    df = client.get_ohlcv("VCB", interval="1D", limit=5, ma=False)
    print("# client.get_ohlcv('VCB', interval='1D', limit=5, ma=False)")
    print(f"  Shape: {df.shape}")
    print(f"  Columns: {list(df.columns)}")
    print(f"\n{df.to_string(index=False)}")


# ── 4. Date Range & Limit ────────────────────────────────────────────────────


def demo_date_range(client: AIPriceAction) -> None:
    section(4, "Date Range & Limit")

    df = client.get_ohlcv(
        "VCB",
        interval="1D",
        start_date="2026-04-01",
        end_date="2026-04-30",
        ma=False,
    )
    print("# client.get_ohlcv('VCB', start_date='2026-04-01', end_date='2026-04-30')")
    print(f"  Rows: {len(df)}")
    print(f"  First: {df['time'].iloc[0]}")
    print(f"  Last:  {df['time'].iloc[-1]}")

    df_limit = client.get_ohlcv("BTCUSDT", interval="1D", limit=3, ma=False)
    print("\n# client.get_ohlcv('BTCUSDT', limit=3)")
    print(f"  Rows: {len(df_limit)}")
    for _, row in df_limit.iterrows():
        print(f"  {row['time']}  C={row['close']:>12,.2f}")


# ── 5. Multi-Ticker ──────────────────────────────────────────────────────────


def demo_multi_ticker(client: AIPriceAction) -> None:
    section(5, "Multi-Ticker (mixed sources)")

    df = client.get_ohlcv(
        tickers=["VCB", "FPT", "BTCUSDT"],
        interval="1D",
        limit=3,
        ma=False,
    )
    print("# client.get_ohlcv(tickers=['VCB', 'FPT', 'BTCUSDT'], limit=3)")
    print(f"  Shape: {df.shape}")
    for sym in df["symbol"].unique():
        subset = df[df["symbol"] == sym]
        row = subset.iloc[-1]
        print(f"  {sym:10s}  {row['time']}  C={row['close']:>12,.2f}  V={row['volume']:>14,}")


# ── 6. MA & EMA Indicators ───────────────────────────────────────────────────


def demo_indicators(client: AIPriceAction) -> None:
    section(6, "MA & EMA Indicators")

    df_sma = client.get_ohlcv("VCB", interval="1D", limit=3, ma=True, ema=False)
    ma_cols = [c for c in df_sma.columns if c.startswith("ma") or "changed" in c]
    print("# client.get_ohlcv('VCB', ma=True, ema=False)  # SMA (default)")
    print(f"  MA columns: {ma_cols}")
    for _, row in df_sma.iterrows():
        print(
            f"  {row['time']}  C={row['close']:>10,.2f}  "
            f"MA20={row['ma20']:>10,.2f}  MA50={row['ma50']:>10,.2f}  "
            f"MA200={row.get('ma200', 'N/A'):>10}  "
            f"chg={row.get('close_changed', 'N/A'):>7}%"
        )

    df_ema = client.get_ohlcv("VCB", interval="1D", limit=3, ma=True, ema=True)
    print("\n# client.get_ohlcv('VCB', ema=True)  # EMA instead of SMA")
    for _, row in df_ema.iterrows():
        print(
            f"  {row['time']}  C={row['close']:>10,.2f}  "
            f"EMA20={row['ma20']:>10,.2f}  EMA50={row['ma50']:>10,.2f}"
        )


# ── 7. Live Data ─────────────────────────────────────────────────────────────


def demo_live_data(client: AIPriceAction) -> None:
    section(7, "Live Data (direct API access)")

    data = client.fetch_live_data("1D", ma=True)
    if data is None:
        print("  Live API unavailable (requires network)")
        return

    print("# client.fetch_live_data('1D', ma=True)")
    print(f"  Tickers in snapshot: {len(data)}")

    for sym in ["VCB", "FPT", "BTCUSDT"]:
        candles = data.get(sym)
        if candles:
            c = candles[-1]
            print(
                f"  {sym:10s}  C={c.get('close', 0):>12,.2f}  "
                f"V={c.get('volume', 0):>14,}  "
                f"chg={c.get('close_changed', 'N/A'):>7}%"
            )


# ── 8. Performers (Top / Worst) ──────────────────────────────────────────────


def demo_performers(client: AIPriceAction) -> None:
    section(8, "Performers (Top / Worst)")

    live_data = client.fetch_live_data("1D", ma=True)
    if live_data is None:
        print("  Live API unavailable")
        return

    tickers = client.get_tickers(source="vn")
    sector_map = {t.ticker: t.group for t in tickers if t.group}

    top, worst = build_performers(
        live_data, sector_map, sort_by="close_changed", limit=5
    )

    print("# Top 5 by price change:")
    for p in top:
        print(f"  {p.symbol:10s}  {p.close_changed:>+7.2f}%  sector={p.sector}")

    print("\n# Worst 5 by price change:")
    for p in worst:
        print(f"  {p.symbol:10s}  {p.close_changed:>+7.2f}%  sector={p.sector}")


# ── 9. Volume Profile ────────────────────────────────────────────────────────


def demo_volume_profile(client: AIPriceAction) -> None:
    section(9, "Volume Profile")

    df = client.get_ohlcv(
        "VCB",
        interval="1m",
        start_date="2026-05-19",
        end_date="2026-05-19",
        ma=False,
    )
    if df.empty:
        print("  No 1m data for this date (non-trading day)")
        return

    result = compute_volume_profile(df, "VCB", source="vn", bins=30)

    print("# compute_volume_profile(df, 'VCB', bins=30)")
    print(f"  Total volume:  {result.total_volume:,.0f}")
    print(f"  POC:           {result.poc.price:,.0f}  ({result.poc.percentage:.1f}%)")
    print(
        f"  Value Area:    {result.value_area.low:,.0f} — {result.value_area.high:,.0f}"
    )
    print(f"  Price range:   {result.price_range.low:,.0f} — {result.price_range.high:,.0f}")
    print(f"  Mean / StdDev: {result.statistics.mean_price:,.0f} / {result.statistics.std_deviation:,.0f}")

    print("\n# Top 5 volume nodes:")
    for level in sorted(result.profile, key=lambda x: x.volume, reverse=True)[:5]:
        print(
            f"  {level.price:>10,.0f}  vol={level.volume:>10,.0f}  "
            f"{level.percentage:.1f}%  cum={level.cumulative_percentage:.1f}%"
        )


# ── 10. OHLCV Aggregation ────────────────────────────────────────────────────


def demo_aggregation(client: AIPriceAction) -> None:
    section(10, "OHLCV Aggregation (client-side)")

    df_1m = client.get_ohlcv(
        "VCB",
        interval="1m",
        start_date="2026-05-19",
        end_date="2026-05-19",
        ma=False,
    )
    if df_1m.empty:
        print("  No 1m data for this date")
        return

    print(f"# Raw 1m bars: {len(df_1m)}")

    df_5m = aggregate_ohlcv(df_1m.drop(columns=["symbol"]), "5m", source="vn")
    print(f"# aggregate_ohlcv(df_1m, '5m')  → {len(df_5m)} bars")
    for _, row in df_5m.head(5).iterrows():
        print(
            f"  {row['time']}  O={row['open']:>10,.2f}  H={row['high']:>10,.2f}  "
            f"L={row['low']:>10,.2f}  C={row['close']:>10,.2f}  V={row['volume']:>10,}"
        )

    df_15m = aggregate_ohlcv(df_1m.drop(columns=["symbol"]), "15m", source="vn")
    print(f"\n# aggregate_ohlcv(df_1m, '15m') → {len(df_15m)} bars")

    print("\n# Or use get_ohlcv() directly with aggregated intervals:")
    df_direct = client.get_ohlcv("VCB", interval="5m", limit=5, ma=False)
    print(f"# client.get_ohlcv('VCB', interval='5m', limit=5)  → {len(df_direct)} bars")
    for _, row in df_direct.iterrows():
        print(
            f"  {row['time']}  O={row['open']:>10,.2f}  C={row['close']:>10,.2f}  V={row['volume']:>10,}"
        )


# ── 11. CSV Download ─────────────────────────────────────────────────────────


def demo_csv_download(client: AIPriceAction) -> None:
    section(11, "CSV Download")

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        paths = client.download_csv(
            "VCB",
            interval="1D",
            limit=3,
            output_dir=tmpdir,
        )
        print("# client.download_csv('VCB', interval='1D', limit=3)")
        print(f"  Downloaded: {len(paths)} files")
        for p in paths:
            size = Path(p).stat().st_size
            print(f"  {Path(p).name}  ({size:,} bytes)")
            if size < 500:
                print(Path(p).read_text()[:200])


# ── 12. Timezone Conversion ──────────────────────────────────────────────────


def demo_timezone(client: AIPriceAction) -> None:
    section(12, "Timezone Conversion")

    print("# Client utc_offset: default (UTC+7)")
    print("  client.convert_time('2026-05-19T09:00:00Z', '1D')")
    print(f"  → {client.convert_time('2026-05-19T09:00:00Z', '1D')}")

    client_utc = AIPriceAction(utc_offset=0)
    print("\n# Client utc_offset=0 (raw UTC)")
    print("  client.convert_time('2026-05-19T09:00:00Z', '1h')")
    print(f"  → {client_utc.convert_time('2026-05-19T09:00:00Z', '1h')}")

    print("\n# Timezone applies to get_ohlcv() time column automatically:")
    df_vn = client.get_ohlcv("VCB", interval="1h", limit=2, ma=False)
    print("  UTC+7 times:")
    for _, row in df_vn.iterrows():
        print(f"    {row['time']}")

    df_utc = client_utc.get_ohlcv("VCB", interval="1h", limit=2, ma=False)
    print("  UTC+0 times:")
    for _, row in df_utc.iterrows():
        print(f"    {row['time']}")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    print("AIPriceAction Python SDK — Full Client Demo")
    print("============================================")

    client = demo_client_setup()

    demos = [
        lambda: demo_tickers(client),
        lambda: demo_single_ticker(client),
        lambda: demo_date_range(client),
        lambda: demo_multi_ticker(client),
        lambda: demo_indicators(client),
        lambda: demo_live_data(client),
        lambda: demo_performers(client),
        lambda: demo_volume_profile(client),
        lambda: demo_aggregation(client),
        lambda: demo_csv_download(client),
        lambda: demo_timezone(client),
    ]

    for demo in demos:
        try:
            demo()
        except Exception as e:
            print(f"\n  Error: {e}")

    print(f"\n{SEPARATOR}")
    print("  Done! Copy any section function into your own project.")
    print(SEPARATOR)


if __name__ == "__main__":
    main()
