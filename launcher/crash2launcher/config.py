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
SCALING_MODES = ("letterbox", "stretch", "fill")

# The runtime's own cap was raised 4 -> 8 (tuning/patches/0003), but measurement
# says 8x is not usable: it allocates and reports "internal scale 8x", then
# produces no frames at all. 6x averages 60 fps with occasional dips to ~53;
# 5x is clean (min 59.6 over 68 samples). So the UI stops at 6 and treats 5 as
# the sweet spot. The config loader throws above the runtime cap, so this must
# never exceed it.
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
    scaling_mode: str = "letterbox"

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
    antialiasing: bool = False
    geometry_correction: bool = False
    perspective_texturing: bool = False

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
    # Diagnostics for the sound cut-off investigation.
    audio_legacy: bool = False
    audio_shadow: bool = False

    # --- performance ------------------------------------------------------
    # Compile streamed level code (overlays) to native instead of letting it
    # fall back to the MIPS interpreter. Needs a C compiler on PATH, which the
    # launcher supplies from psxrecomp's own clang pack.
    native_overlays: bool = True

    # --- diagnostics ------------------------------------------------------
    # Non-zero opens the runtime's TCP debug server, which is how we read the
    # SPU event ring (spu_events / spu_voices).
    debug_port: int = 0
    fps_telemetry: bool = True

    # --- input ------------------------------------------------------------
    # action -> key name. Empty means "use the runtime default".
    bindings: dict[str, str] = field(default_factory=dict)
    # Drive player 1 from the keyboard AND every connected controller at once.
    # On by default: a Release runtime otherwise pins player 1 to "keyboard"
    # and never opens a gamepad, so a pad would appear dead.
    merge_all_input: bool = True

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
        if self.vsync not in (-1, 0, 1):
            self.vsync = 0
        if self.fullscreen_mode not in (0, 1, 2):
            self.fullscreen_mode = 0
        if self.texture_filter not in ("nearest", "bilinear"):
            self.texture_filter = "nearest"
        if self.crt_filter not in ("raw", "crt", "composite", "trinitron"):
            self.crt_filter = "raw"
        if self.scaling_mode not in SCALING_MODES:
            self.scaling_mode = "letterbox"
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
