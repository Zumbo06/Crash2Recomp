"""Single-key capture using SDL names, with an explicit selector as fallback."""

from __future__ import annotations

import sys
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout,
    QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ... import keybinds
from ...config import Settings
from ..common import dim, section

# Windows Set-1 physical scan codes. These keep Turkish and other keyboard
# layouts aligned with SDL's physical scancodes rather than translated text.
_SCAN = dict(zip(range(0x10, 0x1a), "QWERTYUIOP"))
_SCAN.update(zip(range(0x1e, 0x27), "ASDFGHJKL"))
_SCAN.update(zip(range(0x2c, 0x33), "ZXCVBNM"))
_SCAN.update(zip(range(2, 12), "1234567890"))
_SCAN.update({0x2a: "Left Shift", 0x36: "Right Shift", 0x1d: "Left Ctrl", 0x38: "Left Alt"})
_QT_KEYS = {
    Qt.Key.Key_Up: "Up", Qt.Key.Key_Down: "Down", Qt.Key.Key_Left: "Left", Qt.Key.Key_Right: "Right",
    Qt.Key.Key_Return: "Return", Qt.Key.Key_Enter: "Keypad Enter", Qt.Key.Key_Space: "Space",
    Qt.Key.Key_Backspace: "Backspace", Qt.Key.Key_Delete: "Delete", Qt.Key.Key_Insert: "Insert",
    Qt.Key.Key_Home: "Home", Qt.Key.Key_End: "End", Qt.Key.Key_PageUp: "PageUp",
    Qt.Key.Key_PageDown: "PageDown", Qt.Key.Key_Tab: "Tab", Qt.Key.Key_CapsLock: "CapsLock",
    Qt.Key.Key_Shift: "Left Shift", Qt.Key.Key_Control: "Left Ctrl", Qt.Key.Key_Alt: "Left Alt",
    Qt.Key.Key_AltGr: "Right Alt",
}


def key_name(event) -> str | None:
    """Return one SDL scancode name; a chord is never written as a key name."""
    scan = int(event.nativeScanCode()) if sys.platform == "win32" else 0
    if scan:
        extended = bool(scan & 0xff00)
        base = scan & 0xff
        if extended and base in (0x1d, 0x38):
            return "Right Ctrl" if base == 0x1d else "Right Alt"
        if base in _SCAN:
            return _SCAN[base]
    key = event.key()
    if event.modifiers() & Qt.KeyboardModifier.KeypadModifier:
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            return f"Keypad {chr(key)}"
        for qt_key, name in ((Qt.Key.Key_Plus, "+"), (Qt.Key.Key_Minus, "-"),
                             (Qt.Key.Key_Asterisk, "*"), (Qt.Key.Key_Slash, "/"),
                             (Qt.Key.Key_Period, ".")):
            if key == qt_key:
                return "Keypad " + name
    if key in _QT_KEYS:
        return _QT_KEYS[key]
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
        return f"F{key - Qt.Key.Key_F1 + 1}"
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        return chr(key)
    char = event.text()
    return char if char in keybinds.KEY_NAMES else None


class CaptureKey(QDialog):
    def __init__(self, label: str, current: str, parent: QWidget):
        super().__init__(parent)
        self.value = current
        self.setWindowTitle("Assign keyboard key")
        self.setMinimumWidth(380)
        root = QVBoxLayout(self)
        root.addWidget(QLabel(f"Press a key for {label}"))
        root.addWidget(dim("Press one key. Escape cancels. You can also choose a key below."))
        self.choice = QComboBox()
        self.choice.addItems(keybinds.KEY_NAMES)
        self.choice.setCurrentText(current)
        root.addWidget(self.choice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._choose)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()

    def _choose(self):
        self.value = self.choice.currentText()
        self.accept()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        if event.isAutoRepeat():
            return
        value = key_name(event)
        if value:
            self.value = value
            self.accept()
        else:
            event.accept()


class KeyBindingsEditor(QWidget):
    changed = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.buttons = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        root.addWidget(dim("Click a key to change it. Each action can have two keys. Changes apply on the next launch."))
        for title, actions in keybinds.GROUPS:
            root.addWidget(section(title))
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.addWidget(dim("Action"), 0, 0)
            grid.addWidget(dim("Primary key"), 0, 1)
            grid.addWidget(dim("Alternate key"), 0, 2)
            grid.setColumnStretch(0, 2)
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(2, 1)
            for i, (action, label, default) in enumerate(actions, 1):
                grid.addWidget(QLabel(label), i, 0)
                for slot in range(2):
                    button = QPushButton()
                    button.setCursor(Qt.CursorShape.PointingHandCursor)
                    button.setAccessibleName(f"{label}: {'primary' if slot == 0 else 'alternate'} key")
                    button.clicked.connect(lambda checked=False, a=action, s=slot, label=label: self.capture(a, s, label))
                    grid.addWidget(button, i, slot + 1)
                    self.buttons[action, slot] = button
            root.addLayout(grid)
        self.warning = dim("")
        root.addWidget(self.warning)
        reset = QPushButton("Restore keyboard defaults")
        reset.clicked.connect(self.reset_defaults)
        root.addWidget(reset, alignment=Qt.AlignmentFlag.AlignLeft)
        self.refresh()

    def capture(self, action: str, slot: int, label: str):
        current = keybinds.split(self.settings.bindings.get(action, keybinds.DEFAULTS[action]))[slot]
        dialog = CaptureKey(label, current, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.assign(action, slot, dialog.value)

    def assign(self, action: str, slot: int, value: str):
        pair = list(keybinds.split(self.settings.bindings.get(action, keybinds.DEFAULTS[action])))
        pair[slot] = value
        self.settings.bindings[action] = keybinds.join(*pair)
        self.refresh()
        self.changed.emit()

    def reset_defaults(self):
        self.settings.bindings = dict(keybinds.DEFAULTS)
        self.refresh()
        self.changed.emit()

    def refresh(self):
        for (action, slot), button in self.buttons.items():
            value = keybinds.split(self.settings.bindings.get(action, keybinds.DEFAULTS[action]))[slot]
            button.setText("Unbound" if value == "None" else value)
        used = {key for action in keybinds.DEFAULTS
                for key in keybinds.split(self.settings.bindings.get(action, keybinds.DEFAULTS[action]))}
        reserved = sorted(used & {"Home", "F", "F5", "F7", "F8", "F9", "Tab", "Keypad +", "Keypad -"})
        self.warning.setText("These keys also activate runtime shortcuts: " + ", ".join(reserved) if reserved else "")
        self.warning.setVisible(bool(reserved))
