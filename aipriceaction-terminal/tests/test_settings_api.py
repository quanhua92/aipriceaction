"""Tests for persistent API key settings and env var / .env priority."""

from unittest.mock import patch, MagicMock

import pytest
from textual.widgets import Input, Select, Static

from aipriceaction_terminal.app import AIPriceActionApp
from aipriceaction_terminal.user_settings import _DEFAULTS
from aipriceaction_terminal.settings_tab import _value_from_env_or_dotenv


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def app(mock_builder, mock_client, mock_agent):
    """Mount the app with all dependencies patched."""
    with (
        patch("aipriceaction_terminal.app.load_settings", return_value={
            "ticker": "VNINDEX", "interval": "1D", "language": "en",
            "api_key": "", "openai_base_url": "", "openai_model": "",
        }),
        patch("aipriceaction.AIContextBuilder", return_value=mock_builder),
        patch("aipriceaction.AIPriceAction", return_value=mock_client),
        patch("aipriceaction_terminal.agents.AgentSession", return_value=mock_agent),
    ):
        async with AIPriceActionApp().run_test() as pilot:
            yield pilot


async def _switch_to_settings(pilot):
    """Navigate to the Settings tab using the keyboard shortcut (key 6)."""
    await pilot.press("6")
    await pilot.pause(0.1)
    return pilot


# ---------------------------------------------------------------------------
# user_settings defaults
# ---------------------------------------------------------------------------


class TestUserDefaults:
    def test_api_fields_in_defaults(self):
        assert "api_key" in _DEFAULTS
        assert "openai_base_url" in _DEFAULTS
        assert "openai_model" in _DEFAULTS

    def test_api_defaults_are_empty_strings(self):
        assert _DEFAULTS["api_key"] == ""
        assert _DEFAULTS["openai_base_url"] == ""
        assert _DEFAULTS["openai_model"] == ""


# ---------------------------------------------------------------------------
# _value_from_env_or_dotenv helper
# ---------------------------------------------------------------------------


class TestValueFromEnvOrDotenv:
    def test_returns_true_when_sdk_has_value_and_settings_json_empty(self):
        """SDK has a value from .env, settings.json is empty → from .env."""
        with (
            patch("aipriceaction_terminal.settings_tab.settings") as mock_sdk,
            patch("aipriceaction_terminal.settings_tab.load_settings", return_value={}),
        ):
            mock_sdk.openai_api_key = "sk-from-dotenv"
            assert _value_from_env_or_dotenv("openai_api_key", "api_key") is True

    def test_returns_false_when_settings_json_matches_sdk(self):
        """User saved the same value in settings.json → treat as from settings."""
        with (
            patch("aipriceaction_terminal.settings_tab.settings") as mock_sdk,
            patch("aipriceaction_terminal.settings_tab.load_settings", return_value={"api_key": "sk-from-dotenv"}),
        ):
            mock_sdk.openai_api_key = "sk-from-dotenv"
            assert _value_from_env_or_dotenv("openai_api_key", "api_key") is False

    def test_returns_false_when_settings_json_differs(self):
        """User explicitly set a different value in settings.json but SDK reads from .env → from .env."""
        with (
            patch("aipriceaction_terminal.settings_tab.settings") as mock_sdk,
            patch("aipriceaction_terminal.settings_tab.load_settings", return_value={"api_key": "sk-from-ui"}),
        ):
            mock_sdk.openai_api_key = "sk-from-dotenv"
            # saved_val != sdk_val → effective value is from env/.env (overrode settings)
            assert _value_from_env_or_dotenv("openai_api_key", "api_key") is True

    def test_returns_false_when_sdk_empty(self):
        """No value anywhere → not from .env."""
        with (
            patch("aipriceaction_terminal.settings_tab.settings") as mock_sdk,
            patch("aipriceaction_terminal.settings_tab.load_settings", return_value={}),
        ):
            mock_sdk.openai_api_key = ""
            assert _value_from_env_or_dotenv("openai_api_key", "api_key") is False


# ---------------------------------------------------------------------------
# AgentConfig resolution chain
# ---------------------------------------------------------------------------


