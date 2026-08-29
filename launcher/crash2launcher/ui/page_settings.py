"""Settings - every knob that affects a run.

This page exists to make A/B testing fast, so it is organised by *what you are
testing*, not by Qt convenience. Each control writes straight back into the
Settings dataclass and reports the change, which lets the Play page light up its
Relaunch button.

Two delivery routes are deliberately visible here because they behave
differently:

* **environment variables** - applied on the next launch, no files touched
* **game.toml** - supersampling only; the runtime has no env override for it
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import MAX_SUPERSAMPLING, RENDERERS, Settings
from .common import card, dim, heading, row, section

# label -> stored value
VSYNC_MODES = [
    ("Off - wall-clock pacer (best on high-refresh)", 0),
    ("On - vsync (only clocks ~60 Hz panels)", 1),
    ("Adaptive", -1),
]

INTERP_TARGETS = [
    ("Follow host panel", 0),
    ("90 fps", 90),
    ("120 fps", 120),
    ("144 fps", 144),
    ("240 fps", 240),
    ("280 fps", 280),
]


class SettingsPage(QWidget):
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
            "Settings",
            "Changes apply on the next launch. Use Relaunch on the Play page to "
            "restart with them.",
        ))

        lay.addWidget(self._video_card())
        lay.addWidget(self._pacing_card())
        lay.addWidget(self._input_card())
        lay.addWidget(self._audio_card())
        lay.addWidget(self._diagnostics_card())
        lay.addStretch(1)

        self._loading = False

    # -- cards -------------------------------------------------------------
    def _video_card(self) -> QWidget:
        self.renderer = QComboBox()
        self.renderer.addItems(RENDERERS)
        self.renderer.setCurrentText(self.settings.renderer)
        self.renderer.currentTextChanged.connect(self._on_renderer)

        self.scale = QComboBox()
        for n in range(1, MAX_SUPERSAMPLING + 1):
            self.scale.addItem(f"{n}x  ({320 * n}x{240 * n} internal)", n)
        self.scale.setCurrentIndex(max(0, self.settings.supersampling - 1))
        self.scale.currentIndexChanged.connect(self._on_scale)

        self.fullscreen = QCheckBox("Start fullscreen")
        self.fullscreen.setChecked(self.settings.fullscreen)
        self.fullscreen.toggled.connect(self._on_fullscreen)

        return card(
            section("Video"),
            row("Renderer", self.renderer),
            row("Internal resolution", self.scale),
            dim(
                f"Supersampling is written to game.toml - the runtime has no "
                f"environment override for it, and clamps above {MAX_SUPERSAMPLING}x. "
                "The Play page shows the scale the renderer actually used."
            ),
            self.fullscreen,
        )

    def _pacing_card(self) -> QWidget:
        self.vsync = QComboBox()
        for label, value in VSYNC_MODES:
            self.vsync.addItem(label, value)
        self.vsync.setCurrentIndex(
            next((i for i, (_, v) in enumerate(VSYNC_MODES) if v == self.settings.vsync), 0)
        )
        self.vsync.currentIndexChanged.connect(self._on_vsync)

        self.interp = QCheckBox("Enable frame interpolation")
        self.interp.setChecked(self.settings.frame_interpolation)
        self.interp.toggled.connect(self._on_interp)

        self.interp_fps = QComboBox()
        for label, value in INTERP_TARGETS:
            self.interp_fps.addItem(label, value)
        self.interp_fps.setCurrentIndex(
            next((i for i, (_, v) in enumerate(INTERP_TARGETS)
                  if v == self.settings.frame_interpolation_fps), 0)
        )
        self.interp_fps.setEnabled(self.settings.frame_interpolation)
        self.interp_fps.currentIndexChanged.connect(self._on_interp_fps)

        self.smooth = QCheckBox("Smooth 60 fps presentation")
        self.smooth.setChecked(self.settings.smooth_60fps)
        self.smooth.toggled.connect(self._on_smooth)

        self.blend = QCheckBox("Temporal frame blending")
        self.blend.setChecked(self.settings.frame_blend)
        self.blend.toggled.connect(self._on_blend)

        return card(
            section("Frame pacing and high refresh"),
            dim(
                "The game simulates at a fixed 59.94 Hz. Interpolation only "
                "changes how frames are presented, so judge it visually - it can "
                "introduce artifacts."
            ),
            row("V-sync", self.vsync),
            self.interp,
            row("Interpolation target", self.interp_fps),
            self.smooth,
            self.blend,
        )

    def _input_card(self) -> QWidget:
        self.merge_input = QCheckBox(
            "Player 1 reads keyboard and all controllers"
        )
        self.merge_input.setChecked(self.settings.merge_all_input)
        self.merge_input.toggled.connect(self._on_merge)

        return card(
            section("Input"),
            self.merge_input,
            dim(
                "Leave this on. A release build of the runtime pins player 1 to "
                "\"keyboard\" and never opens a gamepad, because it expects its own "
                "built-in launcher to assign a device. This setting works around that."
            ),
        )

    def _audio_card(self) -> QWidget:
        self.audio_legacy = QCheckBox("Legacy audio path (PSXRECOMP_AUDIO_LEGACY)")
        self.audio_legacy.setChecked(self.settings.audio_legacy)
        self.audio_legacy.toggled.connect(self._on_audio_legacy)

        self.audio_shadow = QCheckBox("SPU shadow comparison (PSX_AUDIO_SHADOW)")
        self.audio_shadow.setChecked(self.settings.audio_shadow)
        self.audio_shadow.toggled.connect(self._on_audio_shadow)

        return card(
            section("Audio"),
            dim("Diagnostic switches for the sound cut-off investigation."),
            self.audio_legacy,
            self.audio_shadow,
        )

    def _diagnostics_card(self) -> QWidget:
        self.debug_port = QSpinBox()
        self.debug_port.setRange(0, 65535)
        self.debug_port.setSpecialValueText("Disabled")
        self.debug_port.setValue(self.settings.debug_port)
        self.debug_port.valueChanged.connect(self._on_debug_port)

        self.telemetry = QCheckBox("Print fps telemetry to the log")
        self.telemetry.setChecked(self.settings.fps_telemetry)
        self.telemetry.toggled.connect(self._on_telemetry)

        return card(
            section("Diagnostics"),
            row("Debug server port", self.debug_port),
            dim(
                "A non-zero port opens the runtime's TCP debug server, which serves "
                "spu_events, spu_voices and spu_status - the SPU event ring used to "
                "diagnose the sound cut-off. 28000 is a fine choice."
            ),
            self.telemetry,
        )

    # -- handlers ----------------------------------------------------------
    def _touch(self) -> None:
        if not self._loading:
            self.changed.emit()

    def _on_renderer(self, value: str) -> None:
        self.settings.renderer = value
        self._touch()

    def _on_scale(self, index: int) -> None:
        self.settings.supersampling = self.scale.itemData(index)
        self._touch()

    def _on_fullscreen(self, on: bool) -> None:
        self.settings.fullscreen = on
        self._touch()

    def _on_vsync(self, index: int) -> None:
        self.settings.vsync = self.vsync.itemData(index)
        self._touch()

    def _on_interp(self, on: bool) -> None:
        self.settings.frame_interpolation = on
        self.interp_fps.setEnabled(on)
        self._touch()

    def _on_interp_fps(self, index: int) -> None:
        self.settings.frame_interpolation_fps = self.interp_fps.itemData(index)
        self._touch()

    def _on_smooth(self, on: bool) -> None:
        self.settings.smooth_60fps = on
        self._touch()

    def _on_blend(self, on: bool) -> None:
        self.settings.frame_blend = on
        self._touch()

    def _on_merge(self, on: bool) -> None:
        self.settings.merge_all_input = on
        self._touch()

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
