"""Agents module for AI-powered chat in the terminal TUI."""

from .agent import AgentSession, OpenRouterChatOpenAI
from .callbacks import StreamCallbackHandler, StreamEvent, StreamEventType
from .config import AgentConfig
from .personas import Persona, PersonaRegistry, get_default_persona
from .tools import ToolRegistry, get_default_tools

__all__ = [
    "AgentSession",
    "OpenRouterChatOpenAI",
    "AgentConfig",
    "Persona",
    "PersonaRegistry",
    "get_default_persona",
    "ToolRegistry",
    "get_default_tools",
    "StreamCallbackHandler",
    "StreamEvent",
    "StreamEventType",
]
