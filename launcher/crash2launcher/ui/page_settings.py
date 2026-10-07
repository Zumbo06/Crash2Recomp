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

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)

from ..config import (
    ASPECTS,
    FAST_FORWARD_SPEEDS,
    MAX_SUPERSAMPLING,
    OUTPUT_RESOLUTIONS,
    POSTFX_NEUTRAL,
    POSTFX_RANGES,
    PRESET_NOTES,
    PRESETS,
    RECOMMENDED_SUPERSAMPLING_60FPS,
    RECOMMENDED_SUPERSAMPLING_120FPS,
    RENDERER_LABELS,
    SELECTABLE_RENDERERS,
    SUPERSAMPLING_60FPS_WARN_ABOVE,
    SUPERSAMPLING_120FPS_WARN_ABOVE,
    Settings,
    apply_preset,
    matching_preset,
    native_120fps_active,
)
from .common import FlowLayout, card, dim, heading, row, section, warn
from .theme import ACCENT, PAGE_MARGINS, SPACE_4, TEXT_DIM
from .widgets.key_bindings import HotkeyEditor, KeyBindingsEditor
from .widgets.pad_bindings import PadBindingsEditor

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

# "Needs per-game data" used to be the native-wide label. It never did: the
# runtime derives the extra columns from the display width (NOTES.md
# "Widescreen, part 2"); what it costs is GPU memory.
WIDESCREEN_MODES = [
    ("Squash - light on the GPU", False),
    ("Native-wide - draws real extra columns", True),
]

# Widescreen edge range (objects and scenery): camera-path nodes at 16:9,
# scaled by the runtime.
OBJECT_RANGE_UI = [
    ("Off - as the 4:3 game does", 0),
    ("Slightly wider", 1),
    ("Wider", 2),
    ("Widest", 3),
]

# Values are config.CHEAT_AKU_LEVELS; the runtime takes the name verbatim.
# Rewind buffer length. "Short" is what the runtime has always done on its
# own; "Off" stops it snapshotting every 15 frames, which is why this sits on
# the Performance page rather than with the other conveniences.
REWIND_UI = [
    ("Off - no rewind, no snapshot cost", "off"),
    ("Short - about 12 seconds", "short"),
    ("Long - about 50 seconds", "long"),
]
CHEAT_AKU_UI = [
    ("Off", "off"),
    ("Keep 2 masks", "keep_masks"),
    ("No damage", "no_damage"),
]
SCALING_MODES_UI = [
    ("Letterbox - keep shape, bars", "letterbox"),
    ("Fill - keep shape, crop edges", "fill"),
    ("Fit width - no side bars", "fit_width"),
    ("Stretch - fill exactly, distorts", "stretch"),
]

# Shape of the output canvas, separate from the shape the game RENDERS at.
# Pan & Scan is 4:3 gameplay inside a 16:9 screen.
OUTPUT_ASPECTS_UI = [
    ("Match gameplay aspect", "auto"),
    ("4:3", "4:3"),
    ("16:9 widescreen", "16:9"),
    # Screen shape only - there is deliberately no 21:9 GAMEPLAY aspect. See
    # config.OUTPUT_ASPECTS. The game keeps rendering 14:9 and the zoom/pan/
    # stretch dials place that picture in the wider window, so an ultrawide
    # panel is filled edge to edge horizontally without widening the view.
    ("21:9 ultrawide", "21:9"),
]

# How far to zoom the picture toward filling the screen. The shape is exact at
# every step, so this only ever trades bars for crop - it never stretches.
PRESENT_ZOOMS = [
    ("Follow image fit", -1),
    ("None - letterbox (0%)", 0),
    ("Slight (25%)", 25),
    ("Half (50%)", 50),
    ("Most (75%)", 75),
    ("Full - fills the screen (100%)", 100),
]

# Which edge to favour once the picture is cropped. Positive keeps more of the
# top, which is where Crash 2 puts its mask and counters.
PRESENT_PANS = [
    ("Centred", 0),
    ("Favour top (+4)", 4),
    ("Favour top (+8)", 8),
    ("Favour top (+12)", 12),
    ("Favour bottom (-4)", -4),
    ("Favour bottom (-8)", -8),
]

