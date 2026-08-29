"""Small shared building blocks for the pages.

Kept deliberately thin - these exist so the pages read as layout rather than as
a wall of Qt boilerplate, and so spacing/margins stay consistent without every
page re-deciding them.
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


def card(*children: QWidget, spacing: int = 10) -> QFrame:
    """A bordered panel grouping related controls."""
    frame = QFrame()
    frame.setObjectName("Card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 14, 16, 14)
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
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def dim(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Dim")
    label.setWordWrap(True)
    return label


def row(label: str, widget: QWidget, label_width: int = 170) -> QWidget:
    """A left-aligned label paired with a control."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(12)

    text = QLabel(label)
    text.setFixedWidth(label_width)
    text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    lay.addWidget(text)
    lay.addWidget(widget, 1)
    return box


def hstack(*children: QWidget, spacing: int = 8) -> QWidget:
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
