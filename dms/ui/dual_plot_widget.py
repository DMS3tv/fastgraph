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
from PyQt6.QtCore import QRect, QTimer
from PyQt6.QtWidgets import QFileDialog, QMenu, QWidget, QVBoxLayout
from PyQt6.QtCore import Qt
import pyqtgraph.exporters


pg.setConfigOption("background", "#1a1a1a")
pg.setConfigOption("foreground", "#888888")
pg.setConfigOption("antialias", True)


_GREY = (130, 130, 130, 150)
_TEAL = (80, 180, 170, 220)
_GOLD = "#FCBE11"
_BAND_OUTER = (110, 160, 220, 55)
_BAND_INNER = (130, 185, 255, 90)
_BAND_MEDIAN = (170, 215, 255, 220)

_FREQ_MIN = 20.0
_FREQ_MAX = 20000.0
_Y_WINDOW_DB = 30.0
_Y_DEFAULT_TOP_DB = 15.0
_Y_TOP_HEADROOM_DB = 1.0


class _NoWheelPlotWidget(pg.PlotWidget):
    def wheelEvent(self, event) -> None:
        event.ignore()


def _make_plot_widget(title: str) -> pg.PlotWidget:
    pw = _NoWheelPlotWidget(title=title)
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
    # Lock to 25 dB per decade (1 decade on x equals 25 dB on y).
    pw.getPlotItem().getViewBox().setAspectLocked(lock=True, ratio=25.0)
    return pw


