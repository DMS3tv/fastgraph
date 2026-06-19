from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter, QColor, QLinearGradient, QPalette
from PyQt6.QtCore import Qt, QRect, pyqtSlot


class LevelMeterWidget(QWidget):
    """
    RMS level bar meter.
    Range: -60 dBFS to 0 dBFS.
    Green → yellow → red color gradient.
    Peak hold indicator.
    """

    _FLOOR = -60.0
    _CLIP = 0.0
    _YELLOW_DB = -12.0
    _RED_DB = -3.0
    _PEAK_HOLD_FRAMES = 8

    def __init__(
        self,
        parent=None,
        orientation: Qt.Orientation = Qt.Orientation.Vertical,
    ) -> None:
        super().__init__(parent)
        self._orientation = orientation
        self._level_db: float = self._FLOOR
        self._peak_db: float = self._FLOOR
        self._peak_hold_count: int = 0
        if self._orientation == Qt.Orientation.Horizontal:
            self.setMinimumSize(180, 22)
            self.setMaximumHeight(28)
        else:
            self.setMinimumSize(24, 120)
            self.setMaximumWidth(30)

    @pyqtSlot(float)
    def set_level(self, db: float) -> None:
        self._level_db = max(self._FLOOR, min(self._CLIP, db))

        if self._level_db >= self._peak_db:
            self._peak_db = self._level_db
            self._peak_hold_count = self._PEAK_HOLD_FRAMES
        else:
            self._peak_hold_count -= 1
            if self._peak_hold_count <= 0:
                self._peak_db = max(self._FLOOR, self._peak_db - 1.5)

        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        padding = 3

        # Background
        palette = self.palette()
        painter.fillRect(0, 0, w, h, palette.color(QPalette.ColorRole.AlternateBase))

        bar_w = w - 2 * padding
        bar_h = h - 2 * padding
        bar_x = padding
        bar_y = padding

        def db_to_y(db: float) -> int:
            frac = (db - self._FLOOR) / (self._CLIP - self._FLOOR)
            frac = max(0.0, min(1.0, frac))
            return bar_y + int(bar_h * (1.0 - frac))

        def db_to_x(db: float) -> int:
            frac = (db - self._FLOOR) / (self._CLIP - self._FLOOR)
            frac = max(0.0, min(1.0, frac))
            return bar_x + int(bar_w * frac)

        # Gradient: green → yellow → red
        if self._orientation == Qt.Orientation.Horizontal:
            grad = QLinearGradient(bar_x, 0, bar_x + bar_w, 0)
        else:
            grad = QLinearGradient(0, bar_y + bar_h, 0, bar_y)
        grad.setColorAt(0.0, QColor("#22cc44"))
        grad.setColorAt(0.6, QColor("#cccc22"))
        grad.setColorAt(0.85, QColor("#cc4422"))
        grad.setColorAt(1.0, QColor("#ff1111"))

        if self._orientation == Qt.Orientation.Horizontal:
            fill_right = db_to_x(self._level_db)
            fill_rect = QRect(bar_x, bar_y, max(0, fill_right - bar_x), bar_h)
            painter.fillRect(fill_rect, grad)

            peak_x = db_to_x(self._peak_db)
            painter.setPen(palette.color(QPalette.ColorRole.WindowText))
            painter.drawLine(peak_x, bar_y, peak_x, bar_y + bar_h - 1)

            if self._level_db >= -0.5:
                painter.fillRect(bar_x + bar_w - 4, bar_y, 4, bar_h, QColor("#ff0000"))

            painter.setPen(palette.color(QPalette.ColorRole.Mid))
            for mark_db in [-48, -36, -24, -12, -3]:
                mx = db_to_x(float(mark_db))
                painter.drawLine(mx, bar_y, mx, bar_y + bar_h)
        else:
            fill_top = db_to_y(self._level_db)
            fill_rect = QRect(bar_x, fill_top, bar_w, bar_y + bar_h - fill_top)
            painter.fillRect(fill_rect, grad)

            peak_y = db_to_y(self._peak_db)
            painter.setPen(palette.color(QPalette.ColorRole.WindowText))
            painter.drawLine(bar_x, peak_y, bar_x + bar_w - 1, peak_y)

            if self._level_db >= -0.5:
                painter.fillRect(bar_x, bar_y, bar_w, 4, QColor("#ff0000"))

            painter.setPen(palette.color(QPalette.ColorRole.Mid))
            for mark_db in [-48, -36, -24, -12, -3]:
                my = db_to_y(float(mark_db))
                painter.drawLine(bar_x, my, bar_x + bar_w, my)

        painter.end()
