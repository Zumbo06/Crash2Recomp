"""Persistent launcher settings.

Stored as JSON next to the executable so the whole bundle stays portable - copy
the folder to a USB stick and the settings travel with it. Writes go through a
temp file and an atomic replace, because a half-written settings.json that kills
the launcher on next start is a genuinely annoying failure mode.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

RENDERERS = ("opengl", "vulkan", "software")
ASPECTS = ("4:3", "16:9")

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

# The runtime's own cap was raised 4 -> 8 (tuning/patches/0003). The UI stops
# at 6: on the hardware this was developed against, 8x allocated and reported
# "internal scale 8x" but then produced no frames at all, which looks like an
# implementation ceiling rather than a performance one. The config loader
# throws above the runtime cap, so this must never exceed it.
MAX_SUPERSAMPLING = 6
RECOMMENDED_SUPERSAMPLING = 5


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
    #   Higher quality in principle, but it needs per-game viewport data; with
    #   none, nw_extra stays 0 and NOTHING widens. That is the framework
    #   default and why widescreen silently did nothing on Crash 2.
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
    smooth_60fps: bool = False
    frame_blend: bool = False

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
    # Drive player 1 from the keyboard AND every connected controller at once.
    # On by default: a Release runtime otherwise pins player 1 to "keyboard"
    # and never opens a gamepad, so a pad would appear dead.
    merge_all_input: bool = True

    # Which numbered savestate slot the quick save/load keys use. It is an
    # ordinary slot, so a quick save still appears in the in-game slot menu
    # with its thumbnail - the keys are a shortcut, not a separate store.
    quick_save_slot: int = 0

    # --- mods -------------------------------------------------------------
    enabled_mods: list[str] = field(default_factory=list)

    # --- launcher ---------------------------------------------------------
    last_page: str = "play"
    window_geometry: str = ""

    def clamp(self) -> "Settings":
        """Coerce out-of-range values back to something usable.

        Hand-edited or older settings files should degrade to defaults rather
        than propagate a bad value into the runtime command line.
        """
        if self.renderer not in RENDERERS:
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
        if self.fullscreen_mode not in (0, 1, 2):
            self.fullscreen_mode = 0
        if self.texture_filter not in ("nearest", "bilinear"):
            self.texture_filter = "nearest"
        if self.crt_filter not in ("raw", "crt", "composite", "trinitron"):
            self.crt_filter = "raw"
        if self.present_filter not in ("plain", "sharp", "bicubic"):
            self.present_filter = "bicubic"
        if self.scaling_mode not in SCALING_MODES:
            self.scaling_mode = "letterbox"
        # Cropping more than a quarter of the frame is a mistake, not a setting.
        for name in ("overscan_top", "overscan_bottom",
                     "overscan_left", "overscan_right"):
            setattr(self, name, max(0, min(60, int(getattr(self, name) or 0))))
        # The loader rejects an output size outside these bounds; 0/0 means
        # "auto". Migrate old width-only settings by deriving the missing height
        # once, then persist an exact pair on the next save.
        width = int(self.window_width or 0)
        height = int(self.window_height or 0)
        if width and not height:
            num, den = (int(part) for part in self.aspect.split(":"))
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
    return Settings(**{k: v for k, v in raw.items() if k in known}).clamp()


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
PRESETS: dict[str, dict[str, Any]] = {
    "Authentic": {
        "supersampling": 1,
        "aspect": "4:3",
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
    },
    "Enhanced": {
        "supersampling": RECOMMENDED_SUPERSAMPLING,
        "aspect": "16:9",
        "widescreen_native_wide": False,
        "scaling_mode": "fill",
        "texture_filter": "bilinear",
        "present_filter": "bicubic",
        "crt_filter": "raw",
        "antialiasing": True,
        "frame_interpolation": True,
        "frame_interpolation_fps": 0,
        "overscan_top": 16,
        "overscan_bottom": 16,
        # PGXP stays off: on this engine it trades texture shimmer for
        # geometry pop-in and seam lines.
        "geometry_correction": False,
        "perspective_texturing": False,
    },
    "Performance": {
        "supersampling": 2,
        "aspect": "16:9",
        "widescreen_native_wide": False,
        "scaling_mode": "fill",
        "texture_filter": "nearest",
        "present_filter": "plain",
        "crt_filter": "raw",
        "antialiasing": False,
        "frame_interpolation": False,
        "frame_interpolation_fps": 0,
        "overscan_top": 16,
        "overscan_bottom": 16,
        "geometry_correction": False,
        "perspective_texturing": False,
    },
}

PRESET_NOTES: dict[str, str] = {
    "Authentic": "Original 4:3 presentation, unfiltered - as the console output it.",
    "Enhanced": "Widescreen, sharper image, smoother motion. A good default.",
    "Performance": "Lower internal resolution for weaker GPUs.",
}


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
