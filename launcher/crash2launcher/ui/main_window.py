"""Main window - sidebar navigation over a stack of pages.

Settings are persisted on every change and again on close, so killing the
launcher never loses a configuration you were mid-way through testing.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config
from ..paths import Layout, Mode
from ..runtime import GameSession, apply_config_settings
from .page_log import LogPage
from .page_play import PlayPage
from .page_settings import SettingsPage

PAGES = [
    ("play", "Play"),
    ("settings", "Settings"),
    ("log", "Log"),
]


class MainWindow(QWidget):
    def __init__(self, layout_: Layout, settings: config.Settings):
        super().__init__()
        self.layout_ = layout_
        self.settings = settings

        self.setWindowTitle("Crash Bandicoot 2 Recompiled")
        self.resize(940, 700)
        self.setMinimumSize(760, 560)

        self.session = GameSession(self)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._sidebar())

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.play_page = PlayPage(layout_, settings, self.session)
        self.settings_page = SettingsPage(settings)
        self.log_page = LogPage()

        self.stack.addWidget(self.play_page)
        self.stack.addWidget(self.settings_page)
        self.stack.addWidget(self.log_page)

        self.settings_page.changed.connect(self._on_settings_changed)
        self.session.output.connect(self.log_page.append)
        self.session.failed.connect(self.log_page.append)

        # Restore the page and geometry the user left on.
        index = next((i for i, (key, _) in enumerate(PAGES)
                      if key == settings.last_page), 0)
        self._select(index)
        if settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(
                settings.window_geometry.encode("ascii")))

    # -- chrome ------------------------------------------------------------
    def _sidebar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("Sidebar")
        bar.setFixedWidth(190)

        lay = QVBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 12)
        lay.setSpacing(0)

        title = QLabel("CRASH 2")
        title.setObjectName("SidebarTitle")
        lay.addWidget(title)

        subtitle = QLabel(
            "Workspace" if self.layout_.mode is Mode.WORKSPACE else "Recompiled"
        )
        subtitle.setObjectName("SidebarSubtitle")
        lay.addWidget(subtitle)

        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        for i, (_, label) in enumerate(PAGES):
            btn = QPushButton(label)
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.nav.addButton(btn, i)
            lay.addWidget(btn)

        self.nav.idClicked.connect(self._select)
        lay.addStretch(1)
        return bar

    def _select(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        button = self.nav.button(index)
        if button:
            button.setChecked(True)
        self.settings.last_page = PAGES[index][0]

    # -- events ------------------------------------------------------------
    def _on_settings_changed(self) -> None:
        self.settings.clamp()
        # Supersampling has no env override, so it must reach game.toml before
        # the next launch.
        apply_config_settings(self.layout_, self.settings)
        config.save(self.layout_.settings_file, self.settings)
        self.play_page.mark_settings_changed()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.session.running:
            self.session.stop()
        self.settings.window_geometry = bytes(
            self.saveGeometry().toBase64()).decode("ascii")
        config.save(self.layout_.settings_file, self.settings)
        super().closeEvent(event)
