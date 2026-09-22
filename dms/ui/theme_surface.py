"""Small painted surfaces used by the theme selector and classic theme."""

from __future__ import annotations

from functools import lru_cache

from PyQt6.QtCore import QRect, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

from dms.style_tokens import mode_tokens, tokens_for

_BAYER_8 = (
    (0, 48, 12, 60, 3, 51, 15, 63),
    (32, 16, 44, 28, 35, 19, 47, 31),
    (8, 56, 4, 52, 11, 59, 7, 55),
    (40, 24, 36, 20, 43, 27, 39, 23),
    (2, 50, 14, 62, 1, 49, 13, 61),
    (34, 18, 46, 30, 33, 17, 45, 29),
    (10, 58, 6, 54, 9, 57, 5, 53),
    (42, 26, 38, 22, 41, 25, 37, 21),
)


@lru_cache(maxsize=256)
def _dither_tile(foreground_rgba: int, background_rgba: int, density_steps: int) -> QPixmap:
    foreground = QColor.fromRgba(foreground_rgba)
    background = QColor.fromRgba(background_rgba)
    image = QImage(8, 8, QImage.Format.Format_ARGB32)
    for y, row in enumerate(_BAYER_8):
        for x, threshold in enumerate(row):
            image.setPixelColor(x, y, foreground if threshold < density_steps else background)
    return QPixmap.fromImage(image)


def dither_brush(
    foreground: QColor,
    *,
    background: QColor | None = None,
    density: float = 0.25,
) -> QBrush:
    """Return a cached 8-by-8 ordered pixel pattern."""

    steps = max(0, min(64, round(float(density) * 64)))
    clear = QColor(0, 0, 0, 0) if background is None else QColor(background)
    return QBrush(_dither_tile(QColor(foreground).rgba(), clear.rgba(), steps))


def paint_dither(
    painter: QPainter,
    rect: QRect | QRectF,
    *,
    foreground: QColor,
    background: QColor | None = None,
    density: float = 0.25,
) -> None:
    """Fill a rectangle with the shared variable-density pixel pattern."""

    painter.fillRect(
        rect,
        dither_brush(foreground, background=background, density=density),
    )


def _paint_dither(painter: QPainter, rect: QRect, *, light: QColor, dark: QColor) -> None:
    """Keep the original helper signature for existing callers."""

    paint_dither(
        painter,
        rect,
        foreground=light,
        background=dark,
        density=0.25,
    )


def _chrome_dither_colors(tokens, *, preview: bool = False) -> tuple[QColor, QColor]:
    if tokens.dither_chrome and not tokens.classic_controls:
        light = QColor(tokens.text)
        light.setAlpha(38)
        dark = QColor(tokens.shadow)
        dark.setAlpha(34)
        return light, dark
    return (
        QColor(tokens.border)
        if tokens.terminal_chrome
        else QColor(255, 255, 255, 100 if preview else 92),
        QColor(0, 0, 0, 48 if preview else 42),
    )


class DitherSurface(QWidget):
    """Paint a restrained pixel texture on theme chrome."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("ditherSurface", True)

    def paintEvent(self, event) -> None:
        app = QApplication.instance()
        mode = app.property("fastgraphVisualMode") if app is not None else "dark"
        tokens = mode_tokens(mode)
        if not (tokens.classic_controls or tokens.dither_chrome):
            super().paintEvent(event)
            return

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(tokens.panel))
        light, dark = _chrome_dither_colors(tokens)
        _paint_dither(
            painter,
            self.rect(),
            light=light,
            dark=dark,
        )
        if tokens.flat_controls:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(tokens.border), tokens.geometry.border_px))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()


class ThemePreview(QWidget):
    """Compact token preview for one registered theme."""

    def __init__(self, theme: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = str(theme)
        self.setFixedSize(78, 30)
        self.setAccessibleName(f"{self._theme} theme preview")

    def paintEvent(self, event) -> None:
        tokens = tokens_for(self._theme)
        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            not tokens.classic_controls,
        )
        outer = self.rect().adjusted(0, 0, -1, -1)
        radius = min(5, tokens.geometry.radius_surface)
        painter.setPen(QPen(QColor(tokens.border), 1))
        painter.setBrush(QColor(tokens.background))
        painter.drawRoundedRect(outer, radius, radius)

        panel = QRect(4, 4, 34, 21)
        painter.fillRect(panel, QColor(tokens.panel))
        if tokens.classic_controls or tokens.dither_chrome:
            light, dark = _chrome_dither_colors(tokens, preview=True)
            _paint_dither(
                painter,
                panel,
                light=light,
                dark=dark,
            )
        if tokens.classic_controls:
            painter.setPen(
                QPen(
                    QColor(tokens.border if tokens.terminal_chrome else "#ffffff"),
                    1,
                )
            )
            painter.drawLine(panel.topLeft(), panel.topRight())
            painter.drawLine(panel.topLeft(), panel.bottomLeft())
            painter.setPen(QPen(QColor("#000000"), 1))
            painter.drawLine(panel.bottomLeft(), panel.bottomRight())
            painter.drawLine(panel.topRight(), panel.bottomRight())

        painter.fillRect(QRect(42, 4, 31, 9), QColor(tokens.selected))
        painter.fillRect(QRect(42, 16, 31, 9), QColor(tokens.plot_bg))
        painter.setPen(QPen(QColor(tokens.accent), 1))
        painter.drawLine(44, 22, 51, 19)
        painter.drawLine(51, 19, 58, 22)
        painter.drawLine(58, 22, 70, 18)
        painter.end()
