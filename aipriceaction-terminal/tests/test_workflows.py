"""Tests for WorkflowsTab — Analyze and Deep Research panes."""

from unittest.mock import patch

import pytest
from textual.widgets import RichLog, Input, Select

from aipriceaction_terminal.app import AIPriceActionApp
from conftest import richlog_text


@pytest.fixture()
async def app(mock_builder):
    """Mount the app with AIContextBuilder patched."""
    with patch("aipriceaction.AIContextBuilder", return_value=mock_builder):
        async with AIPriceActionApp().run_test() as pilot:
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

    ticker_input = pilot.app.query_one("#wf-ticker", Input)
    interval_select = pilot.app.query_one("#wf-interval", Select)
    assert ticker_input.value == "VNINDEX"
    assert interval_select.value == "1D"


async def test_analyze_pane_button_triggers_build(app):
    pilot, builder = app
    await pilot.press("5")
    await pilot.pause(0.1)

    await pilot.click("#wf-analyze-btn")
    await pilot.pause(0.3)

    builder.build.assert_called_once()
    call_kwargs = builder.build.call_args[1]
    assert call_kwargs["ticker"] == "VNINDEX"
    assert call_kwargs["interval"] == "1D"

    text = richlog_text(pilot.app.query_one("#wf-output", RichLog))
    assert "Context built" in text


async def test_analyze_pane_empty_ticker_shows_error(app):
    pilot, builder = app
    await pilot.press("5")
    await pilot.pause(0.1)

    ticker_input = pilot.app.query_one("#wf-ticker", Input)
    ticker_input.value = ""
    await pilot.click("#wf-ticker")
    await pilot.press("enter")
    await pilot.pause(0.1)

    builder.build.assert_not_called()

    notifications = pilot.app._notifications
    assert any("ticker" in str(n).lower() for n in notifications)


async def test_analyze_pane_input_submitted(app):
    pilot, builder = app
    await pilot.press("5")
    await pilot.pause(0.1)

    ticker_input = pilot.app.query_one("#wf-ticker", Input)
    ticker_input.value = "FPT"
    await pilot.click("#wf-ticker")
    await pilot.press("enter")
    await pilot.pause(0.3)

    builder.build.assert_called_once()
    call_kwargs = builder.build.call_args[1]
    assert call_kwargs["ticker"] == "FPT"


async def test_analyze_pane_custom_interval(app):
    pilot, builder = app
    await pilot.press("5")
    await pilot.pause(0.1)

    interval_select = pilot.app.query_one("#wf-interval", Select)
    interval_select.value = "1h"
    await pilot.click("#wf-ticker")
    await pilot.press("enter")
    await pilot.pause(0.3)

    builder.build.assert_called_once()
    call_kwargs = builder.build.call_args[1]
    assert call_kwargs["interval"] == "1h"


async def test_analyze_pane_error_handling(app):
    pilot, builder = app
    builder.build.side_effect = RuntimeError("build failed")

    await pilot.press("5")
    await pilot.pause(0.1)

    await pilot.click("#wf-analyze-btn")
    await pilot.pause(0.3)

    text = richlog_text(pilot.app.query_one("#wf-output", RichLog))
    assert "build failed" in text


async def test_deep_research_pane_mount_message(app):
    pilot, _ = app
    await pilot.press("5")
    await pilot.pause(0.1)

    # Switch to deep research sub-tab
    nested_tc = pilot.app.query("WorkflowsTab TabbedContent").first()
    nested_tc.active = "wf-deep-research"
    await pilot.pause(0.1)

    text = richlog_text(pilot.app.query_one("#dr-output", RichLog))
    assert "not yet implemented" in text.lower()
    assert "multi-agent" in text.lower()


async def test_deep_research_button_shows_not_implemented(app):
    pilot, _ = app
    await pilot.press("5")
    await pilot.pause(0.1)

    nested_tc = pilot.app.query("WorkflowsTab TabbedContent").first()
    nested_tc.active = "wf-deep-research"
    await pilot.pause(0.1)

    await pilot.click("#dr-btn")
    await pilot.pause(0.1)

    text = richlog_text(pilot.app.query_one("#dr-output", RichLog))
    assert "Not yet implemented" in text


async def test_deep_research_with_question(app):
    pilot, _ = app
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