class DualPlotWidget(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._kept_curves: list[tuple[np.ndarray, np.ndarray]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._top_plot = _make_plot_widget("All Measurements (Top)")
        self._bot_plot = _make_plot_widget("Averaged Result (1/48 Oct RMS)")
        self._setup_plot_context_menu(self._top_plot, "top_plot")
        self._setup_plot_context_menu(self._bot_plot, "bottom_plot")

        layout.addWidget(self._top_plot, 1)
        layout.addWidget(self._bot_plot, 1)

        self._top_items: list[pg.PlotDataItem] = []
        self._bot_item: Optional[pg.PlotDataItem] = None
        self._bot_extra_items: list[object] = []
        self._reveal_item: Optional[pg.PlotDataItem] = None
        self._reveal_curve: Optional[tuple[np.ndarray, np.ndarray]] = None
        self._reveal_progress = 0.0
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setInterval(10)
        self._reveal_timer.timeout.connect(self._tick_reveal_animation)

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
        variation: Optional[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = None,
        bottom_mode: str = "average",
        animate_last: bool = False,
    ) -> None:
        """Redraw both viewports. Call from main thread only."""
        self._reveal_timer.stop()
        self._reveal_item = None
        self._reveal_curve = None
        self._kept_curves = kept
        self._redraw_top(kept)
        self._redraw_bottom(average=average, variation=variation, mode=bottom_mode)
        if animate_last and kept and self._top_items:
            self._start_last_curve_reveal(kept[-1], self._top_items[-1])

    def clear_all(self) -> None:
        self._reveal_timer.stop()
        self._reveal_item = None
        self._reveal_curve = None
        self._kept_curves = []
        for item in self._top_items:
            self._top_plot.removeItem(item)
        self._top_items.clear()
        if self._bot_item:
            self._bot_plot.removeItem(self._bot_item)
            self._bot_item = None
        for item in self._bot_extra_items:
            self._bot_plot.removeItem(item)
        self._bot_extra_items.clear()

    def bottom_plot_global_rect(self) -> QRect:
        top_left = self._bot_plot.mapToGlobal(self._bot_plot.rect().topLeft())
        return QRect(top_left, self._bot_plot.size())

    def export_bottom_plot_image(self, output_path: str) -> bool:
        pixmap = self._bot_plot.grab()
        return pixmap.save(output_path, "PNG")

    def bottom_plot_pixmap(self):
        return self._bot_plot.grab()

    def _setup_plot_context_menu(self, pw: pg.PlotWidget, default_name: str) -> None:
        # Hide pyqtgraph's large default context menu and replace with export-only options.
        pw.setMenuEnabled(False)
        pw.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        pw.customContextMenuRequested.connect(
            lambda pos, target=pw, name=default_name: self._show_export_menu(target, pos, name)
        )

    def _show_export_menu(self, pw: pg.PlotWidget, pos, default_name: str) -> None:
        menu = QMenu(self)
        export_png = menu.addAction("Export PNG...")
        export_svg = menu.addAction("Export SVG...")
        choice = menu.exec(pw.mapToGlobal(pos))
        if choice is None:
            return

        if choice == export_png:
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Export Plot PNG",
                f"{default_name}.png",
                "PNG Image (*.png)",
            )
            if path:
                exporter = pyqtgraph.exporters.ImageExporter(pw.plotItem)
                exporter.export(path)
            return

        if choice == export_svg:
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Export Plot SVG",
                f"{default_name}.svg",
                "SVG Vector Image (*.svg)",
            )
            if path:
                exporter = pyqtgraph.exporters.SVGExporter(pw.plotItem)
                exporter.export(path)

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

    def _clear_bottom_items(self) -> None:
        if self._bot_item:
            self._bot_plot.removeItem(self._bot_item)
            self._bot_item = None
        for item in self._bot_extra_items:
            self._bot_plot.removeItem(item)
        self._bot_extra_items.clear()

    def _redraw_bottom(
        self,
        average: Optional[tuple[np.ndarray, np.ndarray]],
        variation: Optional[
            tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]
        ],
        mode: str,
    ) -> None:
        self._clear_bottom_items()
        if mode == "variation":
            self._bot_plot.setTitle("Variation Band (Confidence Style)")
            self._draw_variation_bottom(variation)
            return

        self._bot_plot.setTitle("Averaged Result (1/48 Oct RMS)")
        if average is not None and len(average[0]) > 0:
            freqs, mag_db = average
            pen = pg.mkPen(color=_GOLD, width=2.0)
            self._bot_item = self._bot_plot.plot(freqs, mag_db, pen=pen)
            self._auto_center_y(self._bot_plot, [average])

    def _draw_variation_bottom(
        self,
        variation: Optional[
            tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]
        ],
    ) -> None:
        if variation is None:
            return
        freqs, p10, p25, p75, p90, median = variation
        if len(freqs) == 0:
            return

        upper90 = self._bot_plot.plot(freqs, p90, pen=pg.mkPen(color=(0, 0, 0, 0)))
        lower10 = self._bot_plot.plot(freqs, p10, pen=pg.mkPen(color=(0, 0, 0, 0)))
        fill90 = pg.FillBetweenItem(upper90, lower10, brush=pg.mkBrush(_BAND_OUTER))
        self._bot_plot.addItem(fill90)

        upper75 = self._bot_plot.plot(freqs, p75, pen=pg.mkPen(color=(0, 0, 0, 0)))
        lower25 = self._bot_plot.plot(freqs, p25, pen=pg.mkPen(color=(0, 0, 0, 0)))
        fill75 = pg.FillBetweenItem(upper75, lower25, brush=pg.mkBrush(_BAND_INNER))
        self._bot_plot.addItem(fill75)

        median_item = self._bot_plot.plot(
            freqs,
            median,
            pen=pg.mkPen(color=_BAND_MEDIAN, width=1.8),
        )
        self._bot_extra_items.extend([upper90, lower10, fill90, upper75, lower25, fill75, median_item])
        self._auto_center_y(self._bot_plot, [(freqs, p10), (freqs, p90)])

    def _auto_center_y(
        self,
        pw: pg.PlotWidget,
        curves: list[tuple[np.ndarray, np.ndarray]],
    ) -> None:
        if not curves:
            pw.setYRange(_Y_DEFAULT_TOP_DB - _Y_WINDOW_DB, _Y_DEFAULT_TOP_DB, padding=0)
            return
        all_db = np.concatenate([m for _, m in curves])
        if len(all_db) == 0:
            pw.setYRange(_Y_DEFAULT_TOP_DB - _Y_WINDOW_DB, _Y_DEFAULT_TOP_DB, padding=0)
            return

        # Default framing is -15..+15 dB around 0 dB.
        # If peaks exceed the top, shift the fixed 30 dB window upward.
        data_top = float(np.nanmax(all_db)) + _Y_TOP_HEADROOM_DB
        hi = max(_Y_DEFAULT_TOP_DB, data_top)
        lo = hi - _Y_WINDOW_DB
        pw.setYRange(lo, hi, padding=0)

    def _start_last_curve_reveal(
        self,
        curve: tuple[np.ndarray, np.ndarray],
        item: pg.PlotDataItem,
    ) -> None:
        self._reveal_curve = curve
        self._reveal_item = item
        self._reveal_progress = 0.08
        self._tick_reveal_animation()
        self._reveal_timer.start()

    def _tick_reveal_animation(self) -> None:
        if self._reveal_curve is None or self._reveal_item is None:
            self._reveal_timer.stop()
            return

        freqs, mag_db = self._reveal_curve
        n = len(freqs)
        if n <= 1:
            self._reveal_timer.stop()
            return

        k = max(2, min(n, int(n * self._reveal_progress)))
        self._reveal_item.setData(freqs[:k], mag_db[:k])

        self._reveal_progress += 0.14
        if self._reveal_progress >= 1.02:
            self._reveal_item.setData(freqs, mag_db)
            self._reveal_timer.stop()
