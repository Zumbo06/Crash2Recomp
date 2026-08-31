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

from ..config import (
    ASPECTS,
    MAX_SUPERSAMPLING,
    OUTPUT_RESOLUTIONS,
    RECOMMENDED_SUPERSAMPLING,
    RENDERERS,
    Settings,
)
from .common import card, dim, heading, row, section

FULLSCREEN_MODES = [
    ("Windowed", 0),
    ("Borderless fullscreen (desktop resolution)", 1),
    ("Exclusive fullscreen (changes display mode)", 2),
]

TEXTURE_FILTERS = [
    ("Nearest (sharp, authentic)", "nearest"),
    ("Bilinear (smooth)", "bilinear"),
]

# Which widescreen implementation runs when the aspect is not 4:3.
WIDESCREEN_MODES = [
    ("Projection hack (works on any title)", False),
    ("Native-wide (needs per-game data)", True),
]

# Overscan crop presets, in PS1 scanlines out of 240 (symmetric top/bottom).
# Many PS1 titles draw fewer than 240 lines and leave the rest genuinely black;
# those bars are part of the image and survive every scaling mode.
OVERSCAN_PRESETS = [
    ("None (0)", 0),
    ("Slight (4)", 4),
    ("Standard (8)", 8),
    ("Strong (12)", 12),
    ("Maximum (16)", 16),
]

# How the image fills the output canvas.
SCALING_MODES_UI = [
    ("Letterbox (keep shape, bars)", "letterbox"),
    ("Stretch (fill exactly, distorts)", "stretch"),
    ("Fill (keep shape, crop edges)", "fill"),
    ("Fit width (no side bars, bars top/bottom)", "fit_width"),
]