class TestAgentConfigResolution:
    def test_env_var_takes_priority_over_settings(self):
        """OPENAI_API_KEY env var wins over settings.json."""
        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "env-key-123"}, clear=False),
            patch("aipriceaction_terminal.agents.config.load_settings", return_value={"api_key": "saved-key"}),
            patch("aipriceaction_terminal.agents.config.settings") as mock_sdk,
        ):
            mock_sdk.openai_api_key = "sdk-default-key"

            from aipriceaction_terminal.agents.config import _resolve
            result = _resolve("api_key", "OPENAI_API_KEY", mock_sdk.openai_api_key)
            assert result == "env-key-123"

    def test_settings_json_fallback_when_no_env_var(self):
        """settings.json value is used when env var is absent."""
        with (
            patch.dict("os.environ", {}, clear=False),
            patch("aipriceaction_terminal.agents.config.load_settings", return_value={"api_key": "saved-key-456"}),
            patch("aipriceaction_terminal.agents.config.settings") as mock_sdk,
        ):
            mock_sdk.openai_api_key = "sdk-default-key"

            from aipriceaction_terminal.agents.config import _resolve
            result = _resolve("api_key", "OPENAI_API_KEY", mock_sdk.openai_api_key)
            assert result == "saved-key-456"

    def test_sdk_default_when_neither_env_nor_settings(self):
        """SDK default is used when both env var and settings.json are empty."""
        with (
            patch.dict("os.environ", {}, clear=False),
            patch("aipriceaction_terminal.agents.config.load_settings", return_value={}),
            patch("aipriceaction_terminal.agents.config.settings") as mock_sdk,
        ):
            mock_sdk.openai_base_url = "https://openrouter.ai/api/v1"

            from aipriceaction_terminal.agents.config import _resolve
            result = _resolve("openai_base_url", "OPENAI_BASE_URL", mock_sdk.openai_base_url)
            assert result == "https://openrouter.ai/api/v1"


# ---------------------------------------------------------------------------
# Settings tab: API fields visible
# ---------------------------------------------------------------------------


async def test_settings_tab_shows_api_fields(app):
    """API Key, Base URL, Model inputs are present in the settings tab."""
    pilot = await _switch_to_settings(app)
    assert pilot.app.query_one("#setting-api-key", Input) is not None
    assert pilot.app.query_one("#setting-base-url", Input) is not None
    assert pilot.app.query_one("#setting-model", Input) is not None


# ---------------------------------------------------------------------------
# Settings tab: hints show source
# ---------------------------------------------------------------------------


async def test_hint_shows_dotenv_when_sdk_has_key_and_json_empty():
    """Hint says '.env file' when SDK settings has the key but settings.json doesn't."""
    with (
        patch("aipriceaction_terminal.settings_tab.settings") as mock_sdk,
        patch("aipriceaction_terminal.settings_tab.load_settings", return_value={
            "ticker": "VNINDEX", "interval": "1D", "language": "en",
            "api_key": "", "openai_base_url": "", "openai_model": "",
        }),
        patch("aipriceaction_terminal.app.load_settings", return_value={
            "ticker": "VNINDEX", "interval": "1D", "language": "en",
            "api_key": "", "openai_base_url": "", "openai_model": "",
        }),
        patch("aipriceaction.AIContextBuilder"),
        patch("aipriceaction.AIPriceAction"),
        patch("aipriceaction_terminal.agents.AgentSession"),
    ):
        mock_sdk.openai_api_key = "sk-from-dotenv"
        mock_sdk.openai_base_url = "https://openrouter.ai/api/v1"
        mock_sdk.openai_model = "openai/gpt-oss-20b"
        mock_sdk.ai_context_lang = "en"

        async with AIPriceActionApp().run_test() as pilot:
            await _switch_to_settings(pilot)
            hint = pilot.app.query_one("#hint-api-key", Static)
            assert ".env" in hint.content
            assert "OPENAI_API_KEY" in hint.content


