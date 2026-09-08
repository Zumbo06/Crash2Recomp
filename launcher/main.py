"""Crash Bandicoot 2 Recompiled - launcher entry point.

Run from the workspace:      python launcher/main.py
Or as the frozen bundle:     Crash2Launcher.exe
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

# Allow `python launcher/main.py` without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from crash2launcher import config, paths, runtime  # noqa: E402
from crash2launcher.version import APP_NAME, ORG_NAME, full_version  # noqa: E402
from crash2launcher.ui.appicon import app_icon  # noqa: E402
from crash2launcher.ui.main_window import MainWindow  # noqa: E402
from crash2launcher.ui.theme import apply_theme  # noqa: E402


def _claim_taskbar_identity() -> None:
    """Give Windows an explicit AppUserModelID.

    Without one, a PySide6 app is grouped under the generic Python host: the
    taskbar shows a Python icon and pinning the launcher pins the interpreter.
    Harmless to fail - it is cosmetic, and does not exist off Windows.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"{ORG_NAME}.{APP_NAME}".replace(" ", ""))
    except Exception:  # noqa: BLE001 - cosmetic only
        pass


def _print_paths() -> int:
    """`--paths`: report where the launcher thinks everything is.

    The first question in any "it does not work in the bundle" report. It runs
    before Qt starts, writes to stdout, and needs no display - so it also works
    over a remote shell and inside a packaging check.
    """
    from crash2launcher.paths import find_c_toolchain_bin, find_overlay_python

    layout = paths.detect()
    print("Crash 2 Recompiled launcher %s" % full_version())
    print("  frozen           : %s" % paths.is_frozen())
    print("  mode             : %s" % layout.mode.value)
    print("  root             : %s" % layout.root)
    rows = [
        ("runtime", layout.runtime_exe, layout.runtime_exe.is_file()),
        ("recompiler", layout.cli_exe, layout.cli_exe.is_file()),
        ("bios", layout.bios_rom, layout.bios_rom.is_file()),
        ("codegen", layout.recompiler_exe, layout.recompiler_exe.is_file()),
        ("overlay script", layout.overlay_script, layout.overlay_script.is_file()),
        ("game.toml", layout.game_toml, layout.game_toml.is_file()),
        ("disc data", layout.disc_data, layout.disc_data.is_dir()),
        ("userdata", layout.userdata, layout.userdata.is_dir()),
    ]
    for label, path, present in rows:
        print("  %-16s : [%s] %s" % (label, "ok" if present else "--", path))
    tc = find_c_toolchain_bin(layout.root)
    py = find_overlay_python(layout.root)
    print("  C toolchain      : [%s] %s" % ("ok" if tc else "--", tc or "not found on PATH"))
    print("  python (overlays): [%s] %s" % ("ok" if py else "--", py or "not found on PATH"))
    print("  native overlays  : %s" % ("yes" if layout.can_compile_overlays
                                       else "NO - level code would be interpreted"))
    return 0


def main() -> int:
    if "--paths" in sys.argv:
        return _print_paths()

    _claim_taskbar_identity()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationVersion(full_version())
    app.setWindowIcon(app_icon())
    apply_theme(app)

    # Startup does real work - resolving paths, creating directories, reading
    # and rewriting config. Any of it can fail on a fresh machine (a read-only
    # install directory, a corrupt settings file). Previously that surfaced as
    # a traceback on a console the player does not have, and a window that
    # never appeared.
    try:
        layout = paths.detect()
        layout.ensure_writable_dirs()
        settings = config.load(layout.settings_file).clamp()
        # settings.json is the source of truth, but some options (supersampling,
        # the player-1 device) live in game.toml because the runtime has no env
        # override for them. Push them out once at startup so an externally
        # edited game.toml cannot silently disagree with what the UI shows.
        runtime.apply_config_settings(layout, settings)
        window = MainWindow(layout, settings)
    except Exception:  # noqa: BLE001 - last resort, so the user sees something
        QMessageBox.critical(
            None, "Could not start",
            "The launcher failed to start.\n\nThis usually means it was "
            "installed somewhere it cannot write to, such as Program Files. "
            "Try moving the folder somewhere under your user account.",
        )
        traceback.print_exc()
        return 1

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
