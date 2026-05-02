"""
Two stacked pyqtgraph viewports.

Top viewport:
  - All kept curves in grey
  - Most recent kept curve in desaturated teal

Bottom viewport:
  - RMS average of ALL kept curves in #FCBE11

"""

from typing import Optional
import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import Qt


pg.setConfigOption("background", "#1a1a1a")
pg.setConfigOption("foreground", "#888888")
pg.setConfigOption("antialias", True)


_GREY = (100, 100, 100, 120)
_TEAL = (80, 180, 170, 220)
_GOLD = "#FCBE11"

_FREQ_MIN = 20.0
_FREQ_MAX = 20000.0
_Y_PAD = 5.0  # dB padding above/below data
_MIN_Y_RANGE = 12.0


def _make_plot_widget(title: str) -> pg.PlotWidget:
    pw = pg.PlotWidget(title=title)
    ax = pw.getAxis("bottom")
    ax.setLabel("Frequency", units="Hz")
    pw.getAxis("left").setLabel("Magnitude", units="dB")
    pw.setLogMode(x=True, y=False)
    pw.showGrid(x=True, y=True, alpha=0.15)
    pw.setXRange(np.log10(_FREQ_MIN), np.log10(_FREQ_MAX), padding=0)
    # Frequency tick values for log axis
    ticks = [
        (np.log10(20), "20"),
        (np.log10(50), "50"),
        (np.log10(100), "100"),
        (np.log10(200), "200"),
        (np.log10(500), "500"),
        (np.log10(1000), "1k"),
        (np.log10(2000), "2k"),
        (np.log10(5000), "5k"),
        (np.log10(10000), "10k"),
        (np.log10(20000), "20k"),
    ]
    ax.setTicks([ticks])
    return pw


class DualPlotWidget(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._kept_curves: list[tuple[np.ndarray, np.ndarray]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._top_plot = _make_plot_widget("Measurements")
        self._bot_plot = _make_plot_widget("RMS Average (1/48 Oct)")

        layout.addWidget(self._top_plot, 1)
        layout.addWidget(self._bot_plot, 1)

        self._top_items: list[pg.PlotDataItem] = []
        self._bot_item: Optional[pg.PlotDataItem] = None

        # 1 kHz reference line (both plots)
        for pw in (self._top_plot, self._bot_plot):
            ref = pg.InfiniteLine(
                pos=np.log10(1000.0),
                angle=90,
                pen=pg.mkPen(color=(80, 80, 80), style=Qt.PenStyle.DashLine),
            )
            pw.addItem(ref)
            zero = pg.InfiniteLine(
                pos=0.0,
                angle=0,
                pen=pg.mkPen(color=(70, 70, 70), style=Qt.PenStyle.DashLine),
            )
            pw.addItem(zero)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_curves(
        self,
        kept: list[tuple[np.ndarray, np.ndarray]],
        average: Optional[tuple[np.ndarray, np.ndarray]],
    ) -> None:
        """Redraw both viewports. Call from main thread only."""
        self._kept_curves = kept
        self._redraw_top(kept)
        self._redraw_bottom(average)

    def clear_all(self) -> None:
        self._kept_curves = []
        for item in self._top_items:
            self._top_plot.removeItem(item)
        self._top_items.clear()
        if self._bot_item:
            self._bot_plot.removeItem(self._bot_item)
            self._bot_item = None

    def bottom_plot_global_rect(self) -> QRect:
        top_left = self._bot_plot.mapToGlobal(self._bot_plot.rect().topLeft())
        return QRect(top_left, self._bot_plot.size())

    # ------------------------------------------------------------------
    # Internal drawing
    # ------------------------------------------------------------------

    def _redraw_top(self, kept: list[tuple[np.ndarray, np.ndarray]]) -> None:
        # Remove all old items
        for item in self._top_items:
            self._top_plot.removeItem(item)
        self._top_items.clear()

        for i, (freqs, mag_db) in enumerate(kept):
            is_last = i == len(kept) - 1
            if is_last:
                pen = pg.mkPen(color=_TEAL, width=1.5)
            else:
                pen = pg.mkPen(color=_GREY, width=1.0)
            item = self._top_plot.plot(freqs, mag_db, pen=pen)
            self._top_items.append(item)

        self._auto_center_y(self._top_plot, kept)

    def _redraw_bottom(self, average: Optional[tuple[np.ndarray, np.ndarray]]) -> None:
        if self._bot_item:
            self._bot_plot.removeItem(self._bot_item)
            self._bot_item = None

        if average is not None and len(average[0]) > 0:
            freqs, mag_db = average
            pen = pg.mkPen(color=_GOLD, width=2.0)
            self._bot_item = self._bot_plot.plot(freqs, mag_db, pen=pen)
            self._auto_center_y(self._bot_plot, [average])

    def _auto_center_y(
        self,
        pw: pg.PlotWidget,
        curves: list[tuple[np.ndarray, np.ndarray]],
    ) -> None:
        if not curves:
            return
        all_db = np.concatenate([m for _, m in curves])
        if len(all_db) == 0:
            return
        lo = float(np.nanmin(all_db)) - _Y_PAD
        hi = float(np.nanmax(all_db)) + _Y_PAD
        if (hi - lo) < _MIN_Y_RANGE:
            mid = (lo + hi) / 2.0
            half = _MIN_Y_RANGE / 2.0
            lo = mid - half
            hi = mid + half
        pw.setYRange(lo, hi, padding=0)
