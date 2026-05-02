"""Quick smoke test: verify aipriceaction SDK works against any S3 archive URL.

Usage:
    cd sdk/aipriceaction-python
    uv run python scripts/test_url.py                          # defaults to https://s3.aipriceaction.com
    uv run python scripts/test_url.py http://localhost:9000/aipriceaction-archive
    uv run python scripts/test_url.py https://aipriceaction-archive.s3.us-east-1.amazonaws.com
"""

from __future__ import annotations

import random
import sys
import time

from aipriceaction import AIPriceAction

OK = "\033[32mOK\033[0m"
FAIL = "\033[31mFAIL\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"


def check(label: str, condition: bool, detail: str = "", elapsed: float = 0) -> bool:
    status = OK if condition else FAIL
    timing = f" ({elapsed:.2f}s)" if elapsed else ""
    msg = f"  [{status}] {label}{timing}"
    if detail:
        msg += f"\n         {detail}"
    print(msg)
    return condition


def print_df_rows(df, max_rows: int = 3):
    """Print a compact table of DataFrame rows."""
    if df.empty:
        print("         (empty)")
        return
    cols = ["symbol", "time", "close", "volume"]
    available = [c for c in cols if c in df.columns]
    show = df[available].head(max_rows)
    for _, row in show.iterrows():
        parts = []
        for c in available:
            v = row[c]
            if isinstance(v, float):
                parts.append(f"{c}={v:,.2f}")
            else:
                parts.append(f"{c}={v}")
        print(f"         {', '.join(parts)}")
    if len(df) > max_rows:
        print(f"         ... ({len(df)} total rows)")


