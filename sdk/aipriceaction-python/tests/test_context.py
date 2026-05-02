import re

import pytest
import responses

from aipriceaction import AIContextBuilder


@pytest.fixture
def mock_s3():
    """Mock S3 archive with responses library."""
    responses.start()

    # meta/tickers.json
    tickers = [
        {"source": "vn", "ticker": "VCB", "name": "Vietcombank", "group": "BANK"},
        {"source": "vn", "ticker": "FPT", "name": "FPT Corp", "group": "TECH"},
        {"source": "vn", "ticker": "VNINDEX", "name": "VN Index", "group": "INDEX"},
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

    # FPT daily data
    responses.get(
        "http://localhost:9000/aipriceaction-archive/ohlcv/vn/FPT/1D/FPT-1D-2025-04-29.csv",
        body="2025-04-29 00:00:00,145000.0,146500.0,144000.0,146000.0,987654",
    )

    # VNINDEX daily data (for reference ticker tests)
    responses.get(
        "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VNINDEX/1D/VNINDEX-1D-2025-04-28.csv",
        body="2025-04-28 00:00:00,1280.5,1290.0,1275.0,1288.0,500000000",
    )
    responses.get(
        "http://localhost:9000/aipriceaction-archive/ohlcv/vn/VNINDEX/1D/VNINDEX-1D-2025-04-29.csv",
        body="2025-04-29 00:00:00,1288.0,1295.0,1285.0,1292.5,550000000",
    )

    # Catch-all: return 404 for any unmocked CSV URLs
    responses.get(
        re.compile(r"http://localhost:9000/aipriceaction-archive/ohlcv/.*\.csv"),
        status=404,
    )

    yield

    responses.stop()
    responses.reset()


@pytest.fixture
def builder(tmp_path):
    return AIContextBuilder(
        base_url="http://localhost:9000/aipriceaction-archive",
        cache_dir=str(tmp_path),
    )


class TestQuestions:
    def test_single_mode(self, builder):
        qs = builder.questions("single")
        assert isinstance(qs, list)
        assert len(qs) > 0
        for q in qs:
            assert "title" in q
            assert "snippet" in q
            assert "question" in q

    def test_multi_mode(self, builder):
        qs = builder.questions("multi")
        assert isinstance(qs, list)
        assert len(qs) > 0

    def test_default_mode(self, builder):
        qs = builder.questions()
        # Default mode is "multi"
        assert len(qs) > 0


class TestBuildNoData:
    def test_build_no_args(self, builder):
        """builder.build() with no args returns system prompt + disclaimer only."""
        context = builder.build()
        assert "System Prompt" in context or "system prompt" in context.lower()
        assert "Disclaimer" in context or "disclaimer" in context.lower()
        # No market data section
        assert "=== Market Data ===" not in context


class TestBuildSingleTicker:
    def test_single_ticker(self, mock_s3, builder):
        context = builder.build(
            ticker="VCB", interval="1D",
            start_date="2025-04-28", end_date="2025-04-29",
        )
        assert "=== Market Data ===" in context
        assert "VCB" in context
        assert "=== Ticker Info ===" in context
        assert "Vietcombank" in context
        assert "Primary Ticker" in context

    def test_single_ticker_with_question(self, mock_s3, builder):
        context = builder.build(
            ticker="VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
            question="What is the trend?",
        )
        assert "=== Question ===" in context
        assert "What is the trend?" in context

    def test_single_ticker_with_template_question(self, mock_s3, builder):
        qs = builder.questions("single")
        context = builder.build(
            ticker="VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
            question=qs[0]["question"],
        )
        assert "=== Question ===" in context
        assert "VCB" in context


class TestBuildMultiTicker:
    def test_multi_ticker(self, mock_s3, builder):
        context = builder.build(
            tickers=["VCB", "FPT"], interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
        )
        assert "=== Market Data ===" in context
        assert "VCB" in context
        assert "FPT" in context
        assert "=== Ticker Info ===" in context

    def test_multi_no_primary_label(self, mock_s3, builder):
        """Multi-ticker mode should not have 'Primary Ticker' label."""
        context = builder.build(
            tickers=["VCB", "FPT"], interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
        )
        assert "Primary Ticker" not in context


class TestBuildReferenceTicker:
    def test_with_reference_ticker(self, mock_s3, builder):
        context = builder.build(
            ticker="VCB", interval="1D",
            start_date="2025-04-28", end_date="2025-04-29",
            reference_ticker="VNINDEX",
        )
        assert "=== Ticker Info ===" in context
        assert "Reference Ticker" in context
        assert "VNINDEX" in context
        assert "VCB" in context


class TestBuildErrors:
    def test_ticker_and_tickers_mutually_exclusive(self, mock_s3, builder):
        with pytest.raises(ValueError, match="Use either"):
            builder.build(
                ticker="VCB", tickers=["FPT"],
                start_date="2025-04-29", end_date="2025-04-29",
            )


class TestDfToRecords:
    def test_converts_dataframe(self):
        import pandas as pd

        df = pd.DataFrame({
            "time": ["2025-01-01", "2025-01-02"],
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "volume": [1000, 2000],
            "symbol": ["TEST", "TEST"],
            "ma10": [100.5, 101.5],
        })
        records = AIContextBuilder._df_to_records(df)
        assert "TEST" in records
        assert len(records["TEST"]) == 2
        assert records["TEST"][0].close == 101.0
        assert records["TEST"][0].ma10 == 100.5

    def test_handles_nan_optional_fields(self):
        import pandas as pd

        df = pd.DataFrame({
            "time": ["2025-01-01"],
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "volume": [1000],
            "symbol": ["TEST"],
            "ma10": [float("nan")],
        })
        records = AIContextBuilder._df_to_records(df)
        assert records["TEST"][0].ma10 is None

    def test_handles_empty_dataframe(self):
        import pandas as pd

        df = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume", "symbol"])
        records = AIContextBuilder._df_to_records(df)
        assert records == {}
