"""Runtime hotkeys, kept in config.ini [KeyMap] beside each runtime.

The runtime reads them in host_keymap.c: `[Ctrl+][Alt+][Shift+]<SDL key name>`,
comma-separated alternatives, action names matched without regard to case. An
action whose line is missing or empty gets the runtime's own default, so an
action cannot be left with no key at all - the editor therefore always keeps a
primary key. Key names are SDL key names, which for every key offered here are
the same strings keybinds.KEY_NAMES uses for the game's own buttons.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from . import keybinds

# (config.ini name, label, default). Defaults mirror host_keymap.c
# apply_defaults(); the order is the order the editor shows them in.
ACTIONS = (
    ("PauseMenu", "Pause menu", "Home"),
    ("QuickSave", "Quick save", "F5"),
    ("QuickLoad", "Quick load", "F9"),
    ("SaveStateMenu", "Save state slots", "F7"),
    ("Rewind", "Rewind", "F8"),
    ("Turbo", "Fast-forward", "Tab"),
    ("DisplayPerf", "FPS counter", "F"),
    ("Fullscreen", "Toggle fullscreen", "Alt+Return, Ctrl+F"),
    ("VolumeUp", "Volume up", "Keypad +"),
    ("VolumeDown", "Volume down", "Keypad -"),
)
DEFAULTS = {name: default for name, _, default in ACTIONS}
LABELS = {name: label for name, label, _ in ACTIONS}
_ACTION_BY_LOWER = {name.lower(): name for name in DEFAULTS}

# Checked in this order by host_keymap.c; written in this order too.
MODIFIERS = ("Ctrl", "Alt", "Shift")
# Keyboard keys only: host_keymap binds keys, so the mouse buttons keybinds
# offers for the game's buttons are left out.
KEY_NAMES = tuple(k for k in keybinds.KEY_NAMES if not k.startswith("Mouse"))
_KEY_BY_LOWER = {k.lower(): k for k in KEY_NAMES}
_KEY_BY_LOWER.update({"enter": "Return", "esc": "Escape"})


def parse_bind(text: str) -> tuple[frozenset[str], str] | None:
    """"Ctrl+F" -> ({"Ctrl"}, "F"). None when the runtime could not read it."""
    rest = text.strip()
    mods: set[str] = set()
    progress = True
    while progress:
        progress = False
        for mod in MODIFIERS:
            if rest.lower().startswith(mod.lower() + "+"):
                mods.add(mod)
                rest = rest[len(mod) + 1:].strip()
                progress = True
    key = _KEY_BY_LOWER.get(rest.lower())
    if key is None or key == "None":
        return None
    return frozenset(mods), key


def format_bind(mods, key: str) -> str:
    return "+".join([m for m in MODIFIERS if m in mods] + [key])


def split(value: str) -> tuple[str, str]:
    """A stored value as (primary, alternate); "None" for an empty slot."""
    parts = [p.strip() for p in value.split(",") if p.strip()]
    return (parts[0] if parts else "None",
            parts[1] if len(parts) > 1 else "None")


def join(primary: str, alternate: str) -> str:
    return primary if alternate == "None" else f"{primary}, {alternate}"


def normalize(hotkeys: object) -> dict[str, str]:
    """Keep only actions the runtime knows, with one or two readable binds."""
    if not isinstance(hotkeys, dict):
        return {}
    out: dict[str, str] = {}
    for action, value in hotkeys.items():
        name = _ACTION_BY_LOWER.get(str(action).lower())
        if name is None or not isinstance(value, str):
            continue
        binds: list[str] = []
        ok = True
        for part in value.split(","):
            if part.strip().lower() in ("", "none"):
                continue
            parsed = parse_bind(part)
            if parsed is None:
                ok = False
                break
            text = format_bind(*parsed)
            if text not in binds:
                binds.append(text)
        if ok and 1 <= len(binds) <= 2:
            out[name] = ", ".join(binds)
    return out


def current(hotkeys: dict[str, str], action: str) -> str:
    """The effective value of one action: the player's, else the default."""
    return hotkeys.get(action, DEFAULTS[action])


def display(hotkeys: dict[str, str], action: str) -> str:
    """How a key list shows an action's keys, e.g. "Alt+Enter / Ctrl+F"."""
    names = []
    for part in current(hotkeys, action).split(","):
        parsed = parse_bind(part)
        if parsed:
            key = "Enter" if parsed[1] == "Return" else parsed[1]
            names.append(format_bind(parsed[0], key))
    return " / ".join(names)


def bare_keys(hotkeys: dict[str, str]) -> dict[str, str]:
    """Keys a hotkey fires on with no modifier held -> the action's label.

    A game button on one of these keys also triggers the hotkey."""
    out: dict[str, str] = {}
    for action in DEFAULTS:
        for part in current(hotkeys, action).split(","):
            parsed = parse_bind(part)
            if parsed and not parsed[0]:
                out.setdefault(parsed[1], LABELS[action])
    return out


def conflicts(hotkeys: dict[str, str], game_bindings: dict[str, str]) -> list[str]:
    """Plain-language warnings: a key used twice, or shared with a game button."""
    seen: dict[str, str] = {}
    warnings: list[str] = []
    for action in DEFAULTS:
        for part in current(hotkeys, action).split(","):
            parsed = parse_bind(part)
            if not parsed:
                continue
            text = format_bind(*parsed)
            if text in seen and seen[text] != LABELS[action]:
                warnings.append(f"{text} is set for both {seen[text]} and "
                                f"{LABELS[action]}.")
            seen.setdefault(text, LABELS[action])
    game_keys = {key for action in keybinds.DEFAULTS
                 for key in keybinds.split(game_bindings.get(
                     action, keybinds.DEFAULTS[action]))}
    for key, label in bare_keys(hotkeys).items():
        if key in game_keys:
            warnings.append(f"{key} is both a game button and the {label} "
                            "hotkey.")
    return warnings


def read(path: Path) -> dict[str, str]:
    """[KeyMap] from an existing config.ini, for adopting hand-made choices."""
    if not path.is_file():
        return {}
    section = ""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith((";", "#")):
            continue
        if stripped.startswith("["):
            section = stripped.strip("[]").strip().lower()
        elif section == "keymap" and "=" in stripped:
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip()
    return normalize(values)


def save(path: Path, hotkeys: dict[str, str]) -> None:
    """Rewrite only [KeyMap]'s own keys; every other line survives."""
    values = {**DEFAULTS, **normalize(hotkeys)}
    old = path.read_text(encoding="utf-8-sig", errors="replace") if path.is_file() else ""
    lines = old.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines)
                  if line.strip().lower() == "[keymap]"), None)
    if start is None:
        prefix = (old.rstrip() + "\n\n" if old.strip()
                  else "; Runtime hotkeys managed by the Crash 2 launcher.\n\n")
        content = prefix + "[KeyMap]\n" + "".join(
            f"{name} = {value}\n" for name, value in values.items())
    else:
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].lstrip().startswith("[")), len(lines))
        written: set[str] = set()
        body: list[str] = []
        for line in lines[start + 1:end]:
            match = re.match(r"\s*([A-Za-z0-9_]+)\s*=", line)
            name = _ACTION_BY_LOWER.get(match.group(1).lower()) if match else None
            if name is not None:
                if name not in written:
                    body.append(f"{name} = {values[name]}\n")
                    written.add(name)
            else:
                body.append(line if line.endswith("\n") else line + "\n")
        body += [f"{name} = {value}\n" for name, value in values.items()
                 if name not in written]
        content = "".join(lines[:start + 1] + body + lines[end:])
    if content == old:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
