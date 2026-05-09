"""Tests for WorkflowsTab — Analyze and Deep Research panes."""

from unittest.mock import patch

import pytest
from textual.widgets import RichLog, Input, Select

from aipriceaction_terminal.app import AIPriceActionApp
from aipriceaction_terminal.widgets import TickerSelect
from conftest import richlog_text


@pytest.fixture()
async def app(mock_builder, mock_client, mock_agent, sample_ticker_options):
    """Mount the app with AIContextBuilder, AIPriceAction, and AgentSession patched."""
    with (
        patch("aipriceaction.AIContextBuilder", return_value=mock_builder),
        patch("aipriceaction.AIPriceAction", return_value=mock_client),
        patch("aipriceaction_terminal.agents.AgentSession", return_value=mock_agent),
        patch("aipriceaction.settings.settings") as mock_sdk_settings,
    ):
        mock_sdk_settings.openai_api_key = "sk-test-key"
        async with AIPriceActionApp().run_test() as pilot:
            # Switch to workflows tab so TickerSelect is mounted
            await pilot.press("escape")
            await pilot.press("5")
            await pilot.pause(0.1)
            pilot.app.ticker_options = sample_ticker_options
            await pilot.pause(0.1)
            yield pilot, mock_builder


async def test_workflows_tab_has_nested_tabs(app):
    pilot, _ = app
    # Switch to workflows tab (key 5)
    await pilot.press("5")
    await pilot.pause(0.1)

    assert pilot.app.query_one("#wf-analyze") is not None
    assert pilot.app.query_one("#wf-deep-research") is not None


async def test_analyze_pane_default_values(app):
    pilot, _ = app
    await pilot.press("5")
    await pilot.pause(0.1)

    ticker_select = pilot.app.query_one("#wf-ticker", TickerSelect)
    interval_select = pilot.app.query_one("#wf-interval", Select)
    assert ticker_select.value == "VNINDEX"
    assert interval_select.value == "1D"


async def test_analyze_pane_button_triggers_build(app):
    pilot, builder = app
    await pilot.press("escape")
    await pilot.press("5")
    await pilot.pause(0.1)

    await pilot.click("#wf-analyze-btn")
    await pilot.pause(0.5)

    builder.build.assert_called_once()
    call_kwargs = builder.build.call_args[1]
    assert call_kwargs["ticker"] == "VNINDEX"
    assert call_kwargs["interval"] == "1D"

    text = richlog_text(pilot.app.query_one("#wf-output", RichLog))
    assert "Context ready" in text


async def test_analyze_pane_select_different_ticker(app):
    pilot, builder = app
    await pilot.press("escape")
    await pilot.press("5")
    await pilot.pause(0.1)

    ticker_select = pilot.app.query_one("#wf-ticker", TickerSelect)
    ticker_select.value = "FPT"
    await pilot.pause(0.1)

    await pilot.click("#wf-analyze-btn")
    await pilot.pause(0.5)

    builder.build.assert_called_once()
    call_kwargs = builder.build.call_args[1]
    assert call_kwargs["ticker"] == "FPT"


async def test_analyze_pane_custom_interval(app):
    pilot, builder = app
    await pilot.press("escape")
    await pilot.press("5")
    await pilot.pause(0.1)

    interval_select = pilot.app.query_one("#wf-interval", Select)
    interval_select.value = "1h"
    await pilot.pause(0.1)

    await pilot.click("#wf-analyze-btn")
    await pilot.pause(0.5)

    builder.build.assert_called_once()
    call_kwargs = builder.build.call_args[1]
    assert call_kwargs["interval"] == "1h"


async def test_analyze_pane_error_handling(app):
    pilot, builder = app
    builder.build.side_effect = RuntimeError("build failed")

    await pilot.press("escape")
    await pilot.press("5")
    await pilot.pause(0.1)

    await pilot.click("#wf-analyze-btn")
    await pilot.pause(0.5)

    text = richlog_text(pilot.app.query_one("#wf-output", RichLog))
    assert "build failed" in text


async def test_analyze_pane_options_update_on_load(app):
    """Verify that watch_app_ticker_options populates the Select when options load."""
    pilot, _ = app
    await pilot.press("5")
    await pilot.pause(0.1)

    ticker_select = pilot.app.query_one("#wf-ticker", TickerSelect)
    # Default value should be preserved
    assert ticker_select.value == "VNINDEX"


async def test_analyze_pane_autocomplete_select_puts_ticker_symbol(app):
    """Verify that selecting from autocomplete puts the ticker symbol (not label) into input."""
    pilot, builder = app
    await pilot.press("escape")
    await pilot.press("5")
    await pilot.pause(0.1)

    # Clear input and type to trigger autocomplete filtering
    ticker_input = pilot.app.query_one("#ticker-input", Input)
    ticker_input.value = ""
    await pilot.pause(0.05)
    await pilot.click("#ticker-input")
    await pilot.press("F")
    await pilot.press("P")
    await pilot.press("T")
    await pilot.press("space")
    await pilot.press("C")
    await pilot.press("o")
    await pilot.press("r")
    await pilot.press("p")
    await pilot.pause(0.2)

    # Press Enter to select the top fuzzy match
    await pilot.press("enter")
    await pilot.pause(0.1)

    ticker_select = pilot.app.query_one("#wf-ticker", TickerSelect)
    # The input should contain just the ticker symbol, not the full label
    assert ticker_select.value == "FPT"
    # And it should NOT contain the label text
    assert "FPT Corporation" not in ticker_input.value


async def test_deep_research_pane_mount_message(app):
    pilot, _ = app
    await pilot.press("escape")
    await pilot.press("5")
    await pilot.pause(0.1)

    # Switch to deep research sub-tab
    nested_tc = pilot.app.query("WorkflowsTab TabbedContent").first()
    nested_tc.active = "wf-deep-research"
    await pilot.pause(0.1)

    text = richlog_text(pilot.app.query_one("#dr-output", RichLog))
    assert "multi-agent" in text.lower()


async def test_deep_research_button_shows_not_implemented(app):
    pilot, _ = app
    await pilot.press("escape")
    await pilot.press("5")
    await pilot.pause(0.1)

    nested_tc = pilot.app.query("WorkflowsTab TabbedContent").first()
    nested_tc.active = "wf-deep-research"
    await pilot.pause(0.1)

    await pilot.click("#dr-btn")
    await pilot.pause(0.1)

    text = richlog_text(pilot.app.query_one("#dr-output", RichLog))
    assert "Deep Research:" in text


async def test_deep_research_with_question(app):
    pilot, _ = app
    await pilot.press("escape")
    await pilot.press("5")
    await pilot.pause(0.1)

    nested_tc = pilot.app.query("WorkflowsTab TabbedContent").first()
    nested_tc.active = "wf-deep-research"
    await pilot.pause(0.1)

    dr_input = pilot.app.query_one("#dr-question", Input)
    dr_input.value = "What is the market trend?"
    await pilot.click("#dr-question")
    await pilot.press("enter")
    await pilot.pause(0.1)

    text = richlog_text(pilot.app.query_one("#dr-output", RichLog))
    assert "Deep Research:" in text
    assert "What is the market trend?" in text
