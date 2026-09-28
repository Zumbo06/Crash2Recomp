"""Save states and memory cards, for the Saves page.

Where the runtime keeps them (main.cpp savestate_configure, savestate.c):

  <save_dir>/<bios>/state_<ENTRYPC>_slotNN.pst    a save state, slots 00..11
  <save_dir>/<bios>/state_<ENTRYPC>_slotNN.thumb  its 128x96 preview: "PSTH",
      uint32 width, uint32 height (little-endian), then width*height
      little-endian ARGB32 pixels
  <save_dir>/card1.mcd, card2.mcd                 the memory cards

<bios> is "openbios" for the bundled BIOS. The entry PC in the name ties a
state to the game build that wrote it; the newest group is the live one.

Pure Python (no Qt), so the rules can be tested on their own. The page turns
thumbnail pixels into a QImage itself.
"""

from __future__ import annotations

import os
import re
import struct
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

SLOTS = 12
THUMB_MAGIC = b"PSTH"
CARD_NAMES = ("card1.mcd", "card2.mcd")
_STATE_RE = re.compile(r"state_([0-9A-Fa-f]{8})_slot(\d\d)\.pst$")
_BACKUP_GLOB = "memcards-*.zip"
# A PS1 card image is 128 KiB; anything far larger is not one.
_CARD_MAX_BYTES = 1 << 20


@dataclass
class Slot:
    index: int                  # 0..11; the game shows it as index + 1
    state: Path | None          # None: the slot is empty
    thumb: Path | None
    mtime: float | None
    size: int = 0

    @property
    def exists(self) -> bool:
        return self.state is not None


def find_slots(save_dir: Path) -> list[Slot]:
    """All twelve slots, empty ones included, from the newest build's group."""
    groups: dict[tuple[Path, str], dict[int, Path]] = {}
    if save_dir.is_dir():
        for pst in save_dir.glob("*/state_*_slot??.pst"):
            match = _STATE_RE.match(pst.name)
            if not match:
                continue
            index = int(match.group(2))
            try:
                if index >= SLOTS or pst.stat().st_size <= 0:
                    continue
            except OSError:
                continue
            groups.setdefault((pst.parent, match.group(1).upper()), {})[index] = pst

    def newest(files: dict[int, Path]) -> float:
        times = []
        for p in files.values():
            try:
                times.append(p.stat().st_mtime)
            except OSError:
                pass
        return max(times, default=0.0)

    files = max(groups.values(), key=newest) if groups else {}
    slots = []
    for index in range(SLOTS):
        pst = files.get(index)
        if pst is None:
            slots.append(Slot(index, None, None, None))
            continue
        try:
            info = pst.stat()
        except OSError:
            slots.append(Slot(index, None, None, None))
            continue
        thumb = pst.with_suffix(".thumb")
        slots.append(Slot(index, pst, thumb if thumb.is_file() else None,
                          info.st_mtime, info.st_size))
    return slots


def read_thumb(path: Path | None) -> tuple[int, int, bytes] | None:
    """(width, height, ARGB32 pixel bytes) of a .thumb, or None if unusable."""
    if path is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 12 or data[:4] != THUMB_MAGIC:
        return None
    width, height = struct.unpack_from("<II", data, 4)
    if not (0 < width <= 1024 and 0 < height <= 1024):
        return None
    end = 12 + width * height * 4
    if len(data) < end:
        return None
    return width, height, data[12:end]


def delete_slot(slot: Slot) -> None:
    """Remove a state and its preview. Missing files are not an error."""
    for path in (slot.state, slot.thumb):
        if path is not None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def memory_cards(save_dir: Path) -> list[tuple[str, float | None]]:
    """(card file name, last written time or None) for both card slots."""
    out = []
    for name in CARD_NAMES:
        path = save_dir / name
        try:
            out.append((name, path.stat().st_mtime if path.is_file() else None))
        except OSError:
            out.append((name, None))
    return out


def backup_memcards(save_dir: Path, backups: Path, note: str = "") -> Path | None:
    """Zip both cards into `backups`. None when there is no card to back up."""
    cards = [save_dir / n for n in CARD_NAMES if (save_dir / n).is_file()]
    if not cards:
        return None
    backups.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = f"-{note}" if note else ""
    path = backups / f"memcards-{stamp}{suffix}.zip"
    n = 2
    while path.exists():
        path = backups / f"memcards-{stamp}{suffix}-{n}.zip"
        n += 1
    tmp = path.with_suffix(".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            for card in cards:
                archive.write(card, card.name)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def list_backups(backups: Path) -> list[Path]:
    """Memory card backups, newest first."""
    if not backups.is_dir():
        return []
    return sorted(backups.glob(_BACKUP_GLOB),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def restore_memcards(archive_path: Path, save_dir: Path, backups: Path) -> Path | None:
    """Put a backup's cards back. The current cards are backed up first, so a
    restore can itself be undone; that safety copy's path is returned.

    Only members named exactly like a card are read - nothing in the archive
    decides where anything is written.
    """
    with zipfile.ZipFile(archive_path) as archive:
        members = [n for n in archive.namelist() if n in CARD_NAMES]
        if not members:
            raise ValueError("This backup has no memory card in it.")
        payload = {}
        for name in members:
            if archive.getinfo(name).file_size > _CARD_MAX_BYTES:
                raise ValueError(f"{name} in this backup is not a memory card.")
            payload[name] = archive.read(name)
    safety = backup_memcards(save_dir, backups, "before-restore")
    save_dir.mkdir(parents=True, exist_ok=True)
    for name, data in payload.items():
        tmp = save_dir / (name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, save_dir / name)
    return safety