async def test_hint_shows_settings_when_json_has_value():
    """Hint says 'settings' when settings.json has the value and SDK matches."""
    saved = {
        "ticker": "VNINDEX", "interval": "1D", "language": "en",
        "api_key": "sk-saved-in-ui", "openai_base_url": "", "openai_model": "",
    }
    with (
        patch("aipriceaction_terminal.settings_tab.settings") as mock_sdk,
        patch("aipriceaction_terminal.settings_tab.load_settings", return_value=saved),
        patch("aipriceaction_terminal.app.load_settings", return_value=saved),
        patch("aipriceaction.AIContextBuilder"),
        patch("aipriceaction.AIPriceAction"),
        patch("aipriceaction_terminal.agents.AgentSession"),
    ):
        # SDK value must match settings.json for the hint to say "settings"
        mock_sdk.openai_api_key = "sk-saved-in-ui"
        mock_sdk.openai_base_url = "https://openrouter.ai/api/v1"
        mock_sdk.openai_model = "openai/gpt-oss-20b"
        mock_sdk.ai_context_lang = "en"

        async with AIPriceActionApp().run_test() as pilot:
            await _switch_to_settings(pilot)
            hint = pilot.app.query_one("#hint-api-key", Static)
            assert "settings" in hint.content


async def test_hint_shows_environment_when_os_envar_set():
    """Hint says 'environment' when os.environ has the var."""
    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "sk-env"}, clear=False),
        patch("aipriceaction_terminal.settings_tab.settings") as mock_sdk,
        patch("aipriceaction_terminal.settings_tab.load_settings", return_value={
            "ticker": "VNINDEX", "interval": "1D", "language": "en",
            "api_key": "", "openai_base_url": "", "openai_model": "",
        }),
        patch("aipriceaction_terminal.app.load_settings", return_value={
            "ticker": "VNINDEX", "interval": "1D", "language": "en",
            "api_key": "", "openai_base_url": "", "openai_model": "",
        }),
        patch("aipriceaction.AIContextBuilder"),
        patch("aipriceaction.AIPriceAction"),
        patch("aipriceaction_terminal.agents.AgentSession"),
    ):
        mock_sdk.openai_api_key = "sk-env"
        mock_sdk.openai_base_url = "https://openrouter.ai/api/v1"
        mock_sdk.openai_model = "openai/gpt-oss-20b"
        mock_sdk.ai_context_lang = "en"

        async with AIPriceActionApp().run_test() as pilot:
            await _switch_to_settings(pilot)
            hint = pilot.app.query_one("#hint-api-key", Static)
            assert "environment" in hint.content
            assert "OPENAI_API_KEY" in hint.content


async def test_fields_pre_filled_from_settings():
    """Saved settings are loaded into the input fields on mount."""
    saved = {
        "ticker": "FPT", "interval": "1h", "language": "vn",
        "api_key": "sk-saved", "openai_base_url": "https://custom", "openai_model": "my-model",
    }
    with (
        patch("aipriceaction_terminal.app.load_settings", return_value=saved),
        patch("aipriceaction_terminal.settings_tab.load_settings", return_value=saved),
        patch("aipriceaction_terminal.settings_tab.settings") as mock_sdk,
        patch("aipriceaction.AIContextBuilder"),
        patch("aipriceaction.AIPriceAction"),
        patch("aipriceaction_terminal.agents.AgentSession"),
    ):
        mock_sdk.openai_api_key = ""
        mock_sdk.openai_base_url = ""
        mock_sdk.openai_model = ""
        mock_sdk.ai_context_lang = "en"

        async with AIPriceActionApp().run_test() as pilot:
            await _switch_to_settings(pilot)
            assert pilot.app.query_one("#setting-api-key", Input).value == "sk-saved"
            assert pilot.app.query_one("#setting-base-url", Input).value == "https://custom"
            assert pilot.app.query_one("#setting-model", Input).value == "my-model"


# ---------------------------------------------------------------------------
# Settings tab: Apply button persistence
# ---------------------------------------------------------------------------


