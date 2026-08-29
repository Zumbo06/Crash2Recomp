"""Disc image inspection and verification.

The launcher must answer three questions about a dump before it is worth
spending a long recompile on it:

* Does the ``.cue`` parse, and do its tracks exist on disk?
* Is it the game we expect (serial from ``SYSTEM.CNF``)?
* Does it match the hash we recorded when the project was generated?

Hashing is chunked and reports progress, because the Crash 2 image is ~192 MB
and the caller is a GUI thread that must keep painting.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

# A PS1 CD sector is 2352 bytes raw; MODE2/2352 stores 2336 bytes of user data
# after a 16-byte sync+header.
RAW_SECTOR = 2352

_FILE_RE = re.compile(r'^\s*FILE\s+"(?P<name>[^"]+)"\s+(?P<fmt>\S+)', re.IGNORECASE | re.MULTILINE)
_TRACK_RE = re.compile(r"^\s*TRACK\s+(?P<num>\d+)\s+(?P<mode>\S+)", re.IGNORECASE | re.MULTILINE)
_BOOT_RE = re.compile(rb"BOOT\s*=\s*cdrom:\?([A-Z]{4}_\d{3}\.\d{2})", re.IGNORECASE)


@dataclass
class Track:
    number: int
    mode: str


@dataclass
class DiscInfo:
    cue_path: Path
    bin_paths: list[Path] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    serial: str = ""
    total_bytes: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def sector_count(self) -> int:
        return self.total_bytes // RAW_SECTOR

    @property
    def is_sector_aligned(self) -> bool:
        return self.total_bytes > 0 and self.total_bytes % RAW_SECTOR == 0


def parse_cue(cue_path: Path) -> DiscInfo:
    """Parse a .cue sheet and resolve its track files relative to the sheet."""
    info = DiscInfo(cue_path=cue_path)
    if not cue_path.is_file():
        info.problems.append(f"Cue sheet not found: {cue_path}")
        return info

    try:
        text = cue_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        info.problems.append(f"Could not read cue sheet: {exc}")
        return info

    for m in _FILE_RE.finditer(text):
        # Track files are named relative to the cue, never absolute.
        target = (cue_path.parent / m.group("name")).resolve()
        info.bin_paths.append(target)
        if not target.is_file():
            info.problems.append(f"Track file referenced by cue is missing: {target.name}")

    for m in _TRACK_RE.finditer(text):
        info.tracks.append(Track(number=int(m.group("num")), mode=m.group("mode")))

    if not info.bin_paths:
        info.problems.append("Cue sheet lists no FILE entries.")

    info.total_bytes = sum(p.stat().st_size for p in info.bin_paths if p.is_file())

    if info.total_bytes and not info.is_sector_aligned:
        info.problems.append(
            f"Image is not a whole number of {RAW_SECTOR}-byte sectors "
            f"({info.total_bytes:,} bytes) - the dump may be truncated."
        )

    info.serial = read_serial(info.bin_paths[0]) if info.bin_paths and info.bin_paths[0].is_file() else ""
    return info


def read_serial(bin_path: Path, scan_bytes: int = 48 * 1024 * 1024) -> str:
    """Pull the boot serial out of SYSTEM.CNF.

    SYSTEM.CNF lives early in the filesystem, so a partial scan finds it without
    reading the whole image. Returns e.g. ``SCUS-94154``.
    """
    try:
        with bin_path.open("rb") as fh:
            remaining = scan_bytes
            tail = b""
            while remaining > 0:
                chunk = fh.read(min(4 * 1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                m = _BOOT_RE.search(tail + chunk)
                if m:
                    return m.group(1).decode("ascii").replace("_", "-").replace(".", "")
                # Carry a small overlap so a match spanning the boundary survives.
                tail = chunk[-64:]
    except OSError:
        return ""
    return ""


def hash_files(
    paths: Iterable[Path],
    algo: str = "sha1",
    progress: Callable[[int, int], bool] | None = None,
    chunk_size: int = 8 * 1024 * 1024,
) -> str:
    """Hash one or more files as a single stream.

    ``progress(done, total)`` is called as bytes are consumed; returning False
    from it aborts the hash and raises :class:`Cancelled`.
    """
    paths = [p for p in paths]
    total = sum(p.stat().st_size for p in paths if p.is_file())
    digest = hashlib.new(algo)
    done = 0

    for path in paths:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
                done += len(chunk)
                if progress and not progress(done, total):
                    raise Cancelled("Hashing cancelled")
    return digest.hexdigest()


class Cancelled(Exception):
    """Raised when a caller aborts a long-running disc operation."""
