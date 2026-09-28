"""Saves - save-state slots and memory cards, outside the game.

The runtime keeps twelve save-state slots, each with a thumbnail, and two
memory cards. In the game the slots are only reachable through the save state
menu and the quick keys; here they can be looked at, started from, deleted, and
the cards backed up.

Nothing here touches a file while the game runs - the runtime may be writing
it - so deleting and restoring wait for the game to close.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zipfile import BadZipFile

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import saves
from ..config import Settings
from ..paths import Layout
from ..runtime import GameSession
from .common import card, dim, heading, row, section, set_status
from .dialogs import confirm, tell
from .theme import (BG_SUNKEN, BORDER, CARD_MARGINS, PAGE_MARGINS, SPACE_2,
                    SPACE_3, SPACE_4, TEXT_FAINT)

THUMB_W, THUMB_H = 192, 144        # the runtime's 128x96, at 1.5x
TILE_GAP = SPACE_4


def _when(stamp: float | None) -> str:
    if stamp is None:
        return "Empty"
    return datetime.fromtimestamp(stamp).strftime("%d %b %Y, %H:%M")


def thumb_pixmap(slot: saves.Slot) -> QPixmap | None:
    """The slot's preview, scaled for a tile, or None when it has none."""
    pixels = saves.read_thumb(slot.thumb)
    if pixels is None:
        return None
    width, height, data = pixels
    # Little-endian ARGB32 in memory is exactly QImage's Format_ARGB32; copy()
    # so the image owns its pixels once `data` goes away.
    image = QImage(data, width, height, width * 4, QImage.Format.Format_ARGB32).copy()
    return QPixmap.fromImage(image).scaled(
        THUMB_W, THUMB_H, Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation)


class SlotTile(QWidget):
    """One slot: preview, name, date and its two actions."""

    play = Signal(int)
    delete = Signal(int)

    def __init__(self, index: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.index = index
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_2)

        self.thumb = QLabel()
        self.thumb.setFixedSize(THUMB_W, THUMB_H)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # A leaf label, so a widget-level sheet cannot cut anything else off
        # from the application stylesheet (see common.card).
        self.thumb.setStyleSheet(
            f"background: {BG_SUNKEN}; border: 1px solid {BORDER};"
            f" color: {TEXT_FAINT};")
        lay.addWidget(self.thumb)

        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.title)
        self.when = dim("")
        lay.addWidget(self.when)

        buttons = QHBoxLayout()
        buttons.setSpacing(SPACE_2)
        self.play_btn = QPushButton("Play from here")
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.clicked.connect(lambda: self.play.emit(self.index))
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_btn.clicked.connect(lambda: self.delete.emit(self.index))
        buttons.addWidget(self.play_btn)
        buttons.addWidget(self.delete_btn)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        self.setFixedWidth(THUMB_W + 2 * SPACE_3)

    def show_slot(self, slot: saves.Slot, quick: bool, can_play: bool,
                  can_delete: bool) -> None:
        name = f"Slot {slot.index + 1}"
        self.title.setText(name + ("  ·  quick save slot" if quick else ""))
        self.when.setText(_when(slot.mtime))
        pixmap = thumb_pixmap(slot) if slot.exists else None
        if pixmap is not None:
            self.thumb.setPixmap(pixmap)
        else:
            self.thumb.setPixmap(QPixmap())
            self.thumb.setText("No preview" if slot.exists else "Empty")
        self.play_btn.setEnabled(slot.exists and can_play)
        self.delete_btn.setEnabled(slot.exists and can_delete)
        self.play_btn.setToolTip(
            "Starts the game and loads this save state." if slot.exists else "")


