"""Play - the front page: launch, watch, and see what the runtime actually did.

Two things this page is responsible for beyond the Play button:

* **Observed vs requested.** Settings say what was asked for; the runtime
  reports what it did. The renderer clamps the internal scale, widescreen can
  fall back, the overlay tier can drop to the interpreter. Those are parsed out
  of the log and shown here.
* **Diagnostics warning.** A diagnostic left switched on degrades the game -
  the legacy audio path caused 146 underruns in twenty seconds and looked
  exactly like an emulation bug. If any is active it is stated here, on the
  page you cannot avoid seeing.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import Settings, active_diagnostics
from ..paths import Layout
from ..runtime import GameSession, build_plan, observed_from_log
from .common import card, dim, section
from .hero import HeroBanner
from .theme import ERROR, OK, TEXT_DIM, WARN

# "[FPS] game: 59.9 fps (1.00x) | frames: 1246"
_FPS_RE = re.compile(r"\[FPS\][^:]*:\s*([\d.]+)\s*fps.*?\(([\d.]+)x\)", re.IGNORECASE)


class PlayPage(QWidget):
    def __init__(self, layout_: Layout, settings: Settings, session: GameSession,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.layout_ = layout_
        self.settings = settings
        self.session = session
        self._observed: dict[str, str] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.hero = HeroBanner(
            "CRASH BANDICOOT 2",
            "Cortex Strikes Back  -  statically recompiled, running natively",
        )
        root.addWidget(self.hero)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 20, 28, 24)
        lay.setSpacing(14)
        root.addWidget(body, 1)

        lay.addWidget(self._controls())
        self.diag_strip = self._diag_strip()
        lay.addWidget(self.diag_strip)
        lay.addWidget(self._status())
        lay.addStretch(1)

        self.session.started.connect(self._on_started)
        self.session.finished.connect(self._on_finished)
        self.session.output.connect(self._on_output)
        self.session.failed.connect(self._on_failed)

        self.refresh()

    # -- pieces ------------------------------------------------------------
    def _controls(self) -> QWidget:
        self.play_btn = QPushButton("Play")
        self.play_btn.setObjectName("PlayButton")
        self.play_btn.setMinimumHeight(58)
        self.play_btn.clicked.connect(self._on_play)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.session.stop)

        self.relaunch_btn = QPushButton("Relaunch")
        self.relaunch_btn.setToolTip("Restart with the current settings")
        self.relaunch_btn.setEnabled(False)
        self.relaunch_btn.clicked.connect(self._on_relaunch)

        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self.play_btn)

        secondary = QWidget()
        srow = QHBoxLayout(secondary)
        srow.setContentsMargins(0, 0, 0, 0)
        srow.setSpacing(8)
        srow.addWidget(self.stop_btn)
        srow.addWidget(self.relaunch_btn)
        srow.addStretch(1)
        self.state_lbl = QLabel()
        self.state_lbl.setTextFormat(Qt.TextFormat.RichText)
        srow.addWidget(self.state_lbl)
        lay.addWidget(secondary)
        return box

    def _diag_strip(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        frame.setStyleSheet(f"QFrame#Card {{ border-color: {WARN}; }}")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 12, 16, 12)
        self.diag_lbl = QLabel()
        self.diag_lbl.setWordWrap(True)
        self.diag_lbl.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.diag_lbl)
        frame.setVisible(False)
        return frame

    def _status(self) -> QWidget:
        self.perf_lbl = QLabel("-")
        self.observed_lbl = QLabel("-")
        self.observed_lbl.setWordWrap(True)
        self.observed_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.RichText)

        return card(
            section("While running"),
            self._stat("Performance", self.perf_lbl),
            self._stat("Runtime reports", self.observed_lbl),
            dim("These are what the runtime actually did, which can differ from "
                "what was requested - the renderer clamps values it cannot honour."),
            self.notice,
        )

    def _stat(self, label: str, value: QLabel) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        name = QLabel(label)
        name.setObjectName("Dim")
        name.setFixedWidth(150)
        lay.addWidget(name)
        lay.addWidget(value, 1)
        return box

    # -- state -------------------------------------------------------------
    def refresh(self) -> None:
        names = active_diagnostics(self.settings)
        if names:
            self.diag_lbl.setText(
                f'<span style="color:{WARN}"><b>Diagnostics active:</b> '
                + ", ".join(names) +
                "</span><br>These change how the game behaves and can make it "
                "worse. Turn them off on the Advanced page for normal play."
            )
            self.diag_strip.setVisible(True)
        else:
            self.diag_strip.setVisible(False)

        if not self.layout_.has_runtime:
            plan = build_plan(self.layout_, self.settings)
            self.play_btn.setEnabled(False)
            self.notice.setText(
                f'<span style="color:{ERROR}">The recompiled game was not found.'
                f'</span><br><span style="color:{TEXT_DIM}">{plan.program}</span>'
            )
        else:
            self.play_btn.setEnabled(not self.session.running)

    def mark_settings_changed(self) -> None:
        self.refresh()
        if self.session.running:
            self.relaunch_btn.setEnabled(True)
            self.notice.setText(
                f'<span style="color:{WARN}">Settings changed - relaunch to '
                "apply.</span>")

    # -- actions -----------------------------------------------------------
    def _on_play(self) -> None:
        self._observed.clear()
        self.observed_lbl.setText("-")
        self.perf_lbl.setText("-")
        self.notice.setText("")
        self.session.launch(build_plan(self.layout_, self.settings))

    def _on_relaunch(self) -> None:
        self.session.stop()
        self._on_play()

    # -- session -----------------------------------------------------------
    def _on_started(self) -> None:
        self.state_lbl.setText(f'<span style="color:{OK}">Running</span>')
        self.play_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.relaunch_btn.setEnabled(True)

    def _on_finished(self, code: int) -> None:
        colour = TEXT_DIM if code == 0 else ERROR
        self.state_lbl.setText(
            f'<span style="color:{colour}">Exited ({code})</span>')
        self.play_btn.setEnabled(self.layout_.has_runtime)
        self.stop_btn.setEnabled(False)
        self.relaunch_btn.setEnabled(False)

    def _on_failed(self, message: str) -> None:
        self.state_lbl.setText(f'<span style="color:{ERROR}">Failed</span>')
        self.notice.setText(f'<span style="color:{ERROR}">{message}</span>')
        self.play_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def _on_output(self, line: str) -> None:
        m = _FPS_RE.search(line)
        if m:
            fps, speed = m.group(1), m.group(2)
            colour = OK if 0.97 <= float(speed) <= 1.03 else WARN
            self.perf_lbl.setText(
                f'{fps} fps  <span style="color:{colour}">({speed}x speed)</span>')
            self.perf_lbl.setTextFormat(Qt.TextFormat.RichText)
            return

        found = observed_from_log(line)
        if found:
            label, value = found
            self._observed[label] = value
            self.observed_lbl.setText(
                "&nbsp; ".join(f'{k}: <b>{v}</b>'
                               for k, v in sorted(self._observed.items())))
