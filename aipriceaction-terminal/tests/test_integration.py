"""Integration tests that call the real OpenRouter API via AgentSession.

Run with: uv run pytest -m integration --integration

These tests validate the full pipeline:
    settings → AgentConfig → AgentSession → stream → StreamEvents

Assertions are deliberately loose — model behavior varies, so we only verify:
- Stream produces valid StreamEvents without crashes
- Stream ends with DONE
- On success (no fatal ERROR): at least one TOKEN event
- Market queries produce at least one TOOL_CALL_START (when not rate-limited)

Rate limiting is expected in CI/local runs, so tests tolerate transient errors.
"""

from aipriceaction.settings import settings
from aipriceaction_terminal.agents import AgentConfig, AgentSession
from aipriceaction_terminal.agents.callbacks import StreamEvent, StreamEventType

import pytest


def _has_api_key() -> bool:
    return bool(settings.openai_api_key)


def _classify_events(events: list[StreamEvent]) -> dict:
    """Classify collected events into categories for assertion."""
    event_types = [e.type for e in events]
    has_done = StreamEventType.DONE in event_types
    tokens = [e for e in events if e.type == StreamEventType.TOKEN]
    tool_starts = [e for e in events if e.type == StreamEventType.TOOL_CALL_START]
    errors = [e for e in events if e.type == StreamEventType.ERROR]
    return {
        "has_done": has_done,
        "tokens": tokens,
        "tool_starts": tool_starts,
        "errors": errors,
        "all_rate_limited": len(errors) > 0 and len(tokens) == 0,
    }


@pytest.mark.integration
async def test_vnindex_query_produces_tool_call_and_answer():
    """'Check VNINDEX today' should trigger at least one tool call and produce tokens."""
    if not _has_api_key():
        pytest.skip("No OPENAI_API_KEY configured in settings")

    config = AgentConfig()
    session = AgentSession(config)

    events: list[StreamEvent] = []
    async for event in session.stream("Check VNINDEX today"):
        events.append(event)

    info = _classify_events(events)

    # Stream must always end with DONE
    assert info["has_done"], f"Stream did not end with DONE. Events: {[e.type for e in events]}"

    # If rate-limited, skip content assertions
    if info["all_rate_limited"]:
        pytest.skip("Rate-limited by API — all retries exhausted")

    # Must produce at least some token output
    assert len(info["tokens"]) > 0

    # Market query should trigger at least one tool call
    assert len(info["tool_starts"]) >= 1


@pytest.mark.integration
async def test_hello_query_produces_tokens():
    """'Hello' should produce TOKEN events. May or may not call tools."""
    if not _has_api_key():
        pytest.skip("No OPENAI_API_KEY configured in settings")

    config = AgentConfig()
    session = AgentSession(config)

    events: list[StreamEvent] = []
    async for event in session.stream("Hello"):
        events.append(event)

    info = _classify_events(events)

    # Stream must always end with DONE
    assert info["has_done"], f"Stream did not end with DONE. Events: {[e.type for e in events]}"

    # If rate-limited, skip content assertions
    if info["all_rate_limited"]:
        pytest.skip("Rate-limited by API — all retries exhausted")

    # Must produce at least some token output
    assert len(info["tokens"]) > 0


@pytest.mark.integration
async def test_stream_event_metadata_is_valid():
    """All StreamEvents should have valid types and string content."""
    if not _has_api_key():
        pytest.skip("No OPENAI_API_KEY configured in settings")

    config = AgentConfig()
    session = AgentSession(config)

    events: list[StreamEvent] = []
    async for event in session.stream("What is the price of FPT?"):
        events.append(event)

    for event in events:
        assert isinstance(event.type, StreamEventType)
        assert isinstance(event.content, str)
        assert isinstance(event.metadata, dict)
