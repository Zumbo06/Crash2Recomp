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

from .. import config, paths
from ..paths import Layout
from ..runtime import GameSession, apply_config_settings
from ..version import full_version
from .dialogs import about, confirm, tell
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

# Nav entries a player never sees. Log stays visible - it is how someone
# reports a problem - but Advanced is measurement tooling that can make the
# game worse, so it only appears in developer mode.
DEVELOPER_ONLY = {"advanced"}


def nav_groups(developer: bool) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """The sidebar for this mode, with empty groups dropped."""
    if developer:
        return NAV_GROUPS
    out = []
    for group, items in NAV_GROUPS:
        kept = [i for i in items if i[0] not in DEVELOPER_ONLY]
        if kept:
            out.append((group, kept))
    return out

# Which stack widget each nav key shows.
_STACK_FOR = {
    "play": "play", "setup": "setup", "log": "log", "advanced": "advanced",
}


class MainWindow(QWidget):
    def __init__(self, layout_: Layout, settings: config.Settings):
        super().__init__()
        self.layout_ = layout_
        self.settings = settings

        self.setWindowTitle("Crash Bandicoot 2 Recompiled  -  %s" % full_version())
        self.resize(1440, 740)
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
        self.setup_page.relayout.connect(self._relayout)
        self.play_page.crashed.connect(self._on_crash)
        self.play_page.setup_requested.connect(
            lambda: self._select(next(i for i, (key, _, _) in enumerate(self._pages)
                                      if key == "setup")))
        self.settings_page.changed.connect(self._on_settings_changed)
        self.advanced_page.changed.connect(self._on_settings_changed)
        self.session.output.connect(self.log_page.append)
        self.session.failed.connect(self.log_page.append)

        # Restore the page and geometry the user left on.
        start = settings.last_page if layout_.has_runtime else "setup"
        index = next((i for i, (key, _, _) in enumerate(self._pages)
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

        # The build this launcher is driving, not the internal layout mode -
        # "Workspace" meant nothing to anyone who had not read paths.py.
        subtitle = QLabel("Unofficial recompilation")
        subtitle.setObjectName("SidebarSubtitle")
        lay.addWidget(subtitle)

        # Nav buttons are rebuilt whenever developer mode changes, so keep a
        # container to refill rather than recreating the whole sidebar.
        self._nav_host = QWidget()
        self._nav_lay = QVBoxLayout(self._nav_host)
        self._nav_lay.setContentsMargins(0, 0, 0, 0)
        self._nav_lay.setSpacing(0)
        lay.addWidget(self._nav_host)

        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self.nav.idClicked.connect(self._select)
        # The stack and its pages do not exist yet, so only build the buttons;
        # __init__ makes the initial selection once everything is constructed.
        self._rebuild_nav(select=False)

        lay.addStretch(1)

        # Version, credits and the licence position. Sits below the nav rather
        # than in it: it is reference, not a place you work.
        about_btn = QPushButton("About")
        about_btn.setObjectName("NavButton")
        about_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        about_btn.clicked.connect(lambda: about(self))
        lay.addWidget(about_btn)
        return bar

    def _rebuild_nav(self, select: bool = True) -> None:
        """Refill the sidebar for the current mode, preserving the open page."""
        current = self.settings.last_page
        for btn in list(self.nav.buttons()):
            self.nav.removeButton(btn)
            btn.setParent(None)
            btn.deleteLater()
        while self._nav_lay.count():
            item = self._nav_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._pages = [item for _, items in nav_groups(self.settings.developer_mode)
                       for item in items]
        index = 0
        for group, items in nav_groups(self.settings.developer_mode):
            caption = QLabel(group)
            caption.setObjectName("NavGroup")
            self._nav_lay.addWidget(caption)
            for _, label, _section in items:
                btn = QPushButton(label)
                btn.setObjectName("NavButton")
                btn.setCheckable(True)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                self.nav.addButton(btn, index)
                self._nav_lay.addWidget(btn)
                index += 1

        if not select:
            return
        # The page that was open may have just been hidden (leaving developer
        # mode while Advanced is showing); fall back to Play.
        target = next((i for i, (k, _, _) in enumerate(self._pages)
                       if k == current), None)
        if target is None:
            target = next((i for i, (k, _, _) in enumerate(self._pages)
                           if k == "play"), 0)
        self._select(target)

    def _select(self, index: int) -> None:
        if not 0 <= index < len(self._pages):
            return
        key, _label, sec = self._pages[index]
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
        # Developer mode adds or removes a nav entry.
        showing_advanced = any(k == "advanced" for k, _, _ in self._pages)
        if showing_advanced != self.settings.developer_mode:
            self._rebuild_nav()

    def _relayout(self) -> None:
        """Re-resolve every path from disk.

        The layout is worked out once at startup, and a first-run build creates
        files that did not exist then - above all the game binary, whose name
        the recompiler takes from the disc serial rather than the name we
        guessed. Without this a successful build still left Play greyed out.
        """
        self.layout_ = paths.detect()
        self.layout_.ensure_writable_dirs()
        for page in (self.setup_page, self.play_page):
            page.set_layout(self.layout_)

    def _on_crash(self, code: int, explanation: str) -> None:
        """The game died. Say so in words, and put the evidence in front of
        the user instead of leaving it on a page they may not know about."""
        self._select(next((i for i, (k, _, _) in enumerate(self._pages)
                           if k == "log"), 0))
        tell(self, "The game crashed",
             explanation + "\n\nThe log is on screen behind this message. "
             "Save it if you want to report the problem.",
             detail="Exit code: %d" % code, error=True)

    def _on_build_ready(self) -> None:
        """The game just finished building - Play becomes usable."""
        self.play_page.refresh()
        self._select(next(i for i, (k, _, _) in enumerate(self._pages)
                          if k == "play"))

    def closeEvent(self, event: QCloseEvent) -> None:
        # A build is minutes of work and spawns cmake/ninja as children; closing
        # used to orphan them silently, leaving a compiler running with nothing
        # watching it. Ask, then take the whole job down.
        if self.setup_page.busy:
            if not confirm(self, "Stop the build?",
                           "The game is still being built. Closing now cancels "
                           "it, and you will have to start again.",
                           "Close and cancel", danger=True):
                event.ignore()
                return
        self.setup_page.shutdown()
        if self.session.running:
            self.session.stop()
        self.settings.window_geometry = bytes(
            self.saveGeometry().toBase64()).decode("ascii")
        config.save(self.layout_.settings_file, self.settings)
        super().closeEvent(event)
