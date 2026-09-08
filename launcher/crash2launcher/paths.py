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


#: Dropped into a release bundle by tools/package.ps1. It is the marker that
#: says "this is a shipped bundle", and it is what makes a FRESH bundle - one
#: where the player has not built the game yet, so no runtime exists - resolve
#: correctly. Keying player mode purely off the runtime binary meant the very
#: first launch fell through to workspace paths and looked for a _build tree
#: that a bundle does not contain, breaking Setup before it could run once.
BUNDLE_MARKER = "bundle.json"


def is_bundle(root: Path) -> bool:
    return (root / BUNDLE_MARKER).is_file()


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
        here = Path(sys.executable).resolve().parent
        # PyInstaller onedir puts the executable in its own folder, so in a
        # release bundle the exe is one level BELOW the bundle root that holds
        # data/, userdata/, recompiler/ and the marker. Walk up a little to
        # find it; without this the launcher anchors inside its own program
        # folder and writes saves next to the Qt DLLs.
        for candidate in (here, *here.parents[:2]):
            if (candidate / BUNDLE_MARKER).is_file():
                return candidate
        return here
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
        """The emitter that turns overlay captures into native shards.

        compile_overlays.py refuses to emit unless this binary's baked codegen
        hash equals the one the runtime tree stamps into
        overlay_codegen_hash.h. Our patch 0002 edits cpu_state.h and
        psx_cycles.h, both listed in codegen_hash_sources.cmake, so the
        prebuilt psxrecomp-cli binary can never match our tree - it was built
        from unpatched sources. Prefer a recompiler built from the vendored
        tree (_build/build_recompiler.ps1); fall back to the prebuilt one so a
        checkout that has not built it still runs, just interpreted.
        """
        local = (self.project.parent / "build-recompiler"
                 / f"psxrecomp-game{_EXE_SUFFIX}")
        if local.is_file():
            return local
        return self.cli_exe.parent / "libexec" / f"psxrecomp-game{_EXE_SUFFIX}"

    @property
    def bios_rom(self) -> Path:
        """The BIOS image the recompiler needs in order to build a project.

        `psxrecomp.exe build` requires --disc, --bios AND --output; the Setup
        page used to omit --bios and the build died on the usage message before
        doing anything. OpenBIOS ships inside the recompiler's own framework
        tree, so it is always beside the CLI in both layouts.
        """
        return self.cli_exe.parent / "framework" / "bios" / "openbios.bin"

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
            # A frozen bundle has no interpreter of its own; without one the
            # compile command cannot be formed at all.
            and find_overlay_python() is not None
        )

    @property
    def project_is_disposable(self) -> bool:
        """True when the launcher owns the project directory outright.

        `psxrecomp.exe build` refuses a non-empty --output, so a rebuild has to
        clear it first. That is only safe where the directory exists solely to
        hold generated output: the bundle's game/ folder. In a workspace the
        project is the developer's tree - build outputs, tuning, everything -
        and must never be deleted on our initiative.
        """
        return self.mode is Mode.PLAYER

    def ensure_writable_dirs(self) -> None:
        """Create the directories we own. Safe to call repeatedly."""
        for d in (self.userdata, self.save_dir, self.mods):
            d.mkdir(parents=True, exist_ok=True)


def find_overlay_python() -> Path | None:
    """A real Python interpreter for the overlay compile step.

    The runtime shells out to compile_overlays.py while the game runs. From a
    source checkout ``sys.executable`` is the interpreter and that is the right
    answer. FROZEN it is Crash2Launcher.exe, so handing it to the runtime would
    build a command that relaunches the launcher instead of compiling anything
    - the overlay tier would silently fall back to the MIPS interpreter, which
    is the exact degraded state that once hid an audio bug for months.

    So when frozen, look for a real interpreter and report honestly when there
    is none.
    """
    if not is_frozen():
        return Path(sys.executable)
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return Path(found)
    # The Windows launcher: `py -3` resolves any installed version.
    found = shutil.which("py")
    return Path(found) if found else None


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

    # Player mode: either the marker a packaged bundle carries, or a runtime
    # sitting next to us (an already-built bundle, or one repackaged by hand).
    #
    # The generated project goes in its OWN subdirectory rather than the bundle
    # root, because `psxrecomp.exe build` refuses a non-empty --output and the
    # root is full of the things we shipped (the launcher, the recompiler,
    # LICENSES, userdata). game/ is entirely launcher-owned, so a rebuild can
    # clear it without touching saves or settings.
    #
    # Search build/ too, not just the root: build.ps1 configures cmake into
    # <project>/build, so that is where the compiled game actually lands.
    # Searching only the root meant a bundle reported "not built" after a
    # completely successful build.
    project = root / "game"
    player_runtime = find_runtime(
        project / "build", root / "build", root, root / "build-clang")
    if player_runtime is not None or is_bundle(root):
        return Layout(
            mode=Mode.PLAYER,
            root=root,
            project=project,
            # Before the first build there is no runtime yet; name where it
            # will land so the Play page can say so instead of crashing.
            runtime_exe=player_runtime or (project / "build" / RUNTIME_EXE),
            game_toml=project / "game.toml",
            disc_data=root / "data",
            userdata=root / "userdata",
            mods=root / "mods",
            build_dir=project / "build",
            # The recompiler ships in its own folder so its framework/ tree
            # cannot be mistaken for the generated project.
            cli_exe=root / "recompiler" / "psxrecomp.exe",
            src_cli=root / "recompiler" / "psxrecomp_cli.py",
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
