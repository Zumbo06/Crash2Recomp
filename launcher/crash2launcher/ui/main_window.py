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
from .page_advanced import AdvancedPage
from .page_log import LogPage
from .page_play import PlayPage
from .page_settings import SettingsPage
from .page_setup import SetupPage
from .theme import SIDEBAR_W, SPACE_3

# One navigation rail, grouped. The settings sections used to be a second rail
# nested inside the Settings page; they are top-level entries now and all point
# at the same SettingsPage widget with a different section selected.
#
# Advanced sits last and apart: it holds diagnostics that change how the game
# behaves, not quality options.
#
# (key, label, section) - section is the SettingsPage section for settings.* keys.
NAV_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("PLAY", [
        ("play", "Play", ""),
        ("setup", "Setup", ""),
    ]),
    ("SETTINGS", [
        ("settings.video", "Video", "Video"),
        ("settings.audio", "Audio", "Audio"),
        ("settings.input", "Input", "Input"),
        ("settings.performance", "Performance", "Performance"),
    ]),
    ("TOOLS", [
        ("log", "Log", ""),
        ("advanced", "Advanced", ""),
    ]),
]
PAGES = [item for _, items in NAV_GROUPS for item in items]

# Which stack widget each nav key shows.
_STACK_FOR = {
    "play": "play", "setup": "setup", "log": "log", "advanced": "advanced",
}


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

        self.setup_page = SetupPage(layout_, settings)
        self.play_page = PlayPage(layout_, settings, self.session)
        self.settings_page = SettingsPage(settings)
        self.log_page = LogPage()
        self.advanced_page = AdvancedPage(settings)

        # Stack order is independent of the nav order now; _select maps.
        self._stack_index = {}
        for name, widget in (("setup", self.setup_page),
                             ("play", self.play_page),
                             ("settings", self.settings_page),
                             ("log", self.log_page),
                             ("advanced", self.advanced_page)):
            self._stack_index[name] = self.stack.count()
            self.stack.addWidget(widget)

        self.setup_page.ready.connect(self._on_build_ready)
        self.settings_page.changed.connect(self._on_settings_changed)
        self.advanced_page.changed.connect(self._on_settings_changed)
        self.session.output.connect(self.log_page.append)
        self.session.failed.connect(self.log_page.append)

        # Restore the page and geometry the user left on.
        start = settings.last_page if layout_.has_runtime else "setup"
        index = next((i for i, (key, _, _) in enumerate(PAGES)
                      if key == start), 0)
        self._select(index)
        if settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(
                settings.window_geometry.encode("ascii")))

    # -- chrome ------------------------------------------------------------
    def _sidebar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("Sidebar")
        bar.setFixedWidth(SIDEBAR_W)

        lay = QVBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, SPACE_3)
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
        index = 0
        for group, items in NAV_GROUPS:
            caption = QLabel(group)
            caption.setObjectName("NavGroup")
            lay.addWidget(caption)
            for _, label, _section in items:
                btn = QPushButton(label)
                btn.setObjectName("NavButton")
                btn.setCheckable(True)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                self.nav.addButton(btn, index)
                lay.addWidget(btn)
                index += 1

        self.nav.idClicked.connect(self._select)
        lay.addStretch(1)
        return bar

    def _select(self, index: int) -> None:
        key, _label, sec = PAGES[index]
        if sec:
            self.settings_page.show_section(sec)
            self.stack.setCurrentIndex(self._stack_index["settings"])
        else:
            self.stack.setCurrentIndex(self._stack_index[_STACK_FOR[key]])
        button = self.nav.button(index)
        if button:
            button.setChecked(True)
        self.settings.last_page = key

    # -- events ------------------------------------------------------------
    def _on_settings_changed(self) -> None:
        self.settings.clamp()
        # Supersampling has no env override, so it must reach game.toml before
        # the next launch.
        apply_config_settings(self.layout_, self.settings)
        config.save(self.layout_.settings_file, self.settings)
        self.play_page.mark_settings_changed()

    def _on_build_ready(self) -> None:
        """The game just finished building - Play becomes usable."""
        self.play_page.refresh()
        self._select(next(i for i, (k, _, _) in enumerate(PAGES) if k == "play"))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.session.running:
            self.session.stop()
        self.settings.window_geometry = bytes(
            self.saveGeometry().toBase64()).decode("ascii")
        config.save(self.layout_.settings_file, self.settings)
        super().closeEvent(event)
