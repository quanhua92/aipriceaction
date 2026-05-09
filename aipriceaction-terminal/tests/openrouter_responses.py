"""Real OpenRouter SSE response fixtures using actual LangChain message types.

These fixtures reproduce the exact message structures that LangChain's
``_convert_delta_to_message_chunk`` produces from OpenRouter/OpenAI
streaming responses.  They use real ``AIMessageChunk`` and ``ToolMessage``
objects — NOT MagicMock — so tests catch real attribute-access bugs.

Each fixture is a ``list[tuple[message, metadata]]`` matching what
``stream_mode="messages"`` yields from LangGraph.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage


# ---------------------------------------------------------------------------
# 1. Simple text response (no tools, no thinking)
# ---------------------------------------------------------------------------

SIMPLE_TEXT_EVENTS: list[tuple[object, dict]] = [
    (AIMessageChunk(content="Hello"), {"langgraph_node": "agent"}),
    (AIMessageChunk(content=" there"), {"langgraph_node": "agent"}),
    (AIMessageChunk(content="!"), {"langgraph_node": "agent"}),
]


# ---------------------------------------------------------------------------
# 2. Single tool call — realistic OpenAI streaming
#    OpenAI sends:
#      chunk 1: id + name + empty args
#      chunk 2-N: only index + partial args
#      then ToolMessage when execution finishes
# ---------------------------------------------------------------------------

SINGLE_TOOL_CALL_EVENTS: list[tuple[object, dict]] = [
    # chunk 1: id + name + empty args
    (
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": "call_abc123", "name": "get_ohlcv_data", "args": "", "index": 0},
            ],
        ),
        {"langgraph_node": "agent"},
    ),
    # chunk 2: partial args via index only
    (
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": None, "name": None, "args": '{"ticker":', "index": 0},
            ],
        ),
        {"langgraph_node": "agent"},
    ),
    # chunk 3: rest of args
    (
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": None, "name": None, "args": ' "VIC"}', "index": 0},
            ],
        ),
        {"langgraph_node": "agent"},
    ),
    # ToolMessage: execution result
    (
        ToolMessage(
            content="VIC OHLCV data: close=85.5, volume=1.2M",
            tool_call_id="call_abc123",
        ),
        {"langgraph_node": "tools"},
    ),
    # Final answer tokens after tool result
    (
        AIMessageChunk(content="Based on "),
        {"langgraph_node": "agent"},
    ),
    (
        AIMessageChunk(content="the data, VIC is trending up."),
        {"langgraph_node": "agent"},
    ),
]


# ---------------------------------------------------------------------------
# 3. Multiple parallel tool calls (2 calls, different indices)
# ---------------------------------------------------------------------------

MULTI_TOOL_CALL_EVENTS: list[tuple[object, dict]] = [
    # First chunk: both tool calls start with id + name
    (
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": "call_a", "name": "get_ohlcv_data", "args": "", "index": 0},
                {"id": "call_b", "name": "get_ohlcv_data", "args": "", "index": 1},
            ],
        ),
        {"langgraph_node": "agent"},
    ),
    # Args stream in for both
    (
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": None, "name": None, "args": '{"ticker": "FPT"}', "index": 0},
                {"id": None, "name": None, "args": '{"ticker": "VCB"}', "index": 1},
            ],
        ),
        {"langgraph_node": "agent"},
    ),
    # ToolMessages arrive (order may vary)
    (
        ToolMessage(content="FPT data here", tool_call_id="call_a"),
        {"langgraph_node": "tools"},
    ),
    (
        ToolMessage(content="VCB data here", tool_call_id="call_b"),
        {"langgraph_node": "tools"},
    ),
    # Final answer
    (
        AIMessageChunk(content="Both stocks are performing well."),
        {"langgraph_node": "agent"},
    ),
]


# ---------------------------------------------------------------------------
# 4. Thinking/reasoning response
#    OpenRouter reasoning models return reasoning_content in additional_kwargs
#    (injected by OpenRouterChatOpenAI._convert_chunk_to_generation_chunk)
# ---------------------------------------------------------------------------

THINKING_EVENTS: list[tuple[object, dict]] = [
    # Reasoning tokens
    (
        AIMessageChunk(
            content="",
            additional_kwargs={"reasoning_content": "The user is asking about "},
        ),
        {"langgraph_node": "agent"},
    ),
    (
        AIMessageChunk(
            content="",
            additional_kwargs={"reasoning_content": "VNINDEX performance."},
        ),
        {"langgraph_node": "agent"},
    ),
    # Content tokens after reasoning
    (
        AIMessageChunk(content="VNINDEX "),
        {"langgraph_node": "agent"},
    ),
    (
        AIMessageChunk(content="closed at 1,250.5 today."),
        {"langgraph_node": "agent"},
    ),
]


# ---------------------------------------------------------------------------
# 5. Mixed: thinking → tool call → answer (realistic full cycle)
# ---------------------------------------------------------------------------

MIXED_THINKING_TOOL_CALL_EVENTS: list[tuple[object, dict]] = [
    # Thinking
    (
        AIMessageChunk(
            content="",
            additional_kwargs={"reasoning_content": "I need to fetch data for VCB."},
        ),
        {"langgraph_node": "agent"},
    ),
    # Tool call: first chunk with id + name
    (
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": "call_mix1", "name": "get_ohlcv_data", "args": "", "index": 0},
            ],
        ),
        {"langgraph_node": "agent"},
    ),
    # Tool call: args
    (
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"id": None, "name": None, "args": '{"ticker":"VCB"}', "index": 0},
            ],
        ),
        {"langgraph_node": "agent"},
    ),
    # ToolMessage
    (
        ToolMessage(content="VCB OHLCV: close=95.0", tool_call_id="call_mix1"),
        {"langgraph_node": "tools"},
    ),
    # Final answer with content
    (
        AIMessageChunk(content="VCB is at 95.0."),
        {"langgraph_node": "agent"},
    ),
]


# ---------------------------------------------------------------------------
# 6. Non-streaming AIMessage fallback (stream_mode="updates")
# ---------------------------------------------------------------------------

UPDATES_MODE_TOOL_CALL: dict[str, dict] = {
    "agent": {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call_upd1",
                        "name": "get_ohlcv_data",
                        "args": {"ticker": "FPT", "interval": "1D"},
                    },
                ],
            ),
        ],
    },
}
