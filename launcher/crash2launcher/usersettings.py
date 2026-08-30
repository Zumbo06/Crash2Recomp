"""Write the runtime's ``settings.toml``.

This is a *different* file from ``game.toml`` and a different layer: the runtime
reads ``settings.toml`` from beside its own executable and layers it over the
bundled game.toml, so it is where per-user display choices belong. Several
options exist ONLY here - fullscreen mode, window width, the CRT filter and
texture filtering have no game.toml or environment equivalent.

The schema below mirrors ``save_user_settings`` in the framework's
``recompiler/src/config_loader.cpp``; keys the loader does not recognise are
ignored, and out-of-range values are rejected there, so we validate up front.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .config import Settings

# [video] crt_filter accepts exactly these.
CRT_FILTERS = ("raw", "crt", "composite", "trinitron")
TEXTURE_FILTERS = ("nearest", "bilinear")

# Fullscreen is a tri-state, not a bool.
FULLSCREEN_WINDOWED = 0
FULLSCREEN_BORDERLESS = 1
FULLSCREEN_EXCLUSIVE = 2

# The loader rejects a window_width outside this range.
MIN_WINDOW_WIDTH = 640
MAX_WINDOW_WIDTH = 7680


def _vsync_name(value: int) -> str:
    return "immediate" if value == 0 else ("adaptive" if value < 0 else "on")


def render(settings: Settings) -> str:
    """Build the settings.toml text for the current launcher settings."""
    lines = [
        "# psxrecomp user settings - written by the Crash 2 launcher.",
        "# Overrides the bundled game.toml; the command line overrides this file.",
        "",
        "[video]",
        f'renderer          = "{settings.renderer}"',
        f"supersampling     = {settings.supersampling}",
        f'aspect_ratio      = "{settings.aspect}"',
        f"fullscreen        = {settings.fullscreen_mode}",
        f'vsync             = "{_vsync_name(settings.vsync)}"',
        f'texture_filtering = "{settings.texture_filter}"',
        f'crt_filter        = "{settings.crt_filter}"',
        f"antialiasing      = {'true' if settings.antialiasing else 'false'}",
        f"geometry_correction   = {'true' if settings.geometry_correction else 'false'}",
        f"perspective_texturing = {'true' if settings.perspective_texturing else 'false'}",
        f"frame_interpolation = {'true' if settings.frame_interpolation else 'false'}",
        f"frame_interpolation_fps = {settings.frame_interpolation_fps}",
    ]
    # 0 means "let the runtime pick"; only pin a width when the user asked.
    if settings.window_width:
        lines.append(f"window_width      = {settings.window_width}")
    lines.append("")
    return "\n".join(lines)


def save(path: Path, settings: Settings) -> None:
    """Atomically write settings.toml next to the runtime executable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = render(settings)

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
