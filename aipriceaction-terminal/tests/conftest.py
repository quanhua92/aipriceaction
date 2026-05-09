"""Shared fixtures for aipriceaction-terminal tests."""

from unittest.mock import MagicMock

import pytest
from textual.widgets import RichLog


# ---------------------------------------------------------------------------
# Pytest configuration: markers and CLI options
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="Run integration tests that call the real OpenRouter API.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: marks tests that call the real OpenRouter API (requires --integration)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--integration"):
        return
    skip_integration = pytest.mark.skip(reason="needs --integration option to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_builder():
    """Return a mock AIContextBuilder with a canned build response."""
    builder = MagicMock()
    builder.build.return_value = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
    builder.questions.return_value = [
        {
            "title": "Trading Opportunity",
            "snippet": "Identify opportunities",
            "question": "Analyze {ticker} trading opportunities.",
        },
    ]
    return builder


@pytest.fixture()
def mock_client():
    """Return a mock AIPriceAction client with empty ticker list."""
    client = MagicMock()
    client.get_tickers.return_value = []
    return client


@pytest.fixture()
def mock_agent():
    """Return a mock AgentSession with a simple TOKEN + DONE stream."""
    agent = MagicMock()

    async def _mock_stream(message):
        from aipriceaction_terminal.agents.callbacks import StreamEvent, StreamEventType
        yield StreamEvent(type=StreamEventType.TOKEN, content="Hello from agent.")
        yield StreamEvent(type=StreamEventType.DONE)

    agent.stream = _mock_stream
    agent.clear_history = MagicMock()
    return agent


@pytest.fixture()
def mock_agent_with_tool_call():
    """Return a mock AgentSession that simulates a tool call cycle."""
    agent = MagicMock()

    async def _mock_stream(message):
        from aipriceaction_terminal.agents.callbacks import StreamEvent, StreamEventType
        yield StreamEvent(type=StreamEventType.THINKING, content="Need to fetch data...")
        yield StreamEvent(
            type=StreamEventType.TOOL_CALL_START,
            content='get_ohlcv_data({"ticker": "VCB", "interval": "1D"})',
        )
        yield StreamEvent(type=StreamEventType.TOOL_RESULT, content="[1,234 chars]")
        yield StreamEvent(type=StreamEventType.TOKEN, content="VCB is trending up at 95.0.")
        yield StreamEvent(type=StreamEventType.DONE)

    agent.stream = _mock_stream
    agent.clear_history = MagicMock()
    return agent


@pytest.fixture()
def sample_ticker_options():
    """Return sample ticker options for TickerSelect."""
    return [
        ("[CRYPTO] BTCUSDT - Bitcoin", "BTCUSDT"),
        ("[VN] FPT - FPT Corporation", "FPT"),
        ("[VN] VCB - Vietcombank", "VCB"),
        ("[VN] VNINDEX", "VNINDEX"),
    ]


def richlog_text(log: RichLog) -> str:
    """Extract plain text from a RichLog widget (strips Rich markup)."""
    parts = []
    for i in range(len(log.lines)):
        try:
            strip = log.lines[i]
            text = "".join(seg.text for seg in strip._segments)
            parts.append(text)
        except (AttributeError, IndexError):
            pass
    return "\n".join(parts)
