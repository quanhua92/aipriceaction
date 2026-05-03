from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> str:
    """Walk up from this file to find .env."""
    current = Path(__file__).resolve().parent
    for _ in range(5):
        candidate = current / ".env"
        if candidate.exists():
            return str(candidate)
        current = current.parent
    return ".env"  # fallback, will rely on env vars


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_find_env_file(), extra="ignore")

    # OpenAI / OpenRouter
    openai_api_key: str = ""
    openai_base_url: str = "https://openrouter.ai/api/v1"
    openai_model: str = "openai/gpt-oss-20b"

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    anthropic_model: str = "claude-sonnet-4-6"

    # General
    ai_context_lang: str = "en"


settings = Settings()
