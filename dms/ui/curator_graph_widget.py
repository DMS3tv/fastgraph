from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRect, pyqtProperty
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QWidget

from dms.curator.models import CurveData, GraphState, LayerState, PreferenceBounds
from dms.curator.transforms import visible_display_layers
from dms.theme import theme_colors


FREQ_MIN = 20.0
FREQ_MAX = 20000.0
X_RANGE_LEFT_MARGIN = 0.006
X_RANGE_RIGHT_MARGIN = 0.035
Y_RANGE_MARGIN_DB = 0.5
DATA_WIPE_DURATION_MS = 185
FREQUENCY_TICKS = [
    (20, "20"),
    (50, "50"),
    (100, "100"),
    (200, "200"),
    (500, "500"),
    (1000, "1k"),
    (2000, "2k"),
    (3000, "3k"),
    (5000, "5k"),
    (8000, "8k"),
    (10000, "10k"),
    (20000, "20k"),
]
FREQUENCY_MARKERS = {
    1000: (145, 152, 168, 130, 1.8),
    3000: (145, 152, 168, 85, 1.15),
    10000: (145, 152, 168, 125, 1.7),
}


@dataclass
class LayerSnapshot:
    layer: LayerState
    curve: CurveData


@dataclass
class BoundsSnapshot:
    bounds: PreferenceBounds


class AspectRatioWidget(QWidget):
    def __init__(self, child: QWidget, ratio: float = 16.0 / 9.0) -> None:
        super().__init__()
        self.child = child
        self.ratio = float(ratio)
        child.setParent(self)

    def resizeEvent(self, event) -> None:
        width = self.width()
        height = self.height()
        target_width = width
        target_height = int(round(target_width / self.ratio))
        if target_height > height:
            target_height = height
            target_width = int(round(target_height * self.ratio))
        left = (width - target_width) // 2
        top = (height - target_height) // 2
        self.child.setGeometry(QRect(left, top, target_width, target_height))
        super().resizeEvent(event)


