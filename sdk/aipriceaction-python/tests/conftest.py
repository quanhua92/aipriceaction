import io
import json
import re
import zipfile

import pytest
import responses


_BASE = "http://localhost:9000/aipriceaction-archive"


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
        f"{_BASE}/meta/tickers.json",
        json=[{"source": "vn", "ticker": "TCX", "name": "TCX", "group": "REALESTATE"}],
    )
    responses.head(
        f"{_BASE}/meta/tickers.json",
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

    base = f"{_BASE}/ohlcv/vn/TCX/yearly"
    responses.get(f"{base}/TCX-1D-2025.csv", body=yearly_2025)
    responses.head(f"{base}/TCX-1D-2025.csv", headers={"x-amz-meta-content-hash": "tcx-2025-hash"})
    responses.get(f"{base}/TCX-1D-2026.csv", body=yearly_2026)
    responses.head(f"{base}/TCX-1D-2026.csv", headers={"x-amz-meta-content-hash": "tcx-2026-hash"})

    responses.get(
        re.compile(rf"^{re.escape(_BASE)}/ohlcv/.*\.csv"),
        status=404,
    )
    responses.head(
        re.compile(rf"^{re.escape(_BASE)}/ohlcv/.*\.csv"),
        status=404,
    )

    yield

    responses.stop()
    responses.reset()


@pytest.fixture
def mock_s3_fundamental():
    """Mock S3 with fundamental vn.zip containing ACB (bank) and FPT (non-bank)."""
    responses.start()

    tickers = [
        {"source": "vn", "ticker": "ACB", "name": "Asia Commercial Bank", "group": "BANK"},
        {"source": "vn", "ticker": "FPT", "name": "FPT Corp", "group": "TECH"},
        {"source": "vn", "ticker": "VNINDEX", "name": "VN Index", "group": "INDEX"},
    ]
    responses.get(f"{_BASE}/meta/tickers.json", json=tickers)
    responses.head(
        f"{_BASE}/meta/tickers.json",
        headers={"x-amz-meta-content-hash": "tickers-fund-hash"},
    )

    acb_ci = {
        "symbol": "ACB",
        "exchange": "HOSE",
        "industry": "Ngân hàng",
        "company_type": None,
        "established_year": None,
        "employees": None,
        "market_cap": 122252427056200.0,
        "current_price": 23500.0,
        "outstanding_shares": 5136656599,
        "company_profile": "<div>ACB profile</div>",
        "website": None,
        "shareholders": [
            {"name": "Sather Gate Investments Limited", "percentage": 0.0499},
            {"name": "Dragon Financial Holdings Limited", "percentage": 0.036243},
        ],
        "officers": [
            {"name": "Trần Hùng Huy", "position": "Chủ tịch HĐQT", "percentage": 0.0343},
            {"name": "Mai Thị Hằng", "position": "Tổng Giám đốc", "percentage": None},
        ],
    }

    acb_fr = {
        "ticker": "ACB",
        "updated_at": "2026-05-30T12:06:57.696061244+00:00",
        "count": 3,
        "ratios": [
            {
                "yearReport": 2025, "lengthReport": 5, "ticker": "ACB",
                "pe": 8.2386621048, "pb": 1.3531858822, "ps": 3.7843420353,
                "roe": 0.1755767655, "roa": 0.0165353343, "roic": 0.0,
                "grossMargin": 0.6767738678, "afterTaxProfitMargin": 0.4622981564,
                "npl": 0.0097140463, "car": 0.1245, "casaRatio": 0.2181753594,
                "cir": -0.3232261322, "marketCap": 122252427056200.0,
                "equityToLiabilities": 0.1014889219,
                "currentRatio": 0.0, "quickRatio": 0.0, "cashRatio": 0.0,
                "assetTurnover": 0.0, "debtToEquity": 0.0,
            },
            {
                "yearReport": 2013, "lengthReport": 1, "ticker": "ACB",
                "BSA1": 0, "BSA2": 5806521000000, "eps": 333.179,
                "revenue": 4183337000000, "netProfit": 307030000000,
                "pe": 36.81, "pb": 1.1337, "roe": 0.030586, "roa": 0.001935,
            },
            {
                "yearReport": 2025, "lengthReport": 3, "ticker": "ACB",
                "pe": 8.5, "pb": 1.4, "roe": 0.18,
            },
        ],
    }

    fpt_ci = {
        "symbol": "FPT",
        "exchange": "HOSE",
        "industry": "Công nghệ Thông tin",
        "company_type": None,
        "established_year": None,
        "employees": None,
        "market_cap": 126059526954000.0,
        "current_price": 74000.0,
        "outstanding_shares": 1703507121,
        "company_profile": "<div>FPT profile</div>",
        "website": None,
        "shareholders": [
            {"name": "SCIC", "percentage": 0.0567},
        ],
        "officers": [
            {"name": "Trương Gia Bình", "position": "Chủ tịch HĐQT", "percentage": 0.0689},
        ],
    }

    fpt_fr = {
        "ticker": "FPT",
        "updated_at": "2026-05-30T12:10:00.000000+00:00",
        "count": 1,
        "ratios": [
            {
                "yearReport": 2025, "lengthReport": 5, "ticker": "FPT",
                "pe": 13.0086870277, "pb": 3.3434885255, "ps": 1.7396404964,
                "roe": 0.2829368545, "roa": 0.1170976805, "roic": 0.1695199693,
                "grossMargin": 0.369240998, "afterTaxProfitMargin": 0.1602037778,
                "currentRatio": 1.400061121, "debtToEquity": 1.0147643215,
                "cashCycle": 117.9190455063,
                "npl": 0.0, "car": 0.0, "casaRatio": 0.0,
            },
        ],
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ACB/company_info.json", json.dumps(acb_ci))
        zf.writestr("ACB/financial_ratios.json", json.dumps(acb_fr))
        zf.writestr("FPT/company_info.json", json.dumps(fpt_ci))
        zf.writestr("FPT/financial_ratios.json", json.dumps(fpt_fr))
        zf.writestr("VNINDEX/company_info.json", "{}")
        zf.writestr("VNINDEX/financial_ratios.json", "{}")
    zip_bytes = buf.getvalue()

    responses.get(f"{_BASE}/fundamental/vn.zip", body=zip_bytes)
    responses.head(
        f"{_BASE}/fundamental/vn.zip",
        headers={"x-amz-meta-content-hash": "vn-zip-hash-001"},
    )

    yield

    responses.stop()
    responses.reset()
