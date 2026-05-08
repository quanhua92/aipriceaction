"""Tests for ChatTab slash commands and interaction."""

from unittest.mock import patch

import pytest
from textual.widgets import RichLog, Input

from aipriceaction_terminal.app import AIPriceActionApp
from conftest import richlog_text


@pytest.fixture()
async def app(mock_builder, mock_client, mock_agent):
    """Mount the app with AIContextBuilder, AIPriceAction, and AgentSession patched."""
    with (
        patch("aipriceaction.AIContextBuilder", return_value=mock_builder),
        patch("aipriceaction.AIPriceAction", return_value=mock_client),
        patch("aipriceaction_terminal.agents.AgentSession", return_value=mock_agent),
    ):
        async with AIPriceActionApp().run_test() as pilot:
            yield pilot, mock_builder


async def test_chat_mount_shows_welcome(app):
    pilot, _ = app
    text = richlog_text(pilot.app.query_one("#chat-log", RichLog))
    assert "AIPriceAction Terminal" in text
    assert "/help" in text


async def test_chat_input_regular_message(app):
    pilot, _ = app
    chat_input = pilot.app.query_one("#chat-input-field", Input)
    chat_input.value = "hello"
    await pilot.click("#chat-input-field")
    await pilot.press("enter")
    await pilot.pause(0.5)

    text = richlog_text(pilot.app.query_one("#chat-log", RichLog))
    assert "You: hello" in text
    assert "Hello from agent" in text


async def test_chat_input_empty_does_nothing(app):
    pilot, _ = app
    chat_input = pilot.app.query_one("#chat-input-field", Input)
    chat_input.value = ""
    await pilot.click("#chat-input-field")
    await pilot.press("enter")
    await pilot.pause(0.1)

    text = richlog_text(pilot.app.query_one("#chat-log", RichLog))
    assert "AIPriceAction Terminal" in text
    assert "You:" not in text


async def test_chat_slash_help(app):
    pilot, _ = app
    chat_input = pilot.app.query_one("#chat-input-field", Input)
    chat_input.value = "/help"
    await pilot.click("#chat-input-field")
    await pilot.press("enter")
    await pilot.pause(0.1)

    text = richlog_text(pilot.app.query_one("#chat-log", RichLog))
    assert "Available commands" in text
    assert "/analyze" in text
    assert "/deep-research" in text
    assert "/clear" in text


async def test_chat_slash_clear(app):
    pilot, _ = app
    chat_input = pilot.app.query_one("#chat-input-field", Input)
    chat_input.value = "/help"
    await pilot.click("#chat-input-field")
    await pilot.press("enter")
    await pilot.pause(0.1)

    # Verify content exists before clear
    text_before = richlog_text(pilot.app.query_one("#chat-log", RichLog))
    assert "Available commands" in text_before

    chat_input.value = "/clear"
    await pilot.click("#chat-input-field")
    await pilot.press("enter")
    await pilot.pause(0.1)

    log = pilot.app.query_one("#chat-log", RichLog)
    assert len(log.lines) == 0


async def test_chat_slash_analyze_no_arg(app):
    pilot, _ = app
    chat_input = pilot.app.query_one("#chat-input-field", Input)
    chat_input.value = "/analyze"
    await pilot.click("#chat-input-field")
    await pilot.press("enter")
    await pilot.pause(0.1)

    text = richlog_text(pilot.app.query_one("#chat-log", RichLog))
    assert "Usage:" in text


async def test_chat_slash_analyze_with_ticker(app):
    pilot, _ = app
    chat_input = pilot.app.query_one("#chat-input-field", Input)
    chat_input.value = "/analyze VCB"
    await pilot.click("#chat-input-field")
    await pilot.press("enter")
    await pilot.pause(0.3)

    text = richlog_text(pilot.app.query_one("#chat-log", RichLog))
    assert "VCB" in text
    assert "Context built" in text


async def test_chat_slash_analyze_calls_builder(app):
    pilot, mock_builder = app
    chat_input = pilot.app.query_one("#chat-input-field", Input)
    chat_input.value = "/analyze VCB"
    await pilot.click("#chat-input-field")
    await pilot.press("enter")
    await pilot.pause(0.3)

    mock_builder.build.assert_called_once_with(ticker="VCB", interval="1D")


async def test_chat_slash_analyze_error_handling(app):
    pilot, mock_builder = app
    mock_builder.build.side_effect = ConnectionError("network failure")

    chat_input = pilot.app.query_one("#chat-input-field", Input)
    chat_input.value = "/analyze VCB"
    await pilot.click("#chat-input-field")
    await pilot.press("enter")
    await pilot.pause(0.3)

    text = richlog_text(pilot.app.query_one("#chat-log", RichLog))
    assert "network failure" in text


async def test_chat_slash_deep_research_not_implemented(app):
    pilot, _ = app
    chat_input = pilot.app.query_one("#chat-input-field", Input)
    chat_input.value = "/deep-research"
    await pilot.click("#chat-input-field")
    await pilot.press("enter")
    await pilot.pause(0.1)

    text = richlog_text(pilot.app.query_one("#chat-log", RichLog))
    assert "not yet implemented" in text.lower()


async def test_chat_slash_deep_research_with_question(app):
    pilot, _ = app
    chat_input = pilot.app.query_one("#chat-input-field", Input)
    chat_input.value = "/deep-research What is the trend?"
    await pilot.click("#chat-input-field")
    await pilot.press("enter")
    await pilot.pause(0.1)

    text = richlog_text(pilot.app.query_one("#chat-log", RichLog))
    assert "/deep-research" in text
    assert "What is the trend?" in text
    assert "not yet implemented" in text.lower()


async def test_chat_slash_unknown_command(app):
    pilot, _ = app
    chat_input = pilot.app.query_one("#chat-input-field", Input)
    chat_input.value = "/foobar"
    await pilot.click("#chat-input-field")
    await pilot.press("enter")
    await pilot.pause(0.1)

    text = richlog_text(pilot.app.query_one("#chat-log", RichLog))
    assert "Unknown command" in text


async def test_chat_slash_commands_case_insensitive(app):
    pilot, _ = app
    chat_input = pilot.app.query_one("#chat-input-field", Input)

    # Test /HELP
    chat_input.value = "/HELP"
    await pilot.click("#chat-input-field")
    await pilot.press("enter")
    await pilot.pause(0.1)
    text = richlog_text(pilot.app.query_one("#chat-log", RichLog))
    assert "Available commands" in text

    # Test /Clear
    chat_input.value = "/Clear"
    await pilot.click("#chat-input-field")
    await pilot.press("enter")
    await pilot.pause(0.1)
    log = pilot.app.query_one("#chat-log", RichLog)
    assert len(log.lines) == 0

    # Test /ANALYZE VCB
    chat_input.value = "/ANALYZE VCB"
    await pilot.click("#chat-input-field")
    await pilot.press("enter")
    await pilot.pause(0.3)
    text = richlog_text(pilot.app.query_one("#chat-log", RichLog))
    assert "Context built" in text