async def test_apply_saves_api_fields_when_no_dotenv():
    """API fields are persisted when .env/env is NOT providing the value."""
    saved = {
        "ticker": "VCB", "interval": "1D", "language": "en",
        "api_key": "", "openai_base_url": "", "openai_model": "",
    }
    with (
        patch("aipriceaction_terminal.app.load_settings", return_value=saved),
        patch("aipriceaction_terminal.settings_tab.load_settings", return_value=saved),
        patch("aipriceaction_terminal.settings_tab.settings") as mock_sdk,
        patch("aipriceaction_terminal.settings_tab.save_settings") as mock_save,
        patch("aipriceaction.AIContextBuilder"),
        patch("aipriceaction.AIPriceAction"),
        patch("aipriceaction_terminal.agents.AgentSession"),
    ):
        mock_sdk.openai_api_key = ""
        mock_sdk.openai_base_url = ""
        mock_sdk.openai_model = ""
        mock_sdk.ai_context_lang = "en"

        async with AIPriceActionApp().run_test() as pilot:
            await _switch_to_settings(pilot)
            pilot.app.query_one("#setting-api-key", Input).value = "sk-new-key"
            pilot.app.query_one("#setting-base-url", Input).value = "https://new.api"
            pilot.app.query_one("#setting-model", Input).value = "new-model"
            pilot.app.query_one("#apply-btn").focus()
            await pilot.press("enter")
            await pilot.pause(0.1)

            mock_save.assert_called_once()
            saved_data = mock_save.call_args[0][0]
            assert saved_data["api_key"] == "sk-new-key"
            assert saved_data["openai_base_url"] == "https://new.api"
            assert saved_data["openai_model"] == "new-model"


async def test_apply_skips_api_key_when_dotenv_has_it():
    """API key is NOT persisted when .env is providing the value."""
    saved = {
        "ticker": "VCB", "interval": "1D", "language": "en",
        "api_key": "", "openai_base_url": "", "openai_model": "",
    }
    with (
        patch("aipriceaction_terminal.app.load_settings", return_value=saved),
        patch("aipriceaction_terminal.settings_tab.load_settings", return_value=saved),
        patch("aipriceaction_terminal.settings_tab.settings") as mock_sdk,
        patch("aipriceaction_terminal.settings_tab.save_settings") as mock_save,
        patch("aipriceaction.AIContextBuilder"),
        patch("aipriceaction.AIPriceAction"),
        patch("aipriceaction_terminal.agents.AgentSession"),
    ):
        mock_sdk.openai_api_key = "sk-from-dotenv"
        mock_sdk.openai_base_url = ""
        mock_sdk.openai_model = ""
        mock_sdk.ai_context_lang = "en"

        async with AIPriceActionApp().run_test() as pilot:
            await _switch_to_settings(pilot)
            pilot.app.query_one("#setting-api-key", Input).value = "sk-typed-key"
            pilot.app.query_one("#apply-btn").focus()
            await pilot.press("enter")
            await pilot.pause(0.1)

            mock_save.assert_called_once()
            saved_data = mock_save.call_args[0][0]
            assert "api_key" not in saved_data
            # Other API fields without .env values should still be saved
            assert "openai_base_url" in saved_data
            assert "openai_model" in saved_data


async def test_apply_still_saves_general_settings():
    """Ticker, interval, language are always saved."""
    saved = {
        "ticker": "VNINDEX", "interval": "1D", "language": "en",
        "api_key": "", "openai_base_url": "", "openai_model": "",
    }
    with (
        patch("aipriceaction_terminal.app.load_settings", return_value=saved),
        patch("aipriceaction_terminal.settings_tab.load_settings", return_value=saved),
        patch("aipriceaction_terminal.settings_tab.settings") as mock_sdk,
        patch("aipriceaction_terminal.settings_tab.save_settings") as mock_save,
        patch("aipriceaction.AIContextBuilder"),
        patch("aipriceaction.AIPriceAction"),
        patch("aipriceaction_terminal.agents.AgentSession"),
    ):
        mock_sdk.openai_api_key = "sk-from-dotenv"
        mock_sdk.openai_base_url = "https://openrouter.ai/api/v1"
        mock_sdk.openai_model = "openai/gpt-oss-20b"
        mock_sdk.ai_context_lang = "en"

        async with AIPriceActionApp().run_test() as pilot:
            await _switch_to_settings(pilot)
            pilot.app.query_one("#setting-ticker", Input).value = "FPT"
            pilot.app.query_one("#setting-interval", Select).value = "1h"
            pilot.app.query_one("#setting-language", Select).value = "vn"
            pilot.app.query_one("#apply-btn").focus()
            await pilot.press("enter")
            await pilot.pause(0.1)

            saved_data = mock_save.call_args[0][0]
            assert saved_data["ticker"] == "FPT"
            assert saved_data["interval"] == "1h"
            assert saved_data["language"] == "vn"
