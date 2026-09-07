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

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import Settings, active_diagnostics, diagnostic_label
from ..paths import Layout
from ..runtime import GameSession, build_plan, observed_from_log
from ..version import STATUS
from .common import card, dim, section, set_status, stat_row
from .dialogs import confirm
from .hero import HeroBanner
from .theme import ERROR, OK, PAGE_MARGINS, TEXT_DIM, WARN

# "[FPS] game: 59.9 fps (1.00x) | frames: 1246"
_FPS_RE = re.compile(r"\[FPS\][^:]*:\s*([\d.]+)\s*fps.*?\(([\d.]+)x\)", re.IGNORECASE)

# Windows reports a fatal exception as the negative of its NTSTATUS. These are
# the ones a player can actually hit; anything else falls back to the number.
_EXIT_REASONS = {
    -1073741819: ("access violation",
                  "The game read or wrote memory it does not own."),
    -1073741795: ("illegal instruction",
                  "The game tried to run something that is not code."),
    -1073741676: ("divide by zero", "The game divided by zero."),
    -1073740791: ("stack overflow", "The game ran out of stack space."),
    -1073741571: ("stack overflow", "The game ran out of stack space."),
}


def _is_crash(code: int) -> bool:
    """True for an abnormal termination rather than a clean or asked-for exit.
    A stop from the launcher terminates the process, so treat 1 and 15 as ours
    rather than reporting them to the player as a crash."""
    return code not in (0, 1, 15)


def _exit_explanation(code: int) -> str:
    named = _EXIT_REASONS.get(code)
    if named:
        return "%s (%s). This is a bug in the recompilation, not in your disc." % (
            named[1], named[0])
    return ("The game stopped unexpectedly with code %d." % code)


class PlayPage(QWidget):
    # (exit code, plain-language explanation). The window listens and brings
    # the Log page forward - a crash report is useless if the log is two
    # clicks away and the player does not know it exists.
    crashed = Signal(int, str)

    def __init__(self, layout_: Layout, settings: Settings, session: GameSession,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.layout_ = layout_
        self.settings = settings
        self.session = session
        self._observed: dict[str, str] = {}
        self._relaunch_pending = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.hero = HeroBanner(
            "CRASH BANDICOOT 2",
            "Cortex Strikes Back, recompiled to run natively",
        )
        root.addWidget(self.hero)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(14)
        root.addWidget(body, 1)

        # Set expectations before the first launch, not after a player hits a
        # glitch and assumes their disc is bad.
        lay.addWidget(card(dim(STATUS), tone="flat"))
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
        self.stop_btn.clicked.connect(self._on_stop)

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
        # The in-game hotkeys were previously undiscoverable - none of them
        # appeared anywhere in the launcher, so players had no way to learn
        # that the pause menu or the quick slots existed.
        lay.addWidget(dim(
            "In game — Home (or Guide / Start+Select on a pad): pause menu "
            "with restart, aspect, image fit and quick save/load. "
            "F5 quick save, F9 quick load, F7 save slots, F8 rewind, "
            "F toggles the FPS readout."
        ))
        return box

    def _diag_strip(self) -> QFrame:
        # tone= rather than setStyleSheet: a widget-level sheet resets style
        # inheritance for the whole subtree, so this card used to opt out of
        # every other Card rule.
        frame = card(tone="warn")
        lay = frame.layout()
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
            stat_row("Performance", self.perf_lbl),
            stat_row("Runtime reports", self.observed_lbl),
            dim("These are what the runtime actually did, which can differ from "
                "what was requested - the renderer clamps values it cannot honour."),
            self.notice,
        )

    # -- state -------------------------------------------------------------
    def refresh(self) -> None:
        names = active_diagnostics(self.settings)
        if names:
            set_status(
                self.diag_lbl, "Warn",
                "Diagnostics active: "
                + ", ".join(diagnostic_label(n) for n in names)
                + ". These change how the game behaves and can make it worse. "
                  "Turn them off on the Advanced page for normal play.")
            self.diag_strip.setVisible(True)
        else:
            self.diag_strip.setVisible(False)

        if not self.layout_.has_runtime:
            self.play_btn.setEnabled(False)
            set_status(self.notice, "Error",
                       "The game has not been built yet. Open Setup, choose "
                       "your disc image, and build it - that only needs doing "
                       "once.")
        else:
            self.play_btn.setEnabled(not self.session.running)

    def mark_settings_changed(self) -> None:
        self.refresh()
        if self.session.running:
            self.relaunch_btn.setEnabled(True)
            set_status(self.notice, "Warn",
                       "Settings changed - relaunch to apply.")

    # -- actions -----------------------------------------------------------
    def _on_play(self) -> None:
        self._observed.clear()
        self.observed_lbl.setText("-")
        self.perf_lbl.setText("-")
        self.notice.setText("")
        self.session.launch(build_plan(self.layout_, self.settings))

    def _on_relaunch(self) -> None:
        # Starting again immediately raced the shutdown: stop() falls back to
        # kill() after 4 s without waiting, and QProcess.start() on a process
        # that is still running fails with a console warning and no UI change.
        # Wait for the exit, then let _on_finished start the new run.
        self._relaunch_pending = True
        self.relaunch_btn.setEnabled(False)
        self.session.stop()

    def _on_stop(self) -> None:
        if not confirm(self, "Stop the game?",
                       "Anything since your last save is lost. The game saves "
                       "to its memory card at the usual points, and F5 makes a "
                       "quick save at any time.", "Stop"):
            return
        self.session.stop()

    # -- session -----------------------------------------------------------
    def _on_started(self) -> None:
        set_status(self.state_lbl, "Ok", "Running")
        self.play_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.relaunch_btn.setEnabled(True)

    def _on_finished(self, code: int) -> None:
        self.play_btn.setEnabled(self.layout_.has_runtime)
        self.stop_btn.setEnabled(False)
        self.relaunch_btn.setEnabled(False)

        if self._relaunch_pending:
            self._relaunch_pending = False
            self._on_play()
            return

        if code == 0:
            set_status(self.state_lbl, "", "Exited")
            self.notice.setText("")
            return

        # A crash used to read "Exited (-1073741819)" in small grey text, which
        # tells a player nothing. Name it, and put the log within reach.
        set_status(self.state_lbl, "Error", "Crashed" if _is_crash(code)
                   else "Exited (%d)" % code)
        self.crashed.emit(code, _exit_explanation(code))

    def _on_failed(self, message: str) -> None:
        set_status(self.state_lbl, "Error", "Failed to start")
        set_status(self.notice, "Error", message)
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
