"""Persistent user settings stored in ~/.aipriceaction/settings.json."""

import json
from pathlib import Path

_CONFIG_DIR = Path.home() / ".aipriceaction"
_SETTINGS_FILE = _CONFIG_DIR / "settings.json"

_DEFAULTS = {
    "ticker": "VNINDEX",
    "interval": "1D",
    "language": "en",
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