def main(url: str) -> int:
    t_start = time.time()
    print(f"\n{BOLD}aipriceaction SDK smoke test{BOLD}")
    print(f"  URL: {url}\n")

    client = AIPriceAction(url)
    passed = 0
    total = 0

    # 1. Tickers
    total += 1
    try:
        t0 = time.time()
        tickers = client.get_tickers()
        elapsed = time.time() - t0
        sources = {}
        for t in tickers:
            sources[t.source] = sources.get(t.source, 0) + 1
        src_detail = ", ".join(f"{k}={v}" for k, v in sorted(sources.items()))
        ok = check("get_tickers()", len(tickers) > 0,
                     f"{len(tickers)} tickers\n         Sources: {src_detail}",
                     elapsed=elapsed)
        # Show first 5 tickers
        for t in tickers[:5]:
            name = t.name or ""
            group = f" [{t.group}]" if t.group else ""
            print(f"         {DIM}{t.source}/{t.ticker}{group} {name}{DIM}")
        if len(tickers) > 5:
            print(f"         ... and {len(tickers) - 5} more")
        passed += int(ok)
    except Exception as e:
        check("get_tickers()", False, str(e))

    # 2. Source filter
    for src_name in ["vn", "crypto", "yahoo"]:
        total += 1
        try:
            t0 = time.time()
            filtered = client.get_tickers(source=src_name)
            elapsed = time.time() - t0
            check(f"get_tickers(source='{src_name}')", True,
                  f"{len(filtered)} tickers", elapsed=elapsed)
            passed += 1
        except Exception as e:
            check(f"get_tickers(source='{src_name}')", False, str(e))

    # 3. Content hash
    total += 1
    try:
        t0 = time.time()
        h = client.get_content_hash("VCB", "1D", "2025-04-29")
        elapsed = time.time() - t0
        ok = check("get_content_hash()", h is not None,
                     f"VCB 1D 2025-04-29\n         hash={h}",
                     elapsed=elapsed)
        passed += int(ok)
    except Exception as e:
        check("get_content_hash()", False, str(e))

    # 4. Content hash — missing file
    total += 1
    try:
        t0 = time.time()
        h = client.get_content_hash("VCB", "1D", "2099-01-01")
        elapsed = time.time() - t0
        ok = check("get_content_hash() [missing]", h is None,
                     f"VCB 1D 2099-01-01 -> None (expected)",
                     elapsed=elapsed)
        passed += int(ok)
    except Exception as e:
        check("get_content_hash() [missing]", False, str(e))

    # 5. OHLCV (no MA)
    total += 1
    try:
        t0 = time.time()
        df = client.get_ohlcv("VCB", interval="1D",
                             start_date="2025-04-28", end_date="2025-04-29", ma=False)
        elapsed = time.time() - t0
        cols = sorted(df.columns.tolist())
        ok = check("get_ohlcv(ma=False)", len(df) > 0,
                     f"{len(df)} rows, {len(df.columns)} cols\n         Columns: {cols}",
                     elapsed=elapsed)
        print_df_rows(df)
        passed += int(ok)
    except Exception as e:
        check("get_ohlcv(ma=False)", False, str(e))

    # 6. OHLCV with SMA
    total += 1
    try:
        t0 = time.time()
        df = client.get_ohlcv("VCB", interval="1D",
                             start_date="2025-04-29", end_date="2025-04-29", ma=True, ema=False)
        elapsed = time.time() - t0
        ma_cols = [c for c in df.columns if c.startswith("ma") or "changed" in c]
        ok = check("get_ohlcv(ma=True, ema=False)", len(df) > 0 and len(ma_cols) > 0,
                     f"{len(df)} rows, {len(ma_cols)} MA columns",
                     elapsed=elapsed)
        print_df_rows(df)
        if not df.empty:
            row = df.iloc[-1]
            print(f"         Indicators:")
            for c in ["ma10", "ma20", "ma50", "ma100", "ma200"]:
                if c in df.columns:
                    print(f"           {c}={row[c]:,.2f}")
            for c in ["ma10_score", "ma20_score", "ma50_score"]:
                if c in df.columns:
                    print(f"           {c}={row[c]:+.2f}%")
            for c in ["close_changed", "volume_changed", "total_money_changed"]:
                if c in df.columns:
                    v = row[c]
                    if v is not None and v == v:  # not NaN
                        if "money" in c:
                            print(f"           {c}={v:,.0f}")
                        else:
                            print(f"           {c}={v:+.2f}%")
        passed += int(ok)
    except Exception as e:
        check("get_ohlcv(ma=True, ema=False)", False, str(e))

    # 7. OHLCV with EMA
    total += 1
    try:
        t0 = time.time()
        df_ema = client.get_ohlcv("VCB", interval="1D",
                                  start_date="2025-04-29", end_date="2025-04-29", ema=True)
        elapsed = time.time() - t0
        ok = check("get_ohlcv(ema=True)", len(df_ema) > 0,
                     f"{len(df_ema)} rows", elapsed=elapsed)
        passed += int(ok)
    except Exception as e:
        check("get_ohlcv(ema=True)", False, str(e))

    # 8. SMA vs EMA differ
    total += 1
    try:
        t0 = time.time()
        # Need a wider range so EMA and SMA diverge
        df_sma = client.get_ohlcv("VCB", interval="1D",
                                  start_date="2025-04-10", end_date="2025-04-29", ma=True, ema=False)
        df_ema = client.get_ohlcv("VCB", interval="1D",
                                  start_date="2025-04-10", end_date="2025-04-29", ma=True, ema=True)
        elapsed = time.time() - t0
        if not df_sma.empty and not df_ema.empty:
            last_sma = df_sma.iloc[-1]["ma20"]
            last_ema = df_ema.iloc[-1]["ma20"]
            diff = abs(last_sma - last_ema)
            ok = check("SMA != EMA", diff > 0.01,
                         f"SMA ma20={last_sma:,.2f}, EMA ma20={last_ema:,.2f} (diff={diff:.2f})",
                         elapsed=elapsed)
        else:
            ok = check("SMA != EMA", False, "no data", elapsed=elapsed)
        passed += int(ok)
    except Exception as e:
        check("SMA != EMA", False, str(e))

    # 9. Multiple tickers
    total += 1
    try:
        t0 = time.time()
        df = client.get_ohlcv(tickers=["VCB", "BTCUSDT"], interval="1D",
                             start_date="2025-04-29", end_date="2025-04-29", ma=False)
        elapsed = time.time() - t0
        symbols = sorted(df["symbol"].unique()) if not df.empty else []
        ok = check("get_ohlcv(tickers=[...])", len(df) > 0,
                     f"symbols={symbols}, {len(df)} rows",
                     elapsed=elapsed)
        print_df_rows(df)
        passed += int(ok)
    except Exception as e:
        check("get_ohlcv(tickers=[...])", False, str(e))

    # 10. Limit
    total += 1
    try:
        t0 = time.time()
        df = client.get_ohlcv("VCB", interval="1D",
                             start_date="2025-04-28", end_date="2025-04-29",
                             limit=1, ma=False)
        elapsed = time.time() - t0
        ok = check("get_ohlcv(limit=1)", len(df) == 1,
                     f"got {len(df)} row(s)", elapsed=elapsed)
        if not df.empty:
            print(f"         {df.iloc[0]['time']}  close={df.iloc[0]['close']:,.2f}")
        passed += int(ok)
    except Exception as e:
        check("get_ohlcv(limit=1)", False, str(e))

    # 11. Download CSV
    total += 1
    try:
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="aipriceaction-smoke-")
        t0 = time.time()
        paths = client.download_csv("VCB", interval="1D",
                                    start_date="2025-04-29", end_date="2025-04-29",
                                    output_dir=tmpdir)
        elapsed = time.time() - t0
        ok = check("download_csv()", len(paths) > 0,
                     f"{len(paths)} file(s)", elapsed=elapsed)
        for p in paths:
            print(f"         {p}")
        passed += int(ok)
    except Exception as e:
        check("download_csv()", False, str(e))

    # 12. Recent data — sample random tickers from each source, check last 7 days
    print(f"\n  {BOLD}── Recent data (last 7 days, random ticker sampling) ──{BOLD}\n")
    from datetime import datetime, timedelta, timezone
    days = [(datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    t0 = time.time()
    try:
        all_tickers = client.get_tickers()
        by_source: dict[str, list[str]] = {}
        for t in all_tickers:
            by_source.setdefault(t.source, []).append(t.ticker)

        for src in sorted(by_source.keys()):
            tickers_list = by_source[src]
            if not tickers_list:
                continue
            sample = random.sample(tickers_list, min(5, len(tickers_list)))
            for sym in sample:
                results = {}
                for d in days:
                    try:
                        h = client.get_content_hash(sym, "1D", d, source=src)
                        if h is not None:
                            results[d] = True
                    except Exception:
                        results[d] = False
                ok_days = [d for d, found in results.items() if found]
                bar = "".join("█" if d in ok_days else "░" for d in days)
                tag = "OK" if len(ok_days) > 0 else "WARN"
                print(f"  [{tag}] {src:6s}/{sym:16s}  {bar}  {len(ok_days)}/{len(days)} days")
        elapsed = time.time() - t0
        print(f"\n  {DIM}Sampling completed in {elapsed:.2f}s{DIM}")
    except Exception as e:
        print(f"  [FAIL] recent sampling: {e}")

    # Summary
    total_elapsed = time.time() - t_start
    print(f"\n{BOLD}Results: {passed}/{total} passed ({total_elapsed:.2f}s){BOLD}")
    return 0 if passed == total else 1


DEFAULT_URL = "https://s3.aipriceaction.com"


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) >= 2 else DEFAULT_URL
    sys.exit(main(url))
