"""The window and taskbar icon, drawn rather than shipped.

Deliberately not game artwork. Everything visual in this launcher is generated
(see hero.py) so the project distributes no copyrighted assets, and a fan
project has no licence to ship the real logo. A wordmark on the project's
accent colour is honest about what it is and still gives the window a
recognisable identity in the taskbar - which it previously lacked entirely,
appearing as a generic Python process.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPixmap

from .theme import ACCENT, ACCENT_DEEP, ACCENT_INK

# Windows picks the closest of these for the title bar, alt-tab and taskbar.
_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _render(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    grad = QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0.0, QColor(ACCENT))
    grad.setColorAt(1.0, QColor(ACCENT_DEEP))
    p.setBrush(QBrush(grad))
    p.setPen(Qt.PenStyle.NoPen)
    radius = size * 0.22
    p.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    # A "2" reads at 16px where a full wordmark turns to mush.
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(size * 0.68))
    p.setFont(f)
    p.setPen(QColor(ACCENT_INK))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "2")
    p.end()
    return pm


def app_icon() -> QIcon:
    """A multi-resolution icon. Built once at startup; cheap enough to draw."""
    icon = QIcon()
    for size in _SIZES:
        icon.addPixmap(_render(size))
    return icon
