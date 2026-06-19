from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QPointF,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPalette
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QWidget


class ToggleSwitch(QCheckBox):
    def __init__(self, label: str = "", parent=None) -> None:
        super().__init__(label, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(28)
        self._offset = 0.0

        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self.toggled.connect(self._animate_to_state)
        self._offset = 1.0 if self.isChecked() else 0.0

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setHeight(max(30, hint.height()))
        return hint

    def hitButton(self, pos) -> bool:
        """Treat the full custom-painted control as clickable."""
        return self.rect().contains(pos)

    def _animate_to_state(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def get_offset(self) -> float:
        return self._offset

    def set_offset(self, value: float) -> None:
        self._offset = max(0.0, min(1.0, float(value)))
        self.update()

    offset = pyqtProperty(float, get_offset, set_offset)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        track_w = 42
        track_h = 22
        margin = 6
        text_gap = 10
        y = (self.height() - track_h) / 2.0

        track_rect = QRectF(margin, y, track_w, track_h)

        palette = self.palette()
        off_color = palette.color(QPalette.ColorRole.AlternateBase)
        on_color = QColor("#2f7f49")
        border_off = palette.color(QPalette.ColorRole.Mid)
        border_on = QColor("#47a164")

        bg = on_color if self.isChecked() else off_color
        border = border_on if self.isChecked() else border_off

        p.setPen(border)
        p.setBrush(bg)
        p.drawRoundedRect(track_rect, track_h / 2.0, track_h / 2.0)

        knob_d = 16
        knob_min_x = margin + 3
        knob_max_x = margin + track_w - knob_d - 3
        knob_x = knob_min_x + (knob_max_x - knob_min_x) * self._offset
        knob_y = y + (track_h - knob_d) / 2.0

        p.setPen(palette.color(QPalette.ColorRole.Mid))
        p.setBrush(palette.color(QPalette.ColorRole.Base))
        p.drawEllipse(QRectF(knob_x, knob_y, knob_d, knob_d))

        text_rect = QRectF(margin + track_w + text_gap, 0, self.width() - (margin + track_w + text_gap), self.height())
        text_role = QPalette.ColorRole.WindowText if self.isEnabled() else QPalette.ColorRole.PlaceholderText
        p.setPen(palette.color(text_role))
        p.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.text())

        p.end()


class _ThemeIcon(QWidget):
    def __init__(self, kind: str, parent=None) -> None:
        super().__init__(parent)
        self._kind = kind
        self._active = False
        self.setFixedSize(22, 22)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        inactive = self.palette().color(QPalette.ColorRole.Mid)
        color = QColor("#d69b00" if self._kind == "sun" else "#5977b8") if self._active else inactive
        painter.setPen(QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(color)
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        if self._kind == "sun":
            painter.drawEllipse(center, 3.7, 3.7)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for dx, dy in ((0, -8), (0, 8), (-8, 0), (8, 0), (-5.7, -5.7), (5.7, 5.7), (-5.7, 5.7), (5.7, -5.7)):
                length = 2.0
                scale = (dx * dx + dy * dy) ** 0.5
                painter.drawLine(
                    QPointF(center.x() + dx * (1.0 - length / scale), center.y() + dy * (1.0 - length / scale)),
                    QPointF(center.x() + dx, center.y() + dy),
                )
        else:
            outer = QPainterPath()
            outer.addEllipse(QRectF(4.0, 2.5, 14.0, 17.0))
            cutout = QPainterPath()
            cutout.addEllipse(QRectF(8.0, 0.8, 13.0, 15.5))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(outer.subtracted(cutout))
        painter.end()


class ThemeToggleWidget(QWidget):
    """Icon-only sun/light and moon/dark switch for the status bar."""

    toggled = pyqtSignal(bool)

    def __init__(self, dark: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(3)
        self._sun = _ThemeIcon("sun", self)
        self._switch = ToggleSwitch("", self)
        self._switch.setFixedWidth(54)
        self._moon = _ThemeIcon("moon", self)
        layout.addWidget(self._sun)
        layout.addWidget(self._switch)
        layout.addWidget(self._moon)
        self._switch.toggled.connect(self._on_toggled)
        self.set_dark(dark)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._switch.toggle()
            event.accept()
            return
        super().mousePressEvent(event)

    def is_dark(self) -> bool:
        return self._switch.isChecked()

    def set_dark(self, dark: bool) -> None:
        self._switch.blockSignals(True)
        self._switch.setChecked(bool(dark))
        self._switch.set_offset(1.0 if dark else 0.0)
        self._switch.blockSignals(False)
        self._sync_state()

    def _on_toggled(self, dark: bool) -> None:
        self._sync_state()
        self.toggled.emit(dark)

    def _sync_state(self) -> None:
        dark = self.is_dark()
        self._sun.set_active(not dark)
        self._moon.set_active(dark)
        current = "Dark" if dark else "Light"
        target = "Light" if dark else "Dark"
        self.setToolTip(f"{current} mode. Switch to {target} mode.")
        self.setAccessibleName(f"Theme: {current} mode")
