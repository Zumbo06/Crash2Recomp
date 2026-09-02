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
    reset_diagnostics,
)
from .common import card, dim, heading, row, section
from .theme import ERROR, TEXT_DIM, WARN


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
        lay.setContentsMargins(28, 24, 28, 24)
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
        frame = QFrame()
        frame.setObjectName("Card")
        frame.setStyleSheet(f"QFrame#Card {{ border-color: {WARN}; }}")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 14, 16, 14)

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

        warn = QLabel(
            f'<span style="color:{ERROR}"><b>Known to cause dropouts.</b></span> '
            + DIAGNOSTIC_SETTINGS["audio_legacy"]
            + " It exists only to measure the bridge against a baseline."
        )
        warn.setWordWrap(True)
        warn.setTextFormat(Qt.TextFormat.RichText)

        return card(
            section("Audio diagnostics"),
            self.audio_legacy,
            warn,
            self.audio_shadow,
            dim(DIAGNOSTIC_SETTINGS["audio_shadow"]),
        )

    def _capture_card(self) -> QWidget:
        self.debug_port = QSpinBox()
        self.debug_port.setRange(0, 65535)
        self.debug_port.setSpecialValueText("Off")
        self.debug_port.setValue(self.settings.debug_port)
        self.debug_port.valueChanged.connect(self._on_debug_port)

        self.fps_telemetry = QCheckBox("Print fps telemetry to the log")
        self.fps_telemetry.setChecked(self.settings.fps_telemetry)
        self.fps_telemetry.toggled.connect(self._on_telemetry)

        self.build_lbl = QLabel()
        self.build_lbl.setWordWrap(True)
        self.build_lbl.setTextFormat(Qt.TextFormat.RichText)

        return card(
            section("Capture / debug server"),
            row("Debug server port", self.debug_port),
            dim(
                "Serves spu_events, spu_voices, audio_stats and gpu_state over "
                "TCP - what the capture scripts in _build/ read. 4370 is the "
                "port those scripts expect."
            ),
            self.build_lbl,
            self.fps_telemetry,
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
            self.active_lbl.setText(
                f'<span style="color:{WARN}"><b>Active now:</b> '
                + ", ".join(names) + "</span>"
            )
        else:
            self.active_lbl.setText(
                f'<span style="color:{TEXT_DIM}">Nothing active - normal play.</span>'
            )

        # The debug server only exists in the debugtools build, so asking for a
        # port silently swaps which binary runs. Say so rather than surprise.
        if self.settings.debug_port:
            self.build_lbl.setText(
                f'<span style="color:{WARN}">Runs the <b>debugtools</b> build '
                "instead of the release build (tracing overhead). Set to Off "
                "for normal play.</span>"
            )
        else:
            self.build_lbl.setText(
                f'<span style="color:{TEXT_DIM}">Release build will be used.</span>'
            )

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

    def _on_telemetry(self, on: bool) -> None:
        self.settings.fps_telemetry = on
        self._touch()

    def _on_reset(self) -> None:
        reset_diagnostics(self.settings)
        self._loading = True
        self.audio_legacy.setChecked(self.settings.audio_legacy)
        self.audio_shadow.setChecked(self.settings.audio_shadow)
        self.debug_port.setValue(self.settings.debug_port)
        self.fps_telemetry.setChecked(self.settings.fps_telemetry)
        self._loading = False
        self._touch()
