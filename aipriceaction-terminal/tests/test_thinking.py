"""Tests for thinking/reasoning token detection and rendering.

Uses real AIMessageChunk objects (not MagicMock) to match actual LangChain
message types produced by OpenRouter streaming.
"""

from langchain_core.messages import AIMessageChunk

from aipriceaction_terminal.agents.agent import OpenRouterChatOpenAI
from aipriceaction_terminal.agents.callbacks import (
    StreamCallbackHandler,
    StreamEvent,
    StreamEventType,
    _extract_reasoning_content,
)
from openrouter_responses import (
    SIMPLE_TEXT_EVENTS,
    THINKING_EVENTS,
)


class TestExtractReasoningContent:
    """Test _extract_reasoning_content with different provider formats."""

    def test_additional_kwargs_reasoning_content(self):
        """OpenRouter, DeepSeek, XAI, Groq store reasoning in additional_kwargs."""
        msg = AIMessageChunk(
            content="",
            additional_kwargs={"reasoning_content": "Let me think about this..."},
        )
        assert _extract_reasoning_content(msg) == "Let me think about this..."

    def test_additional_kwargs_empty_reasoning(self):
        """Empty reasoning_content should return empty string."""
        msg = AIMessageChunk(
            content="",
            additional_kwargs={"reasoning_content": ""},
        )
        assert _extract_reasoning_content(msg) == ""

    def test_no_reasoning(self):
        """Regular AIMessageChunk without reasoning returns empty string."""
        msg = AIMessageChunk(content="Hello")
        assert _extract_reasoning_content(msg) == ""

    def test_additional_kwargs_missing(self):
        """AIMessage with no additional_kwargs key returns empty string."""
        msg = AIMessageChunk(content="Hello")
        assert _extract_reasoning_content(msg) == ""


class TestStreamCallbackHandlerThinking:
    """Test that the callback handler emits THINKING events."""

    def test_aimessagechunk_with_reasoning_content(self):
        """AIMessageChunk with reasoning_content emits THINKING event."""
        msg = AIMessageChunk(
            content="",
            additional_kwargs={"reasoning_content": "thinking here"},
        )

        handler = StreamCallbackHandler()
        events = handler._process_message(msg)

        assert len(events) == 1
        assert events[0].type == StreamEventType.THINKING
        assert events[0].content == "thinking here"

    def test_aimessagechunk_with_reasoning_and_content(self):
        """AIMessageChunk with both reasoning and content emits both events."""
        msg = AIMessageChunk(
            content="Here is my answer",
            additional_kwargs={"reasoning_content": "hmm"},
        )

        handler = StreamCallbackHandler()
        events = handler._process_message(msg)

        assert len(events) == 2
        assert events[0].type == StreamEventType.THINKING
        assert events[1].type == StreamEventType.TOKEN

    def test_aimessagechunk_content_only_no_thinking(self):
        """Regular AIMessageChunk without reasoning only emits TOKEN."""
        msg = AIMessageChunk(content="Hello")

        handler = StreamCallbackHandler()
        events = handler._process_message(msg)

        assert len(events) == 1
        assert events[0].type == StreamEventType.TOKEN
        assert events[0].content == "Hello"

    def test_process_agent_event_tuple_with_reasoning(self):
        """Full pipeline: stream_mode='messages' tuple with reasoning."""
        msg = AIMessageChunk(
            content="answer",
            additional_kwargs={"reasoning_content": "deep thought"},
        )

        handler = StreamCallbackHandler()
        events = handler.process_agent_event((msg, {}))

        assert len(events) == 2
        assert events[0].type == StreamEventType.THINKING
        assert events[0].content == "deep thought"
        assert events[1].type == StreamEventType.TOKEN
        assert events[1].content == "answer"

    def test_process_agent_event_updates_with_reasoning(self):
        """Full pipeline: stream_mode='updates' dict with reasoning."""
        msg = AIMessageChunk(
            content="result",
            additional_kwargs={"reasoning_content": "analyzing"},
        )

        handler = StreamCallbackHandler()
        events = handler.process_agent_event({"agent": {"messages": [msg]}})

        assert len(events) == 2
        assert events[0].type == StreamEventType.THINKING
        assert events[1].type == StreamEventType.TOKEN

    def test_thinking_fixture_events(self):
        """Verify THINKING fixture produces THINKING then TOKEN events."""
        handler = StreamCallbackHandler()
        all_events: list[StreamEvent] = []
        for event_tuple in THINKING_EVENTS:
            all_events.extend(handler.process_agent_event(event_tuple))

        thinking = [e for e in all_events if e.type == StreamEventType.THINKING]
        tokens = [e for e in all_events if e.type == StreamEventType.TOKEN]

        assert len(thinking) == 2
        assert thinking[0].content == "The user is asking about "
        assert thinking[1].content == "VNINDEX performance."
        assert len(tokens) == 2
        assert tokens[0].content == "VNINDEX "
        assert tokens[1].content == "closed at 1,250.5 today."

    def test_simple_text_fixture_no_thinking(self):
        """Simple text fixture should produce only TOKEN events."""
        handler = StreamCallbackHandler()
        all_events: list[StreamEvent] = []
        for event_tuple in SIMPLE_TEXT_EVENTS:
            all_events.extend(handler.process_agent_event(event_tuple))

        thinking = [e for e in all_events if e.type == StreamEventType.THINKING]
        tokens = [e for e in all_events if e.type == StreamEventType.TOKEN]

        assert thinking == []
        assert len(tokens) == 3
        assert tokens[0].content == "Hello"
        assert tokens[1].content == " there"
        assert tokens[2].content == "!"


