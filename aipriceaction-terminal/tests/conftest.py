"""Shared fixtures for aipriceaction-terminal tests."""

from unittest.mock import MagicMock

import pytest
from textual.widgets import RichLog


@pytest.fixture()
def mock_builder():
    """Return a mock AIContextBuilder with a canned build response."""
    builder = MagicMock()
    builder.build.return_value = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
    return builder


def richlog_text(log: RichLog) -> str:
    """Extract plain text from a RichLog widget (strips Rich markup)."""
    parts = []
    for i in range(len(log.lines)):
        try:
            strip = log.lines[i]
            text = "".join(seg.text for seg in strip._segments)
            parts.append(text)
        except (AttributeError, IndexError):
            pass
    return "\n".join(parts)
