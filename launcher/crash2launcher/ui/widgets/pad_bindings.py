"""Controller button mapping: press-to-assign through XInput, or pick from a list."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QGridLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)

from ... import padbinds, xinput
from ...config import Settings
from ..common import dim, section

POLL_MS = 15


class CapturePad(QDialog):
    """Waits for one controller button; the list below is always available."""

    def __init__(self, label: str, current: str, parent: QWidget):
        super().__init__(parent)
        self.value = current
        self.setWindowTitle("Assign controller button")
        self.setMinimumWidth(420)
        root = QVBoxLayout(self)
        root.addWidget(QLabel(f"Press a button on your controller for {label}"))
        self.status = dim("")
        root.addWidget(self.status)
        self.choice = QComboBox()
        for name, text in padbinds.SOURCES:
            self.choice.addItem(text, name)
        index = self.choice.findData(current)
        self.choice.setCurrentIndex(max(0, index))
        root.addWidget(self.choice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._choose)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._watcher = xinput.PressWatcher() if xinput.available() else None
        self._ticks = 0
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        if self._watcher is not None:
            self._timer.start()
        self._refresh_status()

    def _refresh_status(self) -> None:
        if self._watcher is None:
            self.status.setText("Button capture needs XInput, which this system "
                                "does not have. Choose the button from the list.")
            return
        count = self._watcher.connected()
        if count:
            self.status.setText(
                "Listening to %d Xbox-compatible controller%s. Escape cancels; "
                "you can also choose from the list." % (count, "" if count == 1 else "s"))
        else:
            self.status.setText(
                "No Xbox-compatible controller is connected. PlayStation pads "
                "without Steam Input work in the game but cannot be heard here - "
                "choose the button from the list.")

    def _poll(self) -> None:
        name = self._watcher.poll()
        if name:
            self._timer.stop()
            self.value = name
            self.accept()
            return
        # A pad plugged in while the dialog is open: say so within a second.
        self._ticks += 1
        if self._ticks % 60 == 0:
            self._refresh_status()

    def _choose(self) -> None:
        self.value = self.choice.currentData()
        self.accept()

    def done(self, result: int) -> None:  # noqa: D401 - Qt override
        self._timer.stop()
        super().done(result)


class PadBindingsEditor(QWidget):
    changed = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.buttons: dict[tuple[str, int], QPushButton] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        root.addWidget(dim(
            "Click a button, then press the controller button you want. Each "
            "PS1 button can have two. Names are positions: A is the bottom face "
            "button on every pad (Cross on a PlayStation controller). Changes "
            "apply on the next launch."))
        for title, actions in padbinds.GROUPS:
            root.addWidget(section(title))
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.addWidget(dim("PS1 button"), 0, 0)
            grid.addWidget(dim("Controller button"), 0, 1)
            grid.addWidget(dim("Alternate"), 0, 2)
            grid.setColumnStretch(0, 2)
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(2, 1)
            for i, (action, label, _default) in enumerate(actions, 1):
                grid.addWidget(QLabel(label), i, 0)
                for slot in range(2):
                    button = QPushButton()
                    button.setCursor(Qt.CursorShape.PointingHandCursor)
                    button.setAccessibleName(
                        f"{label}: {'controller' if slot == 0 else 'alternate controller'} button")
                    button.clicked.connect(
                        lambda checked=False, a=action, s=slot, lab=label: self.capture(a, s, lab))
                    grid.addWidget(button, i, slot + 1)
                    self.buttons[action, slot] = button
            root.addLayout(grid)
        self.note = dim("")
        root.addWidget(self.note)
        root.addWidget(dim(
            "The pause menu (Guide, or Start + Back) and the runtime's other "
            "controller shortcuts read the physical buttons, whatever is mapped "
            "here."))
        reset = QPushButton("Restore controller defaults")
        reset.clicked.connect(self.reset_defaults)
        root.addWidget(reset, alignment=Qt.AlignmentFlag.AlignLeft)
        self.refresh()

    def _pair(self, action: str) -> list[str]:
        return list(padbinds.split(self.settings.pad_bindings.get(
            action, padbinds.DEFAULTS[action])))

    def capture(self, action: str, slot: int, label: str) -> None:
        dialog = CapturePad(label, self._pair(action)[slot], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.assign(action, slot, dialog.value)

    def assign(self, action: str, slot: int, source: str) -> None:
        pair = self._pair(action)
        pair[slot] = source
        if pair[0] == pair[1]:
            pair[1] = "none"
        # Record the whole map on first edit: input.ini is only rewritten for
        # buttons the launcher holds, and a partial map would leave the rest
        # to whatever the file says - fine, but surprising next to the editor.
        if not self.settings.pad_bindings:
            self.settings.pad_bindings = dict(padbinds.DEFAULTS)
        self.settings.pad_bindings[action] = padbinds.join(*pair)
        self.refresh()
        self.changed.emit()

    def reset_defaults(self) -> None:
        self.settings.pad_bindings = dict(padbinds.DEFAULTS)
        self.refresh()
        self.changed.emit()

    def refresh(self) -> None:
        for (action, slot), button in self.buttons.items():
            name = self._pair(action)[slot]
            button.setText(padbinds.LABELS.get(name, name))
        uses: dict[str, list[str]] = {}
        labels = {a: lab for _, rows in padbinds.GROUPS for a, lab, _ in rows}
        for action in padbinds.DEFAULTS:
            for name in self._pair(action):
                if name != "none":
                    uses.setdefault(name, []).append(labels[action])
        shared = ["%s → %s" % (padbinds.LABELS[name].split(" ·")[0], " + ".join(acts))
                  for name, acts in uses.items() if len(acts) > 1]
        unbound = [labels[a] for a in padbinds.DEFAULTS
                   if all(n == "none" for n in self._pair(a))]
        parts = []
        if shared:
            parts.append("Pressed together by one button: " + "; ".join(shared) + ".")
        if unbound:
            parts.append("Not reachable from a controller: " + ", ".join(unbound) + ".")
        self.note.setText(" ".join(parts))
        self.note.setVisible(bool(parts))
