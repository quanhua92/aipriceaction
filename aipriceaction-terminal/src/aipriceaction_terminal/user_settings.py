"""Persistent user settings stored in ~/.aipriceaction/settings.json."""

import json
import os
from pathlib import Path

_CONFIG_DIR = Path.home() / ".aipriceaction"
_SETTINGS_FILE = _CONFIG_DIR / "settings.json"

_DEFAULTS = {
    "ticker": "VNINDEX",
    "interval": "1D",
    "language": "vn",
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


def _load_dotenv_values(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict (key=value per line, ignores blanks/comments)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _find_dotenv() -> Path:
    """Walk up from CWD to find .env, falling back to home directory."""
    current = Path.cwd()
    for _ in range(5):
        candidate = current / ".env"
        if candidate.exists():
            return candidate
        current = current.parent
    return Path.home() / ".env"


def apply_settings_to_env() -> None:
    """Seed environment variables so CLI commands pick up config from .env and settings.json.

    Priority (highest wins): real env vars > .env file > settings.json.
    All seeding uses ``os.environ.setdefault`` so real env vars are never overwritten.
    Must be called before any SDK import that reads env vars.
    """
    _env_keys = {"OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"}

    # 1. Seed from .env file (middle priority)
    for key, value in _load_dotenv_values(_find_dotenv()).items():
        if key in _env_keys and value:
            os.environ.setdefault(key, value)

    # 2. Seed from settings.json (lowest priority)
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
