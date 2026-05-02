from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter, QColor, QLinearGradient
from PyQt6.QtCore import Qt, QRect, pyqtSlot


class LevelMeterWidget(QWidget):
    """
    Vertical RMS level bar meter.
    Range: -60 dBFS to 0 dBFS.
    Green → yellow → red color gradient.
    Peak hold indicator.
    """

    _FLOOR = -60.0
    _CLIP = 0.0
    _YELLOW_DB = -12.0
    _RED_DB = -3.0
    _PEAK_HOLD_FRAMES = 30

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._level_db: float = self._FLOOR
        self._peak_db: float = self._FLOOR
        self._peak_hold_count: int = 0
        self.setMinimumSize(24, 120)
        self.setMaximumWidth(36)

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
        painter.fillRect(0, 0, w, h, QColor("#111"))

        bar_w = w - 2 * padding
        bar_h = h - 2 * padding
        bar_x = padding
        bar_y = padding

        def db_to_y(db: float) -> int:
            frac = (db - self._FLOOR) / (self._CLIP - self._FLOOR)
            frac = max(0.0, min(1.0, frac))
            return bar_y + int(bar_h * (1.0 - frac))

        # Filled bar height
        fill_top = db_to_y(self._level_db)
        fill_rect = QRect(bar_x, fill_top, bar_w, bar_y + bar_h - fill_top)

        # Gradient: green → yellow → red (bottom to top)
        grad = QLinearGradient(0, bar_y + bar_h, 0, bar_y)
        grad.setColorAt(0.0, QColor("#22cc44"))
        grad.setColorAt(0.6, QColor("#cccc22"))
        grad.setColorAt(0.85, QColor("#cc4422"))
        grad.setColorAt(1.0, QColor("#ff1111"))

        painter.fillRect(fill_rect, grad)

        # Peak indicator line
        peak_y = db_to_y(self._peak_db)
        painter.setPen(QColor("#ffffff"))
        painter.drawLine(bar_x, peak_y, bar_x + bar_w - 1, peak_y)

        # Clip indicator at top
        if self._level_db >= -0.5:
            painter.fillRect(bar_x, bar_y, bar_w, 4, QColor("#ff0000"))

        # Scale marks at -12 dB and -3 dB
        painter.setPen(QColor("#555"))
        for mark_db in [-48, -36, -24, -12, -3]:
            my = db_to_y(float(mark_db))
            painter.drawLine(bar_x, my, bar_x + bar_w, my)

        painter.end()