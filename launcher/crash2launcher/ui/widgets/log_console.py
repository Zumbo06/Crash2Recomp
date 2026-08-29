"""A bounded, append-only log view.

Build logs run to tens of thousands of lines. Keeping them all in a
QPlainTextEdit costs memory and makes the widget crawl, so the document is
capped and the view only auto-scrolls while the user is already at the bottom -
scrolling up to read something should not get yanked away on the next line.
"""

from __future__ import annotations

import re

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QPlainTextEdit

# Tools emit ANSI colour even when redirected; strip it rather than render it.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


class LogConsole(QPlainTextEdit):
    def __init__(self, parent=None, max_lines: int = 5000):
        super().__init__(parent)
        self.setObjectName("LogConsole")
        self.setReadOnly(True)
        self.setMaximumBlockCount(max_lines)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

    def append_line(self, text: str) -> None:
        at_bottom = self._at_bottom()
        self.appendPlainText(_ANSI_RE.sub("", text.rstrip()))
        if at_bottom:
            self.moveCursor(QTextCursor.MoveOperation.End)
            bar = self.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        # A few pixels of slack so "close enough to the bottom" still follows.
        return bar.value() >= bar.maximum() - 4

    def clear_log(self) -> None:
        self.clear()
