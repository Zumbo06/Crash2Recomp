"""A vertical checklist showing where a multi-stage job has got to."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..theme import ACCENT, ERROR, OK, TEXT_DIM

PENDING, ACTIVE, DONE, FAILED = "pending", "active", "done", "failed"

_MARK = {PENDING: "○", ACTIVE: "◐", DONE: "●", FAILED: "✕"}
_COLOR = {PENDING: TEXT_DIM, ACTIVE: ACCENT, DONE: OK, FAILED: ERROR}


class StepList(QWidget):
    def __init__(self, steps: list[tuple[str, str]], parent=None):
        """``steps`` is a list of ``(key, label)`` pairs."""
        super().__init__(parent)
        self._labels: dict[str, QLabel] = {}
        self._text: dict[str, str] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(7)
        for key, label in steps:
            w = QLabel()
            w.setTextFormat(Qt.TextFormat.RichText)
            self._labels[key] = w
            self._text[key] = label
            lay.addWidget(w)
            self.set_state(key, PENDING)
        lay.addStretch(1)

    def set_state(self, key: str, state: str, note: str = "") -> None:
        w = self._labels.get(key)
        if w is None:
            return
        suffix = f' <span style="color:{TEXT_DIM}">- {note}</span>' if note else ""
        w.setText(
            f'<span style="color:{_COLOR[state]};font-size:14px">{_MARK[state]}</span>'
            f'&nbsp;&nbsp;<span style="color:{_COLOR[state] if state != PENDING else TEXT_DIM}">'
            f"{self._text[key]}</span>{suffix}"
        )

    def reset(self) -> None:
        for key in self._labels:
            self.set_state(key, PENDING)
