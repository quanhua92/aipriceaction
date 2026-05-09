"""Agent configuration: reads from env vars, then settings.json, then SDK defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from aipriceaction.settings import settings

from ..user_settings import load_settings


def _resolve(field_name: str, env_var: str, sdk_default: str) -> str:
    """Resolve a config field: env var > settings.json > SDK default."""
    env_val = os.environ.get(env_var, "")
    if env_val:
        return env_val
    saved = load_settings().get(field_name, "")
    if saved:
        return saved
    return sdk_default


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for an agent session.

    Resolution order per field: environment variable > settings.json > SDK default.
    """

    api_key: str = field(
        default_factory=lambda: _resolve("api_key", "OPENAI_API_KEY", settings.openai_api_key),
    )
    base_url: str = field(
        default_factory=lambda: _resolve("openai_base_url", "OPENAI_BASE_URL", settings.openai_base_url),
    )
    model: str = field(
        default_factory=lambda: _resolve("openai_model", "OPENAI_MODEL", settings.openai_model),
    )
    lang: str = field(
        default_factory=lambda: _resolve("language", "AI_CONTEXT_LANG", settings.ai_context_lang),
    )
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
