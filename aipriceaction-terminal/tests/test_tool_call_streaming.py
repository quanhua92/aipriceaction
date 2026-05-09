"""Tests for tool call buffering during streaming (tool_call_chunks accumulation).

Uses real AIMessageChunk objects with actual tool_call_chunks TypedDicts
to match LangChain's real streaming message format from OpenRouter/OpenAI.
"""

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from aipriceaction_terminal.agents.callbacks import (
    StreamCallbackHandler,
    StreamEvent,
    StreamEventType,
)
from openrouter_responses import (
    MULTI_TOOL_CALL_EVENTS,
    SINGLE_TOOL_CALL_EVENTS,
    UPDATES_MODE_TOOL_CALL,
)


class TestToolCallBuffering:
    """Test that streaming tool_call_chunks are buffered and only emitted on ToolMessage."""

    def test_no_events_from_partial_chunks(self):
        """Streaming chunks with tool_call_chunks should NOT emit TOOL_CALL_START."""
        handler = StreamCallbackHandler(show_tool_calls=True)

        chunk = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": "call_1", "name": "get_ohlcv_data", "args": "", "index": 0},
            ],
        )
        events = handler._process_message(chunk)

        tool_events = [e for e in events if e.type == StreamEventType.TOOL_CALL_START]
        assert tool_events == []

    def test_realistic_openai_streaming_single_call(self):
        """Mimic exact OpenAI streaming: id only on first chunk, args split across chunks."""
        handler = StreamCallbackHandler(show_tool_calls=True)

        # Chunk 1: id + name + empty args (OpenAI sends this first)
        chunk1 = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": "call_abc", "name": "get_ohlcv_data", "args": "", "index": 0},
            ],
        )
        handler._process_message(chunk1)

        # Chunk 2: no id, only index + partial args
        chunk2 = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": None, "name": None, "args": '{"ticker":', "index": 0},
            ],
        )
        handler._process_message(chunk2)

        # Chunk 3: no id, only index + rest of args
        chunk3 = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": None, "name": None, "args": ' "VIC"}', "index": 0},
            ],
        )
        handler._process_message(chunk3)

        # Verify accumulated state before ToolMessage
        assert handler._pending_tool_calls["call_abc"]["name"] == "get_ohlcv_data"
        assert handler._pending_tool_calls["call_abc"]["args_str"] == '{"ticker": "VIC"}'

        # ToolMessage triggers emission
        tool_msg = ToolMessage(content="some ohlcv data", tool_call_id="call_abc")
        events = handler._process_message(tool_msg)

        tool_events = [e for e in events if e.type == StreamEventType.TOOL_CALL_START]
        assert len(tool_events) == 1
        assert tool_events[0].content == 'get_ohlcv_data({"ticker": "VIC"})'

        result_events = [e for e in events if e.type == StreamEventType.TOOL_RESULT]
        assert len(result_events) == 1
        assert result_events[0].content == "[15 chars]"

    def test_realistic_multiple_parallel_tool_calls(self):
        """Mimic OpenAI streaming with 2 parallel tool calls using different indices."""
        handler = StreamCallbackHandler(show_tool_calls=True)

        # First chunk: both tool calls start
        chunk1 = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": "call_a", "name": "get_ohlcv_data", "args": "", "index": 0},
                {"id": "call_b", "name": "get_ohlcv_data", "args": "", "index": 1},
            ],
        )
        handler._process_message(chunk1)

        # Args for index 0 and 1 stream in
        chunk2 = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": None, "name": None, "args": '{"ticker": "FPT"}', "index": 0},
                {"id": None, "name": None, "args": '{"ticker": "VCB"}', "index": 1},
            ],
        )
        handler._process_message(chunk2)

        # Both should be accumulated
        assert handler._pending_tool_calls["call_a"]["args_str"] == '{"ticker": "FPT"}'
        assert handler._pending_tool_calls["call_b"]["args_str"] == '{"ticker": "VCB"}'

        # ToolMessages arrive
        tool_msg_a = ToolMessage(content="fpt data", tool_call_id="call_a")
        events_a = handler._process_message(tool_msg_a)
        assert [e.content for e in events_a if e.type == StreamEventType.TOOL_CALL_START] == [
            'get_ohlcv_data({"ticker": "FPT"})'
        ]

        tool_msg_b = ToolMessage(content="vcb data", tool_call_id="call_b")
        events_b = handler._process_message(tool_msg_b)
        assert [e.content for e in events_b if e.type == StreamEventType.TOOL_CALL_START] == [
            'get_ohlcv_data({"ticker": "VCB"})'
        ]

        assert handler._pending_tool_calls == {}

    def test_realistic_11_parallel_calls(self):
        """Mimic the real scenario: 11 parallel get_ohlcv_data calls for VN30 stocks."""
        handler = StreamCallbackHandler(show_tool_calls=True)

        tickers = ["VIC", "VCB", "FPT", "VNM", "HSG", "MWG", "HPG", "SAB", "VHM", "TCB", "BID"]

        # Chunk 1: all 11 tool calls start (id + name + empty args)
        chunks_init = [
            {"id": f"call_{i}", "name": "get_ohlcv_data", "args": "", "index": i}
            for i in range(11)
        ]
        handler._process_message(AIMessageChunk(content="", tool_call_chunks=chunks_init))

        # Chunk 2: args stream in for each
        chunks_args = [
            {"id": None, "name": None, "args": f'{{"ticker": "{tickers[i]}"}}', "index": i}
            for i in range(11)
        ]
        handler._process_message(AIMessageChunk(content="", tool_call_chunks=chunks_args))

        # Verify all 11 accumulated correctly
        for i, ticker in enumerate(tickers):
            assert handler._pending_tool_calls[f"call_{i}"]["args_str"] == f'{{"ticker": "{ticker}"}}'

        # ToolMessages arrive
        for i, ticker in enumerate(tickers):
            tool_msg = ToolMessage(content=f"{ticker} data", tool_call_id=f"call_{i}")
            events = handler._process_message(tool_msg)
            tool_events = [e for e in events if e.type == StreamEventType.TOOL_CALL_START]
            assert len(tool_events) == 1
            assert tool_events[0].content == f'get_ohlcv_data({{"ticker": "{ticker}"}})'

        assert handler._pending_tool_calls == {}

    def test_show_tool_calls_false_suppresses_emission(self):
        """When show_tool_calls=False, no TOOL_CALL_START is emitted."""
        handler = StreamCallbackHandler(show_tool_calls=False)

        chunk = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": "call_1", "name": "get_ohlcv_data", "args": '{"ticker": "FPT"}', "index": 0},
            ],
        )
        handler._process_message(chunk)

        tool_msg = ToolMessage(content="data", tool_call_id="call_1")
        events = handler._process_message(tool_msg)

        tool_events = [e for e in events if e.type == StreamEventType.TOOL_CALL_START]
        assert tool_events == []

    def test_unknown_tool_call_id_ignored(self):
        """ToolMessage for an unknown tool_call_id is silently ignored."""
        handler = StreamCallbackHandler(show_tool_calls=True)

        tool_msg = ToolMessage(content="data", tool_call_id="unknown_id")
        events = handler._process_message(tool_msg)

        tool_events = [e for e in events if e.type == StreamEventType.TOOL_CALL_START]
        assert tool_events == []

    def test_chunk_without_id_and_no_prior_index_mapping_skipped(self):
        """tool_call_chunks with no id and no prior index mapping are skipped."""
        handler = StreamCallbackHandler(show_tool_calls=True)

        chunk = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": None, "name": None, "args": '{"ticker": "FPT"}', "index": 5},
            ],
        )
        handler._process_message(chunk)

        # No mapping for index 5 was established, so nothing buffered
        assert handler._pending_tool_calls == {}

    def test_malformed_args_json_falls_back_gracefully(self):
        """If accumulated args_str is invalid JSON, falls back to empty dict."""
        handler = StreamCallbackHandler(show_tool_calls=True)

        chunk = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": "call_1", "name": "get_ohlcv_data", "args": "not valid json{", "index": 0},
            ],
        )
        handler._process_message(chunk)

        tool_msg = ToolMessage(content="data", tool_call_id="call_1")
        events = handler._process_message(tool_msg)

        tool_events = [e for e in events if e.type == StreamEventType.TOOL_CALL_START]
        assert len(tool_events) == 1
        assert tool_events[0].content == "get_ohlcv_data({})"

    def test_empty_args_string_yields_empty_dict(self):
        """Empty args_str (no args streamed at all) parses as empty dict."""
        handler = StreamCallbackHandler(show_tool_calls=True)

        chunk = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": "call_1", "name": "get_ticker_list", "args": "", "index": 0},
            ],
        )
        handler._process_message(chunk)

        tool_msg = ToolMessage(content="ticker list", tool_call_id="call_1")
        events = handler._process_message(tool_msg)

        tool_events = [e for e in events if e.type == StreamEventType.TOOL_CALL_START]
        assert len(tool_events) == 1
        assert tool_events[0].content == "get_ticker_list({})"


