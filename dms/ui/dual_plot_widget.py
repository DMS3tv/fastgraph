"""
Two stacked pyqtgraph viewports.

Top viewport:
  - All kept curves in grey
  - Most recent kept curve in desaturated teal

Bottom viewport:
  - RMS average of ALL kept curves in #FCBE11

"""

import numpy as np
import pyqtgraph as pg
import pyqtgraph.exporters
from PyQt6.QtCore import QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QDragEnterEvent, QDragMoveEvent, QDropEvent
from PyQt6.QtWidgets import QFileDialog, QMenu, QVBoxLayout, QWidget

from dms import brand_brand
from dms.graph_display import (
    add_variation_band,
    retro_step_band,
    retro_step_series,
    stipple_trace_pen,
    uses_retro_steps,
)
from dms.processing import VariationBand
from dms.style_tokens import tokens_for
from dms.theme import (
    FASTGRAPH_95,
    LIGHT,
    ensure_graph_color,
    brand_theme_colors,
    normalize_theme,
    theme_colors,
    theme_trace_palette,
)
from dms.ui.rounded_viewport import RoundedViewportFrame

pg.setConfigOption("background", "#1a1a1a")
pg.setConfigOption("foreground", "#888888")
pg.setConfigOption("antialias", True)


_GREY = (130, 130, 130, 150)
_TEAL = (80, 180, 170, 220)
_GOLD = "#FCBE11"
_BAND_OUTER = (110, 160, 220, 55)
_BAND_INNER = (130, 185, 255, 90)
_BAND_MEDIAN = (170, 215, 255, 220)

#: Distortion overlay: fixed secondary-axis range, in dB relative to the
#: fundamental. -110 dB is below anything a real measurement resolves and
#: -20 dB is 10 % THD, so the scale never moves under the reader.
_DISTORTION_Y_MIN = -110.0
_DISTORTION_Y_MAX = -20.0
#: Series name -> (base colour, line width). Drawn dashed so the overlay never
#: reads as another response curve.
_DISTORTION_STYLE = {
    "THD": ("#e0533d", 1.6),
    "H2": ("#e2a03f", 1.2),
    "H3": ("#7f8fe0", 1.2),
}

_FREQ_MIN = 20.0
_FREQ_MAX = 20000.0
_X_RANGE_MARGIN = 0.025
_Y_WINDOW_DB = 30.0
_Y_DEFAULT_TOP_DB = 15.0
_Y_TOP_HEADROOM_DB = 1.0

#: Target comparison. The target is drawn thin and dashed behind the average so
#: it never competes with the measurement; reference layers are thin but solid
#: because they are measurements too.
_TARGET_WIDTH = 1.2
_REFERENCE_WIDTH = 1.3
#: Delta view: a fixed +/-12 dB window. The aspect lock (25 dB per decade) is
#: released while it is on, because a difference curve is not a response curve
#: and forcing the response aspect on it would rescale the frequency axis.
_DELTA_Y_LIMIT_DB = 12.0
_DELTA_TITLE = "Delta vs Target (1/12 Oct)"
_AVERAGE_TITLE = "Averaged Result (1/48 Oct RMS)"


class _NoWheelPlotWidget(pg.PlotWidget):
    def wheelEvent(self, event) -> None:
        event.ignore()