class SavesPage(QWidget):
    # Start the game from a slot (0..11). The window hands it to the Play page.
    play_slot = Signal(int)

    def __init__(self, layout_: Layout, settings: Settings, session: GameSession,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.layout_ = layout_
        self.settings = settings
        self.session = session
        self._slots: list[saves.Slot] = []
        self._columns = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(self.scroll)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(*PAGE_MARGINS)
        lay.setSpacing(SPACE_4)
        self.scroll.setWidget(body)

        lay.addWidget(heading(
            "Saves", "Save states and memory cards. Deleting can't be undone."))
        self.running_note = dim("Close the game to delete a slot or restore a "
                                "backup.")
        lay.addWidget(self.running_note)
        lay.addWidget(self._states_card())
        lay.addWidget(self._cards_card())
        lay.addStretch(1)

        session.started.connect(self.refresh)
        session.finished.connect(lambda _code: self.refresh())
        self.refresh()

    # -- pieces ------------------------------------------------------------
    def _states_card(self) -> QWidget:
        self.tiles = [SlotTile(i) for i in range(saves.SLOTS)]
        for tile in self.tiles:
            tile.play.connect(self._on_play)
            tile.delete.connect(self._on_delete)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(TILE_GAP)
        self.grid.setVerticalSpacing(TILE_GAP)
        self._place_tiles(4)
        return card(
            section("Save states"),
            dim("Quick save, quick load and the save state menu use these "
                "slots. Play from here starts the game in that spot."),
            self.grid_host,
        )

    def _cards_card(self) -> QWidget:
        self.card_lbls = {name: QLabel() for name in saves.CARD_NAMES}
        backup = QPushButton("Back up now")
        backup.clicked.connect(self._on_backup)
        self.restore_btn = QPushButton("Restore a backup...")
        self.restore_btn.clicked.connect(self._on_restore)
        folder = QPushButton("Open saves folder")
        folder.clicked.connect(self._on_open_folder)
        buttons = QWidget()
        blay = QHBoxLayout(buttons)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(SPACE_2)
        for button in (backup, self.restore_btn, folder):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            blay.addWidget(button)
        blay.addStretch(1)
        return card(
            section("Memory cards"),
            row("Card 1", self.card_lbls["card1.mcd"]),
            row("Card 2", self.card_lbls["card2.mcd"]),
            dim("The game's own saves. A backup is a zip in userdata/backups; "
                "restoring one first backs up the cards you have now."),
            buttons,
        )

    # -- layout ------------------------------------------------------------
    def _place_tiles(self, columns: int) -> None:
        columns = max(1, columns)
        if columns == self._columns:
            return
        self._columns = columns
        for tile in self.tiles:
            self.grid.removeWidget(tile)
        for i, tile in enumerate(self.tiles):
            self.grid.addWidget(tile, i // columns, i % columns,
                                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        # Tiles keep their width and sit left; the spare room goes to one
        # empty column after them instead of being spread between them.
        for column in range(saves.SLOTS + 1):
            self.grid.setColumnStretch(column, 1 if column == columns else 0)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # As many columns as fit, so a narrow window wraps instead of
        # scrolling sideways. From the page's own width: the scroll area's
        # viewport is not laid out yet on the first resize. 20 px leaves room
        # for the vertical scroll bar.
        usable = (event.size().width() - 20 - PAGE_MARGINS[0] - PAGE_MARGINS[2]
                  - CARD_MARGINS[0] - CARD_MARGINS[2])
        step = self.tiles[0].width() + TILE_GAP
        self._place_tiles(min(6, max(1, (usable + TILE_GAP) // step)))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Saves happen in the game, not here: re-read whenever the page shows.
        self.refresh()

    # -- state -------------------------------------------------------------
    def set_layout(self, layout_: Layout) -> None:
        self.layout_ = layout_
        self.refresh()

    @property
    def _backups(self):
        return self.layout_.userdata / "backups"

    def refresh(self) -> None:
        running = self.session.running
        self._slots = saves.find_slots(self.layout_.save_dir)
        for tile, slot in zip(self.tiles, self._slots):
            tile.show_slot(slot, slot.index == self.settings.quick_save_slot,
                           can_play=not running and self.layout_.has_runtime,
                           can_delete=not running)
        for name, stamp in saves.memory_cards(self.layout_.save_dir):
            set_status(self.card_lbls[name], "",
                       "Last saved " + _when(stamp) if stamp else "Not created yet")
        self.restore_btn.setEnabled(not running)
        self.running_note.setVisible(running)

    # -- actions -----------------------------------------------------------
    def _on_play(self, index: int) -> None:
        if not self.session.running:
            self.play_slot.emit(index)

    def _on_delete(self, index: int) -> None:
        slot = self._slots[index]
        if self.session.running or not slot.exists:
            return
        if not confirm(self, f"Delete slot {index + 1}?",
                       f"The save state from {_when(slot.mtime)} is removed "
                       "for good.", "Delete", danger=True):
            return
        try:
            saves.delete_slot(slot)
        except OSError as exc:
            tell(self, "Couldn't delete the slot", str(exc), error=True)
        self.refresh()

    def _on_backup(self) -> None:
        try:
            path = saves.backup_memcards(self.layout_.save_dir, self._backups)
        except OSError as exc:
            tell(self, "Backup failed", str(exc), error=True)
            return
        if path is None:
            tell(self, "Nothing to back up",
                 "The game hasn't created a memory card yet.")
        else:
            tell(self, "Memory cards backed up", f"Saved as {path.name}.")

    def _on_restore(self) -> None:
        if self.session.running:
            return
        self._backups.mkdir(parents=True, exist_ok=True)
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Restore memory cards", str(self._backups),
            "Memory card backups (memcards-*.zip);;Zip files (*.zip)")
        if not chosen:
            return
        if not confirm(self, "Restore these memory cards?",
                       "The cards you have now are replaced by the backup. "
                       "They are backed up first, so this can be undone.",
                       "Restore", danger=True):
            return
        try:
            safety = saves.restore_memcards(Path(chosen), self.layout_.save_dir,
                                            self._backups)
        except (OSError, ValueError, BadZipFile) as exc:
            tell(self, "Restore failed", str(exc), error=True)
            return
        note = f" The previous cards are in {safety.name}." if safety else ""
        tell(self, "Memory cards restored", "Restored from "
             f"{Path(chosen).name}.{note}")
        self.refresh()

    def _on_open_folder(self) -> None:
        self.layout_.save_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.layout_.save_dir)))