class TestToolCallFallbackWithToolCalls:
    """Test fallback path: non-streaming AIMessage with tool_calls (stream_mode='updates')."""

    def test_aimessage_with_tool_calls_fallback(self):
        """AIMessage without tool_call_chunks falls back to tool_calls."""
        handler = StreamCallbackHandler(show_tool_calls=True)

        msg = AIMessage(
            content="",
            tool_calls=[
                {"id": "call_1", "name": "get_ohlcv_data", "args": {"ticker": "FPT", "interval": "1D"}},
            ],
        )

        handler._process_message(msg)

        tool_msg = ToolMessage(content="ohlcv data", tool_call_id="call_1")
        events = handler._process_message(tool_msg)

        tool_events = [e for e in events if e.type == StreamEventType.TOOL_CALL_START]
        assert len(tool_events) == 1
        assert '"ticker": "FPT"' in tool_events[0].content
        assert '"interval": "1D"' in tool_events[0].content


class TestToolCallStreamingFullPipeline:
    """Test the full stream_mode='messages' pipeline (tuple events)."""

    def test_pipeline_with_realistic_streaming(self):
        """Full pipeline mimicking real OpenAI streaming behavior."""
        handler = StreamCallbackHandler(show_tool_calls=True)

        # Chunk 1: id + name
        chunk1 = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": "call_1", "name": "get_ohlcv_data", "args": "", "index": 0},
            ],
        )
        events = handler.process_agent_event((chunk1, {"langgraph_node": "agent"}))
        assert all(e.type != StreamEventType.TOOL_CALL_START for e in events)

        # Chunk 2: partial args via index
        chunk2 = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": None, "name": None, "args": '{"ticker":"VIC"}', "index": 0},
            ],
        )
        events = handler.process_agent_event((chunk2, {"langgraph_node": "agent"}))
        assert all(e.type != StreamEventType.TOOL_CALL_START for e in events)

        # ToolMessage
        tool_msg = ToolMessage(content="vic data here", tool_call_id="call_1")
        events = handler.process_agent_event((tool_msg, {"langgraph_node": "tools"}))

        tool_events = [e for e in events if e.type == StreamEventType.TOOL_CALL_START]
        assert len(tool_events) == 1
        assert tool_events[0].content == 'get_ohlcv_data({"ticker": "VIC"})'

    def test_pipeline_no_duplicate_emissions_with_realistic_chunks(self):
        """Realistic streaming: first chunk has id, subsequent only index."""
        handler = StreamCallbackHandler(show_tool_calls=True)

        # Chunk 1: id + name + empty args
        chunk1 = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": "call_1", "name": "get_ohlcv_data", "args": "", "index": 0},
            ],
        )
        handler.process_agent_event((chunk1, {}))

        # Chunks 2-3: only index + partial args (no id)
        chunk2 = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": None, "name": None, "args": '{"ticker"', "index": 0},
            ],
        )
        handler.process_agent_event((chunk2, {}))

        chunk3 = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": None, "name": None, "args": ': "VCB"}', "index": 0},
            ],
        )
        handler.process_agent_event((chunk3, {}))

        # One ToolMessage
        tool_msg = ToolMessage(content="data", tool_call_id="call_1")
        events = handler.process_agent_event((tool_msg, {}))

        tool_events = [e for e in events if e.type == StreamEventType.TOOL_CALL_START]
        assert len(tool_events) == 1
        assert tool_events[0].content == 'get_ohlcv_data({"ticker": "VCB"})'


