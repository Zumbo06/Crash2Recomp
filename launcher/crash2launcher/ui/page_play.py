"""Play - launch, stop, relaunch, and watch what the runtime reports back.

Relaunch is the important control here. The whole point of this launcher is to
change a setting and see its effect immediately, so stopping and restarting with
the new environment is one click rather than a trip to the terminal.

Live status is parsed out of the runtime's own stdout rather than guessed: the
renderer clamps the requested internal scale, so the only honest source for the
*effective* scale is the line it prints at startup.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import Settings
from ..paths import Layout
from ..runtime import GameSession, build_plan, effective_scale_from_log
from .common import card, dim, heading, section

# "[FPS] game: 59.9 fps (1.00x) | frames: 1246"
_FPS_RE = re.compile(r"\[FPS\][^:]*:\s*([\d.]+)\s*fps.*?frames:\s*(\d+)", re.IGNORECASE)


class PlayPage(QWidget):
    request_log = Signal()

    def __init__(self, layout_: Layout, settings: Settings, session: GameSession,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.layout_ = layout_
        self.settings = settings
        self.session = session
        self._dirty = False

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        root.addWidget(heading(
            "Crash Bandicoot 2",
            "Statically recompiled - running as native code, not emulated.",
        ))

        # --- controls -----------------------------------------------------
        self.play_btn = QPushButton("Play")
        self.play_btn.setObjectName("PlayButton")
        self.play_btn.clicked.connect(self._on_play)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.session.stop)

        self.relaunch_btn = QPushButton("Relaunch")
        self.relaunch_btn.setToolTip("Restart the game with the current settings")
        self.relaunch_btn.setEnabled(False)
        self.relaunch_btn.clicked.connect(self._on_relaunch)

        buttons = QWidget()
        brow = QHBoxLayout(buttons)
        brow.setContentsMargins(0, 0, 0, 0)
        brow.setSpacing(8)
        brow.addWidget(self.stop_btn)
        brow.addWidget(self.relaunch_btn)
        brow.addStretch(1)

        root.addWidget(self.play_btn)
        root.addWidget(buttons)

        # --- status -------------------------------------------------------
        self.state_lbl = QLabel("Not running")
        self.state_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.fps_lbl = QLabel("-")
        self.scale_lbl = QLabel("-")
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.RichText)

        root.addWidget(card(
            section("Status"),
            self._stat("State", self.state_lbl),
            self._stat("Performance", self.fps_lbl),
            self._stat("Effective internal scale", self.scale_lbl),
            self.notice,
        ))

        # --- what will run ------------------------------------------------
        self.cmd_lbl = QLabel()
        self.cmd_lbl.setObjectName("Dim")
        self.cmd_lbl.setWordWrap(True)
        self.cmd_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(card(section("Launch command"), self.cmd_lbl))

        root.addStretch(1)

        self.session.started.connect(self._on_started)
        self.session.finished.connect(self._on_finished)
        self.session.output.connect(self._on_output)
        self.session.failed.connect(self._on_failed)

        self.refresh()

    # -- helpers -----------------------------------------------------------
    def _stat(self, label: str, value: QLabel) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        name = QLabel(label)
        name.setObjectName("Dim")
        name.setFixedWidth(190)
        lay.addWidget(name)
        lay.addWidget(value, 1)
        return box

    def refresh(self) -> None:
        """Recompute the preview and button states from current settings."""
        plan = build_plan(self.layout_, self.settings)
        env_bits = " ".join(f"{k}={v}" for k, v in sorted(plan.env.items()))
        self.cmd_lbl.setText(
            (f'<span style="color:#f07e1e">{env_bits}</span><br>' if env_bits else "")
            + plan.as_command()
        )

        if not self.layout_.has_runtime:
            self.play_btn.setEnabled(False)
            self.notice.setText(
                '<span style="color:#e8595b">The recompiled game was not found.</span><br>'
                f'<span style="color:#9aa2b1">Looked for: {plan.program}</span>'
            )
        else:
            self.play_btn.setEnabled(not self.session.running)

    def mark_settings_changed(self) -> None:
        """A setting changed - offer a relaunch if the game is already up."""
        self._dirty = True
        self.refresh()
        if self.session.running:
            self.relaunch_btn.setEnabled(True)
            self.notice.setText(
                '<span style="color:#e8b339">Settings changed - relaunch to apply.</span>'
            )

    # -- actions -----------------------------------------------------------
    def _on_play(self) -> None:
        self.fps_lbl.setText("-")
        self.scale_lbl.setText("-")
        self.notice.setText("")
        self.session.launch(build_plan(self.layout_, self.settings))

    def _on_relaunch(self) -> None:
        self.session.stop()
        self._on_play()

    # -- session signals ---------------------------------------------------
    def _on_started(self) -> None:
        self.state_lbl.setText('<span style="color:#4ac97e">Running</span>')
        self.play_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.relaunch_btn.setEnabled(True)
        self._dirty = False

    def _on_finished(self, code: int) -> None:
        colour = "#9aa2b1" if code == 0 else "#e8595b"
        self.state_lbl.setText(
            f'<span style="color:{colour}">Exited (code {code})</span>'
        )
        self.play_btn.setEnabled(self.layout_.has_runtime)
        self.stop_btn.setEnabled(False)
        self.relaunch_btn.setEnabled(False)

    def _on_failed(self, message: str) -> None:
        self.state_lbl.setText('<span style="color:#e8595b">Failed to start</span>')
        self.notice.setText(f'<span style="color:#e8595b">{message}</span>')
        self.play_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def _on_output(self, line: str) -> None:
        m = _FPS_RE.search(line)
        if m:
            self.fps_lbl.setText(f"{m.group(1)} fps  -  {int(m.group(2)):,} frames")
            return

        scale = effective_scale_from_log(line)
        if scale is not None:
            requested = self.settings.supersampling
            if scale == requested:
                self.scale_lbl.setText(f"{scale}x")
            else:
                # The renderer clamped or fell back; say so rather than pretend.
                self.scale_lbl.setText(
                    f'<span style="color:#e8b339">{scale}x  '
                    f"(requested {requested}x)</span>"
                )
                self.scale_lbl.setTextFormat(Qt.TextFormat.RichText)
