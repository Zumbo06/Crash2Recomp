"""Exercise the Play lifecycle without starting the game or touching saves.

Pass --screenshots to render the native widgets at several window sizes into
_build/ui-preview. The same code runs with and without the optional artwork.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFontDatabase, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from crash2launcher import config, paths
from crash2launcher.ui.main_window import MainWindow
from crash2launcher.ui.page_play import PlayPage
from crash2launcher.ui.theme import apply_theme


class Session(QObject):
    started = Signal()
    finished = Signal(int)
    output = Signal(str)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.running = False
        self.plans = []

    def launch(self, plan):
        self.plans.append(plan)

    def stop(self):
        self.running = False
        self.finished.emit(1)


def main() -> None:
    app = QApplication.instance() or QApplication([])
    # Qt's Windows offscreen plugin does not enumerate system fonts.
    if sys.platform == "win32":
        for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / name))
    apply_theme(app)
    output = Path(__file__).resolve().parents[1] / "_build" / "ui-preview"
    screenshots = "--screenshots" in sys.argv
    if screenshots:
        output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="crash2-play-") as tmp:
        root = Path(tmp)
        runtime = root / "build" / paths.RUNTIME_EXE
        runtime.parent.mkdir()
        runtime.touch()
        layout = dataclasses.replace(paths.detect(), root=root, project=root,
            runtime_exe=runtime, game_toml=root / "game.toml", disc_data=root / "data",
            userdata=root / "userdata", mods=root / "mods", build_dir=runtime.parent)
        settings = config.Settings(renderer="vulkan", quick_save_slot=3)
        session = Session()
        page = PlayPage(layout, settings, session)
        page.resize(1232, 800)
        page.show()
        app.processEvents()
        assert page.play_btn.isEnabled()
        assert page.renderer_lbl.text() == "Vulkan" and page.slot_lbl.text() == "03"

        # Space activates the actual native button; repeated activation while
        # starting must not create another process.
        page.play_btn.setFocus()
        QTest.keyClick(page.play_btn, Qt.Key.Key_Space)
        page._on_play()
        assert len(session.plans) == 1 and not page.play_btn.isEnabled()
        session.running = True
        session.started.emit()
        assert page.stop_btn.isVisible() and page.relaunch_btn.isEnabled()
        session.output.emit("[FPS] game: 59.9 fps (1.00x) | frames: 1246")
        assert "59.9" in page.perf_lbl.text() and "59.9" in page.ready_hint.text()
        settings.renderer = "opengl"
        page.mark_settings_changed()
        assert page.renderer_lbl.text() == "OpenGL" and "relaunch" in page.notice.text()
        if screenshots:
            page.grab().save(str(output / "play-running.png"))
        page.relaunch_btn.click()
        assert len(session.plans) == 2 and not page._relaunch_pending
        assert "opengl" in session.plans[-1].args
        session.running = True
        session.started.emit()
        crashes = []
        page.crashed.connect(lambda code, text: crashes.append(code))
        session.stop()
        assert page.play_btn.isEnabled() and not crashes
        assert not page.notice.text() and not page.stop_btn.isVisible()
        session.failed.emit("A test start failure")
        assert "test start failure" in page.notice.text()
        assert not page.relaunch_btn.isEnabled() and page.play_btn.isEnabled()
        session.finished.emit(-1073741819)
        assert crashes == [-1073741819]

        runtime.unlink()
        page.refresh()
        assert not page.play_btn.isEnabled() and page.setup_btn.isVisible()
        assert page.ready_bar.value() == 0
        if screenshots:
            page.grab().save(str(output / "play-missing.png"))
        runtime.touch()
        page.set_layout(layout)
        assert page.play_btn.isEnabled() and not page.notice.text()
        assert not page.setup_btn.isVisible()
        page.details_btn.click()
        assert page.details.isVisible()
        if screenshots:
            page.details.grab().save(str(output / "play-details.png"))
        page.details.close()
        page.close()

        window = MainWindow(layout, settings)
        window.show()
        for width, height in ((1440, 740), (940, 700), (760, 560), (1920, 1080)):
            window.resize(width, height)
            app.processEvents()
            play = window.play_page
            assert play.rect().contains(play.status_panel.geometry())
            assert not play.play_btn.geometry().intersects(play.status_panel.geometry())
            if screenshots:
                window.grab().save(str(output / f"play-{width}x{height}.png"))
        window.resize(940, 700)
        settings.developer_mode = True
        settings.force_interpreter = True
        window.play_page.refresh()
        app.processEvents()
        assert window.play_page.diag_strip.isVisible()
        assert not window.play_page.diag_strip.geometry().intersects(
            window.play_page.play_btn.geometry())
        if screenshots:
            window.grab().save(str(output / "play-diagnostics.png"))
        settings.developer_mode = False
        window.play_page.refresh()
        assert not window.play_page.diag_strip.isVisible()
        window.play_page.artwork = QPixmap()
        window.play_page.play_btn.artwork = QPixmap()
        window.play_page.update()
        app.processEvents()
        if screenshots:
            window.grab().save(str(output / "play-fallback.png"))
        window.close()
        # The packaged launcher under launcher/dist must find the same current
        # setup as source; a release bundle marker must continue to take priority.
        repo = Path(__file__).resolve().parents[1]
        local_exe = repo / "launcher" / "dist" / "Crash2Launcher" / "Crash2Launcher.exe"
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(local_exe)):
            assert paths.app_dir() == repo
        (root / paths.BUNDLE_MARKER).write_text("{}")
        bundle_exe = root / "Crash2Launcher" / "Crash2Launcher.exe"
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(bundle_exe)):
            assert paths.app_dir() == root
    print("Play lifecycle, keyboard activation, relaunch, recovery and resize checks passed.")


if __name__ == "__main__":
    main()
