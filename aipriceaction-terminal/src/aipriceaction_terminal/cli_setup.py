"""Interactive first-run setup for aipa CLI (plain terminal, no TUI)."""

from .user_settings import load_settings, save_settings


_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "openai/gpt-oss-120b:free"


def cmd_setup() -> None:
    """Run interactive setup and save settings."""
    current = load_settings()

    print("AIPriceAction Terminal Setup")
    print("=" * 30)

    # Language
    lang = _prompt(
        "Language (en/vn)",
        current.get("language", "en"),
    )
    if lang not in ("en", "vn"):
        print("Invalid language, defaulting to 'en'.")
        lang = "en"

    # Reference ticker
    ticker = _prompt(
        "Reference ticker",
        current.get("ticker", "VNINDEX"),
    )

    # API key (optional)
    api_key = _prompt(
        "API key (press Enter to skip)",
        current.get("api_key", ""),
    )

    # Base URL
    base_url = _prompt(
        "Base URL",
        current.get("openai_base_url") or _DEFAULT_BASE_URL,
    )

    # Model
    model = _prompt(
        "Model",
        current.get("openai_model") or _DEFAULT_MODEL,
    )

    data = {
        "ticker": ticker.upper(),
        "interval": current.get("interval", "1D"),
        "language": lang,
        "api_key": api_key,
        "openai_base_url": base_url,
        "openai_model": model,
        "setup_done": True,
    }

    save_settings(data)
    print("Setup complete.")


def _prompt(label: str, default: str) -> str:
    """Prompt user with current default shown in brackets."""
    hint = f" [{default}]" if default else ""
    try:
        value = input(f"{label}{hint}: ").strip()
    except EOFError:
        return default
    return value if value else default
