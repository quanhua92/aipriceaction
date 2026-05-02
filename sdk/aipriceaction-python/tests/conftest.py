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

    # content-hash HEAD
    responses.head(
        "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-29.csv",
        headers={"x-amz-meta-content-hash": "abc123"},
    )
    responses.head(
        "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VCB/1D/VCB-1D-2025-04-30.csv",
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
    )
