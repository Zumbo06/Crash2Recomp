"""Player-one SDL scancode names, shared by the editor and keybinds.ini writer."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

# Names and defaults mirror runtime/src/psx_keybinds.c, not Qt key sequences.
GROUPS = (
    ("Movement & actions", (
        ("up", "Move up", "Up"), ("down", "Move down", "Down"),
        ("left", "Move left", "Left"), ("right", "Move right", "Right"),
        ("cross", "Jump / confirm (Cross)", "X"),
        ("square", "Spin (Square)", "Z"),
        ("circle", "Crouch / slide (Circle)", "S"),
        ("triangle", "Triangle", "A"),
        ("start", "Start", "Return"), ("select", "Select", "Right Shift"),
    )),
    ("Shoulders & stick clicks", (
        ("l1", "L1", "Q"), ("r1", "R1", "W"),
        ("l2", "L2", "E"), ("r2", "R2", "R"),
        ("l3", "Left stick click", "T"), ("r3", "Right stick click", "Y"),
    )),
    ("Analog stick directions", (
        ("ls_up", "Left stick up", "Up"), ("ls_down", "Left stick down", "Down"),
        ("ls_left", "Left stick left", "Left"), ("ls_right", "Left stick right", "Right"),
        ("rs_up", "Right stick up", "None"), ("rs_down", "Right stick down", "None"),
        ("rs_left", "Right stick left", "None"), ("rs_right", "Right stick right", "None"),
    )),
)
DEFAULTS = {action: key for _, rows in GROUPS for action, _, key in rows}
KEY_NAMES = (
    "None", *(chr(n) for n in range(ord("A"), ord("Z") + 1)),
    *(str(n) for n in range(10)), "Up", "Down", "Left", "Right",
    "Return", "Space", "Tab", "Escape", "Backspace", "Delete", "Insert",
    "Home", "End", "PageUp", "PageDown", "Left Shift", "Right Shift",
    "Left Ctrl", "Right Ctrl", "Left Alt", "Right Alt", "CapsLock",
    *(f"F{n}" for n in range(1, 25)),
    "-", "=", "[", "]", "\\", ";", "'", "`", ".", "/",
    *(f"Keypad {n}" for n in range(10)), "Keypad Enter", "Keypad +", "Keypad -",
    "Keypad *", "Keypad /", "Keypad .", "Mouse1", "Mouse2", "Mouse3", "Mouse4", "Mouse5",
)
_CANONICAL = {name.lower(): name for name in KEY_NAMES}
_CANONICAL.update({"enter": "Return", "esc": "Escape", "lshift": "Left Shift",
    "rshift": "Right Shift", "lctrl": "Left Ctrl", "rctrl": "Right Ctrl",
    "lalt": "Left Alt", "ralt": "Right Alt", "backslash": "\\"})


def split(value: str) -> tuple[str, str]:
    parts = value.split(",", 1)
    return parts[0].strip(), parts[1].strip() if len(parts) == 2 else "None"


def join(primary: str, alternate: str) -> str:
    return primary if alternate == "None" else f"{primary}, {alternate}"


def normalize(bindings: object) -> dict[str, str]:
    if not isinstance(bindings, dict):
        return {}
    out = {}
    for action, value in bindings.items():
        if action not in DEFAULTS or not isinstance(value, str):
            continue
        keys = [_CANONICAL.get(part.strip().lower()) for part in value.split(",")]
        if 1 <= len(keys) <= 2 and all(key is not None for key in keys):
            out[action] = join(keys[0], keys[1] if len(keys) == 2 else "None")
    return out


def read(path: Path) -> dict[str, str]:
    """Import existing player-one choices without changing any other player."""
    if not path.is_file():
        return {}
    section = ""
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            section = stripped.strip("[]").lower()
        elif section == "player1" and "=" in stripped and not stripped.startswith(("#", ";")):
            key, value = stripped.split("=", 1)
            values[key.strip().lower()] = value.strip()
    return normalize(values)


def save(path: Path, bindings: dict[str, str]) -> None:
    """Update only managed keys in [player1], retaining other sections/comments."""
    values = {**DEFAULTS, **normalize(bindings)}
    old = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    lines = old.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines)
                  if line.strip().lower() == "[player1]"), None)
    if start is None:
        prefix = old.rstrip() + "\n\n" if old.strip() else "# Keyboard controls managed by the Crash 2 launcher.\n\n"
        content = prefix + "[player1]\n" + "".join(f"{key} = {value}\n" for key, value in values.items())
    else:
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].lstrip().startswith("[")), len(lines))
        written = set()
        body = []
        for line in lines[start + 1:end]:
            match = re.match(r"\s*([a-zA-Z0-9_]+)\s*=", line)
            key = match.group(1).lower() if match else ""
            if key in values:
                if key not in written:
                    body.append(f"{key} = {values[key]}\n")
                    written.add(key)
            else:
                body.append(line if line.endswith("\n") else line + "\n")
        body += [f"{key} = {value}\n" for key, value in values.items() if key not in written]
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