class TestOpenRouterChatOpenAI:
    """Test the OpenRouterChatOpenAI subclass that injects reasoning from raw delta."""

    def test_injects_reasoning_into_additional_kwargs(self):
        """The subclass should move delta.reasoning into additional_kwargs."""
        from unittest.mock import MagicMock

        llm = OpenRouterChatOpenAI(
            api_key="test",
            base_url="http://localhost",
            model="test-model",
            extra_body={"reasoning": {"enabled": True}},
        )

        # Simulate a raw OpenRouter streaming chunk with reasoning
        raw_chunk = {
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "reasoning": "We need to count the items...",
                        "content": "",
                    }
                }
            ]
        }

        result = llm._convert_chunk_to_generation_chunk(
            raw_chunk, MagicMock, None
        )

        assert result is not None
        assert result.message.additional_kwargs.get("reasoning_content") == "We need to count the items..."

    def test_no_reasoning_passthrough(self):
        """Chunk without reasoning field should be unchanged."""
        from unittest.mock import MagicMock

        llm = OpenRouterChatOpenAI(
            api_key="test",
            base_url="http://localhost",
            model="test-model",
            extra_body={"reasoning": {"enabled": True}},
        )

        raw_chunk = {
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "content": "Hello",
                    }
                }
            ]
        }

        result = llm._convert_chunk_to_generation_chunk(
            raw_chunk, MagicMock, None
        )

        assert result is not None
        assert "reasoning_content" not in result.message.additional_kwargs
        assert result.message.content == "Hello"

    def test_usage_only_chunk_handled(self):
        """Chunk with no choices (usage-only) should be handled by parent."""
        llm = OpenRouterChatOpenAI(
            api_key="test",
            base_url="http://localhost",
            model="test-model",
        )

        raw_chunk = {"choices": [], "usage": {"total_tokens": 10}}
        result = llm._convert_chunk_to_generation_chunk(raw_chunk, AIMessageChunk, None)
        assert result is not None  # usage-only chunk is not None
        assert result.message.content == ""

    def test_extra_body_configured(self):
        """extra_body should be set on the instance."""
        llm = OpenRouterChatOpenAI(
            api_key="test",
            base_url="http://localhost",
            model="test-model",
            extra_body={"reasoning": {"enabled": True}},
        )
        assert llm.extra_body == {"reasoning": {"enabled": True}}
