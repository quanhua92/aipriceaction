"""S3 Archive Performance Test — hits local rustfs with per-request logging."""

import argparse
import sys
import time

import requests


class RequestLogger:
    """Wraps a requests.Session to log every HTTP request."""

    def __init__(self, session: requests.Session, base_url: str = "") -> None:
        self.log: list[dict] = []
        self._cumulative_count = 0
        self._cumulative_time = 0.0
        self._session = session
        self._base_url = base_url
        self._original_send = session.send

    def install(self) -> None:
        self._session.send = self._patched_send

    def uninstall(self) -> None:
        self._session.send = self._original_send

    def reset(self) -> None:
        self._cumulative_count += len(self.log)
        self._cumulative_time += sum(r["elapsed"] for r in self.log)
        self.log.clear()

    def _patched_send(self, request, **kwargs):
        t0 = time.monotonic()
        resp = self._original_send(request, **kwargs)
        elapsed = time.monotonic() - t0
        path = str(request.url)
        self.log.append({
            "method": request.method,
            "path": path,
            "status": resp.status_code,
            "elapsed": elapsed,
        })
        return resp

    @property
    def total_requests(self) -> int:
        return self._cumulative_count + len(self.log)

    @property
    def total_time(self) -> float:
        return self._cumulative_time + sum(r["elapsed"] for r in self.log)

    def print_log(self) -> None:
        successes = [r for r in self.log if r["status"] == 200]
        misses = [r for r in self.log if r["status"] != 200]
        for r in successes:
            path = r["path"].replace(self._base_url + "/", "")
            print(f"  {r['method']} {path} ({r['elapsed']:.3f}s)")
        if misses:
            print(f"  ... +{len(misses)} 404s skipped")


def run_test(
    name: str,
    fn,
    logger: RequestLogger,
    threshold: float,
    test_num: int,
    total_tests: int,
) -> bool:
    logger.reset()
    print(f"\n── [{test_num}/{total_tests}] {name} ──")

    t0 = time.monotonic()
    try:
        result = fn()
        elapsed = time.monotonic() - t0
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"  \u2717 FAIL — {e} ({elapsed:.3f}s)")
        return False

    logger.print_log()

    passed = elapsed < threshold
    icon = "\u2713 PASS" if passed else "\u2717 FAIL"
    reqs = len(logger.log)
    hits = sum(1 for r in logger.log if r["status"] == 200)
    misses = sum(1 for r in logger.log if r["status"] != 200)
    rows = len(result) if hasattr(result, "__len__") else "?"
    cache_note = " (disk cache)" if hits == 0 and rows > 0 else ""
    print(f"  {icon} ({elapsed:.3f}s < {threshold:.1f}s) [{hits} hits, {misses} 404s, {rows} rows]{cache_note}")
    return passed


