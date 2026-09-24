"""Recompile-time settings this port needs, re-applied after every generate.

``psxrecomp.exe build`` writes a fresh, minimal ``game.toml`` and recompiles
from it straight away. Settings the runtime reads when it starts can be put
back afterwards (``runtime.py`` does that on every launch), but anything the
*generated C* depends on has to be in ``game.toml`` when the recompiler runs.
This module is the one place those keys live: the Setup page applies it after
the CLI's generate step and runs the recompiler again before compiling, and the
workspace ``game.toml`` carries exactly what :func:`apply` writes.

Currently one feature - native 60 FPS script pacing (``crash2_60fps.h``,
``C2_60_GOOL_UPDATE``): an entry hook on GoolObjectUpdate (0x8001C718) and nine
opcode-verified instruction words that make its once-per-call GOOL work depend
on a flag the hook stores. Without them the 60 FPS mode runs every animation,
moving platform and scripted timer at double speed.

The recompiler verifies each ``expected`` word against the executable and
refuses to build on a mismatch, so a different revision fails loudly here
rather than miscompiling.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ENTRY_FUNCS = ("0x8001C718",)

# (id, address, expected, replacement, note)
PATCHES: tuple[tuple[str, int, int, int, str], ...] = (
    ("c2-60-gool-pad-flag", 0x8001C73C, 0x3C028006, 0x8FA2003C,
     "lui v0,0x8006 -> lw v0,60(sp): the pointer compared against is the frame "
     "flag (crash on a script step, 0 on a physics-only field), so the pad is "
     "read at script rate"),
    ("c2-60-gool-pad-flag-2", 0x8001C740, 0x8C42F38C, 0x00000000,
     "lw v0,-3188(v0) -> nop: the crash pointer now comes from the frame flag"),
    ("c2-60-gool-step-flag", 0x8001C898, 0x00000000, 0x8FA8003C,
     "delay-slot nop -> lw t0,60(sp): t0 = frame flag for the animate path"),
    ("c2-60-gool-trans-ptr", 0x8001C8A8, 0x00000000, 0x8E0A00C8,
     "load-delay nop -> lw t2,200(s0): trans-block pointer, read early to free "
     "a slot at 0x8001C930"),
    ("c2-60-gool-stall-step", 0x8001C8BC, 0x00000000, 0x0008482B,
     "load-delay nop -> sltu t1,zero,t0: 1 on a script step, 0 on a "
     "physics-only field"),
    ("c2-60-gool-stall-count", 0x8001C8C4, 0x2442FFFF, 0x00491023,
     "addiu v0,v0,-1 -> subu v0,v0,t1: the stall countdown only runs on script "
     "steps"),
    ("c2-60-gool-skip", 0x8001C930, 0x8E0200C8, 0x11000028,
     "lw v0,200(s0) -> beq t0,zero,0x8001C9D4: a physics-only field skips the "
     "trans and code blocks"),
    ("c2-60-gool-skip-arg", 0x8001C934, 0x00000000, 0x02002021,
     "load-delay nop -> addu a0,s0,zero: the object argument for 0x8001BFDC on "
     "the skip path"),
    ("c2-60-gool-trans-test", 0x8001C938, 0x1040000E, 0x1140000E,
     "beq v0,zero -> beq t2,zero: same test, on the pointer loaded at "
     "0x8001C8A8"),
)

BEGIN = "# >>> crash2 recompile profile (launcher/crash2launcher/recompprofile.py)"
END = "# <<< crash2 recompile profile"
_ENTRY_KEY = "mod_function_entry_funcs"
_SECTION_RE = re.compile(r"^\s*\[([^\[\]]+)\]\s*(#.*)?$")

_HEADER = (
    "# Native 60 FPS - script-rate GOOL (runtime/src/crash2_60fps.h).",
    "# Written by the launcher after every generate; edit recompprofile.py, not",
    "# this block.",
    "#",
    "# With the 30 Hz gate open the game loop runs once per field, and everything",
    "# GoolObjectUpdate (0x8001C718) does once per call ran twice as often: the",
    "# trans block, code blocks that resume every call, the stall countdown and",
    "# the pad read - so animations, platforms and scripted timers ran at double",
    "# speed. Physics is scaled by measured frame time and correctly runs every",
    "# field. These words make the per-call script work depend on a flag the",
    "# runtime's entry hook stores at sp+60 (the frame's unused padding word):",
    "#   0            physics-only field - no pad read, no stall tick, no scripts",
    "#   otherwise    the crash object pointer (1 if there is none), which is",
    "#                exactly the value the original code compared against",
    "# With the mode off the flag is never 0 and the function is the original.",
    "# Recompile-time only: guest RAM keeps the disc's words, so the text-image",
    "# guard still validates this function and it stays native.",
)


def _block_lines() -> list[str]:
    lines = [BEGIN, *_HEADER]
    for pid, addr, exp, rep, note in PATCHES:
        lines += [
            "",
            "[[recompiler.patch]]",
            f'id = "{pid}"',
            f'address = "0x{addr:08X}"',
            f'expected = "0x{exp:08X}"',
            f'replacement = "0x{rep:08X}"',
            f'note = "{note}"',
        ]
    lines.append(END)
    return lines


def _entry_line(existing: list[str]) -> str:
    merged = list(existing)
    for f in ENTRY_FUNCS:
        if not any(e.lower() == f.lower() for e in merged):
            merged.append(f)
    return f"{_ENTRY_KEY} = [" + ", ".join(f'"{f}"' for f in merged) + "]"


def missing(game_toml: Path) -> list[str]:
    """What the profile needs that ``game_toml`` lacks, as readable strings.

    Empty means a build from this file carries everything. A file that does not
    parse reports that, rather than claiming anything is present.
    """
    try:
        with game_toml.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [f"game.toml unreadable: {exc}"]
    rec = data.get("recompiler", {})
    out = []
    entries = {str(e).lower() for e in rec.get(_ENTRY_KEY, [])}
    for f in ENTRY_FUNCS:
        if f.lower() not in entries:
            out.append(f"{_ENTRY_KEY} {f}")
    have = {}
    for p in rec.get("patch", []):
        if isinstance(p, dict) and "id" in p:
            have[p["id"]] = p
    for pid, addr, exp, rep, _note in PATCHES:
        p = have.get(pid)
        try:
            ok = (p is not None and int(p["address"], 16) == addr
                  and int(p["expected"], 16) == exp
                  and int(p["replacement"], 16) == rep)
        except (KeyError, TypeError, ValueError):
            ok = False
        if not ok:
            out.append(f"patch {pid}")
    return out


def apply(game_toml: Path) -> bool:
    """Make ``game_toml`` carry the profile. Returns True if the file changed.

    Idempotent. Our block is delimited by :data:`BEGIN`/:data:`END` and is
    replaced wholesale, so a newer launcher updates an older block in place.
    The entry-hook key is merged into ``[recompiler]`` without disturbing other
    entries. Line endings follow the file's own.
    """
    raw = game_toml.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    # 1. drop any previous profile block
    if BEGIN in lines:
        start = lines.index(BEGIN)
        try:
            stop = lines.index(END, start)
        except ValueError as exc:
            raise ValueError(f"{game_toml}: profile block has no end marker") from exc
        del lines[start:stop + 1]
        while lines and lines[-1].strip() == "":
            lines.pop()

    # 2. the entry-hook key, inside [recompiler]
    section = ""
    rec_last = None
    entry_at = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[["):
            section = ""
            continue
        m = _SECTION_RE.match(line)
        if m:
            section = m.group(1).strip()
            if section == "recompiler":
                rec_last = i
            continue
        if section == "recompiler" and stripped and not stripped.startswith("#"):
            rec_last = i
            if stripped.split("=", 1)[0].strip() == _ENTRY_KEY:
                entry_at = i
    if rec_last is None:
        raise ValueError(f"{game_toml}: no [recompiler] table")
    if entry_at is not None:
        try:
            current = tomllib.loads(lines[entry_at])[_ENTRY_KEY]
        except (tomllib.TOMLDecodeError, KeyError) as exc:
            raise ValueError(f"{game_toml}: {_ENTRY_KEY} is not a one-line list") from exc
        lines[entry_at] = _entry_line([str(v) for v in current])
    else:
        lines.insert(rec_last + 1, _entry_line([]))

    # 3. our block, last - array-of-tables may follow every other table
    lines += [""] + _block_lines()

    new = "\n".join(lines) + "\n"
    if crlf:
        new = new.replace("\n", "\r\n")
    new_raw = new.encode("utf-8")
    if new_raw == raw:
        return False
    tomllib.loads(new)  # never write a file the recompiler cannot read
    game_toml.write_bytes(new_raw)
    return True
