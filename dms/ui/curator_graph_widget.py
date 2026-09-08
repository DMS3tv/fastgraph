from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRect, pyqtProperty
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QWidget

from dms.curator.export_image import aligned_bounds
from dms.curator.models import CurveData, GraphState, LayerState, PreferenceBounds
from dms.curator.transforms import visible_display_layers
from dms.graph_display import (
    retro_step_group,
    retro_step_series,
    stipple_trace_pen,
    uses_retro_steps,
)
from dms.theme import (
    ensure_graph_color,
    brand_theme_colors,
    normalize_theme,
    theme_colors,
)
from dms.ui.style_tokens import tokens_for
from dms.ui.theme_surface import aperiodic_dither_band_item


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
        self._theme = "dark"
        self._brand_mode = False
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

    def apply_theme(self, theme: str, brand_mode: bool = False) -> None:
        self._theme = normalize_theme(theme)
        self._brand_mode = bool(brand_mode)
        colors = brand_theme_colors() if brand_mode else theme_colors(theme)
        for name in ("bottom", "left"):
            axis = self.getAxis(name)
            axis.setPen(pg.mkPen(colors["plot_fg"]))
            axis.setTextPen(pg.mkPen(colors["plot_fg"]))
        self._render()

    def _uses_retro_steps(self) -> bool:
        return uses_retro_steps(self._theme, brand_mode=self._brand_mode)

    def _display_color(self, color: object) -> QColor:
        background = self._state.background if self._state is not None else "#1a1a1a"
        return ensure_graph_color(color, background)

    def _legend_chip_colors(self) -> tuple[QColor, QColor]:
        """Legend chip fill and border taken from the active theme, not hard-coded."""
        tokens = tokens_for(self._theme, brand_mode=self._brand_mode)
        fill = QColor(tokens.panel)
        fill.setAlpha(224)
        border = QColor(tokens.border)
        border.setAlpha(190)
        return fill, border

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
        for layer, curve in visible_display_layers(
            self._state.layers,
            self._state.smoothing_fraction,
            self._state.variation_combination,
        ):
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
        trace_indexes = {layer.id: index for index, layer in enumerate(state.layers)}
        for fallback_index, snapshot in enumerate(self._exiting_layers):
            self._draw_curve(
                snapshot.curve,
                snapshot.layer.color,
                1.0 - self._wipe_progress,
                trace_indexes.get(snapshot.layer.id, fallback_index),
            )
        visible_layers = visible_display_layers(
            state.layers, state.smoothing_fraction, state.variation_combination
        )
        for fallback_index, (layer, curve) in enumerate(visible_layers):
            progress = self._wipe_progress if layer.id in self._entering_layer_ids else 1.0
            self._draw_curve(
                curve,
                layer.color,
                progress,
                trace_indexes.get(layer.id, fallback_index),
            )
        if state.show_layer_names:
            self._draw_legend(visible_layers)

    def _draw_legend(self, layers: list[tuple[LayerState, CurveData]]) -> None:
        if not layers:
            return
        shown = layers[:16]
        state = self._state
        if state is None:
            return
        y_step = max(1.0, (state.y_max - state.y_min) * 0.055)
        chip_fill, chip_border = self._legend_chip_colors()
        for index, (layer, _curve) in enumerate(shown):
            column = index // 8
            row = index % 8
            item = pg.TextItem(
                text=f"━ {layer.name}",
                color=self._display_color(layer.color),
                fill=pg.mkBrush(chip_fill),
                border=pg.mkPen(chip_border),
                anchor=(1, 0),
            )
            item.setPos(
                np.log10(18500.0 if column == 0 else 3500.0),
                state.y_max - row * y_step,
            )
            self.addItem(item)
            self._items.append(item)
        if len(layers) > len(shown):
            tokens = tokens_for(self._theme, brand_mode=self._brand_mode)
            overflow = pg.TextItem(
                text=f"+{len(layers) - len(shown)} more",
                color=self._display_color(tokens.muted),
                fill=pg.mkBrush(chip_fill),
                border=pg.mkPen(chip_border),
                anchor=(1, 0),
            )
            overflow.setPos(np.log10(3500.0), state.y_max - 8 * y_step)
            self.addItem(overflow)
            self._items.append(overflow)

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
        bounds_freqs, upper_values, lower_values = aligned_bounds(upper, lower)
        freqs, upper_mag, lower_mag = _trim_series_group(
            bounds_freqs,
            (upper_values, lower_values),
            progress,
        )
        if len(freqs) < 2:
            return
        if self._uses_retro_steps():
            freqs, upper_mag, lower_mag = retro_step_group(freqs, (upper_mag, lower_mag))
        tokens = tokens_for(self._theme, brand_mode=self._brand_mode)
        if tokens.dither_chrome:
            fill = aperiodic_dither_band_item(
                np.log10(freqs),
                upper_mag,
                lower_mag,
                foreground=QColor(tokens.plot_grid),
                sample_width=max(64, min(1024, self.viewport().width())),
                sample_height=max(48, min(512, self.viewport().height())),
            )
            if fill is not None:
                self.addItem(fill)
                self._items.append(fill)
            edge_pen = pg.mkPen(QColor(tokens.muted), width=1)
            upper_item = self.plot(
                freqs, upper_mag, pen=edge_pen, antialias=False
            )
            lower_item = self.plot(
                freqs, lower_mag, pen=edge_pen, antialias=False
            )
            self._items.extend([upper_item, lower_item])
            return
        bounds_color = self._display_color("#969696")
        bounds_pen = QColor(bounds_color)
        bounds_pen.setAlpha(185)
        bounds_fill = QColor(bounds_color)
        bounds_fill.setAlpha(102)
        upper_item = self.plot(
            freqs, upper_mag, pen=pg.mkPen(bounds_pen, width=1.5), antialias=True
        )
        lower_item = self.plot(
            freqs, lower_mag, pen=pg.mkPen(bounds_pen, width=1.5), antialias=True
        )
        fill = pg.FillBetweenItem(upper_item, lower_item, brush=pg.mkBrush(bounds_fill))
        self.addItem(fill)
        self._items.extend([upper_item, lower_item, fill])

    def _draw_curve(
        self,
        curve: CurveData,
        color: str,
        progress: float,
        trace_index: int,
    ) -> None:
        trace_tokens = tokens_for(self._theme, brand_mode=self._brand_mode)
        if curve.kind == "fr" and curve.mag_db is not None:
            freqs, mag = _trim_series(curve.freqs, curve.mag_db, progress)
            if len(freqs) < 2:
                return
            if self._uses_retro_steps():
                freqs, mag = retro_step_series(freqs, mag)
            self._items.append(
                self.plot(
                    freqs,
                    mag,
                    pen=stipple_trace_pen(
                        pg.mkPen(self._display_color(color), width=2.0),
                        trace_index,
                        trace_tokens,
                    ),
                    antialias=True,
                )
            )
            return
        if curve.kind != "variation":
            return
        if not _has_variation(curve):
            return
        qcolor = self._display_color(color)
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
        if self._uses_retro_steps():
            freqs, p10, p25, median_values, p75, p90 = retro_step_group(
                freqs, (p10, p25, median_values, p75, p90)
            )
        antialias = True
        upper90 = self.plot(freqs, p90, pen=pg.mkPen((0, 0, 0, 0)), antialias=antialias)
        lower10 = self.plot(freqs, p10, pen=pg.mkPen((0, 0, 0, 0)), antialias=antialias)
        fill90 = pg.FillBetweenItem(upper90, lower10, brush=pg.mkBrush(outer))
        self.addItem(fill90)
        upper75 = self.plot(freqs, p75, pen=pg.mkPen((0, 0, 0, 0)), antialias=antialias)
        lower25 = self.plot(freqs, p25, pen=pg.mkPen((0, 0, 0, 0)), antialias=antialias)
        fill75 = pg.FillBetweenItem(upper75, lower25, brush=pg.mkBrush(inner))
        self.addItem(fill75)
        median = self.plot(
            freqs,
            median_values,
            pen=pg.mkPen(qcolor, width=2.2),
            antialias=antialias,
        )
        self._items.extend([upper90, lower10, fill90, upper75, lower25, fill75, median])


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
