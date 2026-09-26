"""Persistent launcher settings.

Stored as JSON next to the executable so the whole bundle stays portable - copy
the folder to a USB stick and the settings travel with it. Writes go through a
temp file and an atomic replace, because a half-written settings.json that kills
the launcher on next start is a genuinely annoying failure mode.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

# "<start>:<end>" frame numbers, as main.cpp runtime_perf_parse_window reads it.
_BENCH_WINDOW_RE = re.compile(r"\d+:\d+")

# Every backend name the runtime knows about. Reference only - clamp()
# validates against SELECTABLE_RENDERERS, so a settings file naming "vulkan"
# IS rewritten to "opengl" rather than passed through.
RENDERERS = ("opengl", "d3d12", "vulkan", "software")

# What the launcher actually lets a player pick.
#
# Vulkan is excluded because it cannot work on this title, in two independent
# ways. gpu_vk_renderer.c says geometry pipelines, texturing/CLUT, mask-bit,
# semi-transparency, SSAA and the native-wide compositor "currently abort via
# psx_fatal_halt rather than silently no-op" - so a frame with any geometry in
# it kills the process. And the runtime refuses the backend anyway unless
# game.toml sets [video] offer_vulkan, which Crash 2's does not.
#
# It was selectable in the combo the whole time. Picking it was a crash.
#
# Direct3D 12 is the OpenGL renderer itself, compiled a second time against a
# Direct3D 12 layer (runtime gpu_gl12.h), so everything OpenGL does - supersampling,
# native widescreen, frame blending, bezels, post-processing - it does too. It
# is labelled experimental until it has as many hours of play behind it as
# OpenGL; if it cannot start, the runtime falls back to OpenGL on its own.
SELECTABLE_RENDERERS = ("opengl", "d3d12", "software")
RENDERER_LABELS = {
    "opengl": "OpenGL",
    "d3d12": "Direct3D 12 (experimental)",
    "vulkan": "Vulkan",
    "software": "Software",
}
# Renderers that are the hardware path: presentation features (frame blending,
# post-processing, bezels, native widescreen) exist only on these.
HARDWARE_RENDERERS = ("opengl", "d3d12")
# 14:9 is the useful middle: it widens the field of view by ~1.17x instead of
# 16:9's ~1.33x, so it reaches only about half as far past the edge Crash 2's
# levels were actually authored to - which is where scenery pops in and out.
# It also leaves only a 14% gap to a 16:9 panel, small enough that the stretch
# dial can close it without a visible distortion.
ASPECTS = ("4:3", "14:9", "16:9")

# Shapes the OUTPUT CANVAS may take. Deliberately a superset of ASPECTS.
#
# 21:9 is here and NOT in ASPECTS, and that distinction is the whole feature.
# Widening what the game RENDERS at walks the camera past the edge Crash 2's
# levels were authored to, and the margin scales badly: 43 px per side at 14:9,
# 85 at 16:9, 192 at 21:9 (psx_ws_x_margin, gpu.c). 14:9 was chosen precisely
# because 43 px halves the exposure that makes scenery pop in and out, so 21:9
# gameplay would be about 2.3x worse than the 16:9 mode already demoted for it.
# The cull machinery cannot rescue it either - Crash 2 has zero screen-extent
# cull immediates and no 2D backdrop layer (tuning/NOTES.md, Widescreen part 6).
#
# Offering it as a CANVAS costs none of that: the game keeps rendering 14:9 and
# the existing zoom/pan/stretch dials fit that picture into a wider window. An
# ultrawide owner gets a correct full-width image and no new pop-in.
OUTPUT_ASPECTS = ASPECTS + ("21:9",)

# Exact output canvases offered by the launcher.  Aspect ratio is deliberately
# separate: a 4:3 BIOS/FMV can pillarbox inside (say) a 3840x2160 canvas, while
# 3D gameplay uses the title's native-wide profile.
OUTPUT_RESOLUTIONS = (
    ("Auto (fit display)", 0, 0),
    # 4:3 - matches the console's native shape, no pillarboxing.
    ("960 x 720 (4:3)", 960, 720),
    ("1440 x 1080 (4:3)", 1440, 1080),
    ("1920 x 1440 (4:3)", 1920, 1440),
    ("2880 x 2160 (4:3)", 2880, 2160),
    # 16:9
    ("1280 x 720 (HD)", 1280, 720),
    ("1600 x 900 (HD+)", 1600, 900),
    ("1920 x 1080 (Full HD)", 1920, 1080),
    ("2560 x 1440 (QHD)", 2560, 1440),
    ("3200 x 1800 (QHD+)", 3200, 1800),
    ("3840 x 2160 (4K UHD)", 3840, 2160),
    # 16:10
    ("1680 x 1050 (16:10)", 1680, 1050),
    ("1920 x 1200 (16:10)", 1920, 1200),
    ("2560 x 1600 (16:10)", 2560, 1600),
    # Ultrawide. settings.toml caps width at 3840, so 3440 is the practical max.
    ("2560 x 1080 (21:9 UW)", 2560, 1080),
    ("3440 x 1440 (21:9 UW)", 3440, 1440),
)

# How the image fills the output canvas.
SCALING_MODES = ("letterbox", "stretch", "fill", "fit_width")
# Aku Aku assist strengths. The index into this tuple is the C2_AKU_* level the
# runtime uses, so the order is part of the contract with crash2_cheats.h.
CHEAT_AKU_LEVELS = ("off", "keep_masks", "no_damage")

# Rewind buffer sizes, as (depth, interval-in-frames) for PSX_REWIND_DEPTH and
# PSX_REWIND_INTERVAL. The runtime accepts depth 4..200 and interval 1..60
# (psx_rewind.c normalize_rewind_*), and defaults to 50/15 - roughly 12.5
# seconds at 60 Hz. It has always been ON, and the launcher never wrote any of
# these, so a player could neither lengthen it nor stop paying for it.
#
# Snapshots are not free: one is taken every `interval` frames regardless of
# whether the player ever rewinds, which is why "off" is a real performance
# choice and not just a feature toggle.
# Output post-processing (runtime gpu_postfx.h). The launcher's units are
# percent; the runtime clamps to the same ranges. Neutral everywhere means the
# chain does not run at all, so the defaults cost nothing.
POSTFX_AA_MODES = ("off", "fxaa", "smaa")
POSTFX_RANGES: dict[str, tuple[int, int, int]] = {
    # field:                 (low, high, neutral)
    "postfx_sharpen":         (0, 100, 0),
    "postfx_brightness":      (-50, 50, 0),
    "postfx_contrast":        (50, 150, 100),
    "postfx_saturation":      (0, 200, 100),
    "postfx_gamma":           (50, 200, 100),
    "postfx_temperature":     (-100, 100, 0),
    "postfx_bloom":           (0, 100, 0),
    "postfx_bloom_threshold": (0, 100, 70),
    "postfx_vignette":        (0, 100, 0),
    "postfx_grain":           (0, 100, 0),
}
POSTFX_NEUTRAL: dict[str, Any] = {
    "postfx_aa": "off",
    **{name: neutral for name, (_, _, neutral) in POSTFX_RANGES.items()},
    "postfx_dedither": False,
}

REWIND_LEVELS = {
    "off":   None,          # PSX_REWIND=0; no snapshots taken at all
    "short": (50, 15),      # the runtime's own default, ~12.5 s at 60 Hz
    "long":  (200, 15),     # the runtime's maximum depth, ~50 s at 60 Hz
}

# The runtime's own cap was raised 4 -> 8 (tuning/patches/0003). The UI stops
# at 6: on the hardware this was developed against, 8x allocated and reported
# "internal scale 8x" but then produced no frames at all, which looks like an
# implementation ceiling rather than a performance one. The config loader
# throws above the runtime cap, so this must never exceed it.
MAX_SUPERSAMPLING = 6
RECOMMENDED_SUPERSAMPLING = 5

# ...but 5x is only "recommended" at the stock 30 Hz game cadence, which is
# what the 5x/6x/8x ladder was measured at (tuning/NOTES.md "Internal
# resolution"). At 60 FPS the same setting is the thing that breaks the mode.
#
# Measured, tuning/NOTES.md part 10: with native_60fps on and supersampling 5,
# the host produced 2580 presents against 3823 emulated VBlanks and the 60 FPS
# judge returned FAIL_HOST on a window that had 98% of its frames on one field
# - i.e. the GAME was keeping up and the RENDERER was not. Dropping to 2x
# removed the host failure entirely (backoffs 0, verdict 0).
#
# So the ceiling is cadence-dependent, and nothing in the UI used to say so.
RECOMMENDED_SUPERSAMPLING_60FPS = 2
# Above this, warn. 3x is the last value with headroom to spare at 60; it is a
# warning rather than a clamp because it is GPU-dependent and a fast card may
# well hold 4x - the player can see the live frame rate on the Play page.
SUPERSAMPLING_60FPS_WARN_ABOVE = 3
# Native 120 FPS draws twice the frames of 60, so the renderer runs out at a
# lower internal resolution. Not measured yet; one step below the 60 FPS
# warning is the cautious reading of the same measurement.
RECOMMENDED_SUPERSAMPLING_120FPS = 2
SUPERSAMPLING_120FPS_WARN_ABOVE = 2


def recommended_supersampling(native_60fps: bool,
                              native_120fps: bool = False) -> int:
    """The internal-resolution multiple to recommend for a given cadence."""
    if native_60fps and native_120fps:
        return RECOMMENDED_SUPERSAMPLING_120FPS
    return (RECOMMENDED_SUPERSAMPLING_60FPS if native_60fps
            else RECOMMENDED_SUPERSAMPLING)


def native_120fps_active(settings: "Settings") -> bool:
    """120 is a refinement of 60: it only applies with 60 FPS on."""
    return bool(settings.native_60fps and settings.native_120fps)


@dataclass
class Settings:
    # --- disc -------------------------------------------------------------
    disc_path: str = ""
    disc_verified: bool = False
    disc_sha1: str = ""

    # --- video ------------------------------------------------------------
    renderer: str = "opengl"
    aspect: str = "16:9"
    # Tri-state, not a bool: 0 windowed, 1 borderless desktop, 2 exclusive.
    # Alt+Enter / Ctrl+F also toggle this at runtime.
    fullscreen_mode: int = 0
    # 0/0 = let the runtime choose. Otherwise these pin the output canvas in
    # physical pixels; unlike the old width-only setting, height is not inferred
    # from the content aspect.
    window_width: int = 0
    window_height: int = 0
    integer_scaling: bool = False

    # How the image fills the output canvas:
    #   letterbox - preserve aspect, bars on the short axis (default, no distortion)
    #   stretch   - fill the canvas exactly, ignoring aspect (distorts)
    #   fill      - preserve aspect and scale until the canvas is covered,
    #               cropping the overflow (no distortion, loses edges)
    #   fit_width - width ALWAYS spans the display; bars top/bottom when the
    #               image is shorter, cropped when taller. Never side bars.
    scaling_mode: str = "letterbox"

    # Shape of the OUTPUT CANVAS, deliberately separate from `aspect` (which is
    # the shape the GAME renders at). "auto" means "same as aspect", which is
    # what every older setting assumed.
    #
    # Pan & Scan needs them apart: the game renders a true 4:3 frustum - so it
    # never widens its field of view into level edges that were never authored -
    # while the canvas is 16:9 and `present_zoom` scales the picture up until it
    # fills, cropping top and bottom. Without this split the launcher would size
    # a 4:3 window for a 4:3 aspect and leave nothing to crop.
    output_aspect: str = "auto"

    # Pan-and-scan dial, 0..100: 0 = letterbox (bars), 100 = fill (crop the
    # overflow). Aspect is exact at every value, so it never stretches - it only
    # trades bars for crop. -1 = follow scaling_mode (historical behaviour).
    present_zoom: int = -1
    # Vertical pan for the zoomed window, in source scanlines out of 240.
    # POSITIVE reveals more of the TOP (rescues a top-edge HUD).
    present_pan: int = 0
    # Partial stretch, 0..100. Zoom trades bars for crop without distorting;
    # this trades a little distortion for the rest. 100 fills the canvas
    # exactly. Small values are invisible in motion and cost no picture at all,
    # which is why they are usually preferable to cropping.
    present_stretch: int = 0

    # Overscan crop, in PS1 scanlines out of 240 (scale independent).
    #
    # Many PS1 titles draw fewer than 240 lines and leave the rest genuinely
    # black. Those bars are part of the IMAGE, not the presentation, so no
    # scaling mode removes them - the source rect has to be trimmed instead.
    # Crash 2 leaves roughly 8 lines top and bottom.
    overscan_top: int = 0
    overscan_bottom: int = 0
    overscan_left: int = 0
    overscan_right: int = 0

    # Which widescreen implementation to use when aspect != 4:3.
    #
    # False -> GTE X-squash + stretched present. This is the classic
    #   DuckStation/Beetle widescreen hack: squash the projection horizontally,
    #   present stretched, net result is a genuinely wider field of view. Works
    #   on any title with no per-game data.
    # True  -> "native-wide", which renders extra columns instead of squashing.
    #   Higher quality: no horizontal stretch, so the HUD and 2D art keep their
    #   authored proportions.
    #
    #   CORRECTION to what was recorded here: native-wide does NOT need
    #   per-game viewport data. The runtime derives the reveal from the live
    #   display width and the target aspect (gpu.c ws_nw_configured_offset),
    #   which is 85 px per side for Crash 2's 512-wide display at 16:9. The
    #   original "nw_extra stays 0" reading was native-wide never ACTIVATING -
    #   it is gated on the gameplay detector - and predates gte_game_mode being
    #   turned on below.
    #
    #   Keep it False, but for the COST, not the reason previously given here.
    #   The "reveals 170 px that provably contain no geometry" claim was
    #   RETRACTED: it generalised from a single overhang sample. A 78,769-prim
    #   draw census then found 5,735 primitives extending past the canonical
    #   window (xmin -429, xmax 629 against 512). Geometry does reach into the
    #   margins.
    #
    #   The real reason to leave it False: mode 2 costs ~280 MB of GL surfaces
    #   at supersampling 5, for a reveal the cheaper squash already delivers.
    #   Both modes widen the same field of view - mode 1 squashes the
    #   projection, mode 2 renders extra columns - so this is a quality/memory
    #   trade, not a capability one. Toggle it live with the debug server's
    #   `ws_nw` command instead of shipping it on.
    #   See tuning/NOTES.md "Widescreen, part 5" and "part 6".
    widescreen_native_wide: bool = False

    # --- image quality (settings.toml only - no env override exists) -------
    texture_filter: str = "nearest"     # nearest | bilinear
    crt_filter: str = "raw"             # raw | crt | composite | trinitron

    # Present-time reconstruction, i.e. how the internal buffer is resampled
    # down to the window. "plain" is a single tap that averages only 2x2 texels
    # no matter how far the image is being shrunk, so most supersampled detail
    # is thrown away and the surviving samples shift under motion - the usual
    # cause of texture shimmer at high internal resolution. "bicubic" is the
    # Catmull-Rom path already in the present shader.
    present_filter: str = "bicubic"   # plain | sharp | bicubic
    antialiasing: bool = False

    # --- post-processing (PSX_POSTFX, runtime gpu_postfx.h) -----------------
    # Runs on the finished picture at screen resolution, after the downsample
    # filter; the game's own menus are part of the picture, the launcher's
    # overlays and the pause menu are not. POSTFX_RANGES has the bounds.
    postfx_aa: str = "off"              # off | fxaa | smaa
    postfx_sharpen: int = 0
    postfx_brightness: int = 0
    postfx_contrast: int = 100
    postfx_saturation: int = 100
    postfx_gamma: int = 100
    postfx_temperature: int = 0
    postfx_bloom: int = 0
    postfx_bloom_threshold: int = 70
    postfx_vignette: int = 0
    postfx_grain: int = 0
    # Soften the checkerboard dither painted into the game's textures. Applied
    # where textures are sampled, so it also works with the chain above off.
    postfx_dedither: bool = False
    geometry_correction: bool = False
    perspective_texturing: bool = False

    # PGXP tier-2: propagate sub-pixel precision through CPU arithmetic, not
    # just the GTE. The framework defaults this OFF, which only bounds
    # *coverage* - but partial coverage is what causes geometry to pop between
    # precise and rounded positions on an engine like Crash 2 that does a lot of
    # its transform work on the CPU. Enable it whenever geometry_correction is
    # on, or the correction is worse than leaving it off.
    pgxp_cpu_mode: bool = True

    # Internal-resolution supersampling (SSAA). Goes into game.toml as
    # [runtime] video_supersampling, NOT an env var. The runtime clamps to
    # SW_MAX_INTERNAL_SCALE (4) and reports the value it actually used, so
    # treat this as a *request* and read the effective value back from the log.
    supersampling: int = 1

    # --- frame pacing / high refresh --------------------------------------
    # -1 adaptive, 0 immediate, 1 vsync. The runtime notes that vsync only
    # clocks ~60 Hz panels, so on a high-refresh display 0 plus the wall-clock
    # pacer is usually what you want.
    vsync: int = 0
    frame_interpolation: bool = False
    # 0 = follow the host panel; otherwise must be >= 90 or the runtime ignores it.
    frame_interpolation_fps: int = 0
    frame_blend: bool = False
    # Native 60 FPS for SCUS-94154. Not interpolation and not a host pacer:
    # it removes the second VSync wait the game uses to round every frame up
    # to two fields, so the game loop itself runs at 60. World speed is
    # unchanged because the engine already multiplies motion by the measured
    # frame time (17 ticks instead of 34). The launcher always pairs this with
    # 200% virtual PS1 CPU and Prefer 60; there are no separate tuning controls.
    native_60fps: bool = False
    # Native 120 FPS (experimental), on top of native_60fps: the same open
    # frame gate at twice the field rate (crash2_60fps.h). Physics runs every
    # 120 Hz field, scripts keep the game's 30 Hz step and the music keeps
    # its tempo. The runtime steps down to 60 by itself when a scene cannot
    # hold 120. Frame interpolation is not used with it.
    native_120fps: bool = False
    # SCUS-94154 assists. Opt-in, and they can change saved progression.
    cheat_infinite_lives: bool = False
    # "off", "keep_masks" or "no_damage". Two levels rather than two separate
    # toggles, because they are two strengths of the same protection and a
    # player should not have to work out how they interact.
    #
    # keep_masks holds the mask count at 2, so a hit is always absorbed. It is
    # a pure data write, indistinguishable to the engine from having collected
    # them. no_damage instead flips the one instruction (0x8001CE34) that asks
    # "is Crash invincible right now" - which the game already answers yes to
    # while the gold Aku Aku mask is up, so it permits everything that mask
    # permits, without lapsing after 15 seconds.
    cheat_aku_aku: str = "off"

    # --- performance ------------------------------------------------------
    fast_loading: bool = False
    cd_speed_boost: bool = False
    turbo_key: str = "Tab"

    # --- audio ------------------------------------------------------------
    volume: int = 100
    mute: bool = False
    # Output latency: how much audio the runtime keeps buffered ahead of the
    # speakers, in milliseconds. Lower reacts faster; too low and the buffer
    # runs dry and crackles. 90 is comfortable on a normal desktop; 180 was
    # the old hard-coded value and is the safe choice on a busy machine.
    audio_latency_ms: int = 90
    # Higher-quality SPU mix (float re-render, verified against the canonical
    # mix and dropped automatically if it ever disagrees).
    audio_hq: bool = False
    # Diagnostics for the sound cut-off investigation.
    audio_legacy: bool = False
    audio_shadow: bool = False

    # --- performance ------------------------------------------------------
    # Rewind buffer length: "off", "short" or "long". See REWIND_LEVELS.
    # Defaults to "short", which reproduces what the runtime already did
    # unprompted - this setting exposes it rather than changing it.
    rewind: str = "short"
    # Compile streamed level code (overlays) to native instead of letting it
    # fall back to the MIPS interpreter. Needs a C compiler on PATH, which the
    # launcher supplies from psxrecomp's own clang pack.
    native_overlays: bool = True

    # --- developer --------------------------------------------------------
    # Reveals the Advanced page. Off for players: the controls behind it are
    # measurement tools that make the game slower or worse, and none of them
    # should be reachable by accident. While this is off the launcher also
    # refuses to pass any diagnostic to the runtime, so a settings file
    # carried over from a debugging session cannot leak into normal play.
    developer_mode: bool = False

    # --- diagnostics ------------------------------------------------------
    # Non-zero opens the runtime's TCP debug server, which is how we read the
    # SPU event ring (spu_events / spu_voices).
    debug_port: int = 0
    # NOT a diagnostic: it only prints "[FPS] ..." lines that the launcher
    # already captures, and the Play page's performance readout is parsed from
    # them. It was listed as one, and because active_diagnostics() reports
    # anything deviating from its default, a player who turned it OFF made the
    # Play page announce "Diagnostics active: fps_telemetry" - a warning that
    # fired precisely when none were. It is an ordinary setting on the
    # Performance page now, and stays on so the readout works.
    fps_telemetry: bool = True
    # Summarises, every ~5s, how the game picks SPU voices: key-ons per voice
    # index plus each voice's phase and envelope level. Reading it needs no
    # debug port - it prints straight to the Log page.
    voice_alloc_trace: bool = False
    # Enables the runtime's own subsystem profiler (PSX_RUNTIME_PERF_DIAG).
    #
    # It prints one "runtime cadence:" line every few seconds attributing
    # milliseconds-per-second to guest work, pacer wait, autocapture, provider
    # poll, GL upload/texture/draw, plus native-vs-interp dispatch deltas and
    # the hottest native overlay PC. main.cpp calls it "lightweight
    # production-safe" - it adds no per-block or per-instruction recording, so
    # unlike the other entries here it does NOT degrade playback.
    #
    # It is listed as a diagnostic anyway because it is a measurement tool and
    # prints continuously. Nothing in this project had ever switched it on.
    perf_diag: bool = False
    # How often the profiler reports, in milliseconds. The runtime defaults to
    # 5000; below ~1000 the lines arrive faster than they can be read.
    perf_diag_interval_ms: int = 5000
    # Optional "start:end" frame range. When set, the runtime also emits one
    # [BENCH] line summarising just that window - wall time, guest work, pacer
    # wait, autocapture, provider poll and native/interp dispatch counts. That
    # is the before/after harness; leave empty for continuous reporting only.
    perf_bench_window: str = ""
    # Runs streamed level code in the MIPS interpreter instead of the native
    # shards. Slow, but it is the reference: if something works here and not
    # natively, the recompiler's codegen is the bug.
    overlay_interpreter: bool = False
    # Stronger form: EVERY game function, main executable included, runs in
    # the interpreter. overlay_interpreter leaves the main exe compiled, so a
    # codegen fault there survives that test. This one does not.
    force_interpreter: bool = False

    # --- input ------------------------------------------------------------
    # action -> key name. Empty means "use the runtime default".
    bindings: dict[str, str] = field(default_factory=dict)
    # PS1 button -> controller source(s), written to input.ini [mapping]
    # (padbinds.py). Empty means "leave input.ini's own map alone".
    pad_bindings: dict[str, str] = field(default_factory=dict)
    # Stick deadzone, percent of full travel. 10 is the runtime's own default
    # (3277 raw); it shapes both the analog sticks and trigger thresholds.
    pad_deadzone: int = 10
    # Drive player 1 from the keyboard AND every connected controller at once.
    # On by default: a Release runtime otherwise pins player 1 to "keyboard"
    # and never opens a gamepad, so a pad would appear dead.
    merge_all_input: bool = True

    # Which numbered savestate slot the quick save/load keys use. It is an
    # ordinary slot, so a quick save still appears in the in-game slot menu
    # with its thumbnail - the keys are a shortcut, not a separate store.
    quick_save_slot: int = 0

    # --- launcher ---------------------------------------------------------
    last_page: str = "play"
    window_geometry: str = ""

    def canvas_aspect(self) -> str:
        """Shape of the output canvas.

        Distinct from `aspect`, which is the shape the game RENDERS at. They are
        equal for everything except Pan & Scan, where a 4:3 render is zoomed to
        fill a 16:9 canvas.
        """
        return self.aspect if self.output_aspect == "auto" else self.output_aspect

    def clamp(self) -> "Settings":
        """Coerce out-of-range values back to something usable.

        Hand-edited or older settings files should degrade to defaults rather
        than propagate a bad value into the runtime command line.
        """
        # Fall back to OpenGL for anything unknown AND for vulkan, which is a
        # valid name the runtime will not actually run (see
        # SELECTABLE_RENDERERS). A settings file carrying it - written before
        # the option was withdrawn - would otherwise crash on launch.
        if self.renderer not in SELECTABLE_RENDERERS:
            self.renderer = "opengl"
        if self.aspect not in ASPECTS:
            self.aspect = "16:9"
        # The runtime hard-clamps supersampling to SW_MAX_INTERNAL_SCALE.
        self.supersampling = max(1, min(MAX_SUPERSAMPLING, int(self.supersampling or 1)))
        self.volume = max(0, min(100, int(self.volume or 0)))
        # Mirrors the runtime's own accepted range for [audio] buffer_ms.
        self.audio_latency_ms = max(30, min(500, int(self.audio_latency_ms or 90)))
        if self.vsync not in (-1, 0, 1):
            self.vsync = 0
        if self.cheat_aku_aku not in CHEAT_AKU_LEVELS:
            self.cheat_aku_aku = "off"
        if self.rewind not in REWIND_LEVELS:
            self.rewind = "short"
        # Mirrors runtime_perf_init's own clamp (main.cpp).
        self.perf_diag_interval_ms = max(
            250, min(600000, int(self.perf_diag_interval_ms or 5000)))
        window = (self.perf_bench_window or "").strip()
        self.perf_bench_window = (
            window if _BENCH_WINDOW_RE.fullmatch(window) else "")
        if self.fullscreen_mode not in (0, 1, 2):
            self.fullscreen_mode = 0
        if self.texture_filter not in ("nearest", "bilinear"):
            self.texture_filter = "nearest"
        if self.crt_filter not in ("raw", "crt", "composite", "trinitron"):
            self.crt_filter = "raw"
        if self.present_filter not in ("plain", "sharp", "bicubic"):
            self.present_filter = "bicubic"
        if self.postfx_aa not in POSTFX_AA_MODES:
            self.postfx_aa = "off"
        for name, (low, high, neutral) in POSTFX_RANGES.items():
            try:
                value = int(getattr(self, name))
            except (TypeError, ValueError):
                value = neutral
            setattr(self, name, max(low, min(high, value)))
        self.postfx_dedither = bool(self.postfx_dedither)
        if self.scaling_mode not in SCALING_MODES:
            self.scaling_mode = "letterbox"
        # Cropping more than a quarter of the frame is a mistake, not a setting.
        for name in ("overscan_top", "overscan_bottom",
                     "overscan_left", "overscan_right"):
            setattr(self, name, max(0, min(60, int(getattr(self, name) or 0))))
        # The loader rejects an output size outside these bounds; 0/0 means
        # "auto". Migrate old width-only settings by deriving the missing height
        # once, then persist an exact pair on the next save.
        if self.output_aspect not in ("auto",) + OUTPUT_ASPECTS:
            self.output_aspect = "auto"
        self.present_zoom = (-1 if int(self.present_zoom) < 0
                             else min(100, int(self.present_zoom)))
        self.present_pan = max(-120, min(120, int(self.present_pan or 0)))
        self.present_stretch = max(0, min(100, int(self.present_stretch or 0)))
        width = int(self.window_width or 0)
        height = int(self.window_height or 0)
        if width and not height:
            # Derive from the CANVAS shape, never from the render aspect - under
            # Pan & Scan those differ and using `aspect` would rebuild a 4:3 box.
            num, den = (int(part) for part in self.canvas_aspect().split(":"))
            height = round(width * den / num)
        if width == 0:
            height = 0
        if not (width == 0 or (640 <= width <= 7680 and 360 <= height <= 4320)):
            width = height = 0
        self.window_width = width
        self.window_height = height
        # The runtime silently ignores an interpolation target below 90.
        fps = int(self.frame_interpolation_fps or 0)
        self.frame_interpolation_fps = fps if (fps == 0 or fps >= 90) else 0
        self.debug_port = max(0, min(65535, int(self.debug_port or 0)))
        # The runtime exposes 12 slots.
        self.quick_save_slot = max(0, min(11, int(self.quick_save_slot or 0)))
        from .keybinds import normalize
        self.bindings = normalize(self.bindings)
        from . import padbinds
        self.pad_bindings = padbinds.normalize(self.pad_bindings)
        try:
            deadzone = int(self.pad_deadzone)
        except (TypeError, ValueError):
            deadzone = padbinds.DEADZONE_DEFAULT
        self.pad_deadzone = max(0, min(padbinds.DEADZONE_MAX, deadzone))
        return self


def load(path: Path) -> Settings:
    """Read settings, falling back to defaults for anything missing or broken."""
    if not path.is_file():
        return Settings()
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Settings()
    if not isinstance(raw, dict):
        return Settings()

    known = {f.name for f in fields(Settings)}
    values = {k: v for k, v in raw.items() if k in known}
    # cheat_aku_aku shipped as a checkbox first. A settings file written by that
    # build carries a bool, and load() filters by field NAME only, so without
    # this the bool would land in the field and clamp() would quietly reset a
    # player's choice to off.
    if isinstance(values.get("cheat_aku_aku"), bool):
        values["cheat_aku_aku"] = "keep_masks" if values["cheat_aku_aku"] else "off"
    return Settings(**values).clamp()


# --------------------------------------------------------------------------
# Diagnostics
#
# These change the runtime's behaviour for MEASUREMENT, not for playing, and
# some of them actively degrade the game. `audio_legacy` disables the audio
# bridge (no rate control, no fill target) and produced 146 underruns in a
# 20-second window - audible gaps that looked exactly like an SPU bug and cost
# a long investigation. It was exposed next to Volume as though it were an
# ordinary quality option.
#
# Nothing here belongs on a normal settings page. The UI keeps them on their own
# page behind a warning, and the Play page reports whenever any is active.
# --------------------------------------------------------------------------
DIAGNOSTIC_SETTINGS: dict[str, str] = {
    "audio_legacy": (
        "Disables the audio bridge and falls back to blind queue pushing - "
        "no rate control, no buffer target. Causes audible dropouts."
    ),
    "audio_shadow": (
        "Substitutes an alternate float SPU mix. Changes how the game sounds."
    ),
    "debug_port": (
        "Opens the TCP debug server, which only exists in the debugtools build "
        "- so the launcher runs that build instead of the release one, with "
        "tracing overhead."
    ),
    "voice_alloc_trace": (
        "Every ~5s, logs how the game is picking SPU voices. Does not change "
        "how the game sounds - it only counts and prints. For diagnosing the "
        "sound effect cut-outs."
    ),
    "perf_diag": (
        "Prints a per-subsystem frame-time breakdown every few seconds: guest "
        "work, pacer wait, GL upload/draw, native vs interpreted dispatch. "
        "This is the tool for finding out WHERE the time goes. It adds no "
        "per-instruction tracing, so it does not itself slow the game down."
    ),
    "overlay_interpreter": (
        "Runs level code in the MIPS interpreter instead of native shards. "
        "Much slower. It is the reference behaviour: if a level works with "
        "this on and not off, the recompiler's output is wrong for that level."
    ),
    "force_interpreter": (
        "Runs ALL game code in the interpreter - the main executable too, "
        "which the level-code switch above leaves compiled. Very slow. If a "
        "bug survives even this, the recompiler is fully cleared and the "
        "fault is in the emulated hardware (GTE, GPU, CD, timers)."
    ),
}


def _diagnostic_default(name: str) -> Any:
    """The value a diagnostic has when it is switched off."""
    for f in fields(Settings):
        if f.name == name:
            return f.default
    return None


# Human names for the places a diagnostic is reported back to the user. The UI
# used to print the raw field name, so the Play page warned about
# "voice_alloc_trace" and "overlay_interpreter" - accurate, and meaningless to
# anyone who has not read this file.
DIAGNOSTIC_LABELS: dict[str, str] = {
    "audio_legacy": "legacy audio path",
    "audio_shadow": "alternate sound mixing",
    "debug_port": "debug server",
    "voice_alloc_trace": "sound voice tracing",
    "overlay_interpreter": "level code interpreted",
    "force_interpreter": "all code interpreted",
}


def diagnostic_label(name: str) -> str:
    """Display name for a diagnostic. Falls back to the field name so a newly
    added diagnostic is still reported, just less prettily."""
    return DIAGNOSTIC_LABELS.get(name, name.replace("_", " "))


def active_diagnostics(settings: Settings) -> list[str]:
    """Diagnostics currently deviating from their default (i.e. switched on)."""
    return [
        name
        for name in DIAGNOSTIC_SETTINGS
        if getattr(settings, name, None) != _diagnostic_default(name)
    ]


def reset_diagnostics(settings: Settings) -> Settings:
    """Return every diagnostic to its default. One click back to normal play."""
    for name in DIAGNOSTIC_SETTINGS:
        setattr(settings, name, _diagnostic_default(name))
    return settings


# --------------------------------------------------------------------------
# Presets
#
# Gameplay settings only - never diagnostics, so applying a preset can never
# switch on something that degrades the game.
# --------------------------------------------------------------------------
# The enhanced presets add SMAA, which cleans the edges bicubic downsampling
# leaves, and light contrast-adaptive sharpening to win back the crispness the
# same downsample softens. Colour, bloom, vignette and grain stay neutral:
# they are taste, not repair.
POSTFX_ENHANCED: dict[str, Any] = {
    **POSTFX_NEUTRAL,
    "postfx_aa": "smaa",
    "postfx_sharpen": 35,
}

PRESETS: dict[str, dict[str, Any]] = {
    "Authentic": {
        "supersampling": 1,
        "aspect": "4:3",
        "output_aspect": "auto",
        "present_zoom": -1,
        "present_pan": 0,
        "present_stretch": 0,
        "scaling_mode": "letterbox",
        "texture_filter": "nearest",
        "present_filter": "plain",
        "crt_filter": "raw",
        "antialiasing": False,
        "frame_interpolation": False,
        "frame_interpolation_fps": 0,
        "overscan_top": 0,
        "overscan_bottom": 0,
        "geometry_correction": False,
        "perspective_texturing": False,
        **POSTFX_NEUTRAL,
    },
    # A mild widescreen that fills the screen without throwing picture away.
    #
    # 14:9 widens the field of view by ~1.17x rather than 16:9's ~1.33x, so it
    # reaches only about half as far past the edge Crash 2's levels were
    # authored to - which is where scenery pops in and out. Trimming the 12
    # blank scanlines the game leaves at top and bottom brings the DRAWN image
    # to roughly 16:9 on its own, so the remaining gap is a few percent and the
    # stretch dial closes it invisibly. Zoom stays at 0: with the blank lines
    # already gone there is nothing left worth cropping.
    "Enhanced": {
        "supersampling": RECOMMENDED_SUPERSAMPLING,
        "aspect": "14:9",
        "output_aspect": "16:9",
        "widescreen_native_wide": False,
        "scaling_mode": "letterbox",
        "present_zoom": 0,
        "present_pan": 0,
        "present_stretch": 100,
        "texture_filter": "bilinear",
        "present_filter": "bicubic",
        "crt_filter": "raw",
        "antialiasing": True,
        "frame_interpolation": True,
        "frame_interpolation_fps": 0,
        # Crash 2 draws rows 12..227 of its 240-line field; the rest is genuinely
        # black. Trim it so the picture reaches the top and bottom of the screen.
        "overscan_top": 12,
        "overscan_bottom": 12,
        # PGXP stays off: on this engine it trades texture shimmer for
        # geometry pop-in and seam lines.
        "geometry_correction": False,
        "perspective_texturing": False,
        **POSTFX_ENHANCED,
    },
    "Performance": {
        "supersampling": 2,
        "aspect": "14:9",
        "output_aspect": "16:9",
        "widescreen_native_wide": False,
        "scaling_mode": "letterbox",
        "present_zoom": 0,
        "present_pan": 0,
        "present_stretch": 100,
        "texture_filter": "nearest",
        "present_filter": "plain",
        "crt_filter": "raw",
        "antialiasing": False,
        "frame_interpolation": False,
        "frame_interpolation_fps": 0,
        "overscan_top": 12,
        "overscan_bottom": 12,
        "geometry_correction": False,
        "perspective_texturing": False,
        **POSTFX_NEUTRAL,
    },
    # The real widescreen hack: squash the GTE projection and stretch the
    # present, which genuinely widens the field of view. Kept because a wider
    # view is a real benefit, but it walks past the authored edge of Crash 2's
    # levels, so geometry appears and disappears at the frame border.
    # Ultrawide panels, WITHOUT widening the field of view.
    #
    # Identical to Enhanced except the canvas: the game still renders 14:9, so
    # the margin stays 43 px per side and no new scenery pops in. The zoom and
    # stretch dials then fit that picture into a 21:9 window.
    #
    # Deliberately NOT a full edge-to-edge fill. With the 12 blank scanlines
    # trimmed the drawn band is about 1.73:1; reaching 2.33:1 would need ~35%
    # horizontal distortion (Enhanced needs 2%) or throwing away another ~26%
    # of the height. Stretch is left at 0 and zoom at 0, so the picture is
    # correct and pillarboxed; a player who prefers to trade some of that for
    # width can raise either dial themselves and see exactly what it costs.
    "Ultrawide (21:9 screen)": {
        "supersampling": RECOMMENDED_SUPERSAMPLING,
        "aspect": "14:9",
        "output_aspect": "21:9",
        "widescreen_native_wide": False,
        "scaling_mode": "letterbox",
        "present_zoom": 0,
        "present_pan": 0,
        "present_stretch": 0,
        "texture_filter": "bilinear",
        "present_filter": "bicubic",
        "crt_filter": "raw",
        "antialiasing": True,
        "frame_interpolation": True,
        "frame_interpolation_fps": 0,
        "overscan_top": 12,
        "overscan_bottom": 12,
        "geometry_correction": False,
        "perspective_texturing": False,
        **POSTFX_ENHANCED,
    },
    "Widescreen (wider view)": {
        "supersampling": RECOMMENDED_SUPERSAMPLING,
        "aspect": "16:9",
        "output_aspect": "auto",
        "widescreen_native_wide": False,
        "scaling_mode": "fill",
        "present_zoom": -1,
        "present_pan": 0,
        "present_stretch": 0,
        "texture_filter": "bilinear",
        "present_filter": "bicubic",
        "crt_filter": "raw",
        "antialiasing": True,
        "frame_interpolation": True,
        "frame_interpolation_fps": 0,
        "overscan_top": 0,
        "overscan_bottom": 0,
        "geometry_correction": False,
        "perspective_texturing": False,
        **POSTFX_ENHANCED,
    },
}

PRESET_NOTES: dict[str, str] = {
    "Authentic": "Original 4:3 presentation, unfiltered - as the console output it.",
    "Enhanced": ("Slightly wider view (14:9) filling a 16:9 screen, with the "
                 "game's own blank scanlines trimmed so the picture reaches "
                 "every edge. Nothing of the drawn image is cropped, and it "
                 "reaches half as far past the level edges as full widescreen, "
                 "so far less scenery pops in. A good default."),
    "Performance": "Same fit as Enhanced, lower internal resolution for weaker GPUs.",
    "Ultrawide (21:9 screen)": ("For a 21:9 monitor. The view stays as wide as "
                               "Enhanced - only the window gets wider - so no "
                               "extra scenery pops in. The picture is centred "
                               "with bars at the sides; raise Zoom or Stretch "
                               "on this page to trade some accuracy for width."),
    "Widescreen (wider view)": ("Genuinely wider field of view, but Crash 2's "
                                "levels end at the 4:3 edge, so scenery can "
                                "appear and vanish at the frame border."),
}


def postfx_string(settings: Settings) -> str:
    """PSX_POSTFX for the runtime: only what differs from neutral.

    "" when nothing is set, in which case the variable is not passed and the
    runtime's chain does not run.
    """
    parts = []
    if settings.postfx_aa != "off":
        parts.append("aa=%s" % settings.postfx_aa)
    for name, (_, _, neutral) in POSTFX_RANGES.items():
        value = int(getattr(settings, name))
        if value != neutral:
            parts.append("%s=%d" % (name[len("postfx_"):], value))
    if settings.postfx_dedither:
        parts.append("dedither=1")
    return ";".join(parts)


def apply_preset(settings: Settings, name: str) -> Settings:
    """Apply a preset over the current settings, leaving diagnostics alone."""
    for key, value in PRESETS.get(name, {}).items():
        setattr(settings, key, value)
    return settings.clamp()


def matching_preset(settings: Settings) -> str | None:
    """Which preset the current settings correspond to, if any."""
    for name, values in PRESETS.items():
        if all(getattr(settings, k, None) == v for k, v in values.items()):
            return name
    return None


def save(path: Path, settings: Settings) -> None:
    """Atomically write settings to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(settings), indent=2, ensure_ascii=False)

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Never leave a stray temp file behind on failure.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
