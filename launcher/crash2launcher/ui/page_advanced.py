"""Advanced - diagnostics that change the runtime for MEASUREMENT.

These are deliberately kept away from the ordinary settings. One of them
(`audio_legacy`) disables the audio bridge and produced 146 underruns in a
20-second window: audible dropouts that looked exactly like an SPU bug. It had
been sitting next to Volume as though it were a quality option.

So this page states the cost of each switch, shows which are currently active,
and offers a single control to put everything back.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import (
    DIAGNOSTIC_SETTINGS,
    Settings,
    active_diagnostics,
    diagnostic_label,
    reset_diagnostics,
)
from .common import card, dim, heading, row, section, set_status
from .dialogs import confirm
from .theme import PAGE_MARGINS


class AdvancedPage(QWidget):
    changed = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self._loading = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(16)
        scroll.setWidget(body)

        lay.addWidget(heading(
            "Advanced",
            "Diagnostics for investigating problems. These change how the game "
            "behaves and some of them make it worse - they are not quality "
            "settings.",
        ))
        lay.addWidget(self._banner())
        lay.addWidget(self._audio_card())
        lay.addWidget(self._capture_card())
        lay.addWidget(self._reset_card())
        lay.addStretch(1)

        self._loading = False
        self.refresh()

    # -- pieces ------------------------------------------------------------
    def _banner(self) -> QWidget:
        frame = card(tone="warn")
        lay = frame.layout()

        text = QLabel(
            "<b>These are measurement tools, not settings.</b><br>"
            "Leave them off for normal play. If audio or video behaves oddly, "
            "check here first - an enabled diagnostic is the most likely cause."
        )
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(text)

        self.active_lbl = QLabel()
        self.active_lbl.setWordWrap(True)
        self.active_lbl.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.active_lbl)
        return frame

    def _audio_card(self) -> QWidget:
        self.audio_legacy = QCheckBox("Legacy audio path")
        self.audio_legacy.setChecked(self.settings.audio_legacy)
        self.audio_legacy.toggled.connect(self._on_audio_legacy)

        self.audio_shadow = QCheckBox("Alternate SPU mix (shadow)")
        self.audio_shadow.setChecked(self.settings.audio_shadow)
        self.audio_shadow.toggled.connect(self._on_audio_shadow)

        legacy_note = QLabel(
            "Known to cause dropouts. "
            + DIAGNOSTIC_SETTINGS["audio_legacy"]
            + " It exists only to measure the bridge against a baseline."
        )
        legacy_note.setObjectName("Error")
        legacy_note.setWordWrap(True)

        return card(
            section("Audio diagnostics"),
            self.audio_legacy,
            legacy_note,
            self.audio_shadow,
            dim(DIAGNOSTIC_SETTINGS["audio_shadow"]),
        )

    def _capture_card(self) -> QWidget:
        self.debug_port = QSpinBox()
        self.debug_port.setRange(0, 65535)
        self.debug_port.setSpecialValueText("Off")
        self.debug_port.setValue(self.settings.debug_port)
        self.debug_port.valueChanged.connect(self._on_debug_port)

        self.voice_alloc_trace = QCheckBox("Trace SPU voice allocation")
        self.voice_alloc_trace.setChecked(self.settings.voice_alloc_trace)
        self.voice_alloc_trace.toggled.connect(self._on_voice_alloc_trace)

        self.overlay_interpreter = QCheckBox("Run level code in the interpreter")
        self.overlay_interpreter.setChecked(self.settings.overlay_interpreter)
        self.overlay_interpreter.toggled.connect(self._on_overlay_interpreter)

        self.force_interpreter = QCheckBox("Run ALL game code in the interpreter")
        self.force_interpreter.setChecked(self.settings.force_interpreter)
        self.force_interpreter.toggled.connect(self._on_force_interpreter)

        self.build_lbl = QLabel()
        self.build_lbl.setWordWrap(True)
        self.build_lbl.setTextFormat(Qt.TextFormat.RichText)

        return card(
            section("Capture / debug server"),
            row("Debug server port", self.debug_port),
            dim(
                "Serves the sound, video and CPU state over TCP so a capture "
                "tool can read it while the game runs. Use 4370 unless you "
                "have a reason not to."
            ),
            self.build_lbl,
            self.voice_alloc_trace,
            dim(DIAGNOSTIC_SETTINGS["voice_alloc_trace"]),
            self.overlay_interpreter,
            dim(DIAGNOSTIC_SETTINGS["overlay_interpreter"]),
            self.force_interpreter,
            dim(DIAGNOSTIC_SETTINGS["force_interpreter"]),
        )

    def _reset_card(self) -> QWidget:
        btn = QPushButton("Reset all diagnostics")
        btn.setObjectName("Primary")
        btn.clicked.connect(self._on_reset)
        return card(
            section("Back to normal"),
            dim("Turns every diagnostic off and restores normal playback."),
            btn,
        )

    # -- state -------------------------------------------------------------
    def refresh(self) -> None:
        names = active_diagnostics(self.settings)
        if names:
            set_status(self.active_lbl, "Warn",
                       "Active now: " + ", ".join(diagnostic_label(n) for n in names))
        else:
            set_status(self.active_lbl, "", "Nothing active - normal play.")

        # The debug server only exists in the debugtools build, so asking for a
        # port silently swaps which binary runs. Say so rather than surprise.
        if self.settings.debug_port:
            set_status(self.build_lbl, "Warn",
                       "Runs the debugtools build instead of the release "
                       "build, which is slower. Set the port to Off for "
                       "normal play.")
        else:
            set_status(self.build_lbl, "", "Release build will be used.")

    def _touch(self) -> None:
        self.refresh()
        if not self._loading:
            self.changed.emit()

    # -- handlers ----------------------------------------------------------
    def _on_audio_legacy(self, on: bool) -> None:
        self.settings.audio_legacy = on
        self._touch()

    def _on_audio_shadow(self, on: bool) -> None:
        self.settings.audio_shadow = on
        self._touch()

    def _on_debug_port(self, value: int) -> None:
        self.settings.debug_port = value
        self._touch()


    def _on_voice_alloc_trace(self, on: bool) -> None:
        self.settings.voice_alloc_trace = on
        self._touch()

    def _on_overlay_interpreter(self, on: bool) -> None:
        self.settings.overlay_interpreter = on
        self._touch()

    def _on_force_interpreter(self, on: bool) -> None:
        self.settings.force_interpreter = on
        self._touch()

    # Every diagnostic's control, keyed by the settings field it edits. Driving
    # the re-sync from DIAGNOSTIC_SETTINGS rather than a hand-written list is
    # what stops this drifting again: the reset used to clear all of them but
    # refresh only four checkboxes, so voice_alloc_trace, overlay_interpreter
    # and force_interpreter stayed visibly ticked while their setting was off.
    def _diagnostic_controls(self) -> dict:
        return {name: getattr(self, name, None) for name in DIAGNOSTIC_SETTINGS}

    def _on_reset(self) -> None:
        if not confirm(
            self, "Reset diagnostics?",
            "Every measurement tool goes back off and the game returns to "
            "normal playback.", "Reset"):
            return
        reset_diagnostics(self.settings)
        self._loading = True
        for name, widget in self._diagnostic_controls().items():
            value = getattr(self.settings, name)
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value))
        self._loading = False
        self._touch()
