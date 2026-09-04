"""Small shared building blocks for the pages.

Kept deliberately thin - these exist so the pages read as layout rather than as
a wall of Qt boilerplate, and so spacing/margins stay consistent without every
page re-deciding them. All metrics come from `theme`; nothing here invents a
pixel value.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme import CARD_MARGINS, LABEL_COL, PAGE_MARGINS, SPACE_2, SPACE_3, SPACE_4


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


def set_tone(widget: QWidget, tone: str) -> None:
    """Change a card's tone after construction.

    Qt does not re-evaluate property selectors on its own - without the
    unpolish/polish pair the new value is stored and ignored, which is exactly
    why the old `setProperty("active", ...)` call did nothing.
    """
    widget.setProperty("tone", tone or None)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def page_body(*, spacing: int = SPACE_4) -> tuple[QWidget, QVBoxLayout]:
    """A page's content column, with the standard margins."""
    body = QWidget()
    lay = QVBoxLayout(body)
    lay.setContentsMargins(*PAGE_MARGINS)
    lay.setSpacing(spacing)
    return body, lay


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


def row(label: str, widget: QWidget, label_width: int = LABEL_COL) -> QWidget:
    """A left-aligned label paired with a control."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(SPACE_3)

    text = QLabel(label)
    text.setFixedWidth(label_width)
    text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    lay.addWidget(text)
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


def hstack(*children: QWidget, spacing: int = SPACE_2) -> QWidget:
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for child in children:
        lay.addWidget(child)
    return box


def stretch() -> QWidget:
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    return w