class LockedPlotWidget(pg.PlotWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMouseEnabled(x=False, y=False)
        self.getPlotItem().getViewBox().setMouseEnabled(x=False, y=False)
        self.getPlotItem().getViewBox().setAspectLocked(lock=True, ratio=25.0)
        self.setMenuEnabled(False)

    def set_25db_aspect_locked(self, locked: bool) -> None:
        self.getPlotItem().getViewBox().setAspectLocked(lock=locked, ratio=25.0)

    def wheelEvent(self, event) -> None:
        event.ignore()

    def mousePressEvent(self, event) -> None:
        event.ignore()

    def mouseMoveEvent(self, event) -> None:
        event.ignore()

    def mouseReleaseEvent(self, event) -> None:
        event.ignore()


class GraphWidget(LockedPlotWidget):
    def __init__(self) -> None:
        super().__init__()
        self._items: list[object] = []
        self._state: GraphState | None = None
        self._wipe_progress = 1.0
        self._entering_layer_ids: set[str] = set()
        self._entering_bounds = False
        self._exiting_layers: list[LayerSnapshot] = []
        self._exiting_bounds: BoundsSnapshot | None = None
        self._wipe_animation = QPropertyAnimation(self, b"wipeProgress", self)
        self._wipe_animation.setDuration(DATA_WIPE_DURATION_MS)
        self._wipe_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._wipe_animation.finished.connect(self._finish_wipe)
        self.setLogMode(x=True, y=False)
        self.showGrid(x=True, y=True, alpha=0.18)
        self.getAxis("bottom").setLabel("Frequency", units="Hz")
        self.getAxis("left").setLabel("Magnitude", units="dB")
        self.getAxis("bottom").setTicks(
            [[(np.log10(freq), label) for freq, label in FREQUENCY_TICKS]]
        )
        self._apply_x_range()

    def redraw(self, state: GraphState) -> None:
        self._state = state
        self._render()

    def apply_theme(self, theme: str) -> None:
        colors = theme_colors(theme)
        for name in ("bottom", "left"):
            axis = self.getAxis(name)
            axis.setPen(pg.mkPen(colors["plot_fg"]))
            axis.setTextPen(pg.mkPen(colors["plot_fg"]))
        self._render()

    @pyqtProperty(float)
    def wipeProgress(self) -> float:
        return self._wipe_progress

    @wipeProgress.setter
    def wipeProgress(self, value: float) -> None:
        self._wipe_progress = max(0.0, min(1.0, float(value)))
        if self._state is not None:
            self._render()

    def start_data_wipe(
        self,
        *,
        entering_layer_ids: set[str] | None = None,
        entering_bounds: bool = False,
        exiting_layers: list[LayerSnapshot] | None = None,
        exiting_bounds: BoundsSnapshot | None = None,
    ) -> None:
        self._wipe_animation.stop()
        self._entering_layer_ids = set(entering_layer_ids or set())
        self._entering_bounds = entering_bounds
        self._exiting_layers = list(exiting_layers or [])
        self._exiting_bounds = exiting_bounds
        self.wipeProgress = 0.0
        self._wipe_animation.setStartValue(0.0)
        self._wipe_animation.setEndValue(1.0)
        self._wipe_animation.start()

    def snapshot_visible_layer(self, layer_id: str) -> LayerSnapshot | None:
        if self._state is None:
            return None
        for layer, curve in visible_display_layers(self._state.layers):
            if layer.id == layer_id:
                return LayerSnapshot(layer=layer, curve=curve)
        return None

    def snapshot_bounds(self) -> BoundsSnapshot | None:
        if self._state is None or not self._state.bounds.enabled:
            return None
        return BoundsSnapshot(bounds=replace(self._state.bounds, enabled=True))

    def _finish_wipe(self) -> None:
        self._entering_layer_ids.clear()
        self._entering_bounds = False
        self._exiting_layers.clear()
        self._exiting_bounds = None
        self.wipeProgress = 1.0

    def _render(self) -> None:
        if self._state is None:
            return
        state = self._state
        self.clear()
        self._items.clear()
        self.setBackground(state.background)
        self.set_25db_aspect_locked(state.aspect_locked_25db)
        self.setYRange(state.y_min - Y_RANGE_MARGIN_DB, state.y_max + Y_RANGE_MARGIN_DB, padding=0)
        self._apply_x_range()
        self._draw_frequency_markers()
        if self._exiting_bounds is not None:
            self._draw_bounds_data(self._exiting_bounds.bounds, 1.0 - self._wipe_progress)
        bounds_progress = self._wipe_progress if self._entering_bounds else 1.0
        self._draw_bounds(state, bounds_progress)
        for snapshot in self._exiting_layers:
            self._draw_curve(snapshot.curve, snapshot.layer.color, 1.0 - self._wipe_progress)
        for layer, curve in visible_display_layers(state.layers):
            progress = self._wipe_progress if layer.id in self._entering_layer_ids else 1.0
            self._draw_curve(curve, layer.color, progress)

    def _apply_x_range(self) -> None:
        span = np.log10(FREQ_MAX) - np.log10(FREQ_MIN)
        self.setXRange(
            np.log10(FREQ_MIN) - span * X_RANGE_LEFT_MARGIN,
            np.log10(FREQ_MAX) + span * X_RANGE_RIGHT_MARGIN,
            padding=0,
        )

    def _draw_frequency_markers(self) -> None:
        for freq, pen_args in FREQUENCY_MARKERS.items():
            line = pg.InfiniteLine(
                pos=np.log10(freq),
                angle=90,
                movable=False,
                pen=pg.mkPen(pen_args[:4], width=pen_args[4]),
            )
            self.addItem(line)
            self._items.append(line)

    def _draw_bounds(self, state: GraphState, progress: float) -> None:
        self._draw_bounds_data(state.bounds, progress)

    def _draw_bounds_data(self, bounds: PreferenceBounds, progress: float) -> None:
        if not bounds.enabled or bounds.upper is None or bounds.lower is None:
            return
        upper = bounds.upper
        lower = bounds.lower
        if upper.mag_db is None or lower.mag_db is None:
            return
        freqs, upper_mag, lower_mag = _trim_series_group(
            upper.freqs,
            (upper.mag_db, lower.mag_db),
            progress,
        )
        if len(freqs) < 2:
            return
        upper_item = self.plot(freqs, upper_mag, pen=pg.mkPen((150, 150, 150, 185), width=1.5))
        lower_item = self.plot(freqs, lower_mag, pen=pg.mkPen((150, 150, 150, 185), width=1.5))
        fill = pg.FillBetweenItem(upper_item, lower_item, brush=pg.mkBrush(150, 150, 150, 102))
        self.addItem(fill)
        self._items.extend([upper_item, lower_item, fill])

    def _draw_curve(self, curve: CurveData, color: str, progress: float) -> None:
        if curve.kind == "fr" and curve.mag_db is not None:
            freqs, mag = _trim_series(curve.freqs, curve.mag_db, progress)
            if len(freqs) < 2:
                return
            glow = QColor(color)
            glow.setAlpha(58)
            self._items.append(self.plot(freqs, mag, pen=pg.mkPen(glow, width=7.0)))
            self._items.append(self.plot(freqs, mag, pen=pg.mkPen(color, width=2.0)))
            return
        if curve.kind != "variation":
            return
        if not _has_variation(curve):
            return
        qcolor = QColor(color)
        outer = QColor(qcolor)
        outer.setAlpha(55)
        inner = QColor(qcolor)
        inner.setAlpha(95)
        assert curve.p10_db is not None and curve.p25_db is not None
        assert curve.p75_db is not None and curve.p90_db is not None and curve.median_db is not None
        freqs, p10, p25, median_values, p75, p90 = _trim_series_group(
            curve.freqs,
            (curve.p10_db, curve.p25_db, curve.median_db, curve.p75_db, curve.p90_db),
            progress,
        )
        if len(freqs) < 2:
            return
        upper90 = self.plot(freqs, p90, pen=pg.mkPen((0, 0, 0, 0)))
        lower10 = self.plot(freqs, p10, pen=pg.mkPen((0, 0, 0, 0)))
        fill90 = pg.FillBetweenItem(upper90, lower10, brush=pg.mkBrush(outer))
        self.addItem(fill90)
        upper75 = self.plot(freqs, p75, pen=pg.mkPen((0, 0, 0, 0)))
        lower25 = self.plot(freqs, p25, pen=pg.mkPen((0, 0, 0, 0)))
        fill75 = pg.FillBetweenItem(upper75, lower25, brush=pg.mkBrush(inner))
        self.addItem(fill75)
        glow = QColor(qcolor)
        glow.setAlpha(58)
        median_glow = self.plot(freqs, median_values, pen=pg.mkPen(glow, width=7.0))
        median = self.plot(freqs, median_values, pen=pg.mkPen(qcolor, width=2.2))
        self._items.extend([upper90, lower10, fill90, upper75, lower25, fill75, median_glow, median])


def _has_variation(curve: CurveData) -> bool:
    return all(
        value is not None
        for value in (curve.p10_db, curve.p25_db, curve.median_db, curve.p75_db, curve.p90_db)
    )


def _trim_series(freqs: np.ndarray, values: np.ndarray, progress: float) -> tuple[np.ndarray, np.ndarray]:
    trimmed = _trim_series_group(freqs, (values,), progress)
    return trimmed[0], trimmed[1]


def _trim_series_group(
    freqs: np.ndarray,
    value_groups: tuple[np.ndarray, ...],
    progress: float,
) -> tuple[np.ndarray, ...]:
    if progress >= 0.999:
        return (freqs, *value_groups)
    if len(freqs) == 0:
        return (freqs, *value_groups)
    log_min = np.log10(FREQ_MIN)
    log_max = np.log10(FREQ_MAX)
    cutoff = 10 ** (log_min + (log_max - log_min) * max(0.0, min(1.0, progress)))
    mask = freqs <= cutoff
    visible_count = int(np.count_nonzero(mask))
    if visible_count == 0:
        return (freqs[:0], *(values[:0] for values in value_groups))

    out_freqs = freqs[:visible_count]
    out_values = [values[:visible_count] for values in value_groups]
    if visible_count < len(freqs) and freqs[visible_count - 1] < cutoff:
        next_freq = freqs[visible_count]
        prev_freq = freqs[visible_count - 1]
        if next_freq > prev_freq:
            out_freqs = np.append(out_freqs, cutoff)
            fraction = (cutoff - prev_freq) / (next_freq - prev_freq)
            out_values = [
                np.append(values, values[-1] + (source[visible_count] - values[-1]) * fraction)
                for values, source in zip(out_values, value_groups)
            ]
    return (out_freqs, *out_values)
