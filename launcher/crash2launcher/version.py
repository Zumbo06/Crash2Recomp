"""Version and provenance, in one place.

The About panel, the window title and the packaging script all read from here,
so there is exactly one string to bump. A packaged build overwrites
``version.txt`` next to the launcher; running from the source tree falls back
to the constant plus the git revision when one is available.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

VERSION = "1.0.0"

APP_NAME = "Crash 2 Recompiled"
ORG_NAME = "Crash2Recomp"

# Shown in the About panel. Trademark position: this is a fan project, it
# includes no game data, and it is not endorsed by the rights holders.
DISCLAIMER = (
    "An unofficial, non-commercial fan project. Not affiliated with, "
    "authorised or endorsed by Activision, Naughty Dog or Sony Interactive "
    "Entertainment. Crash Bandicoot is a trademark of Activision Publishing, "
    "Inc. No game code, artwork, audio or disc data is included: the game is "
    "built on your machine from a disc image you already own."
)


def _git_revision() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def full_version() -> str:
    """``1.0.0`` in a packaged build, ``1.0.0 (abc1234)`` from a checkout."""
    stamped = Path(__file__).resolve().parent.parent / "version.txt"
    try:
        text = stamped.read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    rev = _git_revision()
    return f"{VERSION} ({rev})" if rev else VERSION
