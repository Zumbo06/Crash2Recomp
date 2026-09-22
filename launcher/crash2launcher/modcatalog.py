"""Stage the framework's builtin mod catalog next to the runtime.

Why this exists
---------------
The runtime has a complete mod system compiled into it - ``mod_packages.cpp``,
``mod_runtime.cpp`` and three builtin plugin implementations are all in
``build.ninja`` - and four manifests upstream that select them: PGXP, Fast
Loading, CD Speed and Bezel.

None of them were reachable. ``runtime.cmake`` stages the catalog only when
``${PSXRECOMP_ROOT}/mods/builtin/packages`` exists, and it does not: the
prebuilt ``psxrecomp.exe`` ships a ``framework/`` payload containing exactly
``bios``, ``recompiler`` and ``runtime``. Every project generated from that CLI
inherits the gap, so ``<exe_dir>/mods`` ended up holding one file::

    state.toml:  format_version = 2

Four working, compiled-in enhancements, invisible.

Rather than patch the generated framework tree - which is gitignored, and is
recreated from scratch every time the user re-runs Generate - the launcher
carries its own copy of the four manifests under ``moddata/`` and stages them
straight into ``<exe_dir>/mods/packages``. That is the directory the runtime
actually scans (``mod_runtime_initialize(exe_dir / "mods", ...)``), so this
needs no rebuild and survives regeneration.

Interaction with CMake
----------------------
If a future framework drop *does* ship ``mods/builtin/packages``, its
POST_BUILD step clears and repopulates ``$<TARGET_FILE_DIR>/mods/packages``
itself. That is fine and takes precedence: we would then be copying the same
manifests it already installed. The clear is scoped to ``mods/packages`` and
deliberately spares ``state.toml``, which is user state.

These manifests are framework-owned content, copied verbatim. When the vendored
framework updates, re-copy them rather than hand-editing:

    cp -r _build/psxrecomp-src/mods/builtin/packages/. \\
          launcher/crash2launcher/moddata/builtin/packages/
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# Package ids we ship. Kept explicit so a stray directory under moddata/ cannot
# silently become a mod, and so a missing one is a loud error rather than a
# quietly shorter catalog.
BUILTIN_PACKAGE_IDS = (
    "psx.enhancement.cd-speed",
    "psx.enhancement.fast-loading",
    "psx.enhancement.pgxp",
    "psx.presentation.bezel",
)


def source_dir() -> Path:
    """Where our copy of the builtin manifests lives.

    Frozen, PyInstaller unpacks ``datas`` under ``sys._MEIPASS``; the spec
    preserves the ``moddata/...`` sub-path so the same relative layout works in
    both shapes.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    base = Path(meipass) if meipass else Path(__file__).resolve().parent
    return base / "moddata" / "builtin" / "packages"


def available() -> bool:
    return source_dir().is_dir()


def stage_builtin(layout, *, force: bool = False) -> tuple[int, str]:
    """Copy the builtin catalog into ``<exe_dir>/mods/packages``.

    Returns ``(count, message)``. ``count`` is how many packages are present
    afterwards, so 0 always means something went wrong and the Mods page has
    nothing to show.

    Existing package directories are left alone unless ``force`` - a player may
    have installed a newer version of a framework package, and overwriting it
    on every launch would undo that silently.
    """
    # Never stage before a build exists. `mods` lives inside the build output
    # directory, and in a player bundle `psxrecomp.exe build` refuses a
    # non-empty --output - so creating it early would break the build that
    # produces the runtime. No runtime also means nothing to serve mods to.
    if not layout.has_runtime:
        return (0, "no runtime built yet; skipping builtin mod staging")

    src = source_dir()
    if not src.is_dir():
        return (0, f"builtin mod catalog missing from the launcher: {src}")

    dst = layout.mod_packages
    try:
        dst.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return (0, f"could not create {dst}: {exc}")

    staged, skipped, failed = [], [], []
    for package_id in BUILTIN_PACKAGE_IDS:
        src_pkg = src / package_id
        if not src_pkg.is_dir():
            failed.append(f"{package_id} (missing from launcher data)")
            continue
        dst_pkg = dst / package_id
        if dst_pkg.exists() and not force:
            skipped.append(package_id)
            continue
        try:
            if dst_pkg.exists():
                shutil.rmtree(dst_pkg)
            shutil.copytree(src_pkg, dst_pkg)
        except OSError as exc:
            failed.append(f"{package_id} ({exc})")
            continue
        staged.append(package_id)

    present = sum(1 for p in BUILTIN_PACKAGE_IDS if (dst / p).is_dir())

    parts = []
    if staged:
        parts.append(f"installed {len(staged)}")
    if skipped:
        parts.append(f"kept {len(skipped)}")
    if failed:
        parts.append("FAILED: " + ", ".join(failed))
    return (present, "builtin mods: " + ("; ".join(parts) or "nothing to do"))


def installed_packages(layout) -> list[str]:
    """Every package id under ``<exe_dir>/mods/packages``, ours or not.

    A package directory is only real if it holds ``<version>/manifest.toml`` -
    the runtime's scan enforces that the directory names match the manifest's
    own id and version, so a bare directory is not a mod.
    """
    root = layout.mod_packages
    if not root.is_dir():
        return []
    found = []
    for pkg in sorted(root.iterdir()):
        if not pkg.is_dir():
            continue
        if any(v.is_dir() and (v / "manifest.toml").is_file() for v in pkg.iterdir()):
            found.append(pkg.name)
    return found
