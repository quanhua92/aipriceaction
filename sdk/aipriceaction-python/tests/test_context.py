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

    def test_single_ticker_stores_context(self, mock_s3, builder):
        """build() stores context in _last_context for answer() reuse."""
        context = builder.build(
            ticker="VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
        )
        assert builder._last_context == context
        assert "=== Question ===" not in context

    def test_single_ticker_with_template_question(self, mock_s3, builder):
        qs = builder.questions("single")
        context = builder.build(
            ticker="VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
        )
        assert "=== Question ===" not in context
        assert "VCB" in context
        # Template question is available via questions() but not embedded in build()
        assert qs[0]["question"]


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

    def test_multi_includes_vnindex_by_default(self, mock_s3, builder):
        """Multi-ticker mode auto-includes VNINDEX as reference ticker."""
        context = builder.build(
            tickers=["VCB", "FPT"], interval="1D",
            start_date="2025-04-28", end_date="2025-04-29",
        )
        assert "Reference Ticker" in context
        assert "VNINDEX" in context

    def test_multi_reference_ticker_none(self, mock_s3, builder):
        """Multi-ticker with reference_ticker=None omits VNINDEX."""
        context = builder.build(
            tickers=["VCB", "FPT"], interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
            reference_ticker=None,
        )
        assert "Reference Ticker" not in context
        assert "VNINDEX" not in context


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


class TestAnswer:
    def test_answer_requires_build_first(self, builder):
        """answer() raises ValueError if build() was not called."""
        with pytest.raises(ValueError, match="Call build"):
            builder.answer("test question")

    def test_answer_returns_response(self, mock_s3, builder):
        """answer() returns the mocked LLM response content."""
        from unittest.mock import MagicMock

        mock_response = MagicMock()
        mock_response.content = "The trend is bullish."
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        builder.build(
            ticker="VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
        )
        result = builder.answer("What is the trend?", llm=mock_llm)
        assert result == "The trend is bullish."
        mock_llm.invoke.assert_called_once()

    def test_answer_passes_question_in_context(self, mock_s3, builder):
        """answer() appends === Question === section to context."""
        from unittest.mock import MagicMock

        mock_response = MagicMock()
        mock_response.content = "response"
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        builder.build(
            ticker="VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
        )
        builder.answer("What is the trend?", llm=mock_llm)

        # Verify the context passed to llm.invoke() contains the question
        call_arg = mock_llm.invoke.call_args[0][0]
        assert "=== Question ===" in call_arg
        assert "What is the trend?" in call_arg

    def test_answer_custom_llm(self, mock_s3, builder, monkeypatch):
        """Custom LLM is used instead of default ChatOpenAI."""
        from unittest.mock import MagicMock, patch

        mock_response = MagicMock()
        mock_response.content = "custom response"
        custom_llm = MagicMock()
        custom_llm.invoke.return_value = mock_response

        # Patch ChatOpenAI at its import location inside _get_default_llm
        with patch("langchain_openai.ChatOpenAI") as mock_chat:
            builder.build(
                ticker="VCB", interval="1D",
                start_date="2025-04-29", end_date="2025-04-29",
            )
            result = builder.answer("test", llm=custom_llm)

        assert result == "custom response"
        mock_chat.assert_not_called()

    def test_answer_passes_history(self, mock_s3, builder):
        """answer() includes previous responses as history sections."""
        from unittest.mock import MagicMock

        mock_response = MagicMock()
        mock_response.content = "response"
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        builder.build(
            ticker="VCB", interval="1D",
            start_date="2025-04-29", end_date="2025-04-29",
        )
        builder.answer(
            "New question?",
            history=["First answer was bullish.", "Second answer was bearish."],
            llm=mock_llm,
        )

        call_arg = mock_llm.invoke.call_args[0][0]
        assert "=== Previous Response 1 ===" in call_arg
        assert "First answer was bullish." in call_arg
        assert "=== Previous Response 2 ===" in call_arg
        assert "Second answer was bearish." in call_arg
        assert "=== Question ===" in call_arg
        assert "New question?" in call_arg
