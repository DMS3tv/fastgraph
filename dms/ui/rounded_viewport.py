"""A plot frame that hides square child corners without changing plot exports."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from dms.ui.style_tokens import mode_tokens


class _RoundedViewportOverlay(QWidget):
    def __init__(self, radius: int, parent: QWidget) -> None:
        super().__init__(parent)
        self._radius = int(radius)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    @property
    def radius(self) -> int:
        return self._radius

    def paintEvent(self, _event) -> None:
        app = QApplication.instance()
        mode = app.property("fastgraphVisualMode") if app is not None else "dark"
        tokens = mode_tokens(mode)
        rect = self.rect().adjusted(0, 0, -1, -1)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            outside = QPainterPath()
            outside.addRect(QRectF(self.rect()))
            rounded = QPainterPath()
            rounded.addRoundedRect(QRectF(rect), self._radius, self._radius)
            painter.fillPath(
                outside.subtracted(rounded),
                QColor(tokens.viewport),
            )

            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(tokens.border), 1.0))
            painter.drawRoundedRect(
                QRectF(rect),
                self._radius,
                self._radius,
            )
        finally:
            painter.end()


class RoundedViewportFrame(QWidget):
    """Show a rounded viewport while leaving the child capture unchanged."""

    def __init__(
        self,
        child: QWidget,
        *,
        radius: int = 10,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.child = child
        self._radius = int(radius)
        self.setProperty("surfaceLevel", "viewport")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(child)

        self._overlay = _RoundedViewportOverlay(self._radius, self)
        self._overlay.raise_()

    @property
    def radius(self) -> int:
        return self._radius

    def resizeEvent(self, event) -> None:
        self._overlay.setGeometry(self.rect())
        self._overlay.raise_()
        super().resizeEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.PaletteChange,
            QEvent.Type.StyleChange,
        }:
            self._overlay.update()
