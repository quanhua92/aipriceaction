import re

import pytest
import responses


@pytest.fixture
def mock_s3():
    """Mock S3 archive with responses library."""
    responses.start()

    # meta/tickers.json
    tickers = [
        {"source": "vn", "ticker": "VCB", "name": "Vietcombank", "group": "BANK"},
        {"source": "vn", "ticker": "FPT", "name": "FPT Corp", "group": "TECH"},
        {"source": "crypto", "ticker": "BTCUSDT", "name": "Bitcoin", "group": "CRYPTO_TOP_100"},
    ]
    responses.get(
        "http://localhost:9000/aipriceaction-archive/meta/tickers.json",
        json=tickers,
    )
    responses.head(
        "http://localhost:9000/aipriceaction-archive/meta/tickers.json",
        headers={"x-amz-meta-content-hash": "tickers-hash-001"},
    )

    # VCB daily data
    responses.get(
        "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-28.csv",
        body="2025-04-28 00:00:00,57284.56,57880.24,57086.00,57086.00,1657552",
    )
    responses.get(
        "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-29.csv",
        body="2025-04-29 00:00:00,57284.56,58078.80,56887.44,56887.44,2437717",
    )
    responses.get(
        "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-30.csv",
        status=404,
    )

    # FPT daily data
    responses.get(
        "http://localhost:9000/aipriceaction-archive/ohlcv/vn/FPT/1D/FPT-1D-2025-04-29.csv",
        body="2025-04-29 00:00:00,145000.0,146500.0,144000.0,146000.0,987654",
    )
    responses.get(
        "http://localhost:9000/aipriceaction-archive/ohlcv/vn/FPT/1D/FPT-1D-2025-04-28.csv",
        status=404,
    )

    # BTCUSDT daily data
    responses.get(
        "http://localhost:9000/aipriceaction-archive/ohlcv/crypto/BTCUSDT/1D/BTCUSDT-1D-2025-04-29.csv",
        body="2025-04-29 00:00:00,93000.0,95000.0,92500.0,94256.82,12345678",
    )
    responses.get(
        "http://localhost:9000/aipriceaction-archive/ohlcv/crypto/BTCUSDT/1D/BTCUSDT-1D-2025-04-28.csv",
        status=404,
    )

    # HEAD mocks for per-day CSVs (matching all GET URLs)
    responses.head(
        "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-28.csv",
        headers={"x-amz-meta-content-hash": "vcb-0428-hash"},
    )
    responses.head(
        "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-29.csv",
        headers={"x-amz-meta-content-hash": "abc123"},
    )
    responses.head(
        "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-30.csv",
        status=404,
    )
    responses.head(
        "http://localhost:9000/aipriceaction-archive/ohlcv/vn/FPT/1D/FPT-1D-2025-04-28.csv",
        status=404,
    )
    responses.head(
        "http://localhost:9000/aipriceaction-archive/ohlcv/vn/FPT/1D/FPT-1D-2025-04-29.csv",
        headers={"x-amz-meta-content-hash": "fpt-0429-hash"},
    )
    responses.head(
        "http://localhost:9000/aipriceaction-archive/ohlcv/crypto/BTCUSDT/1D/BTCUSDT-1D-2025-04-28.csv",
        status=404,
    )
    responses.head(
        "http://localhost:9000/aipriceaction-archive/ohlcv/crypto/BTCUSDT/1D/BTCUSDT-1D-2025-04-29.csv",
        headers={"x-amz-meta-content-hash": "btc-0429-hash"},
    )

    # HEAD for yearly files: 404 so SDK falls back to per-day files
    responses.head(
        re.compile(r"http://localhost:9000/aipriceaction-archive/ohlcv/.*/yearly/.*\.csv"),
        status=404,
    )

    # Yearly files: return 404 so SDK falls back to per-day files
    responses.get(
        re.compile(r"http://localhost:9000/aipriceaction-archive/ohlcv/.*/yearly/.*\.csv"),
        status=404,
    )

    yield

    responses.stop()
    responses.reset()


