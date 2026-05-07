"""Tests for AIPriceActionApp — tab switching, quit, focus, and help."""

from unittest.mock import patch

import pytest
from textual.widgets import TabbedContent

from aipriceaction_terminal.app import AIPriceActionApp


@pytest.fixture()
async def app(mock_builder, mock_client):
    """Mount the app with AIContextBuilder and AIPriceAction patched."""
    with (
        patch("aipriceaction.AIContextBuilder", return_value=mock_builder),
        patch("aipriceaction.AIPriceAction", return_value=mock_client),
    ):
        async with AIPriceActionApp().run_test() as pilot:
            yield pilot, mock_builder


async def test_app_mounts_with_all_tabs(app):
    pilot, _ = app
    tabs = pilot.app.query_one(TabbedContent)
    pane_ids = [pane.id for pane in tabs.query("TabPane").results()]
    assert "chat" in pane_ids
    assert "tickers-vn" in pane_ids
    assert "tickers-crypto" in pane_ids
    assert "tickers-global" in pane_ids
    assert "workflows" in pane_ids
    assert "settings" in pane_ids


async def test_app_default_reactive_state(app):
    pilot, _ = app
    assert pilot.app.ticker == "VNINDEX"
    assert pilot.app.interval == "1D"
    assert pilot.app.language == "en"


async def test_app_builder_instantiated_on_mount(app):
    pilot, builder = app
    assert pilot.app.builder is builder


async def test_action_switch_tab_with_keys(app):
    pilot, _ = app
    tabs = pilot.app.query_one(TabbedContent)

    await pilot.press("1")
    assert tabs.active == "chat"

    await pilot.press("2")
    assert tabs.active == "tickers-vn"

    await pilot.press("5")
    assert tabs.active == "workflows"

    await pilot.press("6")
    assert tabs.active == "settings"


async def test_action_switch_tab_with_numbers_3_4(app):
    pilot, _ = app
    tabs = pilot.app.query_one(TabbedContent)

    await pilot.press("3")
    assert tabs.active == "tickers-crypto"

    await pilot.press("4")
    assert tabs.active == "tickers-global"


async def test_action_confirm_quit_single_press(app):
    pilot, _ = app
    with patch.object(pilot.app, "exit") as mock_exit:
        await pilot.press("ctrl+q")
        await pilot.pause(0.1)
        mock_exit.assert_not_called()

        notifications = pilot.app._notifications
        assert any("again to quit" in str(n).lower() for n in notifications)


async def test_action_confirm_quit_double_press(app):
    pilot, _ = app
    with patch.object(pilot.app, "exit") as mock_exit:
        await pilot.press("ctrl+q")
        await pilot.press("ctrl+q")
        await pilot.pause(0.1)
        mock_exit.assert_called_once()


async def test_action_confirm_quit_timeout(app):
    pilot, _ = app
    with patch.object(pilot.app, "exit") as mock_exit:
        await pilot.press("ctrl+q")
        await pilot.pause(0.1)
        mock_exit.assert_not_called()

        # Wait longer than 2 second timeout
        await pilot.pause(2.5)

        await pilot.press("ctrl+q")
        await pilot.pause(0.1)
        mock_exit.assert_not_called()

        # Two warnings should have been shown
        notifications = [str(n) for n in pilot.app._notifications]
        warning_count = sum(1 for n in notifications if "again to quit" in n.lower())
        assert warning_count == 2


async def test_action_focus_none(app):
    pilot, _ = app
    # Focus something first
    await pilot.click("#chat-input")
    assert pilot.app.focused is not None

    await pilot.press("escape")
    assert pilot.app.focused is None


async def test_action_focus_first_input_in_chat(app):
    pilot, _ = app
    # Enter in the default chat tab should focus chat-input
    await pilot.press("enter")
    await pilot.pause(0.1)
    focused = pilot.app.focused
    assert focused is not None
    assert focused.id == "chat-input"


async def test_action_focus_first_input_in_workflows(app):
    pilot, _ = app
    await pilot.press("5")
    await pilot.pause(0.1)

    await pilot.press("enter")
    await pilot.pause(0.1)
    focused = pilot.app.focused
    assert focused is not None
    assert focused.id == "ticker-input"


async def test_action_show_help(app):
    pilot, _ = app
    await pilot.press("?")
    await pilot.pause(0.1)

    notifications = [str(n) for n in pilot.app._notifications]
    assert any("Keyboard Shortcuts" in n for n in notifications)
