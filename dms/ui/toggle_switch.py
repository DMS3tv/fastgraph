from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    pyqtProperty,
)
from PyQt6.QtGui import QColor, QPainter, QPalette, QPen
from PyQt6.QtWidgets import QApplication, QCheckBox

from dms.ui.style_tokens import mode_tokens


class ToggleSwitch(QCheckBox):
    _TRACK_WIDTH = 42
    _HORIZONTAL_MARGIN = 6
    _TEXT_GAP = 10

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
        text_width = self.fontMetrics().horizontalAdvance(self.text()) if self.text() else 0
        painted_width = (
            self._HORIZONTAL_MARGIN
            + self._TRACK_WIDTH
            + (self._TEXT_GAP + text_width if text_width else 0)
            + self._HORIZONTAL_MARGIN
        )
        hint.setWidth(max(painted_width, hint.width()))
        hint.setHeight(max(30, hint.height()))
        return hint

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, pos) -> bool:
        """Treat the full custom-painted control as clickable."""
        return self.rect().contains(pos)

    def _animate_to_state(self, checked: bool) -> None:
        app = QApplication.instance()
        mode = app.property("fastgraphVisualMode") if app is not None else "dark"
        if mode_tokens(mode).classic_controls:
            self._anim.stop()
            self.set_offset(1.0 if checked else 0.0)
            return
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
        app = QApplication.instance()
        mode = app.property("fastgraphVisualMode") if app is not None else "dark"
        tokens = mode_tokens(mode)
        if tokens.classic_controls:
            self._paint_fastgraph95(tokens)
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        track_w = self._TRACK_WIDTH
        track_h = 22
        margin = self._HORIZONTAL_MARGIN
        text_gap = self._TEXT_GAP
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

        text_rect = QRectF(
            margin + track_w + text_gap,
            0,
            self.width() - (margin + track_w + text_gap),
            self.height(),
        )
        text_role = (
            QPalette.ColorRole.WindowText
            if self.isEnabled()
            else QPalette.ColorRole.PlaceholderText
        )
        p.setPen(palette.color(text_role))
        p.drawText(
            text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.text()
        )

        p.end()

    def _paint_fastgraph95(self, tokens) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        track_w = self._TRACK_WIDTH
        track_h = 20
        margin = self._HORIZONTAL_MARGIN
        text_gap = self._TEXT_GAP
        y = int((self.height() - track_h) / 2.0)
        track = QRectF(margin, y, track_w, track_h).toRect()

        dark_variant = tokens.dark_bevel
        terminal_variant = tokens.terminal_chrome
        terminal_edge = tokens.accent if self.isChecked() else tokens.border
        painter.fillRect(track, QColor(tokens.raised if dark_variant else "#FFFFFF"))
        painter.setPen(QPen(QColor("#000000"), 1))
        painter.drawLine(track.left(), track.bottom(), track.left(), track.top())
        painter.drawLine(track.left(), track.top(), track.right(), track.top())
        painter.setPen(
            QPen(
                QColor(
                    terminal_edge
                    if terminal_variant
                    else ("#8F8F8F" if dark_variant else "#FFFFFF")
                ),
                1,
            )
        )
        painter.drawLine(track.left(), track.bottom(), track.right(), track.bottom())
        painter.drawLine(track.right(), track.top(), track.right(), track.bottom())

        inner = track.adjusted(2, 2, -2, -2)
        if self.isChecked():
            painter.fillRect(inner, QColor(tokens.selected))
        else:
            painter.fillRect(inner, QColor(tokens.panel))

        knob_width = 13
        knob_x = inner.left() if self._offset < 0.5 else inner.right() - knob_width + 1
        knob = QRectF(knob_x, inner.top(), knob_width, inner.height()).toRect()
        painter.fillRect(knob, QColor(tokens.control))
        painter.setPen(
            QPen(
                QColor(
                    terminal_edge
                    if terminal_variant
                    else ("#8F8F8F" if dark_variant else "#FFFFFF")
                ),
                1,
            )
        )
        painter.drawLine(knob.left(), knob.bottom(), knob.left(), knob.top())
        painter.drawLine(knob.left(), knob.top(), knob.right(), knob.top())
        painter.setPen(QPen(QColor("#000000"), 1))
        painter.drawLine(knob.left(), knob.bottom(), knob.right(), knob.bottom())
        painter.drawLine(knob.right(), knob.top(), knob.right(), knob.bottom())

        text_rect = QRectF(
            margin + track_w + text_gap,
            0,
            self.width() - (margin + track_w + text_gap),
            self.height(),
        )
        color = QColor(tokens.text if self.isEnabled() else tokens.disabled)
        painter.setPen(color)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self.text(),
        )
        painter.end()
