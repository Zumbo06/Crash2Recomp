"""Player-one controller mapping, shared by the editor and the input.ini writer.

The runtime reads ``input.ini`` beside its executable (``load_input_config`` in
runtime/src/main.cpp). In ``[mapping]`` each PS1 button lists one or more SDL
gamepad sources and is held while any of them is. The same map drives the
launcher's default "keyboard and all controllers" mode
(``dev_all_controllers_buttons``), so editing it applies whichever way player 1
is set up. The stick deadzone is NOT taken from here: the runtime applies
settings.toml's ``[controller] deadzone`` over input.ini's right after reading
it, so usersettings.py writes that one.

Source names are SDL's positional, Xbox-layout names: ``a`` is the BOTTOM face
button on every pad - Cross on a PlayStation controller - ``b`` the right one,
and so on. Only sources that always act as buttons are offered. Stick
directions are left out on purpose: the runtime ignores them as button sources
whenever the pad presents as analog (``source_is_stick_axis``), so a binding to
one would silently do nothing in this game's analog mode.

Only the sixteen button keys are managed. Stick-direction keys (``ls_up`` ...
``rs_right``), per-controller ``[mapping.<guid>]`` sections, ``[controller]``
and comments are left exactly as they are.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

# (action, label, default) - defaults are set_default_controller_mapping_into().
GROUPS = (
    ("Face buttons", (
        ("cross", "Jump / confirm (Cross)", "a"),
        ("square", "Spin (Square)", "x"),
        ("circle", "Crouch / slide (Circle)", "b"),
        ("triangle", "Triangle", "y"),
    )),
    ("D-pad", (
        ("up", "Up", "dpup"), ("down", "Down", "dpdown"),
        ("left", "Left", "dpleft"), ("right", "Right", "dpright"),
    )),
    ("Shoulders & triggers", (
        ("l1", "L1", "leftshoulder"), ("r1", "R1", "rightshoulder"),
        ("l2", "L2", "lefttrigger"), ("r2", "R2", "righttrigger"),
    )),
    ("Start, Select & stick clicks", (
        ("start", "Start", "start"), ("select", "Select", "back"),
        ("l3", "L3 (left stick click)", "leftstick"),
        ("r3", "R3 (right stick click)", "rightstick"),
    )),
)
DEFAULTS = {action: source for _, rows in GROUPS for action, _, source in rows}

# Canonical source name -> what a player sees. Xbox name first, PlayStation
# position second, because SDL names are positions, not glyphs.
SOURCES = (
    ("none", "Unbound"),
    ("a", "A · bottom (Cross)"),
    ("b", "B · right (Circle)"),
    ("x", "X · left (Square)"),
    ("y", "Y · top (Triangle)"),
    ("dpup", "D-pad up"),
    ("dpdown", "D-pad down"),
    ("dpleft", "D-pad left"),
    ("dpright", "D-pad right"),
    ("leftshoulder", "LB · left bumper (L1)"),
    ("rightshoulder", "RB · right bumper (R1)"),
    ("lefttrigger", "LT · left trigger (L2)"),
    ("righttrigger", "RT · right trigger (R2)"),
    ("leftstick", "Left stick click (L3)"),
    ("rightstick", "Right stick click (R3)"),
    ("start", "Start / Menu (Options)"),
    ("back", "Back / View (Share)"),
)
SOURCE_NAMES = tuple(name for name, _ in SOURCES)
LABELS = dict(SOURCES)

# Spellings parse_controller_source() also accepts, folded to the names above.
_ALIASES = {
    "none": "none", "unbound": "none", "": "none",
    "a": "a", "b": "b", "x": "x", "y": "y",
    "dpup": "dpup", "dpadup": "dpup", "dpdown": "dpdown", "dpaddown": "dpdown",
    "dpleft": "dpleft", "dpadleft": "dpleft", "dpright": "dpright",
    "dpadright": "dpright",
    "leftshoulder": "leftshoulder", "lb": "leftshoulder", "l1": "leftshoulder",
    "rightshoulder": "rightshoulder", "rb": "rightshoulder", "r1": "rightshoulder",
    "lefttrigger": "lefttrigger", "lt": "lefttrigger", "l2": "lefttrigger",
    "righttrigger": "righttrigger", "rt": "righttrigger", "r2": "righttrigger",
    "leftstick": "leftstick", "rightstick": "rightstick",
    "start": "start", "menu": "start",
    "back": "back", "view": "back", "select": "back",
}
_BUTTON_SHAPED = ("leftshoulder", "rightshoulder", "lefttrigger", "righttrigger")

DEADZONE_DEFAULT = 10          # percent; the runtime's own default is 3277 raw
DEADZONE_MAX = 50
_AXIS_MAX = 32767


def _canonical(source: str) -> str | None:
    s = source.strip().lower()
    # Axis-style capture may append +/- to a trigger or shoulder; the runtime
    # accepts both, so do we.
    if s[-1:] in "+-" and s[:-1] in _BUTTON_SHAPED:
        s = s[:-1]
    return _ALIASES.get(s)


def split(value: str) -> tuple[str, str]:
    parts = value.split(",", 1)
    first = parts[0].strip() or "none"
    return first, parts[1].strip() if len(parts) == 2 else "none"


def join(primary: str, alternate: str) -> str:
    if primary == "none":
        primary, alternate = alternate, "none"
    return primary if alternate == "none" else f"{primary}, {alternate}"


def normalize(bindings: object) -> dict[str, str]:
    """Keep only managed buttons whose sources this editor can show."""
    if not isinstance(bindings, dict):
        return {}
    out = {}
    for action, value in bindings.items():
        if action not in DEFAULTS or not isinstance(value, str):
            continue
        parts = [part for part in value.split(",")]
        sources = [_canonical(part) for part in parts]
        if not 1 <= len(sources) <= 2 or any(s is None for s in sources):
            continue
        out[action] = join(sources[0], sources[1] if len(sources) == 2 else "none")
    return out


def deadzone_raw(percent: int) -> int:
    return round(max(0, min(DEADZONE_MAX, int(percent))) * _AXIS_MAX / 100)


def deadzone_percent(raw: int) -> int:
    return max(0, min(DEADZONE_MAX, round(int(raw) * 100 / _AXIS_MAX)))


def _sections(text: str) -> dict[str, dict[str, str]]:
    section = ""
    out: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        stripped = re.split(r"[;#]", line, 1)[0].strip()
        if not stripped:
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().lower()
            out.setdefault(section, {})
        elif "=" in stripped and section:
            key, value = stripped.split("=", 1)
            out[section][key.strip().lower()] = value.strip()
    return out


def read(path: Path) -> dict[str, str]:
    """Import the [mapping] buttons an existing input.ini already has."""
    if not path.is_file():
        return {}
    return normalize(_sections(path.read_text(encoding="utf-8-sig")).get("mapping", {}))


def device_overrides(path: Path) -> list[str]:
    """GUIDs with a [mapping.<guid>] section - those override [mapping]."""
    if not path.is_file():
        return []
    return sorted(name[len("mapping."):]
                  for name in _sections(path.read_text(encoding="utf-8-sig"))
                  if name.startswith("mapping.") and len(name) > len("mapping."))


_HEADER = (
    "; PSXRecomp input mapping. PSX buttons are active when any listed source is pressed.\n"
    "; The Crash 2 launcher manages the button keys in [mapping]; everything else\n"
    "; here is left as you write it. The stick deadzone comes from settings.toml,\n"
    "; which the runtime applies over this file's.\n"
    "; Sources use SDL/Xbox names: a,b,x,y,back,start,leftshoulder,rightshoulder,\n"
    "; lefttrigger,righttrigger,leftstick,rightstick,dpup,dpdown,dpleft,dpright,\n"
    "; leftx-/leftx+/lefty-/lefty+. Optional per-device overrides: [mapping.<sdl-guid>].\n"
)
_STICK_DEFAULTS = (
    ("ls_up", "lefty-"), ("ls_down", "lefty+"), ("ls_left", "leftx-"),
    ("ls_right", "leftx+"), ("rs_up", "righty-"), ("rs_down", "righty+"),
    ("rs_left", "rightx-"), ("rs_right", "rightx+"),
)


def _fresh(values: dict[str, str], raw_deadzone: int) -> str:
    return (_HEADER + "\n[controller]\nenabled = true\ndevice = 0\n"
            f"deadzone = {raw_deadzone}\n\n[mapping]\n"
            + "".join(f"{key} = {value}\n" for key, value in values.items())
            + "".join(f"{key} = {value}\n" for key, value in _STICK_DEFAULTS))


def _update_section(lines: list[str], name: str, values: dict[str, str]) -> list[str]:
    """Rewrite `values` inside [name], appending missing keys; add the section if absent."""
    if not values:
        return lines
    start = next((i for i, line in enumerate(lines)
                  if line.strip().lower() == f"[{name}]"), None)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("\n")
        return lines + [f"[{name}]\n"] + [f"{k} = {v}\n" for k, v in values.items()]
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
    missing = [f"{k} = {v}\n" for k, v in values.items() if k not in written]
    # Keep appended keys above the blank lines that separate sections.
    tail = []
    while body and not body[-1].strip():
        tail.insert(0, body.pop())
    return lines[:start + 1] + body + missing + tail + lines[end:]


def save(path: Path, bindings: dict[str, str]) -> None:
    """Write the managed buttons, preserving everything else.

    Only buttons present in ``bindings`` are rewritten, so a key the player
    edited by hand keeps its value until they change that button here. A
    missing file is created with the runtime's full default layout.
    """
    chosen = normalize(bindings)
    if not path.is_file():
        content = _fresh({**DEFAULTS, **chosen}, deadzone_raw(DEADZONE_DEFAULT))
        old = None
    else:
        old = path.read_text(encoding="utf-8-sig")
        lines = old.splitlines(keepends=True)
        lines = _update_section(lines, "mapping", chosen)
        content = "".join(lines)
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
