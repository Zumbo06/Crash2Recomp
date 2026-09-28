"""Changes made while playing, kept for the next launch.

The runtime writes what the player changed in the Home menu - and with the F
readout key and the volume keys - to PSX_MENU_PREFS_FILE, as JSON keyed by the
names of the settings below (tuning/patches/0045, main.cpp menu_prefs_write).
Only values the player touched are in it. This folds the file back into the
launcher's settings when the game exits.

The launcher stays the owner of every value: each one is checked against the
same rules the settings page enforces, and anything else is dropped rather
than trusted. The one value that is dropped by design is a 21:9 GAME aspect:
the Home menu offers it for the session, but the launcher only offers 21:9 as
a screen shape (config.OUTPUT_ASPECTS explains the pop-in it would cause).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from . import config
from .config import Settings

FILE_NAME = "ingame_changes.json"

_FIT_NAMES = {"letterbox": "Letterbox", "stretch": "Stretch", "fill": "Fill",
              "fit_width": "Fit width"}
_WINDOW_NAMES = {0: "Windowed", 1: "Borderless fullscreen",
                 2: "Exclusive fullscreen"}
_AKU_NAMES = {"off": "Off", "keep_masks": "Keep 2 masks",
              "no_damage": "No damage"}


def changes_path(layout) -> Path:
    """Where the runtime is told to write (runtime.build_plan)."""
    return layout.userdata / FILE_NAME


def _choice(options) -> Callable[[Any], Any]:
    return lambda v: v if isinstance(v, str) and v in options else None


def _int_range(low: int, high: int) -> Callable[[Any], Any]:
    # bool is an int in Python; true/false is never a number here.
    return lambda v: (v if isinstance(v, int) and not isinstance(v, bool)
                      and low <= v <= high else None)


def _flag(v: Any) -> Any:
    return v if isinstance(v, bool) else None


def _on_off(v: bool) -> str:
    return "on" if v else "off"


# setting -> (validator, description or None). No description: the value is
# part of another change (the zoom and stretch a fit clears).
_RULES: dict[str, tuple[Callable[[Any], Any], Callable[[Any], str] | None]] = {
    "aspect": (_choice(config.ASPECTS), lambda v: f"game aspect {v}"),
    "scaling_mode": (_choice(config.SCALING_MODES),
                     lambda v: f"image fit {_FIT_NAMES.get(v, v)}"),
    "present_zoom": (_int_range(-1, 100), None),
    "present_stretch": (_int_range(0, 100), None),
    "fullscreen_mode": (lambda v: v if v in (0, 1, 2) and not isinstance(v, bool)
                        else None,
                        lambda v: f"window mode {_WINDOW_NAMES[v].lower()}"),
    "fps_overlay": (_flag, lambda v: f"FPS counter {_on_off(v)}"),
    "postfx_enabled": (_flag, lambda v: f"post-processing {_on_off(v)}"),
    "cheat_infinite_lives": (_flag, lambda v: f"99 lives {_on_off(v)}"),
    "cheat_aku_aku": (_choice(config.CHEAT_AKU_LEVELS),
                      lambda v: f"Aku Aku {_AKU_NAMES.get(v, v).lower()}"),
    "volume": (_int_range(0, 100), lambda v: f"volume {v}%"),
}


def apply_pending(path: Path, settings: Settings) -> tuple[list[str], list[str]]:
    """Fold a pending changes file into `settings`, then delete it.

    Returns (kept, dropped): short descriptions of the settings that changed,
    and of values that were refused. A file that cannot be read is deleted
    too - left in place it would be retried, and refused, at every start.
    """
    if not path.is_file():
        return [], []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raw = None
    try:
        path.unlink()
    except OSError:
        pass
    if not isinstance(raw, dict):
        return [], ["the file the game left was unreadable"]

    kept: list[str] = []
    dropped: list[str] = []
    framing = False           # zoom/stretch cleared by picking a fit
    for name, value in raw.items():
        rule = _RULES.get(name)
        if rule is None:
            continue
        check, describe = rule
        good = check(value)
        if good is None:
            if name == "aspect" and value == "21:9":
                dropped.append("game aspect 21:9 (21:9 is a screen shape here)")
            else:
                dropped.append(f"{name} = {value!r}")
            continue
        if name == "volume" and settings.mute and good > 0:
            # Turning the volume up in the game is a clear sign the player
            # wants sound; keeping Mute would make the change inaudible.
            settings.mute = False
            kept.append("mute off")
        if getattr(settings, name) != good:
            setattr(settings, name, good)
            if describe is not None:
                kept.append(describe(good))
            else:
                framing = True
    # Picking the fit that was already set still hands the framing back to it,
    # which is a visible change worth naming.
    if framing and not any(k.startswith("image fit") for k in kept):
        kept.append("image fit %s" % _FIT_NAMES.get(settings.scaling_mode,
                                                     settings.scaling_mode))
    return kept, dropped