class _DropPlotWidget(_NoWheelPlotWidget):
    def __init__(self, title: str, on_paths_dropped) -> None:
        super().__init__(title=title)
        self._on_paths_dropped = on_paths_dropped
        self.setAcceptDrops(True)

    @staticmethod
    def _extract_txt_paths(event) -> list[str]:
        mime = event.mimeData()
        if mime is None or not mime.hasUrls():
            return []
        paths: list[str] = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if path.lower().endswith(".txt"):
                paths.append(path)
        return paths

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._extract_txt_paths(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._extract_txt_paths(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = self._extract_txt_paths(event)
        if not paths:
            event.ignore()
            return
        self._on_paths_dropped(paths)
        event.acceptProposedAction()


def _configure_plot_widget(pw: pg.PlotWidget) -> None:
    ax = pw.getAxis("bottom")
    ax.setLabel("Frequency", units="Hz")
    # The tick labels already read 20 … 20k; pyqtgraph would otherwise pick a
    # prefix (kHz, even MHz) from the log10 view range and mislabel the axis.
    ax.enableAutoSIPrefix(False)
    pw.getAxis("left").setLabel("Magnitude", units="dB")
    pw.setLogMode(x=True, y=False)
    pw.showGrid(x=True, y=True, alpha=0.15)
    log_min = np.log10(_FREQ_MIN)
    log_max = np.log10(_FREQ_MAX)
    log_span = log_max - log_min
    pw.setXRange(
        log_min - log_span * _X_RANGE_MARGIN,
        log_max + log_span * _X_RANGE_MARGIN,
        padding=0,
    )
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


def _make_plot_widget(title: str) -> pg.PlotWidget:
    pw = _NoWheelPlotWidget(title=title)
    _configure_plot_widget(pw)
    return pw


class DualPlotWidget(QWidget):
    measurement_files_dropped = pyqtSignal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = "dark"
        self._brand_mode = False
        self._kept_curves: list[tuple[np.ndarray, np.ndarray]] = []
        self._last_average: tuple[np.ndarray, np.ndarray] | None = None
        self._last_variation: VariationBand | None = None
        self._last_bottom_mode = "average"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._top_plot = _DropPlotWidget(
            title="All Measurements (Top)",
            on_paths_dropped=self._emit_measurement_files_dropped,
        )
        _configure_plot_widget(self._top_plot)
        self._bot_plot = _make_plot_widget("Averaged Result (1/48 Oct RMS)")
        self._setup_plot_context_menu(self._top_plot, "top_plot")
        self._setup_plot_context_menu(self._bot_plot, "bottom_plot")

        self._top_frame = RoundedViewportFrame(self._top_plot)
        self._bot_frame = RoundedViewportFrame(self._bot_plot)
        layout.addWidget(self._top_frame, 1)
        layout.addWidget(self._bot_frame, 1)
        self._header_widget: QWidget | None = None
        self._between_plots_widget: QWidget | None = None
        self._footer_widget: QWidget | None = None

        self._top_items: list[pg.PlotDataItem] = []
        self._bot_item: pg.PlotDataItem | None = None
        self._bot_extra_items: list[object] = []
        # The distortion overlay lives in its own ViewBox, so its items are
        # tracked separately from ``_bot_extra_items``: removing them from the
        # bottom PlotWidget would not detach them from that ViewBox.
        self._distortion_vb: pg.ViewBox | None = None
        self._distortion_items: list[object] = []
        self._last_distortion: tuple[np.ndarray, dict] | None = None
        # Target comparison. These live on the bottom PlotWidget but outside
        # ``_bot_extra_items`` so a plain redraw of the average or the
        # variation band does not take them down with it.
        self._target_curve: tuple[np.ndarray, np.ndarray] | None = None
        self._reference_layers: list[tuple[str, np.ndarray, np.ndarray, str]] = []
        self._compare_items: list[object] = []
        self._compare_legend: pg.LegendItem | None = None
        self._delta_mode = False
        self._delta_zero_line: pg.InfiniteLine | None = None
        self._reveal_item: pg.PlotDataItem | None = None
        self._reveal_curve: tuple[np.ndarray, np.ndarray] | None = None
        self._reveal_progress = 0.0
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setInterval(10)
        self._reveal_timer.timeout.connect(self._tick_reveal_animation)

        # 1 kHz reference line (both plots)
        self._reference_lines: list[pg.InfiniteLine] = []
        for pw in (self._top_plot, self._bot_plot):
            ref = pg.InfiniteLine(
                pos=np.log10(1000.0),
                angle=90,
                pen=pg.mkPen(color=(80, 80, 80), style=Qt.PenStyle.DashLine),
            )
            pw.addItem(ref)
            self._reference_lines.append(ref)
            zero = pg.InfiniteLine(
                pos=0.0,
                angle=0,
                pen=pg.mkPen(color=(70, 70, 70), style=Qt.PenStyle.DashLine),
            )
            pw.addItem(zero)
            self._reference_lines.append(zero)

    def _emit_measurement_files_dropped(self, paths: list[str]) -> None:
        self.measurement_files_dropped.emit(paths)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _bottom_accent_color(self) -> str:
        if self._brand_mode:
            base = brand_brand.GRADIENT_ORANGE
            background = brand_theme_colors()["plot_bg"]
        else:
            custom_palette = tokens_for(self._theme).trace_palette
            base = (
                theme_trace_palette(self._theme)[0]
                if custom_palette
                else ("#000080" if self._theme == FASTGRAPH_95 else _GOLD)
            )
            background = theme_colors(self._theme)["plot_bg"]
        return ensure_graph_color(base, background).name()

    def _uses_light_plot(self) -> bool:
        return self._theme in {LIGHT, FASTGRAPH_95}

    def _uses_retro_steps(self) -> bool:
        return uses_retro_steps(self._theme, brand_mode=self._brand_mode)

    def _display_curve(
        self, freqs: np.ndarray, mag_db: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        if self._uses_retro_steps():
            return retro_step_series(freqs, mag_db)
        return freqs, mag_db

    def apply_theme(self, theme: str, brand_mode: bool = False) -> None:
        self._theme = normalize_theme(theme)
        self._brand_mode = bool(brand_mode)
        colors = brand_theme_colors() if self._brand_mode else theme_colors(self._theme)
        foreground = colors["plot_fg"]
        for plot in (self._top_plot, self._bot_plot):
            plot.setBackground(colors["plot_bg"])
            for axis_name in ("left", "bottom"):
                axis = plot.getAxis(axis_name)
                axis.setPen(pg.mkPen(foreground))
                axis.setTextPen(pg.mkPen(foreground))
            plot.getPlotItem().titleLabel.setAttr("color", foreground)
        light_plot = self._uses_light_plot()
        reference = (128, 128, 128) if light_plot else (75, 75, 75)
        for line in self._reference_lines:
            line.setPen(pg.mkPen(color=reference, style=Qt.PenStyle.DashLine))
        for index, item in enumerate(self._top_items):
            is_last = index == len(self._top_items) - 1
            if is_last:
                color = (
                    (0, 0, 128, 235)
                    if self._theme == FASTGRAPH_95
                    else ((35, 135, 128, 235) if light_plot else _TEAL)
                )
                width = 1.7 if light_plot else 1.5
            else:
                color = (90, 98, 108, 180) if light_plot else _GREY
                width = 1.0
            item.setPen(pg.mkPen(color=color, width=width))
        if self._bot_item is not None:
            self._bot_item.setPen(pg.mkPen(color=self._bottom_accent_color(), width=2.0))
        if self._kept_curves or self._last_average is not None or self._last_variation is not None:
            self._redraw_top(self._kept_curves)
            self._redraw_bottom(
                average=self._last_average,
                variation=self._last_variation,
                mode=self._last_bottom_mode,
            )
        if self._last_distortion is not None:
            right = self._bot_plot.getPlotItem().getAxis("right")
            right.setPen(pg.mkPen(foreground))
            right.setTextPen(pg.mkPen(foreground))
            distortion_freqs, distortion_series = self._last_distortion
            self.set_distortion_overlay(distortion_freqs, distortion_series)
        # Target and reference pens are contrast-corrected against the plot
        # background, so they have to be rebuilt for the new theme.
        self._redraw_compare_layers()
        self.update()

    def update_curves(
        self,
        kept: list[tuple[np.ndarray, np.ndarray]],
        average: tuple[np.ndarray, np.ndarray] | None,
        variation: VariationBand | None = None,
        bottom_mode: str = "average",
        animate_last: bool = False,
    ) -> None:
        """Redraw both viewports. Call from main thread only."""
        self._reveal_timer.stop()
        self._reveal_item = None
        self._reveal_curve = None
        self._kept_curves = kept
        self._last_average = average
        self._last_variation = variation
        self._last_bottom_mode = bottom_mode
        self._redraw_top(kept)
        self._redraw_bottom(average=average, variation=variation, mode=bottom_mode)
        if animate_last and kept and self._top_items:
            self._start_last_curve_reveal(kept[-1], self._top_items[-1])

    def clear_all(self) -> None:
        self._reveal_timer.stop()
        self._reveal_item = None
        self._reveal_curve = None
        self._kept_curves = []
        self._last_average = None
        self._last_variation = None
        self._last_bottom_mode = "average"
        for item in self._top_items:
            self._top_plot.removeItem(item)
        self._top_items.clear()
        if self._bot_item:
            self._bot_plot.removeItem(self._bot_item)
            self._bot_item = None
        for item in self._bot_extra_items:
            self._bot_plot.removeItem(item)
        self._bot_extra_items.clear()
        self.set_distortion_overlay(None, None)
        # A full reset takes the comparison layers down too. The window
        # re-pushes whatever target and references are still loaded right
        # after it clears, so nothing the user chose is silently lost.
        self._target_curve = None
        self._reference_layers = []
        self.set_delta_mode(False)
        self._redraw_compare_layers()

    # ------------------------------------------------------------------
    # Target comparison (target curve, A/B references, delta view)
    # ------------------------------------------------------------------

    def set_target_curve(
        self,
        freqs: np.ndarray | None,
        mag_db: np.ndarray | None = None,
    ) -> None:
        """Draw a target curve behind the average, or remove it with ``None``."""
        if freqs is None or mag_db is None or len(np.asarray(freqs)) == 0:
            self._target_curve = None
        else:
            self._target_curve = (
                np.asarray(freqs, dtype=float),
                np.asarray(mag_db, dtype=float),
            )
        self._redraw_compare_layers()

    def set_reference_layers(
        self,
        layers: list[tuple[str, np.ndarray, np.ndarray, str]] | None,
    ) -> None:
        """Draw ``(name, freqs, mag_db, colour)`` A/B layers, or clear them."""
        cleaned: list[tuple[str, np.ndarray, np.ndarray, str]] = []
        for entry in layers or []:
            name, freqs, mag_db, color = entry
            freqs = np.asarray(freqs, dtype=float)
            mag_db = np.asarray(mag_db, dtype=float)
            if freqs.size == 0 or freqs.size != mag_db.size:
                continue
            cleaned.append((str(name), freqs, mag_db, str(color)))
        self._reference_layers = cleaned
        self._redraw_compare_layers()

    def set_delta_mode(self, enabled: bool) -> None:
        """Switch the bottom viewport between the response and a delta curve.

        A delta is not a response curve, so the 25 dB-per-decade aspect lock is
        released while it is on and the window is pinned to +/-12 dB around an
        emphasized 0 dB line.
        """
        enabled = bool(enabled)
        changed = enabled != self._delta_mode
        self._delta_mode = enabled
        plot_item = self._bot_plot.getPlotItem()
        view_box = plot_item.getViewBox()
        if enabled:
            view_box.setAspectLocked(lock=False)
            if self._delta_zero_line is None:
                line = pg.InfiniteLine(
                    pos=0.0,
                    angle=0,
                    pen=pg.mkPen(color=(150, 150, 150), width=1.0),
                )
                self._bot_plot.addItem(line)
                self._delta_zero_line = line
            self._bot_plot.setYRange(-_DELTA_Y_LIMIT_DB, _DELTA_Y_LIMIT_DB, padding=0)
        else:
            view_box.setAspectLocked(lock=True, ratio=25.0)
            if self._delta_zero_line is not None:
                self._bot_plot.removeItem(self._delta_zero_line)
                self._delta_zero_line = None
        if changed:
            self._redraw_bottom(
                average=self._last_average,
                variation=self._last_variation,
                mode=self._last_bottom_mode,
            )
            self._redraw_compare_layers()

    def _redraw_compare_layers(self) -> None:
        for item in self._compare_items:
            self._bot_plot.removeItem(item)
        self._compare_items.clear()
        # The legend is created once and reused: pyqtgraph re-anchors a
        # LegendItem inside setParentItem(), so detaching it would raise.
        legend = self._compare_legend
        if legend is not None:
            legend.clear()
            legend.hide()

        # In delta view the bottom curve is already measured *against* the
        # target, so a target line (and any absolute reference) would sit on a
        # scale it does not belong to. Nothing comparative is drawn there.
        if self._delta_mode:
            return

        colors = brand_theme_colors() if self._brand_mode else theme_colors(self._theme)
        background = colors["plot_bg"]
        entries: list[tuple[object, str]] = []

        if self._target_curve is not None:
            freqs, mag_db = self._target_curve
            display_freqs, display_mag = self._display_curve(freqs, mag_db)
            pen = pg.mkPen(
                color=ensure_graph_color("#9aa0a6", background),
                width=_TARGET_WIDTH,
                style=Qt.PenStyle.DashLine,
            )
            item = self._bot_plot.plot(display_freqs, display_mag, pen=pen, antialias=True)
            item.setZValue(-2)
            self._compare_items.append(item)
            entries.append((item, "Target"))

        for name, freqs, mag_db, color in self._reference_layers:
            display_freqs, display_mag = self._display_curve(freqs, mag_db)
            pen = pg.mkPen(
                color=ensure_graph_color(color, background),
                width=_REFERENCE_WIDTH,
            )
            item = self._bot_plot.plot(display_freqs, display_mag, pen=pen, antialias=True)
            item.setZValue(-1)
            self._compare_items.append(item)
            entries.append((item, name))

        if not entries:
            return
        if legend is None:
            legend = pg.LegendItem(offset=(12, 8), verSpacing=-4)
            legend.setParentItem(self._bot_plot.getPlotItem().vb)
            self._compare_legend = legend
        legend.clear()
        legend.setLabelTextColor(ensure_graph_color(colors["text"], background))
        for item, name in entries:
            legend.addItem(item, name)
        legend.show()

    # ------------------------------------------------------------------
    # Distortion overlay (secondary right axis on the bottom viewport)
    # ------------------------------------------------------------------

    def set_distortion_overlay(
        self,
        freqs: np.ndarray | None,
        series: dict[str, np.ndarray] | None,
    ) -> None:
        """Draw THD/H2/H3 in dB relative to the fundamental, or hide them.

        The overlay is drawn into a secondary :class:`pg.ViewBox` that is
        x-linked to the bottom plot and carries its own right-hand axis on a
        fixed -110..-20 dB scale, so distortion never rescales the response
        curve underneath it. ``series=None`` hides the overlay entirely.
        """
        for item in self._distortion_items:
            if self._distortion_vb is not None:
                self._distortion_vb.removeItem(item)
        self._distortion_items.clear()
        # The legend is created once and reused: pyqtgraph re-anchors a
        # LegendItem inside setParentItem(), so detaching it would raise.
        legend = getattr(self, "_distortion_legend", None)
        if legend is not None:
            legend.clear()
            legend.hide()

        if freqs is None or series is None or len(np.asarray(freqs)) == 0:
            self._last_distortion = None
            if self._distortion_vb is not None:
                self._distortion_vb.setVisible(False)
                self._bot_plot.getPlotItem().hideAxis("right")
            return

        self._last_distortion = (np.asarray(freqs, dtype=float), dict(series))
        vb = self._ensure_distortion_viewbox()
        vb.setVisible(True)
        self._bot_plot.getPlotItem().showAxis("right")

        colors = brand_theme_colors() if self._brand_mode else theme_colors(self._theme)
        background = colors["plot_bg"]
        log_freqs = np.log10(np.clip(np.asarray(freqs, dtype=float), 1e-6, None))
        for name, values in series.items():
            base, width = _DISTORTION_STYLE.get(name, ("#9aa0a6", 1.2))
            color = ensure_graph_color(base, background)
            pen = pg.mkPen(color=color, width=width, style=Qt.PenStyle.DashLine)
            item = pg.PlotDataItem(
                log_freqs,
                np.asarray(values, dtype=float),
                pen=pen,
                antialias=True,
                connect="finite",
            )
            vb.addItem(item)
            self._distortion_items.append(item)

        # A small legend so the operator can tell THD from H2 and H3; it is
        # parented to the overlay view so it hides with it.
        # Anchor to the plot's own ViewBox (the supported anchor parent); the
        # entries sample the overlay items' pens, so the parent does not matter
        # for what is shown.
        legend = getattr(self, "_distortion_legend", None)
        if legend is None:
            legend = pg.LegendItem(offset=(-12, 8), verSpacing=-4)
            legend.setParentItem(self._bot_plot.getPlotItem().vb)
            self._distortion_legend = legend
        legend.clear()
        text_color = ensure_graph_color(colors["text"], background)
        legend.setLabelTextColor(text_color)
        for item, name in zip(self._distortion_items, series.keys()):
            legend.addItem(item, name)
        legend.show()
        self._sync_distortion_geometry()

    def _ensure_distortion_viewbox(self) -> pg.ViewBox:
        if self._distortion_vb is not None:
            return self._distortion_vb
        plot_item = self._bot_plot.getPlotItem()
        vb = pg.ViewBox(enableMenu=False)
        plot_item.scene().addItem(vb)
        axis = plot_item.getAxis("right")
        axis.linkToView(vb)
        axis.setLabel("Distortion (dB rel.)")
        vb.setXLink(plot_item.vb)
        vb.setYRange(_DISTORTION_Y_MIN, _DISTORTION_Y_MAX, padding=0)
        vb.setMouseEnabled(x=False, y=False)
        vb.setZValue(10)
        plot_item.vb.sigResized.connect(self._sync_distortion_geometry)
        self._distortion_vb = vb
        return vb

    def _sync_distortion_geometry(self, *_args) -> None:
        vb = self._distortion_vb
        if vb is None:
            return
        plot_item = self._bot_plot.getPlotItem()
        vb.setGeometry(plot_item.vb.sceneBoundingRect())
        vb.linkedViewChanged(plot_item.vb, vb.XAxis)
        # The Y range is deliberately fixed; the x-link can otherwise drag it.
        vb.setYRange(_DISTORTION_Y_MIN, _DISTORTION_Y_MAX, padding=0)

    def set_between_plots_widget(self, widget: QWidget) -> None:
        """Insert application controls between the top and bottom viewports."""
        if self._between_plots_widget is not None:
            self.layout().removeWidget(self._between_plots_widget)
            self._between_plots_widget.setParent(None)
        self._between_plots_widget = widget
        top_index = self.layout().indexOf(self._top_frame)
        self.layout().insertWidget(top_index + 1, widget, 0)

    def set_header_widget(self, widget: QWidget) -> None:
        """Insert application controls immediately above the top viewport."""
        if self._header_widget is not None:
            self.layout().removeWidget(self._header_widget)
            self._header_widget.setParent(None)
        self._header_widget = widget
        top_index = self.layout().indexOf(self._top_frame)
        self.layout().insertWidget(top_index, widget, 0)

    def set_footer_widget(self, widget: QWidget) -> None:
        """Insert application controls immediately below the bottom viewport."""
        if self._footer_widget is not None:
            self.layout().removeWidget(self._footer_widget)
            self._footer_widget.setParent(None)
        self._footer_widget = widget
        self.layout().addWidget(widget, 0)

    def release_shared_widgets(self) -> None:
        """Release header/control/footer widgets so another plot page can use them."""
        for name in ("_header_widget", "_between_plots_widget", "_footer_widget"):
            widget = getattr(self, name, None)
            if widget is not None:
                self.layout().removeWidget(widget)
                setattr(self, name, None)

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
            custom_palette = tokens_for(self._theme).trace_palette if not self._brand_mode else ()
            if custom_palette:
                color = QColor(custom_palette[i % len(custom_palette)])
                color.setAlpha(235 if is_last else 190)
                pen = pg.mkPen(color=color, width=1.7 if is_last else 1.15)
            elif is_last:
                light_plot = self._uses_light_plot()
                color = (
                    (0, 0, 128, 235)
                    if self._theme == FASTGRAPH_95
                    else ((35, 135, 128, 235) if light_plot else _TEAL)
                )
                pen = pg.mkPen(color=color, width=1.7 if light_plot else 1.5)
            else:
                color = (90, 98, 108, 180) if self._uses_light_plot() else _GREY
                pen = pg.mkPen(color=color, width=1.0)
            pen = stipple_trace_pen(
                pen,
                i,
                tokens_for(self._theme, brand_mode=self._brand_mode),
            )
            display_freqs, display_mag = self._display_curve(freqs, mag_db)
            item = self._top_plot.plot(display_freqs, display_mag, pen=pen, antialias=True)
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
        average: tuple[np.ndarray, np.ndarray] | None,
        variation: VariationBand | None,
        mode: str,
    ) -> None:
        self._clear_bottom_items()
        if self._delta_mode:
            # The caller hands the delta curve in through ``average``; the
            # variation band is meaningless against a target and is skipped.
            self._bot_plot.setTitle(_DELTA_TITLE)
            if average is not None and len(average[0]) > 0:
                freqs, mag_db = average
                display_freqs, display_mag = self._display_curve(freqs, mag_db)
                pen = pg.mkPen(color=self._bottom_accent_color(), width=2.0)
                pen = stipple_trace_pen(
                    pen,
                    0,
                    tokens_for(self._theme, brand_mode=self._brand_mode),
                )
                self._bot_item = self._bot_plot.plot(
                    display_freqs, display_mag, pen=pen, antialias=True
                )
            self._bot_plot.setYRange(-_DELTA_Y_LIMIT_DB, _DELTA_Y_LIMIT_DB, padding=0)
            return

        if mode == "variation":
            self._bot_plot.setTitle("Variation Band (Confidence Style)")
            self._draw_variation_bottom(variation)
            return

        self._bot_plot.setTitle(_AVERAGE_TITLE)
        if average is not None and len(average[0]) > 0:
            freqs, mag_db = average
            display_freqs, display_mag = self._display_curve(freqs, mag_db)
            pen = pg.mkPen(color=self._bottom_accent_color(), width=2.0)
            pen = stipple_trace_pen(
                pen,
                0,
                tokens_for(self._theme, brand_mode=self._brand_mode),
            )
            self._bot_item = self._bot_plot.plot(
                display_freqs, display_mag, pen=pen, antialias=True
            )
            self._auto_center_y(self._bot_plot, [average])

    def _draw_variation_bottom(self, band: VariationBand | None) -> None:
        if band is None or len(band.freqs) == 0:
            return
        if self._uses_retro_steps():
            band = retro_step_band(band)

        if not self._brand_mode and tokens_for(self._theme).trace_palette:
            base = QColor(theme_trace_palette(self._theme)[0])
            outer_color = QColor(base)
            outer_color.setAlpha(55)
            inner_color = QColor(base)
            inner_color.setAlpha(90)
        else:
            outer_color = QColor(*_BAND_OUTER)
            inner_color = QColor(*_BAND_INNER)
        colors = brand_theme_colors() if self._brand_mode else theme_colors(self._theme)
        median_base = (
            QColor(theme_trace_palette(self._theme)[0])
            if not self._brand_mode and tokens_for(self._theme).trace_palette
            else QColor(*_BAND_MEDIAN[:3])
        )
        median_color = ensure_graph_color(median_base, colors["plot_bg"])
        median_color.setAlpha(_BAND_MEDIAN[3])
        self._bot_extra_items.extend(
            add_variation_band(
                self._bot_plot,
                band,
                outer_brush=outer_color,
                inner_brush=inner_color,
                median_pen=pg.mkPen(color=median_color, width=1.8),
            )
        )
        self._auto_center_y(self._bot_plot, [(band.freqs, band.p10), (band.freqs, band.p90)])

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
        display_freqs, display_mag = self._display_curve(freqs[:k], mag_db[:k])
        self._reveal_item.setData(display_freqs, display_mag, antialias=True)

        self._reveal_progress += 0.14
        if self._reveal_progress >= 1.02:
            display_freqs, display_mag = self._display_curve(freqs, mag_db)
            self._reveal_item.setData(display_freqs, display_mag, antialias=True)
            self._reveal_timer.stop()
