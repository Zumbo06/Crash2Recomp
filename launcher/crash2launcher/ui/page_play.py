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
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import Settings, active_diagnostics, diagnostic_label
from ..paths import Layout
from ..runtime import GameSession, build_plan, observed_from_log
from ..version import STATUS, VERSION
from .common import card, dim, section, set_status, stat_row
from .dialogs import confirm
from .play_scene import PlayButton, PlayScene, REFERENCE_WIDTH, StatusBadge, StatusPanel
from .theme import ERROR, OK, PLAY_GREEN, WARN

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


class PlayPage(PlayScene):
    # (exit code, plain-language explanation). The window listens and brings
    # the Log page forward - a crash report is useless if the log is two
    # clicks away and the player does not know it exists.
    crashed = Signal(int, str)
    setup_requested = Signal()

    def __init__(self, layout_: Layout, settings: Settings, session: GameSession,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.layout_ = layout_
        self.settings = settings
        self.session = session
        self._observed: dict[str, str] = {}
        self._relaunch_pending = False
        self._launching = False
        self._missing_runtime = False

        self.subtitle = QLabel("Unofficial fan recompilation for PC", self)
        self.subtitle.setObjectName("PlaySubtitle")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.play_btn = PlayButton(self.artwork, self)
        self.play_btn.clicked.connect(self._on_play)
        self.secondary = self._controls()
        self.diag_strip = self._diag_strip()
        self.diag_strip.setParent(self)
        self.status_panel = self._status_panel()
        self.details = QDialog(self.window())
        self.details.setObjectName("PlayDetails")
        self.details.setWindowTitle("Controls & session details")
        self.details.resize(610, 430)
        details_layout = QVBoxLayout(self.details)
        details_layout.addWidget(section("In-game controls"))
        details_layout.addWidget(dim(
            "Home or Guide / Start+Select: pause menu with restart, aspect ratio, "
            "image fit and quick save/load.\n\n"
            "F5: quick save    F9: quick load    F7: save slots\n"
            "F8: rewind    F: FPS readout"))
        details_layout.addWidget(self._status())
        details_layout.addWidget(dim(STATUS))
        details_layout.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.details.close)
        details_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
        self.footer = QLabel("WINDOWS PC   /   Unofficial fan project", self)
        self.footer.setObjectName("PlayFooter")
        self.preview_note = QLabel("Preview build · Some sound and graphical issues remain", self)
        self.preview_note.setObjectName("PlayFooter")
        self.preview_note.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.session.started.connect(self._on_started)
        self.session.finished.connect(self._on_finished)
        self.session.output.connect(self._on_output)
        self.session.failed.connect(self._on_failed)

        self.refresh()

    # -- pieces ------------------------------------------------------------
    def _controls(self) -> QWidget:
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)

        self.relaunch_btn = QPushButton("Relaunch")
        self.relaunch_btn.setToolTip("Restart with the current settings")
        self.relaunch_btn.setEnabled(False)
        self.relaunch_btn.clicked.connect(self._on_relaunch)

        secondary = QWidget(self)
        srow = QHBoxLayout(secondary)
        srow.setContentsMargins(0, 0, 0, 0)
        srow.setSpacing(8)
        srow.addWidget(self.stop_btn)
        srow.addWidget(self.relaunch_btn)
        self.details_btn = QPushButton("Controls && details")
        self.details_btn.clicked.connect(self._show_details)
        srow.addWidget(self.details_btn)
        for button in (self.stop_btn, self.relaunch_btn, self.details_btn):
            button.setObjectName("PlaySecondary")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        srow.addStretch(1)
        return secondary

    def _show_details(self) -> None:
        self.details.show()
        self.details.raise_()
        self.details.activateWindow()

    def _status_panel(self) -> QFrame:
        panel = StatusPanel(self)
        panel.setObjectName("PlayStatusPanel")
        row = QHBoxLayout(panel)
        row.setContentsMargins(22, 18, 22, 18)
        row.setSpacing(22)
        metadata = QWidget()
        grid = QGridLayout(metadata)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(5)
        self.version_lbl = QLabel(VERSION)
        self.renderer_lbl = QLabel()
        self.slot_lbl = QLabel()
        self.input_lbl = QLabel()
        self.input_lbl.setToolTip("Configured input mode. Controller connection is detected by the game.")
        self.renderer_lbl.setToolTip("Requested renderer; session details show values reported by the runtime.")
        for i, (name, value) in enumerate((
            ("Version", self.version_lbl), ("Renderer", self.renderer_lbl),
            ("Save slot", self.slot_lbl), ("Input", self.input_lbl),
        )):
            label = QLabel(name)
            label.setObjectName("PlayMetaLabel")
            value.setObjectName("PlayMetaValue")
            grid.addWidget(label, i, 0)
            grid.addWidget(value, i, 1)
        row.addWidget(metadata)
        divider = QFrame()
        divider.setObjectName("PlayDivider")
        divider.setFixedWidth(1)
        row.addWidget(divider)
        right = QVBoxLayout()
        right.setSpacing(5)
        self.state_lbl = QLabel()
        self.state_lbl.setProperty("playHeading", True)
        self.state_lbl.setWordWrap(True)
        self.ready_hint = QLabel()
        self.ready_hint.setObjectName("PlayMetaLabel")
        self.ready_hint.setWordWrap(True)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        # Process errors may contain filesystem paths or arbitrary output.
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.ready_bar = QProgressBar()
        self.ready_bar.setObjectName("PlayReadyBar")
        self.ready_bar.setTextVisible(False)
        self.ready_bar.setFixedHeight(9)
        self.setup_btn = QPushButton("Open Setup")
        self.setup_btn.setObjectName("PlaySecondary")
        self.setup_btn.clicked.connect(self.setup_requested.emit)
        heading_row = QHBoxLayout()
        heading_row.setSpacing(10)
        self.status_badge = StatusBadge()
        heading_row.addWidget(self.status_badge)
        heading_row.addWidget(self.state_lbl, 1)
        right.addLayout(heading_row)
        right.addWidget(self.ready_hint)
        right.addWidget(self.notice)
        right.addWidget(self.ready_bar)
        right.addWidget(self.setup_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        row.addLayout(right, 1)
        return panel

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
        return card(
            section("While running"),
            stat_row("Performance", self.perf_lbl),
            stat_row("Runtime reports", self.observed_lbl),
            dim("These are what the runtime actually did, which can differ from "
                "what was requested - the renderer clamps values it cannot honour."),
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_children()

    def _position_children(self) -> None:
        if not hasattr(self, "status_panel"):
            return
        w, h = self.width(), self.height()
        scale = w / REFERENCE_WIDTH
        x = round(84 * scale)
        button_w = max(220, round(458 * scale))
        button_h = max(54, round(115 * scale))
        compact = w < 800
        if self.subtitle.property("compact") != compact:
            self.subtitle.setProperty("compact", compact)
            self.subtitle.style().unpolish(self.subtitle)
            self.subtitle.style().polish(self.subtitle)
        self.subtitle.setGeometry(x, round(334 * scale), max(button_w, round(470 * scale)),
                                  max(13, round(36 * scale)))
        self.play_btn.setGeometry(x, round(373 * scale), button_w, button_h)
        self.secondary.setGeometry(x, self.play_btn.geometry().bottom() + 8,
                                   max(330, button_w), 34)
        panel_x = round(w * .29) if w >= 1100 else 22
        panel_w = w - panel_x - 22
        self.status_panel.setFixedWidth(panel_w)
        self.status_panel.layout().activate()
        panel_h = max(144, self.status_panel.sizeHint().height())
        # Completely cover the concept's status box, including its border.
        # On smaller windows the native panel grows beyond it for legibility.
        panel_y = min(round(735 * scale), h - panel_h - 44)
        panel_h = max(panel_h, round(896 * scale) - panel_y)
        self.status_panel.setGeometry(panel_x, panel_y, panel_w, panel_h)
        # Warnings stay beside the logo, clear of the Play/Stop controls.
        diag_x = max(self.play_btn.geometry().right() + 16, round(w * .60))
        diag_w = w - diag_x - 22
        self.diag_strip.setFixedWidth(diag_w)
        diag_h = max(64, self.diag_strip.layout().totalHeightForWidth(diag_w))
        self.diag_strip.setGeometry(diag_x, 22, diag_w, diag_h)
        self.footer.setGeometry(22, h - 32, w - 44, 24)
        self.preview_note.setVisible(w >= 1000)
        self.preview_note.setGeometry(w // 2, h - 32, w // 2 - 22, 24)

    def _set_readiness(self, title: str, hint: str, tone: str = "Ok", ready: bool = True) -> None:
        set_status(self.state_lbl, tone, title)
        self.ready_hint.setText(hint)
        self.ready_bar.setValue(100 if ready else 0)
        self.status_badge.ready = ready
        self.status_badge.colour = {"Warn": WARN, "Error": ERROR}.get(tone, PLAY_GREEN)
        self.status_badge.setAccessibleName(title)
        self.status_badge.update()
        self._position_children()

    # -- state -------------------------------------------------------------
    def refresh(self) -> None:
        self.renderer_lbl.setText({"opengl": "OpenGL", "vulkan": "Vulkan",
                                   "software": "Software"}.get(self.settings.renderer,
                                                                self.settings.renderer))
        self.slot_lbl.setText(f"{self.settings.quick_save_slot:02d}")
        self.input_lbl.setText("Keyboard + pad" if self.settings.merge_all_input else "Keyboard")
        running = self.session.running
        self.stop_btn.setVisible(running)
        self.relaunch_btn.setVisible(running)
        self.setup_btn.setVisible(not self.layout_.has_runtime)
        names = active_diagnostics(self.settings) if self.settings.developer_mode else []
        if names:
            labels = ", ".join(diagnostic_label(n) for n in names)
            summary = labels if len(names) <= 2 else f"{len(names)} options enabled"
            set_status(
                self.diag_lbl, "Warn",
                f"Diagnostics active: {summary}. "
                "Turn them off in Advanced for normal play.")
            self.diag_lbl.setToolTip(labels + ". These change game behavior and can make it worse.")
            self.diag_strip.setVisible(True)
        else:
            self.diag_strip.setVisible(False)

        if not self.layout_.has_runtime:
            self._missing_runtime = True
            self.play_btn.setEnabled(False)
            self.play_btn.setToolTip("Open Setup to build the game first")
            self._set_readiness("Build the game first", "Choose your disc image in Setup.",
                                "Warn", ready=False)
        else:
            if self._missing_runtime:
                self._missing_runtime = False
                self._set_notice("", "")
            self.play_btn.setEnabled(not running and not self._launching)
            self.play_btn.setToolTip("Launch with your current settings")
            if not running and not self._launching:
                self._set_readiness("Ready to play", "Game build found · Settings apply on launch")
        self.notice.setVisible(bool(self.notice.text()))
        self._position_children()

    def _set_notice(self, tone: str, text: str) -> None:
        set_status(self.notice, tone, text)
        self.notice.setVisible(bool(text))
        self._position_children()

    def mark_settings_changed(self) -> None:
        self.refresh()
        if self.session.running:
            self.relaunch_btn.setEnabled(True)
            self._set_notice("Warn", "Settings changed — relaunch to apply.")

    def set_layout(self, layout_: Layout) -> None:
        """Adopt a freshly resolved layout - after a build the runtime exists
        and its real name is known, which decides whether Play is enabled."""
        self.layout_ = layout_
        self.refresh()

    # -- actions -----------------------------------------------------------
    def _on_play(self) -> None:
        if self.session.running or self._launching or not self.layout_.has_runtime:
            return
        self._launching = True
        self.play_btn.setEnabled(False)
        self.play_btn.setText("STARTING")
        self._observed.clear()
        self.observed_lbl.setText("-")
        self.perf_lbl.setText("-")
        self._set_notice("", "")
        self._set_readiness("Starting the game", "Preparing your session…")
        self.session.launch(build_plan(self.layout_, self.settings))

    def _on_relaunch(self) -> None:
        # Starting again immediately raced the shutdown: stop() falls back to
        # kill() after 4 s without waiting, and QProcess.start() on a process
        # that is still running fails with a console warning and no UI change.
        # Wait for the exit, then let _on_finished start the new run.
        self._relaunch_pending = True
        self.relaunch_btn.setEnabled(False)
        self._set_readiness("Restarting the game", "Applying your current settings…")
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
        self._launching = False
        self._set_readiness("Game running", "Enjoy the adventure. Home opens the pause menu.")
        self.play_btn.setText("RUNNING")
        self.play_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.relaunch_btn.setEnabled(True)
        self.stop_btn.show()
        self.relaunch_btn.show()

    def _on_finished(self, code: int) -> None:
        self._launching = False
        self.play_btn.setText("PLAY")
        self.play_btn.setEnabled(self.layout_.has_runtime)
        self.stop_btn.setEnabled(False)
        self.relaunch_btn.setEnabled(False)
        self.stop_btn.hide()
        self.relaunch_btn.hide()

        if self._relaunch_pending:
            self._relaunch_pending = False
            self._on_play()
            return

        if not _is_crash(code):
            self.refresh()
            self._set_notice("", "")
            return

        # A crash used to read "Exited (-1073741819)" in small grey text, which
        # tells a player nothing. Name it, and put the log within reach.
        self._set_readiness("The game stopped", "Open the Log page for details.",
                            "Error", ready=False)
        self.crashed.emit(code, _exit_explanation(code))

    def _on_failed(self, message: str) -> None:
        self._launching = False
        self._relaunch_pending = False
        self.play_btn.setText("PLAY")
        self._set_readiness("Couldn't start the game", "Check the Log page and try again.",
                            "Error", ready=False)
        self._set_notice("Error", message)
        self.play_btn.setEnabled(self.layout_.has_runtime)
        self.stop_btn.setEnabled(False)
        self.relaunch_btn.setEnabled(False)
        self.stop_btn.hide()
        self.relaunch_btn.hide()

    def _on_output(self, line: str) -> None:
        m = _FPS_RE.search(line)
        if m:
            fps, speed = m.group(1), m.group(2)
            colour = OK if 0.97 <= float(speed) <= 1.03 else WARN
            self.perf_lbl.setText(
                f'{fps} fps  <span style="color:{colour}">({speed}x speed)</span>')
            self.perf_lbl.setTextFormat(Qt.TextFormat.RichText)
            self.ready_hint.setText(f"{fps} fps · {speed}x speed · Home for pause menu")
            return

        found = observed_from_log(line)
        if found:
            label, value = found
            self._observed[label] = value
            self.observed_lbl.setText(
                "&nbsp; ".join(f'{k}: <b>{v}</b>'
                               for k, v in sorted(self._observed.items())))
