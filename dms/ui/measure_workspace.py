"""Single-channel and two-channel Measure plot workspace."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from dms.graph_display import retro_step_group, retro_step_series, stipple_trace_pen
from dms.theme import ensure_graph_color, brand_theme_colors, normalize_theme, theme_colors
from dms.ui.dual_plot_widget import DualPlotWidget, _configure_plot_widget
from dms.ui.rounded_viewport import RoundedViewportFrame
from dms.ui.style_tokens import tokens_for
from dms.ui.modern_spinbox import ModernDoubleSpinBox


Curve = tuple[np.ndarray, np.ndarray]
Variation = tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]
BLUE = "#3B82F6"
RED = "#EF4444"


class _PlotPane(QWidget):
    selected = pyqtSignal(str)

    def __init__(self, key: str, title: str, *, frequency_response: bool = True) -> None:
        super().__init__()
        self.key = key
        self.plot = pg.PlotWidget(title=title)
        if frequency_response:
            _configure_plot_widget(self.plot)
        else:
            self.plot.showGrid(x=True, y=True, alpha=0.15)
            self.plot.getAxis("bottom").setLabel("Time", units="ms")
            self.plot.getAxis("left").setLabel("Amplitude")
            self.plot.getAxis("left").enableAutoSIPrefix(False)
        self.plot.setMenuEnabled(False)
        self.frame = RoundedViewportFrame(self.plot)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.frame)
        self._items: list[object] = []
        self._selected = False
        self.plot.scene().sigMouseClicked.connect(lambda _event: self.selected.emit(self.key))

    def clear(self) -> None:
        for item in self._items:
            try:
                self.plot.removeItem(item)
            except Exception:
                pass
        self._items.clear()

    def set_selected(self, selected: bool, focus_color: str, border_color: str) -> None:
        self._selected = bool(selected)
        color = focus_color if selected else border_color
        width = 2 if selected else 1
        self.frame.setStyleSheet(f"border: {width}px solid {color};")

    def apply_theme(self, theme: str, brand_mode: bool) -> None:
        colors = brand_theme_colors() if brand_mode else theme_colors(theme)
        self.plot.setBackground(colors["plot_bg"])
        for axis_name in ("left", "bottom"):
            axis = self.plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(colors["plot_fg"]))
            axis.setTextPen(pg.mkPen(colors["plot_fg"]))
        self.plot.getPlotItem().titleLabel.setAttr("color", colors["plot_fg"])
        tokens = tokens_for(theme, brand_mode=brand_mode)
        self.set_selected(self._selected, tokens.focus, tokens.border)

    def draw_curves(
        self,
        curves: list[Curve],
        *,
        theme: str,
        brand_mode: bool,
        accent: str | None = None,
    ) -> None:
        self.clear()
        colors = brand_theme_colors() if brand_mode else theme_colors(theme)
        tokens = tokens_for(theme, brand_mode=brand_mode)
        for index, (freqs, values) in enumerate(curves):
            latest = index == len(curves) - 1
            if accent:
                base = accent
            elif tokens.trace_palette:
                base = tokens.trace_palette[index % len(tokens.trace_palette)]
            else:
                base = None
            if not base:
                base = "#50B4AA" if latest else "#828282"
            color = ensure_graph_color(base, colors["plot_bg"])
            color.setAlpha(235 if latest else 155)
            pen = pg.mkPen(color, width=1.7 if latest else 1.0)
            pen = stipple_trace_pen(pen, index, tokens)
            display_freqs, display_values = (
                retro_step_series(freqs, values) if tokens.retro_graph and not brand_mode else (freqs, values)
            )
            self._items.append(self.plot.plot(display_freqs, display_values, pen=pen))
        _auto_center(self.plot, curves)

    def draw_result(
        self,
        curve: Curve | None,
        variation: Variation | None,
        *,
        show_variation: bool,
        theme: str,
        brand_mode: bool,
        title: str,
        accent: str | None = None,
    ) -> None:
        self.clear()
        self.plot.setTitle(title)
        colors = brand_theme_colors() if brand_mode else theme_colors(theme)
        tokens = tokens_for(theme, brand_mode=brand_mode)
        base = accent or (tokens.trace_palette[0] if tokens.trace_palette else "#FCBE11")
        display_color = ensure_graph_color(base, colors["plot_bg"])
        if show_variation and variation is not None:
            freqs, p10, p25, p75, p90, median = variation
            if tokens.retro_graph and not brand_mode:
                freqs, p10, p25, p75, p90, median = retro_step_group(
                    freqs, (p10, p25, p75, p90, median)
                )
            outer = QColor(display_color)
            outer.setAlpha(50)
            inner = QColor(display_color)
            inner.setAlpha(88)
            u90 = self.plot.plot(freqs, p90, pen=pg.mkPen(None))
            l10 = self.plot.plot(freqs, p10, pen=pg.mkPen(None))
            u75 = self.plot.plot(freqs, p75, pen=pg.mkPen(None))
            l25 = self.plot.plot(freqs, p25, pen=pg.mkPen(None))
            fill90 = pg.FillBetweenItem(u90, l10, brush=pg.mkBrush(outer))
            fill75 = pg.FillBetweenItem(u75, l25, brush=pg.mkBrush(inner))
            self.plot.addItem(fill90)
            self.plot.addItem(fill75)
            median_item = self.plot.plot(freqs, median, pen=pg.mkPen(display_color, width=2.0))
            self._items.extend([u90, l10, u75, l25, fill90, fill75, median_item])
            _auto_center(self.plot, [(freqs, p10), (freqs, p90)])
        elif curve is not None:
            freqs, values = curve
            if tokens.retro_graph and not brand_mode:
                freqs, values = retro_step_series(freqs, values)
            pen = stipple_trace_pen(pg.mkPen(display_color, width=2.0), 0, tokens)
            self._items.append(self.plot.plot(freqs, values, pen=pen))
            _auto_center(self.plot, [curve])


class TwoChannelMeasureWidget(QWidget):
    selection_changed = pyqtSignal(str)
    balance_start_requested = pyqtSignal()
    balance_stop_requested = pyqtSignal()
    balance_parameters_changed = pyqtSignal(str, float, float)

    def __init__(self) -> None:
        super().__init__()
        self._theme = "dark"
        self._brand_mode = False
        self._selection = "channel_1"
        self._bottom_mode = "combined"
        self._scope_limit = 0.1
        self._header_widget: QWidget | None = None
        self._between_widget: QWidget | None = None
        self._footer_widget: QWidget | None = None

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(4)
        self._pages = QStackedWidget()
        self._root.addWidget(self._pages, 1)

        self._frequency_page = QWidget()
        freq_layout = QVBoxLayout(self._frequency_page)
        freq_layout.setContentsMargins(0, 0, 0, 0)
        freq_layout.setSpacing(4)
        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(4)
        self._top_2 = _PlotPane("channel_2", "Channel 2 — R")
        self._top_1 = _PlotPane("channel_1", "Channel 1 — L")
        top_layout.addWidget(self._top_2, 1)
        top_layout.addWidget(self._top_1, 1)
        freq_layout.addWidget(top, 1)
        self._between_host = QVBoxLayout()
        self._between_host.setContentsMargins(0, 0, 0, 0)
        freq_layout.addLayout(self._between_host)
        self._bottom_pages = QStackedWidget()
        self._combined = _PlotPane("combined", "Combined — BOTH")
        self._bottom_pages.addWidget(self._combined)
        separate = QWidget()
        separate_layout = QHBoxLayout(separate)
        separate_layout.setContentsMargins(0, 0, 0, 0)
        separate_layout.setSpacing(4)
        self._bottom_2 = _PlotPane("channel_2", "Channel 2 — R")
        self._bottom_1 = _PlotPane("channel_1", "Channel 1 — L")
        separate_layout.addWidget(self._bottom_2, 1)
        separate_layout.addWidget(self._bottom_1, 1)
        self._bottom_pages.addWidget(separate)
        freq_layout.addWidget(self._bottom_pages, 1)
        self._footer_host = QVBoxLayout()
        self._footer_host.setContentsMargins(0, 0, 0, 0)
        freq_layout.addLayout(self._footer_host)
        self._pages.addWidget(self._frequency_page)

        self._balance_page = QWidget()
        balance_layout = QVBoxLayout(self._balance_page)
        balance_layout.setContentsMargins(0, 0, 0, 0)
        balance_layout.setSpacing(6)
        scopes = QWidget()
        scope_layout = QHBoxLayout(scopes)
        scope_layout.setContentsMargins(0, 0, 0, 0)
        scope_layout.setSpacing(4)
        self._overlay_scope = _PlotPane("overlay", "Channel Balance — L / R", frequency_response=False)
        self._delta_scope = _PlotPane("delta", "Delta — L − R", frequency_response=False)
        scope_layout.addWidget(self._overlay_scope, 1)
        scope_layout.addWidget(self._delta_scope, 1)
        balance_layout.addWidget(scopes, 1)
        controls = QWidget()
        controls.setProperty("surfaceLevel", "raised")
        row = QHBoxLayout(controls)
        row.setContentsMargins(8, 6, 8, 6)
        self._balance_start = QPushButton("Start Generator")
        self._balance_stop = QPushButton("Stop")
        self._balance_stop.setEnabled(False)
        self._waveform = QComboBox()
        self._waveform.addItems(["Sine", "Square"])
        self._frequency = ModernDoubleSpinBox()
        self._frequency.setRange(20.0, 20000.0)
        self._frequency.setDecimals(1)
        self._frequency.setValue(500.0)
        self._frequency.setSuffix(" Hz")
        self._level = ModernDoubleSpinBox()
        self._level.setRange(-120.0, 0.0)
        self._level.setDecimals(1)
        self._level.setSuffix(" dBFS")
        self._level.setValue(-6.0)
        self._readout = QLabel("L — dBFS   R — dBFS   Δ — dB")
        self._readout.setProperty("tone", "muted")
        row.addWidget(self._balance_start)
        row.addWidget(self._balance_stop)
        row.addWidget(QLabel("Wave"))
        row.addWidget(self._waveform)
        row.addWidget(QLabel("Frequency"))
        row.addWidget(self._frequency)
        row.addWidget(QLabel("Output"))
        row.addWidget(self._level)
        row.addWidget(self._readout, 1)
        balance_layout.addWidget(controls)
        self._pages.addWidget(self._balance_page)

        for pane in (self._bottom_1, self._bottom_2):
            pane.selected.connect(self.set_selection)
        self._balance_start.clicked.connect(self.balance_start_requested)
        self._balance_stop.clicked.connect(self.balance_stop_requested)
        self._waveform.currentTextChanged.connect(self._emit_balance_parameters)
        self._frequency.valueChanged.connect(self._emit_balance_parameters)
        self._level.valueChanged.connect(self._emit_balance_parameters)
        self._refresh_selection()

    @property
    def selection(self) -> str:
        return self._selection if self._bottom_mode == "separate" else "combined"

    def set_header_widget(self, widget: QWidget) -> None:
        if self._header_widget is not None:
            self._root.removeWidget(self._header_widget)
        self._header_widget = widget
        self._root.insertWidget(0, widget)

    def set_between_plots_widget(self, widget: QWidget) -> None:
        if self._between_widget is not None:
            self._between_host.removeWidget(self._between_widget)
        self._between_widget = widget
        self._between_host.addWidget(widget)

    def set_footer_widget(self, widget: QWidget) -> None:
        if self._footer_widget is not None:
            self._footer_host.removeWidget(self._footer_widget)
        self._footer_widget = widget
        self._footer_host.addWidget(widget)

    def release_shared_widgets(self) -> None:
        if self._header_widget is not None:
            self._root.removeWidget(self._header_widget)
            self._header_widget = None
        if self._between_widget is not None:
            self._between_host.removeWidget(self._between_widget)
            self._between_widget = None
        if self._footer_widget is not None:
            self._footer_host.removeWidget(self._footer_widget)
            self._footer_widget = None

    def set_bottom_mode(self, mode: str) -> None:
        self._bottom_mode = "separate" if mode == "separate" else "combined"
        self._bottom_pages.setCurrentIndex(1 if self._bottom_mode == "separate" else 0)
        self._refresh_selection()

    def set_selection(self, selection: str) -> None:
        if selection not in {"channel_1", "channel_2"}:
            return
        self._selection = selection
        self._refresh_selection()
        self.selection_changed.emit(selection)

    def _refresh_selection(self) -> None:
        tokens = tokens_for(self._theme, brand_mode=self._brand_mode)
        self._bottom_1.set_selected(self._selection == "channel_1", tokens.focus, tokens.border)
        self._bottom_2.set_selected(self._selection == "channel_2", tokens.focus, tokens.border)

    def set_balance_mode(self, enabled: bool) -> None:
        self._pages.setCurrentIndex(1 if enabled else 0)

    def set_generator_level(self, value: float) -> None:
        self._level.blockSignals(True)
        self._level.setValue(float(value))
        self._level.blockSignals(False)

    def set_frequency_limit(self, value: float) -> None:
        self._frequency.setMaximum(float(value))

    def set_balance_running(self, running: bool) -> None:
        self._balance_start.setEnabled(not running)
        self._balance_stop.setEnabled(running)

    def _emit_balance_parameters(self, *_args) -> None:
        self.balance_parameters_changed.emit(
            self._waveform.currentText().lower(),
            float(self._frequency.value()),
            float(self._level.value()),
        )

    def update_frequency_response(
        self,
        *,
        top_channel_1: list[Curve],
        top_channel_2: list[Curve],
        averages: dict[str, Curve | None],
        variations: dict[str, Variation | None],
        show_variation: bool,
    ) -> None:
        self._top_1.draw_curves(top_channel_1, theme=self._theme, brand_mode=self._brand_mode)
        self._top_2.draw_curves(top_channel_2, theme=self._theme, brand_mode=self._brand_mode)
        self._combined.draw_result(
            averages.get("combined"), variations.get("combined"),
            show_variation=show_variation, theme=self._theme, brand_mode=self._brand_mode,
            title="Combined Variation — BOTH" if show_variation else "Combined Average — BOTH",
        )
        self._bottom_1.draw_result(
            averages.get("channel_1"), variations.get("channel_1"),
            show_variation=show_variation, theme=self._theme, brand_mode=self._brand_mode,
            title="Channel 1 Variation — L" if show_variation else "Channel 1 Average — L",
        )
        self._bottom_2.draw_result(
            averages.get("channel_2"), variations.get("channel_2"),
            show_variation=show_variation, theme=self._theme, brand_mode=self._brand_mode,
            title="Channel 2 Variation — R" if show_variation else "Channel 2 Average — R",
        )

    def update_scope(
        self,
        left: np.ndarray,
        right: np.ndarray,
        sample_rate: int,
        left_db: float,
        right_db: float,
        delta_db: float,
    ) -> None:
        self._overlay_scope.clear()
        self._delta_scope.clear()
        if len(left) == 0 or len(right) == 0:
            return
        count = min(len(left), len(right))
        left = left[-count:]
        right = right[-count:]
        times = 1000.0 * np.arange(count, dtype=float) / float(sample_rate)
        colors = brand_theme_colors() if self._brand_mode else theme_colors(self._theme)
        left_color = ensure_graph_color(BLUE, colors["plot_bg"])
        right_color = ensure_graph_color(RED, colors["plot_bg"])
        delta_color = ensure_graph_color(tokens_for(self._theme, brand_mode=self._brand_mode).accent, colors["plot_bg"])
        self._overlay_scope._items.extend([
            self._overlay_scope.plot.plot(times, left, pen=pg.mkPen(left_color, width=1.7)),
            self._overlay_scope.plot.plot(times, right, pen=pg.mkPen(right_color, width=1.7)),
        ])
        delta = left - right
        self._delta_scope._items.append(
            self._delta_scope.plot.plot(times, delta, pen=pg.mkPen(delta_color, width=1.7))
        )
        peak = max(float(np.max(np.abs(left))), float(np.max(np.abs(right))), float(np.max(np.abs(delta))), 1e-4)
        target_limit = min(1.2, peak * 1.15)
        smoothing = 0.45 if target_limit > self._scope_limit else 0.12
        self._scope_limit += smoothing * (target_limit - self._scope_limit)
        limit = max(1e-4, self._scope_limit)
        for pane in (self._overlay_scope, self._delta_scope):
            pane.plot.setXRange(float(times[0]), float(times[-1]), padding=0)
            pane.plot.setYRange(-limit, limit, padding=0)
        self._readout.setText(f"L {left_db:.1f} dBFS   R {right_db:.1f} dBFS   Δ {delta_db:+.1f} dB")

    def apply_theme(self, theme: str, brand_mode: bool = False) -> None:
        self._theme = normalize_theme(theme)
        self._brand_mode = bool(brand_mode)
        for pane in (
            self._top_1, self._top_2, self._combined, self._bottom_1, self._bottom_2,
            self._overlay_scope, self._delta_scope,
        ):
            pane.apply_theme(self._theme, self._brand_mode)
        self._refresh_selection()

    def bottom_plot_global_rect(self) -> QRect:
        pane = self._combined if self._bottom_mode == "combined" else (
            self._bottom_1 if self._selection == "channel_1" else self._bottom_2
        )
        top_left = pane.plot.mapToGlobal(pane.plot.rect().topLeft())
        return QRect(top_left, pane.plot.size())


class MeasureWorkspace(QWidget):
    measurement_files_dropped = pyqtSignal(list)
    selection_changed = pyqtSignal(str)
    balance_start_requested = pyqtSignal()
    balance_stop_requested = pyqtSignal()
    balance_parameters_changed = pyqtSignal(str, float, float)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        layout.addWidget(self._stack)
        self.single = DualPlotWidget()
        self.two = TwoChannelMeasureWidget()
        self._top_frame = self.single._top_frame
        self._bot_frame = self.single._bot_frame
        self._top_plot = self.single._top_plot
        self._bot_plot = self.single._bot_plot
        self._header_widget = None
        self._between_plots_widget = None
        self._footer_widget = None
        self._stack.addWidget(self.single)
        self._stack.addWidget(self.two)
        self.single.measurement_files_dropped.connect(self.measurement_files_dropped)
        self.two.selection_changed.connect(self.selection_changed)
        self.two.balance_start_requested.connect(self.balance_start_requested)
        self.two.balance_stop_requested.connect(self.balance_stop_requested)
        self.two.balance_parameters_changed.connect(self.balance_parameters_changed)
        self._header: QWidget | None = None
        self._between: QWidget | None = None
        self._footer: QWidget | None = None
        self._two_enabled = False

    def set_header_widget(self, widget: QWidget) -> None:
        self._header = widget
        self._header_widget = widget
        self._active().set_header_widget(widget)

    def set_between_plots_widget(self, widget: QWidget) -> None:
        self._between = widget
        self._between_plots_widget = widget
        self._active().set_between_plots_widget(widget)

    def set_footer_widget(self, widget: QWidget) -> None:
        self._footer = widget
        self._footer_widget = widget
        self._active().set_footer_widget(widget)

    def layout(self):
        if not getattr(self, "_two_enabled", False):
            return self.single.layout()
        return QWidget.layout(self)

    def _active(self):
        return self.two if self._two_enabled else self.single

    def set_two_channel_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._two_enabled:
            return
        self._active().release_shared_widgets()
        self._two_enabled = enabled
        self._stack.setCurrentWidget(self.two if enabled else self.single)
        active = self._active()
        if self._header is not None:
            active.set_header_widget(self._header)
        if self._between is not None:
            active.set_between_plots_widget(self._between)
        if self._footer is not None:
            active.set_footer_widget(self._footer)

    def update_curves(self, *args, **kwargs) -> None:
        self.single.update_curves(*args, **kwargs)

    def clear_all(self) -> None:
        self.single.clear_all()

    def apply_theme(self, theme: str, brand_mode: bool = False) -> None:
        self.single.apply_theme(theme, brand_mode)
        self.two.apply_theme(theme, brand_mode)

    def bottom_plot_global_rect(self) -> QRect:
        return self._active().bottom_plot_global_rect()

    def export_bottom_plot_image(self, output_path: str) -> bool:
        if not self._two_enabled:
            return self.single.export_bottom_plot_image(output_path)
        pane = self.two._combined if self.two._bottom_mode == "combined" else (
            self.two._bottom_1 if self.two._selection == "channel_1" else self.two._bottom_2
        )
        return pane.plot.grab().save(output_path, "PNG")

    def bottom_plot_pixmap(self):
        if not self._two_enabled:
            return self.single.bottom_plot_pixmap()
        pane = self.two._combined if self.two._bottom_mode == "combined" else (
            self.two._bottom_1 if self.two._selection == "channel_1" else self.two._bottom_2
        )
        return pane.plot.grab()


def _auto_center(plot: pg.PlotWidget, curves: list[Curve]) -> None:
    if not curves:
        plot.setYRange(-15.0, 15.0, padding=0)
        return
    values = [np.asarray(mag, dtype=float) for _freqs, mag in curves if len(mag)]
    if not values:
        return
    merged = np.concatenate(values)
    low = float(np.nanpercentile(merged, 2.0))
    high = float(np.nanpercentile(merged, 98.0))
    center = (low + high) / 2.0
    plot.setYRange(center - 15.0, center + 15.0, padding=0)