# Closes whatever gap Zoom leaves, by accepting a little horizontal distortion
# instead of bars or crop. From 14:9 the whole gap to a 16:9 panel is only 14%,
# so even "Full" here is mild; from 4:3 it would be 33%.
PRESENT_STRETCHES = [
    ("None - exact shape", 0),
    ("Slight (25%)", 25),
    ("Half (50%)", 50),
    ("Most (75%)", 75),
    ("Full - fills the width (100%)", 100),
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

# How fast Tab (or whatever the Fast-forward hotkey is) runs the game.
FAST_FORWARD_UI = [
    ("%dx" % n if n else "Unlimited", n) for n in FAST_FORWARD_SPEEDS
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
SECTIONS = ["Video", "Audio", "Input", "Performance", "Cheats"]
SECTION_HINTS = {
    "Video": "Resolution, aspect and image quality. Applies on next launch.",
    "Audio": "Output level and mixing.",
    "Input": "Controllers, save states and the keys used while playing.",
    "Performance": "Frame pacing and how streamed level code is executed.",
    "Cheats": "Optional assists. They change saved progression.",
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


POSTFX_AA_ITEMS = [
    ("Off", "off"),
    ("FXAA - fastest, slightly soft", "fxaa"),
    ("SMAA - sharper edges", "smaa"),
]


def _postfx_label(name: str, value: int) -> str:
    """How a post-processing slider reads: signed for offsets, else percent."""
    if name in ("postfx_brightness", "postfx_temperature"):
        return "%+d" % value if value else "0"
    return "%d%%" % value


class NeutralSlider(QSlider):
    """A horizontal slider that marks its neutral value and returns to it on a
    double-click.

    The post-processing ranges are not symmetric (gamma runs 50-200 around
    100), so "100%" sat a third of the way along one slider and in the middle
    of the next. The tick says where "unchanged" is on each.
    """

    def __init__(self, neutral: int, parent: QWidget | None = None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.neutral = neutral
        self.setMinimumHeight(24)
        self.setToolTip("Double-click to reset")

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.setValue(self.neutral)
        event.accept()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        style = self.style()
        groove = style.subControlRect(QStyle.ComplexControl.CC_Slider, opt,
                                      QStyle.SubControl.SC_SliderGroove, self)
        handle = style.subControlRect(QStyle.ComplexControl.CC_Slider, opt,
                                      QStyle.SubControl.SC_SliderHandle, self)
        span = groove.width() - handle.width()
        if span <= 0:
            return
        x = (groove.x() + handle.width() / 2
             + QStyle.sliderPositionFromValue(self.minimum(), self.maximum(),
                                              self.neutral, span))
        cy = groove.center().y() + 0.5
        p = QPainter(self)
        p.setPen(QPen(QColor(TEXT_DIM), 2))
        # Above and below the groove, clear of the 14 px handle.
        p.drawLine(QPointF(x, cy - 11), QPointF(x, cy - 8))
        p.drawLine(QPointF(x, cy + 8), QPointF(x, cy + 11))
        p.end()


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

        # One stack entry per sidebar section. Video stacks the old Display and
        # Image builders; the builders themselves are untouched, so every
        # control keeps the attribute name the coverage test looks for.
        #
        # The presets are video presets, so they live at the top of the Video
        # section only - and inside its scroll area, under the same scroll bar
        # as everything else on the page. They used to sit above the stack and
        # showed on Audio and Cheats too.
        presets = self._preset_bar()
        self.stack = QStackedWidget()
        for builders in ((self._display_page, self._image_page),
                         (self._audio_page,),
                         (self._input_page,),
                         (self._performance_page,),
                         (self._cheats_page,)):
            pages = [b() for b in builders]
            if builders[0] == self._display_page:
                pages.insert(0, presets)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setWidget(self._merge(*pages))
            self.stack.addWidget(scroll)
        root.addWidget(self.stack, 1)

        self._loading = False
        self._sync_dependent_controls()
        self._refresh_scale_warning()
        self._refresh_preset_label()

    # -- chrome ------------------------------------------------------------
    def _preset_bar(self) -> QWidget:
        # Wraps rather than squeezing: at the minimum window width a row of
        # five clipped every label ("uthenti", "nhance").
        buttons = QWidget()
        lay = FlowLayout(buttons)

        self.preset_buttons: dict[str, QPushButton] = {}
        for name in PRESETS:
            btn = QPushButton(name)
            btn.setToolTip(PRESET_NOTES.get(name, ""))
            btn.clicked.connect(lambda _=False, n=name: self._apply_preset(n))
            lay.addWidget(btn)
            self.preset_buttons[name] = btn

        self.preset_lbl = QLabel()
        self.preset_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.preset_lbl.setMinimumHeight(
            next(iter(self.preset_buttons.values())).sizeHint().height())
        lay.addWidget(self.preset_lbl)

        return card(
            section("Preset"),
            dim("Quick starting points. Presets never touch the Advanced "
                "page."),
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
        self.renderer = self._combo(
            [(RENDERER_LABELS.get(r, r), r) for r in SELECTABLE_RENDERERS],
            self.settings.renderer, self._on_renderer)
        self.renderer.setToolTip(
            "OpenGL: the default, most tested.\n\nDirect3D 12: the same "
            "renderer on Direct3D 12. Try it if OpenGL misbehaves; it falls "
            "back to OpenGL if it can't start.\n\nSoftware: slow, native "
            "resolution only.")

        self.scale = QComboBox()
        for n in range(1, MAX_SUPERSAMPLING + 1):
            label = "%dx  -  %d x %d  (%s)" % (
                n, GAME_FB_W * n, GAME_FB_H * n, scale_cost(n))
            self.scale.addItem(label, n)
        self.scale.setCurrentIndex(max(0, self.settings.supersampling - 1))
        self.scale.setToolTip(
            "Renders at this multiple of native resolution, then scales down "
            "to your window. Higher looks sharper and costs GPU time.")
        self.scale.currentIndexChanged.connect(self._on_scale)

        # Internal resolution and 60 FPS interact, and they live on different
        # pages, so neither control could say so on its own. See
        # config.RECOMMENDED_SUPERSAMPLING_60FPS for the measurement.
        self.scale_warning = warn("")
        self.scale_warning.setVisible(False)

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
        self.widescreen_object_range = self._combo(
            OBJECT_RANGE_UI, self.settings.widescreen_object_range,
            self._on_object_range)
        self.scaling = self._combo(SCALING_MODES_UI, self.settings.scaling_mode,
                                   self._on_scaling)
        self.output_aspect = self._combo(OUTPUT_ASPECTS_UI,
                                         self.settings.output_aspect,
                                         self._on_output_aspect)
        # Native-wide and the object range are developer previews for now:
        # _sync_dependent_controls shows these only in developer mode, and
        # config.widescreen_*_active() keeps them off everywhere else.
        self.ws_mode_row = row("Widescreen mode", self.ws_mode)
        self.object_range_row = row("Edge range", self.widescreen_object_range)
        self.ws_mode_note = dim("Native-wide looks sharper but uses far more GPU "
                                "memory at high internal resolution.")
        self.object_range_note = dim("Edge range spawns enemies and crates, and "
                                     "draws level scenery, for the wider view, "
                                     "so they stop popping in at the edges. "
                                     "Costs a little CPU.")

        return self._wrap(
            card(
                section("Output"),
                row("Renderer", self.renderer),
                # "Window mode" - the Home menu's name for the same choice, and
                # what the values are (a row called Fullscreen read "Windowed").
                row("Window mode", self.fullscreen),
                row("Output resolution", self.output_resolution),
                row("Screen shape", self.output_aspect),
                row("Image fit", self.scaling),
                dim("Alt+Enter or Ctrl+F toggles fullscreen while playing."),
            ),
            card(
                section("Rendering"),
                row("Internal resolution", self.scale),
                self.scale_warning,
                row("Gameplay aspect", self.aspect),
                self.ws_mode_row,
                self.object_range_row,
                dim("Gameplay aspect is what the game draws; Screen shape is "
                    "the window. 4:3 gameplay with Zoom fills a wide screen "
                    "without pop-in at the edges."),
                self.ws_mode_note,
                self.object_range_note,
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
        self.present_zoom = self._combo(PRESENT_ZOOMS, self.settings.present_zoom,
                                        self._on_present_zoom)
        self.present_pan = self._combo(PRESENT_PANS, self.settings.present_pan,
                                       self._on_present_pan)
        self.present_stretch = self._combo(PRESENT_STRETCHES,
                                           self.settings.present_stretch,
                                           self._on_present_stretch)

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
            self._postfx_card(),
            card(
                section("Framing"),
                row("Zoom", self.present_zoom),
                row("Stretch", self.present_stretch),
                dim("Zoom crops top and bottom; Stretch widens the picture. "
                    "Mix them to fill the screen."),
                row("Vertical pan", self.present_pan),
                dim("Moves the picture up or down while Zoom is cropping."),
                row("Overscan crop", self.overscan),
                dim("Crops the unused black lines many PS1 games leave. Keep "
                    "it at None while Zoom already crops."),
            ),
            card(
                section("Geometry (PGXP)"),
                self.geom,
                self.persp,
                dim("PGXP fixes texture wobble but adds pop-in and seams in "
                    "this game, so presets leave it off."),
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
                dim("Lower responds faster; raise it if you hear crackling."),
                self.audio_hq,
                dim("Higher-precision audio mix. Uses a little more CPU and "
                    "falls back by itself if needed."),
            ),
        )

    def _input_page(self) -> QWidget:
        self.bindings = KeyBindingsEditor(self.settings)
        self.bindings.changed.connect(self._touch)
        self.pad_bindings = PadBindingsEditor(self.settings)
        self.pad_bindings.changed.connect(self._touch)
        self.pad_deadzone = QSpinBox()
        self.pad_deadzone.setRange(0, 50)
        self.pad_deadzone.setSuffix(" %")
        self.pad_deadzone.setValue(self.settings.pad_deadzone)
        self.pad_deadzone.valueChanged.connect(self._on_pad_deadzone)
        self.merge_input = QCheckBox("Player 1 reads keyboard and all controllers")
        self.merge_input.setChecked(self.settings.merge_all_input)
        self.merge_input.toggled.connect(self._on_merge)

        self.quick_save_slot = QComboBox()
        for n in range(12):
            self.quick_save_slot.addItem("Slot %d" % (n + 1), n)
        self.quick_save_slot.setCurrentIndex(self.settings.quick_save_slot)
        self.quick_save_slot.currentIndexChanged.connect(self._on_quick_slot)

        # The runtime's own keys (config.ini [KeyMap]). They and the game's
        # buttons warn about each other, so each refreshes the other.
        self.hotkeys = HotkeyEditor(self.settings)
        self.hotkeys.changed.connect(self._on_hotkeys)
        self.bindings.changed.connect(self.hotkeys.refresh)

        self.fast_forward_speed = self._combo(FAST_FORWARD_UI,
                                              self.settings.fast_forward_speed,
                                              self._on_fast_forward_speed)
        self.fast_forward_toggle = QCheckBox(
            "Press once to start fast-forward, again to stop")
        self.fast_forward_toggle.setChecked(self.settings.fast_forward_toggle)
        self.fast_forward_toggle.toggled.connect(self._on_fast_forward_toggle)
        self.pause_on_focus_loss = QCheckBox(
            "Pause when the game window loses focus")
        self.pause_on_focus_loss.setChecked(self.settings.pause_on_focus_loss)
        self.pause_on_focus_loss.toggled.connect(self._on_pause_on_focus_loss)

        return self._wrap(
            card(
                section("Controls"),
                self.merge_input,
                dim("Use the keyboard alongside any connected controller."),
            ),
            card(section("Keyboard configuration · Player 1"), self.bindings),
            card(
                section("Controller configuration · Player 1"),
                self.pad_bindings,
                row("Stick deadzone", self.pad_deadzone),
                dim("Raise it if Crash drifts with the stick released. "
                    "Default 10%."),
            ),
            card(section("Hotkeys"), self.hotkeys),
            card(
                section("While playing"),
                row("Fast-forward speed", self.fast_forward_speed),
                self.fast_forward_toggle,
                self.pause_on_focus_loss,
                dim("Alt-tab and the game waits behind the Home menu."),
            ),
            card(
                section("Save states"),
                row("Quick save slot", self.quick_save_slot),
                dim("Quick save and quick load use this slot. The Saves page "
                    "shows every slot."),
            ),
        )

    def _cheats_page(self) -> QWidget:
        """Their own section: they are not performance settings, and putting
        something that rewrites a save two cards below "Frame pacing" made it
        easy to switch on without reading why that matters."""
        return self._wrap(
            card(
                section("Assists"),
                self.cheat_infinite_lives,
                row("Aku Aku", self.cheat_aku_aku),
                dim("\"Keep 2 masks\" always absorbs a hit. \"No damage\" "
                    "keeps Crash invincible, like the gold Aku Aku mask."),
                dim("Neither stops falls, crushing or drowning. Both pause "
                    "during demos."),
            ),
            card(
                section("Before you turn these on"),
                dim("Assists change your save: lives and masks are written "
                    "to the memory card as you play."),
                dim("The Home menu has the same options and applies them "
                    "right away."),
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

        self.native_60fps = QCheckBox("60 FPS game updates")
        self.native_60fps.setChecked(self.settings.native_60fps)
        self.native_60fps.toggled.connect(self._on_native_60fps)

        # Developer mode only (config.native_120fps_active): in play it reached
        # 120 in bursts and stuttered when it did.
        self.native_120fps = QCheckBox("120 FPS (developer preview)")
        self.native_120fps.setChecked(self.settings.native_120fps)
        self.native_120fps.toggled.connect(self._on_native_120fps)
        self.native_120fps_note = dim(
            "Unstable: usually falls back to 60 and can stutter.")


        self.cheat_infinite_lives = QCheckBox("Keep 99 lives")
        self.cheat_infinite_lives.setChecked(self.settings.cheat_infinite_lives)
        self.cheat_infinite_lives.toggled.connect(self._on_cheat_infinite_lives)
        self.cheat_aku_aku = self._combo(CHEAT_AKU_UI, self.settings.cheat_aku_aku,
                                         self._on_cheat_aku_aku)

        self.rewind = self._combo(REWIND_UI, self.settings.rewind,
                                  self._on_rewind)

        self.native_overlays = QCheckBox("Compile level code natively")
        self.native_overlays.setChecked(self.settings.native_overlays)
        self.native_overlays.toggled.connect(self._on_native_overlays)

        self.fps_telemetry = QCheckBox("Show performance readout on the Play page")
        self.fps_telemetry.setChecked(self.settings.fps_telemetry)
        self.fps_telemetry.toggled.connect(self._on_fps_telemetry)

        self.fps_overlay = QCheckBox("FPS counter in the game window")
        self.fps_overlay.setChecked(self.settings.fps_overlay)
        self.fps_overlay.toggled.connect(self._on_fps_overlay)

        self.developer_mode = QCheckBox("Developer mode")
        self.developer_mode.setChecked(self.settings.developer_mode)
        self.developer_mode.toggled.connect(self._on_developer_mode)

        return self._wrap(
            card(
                section("Frame pacing"),
                row("V-sync", self.vsync),
                self.interp,
                row("Interpolation target", self.interp_fps),
                dim("Adds blended in-between frames for smoothness. The game "
                    "itself doesn't respond faster."),
            ),
            card(
                section("Game update rate"),
                self.native_60fps,
                dim("The game's own loop runs at 60, not interpolated. Uses "
                    "200% virtual PS1 CPU. Experimental; relaunch to apply."),
                self.native_120fps,
                self.native_120fps_note,
                dim("Keep \"Compile level code natively\" on (below), or 60 "
                    "FPS won't hold."),
                dim("If 60 FPS stutters, lower Internal resolution on the "
                    "Video page first."),
            ),
            card(
                section("Execution"),
                self.native_overlays,
                dim("Compiles streamed level code instead of interpreting "
                    "it. Much faster."),
                row("Rewind", self.rewind),
                dim("F8 rewinds. Turn it off to skip the snapshot work."),
            ),
            card(
                section("Reporting"),
                self.fps_telemetry,
                dim("Shows performance on the Play page."),
                self.fps_overlay,
                dim("Its hotkey and the Home menu switch it too; that's kept."),
                self.developer_mode,
                dim("Adds the Advanced page with debugging tools, which can "
                    "slow the game down."),
            ),
        )

    def _postfx_card(self) -> QWidget:
        self._postfx_values: dict[str, QLabel] = {}
        self.postfx_enabled = QCheckBox("Post-processing on")
        self.postfx_enabled.setChecked(self.settings.postfx_enabled)
        self.postfx_enabled.toggled.connect(self._on_postfx_enabled)
        self.postfx_aa = self._combo(POSTFX_AA_ITEMS, self.settings.postfx_aa,
                                     self._on_postfx_aa)
        self.postfx_dedither = QCheckBox("Smooth dithered textures")
        self.postfx_dedither.setChecked(self.settings.postfx_dedither)
        self.postfx_dedither.toggled.connect(self._on_postfx_dedither)
        reset = QPushButton("Reset post-processing")
        reset.clicked.connect(self._reset_postfx)
        return card(
            section("Post-processing"),
            self.postfx_enabled,
            dim("Effects on the final picture. POST FX in the Home menu "
                "switches them while you play, and that's kept."),
            row("Edge smoothing", self.postfx_aa),
            row("Sharpening", self._postfx_slider("postfx_sharpen")),
            row("Brightness", self._postfx_slider("postfx_brightness")),
            row("Contrast", self._postfx_slider("postfx_contrast")),
            row("Saturation", self._postfx_slider("postfx_saturation")),
            row("Gamma", self._postfx_slider("postfx_gamma")),
            row("Colour temperature", self._postfx_slider("postfx_temperature")),
            row("Bloom", self._postfx_slider("postfx_bloom")),
            row("Bloom threshold", self._postfx_slider("postfx_bloom_threshold")),
            row("Vignette", self._postfx_slider("postfx_vignette")),
            row("Film grain", self._postfx_slider("postfx_grain")),
            self.postfx_dedither,
            dim("Smooths the checkerboard dither in PS1 textures; edges and "
                "text stay sharp."),
            reset,
        )

    def _postfx_slider(self, name: str) -> QWidget:
        low, high, neutral = POSTFX_RANGES[name]
        slider = NeutralSlider(neutral)
        slider.setRange(low, high)
        slider.setValue(int(getattr(self.settings, name)))
        value = QLabel()
        value.setMinimumWidth(44)
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value.setText(_postfx_label(name, slider.value()))
        slider.valueChanged.connect(
            lambda v, n=name, lbl=value: self._on_postfx_value(n, v, lbl))
        setattr(self, name, slider)
        self._postfx_values[name] = value
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(slider, 1)
        lay.addWidget(value)
        return box

    def _on_postfx_value(self, name: str, value: int, label: QLabel) -> None:
        label.setText(_postfx_label(name, value))
        setattr(self.settings, name, value)
        self._touch(debounce=True)

    def _on_postfx_aa(self, index: int) -> None:
        self.settings.postfx_aa = self.postfx_aa.itemData(index)
        self._touch()

    def _on_postfx_dedither(self, on: bool) -> None:
        self.settings.postfx_dedither = on
        self._touch()

    def _on_postfx_enabled(self, on: bool) -> None:
        self.settings.postfx_enabled = on
        self._touch()

    def _reset_postfx(self) -> None:
        for name, value in POSTFX_NEUTRAL.items():
            setattr(self.settings, name, value)
        self._rebuild_from_settings()
        self._touch()

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

        fast = native_120fps_active(self.settings)
        self.interp.setEnabled(not fast)
        self.interp.setToolTip(
            "Not used with 120 FPS game updates." if fast else "")
        on = self.settings.frame_interpolation and not fast
        self.interp_fps.setEnabled(on)
        self.interp_fps.setToolTip(
            "" if on else "Enable frame interpolation to choose a target.")

        developer = self.settings.developer_mode

        # Widescreen settings do nothing while the game draws 4:3. Native-wide
        # and the object range are developer previews: hidden, and not applied
        # (config.widescreen_*_active), outside developer mode.
        wide = self.settings.aspect != "4:3"
        for box in (self.ws_mode, self.widescreen_object_range):
            box.setEnabled(wide)
            box.setToolTip("" if wide else "Only with a widescreen gameplay aspect.")
        for w in (self.ws_mode_row, self.object_range_row,
                  self.ws_mode_note, self.object_range_note):
            w.setVisible(developer)

        # 120 refines 60; it cannot apply on its own. And it is a developer
        # preview: hidden, and not applied, outside developer mode.
        self.native_120fps.setVisible(developer)
        self.native_120fps_note.setVisible(developer)
        sixty = self.settings.native_60fps
        self.native_120fps.setEnabled(sixty)
        self.native_120fps.setToolTip(
            "" if sixty else "Turn on 60 FPS game updates first.")

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

    def refresh_from_settings(self) -> None:
        """Show values changed outside this page (kept in-game changes)."""
        self._rebuild_from_settings()

    def _rebuild_from_settings(self) -> None:
        """Push the dataclass back into the widgets after a bulk change.

        Every control must be listed. Zoom, Stretch, Vertical pan and Screen
        shape used to be missing, although every preset sets them, so after a
        preset those four showed the values from before it."""
        self._loading = True
        self.bindings.refresh()
        self.pad_bindings.refresh()
        self.hotkeys.refresh()
        self.pad_deadzone.setValue(self.settings.pad_deadzone)
        self._refresh_scale_warning()
        idx = self.renderer.findData(self.settings.renderer)
        if idx >= 0:
            self.renderer.setCurrentIndex(idx)
        self.scale.setCurrentIndex(max(0, self.settings.supersampling - 1))
        self.aspect.setCurrentText(self.settings.aspect)
        for box, value in (
            (self.fullscreen, self.settings.fullscreen_mode),
            (self.rewind, self.settings.rewind),
            (self.ws_mode, self.settings.widescreen_native_wide),
            (self.widescreen_object_range, self.settings.widescreen_object_range),
            (self.scaling, self.settings.scaling_mode),
            (self.output_aspect, self.settings.output_aspect),
            (self.present_zoom, self.settings.present_zoom),
            (self.present_pan, self.settings.present_pan),
            (self.present_stretch, self.settings.present_stretch),
            (self.tex_filter, self.settings.texture_filter),
            (self.present_filter, self.settings.present_filter),
            (self.crt, self.settings.crt_filter),
            (self.overscan, self.settings.overscan_top),
            (self.vsync, self.settings.vsync),
            (self.interp_fps, self.settings.frame_interpolation_fps),
            (self.cheat_aku_aku, self.settings.cheat_aku_aku),
            (self.postfx_aa, self.settings.postfx_aa),
            (self.audio_latency_ms, self.settings.audio_latency_ms),
            (self.quick_save_slot, self.settings.quick_save_slot),
            (self.fast_forward_speed, self.settings.fast_forward_speed),
        ):
            idx = box.findData(value)
            if idx >= 0:
                box.setCurrentIndex(idx)
        self.output_resolution.setCurrentIndex(next(
            (i for i, (_, w, h) in enumerate(OUTPUT_RESOLUTIONS)
             if (w, h) == (self.settings.window_width, self.settings.window_height)),
            0))
        self.volume.setValue(self.settings.volume)
        self.volume_lbl.setText("%d%%" % self.settings.volume)
        self.volume.setEnabled(not self.settings.mute)
        for box, value in (
            (self.aa, self.settings.antialiasing),
            (self.geom, self.settings.geometry_correction),
            (self.persp, self.settings.perspective_texturing),
            (self.interp, self.settings.frame_interpolation),
            (self.native_60fps, self.settings.native_60fps),
            (self.native_120fps, self.settings.native_120fps),
            (self.cheat_infinite_lives, self.settings.cheat_infinite_lives),
            (self.mute, self.settings.mute),
            (self.audio_hq, self.settings.audio_hq),
            (self.merge_input, self.settings.merge_all_input),
            (self.native_overlays, self.settings.native_overlays),
            (self.fps_telemetry, self.settings.fps_telemetry),
            (self.fps_overlay, self.settings.fps_overlay),
            (self.developer_mode, self.settings.developer_mode),
            (self.postfx_enabled, self.settings.postfx_enabled),
            (self.postfx_dedither, self.settings.postfx_dedither),
            (self.fast_forward_toggle, self.settings.fast_forward_toggle),
            (self.pause_on_focus_loss, self.settings.pause_on_focus_loss),
        ):
            box.setChecked(bool(value))
        for name in POSTFX_RANGES:
            getattr(self, name).setValue(int(getattr(self.settings, name)))
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
    def _on_renderer(self, index: int) -> None:
        self.settings.renderer = self.renderer.itemData(index)
        self._touch()

    def _refresh_scale_warning(self) -> None:
        """Warn when internal resolution is the thing that will cost 60 FPS.

        Measured (tuning/NOTES.md part 10): at supersampling 5 with 60 FPS on,
        the host managed 2580 presents against 3823 emulated VBlanks while 98%
        of frames were still fitting in one field - the renderer was the
        constraint, not the game. This is a warning and not a clamp because the
        exact ceiling is GPU-dependent.
        """
        if not hasattr(self, "scale_warning"):
            return
        fast = native_120fps_active(self.settings)
        limit = (SUPERSAMPLING_120FPS_WARN_ABOVE if fast
                 else SUPERSAMPLING_60FPS_WARN_ABOVE)
        too_high = (self.settings.native_60fps
                    and self.settings.supersampling > limit)
        if too_high and fast:
            self.scale_warning.setText(
                "<b>Likely too high for 120 FPS.</b> Try %dx."
                % RECOMMENDED_SUPERSAMPLING_120FPS)
        elif too_high:
            self.scale_warning.setText(
                "<b>Likely too high for 60 FPS.</b> Try %dx."
                % RECOMMENDED_SUPERSAMPLING_60FPS)
        self.scale_warning.setVisible(too_high)

    def _on_scale(self, index: int) -> None:
        self.settings.supersampling = self.scale.itemData(index)
        self._refresh_scale_warning()
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

    def _on_object_range(self, index: int) -> None:
        self.settings.widescreen_object_range = \
            self.widescreen_object_range.itemData(index)
        self._touch()

    def _on_scaling(self, index: int) -> None:
        self.settings.scaling_mode = self.scaling.itemData(index)
        self._touch()

    def _on_output_aspect(self, index: int) -> None:
        self.settings.output_aspect = self.output_aspect.itemData(index)
        self._touch()

    def _on_present_zoom(self, index: int) -> None:
        self.settings.present_zoom = self.present_zoom.itemData(index)
        self._touch()

    def _on_present_pan(self, index: int) -> None:
        self.settings.present_pan = self.present_pan.itemData(index)
        self._touch()

    def _on_present_stretch(self, index: int) -> None:
        self.settings.present_stretch = self.present_stretch.itemData(index)
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

    def _on_fps_overlay(self, on: bool) -> None:
        self.settings.fps_overlay = on
        self._touch()

    def _on_hotkeys(self) -> None:
        # The game-button editor warns about keys shared with a hotkey.
        self.bindings.refresh()
        self._touch()

    def _on_fast_forward_speed(self, index: int) -> None:
        self.settings.fast_forward_speed = self.fast_forward_speed.itemData(index)
        self._touch()

    def _on_fast_forward_toggle(self, on: bool) -> None:
        self.settings.fast_forward_toggle = on
        self._touch()

    def _on_pause_on_focus_loss(self, on: bool) -> None:
        self.settings.pause_on_focus_loss = on
        self._touch()

    def _on_developer_mode(self, on: bool) -> None:
        self.settings.developer_mode = on
        self._touch()

    def _on_rewind(self, index: int) -> None:
        self.settings.rewind = self.rewind.itemData(index)
        self._touch()

    def _on_native_60fps(self, on: bool) -> None:
        self.settings.native_60fps = on
        self._refresh_scale_warning()
        self._touch()

    def _on_native_120fps(self, on: bool) -> None:
        self.settings.native_120fps = on
        self._refresh_scale_warning()
        self._touch()

    def _on_cheat_infinite_lives(self, on: bool) -> None:
        self.settings.cheat_infinite_lives = on
        self._touch()

    def _on_cheat_aku_aku(self, index: int) -> None:
        self.settings.cheat_aku_aku = self.cheat_aku_aku.itemData(index)
        self._touch()

    def _on_merge(self, on: bool) -> None:
        self.settings.merge_all_input = on
        self._touch()

    def _on_pad_deadzone(self, value: int) -> None:
        self.settings.pad_deadzone = value
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