@pytest.fixture
def mock_s3_ma():
    """Mock S3 with 15 days of VCB data for MA indicator tests."""
    responses.start()

    responses.get(
        "http://localhost:9000/aipriceaction-archive/meta/tickers.json",
        json=[{"source": "vn", "ticker": "VCB", "name": "VCB", "group": "BANK"}],
    )
    responses.head(
        "http://localhost:9000/aipriceaction-archive/meta/tickers.json",
        headers={"x-amz-meta-content-hash": "tickers-hash-ma"},
    )

    # 15 trading days of linearly increasing prices
    # Closes: 100.0, 101.0, 102.0, ..., 114.0
    # Volumes: 1000000 each
    from datetime import date, timedelta

    base = date(2025, 4, 10)
    for i in range(15):
        d = base + timedelta(days=i)
        close = 100.0 + i
        o = close - 1.0
        h = close + 2.0
        l = close - 2.0
        vol = 1000000 + i * 10000
        url = f"http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-{d.isoformat()}.csv"
        body = f"{d.isoformat()} 00:00:00,{o},{h},{l},{close},{vol}"
        responses.get(url, body=body)
        responses.head(url, headers={"x-amz-meta-content-hash": f"vcb-ma-{d.isoformat()}-hash"})

    # Catch-all: return 404 for any unmocked CSV URLs (MA buffer expansion hits dates before 2025-04-10)
    responses.get(
        re.compile(r"http://localhost:9000/aipriceaction-archive/ohlcv/.*\.csv"),
        status=404,
    )
    responses.head(
        re.compile(r"http://localhost:9000/aipriceaction-archive/ohlcv/.*\.csv"),
        status=404,
    )

    yield

    responses.stop()
    responses.reset()


@pytest.fixture
def client(tmp_path):
    from aipriceaction import AIPriceAction

    return AIPriceAction(
        "http://localhost:9000/aipriceaction-archive",
        cache_dir=str(tmp_path),
        utc_offset=0,
    )


@pytest.fixture
def mock_s3_yearly_only():
    """Mock S3 with yearly files covering all requested dates, no per-day files.

    Tests that yearly data is returned even when remaining_days=0 (no per-day fallback needed).
    Regression test for: yearly frames discarded when remaining_days was empty.
    """
    responses.start()

    responses.get(
        "http://localhost:9000/aipriceaction-archive/meta/tickers.json",
        json=[{"source": "vn", "ticker": "TCX", "name": "TCX", "group": "REALESTATE"}],
    )
    responses.head(
        "http://localhost:9000/aipriceaction-archive/meta/tickers.json",
        headers={"x-amz-meta-content-hash": "tickers-yearly-hash"},
    )

    yearly_2025 = "\n".join(
        f"{d} 00:00:00,{100.0+i},{105.0+i},{95.0+i},{100.0+i},{500000+i*10000}"
        for i, d in enumerate(["2025-10-21", "2025-10-22", "2025-10-23"])
    )
    yearly_2026 = "\n".join(
        f"{d} 00:00:00,{200.0+i},{205.0+i},{195.0+i},{200.0+i},{600000+i*10000}"
        for i, d in enumerate(["2026-01-05", "2026-01-06"])
    )

    base = "http://localhost:9000/aipriceaction-archive/ohlcv/vn/TCX/yearly"
    responses.get(f"{base}/TCX-1D-2025.csv", body=yearly_2025)
    responses.head(f"{base}/TCX-1D-2025.csv", headers={"x-amz-meta-content-hash": "tcx-2025-hash"})
    responses.get(f"{base}/TCX-1D-2026.csv", body=yearly_2026)
    responses.head(f"{base}/TCX-1D-2026.csv", headers={"x-amz-meta-content-hash": "tcx-2026-hash"})

    responses.get(
        re.compile(r"http://localhost:9000/aipriceaction-archive/ohlcv/.*\.csv"),
        status=404,
    )
    responses.head(
        re.compile(r"http://localhost:9000/aipriceaction-archive/ohlcv/.*\.csv"),
        status=404,
    )

    yield

    responses.stop()
    responses.reset()
