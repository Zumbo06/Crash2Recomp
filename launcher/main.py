"""Crash Bandicoot 2 Recompiled - launcher entry point.

Run from the workspace:      python launcher/main.py
Or as the frozen bundle:     Crash2.exe
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python launcher/main.py` without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from crash2launcher import config, paths, runtime  # noqa: E402
from crash2launcher.ui.main_window import MainWindow  # noqa: E402
from crash2launcher.ui.theme import QSS  # noqa: E402


def main() -> int:
    layout = paths.detect()
    layout.ensure_writable_dirs()

    settings = config.load(layout.settings_file).clamp()

    # settings.json is the source of truth, but some options (supersampling,
    # the player-1 device) live in game.toml because the runtime has no env
    # override for them. Push them out once at startup so an externally edited
    # game.toml cannot silently disagree with what the UI is showing.
    runtime.apply_config_settings(layout, settings)

    app = QApplication(sys.argv)
    app.setApplicationName("Crash Bandicoot 2 Recompiled")
    app.setStyleSheet(QSS)

    window = MainWindow(layout, settings)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