CRT_FILTERS = [
    ("Off (raw)", "raw"),
    ("CRT", "crt"),
    ("Composite", "composite"),
    ("Trinitron", "trinitron"),
]

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
        lay.addWidget(self._quality_card())
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
            label = f"{n}x  ({320 * n}x{240 * n} internal)"
            if n == RECOMMENDED_SUPERSAMPLING:
                label += "   - recommended"
            self.scale.addItem(label, n)
        self.scale.setCurrentIndex(max(0, self.settings.supersampling - 1))
        self.scale.currentIndexChanged.connect(self._on_scale)

        self.fullscreen = QComboBox()
        for label, value in FULLSCREEN_MODES:
            self.fullscreen.addItem(label, value)
        self.fullscreen.setCurrentIndex(
            next((i for i, (_, v) in enumerate(FULLSCREEN_MODES)
                  if v == self.settings.fullscreen_mode), 0))
        self.fullscreen.currentIndexChanged.connect(self._on_fullscreen)

        self.output_resolution = QComboBox()
        for label, width, height in OUTPUT_RESOLUTIONS:
            self.output_resolution.addItem(label, (width, height))
        self.output_resolution.setCurrentIndex(next(
            (i for i, (_, width, height) in enumerate(OUTPUT_RESOLUTIONS)
             if (width, height) == (
                 self.settings.window_width, self.settings.window_height
             )),
            0,
        ))
        self.output_resolution.currentIndexChanged.connect(
            self._on_output_resolution
        )

        self.aspect = QComboBox()
        self.aspect.addItems(ASPECTS)
        self.aspect.setCurrentText(self.settings.aspect)
        self.aspect.currentTextChanged.connect(self._on_aspect)

        self.ws_mode = QComboBox()
        for label, value in WIDESCREEN_MODES:
            self.ws_mode.addItem(label, value)
        self.ws_mode.setCurrentIndex(
            next((i for i, (_, v) in enumerate(WIDESCREEN_MODES)
                  if v == self.settings.widescreen_native_wide), 0))
        self.ws_mode.currentIndexChanged.connect(self._on_ws_mode)

        self.overscan = QComboBox()
        for label, value in OVERSCAN_PRESETS:
            self.overscan.addItem(label, value)
        self.overscan.setCurrentIndex(
            next((i for i, (_, v) in enumerate(OVERSCAN_PRESETS)
                  if v == self.settings.overscan_top), 0))
        self.overscan.currentIndexChanged.connect(self._on_overscan)

        self.scaling = QComboBox()
        for label, value in SCALING_MODES_UI:
            self.scaling.addItem(label, value)
        self.scaling.setCurrentIndex(
            next((i for i, (_, v) in enumerate(SCALING_MODES_UI)
                  if v == self.settings.scaling_mode), 0))
        self.scaling.currentIndexChanged.connect(self._on_scaling)

        self._sync_output_controls()

        return card(
            section("Display"),
            row("Renderer", self.renderer),
            row("Internal resolution", self.scale),
            dim(
                f"Measured on this machine: 5x is clean, 6x dips to ~53 fps, and "
                f"8x produces no frames at all - so the list stops at "
                f"{MAX_SUPERSAMPLING}x. The Play page shows the scale the renderer "
                "actually used, which is the only trustworthy number."
            ),
            row("Fullscreen", self.fullscreen),
            row("Output resolution", self.output_resolution),
            row("Gameplay aspect", self.aspect),
            row("Widescreen mode", self.ws_mode),
            row("Image fit", self.scaling),
            row("Overscan crop", self.overscan),
            dim(
                "The 1080p, 1440p and 4K choices are exact output canvases, "
                "independent of the aspect - 4:3 content pillarboxes inside them. "
                "Borderless always uses the desktop resolution, and Alt+Enter or "
                "Ctrl+F toggles fullscreen while playing.\n\n"
                "Leave Widescreen mode on the projection hack: native-wide needs "
                "per-game viewport data that Crash 2 does not have, and without it "
                "nothing widens at all."
            ),
        )

    def _quality_card(self) -> QWidget:
        self.tex_filter = QComboBox()
        for label, value in TEXTURE_FILTERS:
            self.tex_filter.addItem(label, value)
        self.tex_filter.setCurrentIndex(
            next((i for i, (_, v) in enumerate(TEXTURE_FILTERS)
                  if v == self.settings.texture_filter), 0))
        self.tex_filter.currentIndexChanged.connect(self._on_tex_filter)

        self.crt = QComboBox()
        for label, value in CRT_FILTERS:
            self.crt.addItem(label, value)
        self.crt.setCurrentIndex(
            next((i for i, (_, v) in enumerate(CRT_FILTERS)
                  if v == self.settings.crt_filter), 0))
        self.crt.currentIndexChanged.connect(self._on_crt)

        self.aa = QCheckBox("Anti-aliasing")
        self.aa.setChecked(self.settings.antialiasing)
        self.aa.toggled.connect(self._on_aa)

        self.geom = QCheckBox("Geometry correction (reduces PS1 vertex wobble)")
        self.geom.setChecked(self.settings.geometry_correction)
        self.geom.toggled.connect(self._on_geom)

        self.persp = QCheckBox("Perspective-correct texturing (reduces warping)")
        self.persp.setChecked(self.settings.perspective_texturing)
        self.persp.toggled.connect(self._on_persp)

        return card(
            section("Image quality"),
            row("Texture filtering", self.tex_filter),
            row("CRT filter", self.crt),
            self.aa,
            self.geom,
            self.persp,
            dim(
                "These live in settings.toml beside the game executable - there is "
                "no environment override, so they apply on the next launch."
            ),
        )

    def _pacing_card(self) -> QWidget:
        self.vsync = QComboBox()
        for label, value in VSYNC_MODES:
            self.vsync.addItem(label, value)
        self.vsync.setCurrentIndex(
            next((i for i, (_, v) in enumerate(VSYNC_MODES) if v == self.settings.vsync), 0)
        )
        self.vsync.currentIndexChanged.connect(self._on_vsync)

        self.interp = QCheckBox("High-refresh presentation interpolation (OpenGL)")
        self.interp.setChecked(self.settings.frame_interpolation)
        self.interp.toggled.connect(self._on_interp)

        self.interp_fps = QComboBox()
        for label, value in INTERP_TARGETS:
            self.interp_fps.addItem(label, value)
        self.interp_fps.setCurrentIndex(
            next((i for i, (_, v) in enumerate(INTERP_TARGETS)
                  if v == self.settings.frame_interpolation_fps), 0)
        )
        self.interp_fps.currentIndexChanged.connect(self._on_interp_fps)
        self._sync_interpolation_controls()

        self.blend = QCheckBox("Temporal frame blending")
        self.blend.setChecked(self.settings.frame_blend)
        self.blend.toggled.connect(self._on_blend)

        return card(
            section("Frame pacing and high refresh"),
            dim(
                "Crash 2 gameplay now updates and renders at a native 59.94 fps "
                "instead of repeating each frame twice. Targets above 60 are "
                "presentation interpolation only; they do not accelerate gameplay "
                "and can introduce blending artifacts."
            ),
            row("V-sync", self.vsync),
            self.interp,
            row("Interpolation target", self.interp_fps),
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

    def _on_fullscreen(self, index: int) -> None:
        self.settings.fullscreen_mode = self.fullscreen.itemData(index)
        self._sync_output_controls()
        self._touch()

    def _on_win_width(self, index: int) -> None:
        self.settings.window_width = self.win_width.itemData(index)
        self._touch()

    def _on_aspect(self, value: str) -> None:
        self.settings.aspect = value
        self._touch()

    def _on_ws_mode(self, index: int) -> None:
        self.settings.widescreen_native_wide = self.ws_mode.itemData(index)
        self._touch()

    def _on_overscan(self, index: int) -> None:
        # Symmetric top/bottom: that is where PS1 blank scanlines live.
        value = self.overscan.itemData(index)
        self.settings.overscan_top = value
        self.settings.overscan_bottom = value
        self._touch()

    def _on_scaling(self, index: int) -> None:
        self.settings.scaling_mode = self.scaling.itemData(index)
        self._touch()

    def _on_output_resolution(self, index: int) -> None:
        width, height = self.output_resolution.itemData(index)
        self.settings.window_width = width
        self.settings.window_height = height
        self._sync_output_controls()
        self._touch()

    def _sync_interpolation_controls(self) -> None:
        """The interpolation target only means anything while interpolation is
        on, and the runtime ignores any value below 90."""
        on = self.settings.frame_interpolation
        self.interp_fps.setEnabled(on)
        self.interp_fps.setToolTip(
            "" if on else "Enable frame interpolation to choose a target."
        )

    def _sync_output_controls(self) -> None:
        """Borderless fullscreen always uses the desktop resolution, so an
        explicit output canvas cannot apply - say so rather than letting the
        control look effective."""
        borderless = self.settings.fullscreen_mode == 1
        self.output_resolution.setEnabled(not borderless)
        self.output_resolution.setToolTip(
            "Borderless fullscreen always uses the desktop resolution."
            if borderless else ""
        )

    def _on_tex_filter(self, index: int) -> None:
        self.settings.texture_filter = self.tex_filter.itemData(index)
        self._touch()

    def _on_crt(self, index: int) -> None:
        self.settings.crt_filter = self.crt.itemData(index)
        self._touch()

    def _on_aa(self, on: bool) -> None:
        self.settings.antialiasing = on
        self._touch()

    def _on_geom(self, on: bool) -> None:
        self.settings.geometry_correction = on
        self._touch()

    def _on_persp(self, on: bool) -> None:
        self.settings.perspective_texturing = on
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
