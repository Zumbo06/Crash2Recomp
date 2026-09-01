"""Write the runtime's ``settings.toml``.

This is a *different* file from ``game.toml`` and a different layer: the runtime
reads ``settings.toml`` from beside its own executable and layers it over the
bundled game.toml, so it is where per-user display choices belong. Several
options exist ONLY here - fullscreen mode, output size, the CRT filter and
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

# The loader rejects an output size outside these ranges.
MIN_WINDOW_WIDTH = 640
MAX_WINDOW_WIDTH = 7680
MIN_WINDOW_HEIGHT = 360
MAX_WINDOW_HEIGHT = 4320


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
    width, height = settings.window_width, settings.window_height
    if not (width and height) and settings.fullscreen_mode == FULLSCREEN_WINDOWED:
        # "Auto" must not reach the runtime in windowed mode. With no explicit
        # width the runtime calls SDL_MaximizeWindow(), and a maximised window
        # on a single-monitor desktop is indistinguishable from fullscreen - so
        # picking Windowed + Auto looked like the mode was ignored. Resolve Auto
        # to a real window that leaves the desktop visible.
        width, height = _auto_windowed_size(settings.aspect)

    if width and height:
        lines.append(f"window_width      = {width}")
        lines.append(f"window_height     = {height}")
    lines.append("")
    return "\n".join(lines)


def _auto_windowed_size(aspect: str = "16:9") -> tuple[int, int]:
    """A sensible windowed size: the largest box of the right SHAPE that fits
    in ~80% of the desktop.

    Deriving width and height independently from the desktop produces the wrong
    shape (a 640x640 square on one test box), so fit the configured aspect
    instead and let the short axis follow. Falls back to a 1280-wide window when
    Qt cannot be queried - a frozen build, or no display attached.
    """
    try:
        num, den = (int(part) for part in aspect.split(":"))
        if num <= 0 or den <= 0:
            raise ValueError
    except Exception:
        num, den = 16, 9

    # Default to a 1600x900-class window. Only trust a queried screen when it
    # is plausibly a real desktop: the offscreen Qt platform reports a tiny
    # surface, which used to collapse this to the 640x480 minimum.
    avail_w, avail_h = 1600, 900
    try:
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            if area.width() >= 1024 and area.height() >= 768:
                avail_w = int(area.width() * 0.8)
                avail_h = int(area.height() * 0.8)
    except Exception:
        pass

    # Fit the aspect box inside the available area.
    width = avail_w
    height = width * den // num
    if height > avail_h:
        height = avail_h
        width = height * num // den

    width = max(MIN_WINDOW_WIDTH, min(MAX_WINDOW_WIDTH, width))
    height = max(MIN_WINDOW_HEIGHT, min(MAX_WINDOW_HEIGHT, height))
    return width, height


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
