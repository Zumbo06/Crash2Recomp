"""The Play page's hero banner.

Artwork policy: the launcher does NOT ship game art. It is copyrighted, and
this repository is public - the same rule that keeps the disc, the boot
executable and the generated C out of git.

So: if the user drops their own image at ``launcher/assets/hero.png`` it is
used; otherwise the banner is drawn from the theme palette. The generated
version is a real design, not a placeholder apology - a warm gradient with the
title set over it - so the launcher looks finished out of the box.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPaintEvent,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from ..paths import bundled_asset_dir
from .theme import ACCENT, ACCENT_DEEP, BG_RAISED, TEXT, TEXT_DIM

HERO_FILENAMES = ("hero.png", "hero.jpg", "hero.jpeg")


def find_hero_image() -> Path | None:
    """A user-supplied banner, if there is one."""
    for name in HERO_FILENAMES:
        candidate = bundled_asset_dir() / name
        if candidate.is_file():
            return candidate
    return None


class HeroBanner(QWidget):
    """Title banner: user artwork when available, generated art otherwise."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.title = title
        self.subtitle = subtitle
        self.setMinimumHeight(190)
        self._pixmap: QPixmap | None = None

        path = find_hero_image()
        if path:
            pm = QPixmap(str(path))
            if not pm.isNull():
                self._pixmap = pm

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect())

        if self._pixmap is not None:
            self._paint_artwork(painter, rect)
        else:
            self._paint_generated(painter, rect)

        self._paint_title(painter, rect)
        painter.end()

    # -- backgrounds -------------------------------------------------------
    def _paint_artwork(self, painter: QPainter, rect: QRectF) -> None:
        """Cover-fit the user's image, then darken it so text stays readable."""
        assert self._pixmap is not None
        scaled = self._pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        # Centre the overflow rather than anchoring top-left.
        x = (scaled.width() - self.width()) / 2
        y = (scaled.height() - self.height()) / 2
        painter.drawPixmap(self.rect(), scaled, scaled.rect().adjusted(
            int(x), int(y), -int(x), -int(y)))

        shade = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        shade.setColorAt(0.0, QColor(0, 0, 0, 60))
        shade.setColorAt(1.0, QColor(0, 0, 0, 190))
        painter.fillRect(rect, QBrush(shade))

    def _paint_generated(self, painter: QPainter, rect: QRectF) -> None:
        """A warm diagonal gradient with a soft glow behind the title."""
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, QColor(BG_RAISED))
        grad.setColorAt(0.55, QColor(ACCENT_DEEP).darker(190))
        grad.setColorAt(1.0, QColor(BG_RAISED))
        painter.fillRect(rect, QBrush(grad))

        glow = QColor(ACCENT)
        glow.setAlpha(38)
        painter.setBrush(QBrush(glow))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(rect.width() * 0.26, rect.height() * 0.55),
                            rect.width() * 0.30, rect.height() * 0.62)

    # -- foreground --------------------------------------------------------
    def _paint_title(self, painter: QPainter, rect: QRectF) -> None:
        painter.setPen(QColor(ACCENT))
        font = QFont(self.font())
        font.setPointSize(30)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
        painter.setFont(font)
        painter.drawText(
            rect.adjusted(28, 0, -28, -int(rect.height() * 0.20)),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self.title,
        )

        if self.subtitle:
            painter.setPen(QColor(TEXT_DIM))
            sub = QFont(self.font())
            sub.setPointSize(10)
            painter.setFont(sub)
            painter.drawText(
                rect.adjusted(30, 0, -28, -20),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom),
                self.subtitle,
            )