def main() -> None:
    parser = argparse.ArgumentParser(description="S3 Archive Performance Test")
    parser.add_argument(
        "--url",
        default="http://localhost:9000/aipriceaction-archive",
        help="S3 archive base URL (default: local rustfs)",
    )
    args = parser.parse_args()

    base_url = args.url.rstrip("/")

    print(f"\n{'=' * 50}")
    print(f"  S3 Archive Performance Test")
    print(f"{'=' * 50}")
    print(f"  Target: {base_url}\n")

    from aipriceaction import AIPriceAction

    import tempfile
    cache_dir = tempfile.mkdtemp(prefix="test-perf-")
    client = AIPriceAction(base_url, cache_dir=cache_dir)
    logger = RequestLogger(client._session, base_url=base_url)
    logger.install()

    # ── Basic scenarios ──

    def test_tickers():
        return client.get_tickers()

    def test_vcb_daily():
        return client.get_ohlcv("VCB", ma=False)

    def test_vcb_daily_ma():
        return client.get_ohlcv("VCB", ma=True)

    def test_batch_ma():
        return client.get_ohlcv(
            tickers=["VCB", "FPT", "VIC", "VNM"], ma=True
        )

    def test_vcb_hourly():
        return client.get_ohlcv("VCB", interval="1h", ma=False)

    def test_vcb_minute():
        return client.get_ohlcv("VCB", interval="1m", ma=False)

    def test_btcusdt_daily_ma():
        return client.get_ohlcv("BTCUSDT", ma=True)

    # ── start_date + limit (single ticker) ──

    def test_start_limit():
        """start_date + limit → limit=10000 (has explicit dates)"""
        return client.get_ohlcv("VCB", start_date="2025-04-01", limit=10, ma=False)

    def test_start_limit_ma():
        """start_date + limit + ma=True → fetches MA buffer before start"""
        return client.get_ohlcv("VCB", start_date="2025-04-01", limit=5, ma=True)

    def test_start_limit_hourly():
        """start_date + limit + 1h → per-day backwards from start"""
        return client.get_ohlcv("VCB", interval="1h", start_date="2026-04-01", limit=50, ma=False)

    # ── end_date + limit (no start_date) ──

    def test_end_limit():
        """end_date + limit, no start → greedy backwards from end"""
        return client.get_ohlcv("VCB", end_date="2025-12-31", limit=30, ma=False)

    def test_end_limit_ma():
        """end_date + limit + ma=True → greedy backwards with MA buffer"""
        return client.get_ohlcv("VCB", end_date="2025-12-31", limit=10, ma=True)

    # ── start_date only (no limit, no end_date) ──

    def test_start_only():
        """start_date only, no limit → limit=10000 (has explicit dates)"""
        return client.get_ohlcv("VCB", start_date="2025-04-01", ma=False)

    def test_start_only_batch():
        """start_date + batch tickers, no limit → limit=1 per ticker"""
        return client.get_ohlcv(
            tickers=["VCB", "FPT"], start_date="2025-04-01", ma=False
        )

    # ── no dates, no limit (defaults) ──

    def test_no_dates_batch():
        """no dates, no limit, batch → limit=1 per ticker, ma=True"""
        return client.get_ohlcv(
            tickers=["VCB", "FPT", "VIC"], ma=True
        )

    # ── start_date + end_date, no limit ──

    def test_start_end():
        """start_date + end_date, no limit → fetch full range"""
        return client.get_ohlcv("VCB", start_date="2025-04-01", end_date="2025-04-30", ma=False)

    # ── start_date + end_date + limit + batch ──

    def test_start_end_limit_batch():
        """start_date + end_date + limit + batch tickers"""
        return client.get_ohlcv(
            tickers=["VCB", "FPT"], start_date="2025-04-01",
            end_date="2025-04-30", limit=5, ma=True
        )

    # ── Intraday with MA ──

    def test_hourly_ma():
        """1h + ma=True → backwards fetch with MA buffer"""
        return client.get_ohlcv("VCB", interval="1h", ma=True)

    def test_minute_ma():
        """1m + ma=True → 1 day file has 390 bars > 252+200 need"""
        return client.get_ohlcv("VCB", interval="1m", ma=True)

    # ── AI context: typical use case ──

    def test_ai_single():
        """no dates, limit=1, ma=True → latest price with MA scores"""
        return client.get_ohlcv("VCB", limit=1, ma=True)

    def test_ai_batch():
        """no dates, limit=1, batch, ma=True → latest prices for multiple tickers"""
        return client.get_ohlcv(
            tickers=["VCB", "FPT", "VIC", "VNM", "VNM"], limit=1, ma=True
        )

    def test_ai_limit10():
        """no dates, limit=10, ma=True"""
        return client.get_ohlcv("VCB", limit=10, ma=True)

    # ── limit=None (all data) ──

    def test_no_limit():
        """no dates, limit=None, ma=False → greedy with limit=252"""
        return client.get_ohlcv("VCB", limit=None, ma=False)

    def test_no_limit_ma():
        """no dates, limit=None, ma=True → greedy with 252+200 need"""
        return client.get_ohlcv("VCB", limit=None, ma=True)

    # ── Intraday with explicit dates ──

    def test_1h_start_end():
        """1h + start + end, no limit → limit=10000 (has explicit dates)"""
        return client.get_ohlcv(
            "VCB", interval="1h", start_date="2026-04-01",
            end_date="2026-04-30", ma=False
        )

    def test_1m_start_end():
        """1m + start + end, no limit → limit=10000"""
        return client.get_ohlcv(
            "VCB", interval="1m", start_date="2026-04-28",
            end_date="2026-04-30", ma=False
        )

    # ── end_date only (no start, no limit) ──

    def test_end_only():
        """end_date only, no limit → greedy backwards"""
        return client.get_ohlcv("VCB", end_date="2025-06-30", ma=False)

    # ── start_date only with intraday ──

    def test_start_only_1h():
        """start_date + 1h, no limit → limit=10000"""
        return client.get_ohlcv("VCB", interval="1h", start_date="2026-04-01", ma=False)

    # ── Crypto intraday ──

    def test_btc_1h():
        """BTCUSDT 1h → backwards fetch"""
        return client.get_ohlcv("BTCUSDT", interval="1h", ma=False)

    def test_btc_1m():
        """BTCUSDT 1m → 1 day file enough"""
        return client.get_ohlcv("BTCUSDT", interval="1m", ma=False)

    # ── Large batch, no dates, ma=False ──

    def test_batch_no_ma():
        """batch tickers, no dates, ma=False → limit=1 per ticker"""
        return client.get_ohlcv(
            tickers=["VCB", "FPT", "VIC", "VNM", "BTCUSDT"], ma=False
        )

    scenarios = [
        # ── Basic ──
        ("get_tickers()", test_tickers, 2.0),
        ('VCB 1D, ma=False', test_vcb_daily, 5.0),
        ('VCB 1D, ma=True', test_vcb_daily_ma, 5.0),
        ('batch 4, 1D, ma=True', test_batch_ma, 15.0),
        ('VCB 1h, ma=False', test_vcb_hourly, 5.0),
        ('VCB 1m, ma=False', test_vcb_minute, 5.0),
        ('BTCUSDT 1D, ma=True', test_btcusdt_daily_ma, 5.0),
        # ── Intraday + MA ──
        ('VCB 1h, ma=True', test_hourly_ma, 5.0),
        ('VCB 1m, ma=True', test_minute_ma, 5.0),
        # ── AI context (typical) ──
        ('VCB limit=1, ma=True', test_ai_single, 5.0),
        ('batch 5, limit=1, ma=True', test_ai_batch, 5.0),
        ('VCB limit=10, ma=True', test_ai_limit10, 5.0),
        # ── limit=None ──
        ('VCB limit=None, ma=False', test_no_limit, 5.0),
        ('VCB limit=None, ma=True', test_no_limit_ma, 5.0),
        # ── start_date + limit ──
        ('start_date + limit', test_start_limit, 5.0),
        ('start_date + limit + ma', test_start_limit_ma, 5.0),
        ('start_date + limit + 1h', test_start_limit_hourly, 5.0),
        # ── end_date + limit ──
        ('end_date + limit', test_end_limit, 5.0),
        ('end_date + limit + ma', test_end_limit_ma, 5.0),
        # ── start_date only ──
        ('start_date only (limit=10000)', test_start_only, 5.0),
        ('start_date + batch (limit=1)', test_start_only_batch, 5.0),
        # ── no dates, batch ──
        ('no dates, batch, ma=True', test_no_dates_batch, 5.0),
        ('no dates, batch 5, ma=False', test_batch_no_ma, 5.0),
        # ── start + end ──
        ('start + end, 1D', test_start_end, 5.0),
        ('start + end, 1h', test_1h_start_end, 5.0),
        ('start + end, 1m', test_1m_start_end, 5.0),
        ('start + end + limit + batch', test_start_end_limit_batch, 5.0),
        # ── end_date only ──
        ('end_date only', test_end_only, 5.0),
        # ── start only intraday ──
        ('start_date + 1h', test_start_only_1h, 5.0),
        # ── Crypto intraday ──
        ('BTCUSDT 1h', test_btc_1h, 5.0),
        ('BTCUSDT 1m', test_btc_1m, 5.0),
    ]

    total_tests = len(scenarios)
    passed = 0
    failed = 0

    for i, (name, fn, threshold) in enumerate(scenarios, 1):
        if run_test(name, fn, logger, threshold, i, total_tests):
            passed += 1
        else:
            failed += 1

    logger.uninstall()

    print(f"\n{'=' * 50}")
    print(f"  Summary")
    print(f"{'=' * 50}")
    print(f"  {passed}/{total_tests} passed, {failed} failed")
    print(f"  Total: {logger.total_requests} requests in {logger.total_time:.1f}s")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
