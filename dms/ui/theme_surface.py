"""Small painted surfaces used by the theme selector and classic theme."""

from __future__ import annotations

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

from dms.ui.style_tokens import mode_tokens, tokens_for


def _paint_dither(painter: QPainter, rect: QRect, *, light: QColor, dark: QColor) -> None:
    """Paint a restrained two-color pixel pattern."""
    painter.setPen(Qt.PenStyle.NoPen)
    for y in range(rect.top() + 1, rect.bottom(), 4):
        offset = 0 if ((y - rect.top()) // 4) % 2 == 0 else 2
        for x in range(rect.left() + 1 + offset, rect.right(), 4):
            painter.fillRect(x, y, 1, 1, light)
            if x + 2 < rect.right() and y + 2 < rect.bottom():
                painter.fillRect(x + 2, y + 2, 1, 1, dark)


class DitherSurface(QWidget):
    """Use a light pixel dither only while the FastGraph 95 theme is active."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("ditherSurface", True)

    def paintEvent(self, event) -> None:
        app = QApplication.instance()
        mode = app.property("fastgraphVisualMode") if app is not None else "dark"
        tokens = mode_tokens(mode)
        if not tokens.classic_controls:
            super().paintEvent(event)
            return

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(tokens.panel))
        _paint_dither(
            painter,
            self.rect(),
            light=QColor(tokens.border) if tokens.terminal_chrome else QColor(255, 255, 255, 92),
            dark=QColor(0, 0, 0, 42),
        )
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
        if tokens.classic_controls:
            _paint_dither(
                painter,
                panel,
                light=QColor(tokens.border) if tokens.terminal_chrome else QColor(255, 255, 255, 100),
                dark=QColor(0, 0, 0, 48),
            )
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
