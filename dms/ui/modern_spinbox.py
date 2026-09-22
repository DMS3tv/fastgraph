"""Theme-aware spin boxes with compact painted step controls."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QApplication,
    QDoubleSpinBox,
    QSpinBox,
    QWidget,
)

from dms.style_tokens import mode_tokens
from dms.theme import mix_colors


class _ChevronStepButton(QAbstractButton):
    def __init__(self, direction: int, parent: QWidget) -> None:
        super().__init__(parent)
        self._direction = 1 if direction > 0 else -1
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoRepeat(True)
        self.setAutoRepeatDelay(350)
        self.setAutoRepeatInterval(80)

    def paintEvent(self, _event) -> None:
        app = QApplication.instance()
        mode = app.property("fastgraphVisualMode") if app is not None else "dark"
        tokens = mode_tokens(mode)
        accent = QColor(tokens.accent)
        base = QColor(tokens.control)
        if self.underMouse():
            base = mix_colors(base, accent, 0.18)
        if self.isDown():
            base = mix_colors(base, accent, 0.28)
        if not self.isEnabled():
            base = QColor(tokens.alternate)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = min(5.0, self.height() / 2.0)
        background = QPainterPath()
        if self._direction > 0:
            background.moveTo(0.0, 0.0)
            background.lineTo(self.width() - radius, 0.0)
            background.quadTo(
                float(self.width()),
                0.0,
                float(self.width()),
                radius,
            )
            background.lineTo(float(self.width()), float(self.height()))
            background.lineTo(0.0, float(self.height()))
        else:
            background.moveTo(0.0, 0.0)
            background.lineTo(float(self.width()), 0.0)
            background.lineTo(float(self.width()), self.height() - radius)
            background.quadTo(
                float(self.width()),
                float(self.height()),
                self.width() - radius,
                float(self.height()),
            )
            background.lineTo(0.0, float(self.height()))
        background.closeSubpath()
        painter.fillPath(background, base)

        border = QColor(tokens.border)
        painter.setPen(QPen(border, 1.0))
        painter.drawLine(0, 0, 0, self.height())
        if self._direction < 0:
            painter.drawLine(0, 0, self.width(), 0)

        arrow = accent if self.underMouse() else QColor(tokens.text)
        if not self.isEnabled():
            arrow = QColor(tokens.disabled)
        center_x = self.width() / 2.0
        center_y = self.height() / 2.0
        half_width = 3.2
        half_height = 1.8
        path = QPainterPath()
        if self._direction > 0:
            path.moveTo(QPointF(center_x - half_width, center_y + half_height))
            path.lineTo(QPointF(center_x, center_y - half_height))
            path.lineTo(QPointF(center_x + half_width, center_y + half_height))
        else:
            path.moveTo(QPointF(center_x - half_width, center_y - half_height))
            path.lineTo(QPointF(center_x, center_y + half_height))
            path.lineTo(QPointF(center_x + half_width, center_y - half_height))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                arrow,
                1.6,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawPath(path)
        painter.end()


class _ModernSpinMixin:
    _step_width = 24

    def _init_modern_spin(self) -> None:
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setProperty("modernSpinBox", True)

        self._step_strip = QWidget(self)
        self._step_strip.setObjectName("modernSpinStepStrip")
        self._step_strip.setProperty("layoutRole", "transparent")

        self._step_up_button = _ChevronStepButton(1, self._step_strip)
        self._step_down_button = _ChevronStepButton(-1, self._step_strip)
        self._step_up_button.clicked.connect(self.stepUp)
        self._step_down_button.clicked.connect(self.stepDown)
        self._sync_step_geometry()

    def _sync_step_geometry(self) -> None:
        inset = 2
        self._step_strip.setGeometry(
            max(inset, self.width() - self._step_width - inset),
            inset,
            self._step_width,
            max(2, self.height() - inset * 2),
        )
        half_height = self._step_strip.height() // 2
        self._step_up_button.setGeometry(
            0,
            0,
            self._step_strip.width(),
            half_height,
        )
        self._step_down_button.setGeometry(
            0,
            half_height,
            self._step_strip.width(),
            self._step_strip.height() - half_height,
        )
        self._step_strip.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_step_geometry()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if not hasattr(self, "_step_up_button"):
            return
        if event.type() in {
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.EnabledChange,
            QEvent.Type.PaletteChange,
            QEvent.Type.StyleChange,
        }:
            self._step_up_button.setEnabled(self.isEnabled())
            self._step_down_button.setEnabled(self.isEnabled())
            self._step_up_button.update()
            self._step_down_button.update()


class ModernSpinBox(_ModernSpinMixin, QSpinBox):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._init_modern_spin()


class ModernDoubleSpinBox(_ModernSpinMixin, QDoubleSpinBox):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._init_modern_spin()
