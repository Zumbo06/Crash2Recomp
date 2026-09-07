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


def main() -> int:
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
