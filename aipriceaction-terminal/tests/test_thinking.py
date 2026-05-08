"""Tests for thinking/reasoning token detection and rendering."""

from unittest.mock import MagicMock, patch

import pytest

from aipriceaction_terminal.agents.agent import OpenRouterChatOpenAI
from aipriceaction_terminal.agents.callbacks import (
    StreamCallbackHandler,
    StreamEvent,
    StreamEventType,
    _extract_reasoning_content,
)


class TestExtractReasoningContent:
    """Test _extract_reasoning_content with different provider formats."""

    def test_additional_kwargs_reasoning_content(self):
        """OpenRouter, DeepSeek, XAI, Groq store reasoning in additional_kwargs."""
        msg = MagicMock()
        msg.additional_kwargs = {"reasoning_content": "Let me think about this..."}
        assert _extract_reasoning_content(msg) == "Let me think about this..."

    def test_additional_kwargs_empty_reasoning(self):
        """Empty reasoning_content should return empty string."""
        msg = MagicMock()
        msg.additional_kwargs = {"reasoning_content": ""}
        assert _extract_reasoning_content(msg) == ""

    def test_content_blocks_dict_reasoning(self):
        """Anthropic/OpenAI use content_blocks with type='reasoning' dict."""
        block = {"type": "reasoning", "reasoning": "Analyzing the data..."}
        msg = MagicMock()
        msg.additional_kwargs = {}
        msg.content_blocks = [block]
        assert _extract_reasoning_content(msg) == "Analyzing the data..."

    def test_content_blocks_object_reasoning(self):
        """Content blocks may be objects with .type and .reasoning attrs."""
        block = MagicMock()
        block.type = "reasoning"
        block.reasoning = "Step by step reasoning..."
        msg = MagicMock()
        msg.additional_kwargs = {}
        msg.content_blocks = [block]
        assert _extract_reasoning_content(msg) == "Step by step reasoning..."

    def test_no_reasoning(self):
        """Regular AIMessageChunk without reasoning returns empty string."""
        msg = MagicMock()
        msg.additional_kwargs = {}
        msg.content_blocks = []
        assert _extract_reasoning_content(msg) == ""

    def test_additional_kwargs_missing(self):
        """Message without additional_kwargs returns empty string."""
        msg = MagicMock(spec=[])  # minimal mock, no additional_kwargs
        msg.content_blocks = []
        assert _extract_reasoning_content(msg) == ""

    def test_additional_kwargs_none(self):
        """Message with additional_kwargs=None returns empty string."""
        msg = MagicMock()
        msg.additional_kwargs = None
        msg.content_blocks = []
        assert _extract_reasoning_content(msg) == ""


class TestStreamCallbackHandlerThinking:
    """Test that the callback handler emits THINKING events."""

    def test_aimessagechunk_with_reasoning_content(self):
        """AIMessageChunk with reasoning_content emits THINKING event."""
        msg = MagicMock()
        msg.additional_kwargs = {"reasoning_content": "thinking here"}
        msg.content = ""
        msg.tool_calls = None
        msg.__class__.__name__ = "AIMessageChunk"

        handler = StreamCallbackHandler()
        events = handler._process_message(msg)

        assert len(events) == 1
        assert events[0].type == StreamEventType.THINKING
        assert events[0].content == "thinking here"

    def test_aimessagechunk_with_reasoning_and_content(self):
        """AIMessageChunk with both reasoning and content emits both events."""
        msg = MagicMock()
        msg.additional_kwargs = {"reasoning_content": "hmm"}
        msg.content = "Here is my answer"
        msg.tool_calls = None
        msg.__class__.__name__ = "AIMessageChunk"

        handler = StreamCallbackHandler()
        events = handler._process_message(msg)

        assert len(events) == 2
        assert events[0].type == StreamEventType.THINKING
        assert events[1].type == StreamEventType.TOKEN

    def test_aimessagechunk_content_only_no_thinking(self):
        """Regular AIMessageChunk without reasoning only emits TOKEN."""
        msg = MagicMock()
        msg.additional_kwargs = {}
        msg.content_blocks = []
        msg.content = "Hello"
        msg.tool_calls = None
        msg.__class__.__name__ = "AIMessageChunk"

        handler = StreamCallbackHandler()
        events = handler._process_message(msg)

        assert len(events) == 1
        assert events[0].type == StreamEventType.TOKEN
        assert events[0].content == "Hello"

    def test_process_agent_event_tuple_with_reasoning(self):
        """Full pipeline: stream_mode='messages' tuple with reasoning."""
        msg = MagicMock()
        msg.additional_kwargs = {"reasoning_content": "deep thought"}
        msg.content = "answer"
        msg.tool_calls = None
        msg.__class__.__name__ = "AIMessageChunk"

        handler = StreamCallbackHandler()
        # Simulate stream_mode="messages" tuple
        events = handler.process_agent_event((msg, {}))

        assert len(events) == 2
        assert events[0].type == StreamEventType.THINKING
        assert events[0].content == "deep thought"
        assert events[1].type == StreamEventType.TOKEN
        assert events[1].content == "answer"

    def test_process_agent_event_updates_with_reasoning(self):
        """Full pipeline: stream_mode='updates' dict with reasoning."""
        msg = MagicMock()
        msg.additional_kwargs = {"reasoning_content": "analyzing"}
        msg.content = "result"
        msg.tool_calls = None
        msg.__class__.__name__ = "AIMessageChunk"

        handler = StreamCallbackHandler()
        # Simulate stream_mode="updates" dict
        events = handler.process_agent_event({"agent": {"messages": [msg]}})

        assert len(events) == 2
        assert events[0].type == StreamEventType.THINKING
        assert events[1].type == StreamEventType.TOKEN


class TestOpenRouterChatOpenAI:
    """Test the ReasoningChatOpenAI subclass that injects reasoning from raw delta."""

    def test_injects_reasoning_into_additional_kwargs(self):
        """The subclass should move delta.reasoning into additional_kwargs."""
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
        from langchain_core.messages import AIMessageChunk

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
