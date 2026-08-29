"""Filesystem layout resolution.

The launcher runs in two shapes and has to find its files in both:

* **Player** - the shipped bundle. ``Crash2.exe`` sits beside ``psx-runtime.exe``,
  ``game.toml`` and ``data/``. Everything is one flat folder.
* **Workspace** - the development tree. The launcher runs from source and the
  generated project lives under ``_build/Crash2Recomp/``.

Getting this wrong is the classic "works in dev, broken once packaged" bug, so
mode detection is explicit and every path flows from :func:`app_dir`.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

_EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""

# The recompiled binary is named after the game by the generator
# (e.g. Crash_Bandicoot_2_Recompiled.exe), and older//generic builds use
# psx-runtime. Search rather than assume, most specific name first.
RUNTIME_NAMES = (
    f"Crash_Bandicoot_2_Recompiled{_EXE_SUFFIX}",
    f"psx-runtime{_EXE_SUFFIX}",
)
RUNTIME_EXE = RUNTIME_NAMES[0]


def find_runtime(*dirs: Path) -> Path | None:
    """Locate the recompiled game binary in the first directory that has one."""
    for d in dirs:
        if not d or not d.is_dir():
            continue
        for name in RUNTIME_NAMES:
            candidate = d / name
            if candidate.is_file():
                return candidate
        # Fall back to any *Recompiled executable the generator produced.
        for candidate in sorted(d.glob(f"*Recompiled{_EXE_SUFFIX}")):
            if candidate.is_file():
                return candidate
    return None


class Mode(Enum):
    PLAYER = "player"
    WORKSPACE = "workspace"


def is_frozen() -> bool:
    """True when running from a PyInstaller build."""
    return getattr(sys, "frozen", False)


def app_dir() -> Path:
    """Directory the application is anchored to.

    Frozen: the folder holding the executable - *not* ``sys._MEIPASS``, which is
    the temporary unpack dir and vanishes on exit. Source: the repo root, two
    levels above this file (``launcher/crash2launcher/paths.py``).
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def bundled_asset_dir() -> Path:
    """Read-only assets shipped *inside* the frozen bundle (icons, QSS)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent / "ui" / "assets"


@dataclass(frozen=True)
class Layout:
    """Every path the launcher needs, resolved once at startup."""

    mode: Mode
    root: Path            # app anchor
    project: Path         # generated recomp project (workspace) or bundle root
    runtime_exe: Path     # psx-runtime executable
    game_toml: Path
    disc_data: Path       # prepared disc image directory
    userdata: Path        # settings + saves, always writable
    mods: Path
    build_dir: Path       # workspace only; where cmake writes
    cli_exe: Path         # workspace only; psxrecomp.exe
    src_cli: Path         # workspace only; psxrecomp_cli.py for `analyze`

    @property
    def settings_file(self) -> Path:
        return self.userdata / "settings.json"

    @property
    def save_dir(self) -> Path:
        return self.userdata / "save"

    @property
    def has_runtime(self) -> bool:
        return self.runtime_exe.is_file()

    # --- overlay native-compilation inputs --------------------------------
    # The runtime compiles captured overlays by shelling out to
    # tools/compile_overlays.py. These are the pieces that command needs.

    @property
    def overlay_script(self) -> Path:
        return self.src_cli.parent / "tools" / "compile_overlays.py"

    @property
    def recompiler_exe(self) -> Path:
        return self.cli_exe.parent / "libexec" / f"psxrecomp-game{_EXE_SUFFIX}"

    @property
    def runtime_include(self) -> Path:
        return self.project / "psxrecomp" / "runtime" / "include"

    @property
    def overlay_cache(self) -> Path:
        return self.runtime_exe.parent / "cache"

    @property
    def overlay_captures(self) -> Path:
        return self.runtime_exe.parent / "overlay_captures.json"

    @property
    def can_compile_overlays(self) -> bool:
        """True when every input for the native overlay tier is present."""
        return (
            self.overlay_script.is_file()
            and self.recompiler_exe.is_file()
            and self.runtime_include.is_dir()
            and find_c_toolchain_bin() is not None
        )

    def ensure_writable_dirs(self) -> None:
        """Create the directories we own. Safe to call repeatedly."""
        for d in (self.userdata, self.save_dir, self.mods):
            d.mkdir(parents=True, exist_ok=True)


def find_c_toolchain_bin() -> Path | None:
    """Locate a C compiler directory for the runtime's overlay compiler.

    Crash 2 streams level code as overlays. Any overlay the runtime cannot
    compile natively falls back to the MIPS interpreter, which is correct but
    slow. The runtime decides by scanning PATH for gcc/cc/clang
    (``autocompile_toolchain_available`` in runtime/src/autocompile.c), so
    putting the toolchain on PATH is all that is needed to unlock the native
    tier.

    Prefers psxrecomp's own pinned clang/MinGW pack - the same toolchain the
    game itself was built with - then falls back to anything already on PATH.
    """
    pack = Path.home() / ".local" / "share" / "retcomm" / "toolchains" / "cmake-clang-v1"
    if pack.is_dir():
        # Newest version wins; the directory is named by semver.
        for version in sorted(pack.iterdir(), reverse=True):
            candidate = version / "bin"
            if (candidate / f"clang{_EXE_SUFFIX}").is_file():
                return candidate

    for name in (f"clang{_EXE_SUFFIX}", f"gcc{_EXE_SUFFIX}", f"cc{_EXE_SUFFIX}"):
        found = shutil.which(name)
        if found:
            return Path(found).parent
    return None


def detect(root: Path | None = None) -> Layout:
    """Work out which shape we are running in and resolve all paths."""
    root = (root or app_dir()).resolve()

    # Player mode is defined by the runtime sitting next to us. That is the one
    # signal that is true in the shipped bundle and false in the source tree.
    player_runtime = find_runtime(root)
    if player_runtime is not None:
        return Layout(
            mode=Mode.PLAYER,
            root=root,
            project=root,
            runtime_exe=player_runtime,
            game_toml=root / "game.toml",
            disc_data=root / "data",
            userdata=root / "userdata",
            mods=root / "mods",
            build_dir=root / "build",
            cli_exe=root / "psxrecomp.exe",
            src_cli=root / "psxrecomp_cli.py",
        )

    build = root / "_build"
    project = build / "Crash2Recomp"
    # The clang build tree is the supported one; keep the msvc tree as a fallback.
    ws_runtime = find_runtime(project / "build-clang", project / "build")
    return Layout(
        mode=Mode.WORKSPACE,
        root=root,
        project=project,
        runtime_exe=ws_runtime or (project / "build-clang" / RUNTIME_EXE),
        game_toml=project / "game.toml",
        disc_data=project / "input",
        userdata=root / "launcher" / "userdata",
        mods=project / "mods",
        build_dir=project / "build-clang",
        cli_exe=build / "psxrecomp-cli" / "psxrecomp.exe",
        src_cli=build / "psxrecomp-src" / "psxrecomp_cli.py",
    )
