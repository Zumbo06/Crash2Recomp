"""Artwork-backed Play surface with native, keyboard-accessible controls.

The concept's mock status panel is covered by live widgets and its footer is
masked by the page footer. No modified copy of the supplied image is created.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import QFrame, QPushButton, QWidget

from ..paths import bundled_asset_dir
from .theme import PLAY_BG, PLAY_GOLD, PLAY_ORANGE, PLAY_EDGE

REFERENCE_WIDTH = 1672
SCENE_BOTTOM = 941
BUTTON_SOURCE = QRectF(84, 373, 458, 115)


class StatusPanel(QFrame):
    """Metal corner fasteners drawn over the stylesheet's panel surface."""

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for x in (9, self.width() - 9):
            for y in (9, self.height() - 9):
                p.setBrush(QColor("#26455f"))
                p.setPen(QPen(QColor("#72a1c3"), 1))
                p.drawEllipse(QPointF(x, y), 2.5, 2.5)
        p.end()


class StatusBadge(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(48, 48)
        self.colour = "#42ef85"
        self.ready = True

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        glow = QColor(self.colour)
        glow.setAlpha(18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QRectF(0, 0, 48, 48))
        p.setPen(QPen(QColor(self.colour), 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(5, 5, 38, 38))
        if self.ready:
            p.setPen(QPen(QColor(self.colour), 5, Qt.PenStyle.SolidLine,
                         Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPolyline([QPointF(14, 24), QPointF(22, 32), QPointF(35, 17)])
        else:
            p.setPen(QPen(QColor(self.colour), 3, Qt.PenStyle.SolidLine,
                         Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(24, 14), QPointF(24, 27))
            p.drawPoint(QPointF(24, 34))
        p.end()


class PlayScene(QWidget):
    """A full-page scene with native children positioned by the Play page."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("PlayScene")
        self.artwork = QPixmap(str(bundled_asset_dir() / "play-reference.png"))
        self.custom_artwork = QPixmap()
        if self.artwork.isNull():
            for name in ("hero.png", "hero.jpg", "hero.jpeg"):
                self.custom_artwork.load(str(bundled_asset_dir() / name))
                if not self.custom_artwork.isNull():
                    break

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(PLAY_BG))
        scale = self.width() / REFERENCE_WIDTH
        art_height = SCENE_BOTTOM * scale
        if not self.artwork.isNull():
            p.drawPixmap(QRectF(0, 0, self.width(), art_height), self.artwork,
                         QRectF(0, 0, REFERENCE_WIDTH, SCENE_BOTTOM))
        else:
            self._paint_fallback(p, scale)

        # Remove the concept's footer. The live panel covers its mock values;
        # both use the same reference coordinates (see PlayPage).
        footer_y = 894 * scale
        p.fillRect(QRectF(0, footer_y, self.width(), self.height()), QColor(PLAY_BG))
        fade = QLinearGradient(0, 885 * scale, 0, footer_y)
        clear = QColor(PLAY_BG)
        clear.setAlpha(0)
        fade.setColorAt(0, clear)
        fade.setColorAt(1, QColor(PLAY_BG))
        p.fillRect(QRectF(0, 885 * scale, self.width(), 9 * scale + 1), fade)
        p.end()

    def _paint_fallback(self, p: QPainter, scale: float) -> None:
        if not self.custom_artwork.isNull():
            pm = self.custom_artwork.scaled(
                self.width(), round(SCENE_BOTTOM * scale),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(0, 0, pm)
            p.fillRect(self.rect(), QColor(3, 12, 25, 170))
        else:
            glow = QLinearGradient(0, 0, self.width(), self.height())
            glow.setColorAt(0, QColor("#102a3d"))
            glow.setColorAt(.55, QColor("#082033"))
            glow.setColorAt(1, QColor(PLAY_BG))
            p.fillRect(self.rect(), glow)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for radius in (180, 210, 240, 275):
                p.setPen(QPen(QColor(32, 159, 216, 45), 4))
                p.drawEllipse(QPointF(self.width() * .73, self.height() * .38),
                              radius * scale, radius * scale)
        p.setPen(QColor(PLAY_GOLD))
        font = QFont("Segoe UI", max(20, round(61 * scale)), QFont.Weight.Black)
        p.setFont(font)
        p.drawText(QRectF(85 * scale, 55 * scale, 650 * scale, 145 * scale),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "CRASH 2")
        font.setPointSize(max(13, round(28 * scale)))
        p.setFont(font)
        p.drawText(QRectF(90 * scale, 195 * scale, 620 * scale, 75 * scale),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "RECOMPILED")


class PlayButton(QPushButton):
    """Normal QPushButton semantics, including Tab, Space and focus."""

    def __init__(self, artwork: QPixmap, parent: QWidget | None = None):
        super().__init__("PLAY", parent)
        self.artwork = artwork
        self.setObjectName("ArtworkPlayButton")
        self.setAccessibleName("Play")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        if self.isDown():
            p.translate(0, 2)
        if not self.artwork.isNull() and self.text() == "PLAY":
            p.drawPixmap(QRectF(self.rect()), self.artwork, BUTTON_SOURCE)
        else:
            wood = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            wood.setColorAt(0, QColor(PLAY_ORANGE))
            wood.setColorAt(.48, QColor("#e5790b"))
            wood.setColorAt(.51, QColor("#b54a06"))
            wood.setColorAt(1, QColor("#e86b0b"))
            p.setBrush(wood)
            p.setPen(QPen(QColor(PLAY_GOLD), 3))
            p.drawRoundedRect(rect, 9, 9)
            p.setPen(QPen(QColor("#7c3005"), 1))
            p.drawLine(QPointF(12, self.height() * .55),
                       QPointF(self.width() - 12, self.height() * .55))
            for x in (14, self.width() - 14):
                for y in (14, self.height() - 14):
                    p.setBrush(QColor("#a44708"))
                    p.drawEllipse(QPointF(x, y), 3, 3)
            font = QFont("Segoe UI", max(14, int(self.height() * .32)), QFont.Weight.Black)
            p.setFont(font)
            metrics = p.fontMetrics()
            path = QPainterPath()
            path.addText((self.width() - metrics.horizontalAdvance(self.text())) / 2,
                         (self.height() + metrics.ascent() - metrics.descent()) / 2,
                         font, self.text())
            p.setPen(QPen(QColor("#451d05"), 2))
            p.setBrush(QColor(PLAY_GOLD))
            p.drawPath(path)
            p.fillPath(path, QColor(PLAY_GOLD))
        if not self.isEnabled():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(5, 15, 26, 115))
            p.drawRoundedRect(rect, 9, 9)
        elif self.underMouse():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 228, 118, 35))
            p.drawRoundedRect(rect, 9, 9)
        if self.hasFocus():
            p.setPen(QPen(QColor(PLAY_EDGE), 2, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(5, 5, -5, -5), 5, 5)
        p.end()
