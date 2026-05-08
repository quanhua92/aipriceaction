"""Agent session: wraps LangChain create_agent with streaming and retry."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver

from .callbacks import StreamCallbackHandler, StreamEvent, StreamEventType
from .config import AgentConfig, TRANSIENT_ERROR_KEYWORDS

if TYPE_CHECKING:
    from .personas import Persona
    from .tools import ToolRegistry


class OpenRouterChatOpenAI(ChatOpenAI):
    """ChatOpenAI subclass that preserves reasoning tokens from OpenRouter.

    OpenRouter reasoning models (e.g. nvidia/nemotron-3-nano-omni-reasoning) return
    a ``reasoning`` string field in the streaming delta.  LangChain's default
    ``_convert_delta_to_message_chunk`` ignores this field, so we override
    ``_convert_chunk_to_generation_chunk`` to inject it into
    ``AIMessageChunk.additional_kwargs["reasoning_content"]`` after the chunk is built.
    """

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict[str, Any],
        default_chunk_class: type,
        base_generation_info: dict[str, Any] | None,
    ) -> Any:
        """Build the generation chunk, then inject reasoning into additional_kwargs."""
        result = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if result is None:
            return result

        # Extract reasoning from the raw delta before LangChain discards it.
        choices = chunk.get("choices", [])
        if choices:
            delta = choices[0].get("delta")
            if isinstance(delta, dict):
                reasoning = delta.get("reasoning")
                if reasoning and isinstance(result.message, AIMessageChunk):
                    result.message.additional_kwargs["reasoning_content"] = reasoning

        return result


class AgentSession:
    """Manages a single agent session with memory, streaming, and retry."""

    def __init__(
        self,
        config: AgentConfig,
        persona: Persona | None = None,
        tools: ToolRegistry | None = None,
    ) -> None:
        from .personas import get_default_persona
        from .tools import get_default_tools

        self.config = config
        self.persona = persona or get_default_persona(config.lang)
        self.tools = tools or get_default_tools(config.lang)
        self._checkpointer = MemorySaver()
        self._thread_id = "terminal-default"
        self._agent = self._build_agent()

    def _build_agent(self) -> object:
        """Build the LangChain agent from current config/persona/tools."""
        llm = OpenRouterChatOpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            model=self.config.model,
            extra_body={"reasoning": {"enabled": True}},
        )
        system_prompt = self.persona.build_system_prompt(self.config.lang)
        lc_tools = self.tools.get_tools()

        return create_agent(
            llm,
            lc_tools,
            checkpointer=self._checkpointer,
            system_prompt=system_prompt,
        )

    async def stream(
        self,
        message: str,
        *,
        callback: object | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream an agent response as StreamEvents.

        Built-in retry with exponential backoff on transient errors.
        """
        handler = StreamCallbackHandler(
            show_tool_calls=True,
            show_tool_results=False,
        )

        last_error: Exception | None = None
        for attempt in range(self.config.max_retries):
            try:
                input_dict = {"messages": [{"role": "user", "content": message}]}
                config = {"configurable": {"thread_id": self._thread_id}}

                async for lc_event in self._agent.astream(
                    input_dict,
                    config=config,
                    stream_mode="messages",
                ):
                    for stream_event in handler.process_agent_event(lc_event):
                        if callback:
                            await callback(stream_event)
                        yield stream_event

                yield StreamEvent(type=StreamEventType.DONE)
                return

            except Exception as e:
                last_error = e
                err_str = str(e).lower()
                is_transient = any(kw in err_str for kw in TRANSIENT_ERROR_KEYWORDS)

                if is_transient and attempt < self.config.max_retries - 1:
                    delay = self.config.base_retry_delay * (2 ** attempt)
                    delay = min(delay, self.config.max_retry_delay)
                    yield StreamEvent(
                        type=StreamEventType.ERROR,
                        content=f"Retry {attempt + 1}/{self.config.max_retries}: {type(e).__name__}",
                    )
                    await asyncio.sleep(delay)
                else:
                    break

        # All retries exhausted
        yield StreamEvent(
            type=StreamEventType.ERROR,
            content=f"Error: {last_error}",
        )
        yield StreamEvent(type=StreamEventType.DONE)

    async def run(self, message: str) -> str:
        """Convenience wrapper: collect tokens and return the final answer."""
        parts: list[str] = []
        async for event in self.stream(message):
            if event.type == StreamEventType.TOKEN:
                parts.append(event.content)

        # Return the longest part (final answer after potential re-generation)
        return max(parts, key=len) if parts else ""

    def switch_persona(self, persona: Persona) -> None:
        """Switch to a different persona, clearing conversation history."""
        self.persona = persona
        self._checkpointer = MemorySaver()
        self._agent = self._build_agent()

    def clear_history(self) -> None:
        """Clear conversation history (start fresh session)."""
        self._checkpointer = MemorySaver()
        self._agent = self._build_agent()

    def rebuild(self) -> None:
        """Rebuild agent after config change (language/model)."""
        self._checkpointer = MemorySaver()
        self._agent = self._build_agent()
