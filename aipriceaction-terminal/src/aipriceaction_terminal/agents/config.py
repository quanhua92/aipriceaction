"""Agent configuration: reads from SDK settings."""

from __future__ import annotations

from dataclasses import dataclass, field

from aipriceaction.settings import settings


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for an agent session.

    Reads defaults from the SDK settings singleton (same .env the SDK uses).
    """

    api_key: str = field(default_factory=lambda: settings.openai_api_key)
    base_url: str = field(default_factory=lambda: settings.openai_base_url)
    model: str = field(default_factory=lambda: settings.openai_model)
    lang: str = field(default_factory=lambda: settings.ai_context_lang)
    max_retries: int = 3
    base_retry_delay: float = 5.0
    max_retry_delay: float = 60.0


TRANSIENT_ERROR_KEYWORDS: tuple[str, ...] = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "timeout",
    "connection",
    "overloaded",
)
