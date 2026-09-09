"""Settings - presets on top, one section at a time below.

Everything used to live on a single scrolling page with roughly thirty controls
stacked in six cards, which made it hard to find anything and easy to change
something by accident. Now a preset covers the common cases in one click, and
each section holds a handful of related controls.

Diagnostics are NOT here: they live on the Advanced page, because one of them
(the legacy audio path) degrades playback and was previously indistinguishable
from a quality option.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import (
    ASPECTS,
    MAX_SUPERSAMPLING,
    OUTPUT_RESOLUTIONS,
    PRESET_NOTES,
    PRESETS,
    RENDERERS,
    Settings,
    apply_preset,
    matching_preset,
)
from .common import card, dim, heading, row, section
from .theme import ACCENT, PAGE_MARGINS, SPACE_4, TEXT_DIM
from .widgets.key_bindings import KeyBindingsEditor

# Crash 2's own framebuffer, measured from the runtime's gpu_state. The
# supersampling multiplier scales THIS, not the 320x240 the PS1 is usually
# quoted at, so the internal resolution is wider than a naive label suggests.
GAME_FB_W, GAME_FB_H = 512, 240

def scale_cost(n: int) -> str:
    """A GPU-cost hint that holds on any machine.

    Deliberately relative, not absolute: the right ceiling depends on the GPU,
    the display and the level, so quoting frame rates measured on one machine
    would mislead everyone else.
    """
    if n <= 2:
        return "very light"
    if n <= 4:
        return "moderate"
    if n == 5:
        return "demanding"
    return "very demanding"

FULLSCREEN_MODES = [
    ("Windowed", 0),
    ("Borderless fullscreen (desktop resolution)", 1),
    ("Exclusive fullscreen (changes display mode)", 2),
]

TEXTURE_FILTERS = [
    ("Nearest - sharp, authentic", "nearest"),
    ("Bilinear - smoother, less texel crawl", "bilinear"),
]

PRESENT_FILTERS = [
    ("Bicubic - best when downsampling", "bicubic"),
    ("Sharp bilinear", "sharp"),
    ("Plain - single tap", "plain"),
]

WIDESCREEN_MODES = [
    ("Projection hack - works on any title", False),
    ("Native-wide - needs per-game data", True),
]

SCALING_MODES_UI = [
    ("Letterbox - keep shape, bars", "letterbox"),
    ("Fill - keep shape, crop edges", "fill"),
    ("Fit width - no side bars", "fit_width"),
    ("Stretch - fill exactly, distorts", "stretch"),
]

OVERSCAN_PRESETS = [
    ("None (0)", 0),
    ("Slight (4)", 4),
    ("Standard (8)", 8),
    ("Strong (12)", 12),
    ("Maximum (16)", 16),
]

CRT_FILTERS = [
    ("Off", "raw"),
    ("CRT", "crt"),
    ("Composite", "composite"),
    ("Trinitron", "trinitron"),
]

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

# Sidebar entries. These used to be a SECOND nav rail inside this page,
# 150px wide, sharing the NavButton style with the real sidebar. They are now
# driven from the main window via show_section(), so there is one rail.
# "Video" is the old Display + Image sections merged - neither filled a page.
SECTIONS = ["Video", "Audio", "Input", "Performance"]
SECTION_HINTS = {
    "Video": "Resolution, aspect and image quality. Applies on next launch.",
    "Audio": "Output level and mixing.",
    "Input": "Controllers, save states and the keys used while playing.",
    "Performance": "Frame pacing and how streamed level code is executed.",
}


def _native_resolution() -> tuple[int, int] | None:
    """The desktop resolution of the machine running the launcher, if known."""
    try:
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            size = screen.size()
            if size.width() > 0 and size.height() > 0:
                return size.width(), size.height()
    except Exception:
        pass
    return None


class SettingsPage(QWidget):
    changed = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self._loading = True

        # Coalesces saves from continuous controls (see _touch).
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self.changed.emit)

        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(SPACE_4)

        self.title = heading("Video", SECTION_HINTS["Video"])
        root.addWidget(self.title)
        root.addWidget(self._preset_bar())

        # One stack entry per sidebar section. Video stacks the old Display and
        # Image builders; the builders themselves are untouched, so every
        # control keeps the attribute name the coverage test looks for.
        self.stack = QStackedWidget()
        for builders in ((self._display_page, self._image_page),
                         (self._audio_page,),
                         (self._input_page,),
                         (self._performance_page,)):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setWidget(self._merge(*[b() for b in builders]))
            self.stack.addWidget(scroll)
        root.addWidget(self.stack, 1)

        self._loading = False
        self._sync_dependent_controls()
        self._refresh_preset_label()

    # -- chrome ------------------------------------------------------------
    def _preset_bar(self) -> QWidget:
        buttons = QWidget()
        lay = QHBoxLayout(buttons)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.preset_buttons: dict[str, QPushButton] = {}
        for name in PRESETS:
            btn = QPushButton(name)
            btn.setToolTip(PRESET_NOTES.get(name, ""))
            btn.clicked.connect(lambda _=False, n=name: self._apply_preset(n))
            lay.addWidget(btn)
            self.preset_buttons[name] = btn

        self.preset_lbl = QLabel()
        self.preset_lbl.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.preset_lbl, 1)

        return card(
            section("Preset"),
            dim("A starting point for the common cases. Presets never change "
                "anything on the Advanced page."),
            buttons,
        )

    def _merge(self, *pages: QWidget) -> QWidget:
        """Stack several section builders into one scrollable column."""
        if len(pages) == 1:
            return pages[0]
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_4)
        for page in pages:
            lay.addWidget(page)
        lay.addStretch(1)
        return box

    def show_section(self, name: str) -> None:
        """Select a section by its sidebar name. Called by the main window,
        which now owns the only navigation rail."""
        if name not in SECTIONS:
            return
        self.stack.setCurrentIndex(SECTIONS.index(name))
        new_title = heading(name, SECTION_HINTS.get(name, ""))
        self.layout().replaceWidget(self.title, new_title)
        self.title.deleteLater()
        self.title = new_title

    # -- sections ----------------------------------------------------------
    def _display_page(self) -> QWidget:
        self.renderer = QComboBox()
        self.renderer.addItems(RENDERERS)
        self.renderer.setCurrentText(self.settings.renderer)
        self.renderer.currentTextChanged.connect(self._on_renderer)

        self.scale = QComboBox()
        for n in range(1, MAX_SUPERSAMPLING + 1):
            label = "%dx  -  %d x %d  (%s)" % (
                n, GAME_FB_W * n, GAME_FB_H * n, scale_cost(n))
            self.scale.addItem(label, n)
        self.scale.setCurrentIndex(max(0, self.settings.supersampling - 1))
        self.scale.setToolTip(
            "The game is rendered at this multiple of its own resolution and "
            "scaled down to your window, which sharpens the image.\n\n"
            "Higher values cost GPU performance. If the game stutters or drops "
            "below full speed, lower it - the Play page shows the live frame "
            "rate while you test.")
        self.scale.currentIndexChanged.connect(self._on_scale)

        self.fullscreen = self._combo(FULLSCREEN_MODES,
                                      self.settings.fullscreen_mode,
                                      self._on_fullscreen)
        self.output_resolution = QComboBox()
        native = _native_resolution()
        for label, w, h in OUTPUT_RESOLUTIONS:
            # Mark whichever entry matches the machine actually running this,
            # instead of assuming any particular display.
            if native and (w, h) == native:
                label += "   - your display"
            self.output_resolution.addItem(label, (w, h))
        self.output_resolution.setCurrentIndex(next(
            (i for i, (_, w, h) in enumerate(OUTPUT_RESOLUTIONS)
             if (w, h) == (self.settings.window_width, self.settings.window_height)), 0))
        self.output_resolution.currentIndexChanged.connect(self._on_output_resolution)

        self.aspect = QComboBox()
        self.aspect.addItems(ASPECTS)
        self.aspect.setCurrentText(self.settings.aspect)
        self.aspect.currentTextChanged.connect(self._on_aspect)

        self.ws_mode = self._combo(WIDESCREEN_MODES,
                                   self.settings.widescreen_native_wide,
                                   self._on_ws_mode)
        self.scaling = self._combo(SCALING_MODES_UI, self.settings.scaling_mode,
                                   self._on_scaling)

        return self._wrap(
            card(
                section("Output"),
                row("Renderer", self.renderer),
                row("Fullscreen", self.fullscreen),
                row("Output resolution", self.output_resolution),
                row("Image fit", self.scaling),
                dim("Alt+Enter or Ctrl+F toggles fullscreen while playing."),
            ),
            card(
                section("Rendering"),
                row("Internal resolution", self.scale),
                row("Gameplay aspect", self.aspect),
                row("Widescreen mode", self.ws_mode),
                dim("Leave Widescreen mode on the projection hack - native-wide "
                    "needs per-game data Crash 2 does not have, and without it "
                    "nothing widens at all."),
            ),
        )

    def _image_page(self) -> QWidget:
        self.tex_filter = self._combo(TEXTURE_FILTERS,
                                      self.settings.texture_filter,
                                      self._on_tex_filter)
        self.present_filter = self._combo(PRESENT_FILTERS,
                                          self.settings.present_filter,
                                          self._on_present_filter)
        self.crt = self._combo(CRT_FILTERS, self.settings.crt_filter, self._on_crt)
        self.overscan = self._combo(OVERSCAN_PRESETS, self.settings.overscan_top,
                                    self._on_overscan)

        self.aa = QCheckBox("Anti-aliasing (smooths the supersample downscale)")
        self.aa.setChecked(self.settings.antialiasing)
        self.aa.toggled.connect(self._on_aa)

        self.geom = QCheckBox("Geometry correction (PGXP)")
        self.geom.setChecked(self.settings.geometry_correction)
        self.geom.toggled.connect(self._on_geom)

        self.persp = QCheckBox("Perspective-correct texturing (PGXP)")
        self.persp.setChecked(self.settings.perspective_texturing)
        self.persp.toggled.connect(self._on_persp)

        return self._wrap(
            card(
                section("Filtering"),
                row("Texture filtering", self.tex_filter),
                row("Downsample filter", self.present_filter),
                row("CRT filter", self.crt),
                self.aa,
            ),
            card(
                section("Framing"),
                row("Overscan crop", self.overscan),
                dim("PS1 games often draw fewer than 240 scanlines and leave "
                    "the rest black. Those bars are part of the image, so no "
                    "image-fit mode can remove them - only this can."),
            ),
            card(
                section("Geometry (PGXP)"),
                self.geom,
                self.persp,
                dim("Measured on this port: PGXP trades texture shimmer for "
                    "geometry pop-in and seam lines. Both are off in every "
                    "preset for that reason - try them, but expect that "
                    "trade."),
            ),
        )

    def _audio_page(self) -> QWidget:
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(self.settings.volume)
        self.volume.valueChanged.connect(self._on_volume)
        self.volume_lbl = QLabel("%d%%" % self.settings.volume)
        self.volume_lbl.setFixedWidth(48)

        vol_row = QWidget()
        vl = QHBoxLayout(vol_row)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.addWidget(self.volume, 1)
        vl.addWidget(self.volume_lbl)

        self.mute = QCheckBox("Mute")
        self.mute.setChecked(self.settings.mute)
        self.mute.toggled.connect(self._on_mute)
        self.volume.setEnabled(not self.settings.mute)

        # Latency: named choices rather than a raw millisecond spinner. The
        # runtime accepts 30-500; these are the three that are worth offering.
        self.audio_latency_ms = QComboBox()
        for label, ms in (("Low - 60 ms", 60),
                          ("Normal - 90 ms", 90),
                          ("Safe - 180 ms", 180)):
            self.audio_latency_ms.addItem(label, ms)
        idx = self.audio_latency_ms.findData(self.settings.audio_latency_ms)
        if idx < 0:                       # hand-edited value: keep it visible
            self.audio_latency_ms.addItem(
                "Custom - %d ms" % self.settings.audio_latency_ms,
                self.settings.audio_latency_ms)
            idx = self.audio_latency_ms.count() - 1
        self.audio_latency_ms.setCurrentIndex(idx)
        self.audio_latency_ms.currentIndexChanged.connect(self._on_audio_latency)

        self.audio_hq = QCheckBox("Higher-quality sound mixing")
        self.audio_hq.setChecked(self.settings.audio_hq)
        self.audio_hq.toggled.connect(self._on_audio_hq)

        return self._wrap(
            card(
                section("Audio"),
                row("Volume", vol_row),
                self.mute,
                row("Latency", self.audio_latency_ms),
                dim("How far ahead the game buffers sound. Lower responds "
                    "faster; too low and it crackles. Drop to Safe if you "
                    "hear crackling."),
                self.audio_hq,
                dim("Re-mixes the sound at higher precision. Slightly more "
                    "CPU. The game checks it against the normal mix while it "
                    "plays and falls back on its own if they ever disagree."),
            ),
        )

    def _input_page(self) -> QWidget:
        self.bindings = KeyBindingsEditor(self.settings)
        self.bindings.changed.connect(self._touch)
        self.merge_input = QCheckBox("Player 1 reads keyboard and all controllers")
        self.merge_input.setChecked(self.settings.merge_all_input)
        self.merge_input.toggled.connect(self._on_merge)

        self.quick_save_slot = QComboBox()
        for n in range(12):
            self.quick_save_slot.addItem("Slot %d" % (n + 1), n)
        self.quick_save_slot.setCurrentIndex(self.settings.quick_save_slot)
        self.quick_save_slot.currentIndexChanged.connect(self._on_quick_slot)

        keys = QLabel(
            "<table cellpadding='3'>"
            "<tr><td><b>F5</b></td><td>Quick save</td></tr>"
            "<tr><td><b>F9</b></td><td>Quick load</td></tr>"
            "<tr><td><b>F7</b></td><td>Save state menu (all slots)</td></tr>"
            "<tr><td><b>F8</b></td><td>Rewind</td></tr>"
            "<tr><td><b>Alt+Enter</b></td><td>Toggle fullscreen</td></tr>"
            "<tr><td><b>Tab</b></td><td>Fast-forward (hold)</td></tr>"
            "</table>"
        )
        keys.setTextFormat(Qt.TextFormat.RichText)

        return self._wrap(
            card(
                section("Controls"),
                self.merge_input,
                dim("Use the keyboard alongside any connected controller."),
            ),
            card(section("Keyboard configuration · Player 1"), self.bindings),
            card(
                section("Save states"),
                row("Quick save slot", self.quick_save_slot),
                dim("The quick keys act on this slot. It is an ordinary slot, so "
                    "a quick save still shows in the F7 menu with its thumbnail - "
                    "point it somewhere else if you want your manual saves left "
                    "untouched."),
            ),
            card(
                section("Keys while playing"),
                keys,
            ),
        )

    def _performance_page(self) -> QWidget:
        self.vsync = self._combo(VSYNC_MODES, self.settings.vsync, self._on_vsync)

        self.interp = QCheckBox("Frame interpolation")
        self.interp.setChecked(self.settings.frame_interpolation)
        self.interp.toggled.connect(self._on_interp)

        self.interp_fps = self._combo(INTERP_TARGETS,
                                      self.settings.frame_interpolation_fps,
                                      self._on_interp_fps)

        self.native_overlays = QCheckBox("Compile level code natively")
        self.native_overlays.setChecked(self.settings.native_overlays)
        self.native_overlays.toggled.connect(self._on_native_overlays)

        self.fps_telemetry = QCheckBox("Show performance readout on the Play page")
        self.fps_telemetry.setChecked(self.settings.fps_telemetry)
        self.fps_telemetry.toggled.connect(self._on_fps_telemetry)

        self.developer_mode = QCheckBox("Developer mode")
        self.developer_mode.setChecked(self.settings.developer_mode)
        self.developer_mode.toggled.connect(self._on_developer_mode)

        return self._wrap(
            card(
                section("Frame pacing"),
                row("V-sync", self.vsync),
                self.interp,
                row("Interpolation target", self.interp_fps),
                dim("The game simulates at a fixed 59.94 Hz. Interpolation only "
                    "changes how many frames are PRESENTED - it blends between "
                    "real frames rather than adding simulation, so judge it by "
                    "eye."),
            ),
            card(
                section("Execution"),
                self.native_overlays,
                dim("Crash 2 streams level code from disc. Without this it runs "
                    "on the MIPS interpreter - correct, but far slower."),
            ),
            card(
                section("Reporting"),
                self.fps_telemetry,
                dim("Feeds the performance line on the Play page. Costs "
                    "nothing but a line in the log."),
                self.developer_mode,
                dim("Adds the Advanced page: capture and measurement tools "
                    "used to investigate bugs. They can make the game slower "
                    "or sound worse, so they stay switched off, and unreachable, "
                    "unless you turn this on."),
            ),
        )

    # -- helpers -----------------------------------------------------------
    def _combo(self, items, current, handler) -> QComboBox:
        box = QComboBox()
        for label, value in items:
            box.addItem(label, value)
        box.setCurrentIndex(
            next((i for i, (_, v) in enumerate(items) if v == current), 0))
        box.currentIndexChanged.connect(handler)
        return box

    def _wrap(self, *cards: QWidget) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        for c in cards:
            lay.addWidget(c)
        lay.addStretch(1)
        return page

    def _sync_dependent_controls(self) -> None:
        """Grey out controls that cannot take effect, rather than hiding them."""
        # Both fullscreen modes take the desktop resolution; only windowed uses
        # an explicit canvas.
        windowed = self.settings.fullscreen_mode == 0
        self.output_resolution.setEnabled(windowed)
        self.output_resolution.setToolTip(
            "" if windowed
            else "Fullscreen always uses the desktop resolution.")

        on = self.settings.frame_interpolation
        self.interp_fps.setEnabled(on)
        self.interp_fps.setToolTip(
            "" if on else "Enable frame interpolation to choose a target.")

    def _refresh_preset_label(self) -> None:
        name = matching_preset(self.settings)
        for label, btn in self.preset_buttons.items():
            btn.setProperty("active", label == name)
        if name:
            self.preset_lbl.setText(
                f'<span style="color:{ACCENT}">&nbsp;&nbsp;{name}</span>')
        else:
            self.preset_lbl.setText(
                f'<span style="color:{TEXT_DIM}">&nbsp;&nbsp;Custom</span>')

    def _apply_preset(self, name: str) -> None:
        apply_preset(self.settings, name)
        self.changed.emit()          # persist + push to the runtime config
        self._rebuild_from_settings()

    def _rebuild_from_settings(self) -> None:
        """Push the dataclass back into the widgets after a bulk change."""
        self._loading = True
        self.bindings.refresh()
        self.renderer.setCurrentText(self.settings.renderer)
        self.scale.setCurrentIndex(max(0, self.settings.supersampling - 1))
        self.aspect.setCurrentText(self.settings.aspect)
        for box, value in (
            (self.fullscreen, self.settings.fullscreen_mode),
            (self.ws_mode, self.settings.widescreen_native_wide),
            (self.scaling, self.settings.scaling_mode),
            (self.tex_filter, self.settings.texture_filter),
            (self.present_filter, self.settings.present_filter),
            (self.crt, self.settings.crt_filter),
            (self.overscan, self.settings.overscan_top),
            (self.vsync, self.settings.vsync),
            (self.interp_fps, self.settings.frame_interpolation_fps),
        ):
            idx = box.findData(value)
            if idx >= 0:
                box.setCurrentIndex(idx)
        self.aa.setChecked(self.settings.antialiasing)
        self.geom.setChecked(self.settings.geometry_correction)
        self.persp.setChecked(self.settings.perspective_texturing)
        self.interp.setChecked(self.settings.frame_interpolation)
        self._loading = False
        self._sync_dependent_controls()
        self._refresh_preset_label()

    def _touch(self, debounce: bool = False) -> None:
        self._sync_dependent_controls()
        self._refresh_preset_label()
        if self._loading:
            return
        if debounce:
            # Continuous controls (the volume slider) emit per pixel, and each
            # commit rewrites settings.toml and re-parses game.toml. Coalesce.
            self._save_timer.start()
            return
        self._save_timer.stop()
        self.changed.emit()

    # -- handlers ----------------------------------------------------------
    def _on_renderer(self, value: str) -> None:
        self.settings.renderer = value
        self._touch()

    def _on_scale(self, index: int) -> None:
        self.settings.supersampling = self.scale.itemData(index)
        self._touch()

    def _on_fullscreen(self, index: int) -> None:
        self.settings.fullscreen_mode = self.fullscreen.itemData(index)
        self._touch()

    def _on_output_resolution(self, index: int) -> None:
        self.settings.window_width, self.settings.window_height = \
            self.output_resolution.itemData(index)
        self._touch()

    def _on_aspect(self, value: str) -> None:
        self.settings.aspect = value
        self._touch()

    def _on_ws_mode(self, index: int) -> None:
        self.settings.widescreen_native_wide = self.ws_mode.itemData(index)
        self._touch()

    def _on_scaling(self, index: int) -> None:
        self.settings.scaling_mode = self.scaling.itemData(index)
        self._touch()

    def _on_tex_filter(self, index: int) -> None:
        self.settings.texture_filter = self.tex_filter.itemData(index)
        self._touch()

    def _on_present_filter(self, index: int) -> None:
        self.settings.present_filter = self.present_filter.itemData(index)
        self._touch()

    def _on_crt(self, index: int) -> None:
        self.settings.crt_filter = self.crt.itemData(index)
        self._touch()

    def _on_overscan(self, index: int) -> None:
        value = self.overscan.itemData(index)
        self.settings.overscan_top = value
        self.settings.overscan_bottom = value
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

    def _on_volume(self, value: int) -> None:
        self.settings.volume = value
        self.volume_lbl.setText("%d%%" % value)
        # Dragging the slider fires per pixel, and each commit rewrites
        # settings.toml and re-parses game.toml. Show the number immediately,
        # save once the value settles.
        self._touch(debounce=True)

    def _on_mute(self, on: bool) -> None:
        self.settings.mute = on
        self.volume.setEnabled(not on)
        self._touch()

    def _on_audio_latency(self, index: int) -> None:
        self.settings.audio_latency_ms = self.audio_latency_ms.itemData(index)
        self._touch()

    def _on_audio_hq(self, on: bool) -> None:
        self.settings.audio_hq = on
        self._touch()

    def _on_fps_telemetry(self, on: bool) -> None:
        self.settings.fps_telemetry = on
        self._touch()

    def _on_developer_mode(self, on: bool) -> None:
        self.settings.developer_mode = on
        self._touch()

    def _on_merge(self, on: bool) -> None:
        self.settings.merge_all_input = on
        self._touch()

    def _on_quick_slot(self, index: int) -> None:
        self.settings.quick_save_slot = self.quick_save_slot.itemData(index)
        self._touch()

    def _on_vsync(self, index: int) -> None:
        self.settings.vsync = self.vsync.itemData(index)
        self._touch()

    def _on_interp(self, on: bool) -> None:
        self.settings.frame_interpolation = on
        self._touch()

    def _on_interp_fps(self, index: int) -> None:
        self.settings.frame_interpolation_fps = self.interp_fps.itemData(index)
        self._touch()

    def _on_native_overlays(self, on: bool) -> None:
        self.settings.native_overlays = on
        self._touch()
