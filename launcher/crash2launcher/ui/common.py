"""Small shared building blocks for the pages.

Kept deliberately thin - these exist so the pages read as layout rather than as
a wall of Qt boilerplate, and so spacing/margins stay consistent without every
page re-deciding them. All metrics come from `theme`; nothing here invents a
pixel value.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme import (CARD_MARGINS, LABEL_COL, NUMBER_W, PAGE_MARGINS, SPACE_2,
                    SPACE_3, SPACE_4)


class FlowLayout(QLayout):
    """Left-to-right items that wrap onto the next line when the row is full.

    The preset buttons used a QHBoxLayout and, at the window's minimum width,
    were squeezed until their text clipped ("uthenti", "nhance"). This is
    Qt's own flow-layout example: every item keeps its size hint and the
    layout reports a height for the width it is given.
    """

    def __init__(self, parent: QWidget | None = None, spacing: int = SPACE_2):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802 - Qt naming
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _arrange(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line = area.x(), area.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > area.right() + 1 and line > 0:
                x = area.x()
                y += line + self._spacing
                line = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line = max(line, hint.height())
        return y + line - rect.y() + m.bottom()


def card(*children: QWidget, spacing: int = SPACE_3, tone: str = "") -> QFrame:
    """A bordered panel grouping related controls.

    `tone` ("warn" / "error" / "flat") is applied as a Qt property that the
    stylesheet selects on. It must not be done with setStyleSheet() on the
    frame: a widget-level sheet resets inheritance for that whole subtree, so
    such a card silently opts out of every other Card rule.
    """
    frame = QFrame()
    frame.setObjectName("Card")
    if tone:
        frame.setProperty("tone", tone)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(*CARD_MARGINS)
    lay.setSpacing(spacing)
    for child in children:
        lay.addWidget(child)
    return frame



def heading(title: str, hint: str = "") -> QWidget:
    """Page title with an optional one-line explanation beneath it."""
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(3)

    label = QLabel(title)
    label.setObjectName("PageTitle")
    lay.addWidget(label)

    if hint:
        sub = QLabel(hint)
        sub.setObjectName("PageHint")
        sub.setWordWrap(True)
        lay.addWidget(sub)
    return box


def section(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("SectionTitle")
    return label


def _tagged(text: str, name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(True)
    return label


def dim(text: str) -> QLabel:
    return _tagged(text, "Dim")


# Status colours go through these rather than inline <span style="color:...">
# HTML. The QSS classes already existed and were never used, so every status
# string in the app bypassed the stylesheet and hard-coded a palette value.
def ok(text: str) -> QLabel:
    return _tagged(text, "Ok")


def warn(text: str) -> QLabel:
    return _tagged(text, "Warn")


def err(text: str) -> QLabel:
    return _tagged(text, "Error")


def set_status(label: QLabel, tone: str, text: str) -> None:
    """Re-tone an EXISTING label whose text changes at runtime.

    ``tone`` is "", "Ok", "Warn" or "Error". Qt only re-evaluates a stylesheet
    when a widget is re-polished, so changing the object name alone leaves the
    old colour on screen - the same trap that made an earlier setProperty call
    elsewhere in the UI a silent no-op.
    """
    label.setText(text)
    if label.objectName() != tone:
        label.setObjectName(tone)
        style = label.style()
        style.unpolish(label)
        style.polish(label)


def row(label: str, widget: QWidget, label_width: int = LABEL_COL) -> QWidget:
    """A left-aligned label paired with a control.

    A number field keeps a number's width instead of stretching across the
    card - a "4" in a 900 px box read as a text field and buried its arrows.
    """
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(SPACE_3)

    text = QLabel(label)
    text.setFixedWidth(label_width)
    text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    lay.addWidget(text)
    if isinstance(widget, QAbstractSpinBox):
        widget.setMaximumWidth(NUMBER_W)
        lay.addWidget(widget)
        lay.addStretch(1)
    else:
        if isinstance(widget, QComboBox):
            # A combo otherwise refuses to be narrower than its longest option,
            # which pushed the Video and Performance cards past the window at
            # its minimum width (a sideways scroll bar and clipped cards). Let
            # the closed box shrink and elide; the open list stays readable.
            widget.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            widget.setMinimumContentsLength(12)
            # From the texts, not the view: a view that has never been shown
            # reports a size hint of 0. The margin covers padding, the scroll
            # bar and the stylesheet's 13 px font being wider than the default.
            fm = widget.fontMetrics()
            texts = [widget.itemText(i) for i in range(widget.count())]
            if texts:
                widget.view().setMinimumWidth(
                    max(fm.horizontalAdvance(t) for t in texts) + 56)
        lay.addWidget(widget, 1)
    return box


def stat_row(label: str, value: QLabel) -> QWidget:
    """A read-only label/value pair. Shares row()'s label column so status
    lines and settings rows line up rather than each picking a width."""
    dim_label = QLabel(label)
    dim_label.setObjectName("Dim")
    dim_label.setFixedWidth(LABEL_COL)
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(SPACE_3)
    lay.addWidget(dim_label)
    lay.addWidget(value, 1)
    return box



def stretch() -> QWidget:
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    return w