class TestFixtures:
    """Verify the fixture data itself processes correctly through the handler."""

    def test_single_tool_call_fixture(self):
        """SINGLE_TOOL_CALL_EVENTS produces TOOL_CALL_START + TOOL_RESULT + TOKEN events."""
        handler = StreamCallbackHandler(show_tool_calls=True)
        all_events: list[StreamEvent] = []
        for event_tuple in SINGLE_TOOL_CALL_EVENTS:
            all_events.extend(handler.process_agent_event(event_tuple))

        tool_starts = [e for e in all_events if e.type == StreamEventType.TOOL_CALL_START]
        tool_results = [e for e in all_events if e.type == StreamEventType.TOOL_RESULT]
        tokens = [e for e in all_events if e.type == StreamEventType.TOKEN]

        assert len(tool_starts) == 1
        assert "get_ohlcv_data" in tool_starts[0].content
        assert "VIC" in tool_starts[0].content
        assert len(tool_results) == 1
        assert len(tokens) == 2

    def test_multi_tool_call_fixture(self):
        """MULTI_TOOL_CALL_EVENTS produces 2 TOOL_CALL_START events."""
        handler = StreamCallbackHandler(show_tool_calls=True)
        all_events: list[StreamEvent] = []
        for event_tuple in MULTI_TOOL_CALL_EVENTS:
            all_events.extend(handler.process_agent_event(event_tuple))

        tool_starts = [e for e in all_events if e.type == StreamEventType.TOOL_CALL_START]
        tokens = [e for e in all_events if e.type == StreamEventType.TOKEN]

        assert len(tool_starts) == 2
        names = [e.content for e in tool_starts]
        assert any("FPT" in n for n in names)
        assert any("VCB" in n for n in names)
        assert len(tokens) == 1

    def test_updates_mode_tool_call_fixture(self):
        """UPDATES_MODE_TOOL_CALL fixture produces TOOL_CALL_START via fallback."""
        handler = StreamCallbackHandler(show_tool_calls=True)
        events = handler.process_agent_event(UPDATES_MODE_TOOL_CALL)

        # AIMessage has tool_calls but no tool_call_chunks, so it buffers via fallback
        # No ToolMessage in this fixture, so no TOOL_CALL_START emitted yet
        # but the tool call should be buffered in _pending_tool_calls
        assert "call_upd1" in handler._pending_tool_calls
        assert handler._pending_tool_calls["call_upd1"]["name"] == "get_ohlcv_data"
        assert '"ticker": "FPT"' in handler._pending_tool_calls["call_upd1"]["args_str"]
