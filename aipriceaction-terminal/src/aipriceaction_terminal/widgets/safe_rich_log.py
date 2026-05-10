"""RichLog subclass that buffers writes while the widget is hidden."""

from __future__ import annotations

from textual.events import Resize
from textual.widgets import RichLog


class SafeRichLog(RichLog):
    """RichLog that buffers writes while the widget is hidden (display: none).

    When a TabPane with display: none contains a RichLog, its
    scrollable_content_region width becomes 0. Any write() calls while
    hidden render content at min_width (1 char), permanently corrupting
    the stored line strips. This subclass buffers string writes while
    hidden and replays them once the widget becomes visible again.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._write_buffer: list[str] = []

    def on_resize(self, event: Resize) -> None:
        super().on_resize(event)
        self._flush_buffer()

    def write(
        self,
        content,
        width=None,
        expand=False,
        shrink=True,
        scroll_end=None,
        animate=False,
    ):
        if self.scrollable_content_region.width > 1:
            self._flush_buffer()
            super().write(content, width=width, expand=expand, shrink=shrink, scroll_end=scroll_end, animate=animate)
        elif isinstance(content, str):
            self._write_buffer.append(content)

    def _flush_buffer(self) -> None:
        if self._write_buffer and self.scrollable_content_region.width > 1:
            for text in self._write_buffer:
                super().write(text)
            self._write_buffer.clear()
