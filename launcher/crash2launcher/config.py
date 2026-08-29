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
ASPECTS = ("4:3", "16:9", "21:9")

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
    aspect: str = "4:3"
    fullscreen: bool = False
    integer_scaling: bool = False

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
            self.aspect = "4:3"
        # The runtime hard-clamps supersampling to SW_MAX_INTERNAL_SCALE.
        self.supersampling = max(1, min(MAX_SUPERSAMPLING, int(self.supersampling or 1)))
        self.volume = max(0, min(100, int(self.volume or 0)))
        if self.vsync not in (-1, 0, 1):
            self.vsync = 0
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
