"""Persistent user settings stored in ~/.aipriceaction/settings.json."""

import json
import os
from pathlib import Path

_CONFIG_DIR = Path.home() / ".aipriceaction"
_SETTINGS_FILE = _CONFIG_DIR / "settings.json"

_DEFAULTS = {
    "ticker": "VNINDEX",
    "interval": "1D",
    "language": "en",
    "api_key": "",
    "openai_base_url": "",
    "openai_model": "",
    "setup_done": False,
}


def load_settings() -> dict:
    """Load settings from disk, falling back to defaults for missing keys."""
    if _SETTINGS_FILE.exists():
        data = json.loads(_SETTINGS_FILE.read_text())
        return {**_DEFAULTS, **data}
    return dict(_DEFAULTS)


def save_settings(data: dict) -> None:
    """Persist settings to disk, creating the config directory if needed."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _SETTINGS_FILE.write_text(json.dumps(data, indent=2))


def apply_settings_to_env() -> None:
    """Seed environment variables from settings.json.

    Uses ``os.environ.setdefault`` so that existing env vars always win.
    Must be called before any SDK import that reads env vars.
    """
    settings = load_settings()
    _mapping = {
        "OPENAI_API_KEY": "api_key",
        "OPENAI_BASE_URL": "openai_base_url",
        "OPENAI_MODEL": "openai_model",
    }
    for env_key, settings_key in _mapping.items():
        value = settings.get(settings_key, "")
        if value:
            os.environ.setdefault(env_key, value)
