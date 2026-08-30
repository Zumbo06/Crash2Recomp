"""Read and surgically edit the recompiled project's ``game.toml``.

Python ships a TOML *reader* (``tomllib``) but no writer, and pulling in a full
round-trip library to change three scalars is not worth it. More importantly, a
naive rewrite would discard the comments and key ordering that make this file
readable when debugging a boot problem by hand.

So: read with ``tomllib``, write by rewriting only the specific lines that
changed. Keys that do not exist yet are appended to their section.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def read(path: Path) -> dict[str, Any]:
    """Parse game.toml. Returns an empty dict if it is missing or malformed."""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def get(data: dict[str, Any], section: str, key: str, default: Any = None) -> Any:
    return data.get(section, {}).get(key, default) if isinstance(data.get(section), dict) else default


def update(path: Path, changes: dict[str, dict[str, Any]]) -> None:
    """Apply ``{section: {key: value}}`` to game.toml, preserving everything else.

    Existing keys are edited in place. Missing keys are appended to the end of
    their section. Missing sections are appended to the end of the file.
    """
    if not changes:
        return

    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []

    # Track which (section, key) pairs we still need to write.
    pending = {(s, k): v for s, kv in changes.items() for k, v in kv.items()}

    # Where each section's body ends, so appends land inside the right section.
    section_end: dict[str, int] = {}
    current = ""
    for i, line in enumerate(lines):
        # An array-of-tables (for example [[recompiler.patch]]) ends the scalar
        # table for our purposes. Treating its keys as part of the preceding
        # section would make a later settings save insert keys into the patch.
        if line.lstrip().startswith("[["):
            current = ""
            continue
        m = _SECTION_RE.match(line)
        if m:
            current = m.group(1).strip()
            section_end[current] = i
            continue
        if current and line.strip():
            section_end[current] = i

    current = ""
    for i, line in enumerate(lines):
        if line.lstrip().startswith("[["):
            current = ""
            continue
        m = _SECTION_RE.match(line)
        if m:
            current = m.group(1).strip()
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if (current, key) in pending:
            value = pending.pop((current, key))
            indent = line[: len(line) - len(line.lstrip())]
            lines[i] = f"{indent}{key} = {_fmt(value)}"

    # Anything left over is a new key. Insert from the bottom up so the
    # line indices we recorded stay valid as we splice.
    leftovers: dict[str, list[tuple[str, Any]]] = {}
    for (section, key), value in pending.items():
        leftovers.setdefault(section, []).append((key, value))

    for section in sorted(leftovers, key=lambda s: section_end.get(s, -1), reverse=True):
        entries = [f"{k} = {_fmt(v)}" for k, v in leftovers[section]]
        if section in section_end:
            at = section_end[section] + 1
            lines[at:at] = entries
        else:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"[{section}]")
            lines.extend(entries)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
