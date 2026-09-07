"""Log - everything the runtime prints, with a filter.

The runtime is chatty and the useful lines are buried: BIOS handoff, renderer
setup, overlay capture, fps telemetry. A substring filter plus a "hide fps"
toggle is enough to find them, and costs far less than a structured viewer.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .common import heading
from .dialogs import confirm, tell
from .theme import PAGE_MARGINS
from .widgets.log_console import LogConsole


class LogPage(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._lines: list[str] = []
        self._max_kept = 5000

        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(12)
        root.addWidget(heading("Log", "Output from the running game."))

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter (substring, case-insensitive)...")
        self.filter.textChanged.connect(self._rebuild)

        self.hide_fps = QCheckBox("Hide fps telemetry")
        self.hide_fps.setChecked(True)
        self.hide_fps.toggled.connect(self._rebuild)

        # The runtime writes no log file of its own - this capture is the only
        # record a player has, so it has to be possible to get it out.
        save = QPushButton("Save to file...")
        save.clicked.connect(self._on_save)

        clear = QPushButton("Clear")
        clear.clicked.connect(self._on_clear)

        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.filter, 1)
        row.addWidget(self.hide_fps)
        row.addWidget(save)
        row.addWidget(clear)
        root.addWidget(bar)

        self.console = LogConsole()
        root.addWidget(self.console, 1)

    # -- api ---------------------------------------------------------------
    def append(self, line: str) -> None:
        self._lines.append(line)
        if len(self._lines) > self._max_kept:
            # Drop in a chunk rather than one line at a time.
            del self._lines[: self._max_kept // 5]
            self._rebuild()
            return
        if self._passes(line):
            self.console.append_line(line)

    def clear(self) -> None:
        self._lines.clear()
        self.console.clear_log()

    # -- actions -----------------------------------------------------------
    def _on_clear(self) -> None:
        if not self._lines:
            return
        if confirm(self, "Clear the log?",
                   "The %d captured lines are discarded. Save them first if "
                   "you are reporting a problem." % len(self._lines), "Clear"):
            self.clear()

    def _on_save(self) -> None:
        """Write the FULL capture, not the filtered view - a filter is for
        reading, and a report needs everything."""
        if not self._lines:
            tell(self, "Nothing to save", "The log is empty.")
            return
        default = str(Path.home() / ("crash2-log-%s.txt"
                                     % datetime.now().strftime("%Y%m%d-%H%M%S")))
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Save the log", default, "Text files (*.txt);;All files (*)")
        if not chosen:
            return
        try:
            Path(chosen).write_text("\n".join(self._lines) + "\n",
                                    encoding="utf-8", errors="replace")
        except OSError as exc:
            tell(self, "Could not save the log", str(exc), error=True)
        else:
            tell(self, "Log saved", chosen)

    # -- internals ---------------------------------------------------------
    def _passes(self, line: str) -> bool:
        if self.hide_fps.isChecked() and line.lstrip().startswith("[FPS]"):
            return False
        needle = self.filter.text().strip().lower()
        return needle in line.lower() if needle else True

    def _rebuild(self) -> None:
        self.console.clear_log()
        for line in self._lines:
            if self._passes(line):
                self.console.append_line(line)
