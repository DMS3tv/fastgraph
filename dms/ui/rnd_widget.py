from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Iterable
from functools import lru_cache, partial
from pathlib import Path
from uuid import uuid4

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dms import brand_brand
from dms.curator.models import PreferenceBounds
from dms.curator.parser import load_preference_bounds, load_two_column_txt_curve
from dms.graph_display import (
    add_bounds_band,
    add_variation_band,
    retro_step_group,
    retro_step_series,
    stipple_trace_pen,
    uses_retro_steps,
)
from dms.hrtf import HRTFCurve
from dms.processing import DEFAULT_SMOOTHING, F_REF, VariationBand, smooth_fractional_octave
from dms.rnd.models import (
    DEFAULT_COLORS,
    RnDGroup,
    RnDMeasurement,
    RnDSession,
    group_variation,
)
from dms.rnd.photos import RnDPhotoStore
from dms.style_tokens import tokens_for
from dms.theme import (
    colors_for,
    ensure_graph_color,
    normalize_theme,
    theme_trace_palette,
)
from dms.ui.dual_plot_widget import _configure_plot_widget, _NoWheelPlotWidget
from dms.ui.modern_button import ModernButton as QPushButton
from dms.ui.modern_spinbox import ModernDoubleSpinBox as QDoubleSpinBox
from dms.ui.rnd_photo_dialogs import CameraCaptureDialog, PhotoViewerDialog
from dms.ui.rounded_viewport import RoundedViewportFrame
from dms.ui.theme_surface import DitherSurface
from dms.ui.toggle_switch import ToggleSwitch

ROOT_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
HRTF_DIR = ROOT_DIR / "HRTFs"
BOUNDS_DIR = ROOT_DIR / "Bounds"
UPPER_BOUNDS_PATH = BOUNDS_DIR / "- Upper Bounds.txt"
LOWER_BOUNDS_PATH = BOUNDS_DIR / "- Lower Bounds.txt"

VARIATION_COLOR = "#FCBE11"
SMOOTHING_OPTIONS = [48, 24, 12, 6, 3]

ROLE_KIND = Qt.ItemDataRole.UserRole
ROLE_ID = Qt.ItemDataRole.UserRole + 1
KIND_GROUP = "group"
KIND_MEASUREMENT = "measurement"

logger = logging.getLogger(__name__)

#: Trailing " (3)" count suffix that group rows show after their name.
_GROUP_COUNT_SUFFIX = re.compile(r"\s\(\d+\)$")


def cached_hrtf_curve(path: str) -> HRTFCurve:
    """Return a parsed ``HRTFCurve``, reusing one while the file is unchanged.

    A redraw asks for the same handful of files once per measurement; without
    the cache every one of those reads and interpolates the file again.
    """
    stat = os.stat(path)
    return _parsed_hrtf_curve(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=24)
def _parsed_hrtf_curve(path: str, _mtime_ns: int, _size: int) -> HRTFCurve:
    return HRTFCurve(path)


def _configure_plot(plot: pg.PlotWidget) -> None:
    _configure_plot_widget(plot)
    for pos, angle in ((np.log10(F_REF), 90), (0.0, 0)):
        plot.addItem(
            pg.InfiniteLine(
                pos=pos,
                angle=angle,
                pen=pg.mkPen(color=(75, 75, 75), style=Qt.PenStyle.DashLine),
            )
        )


class RnDPlotWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._theme = "dark"
        self._brand_mode = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.top_plot = _NoWheelPlotWidget(title="View 1")
        self.bottom_plot = _NoWheelPlotWidget(title="View 2")
        _configure_plot(self.top_plot)
        _configure_plot(self.bottom_plot)
        self.top_frame = RoundedViewportFrame(self.top_plot)
        self.bottom_frame = RoundedViewportFrame(self.bottom_plot)
        layout.addWidget(self.top_frame, 1)
        self._between_plots_widget: QWidget | None = None
        self._curve_sources: dict[pg.PlotDataItem, tuple[np.ndarray, np.ndarray]] = {}
        layout.addWidget(self.bottom_frame, 1)
        self._items: list[object] = []

    def set_between_plots_widget(self, widget: QWidget) -> None:
        if self._between_plots_widget is not None:
            self.layout().removeWidget(self._between_plots_widget)
            self._between_plots_widget.setParent(None)
        self._between_plots_widget = widget
        self.layout().insertWidget(1, widget)

    def apply_theme(self, theme: str, brand_mode: bool = False) -> None:
        self._theme = normalize_theme(theme)
        self._brand_mode = bool(brand_mode)
        colors = colors_for(self._theme, brand_mode=self._brand_mode)
        for plot in (self.top_plot, self.bottom_plot):
            plot.setBackground(colors["plot_bg"])
            for axis_name in ("left", "bottom"):
                axis = plot.getAxis(axis_name)
                axis.setPen(pg.mkPen(colors["plot_fg"]))
                axis.setTextPen(pg.mkPen(colors["plot_fg"]))
            plot.getPlotItem().titleLabel.setAttr("color", colors["accent"])
        for item, (freqs, values) in self._curve_sources.items():
            display_freqs, display_values = self._display_curve(freqs, values)
            item.setData(display_freqs, display_values, antialias=True)

    def _accent_color(self) -> str:
        if self._brand_mode:
            return brand_brand.GRADIENT_ORANGE
        palette = theme_trace_palette(self._theme)
        return palette[0] if tokens_for(self._theme).trace_palette else VARIATION_COLOR

    def _display_color(self, color: object) -> QColor:
        colors = colors_for(self._theme, brand_mode=self._brand_mode)
        return ensure_graph_color(color, colors["plot_bg"])

    def _uses_retro_steps(self) -> bool:
        return uses_retro_steps(self._theme, brand_mode=self._brand_mode)

    def _display_curve(
        self, freqs: np.ndarray, values: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        if self._uses_retro_steps():
            return retro_step_series(freqs, values)
        return freqs, values

    def _plot_curve(self, plot: pg.PlotWidget, freqs, values, *, pen) -> pg.PlotDataItem:
        source_freqs = np.asarray(freqs, dtype=float)
        source_values = np.asarray(values, dtype=float)
        display_freqs, display_values = self._display_curve(source_freqs, source_values)
        item = plot.plot(display_freqs, display_values, pen=pen, antialias=True)
        self._curve_sources[item] = (source_freqs, source_values)
        return item

    def redraw(
        self,
        *,
        top_measurements: list[tuple[RnDMeasurement, np.ndarray]],
        pinned_measurements: list[tuple[RnDMeasurement, np.ndarray]],
        top_group_variations: list[
            tuple[
                RnDGroup,
                VariationBand,
            ]
        ],
        bottom_group_variations: list[
            tuple[
                RnDGroup,
                VariationBand,
            ]
        ],
        delta_mode_active: bool = False,
        delta_measurements: list[tuple[RnDMeasurement, np.ndarray, np.ndarray]] | None = None,
        delta_group_variations: list[
            tuple[
                RnDGroup,
                VariationBand,
            ]
        ]
        | None = None,
        preference_bounds: PreferenceBounds | None = None,
        target_curve: tuple[str, np.ndarray, np.ndarray] | None = None,
        review_curve: tuple[np.ndarray, np.ndarray] | None = None,
    ) -> None:
        for item in self._items:
            try:
                self.top_plot.removeItem(item)
                self.bottom_plot.removeItem(item)
            except Exception:
                pass
        self._items.clear()
        self._curve_sources.clear()

        top_curves = []
        bottom_curves = []
        trace_tokens = tokens_for(self._theme, brand_mode=self._brand_mode)
        trace_palette = theme_trace_palette(self._theme, brand_mode=self._brand_mode)

        def trace_index_for(measurement: RnDMeasurement, fallback: int) -> int:
            try:
                return trace_palette.index(measurement.color)
            except ValueError:
                return fallback

        for fallback_index, (measurement, mag_db) in enumerate(top_measurements):
            trace_index = trace_index_for(measurement, fallback_index)
            item = self._plot_curve(
                self.top_plot,
                measurement.freqs,
                mag_db,
                pen=stipple_trace_pen(
                    pg.mkPen(
                        self._display_color(measurement.color),
                        width=2.4 if measurement.milestone else 1.2,
                    ),
                    trace_index,
                    trace_tokens,
                ),
            )
            self._items.append(item)
            top_curves.append((measurement.freqs, mag_db))
        if review_curve is not None:
            freqs, mag_db = review_curve
            color = self._display_color(self._accent_color())
            glow_color = QColor(color)
            glow_color.setAlpha(72)
            glow = self._plot_curve(
                self.top_plot,
                freqs,
                mag_db,
                pen=pg.mkPen(glow_color, width=8.0),
            )
            item = self._plot_curve(
                self.top_plot,
                freqs,
                mag_db,
                pen=stipple_trace_pen(
                    pg.mkPen(color, width=2.5),
                    0,
                    trace_tokens,
                ),
            )
            self._items.extend([glow, item])
            top_curves.append((freqs, mag_db))
        delta_measurements = delta_measurements or []
        delta_group_variations = delta_group_variations or []
        if delta_mode_active:
            for fallback_index, (measurement, freqs, mag_db) in enumerate(delta_measurements):
                trace_index = trace_index_for(measurement, fallback_index)
                item = self._plot_curve(
                    self.bottom_plot,
                    freqs,
                    mag_db,
                    pen=stipple_trace_pen(
                        pg.mkPen(
                            self._display_color(measurement.color),
                            width=2.4 if measurement.milestone else 1.5,
                        ),
                        trace_index,
                        trace_tokens,
                    ),
                )
                self._items.append(item)
                bottom_curves.append((freqs, mag_db))
            for group, variation in delta_group_variations:
                bottom_curves.extend(self._draw_variation(self.bottom_plot, group, variation))
        else:
            for fallback_index, (measurement, mag_db) in enumerate(pinned_measurements):
                trace_index = trace_index_for(measurement, fallback_index)
                item = self._plot_curve(
                    self.bottom_plot,
                    measurement.freqs,
                    mag_db,
                    pen=stipple_trace_pen(
                        pg.mkPen(
                            self._display_color(measurement.color),
                            width=2.4 if measurement.milestone else 1.5,
                        ),
                        trace_index,
                        trace_tokens,
                    ),
                )
                self._items.append(item)
                bottom_curves.append((measurement.freqs, mag_db))

        for group, variation in top_group_variations:
            top_curves.extend(self._draw_variation(self.top_plot, group, variation))

        if not delta_mode_active:
            for group, variation in bottom_group_variations:
                bottom_curves.extend(self._draw_variation(self.bottom_plot, group, variation))

        if preference_bounds is not None and preference_bounds.enabled:
            bounds_curves = self._draw_preference_bounds(self.top_plot, preference_bounds)
            top_curves.extend(bounds_curves)
            if not delta_mode_active:
                bounds_curves = self._draw_preference_bounds(self.bottom_plot, preference_bounds)
                bottom_curves.extend(bounds_curves)

        if target_curve is not None:
            _name, freqs, mag_db = target_curve
            top_curves.extend(self._draw_target(self.top_plot, freqs, mag_db))
            if not delta_mode_active:
                bottom_curves.extend(self._draw_target(self.bottom_plot, freqs, mag_db))

        self._auto_center(self.top_plot, top_curves)
        self._auto_center(self.bottom_plot, bottom_curves)

    def _draw_variation(
        self,
        plot: pg.PlotWidget,
        group: RnDGroup,
        variation: VariationBand,
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        qcolor = self._display_color(
            "#ff5078" if group.milestone else group.color or self._accent_color()
        )
        outer = QColor(qcolor)
        outer.setAlpha(55 if not group.milestone else 75)
        inner = QColor(qcolor)
        inner.setAlpha(95 if not group.milestone else 118)
        glow = QColor(qcolor)
        glow.setAlpha(58)
        self._items.extend(
            add_variation_band(
                plot,
                variation,
                outer_brush=outer,
                inner_brush=inner,
                median_pen=pg.mkPen(qcolor, width=2.2 if not group.milestone else 2.8),
                median_glow_pen=pg.mkPen(glow, width=7.0),
                plot_curve=partial(self._plot_curve, plot),
            )
        )
        return [(variation.freqs, variation.p10), (variation.freqs, variation.p90)]

    def _draw_preference_bounds(
        self,
        plot: pg.PlotWidget,
        bounds: PreferenceBounds,
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        if bounds.upper is None or bounds.lower is None:
            return []
        if bounds.upper.mag_db is None or bounds.lower.mag_db is None:
            return []
        freqs = np.array(bounds.upper.freqs, dtype=float, copy=True)
        upper = np.array(bounds.upper.mag_db, dtype=float, copy=True)
        lower = np.interp(freqs, bounds.lower.freqs, bounds.lower.mag_db)
        if len(freqs) < 2:
            return []
        tokens = tokens_for(self._theme, brand_mode=self._brand_mode)
        # The dither fill is drawn as given, so it is stepped here as a group;
        # plain edges are stepped one by one through ``_plot_curve``.
        shown = (freqs, upper, lower)
        if tokens.dither_chrome:
            shown = retro_step_group(freqs, (upper, lower))
        self._items.extend(
            add_bounds_band(
                plot,
                *shown,
                tokens=tokens,
                color=self._display_color("#969696"),
                plot_curve=partial(self._plot_curve, plot),
            )
        )
        return [(freqs, upper), (freqs, lower)]

    def _draw_target(
        self,
        plot: pg.PlotWidget,
        freqs: np.ndarray,
        mag_db: np.ndarray,
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        if len(freqs) < 2:
            return []
        target_color = self._display_color("#F5F5F5")
        glow_color = QColor(target_color)
        glow_color.setAlpha(48)
        line_color = QColor(target_color)
        line_color.setAlpha(225)
        glow = self._plot_curve(plot, freqs, mag_db, pen=pg.mkPen(glow_color, width=5.0))
        item = self._plot_curve(
            plot,
            freqs,
            mag_db,
            pen=pg.mkPen(color=line_color, width=1.8, style=Qt.PenStyle.DashLine),
        )
        self._items.extend([glow, item])
        return [(freqs, mag_db)]

    @staticmethod
    def _auto_center(plot: pg.PlotWidget, curves: list[tuple[np.ndarray, np.ndarray]]) -> None:
        if not curves:
            plot.setYRange(-15.0, 15.0, padding=0)
            return
        values = np.concatenate([mag for _freqs, mag in curves if len(mag)])
        if len(values) == 0:
            plot.setYRange(-15.0, 15.0, padding=0)
            return
        hi = max(15.0, float(np.nanmax(values)) + 1.0)
        plot.setYRange(hi - 30.0, hi, padding=0)


class _RnDTree(QTreeWidget):
    structure_changed = pyqtSignal()

    def dropEvent(self, event) -> None:
        super().dropEvent(event)
        self.structure_changed.emit()


class RnDWidget(QWidget):
    measure_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    export_requested = pyqtSignal()
    send_to_curator_requested = pyqtSignal()
    save_requested = pyqtSignal()
    load_requested = pyqtSignal()
    selection_changed = pyqtSignal()
    view_state_changed = pyqtSignal()
    state_changed = pyqtSignal()
    input_channel_changed = pyqtSignal(int)
    notes_expanded_changed = pyqtSignal(bool)
    splitter_ratio_changed = pyqtSignal(float)

    def __init__(
        self,
        parent=None,
        *,
        notes_expanded: bool = True,
        splitter_ratio: float = 0.5,
    ) -> None:
        super().__init__(parent)
        self.session = RnDSession()
        self.photo_store = RnDPhotoStore()
        self._theme = "dark"
        self._brand_mode = False
        self._syncing = False
        self._busy = False
        self._normal_status = "Ready"
        self._recovery_warning = ""
        self._notes_expanded = bool(notes_expanded)
        self._splitter_ratio = min(0.8, max(0.2, float(splitter_ratio)))
        self._applying_splitter_ratio = False
        self._splitter_save_timer = QTimer(self)
        self._splitter_save_timer.setSingleShot(True)
        self._splitter_save_timer.setInterval(250)
        self._splitter_save_timer.timeout.connect(
            lambda: self.splitter_ratio_changed.emit(self._splitter_ratio)
        )
        self._hrtf_options = self._load_hrtf_options()
        self._preference_bounds = self._load_preference_bounds()
        self._missing_hrtf_names: set[str] = set()
        self._review_curve: tuple[np.ndarray, np.ndarray] | None = None
        self._build_ui()
        self._sync_tree()
        self.apply_theme(self._theme)

    def apply_theme(self, theme: str, brand_mode: bool = False) -> None:
        self._theme = normalize_theme(theme)
        self._brand_mode = bool(brand_mode)
        self._plots.apply_theme(theme, brand_mode=self._brand_mode)
        self._apply_accent_stylesheets()
        self._redraw()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._measure_btn.setEnabled(not busy)
        self._cancel_btn.setEnabled(busy)
        self._tree.setEnabled(not busy)
        self._notes_edit.setEnabled(not busy)
        self._bounds_enabled.setEnabled(not busy)
        self._smoothing_combo.setEnabled(not busy)
        self._delta_mode_toggle.setEnabled(not busy)
        self._target_enabled.setEnabled(not busy)
        self._target_import_btn.setEnabled(not busy)
        self._target_offset_spin.setEnabled(not busy and len(self.session.target_freqs) >= 2)
        self._default_hrtf_combo.setEnabled(not busy)
        self._input_channel_combo.setEnabled(not busy and self._input_channel_combo.count() > 0)
        for button in self._editing_buttons:
            button.setEnabled(not busy)
        self._sync_photo_panel()

    def add_measurement(self, measurement: RnDMeasurement) -> None:
        self.add_measurement_batch([measurement])

    def set_review_curve(self, curve: tuple[np.ndarray, np.ndarray] | None) -> None:
        """Show a temporary measurement in View 1 without changing the session."""
        if curve is None:
            self._review_curve = None
        else:
            freqs, mag_db = curve
            self._review_curve = (
                np.array(freqs, dtype=float, copy=True),
                np.array(mag_db, dtype=float, copy=True),
            )
        self._redraw()

    def add_measurement_batch(
        self,
        measurements: Iterable[RnDMeasurement],
        *,
        group: RnDGroup | None = None,
        inherit_default_hrtf: bool = True,
    ) -> None:
        measurements = list(measurements)
        if not measurements:
            return
        colors = theme_trace_palette(self._theme, brand_mode=self._brand_mode)
        start_index = len(self.session.measurements)
        for index, measurement in enumerate(measurements):
            measurement.color = colors[(start_index + index) % len(colors)]
            if inherit_default_hrtf and not measurement.hrtf_path and self.session.hrtf_path:
                measurement.hrtf_path = str(self.session.hrtf_path)
                measurement.hrtf_name = self.session.hrtf_name or self._hrtf_label(
                    measurement.hrtf_path,
                    "",
                )
            elif measurement.hrtf_path and not measurement.hrtf_name:
                measurement.hrtf_name = self._hrtf_label(measurement.hrtf_path, "")
            self.session.measurements.append(measurement)
        if group is None:
            self.session.ungrouped_order.extend(item.id for item in measurements)
            selected_id = measurements[-1].id
        else:
            group.measurement_ids = [item.id for item in measurements]
            self.session.groups.append(group)
            selected_id = group.id
        self.session.selected_id = selected_id
        self.session.repair_ordering()
        self._sync_tree()
        signals_blocked = self._tree.blockSignals(True)
        try:
            self._select_id(selected_id)
        finally:
            self._tree.blockSignals(signals_blocked)
        self._sync_detail_panel()
        self._redraw()
        self.state_changed.emit()

    def selected_measurement(self) -> RnDMeasurement | None:
        item = self._tree.currentItem()
        if item is None or item.data(0, ROLE_KIND) != KIND_MEASUREMENT:
            return None
        return self.session.measurement_by_id(item.data(0, ROLE_ID))

    def selected_group(self) -> RnDGroup | None:
        item = self._tree.currentItem()
        if item is None or item.data(0, ROLE_KIND) != KIND_GROUP:
            return None
        return self.session.group_by_id(item.data(0, ROLE_ID))

    def selected_id(self) -> str | None:
        item = self._tree.currentItem()
        return item.data(0, ROLE_ID) if item is not None else None

    def selected_group_measurements(self) -> list[RnDMeasurement]:
        group = self.selected_group()
        if group is None:
            return []
        return [
            measurement
            for measurement_id in group.measurement_ids
            if (measurement := self.session.measurement_by_id(measurement_id)) is not None
        ]

    def replace_session(self, session: RnDSession) -> None:
        self.session = session
        self._sync_bounds_control()
        self._sync_view_controls()
        self._sync_target_controls()
        self._sync_default_hrtf_control()
        self._sync_tree()
        if self.session.selected_id:
            self._select_id(self.session.selected_id)
        self._redraw()
        self.state_changed.emit()

    def merge_session(self, incoming: RnDSession) -> None:
        existing_ids = {item.id for item in self.session.measurements}
        existing_group_ids = {item.id for item in self.session.groups}
        existing_names = {item.name for item in self.session.measurements}
        id_map: dict[str, str] = {}
        for measurement in incoming.measurements:
            old_id = measurement.id
            if measurement.id in existing_ids:
                measurement.id = self._new_id(existing_ids)
            existing_ids.add(measurement.id)
            id_map[old_id] = measurement.id
            if measurement.name in existing_names:
                measurement.name = self._unique_name(measurement.name, existing_names)
            existing_names.add(measurement.name)
            self.session.measurements.append(measurement)
        for group in incoming.groups:
            if group.id in existing_group_ids:
                group.id = self._new_id(existing_group_ids)
            existing_group_ids.add(group.id)
            group.measurement_ids = [
                id_map[item] for item in group.measurement_ids if item in id_map
            ]
            self.session.groups.append(group)
        self.session.ungrouped_order.extend(
            id_map[item] for item in incoming.ungrouped_order if item in id_map
        )
        self.session.repair_ordering()
        self._sync_bounds_control()
        self._sync_view_controls()
        self._sync_target_controls()
        self._sync_default_hrtf_control()
        self._sync_tree()
        self._redraw()
        self.state_changed.emit()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)
        self._splitter = splitter
        splitter.splitterMoved.connect(self._on_splitter_moved)

        viewport_panel = QWidget()
        viewport_panel.setMinimumWidth(360)
        viewport_panel.setProperty("surfaceLevel", "viewport")
        self._viewport_panel = viewport_panel
        viewport_layout = QVBoxLayout(viewport_panel)
        viewport_layout.setContentsMargins(8, 8, 8, 8)
        viewport_layout.setSpacing(6)
        toolbar = QWidget()
        toolbar.setObjectName("rnd_top_toolbar")
        toolbar.setProperty("surfaceLevel", "raised")
        toolbar.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )
        self._top_toolbar_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, toolbar)
        self._top_toolbar_layout.setContentsMargins(4, 2, 4, 2)
        self._top_toolbar_layout.setSpacing(10)

        measure_controls = QWidget()
        measure_controls.setObjectName("rnd_measure_controls")
        measure_controls.setProperty("layoutRole", "transparent")
        measure_controls.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        measure_row = QHBoxLayout(measure_controls)
        measure_row.setContentsMargins(0, 0, 0, 0)
        measure_row.setSpacing(6)
        self._status_label = QLabel("Ready")
        self._status_label.setProperty("tone", "muted")
        measure_row.addWidget(self._status_label)
        measure_row.addSpacing(12)
        self._measure_btn = QPushButton("Measure")
        self._measure_btn.setObjectName("btn_start")
        self._measure_btn.clicked.connect(self.measure_requested)
        measure_row.addWidget(self._measure_btn)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setObjectName("btn_cancel")
        self._cancel_btn.clicked.connect(self.cancel_requested)
        self._cancel_btn.setEnabled(False)
        measure_row.addWidget(self._cancel_btn)
        measure_row.addWidget(QLabel("Input Channel"))
        self._input_channel_combo = QComboBox()
        self._input_channel_combo.setMinimumWidth(78)
        self._input_channel_combo.currentIndexChanged.connect(self._emit_input_channel_changed)
        measure_row.addWidget(self._input_channel_combo)
        measure_row.addWidget(QLabel("HRTF"))
        self._default_hrtf_combo = QComboBox()
        self._default_hrtf_combo.setToolTip("Default HRTF for newly kept R&D measurements")
        self._default_hrtf_combo.setMinimumWidth(132)
        for label, value in self._hrtf_options:
            self._default_hrtf_combo.addItem(label, value)
        self._ensure_hrtf_combo_option(
            self._default_hrtf_combo,
            self.session.hrtf_path or "",
            self.session.hrtf_name,
        )
        hrtf_index = self._default_hrtf_combo.findData(self.session.hrtf_path or "")
        self._default_hrtf_combo.setCurrentIndex(hrtf_index if hrtf_index >= 0 else 0)
        self._default_hrtf_combo.currentIndexChanged.connect(self._on_default_hrtf_changed)
        measure_row.addWidget(self._default_hrtf_combo, 1)

        target_controls = QWidget()
        target_controls.setObjectName("rnd_target_controls")
        target_controls.setProperty("layoutRole", "transparent")
        target_controls.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        view_controls = QHBoxLayout(target_controls)
        view_controls.setContentsMargins(0, 0, 0, 0)
        view_controls.addWidget(QLabel("Preference Bounds"))
        self._bounds_enabled = ToggleSwitch()
        self._bounds_enabled.setToolTip("Show preference bounds in both R&D viewports")
        self._bounds_enabled.setChecked(self.session.preference_bounds_enabled)
        self._bounds_enabled.stateChanged.connect(self._on_bounds_enabled_changed)
        view_controls.addWidget(self._bounds_enabled)
        view_controls.addSpacing(12)
        view_controls.addWidget(QLabel("Target"))
        self._target_enabled = ToggleSwitch()
        self._target_enabled.setToolTip("Show the imported target in both R&D viewports")
        self._target_enabled.setChecked(self.session.target_visible)
        self._target_enabled.stateChanged.connect(self._on_target_enabled_changed)
        view_controls.addWidget(self._target_enabled)
        self._target_import_btn = QPushButton("Import Target...")
        self._target_import_btn.clicked.connect(self._import_target)
        view_controls.addWidget(self._target_import_btn)
        self._target_offset_spin = QDoubleSpinBox()
        self._target_offset_spin.setRange(-120.0, 120.0)
        self._target_offset_spin.setDecimals(2)
        self._target_offset_spin.setSingleStep(0.5)
        self._target_offset_spin.setSuffix(" dB")
        self._target_offset_spin.setFixedWidth(92)
        self._target_offset_spin.setValue(self.session.target_offset_db)
        self._target_offset_spin.valueChanged.connect(self._set_target_offset)
        view_controls.addWidget(self._target_offset_spin)
        self._target_label = QLabel("No target")
        self._target_label.setProperty("tone", "muted")
        view_controls.addWidget(self._target_label, 1)
        self._top_toolbar_layout.addWidget(measure_controls, 1)
        self._top_toolbar_layout.addWidget(target_controls, 1)
        viewport_layout.addWidget(toolbar)
        self._top_toolbar = toolbar
        self._plots = RnDPlotWidget()
        self._plots.set_between_plots_widget(self._build_interplot_controls())
        viewport_layout.addWidget(self._plots, 1)
        viewport_layout.addWidget(self._build_footer_controls())
        splitter.addWidget(viewport_panel)

        panel = DitherSurface()
        panel.setObjectName("controlPanel")
        panel.setProperty("surfaceLevel", "panel")
        panel.setMinimumWidth(360)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(8, 8, 8, 8)
        panel_layout.setSpacing(8)
        splitter.addWidget(panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        QTimer.singleShot(0, self._apply_splitter_ratio)

        data_box = QGroupBox("Measurements")
        data_layout = QVBoxLayout(data_box)
        self._tree = _RnDTree()
        self._tree.setHeaderLabels(
            ["Name", "View 1", "View 2", "Var", "Milestone", "Offset", "HRTF"]
        )
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in {
            1: 56,
            2: 56,
            3: 48,
            4: 80,
            5: 96,
            6: 108,
        }.items():
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self._tree.setColumnWidth(column, width)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.currentItemChanged.connect(self._on_selection_changed)
        self._tree.itemExpanded.connect(lambda item: self._on_group_expansion_changed(item, True))
        self._tree.itemCollapsed.connect(lambda item: self._on_group_expansion_changed(item, False))
        self._tree.structure_changed.connect(self._on_tree_structure_changed)
        data_layout.addWidget(self._tree, 1)

        edit_row = QHBoxLayout()
        self._new_group_btn = QPushButton("New Group")
        self._new_group_btn.clicked.connect(self._new_group)
        edit_row.addWidget(self._new_group_btn)
        self._remove_btn = QPushButton("Remove")
        self._remove_btn.clicked.connect(self._remove_selected)
        edit_row.addWidget(self._remove_btn)
        self._up_btn = QPushButton("Up")
        self._up_btn.clicked.connect(lambda: self._move_selected(-1))
        edit_row.addWidget(self._up_btn)
        self._down_btn = QPushButton("Down")
        self._down_btn.clicked.connect(lambda: self._move_selected(1))
        edit_row.addWidget(self._down_btn)
        data_layout.addLayout(edit_row)
        panel_layout.addWidget(data_box, 1)

        notes_panel = QWidget()
        notes_panel.setProperty("layoutRole", "transparent")
        notes_layout = QVBoxLayout(notes_panel)
        notes_layout.setContentsMargins(0, 0, 0, 0)
        notes_layout.setSpacing(4)
        self._notes_toggle = QToolButton()
        self._notes_toggle.setObjectName("section_toggle")
        self._notes_toggle.setText("Notes")
        self._notes_toggle.setCheckable(True)
        self._notes_toggle.setChecked(self._notes_expanded)
        self._notes_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._notes_toggle.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._notes_toggle.clicked.connect(self._on_notes_toggled)
        notes_layout.addWidget(self._notes_toggle)
        self._notes_content = QGroupBox()
        detail_layout = QVBoxLayout(self._notes_content)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Name")
        self._name_edit.editingFinished.connect(self._save_selected_name)
        detail_layout.addWidget(self._name_edit)
        self._notes_edit = QTextEdit()
        self._notes_edit.setPlaceholderText("Notes")
        self._notes_edit.setMaximumHeight(110)
        self._notes_edit.textChanged.connect(self._save_selected_notes)
        detail_layout.addWidget(self._notes_edit)
        photo_header = QHBoxLayout()
        self._photo_count = QLabel("Photos (0)")
        photo_header.addWidget(self._photo_count)
        photo_header.addStretch(1)
        self._capture_photo_btn = QPushButton("Capture...")
        self._capture_photo_btn.setToolTip("Capture a photo from a webcam for the selected item")
        self._capture_photo_btn.clicked.connect(self._capture_photo)
        photo_header.addWidget(self._capture_photo_btn)
        self._import_photo_btn = QPushButton("Import...")
        self._import_photo_btn.setToolTip("Attach an existing image file to the selected item")
        self._import_photo_btn.clicked.connect(self._import_photo)
        photo_header.addWidget(self._import_photo_btn)
        detail_layout.addLayout(photo_header)
        photo_scroll = QScrollArea()
        photo_scroll.setWidgetResizable(True)
        photo_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        photo_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        photo_scroll.setMaximumHeight(92)
        self._photo_strip = QWidget()
        self._photo_strip.setProperty("layoutRole", "transparent")
        self._photo_strip_layout = QHBoxLayout(self._photo_strip)
        self._photo_strip_layout.setContentsMargins(2, 2, 2, 2)
        self._photo_strip_layout.setSpacing(5)
        self._photo_strip_layout.addStretch(1)
        photo_scroll.setWidget(self._photo_strip)
        detail_layout.addWidget(photo_scroll)
        notes_layout.addWidget(self._notes_content)
        panel_layout.addWidget(notes_panel)
        self._sync_notes_expanded()
        self._editing_buttons = [
            self._new_group_btn,
            self._remove_btn,
            self._up_btn,
            self._down_btn,
            self._export_btn,
            self._curator_btn,
            self._save_btn,
            self._load_btn,
        ]
        self._apply_accent_stylesheets()

    def _accent_color(self) -> str:
        return brand_brand.GRADIENT_ORANGE if self._brand_mode else "#FCBE11"

    def _apply_accent_stylesheets(self) -> None:
        self._tree.setStyleSheet("")
        self._export_btn.setRole("primary")

    def _build_footer_controls(self) -> QWidget:
        footer = QWidget()
        footer.setObjectName("rnd_footer_controls")
        footer.setProperty("surfaceLevel", "raised")
        row = QHBoxLayout(footer)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(8)
        row.addStretch(1)
        self._export_btn = QPushButton("Export Selected...")
        self._export_btn.clicked.connect(self.export_requested)
        row.addWidget(self._export_btn)
        self._curator_btn = QPushButton("Send to Curator")
        self._curator_btn.clicked.connect(self.send_to_curator_requested)
        row.addWidget(self._curator_btn)
        self._save_btn = QPushButton("Save Session...")
        self._save_btn.setObjectName("btn_upload")
        self._save_btn.clicked.connect(self.save_requested)
        row.addWidget(self._save_btn)
        self._load_btn = QPushButton("Load Session...")
        self._load_btn.clicked.connect(self.load_requested)
        row.addWidget(self._load_btn)
        return footer

    def _on_notes_toggled(self, checked: bool) -> None:
        self._notes_expanded = bool(checked)
        self._sync_notes_expanded()
        self.notes_expanded_changed.emit(self._notes_expanded)

    def _sync_notes_expanded(self) -> None:
        self._notes_content.setVisible(self._notes_expanded)
        self._notes_toggle.setArrowType(
            Qt.ArrowType.DownArrow if self._notes_expanded else Qt.ArrowType.RightArrow
        )

    def set_input_channels(self, channels: list[tuple[str, int]], selected: int) -> None:
        self._input_channel_combo.blockSignals(True)
        self._input_channel_combo.clear()
        for label, value in channels:
            self._input_channel_combo.addItem(label, value)
        index = self._input_channel_combo.findData(selected)
        self._input_channel_combo.setCurrentIndex(index if index >= 0 else -1)
        self._input_channel_combo.blockSignals(False)
        self._input_channel_combo.setEnabled(not self._busy and bool(channels))

    def set_input_channel(self, selected: int) -> None:
        index = self._input_channel_combo.findData(selected)
        if index < 0:
            return
        self._input_channel_combo.blockSignals(True)
        self._input_channel_combo.setCurrentIndex(index)
        self._input_channel_combo.blockSignals(False)

    def _emit_input_channel_changed(self, index: int) -> None:
        if index >= 0:
            self.input_channel_changed.emit(int(self._input_channel_combo.itemData(index)))

    def _build_interplot_controls(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("rnd_interplot_controls")
        panel.setProperty("layoutRole", "transparent")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)
        layout.addStretch(1)
        layout.addWidget(QLabel("Smoothing"))
        self._smoothing_combo = QComboBox()
        for fraction in SMOOTHING_OPTIONS:
            self._smoothing_combo.addItem(f"1/{fraction}", fraction)
        index = self._smoothing_combo.findData(self.session.smoothing_fraction)
        self._smoothing_combo.setCurrentIndex(index if index >= 0 else 0)
        self._smoothing_combo.currentIndexChanged.connect(self._on_smoothing_changed)
        layout.addWidget(self._smoothing_combo)
        layout.addSpacing(14)
        layout.addWidget(QLabel("Delta Mode"))
        self._delta_mode_toggle = ToggleSwitch()
        self._delta_mode_toggle.setToolTip(
            "Show bottom viewport curves as deltas from the first bottom item"
        )
        self._delta_mode_toggle.setChecked(self.session.delta_mode_enabled)
        self._delta_mode_toggle.stateChanged.connect(self._on_delta_mode_changed)
        layout.addWidget(self._delta_mode_toggle)
        layout.addStretch(1)
        return panel

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_splitter_ratio)

    def resizeEvent(self, event) -> None:
        intended_viewport_width = event.size().width() * self._splitter_ratio
        stacked = intended_viewport_width < 1100
        direction = (
            QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight
        )
        self._top_toolbar_layout.setDirection(direction)
        stretch = 0 if stacked else 1
        self._top_toolbar_layout.setStretch(0, stretch)
        self._top_toolbar_layout.setStretch(1, stretch)
        self._top_toolbar_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop if stacked else Qt.AlignmentFlag.AlignVCenter
        )
        self._top_toolbar_layout.invalidate()
        self._top_toolbar.updateGeometry()
        super().resizeEvent(event)
        QTimer.singleShot(0, self._apply_splitter_ratio)

    def _apply_splitter_ratio(self) -> None:
        available = self._splitter.width() - self._splitter.handleWidth()
        if available <= 1:
            return
        first = max(1, int(round(available * self._splitter_ratio)))
        second = max(1, available - first)
        self._applying_splitter_ratio = True
        try:
            self._splitter.setSizes([first, second])
        finally:
            self._applying_splitter_ratio = False

    def _on_splitter_moved(self, _position: int, _index: int) -> None:
        if self._applying_splitter_ratio:
            return
        sizes = self._splitter.sizes()
        total = sum(sizes)
        if len(sizes) < 2 or total <= 0:
            return
        self._splitter_ratio = min(0.8, max(0.2, sizes[0] / total))
        self._splitter_save_timer.start()

    def _sync_tree(self) -> None:
        self._syncing = True
        try:
            self._refresh_group_colors()
            self._tree.clear()
            for measurement_id in self.session.ungrouped_order:
                measurement = self.session.measurement_by_id(measurement_id)
                if measurement is not None:
                    item = self._measurement_item(measurement)
                    self._tree.addTopLevelItem(item)
                    self._install_measurement_toggles(item, measurement)
            for group in self.session.groups:
                group_item = self._group_item(group)
                self._tree.addTopLevelItem(group_item)
                self._install_group_toggles(group_item, group)
                for measurement_id in group.measurement_ids:
                    measurement = self.session.measurement_by_id(measurement_id)
                    if measurement is not None:
                        child = self._measurement_item(measurement)
                        group_item.addChild(child)
                        self._install_measurement_toggles(child, measurement)
                group_item.setExpanded(group.expanded)
        finally:
            self._syncing = False
        self._sync_detail_panel()

    def _refresh_group_colors(self) -> None:
        colors = theme_trace_palette(self._theme, brand_mode=self._brand_mode)
        for index, group in enumerate(self.session.groups):
            if not group.color or group.color == DEFAULT_COLORS[0]:
                group.color = colors[index % len(colors)]

    def _measurement_item(self, measurement: RnDMeasurement) -> QTreeWidgetItem:
        item = QTreeWidgetItem([measurement.name, "", "", "", "", "", ""])
        item.setData(0, ROLE_KIND, KIND_MEASUREMENT)
        item.setData(0, ROLE_ID, measurement.id)
        # Drops land between rows, never on one: dropping onto an item is what
        # used to nest a group inside a group and empty it.
        item.setFlags(
            (item.flags() | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsDragEnabled)
            & ~Qt.ItemFlag.ItemIsDropEnabled
        )
        item.setText(1, "")
        item.setText(2, "")
        item.setText(3, "")
        item.setText(4, "")
        item.setText(5, "")
        if measurement.notes:
            item.setToolTip(0, measurement.notes)
        return item

    def _group_item(self, group: RnDGroup) -> QTreeWidgetItem:
        label = f"{group.name} ({len(group.measurement_ids)})"
        item = QTreeWidgetItem([label, "", "", "", "", "", ""])
        item.setData(0, ROLE_KIND, KIND_GROUP)
        item.setData(0, ROLE_ID, group.id)
        # Dropping a measurement onto a group is the natural way to file it,
        # so groups stay drop targets. A group dropped onto another group has
        # no place in the session model; _on_tree_structure_changed moves its
        # measurements back to the parent level and warns.
        item.setFlags(
            item.flags()
            | Qt.ItemFlag.ItemIsEditable
            | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsDropEnabled
        )
        item.setText(1, "")
        item.setText(2, "")
        item.setText(3, "")
        item.setText(4, "")
        item.setText(5, "")
        if group.notes:
            item.setToolTip(0, group.notes)
        return item

    def _install_measurement_toggles(
        self, item: QTreeWidgetItem, measurement: RnDMeasurement
    ) -> None:
        self._tree.setItemWidget(
            item,
            1,
            self._checkbox_cell(
                measurement.top_visible,
                lambda checked, measurement_id=measurement.id: self._set_measurement_top_visible(
                    measurement_id, checked
                ),
                "Show this measurement in View 1",
            ),
        )
        self._tree.setItemWidget(
            item,
            2,
            self._checkbox_cell(
                measurement.pinned,
                lambda checked, measurement_id=measurement.id: self._set_measurement_pinned(
                    measurement_id, checked
                ),
                "Show this measurement in View 2",
            ),
        )
        self._tree.setItemWidget(
            item,
            4,
            self._checkbox_cell(
                measurement.milestone,
                lambda checked, measurement_id=measurement.id: self._set_measurement_milestone(
                    measurement_id, checked
                ),
                "Mark this measurement as a milestone",
            ),
        )
        self._tree.setItemWidget(
            item,
            5,
            self._offset_cell(
                measurement.vertical_offset_db,
                lambda value, measurement_id=measurement.id: self._set_measurement_offset(
                    measurement_id, value
                ),
            ),
        )
        self._tree.setItemWidget(item, 6, self._hrtf_cell(measurement))

    def _install_group_toggles(self, item: QTreeWidgetItem, group: RnDGroup) -> None:
        self._tree.setItemWidget(
            item,
            1,
            self._checkbox_cell(
                group.visible,
                lambda checked, group_id=group.id: self._set_group_visible(group_id, checked),
                "Show this whole group in View 1",
            ),
        )
        self._tree.setItemWidget(
            item,
            2,
            self._checkbox_cell(
                group.pinned,
                lambda checked, group_id=group.id: self._set_group_pinned(group_id, checked),
                "Show this whole group in View 2",
            ),
        )
        self._tree.setItemWidget(
            item,
            3,
            self._checkbox_cell(
                group.variation_enabled,
                lambda checked, group_id=group.id: self._set_group_variation(group_id, checked),
                "Show this group's variation band",
            ),
        )
        self._tree.setItemWidget(
            item,
            4,
            self._checkbox_cell(
                group.milestone,
                lambda checked, group_id=group.id: self._set_group_milestone(group_id, checked),
                "Mark this group as a milestone",
            ),
        )
        self._tree.setItemWidget(
            item,
            5,
            self._offset_cell(
                group.vertical_offset_db,
                lambda value, group_id=group.id: self._set_group_offset(group_id, value),
            ),
        )
        label = QLabel("Group")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setProperty("tone", "muted")
        self._tree.setItemWidget(item, 6, label)

    def _checkbox_cell(self, checked: bool, callback, tooltip: str) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(3, 1, 3, 1)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        checkbox = QCheckBox()
        checkbox.setToolTip(tooltip)
        checkbox.setChecked(checked)
        checkbox.stateChanged.connect(lambda _state: callback(checkbox.isChecked()))
        layout.addWidget(checkbox)
        return container

    def _offset_cell(self, value: float, callback) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(2, 1, 2, 1)
        spin = QDoubleSpinBox()
        spin.setRange(-120.0, 120.0)
        spin.setDecimals(2)
        spin.setSingleStep(0.5)
        spin.setSuffix(" dB")
        spin.setFixedWidth(88)
        spin.setValue(float(value))
        spin.valueChanged.connect(lambda new_value: callback(float(new_value)))
        layout.addWidget(spin)
        return container

    def _hrtf_cell(self, measurement: RnDMeasurement) -> QWidget:
        combo = QComboBox()
        combo.setToolTip("Apply an HRTF to this R&D measurement")
        combo.setMinimumWidth(92)
        combo.setMaximumWidth(108)
        for label, value in self._hrtf_options:
            combo.addItem(label, value)
        resolved = self.resolve_hrtf_path(measurement.hrtf_path, measurement.hrtf_name)
        self._ensure_hrtf_combo_option(combo, measurement.hrtf_path, measurement.hrtf_name)
        index = combo.findData(resolved or measurement.hrtf_path)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.currentIndexChanged.connect(
            lambda _index, measurement_id=measurement.id, widget=combo: self._set_measurement_hrtf(
                measurement_id,
                str(widget.currentData() or ""),
                self._combo_hrtf_name(widget),
            )
        )
        return combo

    def _set_measurement_top_visible(self, measurement_id: str, checked: bool) -> None:
        measurement = self.session.measurement_by_id(measurement_id)
        if measurement is None:
            return
        measurement.top_visible = checked
        self._redraw()
        self.state_changed.emit()

    def _set_measurement_pinned(self, measurement_id: str, checked: bool) -> None:
        measurement = self.session.measurement_by_id(measurement_id)
        if measurement is None:
            return
        measurement.pinned = checked
        self._redraw()
        self.state_changed.emit()

    def _set_measurement_milestone(self, measurement_id: str, checked: bool) -> None:
        measurement = self.session.measurement_by_id(measurement_id)
        if measurement is None:
            return
        measurement.milestone = checked
        self._redraw()
        self.state_changed.emit()

    def _set_measurement_hrtf(self, measurement_id: str, path: str, name: str = "") -> None:
        measurement = self.session.measurement_by_id(measurement_id)
        if measurement is None:
            return
        measurement.hrtf_path = path
        measurement.hrtf_name = name
        self._redraw()
        self.state_changed.emit()

    def _set_measurement_offset(self, measurement_id: str, value: float) -> None:
        measurement = self.session.measurement_by_id(measurement_id)
        if measurement is None:
            return
        measurement.vertical_offset_db = float(value)
        self._redraw()
        self.state_changed.emit()

    def _set_group_visible(self, group_id: str, checked: bool) -> None:
        group = self.session.group_by_id(group_id)
        if group is None:
            return
        group.visible = checked
        self._redraw()
        self.state_changed.emit()

    def _set_group_pinned(self, group_id: str, checked: bool) -> None:
        group = self.session.group_by_id(group_id)
        if group is None:
            return
        group.pinned = checked
        self._redraw()
        self.state_changed.emit()

    def _set_group_variation(self, group_id: str, checked: bool) -> None:
        group = self.session.group_by_id(group_id)
        if group is None:
            return
        group.variation_enabled = checked
        self._redraw()
        self.state_changed.emit()

    def _set_group_milestone(self, group_id: str, checked: bool) -> None:
        group = self.session.group_by_id(group_id)
        if group is None:
            return
        group.milestone = checked
        self._redraw()
        self.state_changed.emit()

    def _set_group_offset(self, group_id: str, value: float) -> None:
        group = self.session.group_by_id(group_id)
        if group is None:
            return
        group.vertical_offset_db = float(value)
        self._redraw()
        self.state_changed.emit()

    def _on_group_expansion_changed(
        self,
        item: QTreeWidgetItem,
        expanded: bool,
    ) -> None:
        if self._syncing or item.data(0, ROLE_KIND) != KIND_GROUP:
            return
        group = self.session.group_by_id(item.data(0, ROLE_ID))
        if group is None or group.expanded == expanded:
            return
        group.expanded = expanded
        self.view_state_changed.emit()

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._syncing:
            return
        kind = item.data(0, ROLE_KIND)
        item_id = item.data(0, ROLE_ID)
        if kind == KIND_MEASUREMENT:
            measurement = self.session.measurement_by_id(item_id)
            if measurement is None:
                return
            if column == 0:
                measurement.name = item.text(0).strip() or measurement.name
        elif kind == KIND_GROUP:
            group = self.session.group_by_id(item_id)
            if group is None:
                return
            if column == 0:
                # Rows read "Name (3)"; only that trailing count is stripped, so
                # a group genuinely called "Prototype (v2)" keeps its name.
                text = _GROUP_COUNT_SUFFIX.sub("", item.text(0).strip()).strip()
                if text:
                    group.name = text
        self._redraw()
        self.state_changed.emit()

    def _on_selection_changed(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        if self._syncing:
            return
        self.session.selected_id = current.data(0, ROLE_ID) if current is not None else None
        self._sync_detail_panel()
        self.selection_changed.emit()

    def _sync_detail_panel(self) -> None:
        self._syncing = True
        try:
            measurement = self.selected_measurement()
            group = self.selected_group()
            if measurement is not None:
                self._name_edit.setEnabled(True)
                self._notes_edit.setEnabled(True)
                self._name_edit.setText(measurement.name)
                self._notes_edit.setPlainText(measurement.notes)
            elif group is not None:
                self._name_edit.setEnabled(True)
                self._notes_edit.setEnabled(True)
                self._name_edit.setText(group.name)
                self._notes_edit.setPlainText(group.notes)
            else:
                self._name_edit.clear()
                self._notes_edit.clear()
                self._name_edit.setEnabled(False)
                self._notes_edit.setEnabled(False)
            self._sync_photo_panel()
        finally:
            self._syncing = False

    def _selected_photos(self):
        if measurement := self.selected_measurement():
            return measurement.photos
        if group := self.selected_group():
            return group.photos
        return None

    def _sync_photo_panel(self) -> None:
        if not hasattr(self, "_photo_strip_layout"):
            return
        photos = self._selected_photos()
        enabled = photos is not None and not self._busy
        self._capture_photo_btn.setEnabled(enabled)
        self._import_photo_btn.setEnabled(enabled)
        self._photo_count.setText(f"Photos ({len(photos or [])})")
        while self._photo_strip_layout.count():
            item = self._photo_strip_layout.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()
        if not photos:
            empty = QLabel("Select an item and add a photo, or no photos attached")
            empty.setProperty("tone", "muted")
            self._photo_strip_layout.addWidget(empty)
        else:
            for photo in photos:
                button = QPushButton()
                button.setFixedSize(76, 76)
                image = QImage(photo.runtime_path) if photo.runtime_path else QImage()
                if image.isNull():
                    button.setText("Missing\nphoto")
                else:
                    button.setIcon(
                        QIcon(
                            QPixmap.fromImage(image).scaled(
                                68,
                                68,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation,
                            )
                        )
                    )
                    button.setIconSize(QSize(68, 68))
                button.setToolTip(photo.caption or photo.display_name)
                button.setEnabled(not self._busy)
                button.clicked.connect(
                    lambda _checked=False, selected=photo: self._open_photo(selected)
                )
                self._photo_strip_layout.addWidget(button)
        self._photo_strip_layout.addStretch(1)

    def _capture_photo(self) -> None:
        photos = self._selected_photos()
        if photos is None:
            return
        dialog = CameraCaptureDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.image is None:
            return
        try:
            photos.append(
                self.photo_store.add_image(
                    dialog.image, display_name="Webcam photo", caption=dialog.caption
                )
            )
        except Exception as exc:
            QMessageBox.warning(self, "Photo Capture Failed", str(exc))
            return
        self._sync_photo_panel()
        self.state_changed.emit()

    def _import_photo(self) -> None:
        photos = self._selected_photos()
        if photos is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import R&D Photo",
            "",
            "Image Files (*.jpg *.jpeg *.png *.bmp *.webp);;All Files (*)",
        )
        if not path:
            return
        try:
            photos.append(self.photo_store.import_file(path))
        except Exception as exc:
            QMessageBox.warning(self, "Photo Import Failed", str(exc))
            return
        self._sync_photo_panel()
        self.state_changed.emit()

    def _open_photo(self, photo) -> None:
        photos = self._selected_photos()
        if photos is None:
            return
        index = photos.index(photo)
        entries = [
            (
                QImage(item.runtime_path) if item.runtime_path else None,
                item.caption,
                item.display_name,
            )
            for item in photos
        ]
        dialog = PhotoViewerDialog(entries, index, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        # Captions are applied first: removing one photo must not throw away
        # the caption the user just typed on another.
        for item, caption in zip(photos, dialog.captions):
            item.caption = caption
        if dialog.remove_requested and dialog.remove_index is not None:
            photos.pop(dialog.remove_index)
        self._sync_photo_panel()
        self.state_changed.emit()

    def _save_selected_name(self) -> None:
        if self._syncing:
            return
        text = self._name_edit.text().strip()
        if not text:
            return
        if measurement := self.selected_measurement():
            measurement.name = text
        elif group := self.selected_group():
            group.name = text
        self._sync_tree()
        if self.session.selected_id:
            self._select_id(self.session.selected_id)
        self.state_changed.emit()

    def _save_selected_notes(self) -> None:
        if self._syncing:
            return
        notes = self._notes_edit.toPlainText()
        if measurement := self.selected_measurement():
            measurement.notes = notes
        elif group := self.selected_group():
            group.notes = notes
        self.state_changed.emit()

    def _on_bounds_enabled_changed(self, _state: int) -> None:
        if self._syncing:
            return
        self.session.preference_bounds_enabled = self._bounds_enabled.isChecked()
        self._preference_bounds.enabled = self.session.preference_bounds_enabled
        self._redraw()
        self.state_changed.emit()

    def _on_smoothing_changed(self, _index: int) -> None:
        if self._syncing:
            return
        self.session.smoothing_fraction = int(
            self._smoothing_combo.currentData() or DEFAULT_SMOOTHING
        )
        self._redraw()
        self.state_changed.emit()

    def _on_delta_mode_changed(self, _state: int) -> None:
        if self._syncing:
            return
        self.session.delta_mode_enabled = self._delta_mode_toggle.isChecked()
        self._redraw()
        self.state_changed.emit()

    def _on_target_enabled_changed(self, _state: int) -> None:
        if self._syncing:
            return
        self.session.target_visible = self._target_enabled.isChecked()
        self._sync_target_controls()
        self._redraw()
        self.state_changed.emit()

    def _set_target_offset(self, value: float) -> None:
        if self._syncing:
            return
        self.session.target_offset_db = float(value)
        self._redraw()
        self.state_changed.emit()

    def _import_target(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import R&D Target",
            "",
            "Text Files (*.txt);;All Files (*)",
        )
        if not path:
            return
        try:
            freqs, mag_db = load_two_column_txt_curve(path, label="Target")
        except Exception as exc:
            QMessageBox.warning(self, "Target Import Failed", str(exc))
            return
        self.session.target_freqs = freqs
        self.session.target_mag_db = mag_db
        self.session.target_path = path
        self.session.target_name = Path(path).stem
        self.session.target_visible = True
        self._sync_target_controls()
        self._redraw()
        self.state_changed.emit()

    def _on_default_hrtf_changed(self, _index: int) -> None:
        if self._syncing:
            return
        self.session.hrtf_path = str(self._default_hrtf_combo.currentData() or "")
        self.session.hrtf_name = self._combo_hrtf_name(self._default_hrtf_combo)
        self.session.hrtf_enabled = bool(self.session.hrtf_path)
        self.state_changed.emit()

    def _sync_bounds_control(self) -> None:
        self._syncing = True
        try:
            self._bounds_enabled.setChecked(self.session.preference_bounds_enabled)
            self._preference_bounds.enabled = self.session.preference_bounds_enabled
        finally:
            self._syncing = False

    def _sync_view_controls(self) -> None:
        self._syncing = True
        try:
            index = self._smoothing_combo.findData(
                int(self.session.smoothing_fraction or DEFAULT_SMOOTHING)
            )
            self._smoothing_combo.setCurrentIndex(index if index >= 0 else 0)
            self._delta_mode_toggle.setChecked(bool(self.session.delta_mode_enabled))
        finally:
            self._syncing = False

    def _sync_target_controls(self) -> None:
        self._syncing = True
        try:
            self._target_enabled.setChecked(self.session.target_visible)
            self._target_offset_spin.setValue(self.session.target_offset_db)
            has_target = (
                len(self.session.target_freqs) >= 2 and len(self.session.target_mag_db) >= 2
            )
            self._target_offset_spin.setEnabled(has_target)
            self._target_label.setText(self.session.target_name or "No target")
            self._target_label.setToolTip(self.session.target_path)
        finally:
            self._syncing = False

    def _sync_default_hrtf_control(self) -> None:
        self._syncing = True
        try:
            resolved = self.resolve_hrtf_path(self.session.hrtf_path or "", self.session.hrtf_name)
            self._ensure_hrtf_combo_option(
                self._default_hrtf_combo,
                self.session.hrtf_path or "",
                self.session.hrtf_name,
            )
            index = self._default_hrtf_combo.findData(resolved or self.session.hrtf_path or "")
            self._default_hrtf_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self._syncing = False

    def _new_group(self) -> None:
        names = {group.name for group in self.session.groups}
        name = self._unique_name("New Group", names)
        group = RnDGroup(name=name)
        colors = theme_trace_palette(self._theme, brand_mode=self._brand_mode)
        group.color = colors[len(self.session.groups) % len(colors)]
        selected_ids = self._selected_measurement_ids()
        if selected_ids:
            self._remove_measurement_ids_from_orders(selected_ids)
            group.measurement_ids = selected_ids
        self.session.groups.append(group)
        self.session.selected_id = group.id
        self.session.repair_ordering()
        self._sync_tree()
        self._select_id(group.id)
        self._redraw()
        self.state_changed.emit()

    def _remove_selected(self) -> None:
        items = list(self._tree.selectedItems())
        current = self._tree.currentItem()
        if not items and current is not None:
            items = [current]
        measurement_ids: list[str] = []
        group_ids: list[str] = []
        for item in items:
            kind = item.data(0, ROLE_KIND)
            item_id = item.data(0, ROLE_ID)
            if kind == KIND_MEASUREMENT and item_id not in measurement_ids:
                measurement_ids.append(item_id)
            elif kind == KIND_GROUP and item_id not in group_ids:
                group_ids.append(item_id)
        count = len(measurement_ids) + len(group_ids)
        if not count:
            return
        if not self._confirm_removal(measurement_ids, group_ids):
            return
        doomed = set(measurement_ids)
        for group_id in group_ids:
            group = self.session.group_by_id(group_id)
            if group is not None:
                # A removed group releases its measurements unless they were
                # selected for removal too.
                self.session.ungrouped_order.extend(
                    item_id for item_id in group.measurement_ids if item_id not in doomed
                )
        if group_ids:
            removed_groups = set(group_ids)
            self.session.groups = [
                group for group in self.session.groups if group.id not in removed_groups
            ]
        if doomed:
            self.session.measurements = [
                item for item in self.session.measurements if item.id not in doomed
            ]
            self.session.ungrouped_order = [
                item_id for item_id in self.session.ungrouped_order if item_id not in doomed
            ]
            for group in self.session.groups:
                group.measurement_ids = [
                    item_id for item_id in group.measurement_ids if item_id not in doomed
                ]
        self.session.selected_id = None
        self.session.repair_ordering()
        self._sync_tree()
        self._redraw()
        self.state_changed.emit()

    def _confirm_removal(self, measurement_ids: list[str], group_ids: list[str]) -> bool:
        """Ask once, naming exactly what is about to be removed."""
        parts = []
        if measurement_ids:
            count = len(measurement_ids)
            parts.append(f"{count} measurement{'s' if count != 1 else ''}")
        if group_ids:
            count = len(group_ids)
            parts.append(f"{count} group{'s' if count != 1 else ''}")
        choice = QMessageBox.question(
            self,
            "Remove From R&D Session",
            f"Remove {' and '.join(parts)} from this R&D session?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return choice == QMessageBox.StandardButton.Yes

    def _move_selected(self, delta: int) -> None:
        item = self._tree.currentItem()
        if item is None:
            return
        kind = item.data(0, ROLE_KIND)
        item_id = item.data(0, ROLE_ID)
        if kind == KIND_GROUP:
            order = [group.id for group in self.session.groups]
            collection = self.session.groups
        else:
            parent = item.parent()
            if parent is None:
                order = self.session.ungrouped_order
                collection = None
            else:
                group = self.session.group_by_id(parent.data(0, ROLE_ID))
                if group is None:
                    return
                order = group.measurement_ids
                collection = None
        try:
            index = order.index(item_id)
        except ValueError:
            return
        new_index = max(0, min(len(order) - 1, index + delta))
        if new_index == index:
            return
        if kind == KIND_GROUP and collection is not None:
            collection[index], collection[new_index] = collection[new_index], collection[index]
        else:
            order[index], order[new_index] = order[new_index], order[index]
        self._sync_tree()
        self._select_id(item_id)
        self._redraw()
        self.state_changed.emit()

    def _on_tree_structure_changed(self) -> None:
        if self._syncing:
            return
        self.session.ungrouped_order.clear()
        for group in self.session.groups:
            group.measurement_ids.clear()
        groups_by_id = {group.id: group for group in self.session.groups}
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            kind = item.data(0, ROLE_KIND)
            item_id = item.data(0, ROLE_ID)
            if kind == KIND_MEASUREMENT:
                self.session.ungrouped_order.append(item_id)
            elif kind == KIND_GROUP:
                group = groups_by_id.get(item_id)
                if group is None:
                    continue
                group.expanded = item.isExpanded()
                group.measurement_ids.extend(self._collect_child_measurement_ids(item))
        self.session.repair_ordering()
        self._sync_tree()
        self._redraw()
        self.state_changed.emit()

    def _collect_child_measurement_ids(self, item: QTreeWidgetItem) -> list[str]:
        """Every measurement under ``item``, flattened.

        The model has no nested groups. If a drop ever produced one anyway, its
        measurements are pulled up to the parent group instead of being dropped
        on the floor, and the event is logged.
        """
        collected: list[str] = []
        for index in range(item.childCount()):
            child = item.child(index)
            kind = child.data(0, ROLE_KIND)
            if kind == KIND_MEASUREMENT:
                collected.append(child.data(0, ROLE_ID))
            elif kind == KIND_GROUP:
                rescued = self._collect_child_measurement_ids(child)
                logger.warning(
                    "R&D tree contained a nested group (%s); moved %d measurement(s) "
                    "back to the parent level.",
                    child.data(0, ROLE_ID),
                    len(rescued),
                )
                collected.extend(rescued)
        return collected

    def _selected_measurement_ids(self) -> list[str]:
        selected = {
            item.data(0, ROLE_ID)
            for item in self._tree.selectedItems()
            if item.data(0, ROLE_KIND) == KIND_MEASUREMENT
        }
        if not selected:
            return []
        return [item_id for item_id in self._visual_measurement_order() if item_id in selected]

    def _visual_measurement_order(self) -> list[str]:
        ordered: list[str] = []
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            if item.data(0, ROLE_KIND) == KIND_MEASUREMENT:
                ordered.append(item.data(0, ROLE_ID))
            elif item.data(0, ROLE_KIND) == KIND_GROUP:
                for child_index in range(item.childCount()):
                    child = item.child(child_index)
                    if child.data(0, ROLE_KIND) == KIND_MEASUREMENT:
                        ordered.append(child.data(0, ROLE_ID))
        return ordered

    def _remove_measurement_ids_from_orders(self, measurement_ids: list[str]) -> None:
        selected = set(measurement_ids)
        self.session.ungrouped_order = [
            item_id for item_id in self.session.ungrouped_order if item_id not in selected
        ]
        for group in self.session.groups:
            group.measurement_ids = [
                item_id for item_id in group.measurement_ids if item_id not in selected
            ]

    def _visible_measurements_for_group(
        self, group_id: str | None, *, top: bool
    ) -> list[tuple[RnDMeasurement, np.ndarray]]:
        if group_id is None:
            ids = self.session.ungrouped_order
            group_visible = True
        else:
            group = self.session.group_by_id(group_id)
            if group is None:
                return []
            ids = group.measurement_ids
            group_visible = group.visible if top else group.pinned
        if not group_visible:
            return []
        measurements = []
        for measurement_id in ids:
            measurement = self.session.measurement_by_id(measurement_id)
            if measurement is None:
                continue
            group = self.session.group_by_id(group_id) if group_id is not None else None
            _freqs, displayed = self.displayed_measurement_curve(measurement, group=group)
            if top and measurement.top_visible:
                measurements.append((measurement, displayed))
            # View 2 honours the row's own checkbox for grouped measurements as
            # well: the group's View 2 decides whether the group is shown at
            # all, the row decides whether that measurement is part of it.
            if not top and measurement.pinned:
                measurements.append((measurement, displayed))
        return measurements

    def _measurements_for_group_view(self, group: RnDGroup, *, top: bool) -> list[RnDMeasurement]:
        if top and not group.visible:
            return []
        if not top and not group.pinned:
            return []
        result = []
        for measurement_id in group.measurement_ids:
            measurement = self.session.measurement_by_id(measurement_id)
            if measurement is None:
                continue
            if top and not measurement.top_visible:
                continue
            if not top and not measurement.pinned:
                continue
            result.append(measurement)
        return result

    def _redraw(self) -> None:
        self._missing_hrtf_names.clear()
        variation_needs_more: set[str] = set()
        self._refresh_group_colors()
        top = self._visible_measurements_for_group(None, top=True)
        pinned = self._visible_measurements_for_group(None, top=False)
        top_variations = []
        bottom_variations = []
        bottom_items: list[tuple[str, object, object]] = [
            ("measurement", measurement, (measurement.freqs, mag)) for measurement, mag in pinned
        ]
        for group in self.session.groups:
            group_top = self._visible_measurements_for_group(group.id, top=True)
            group_pinned = self._visible_measurements_for_group(group.id, top=False)
            top_variation = None
            bottom_variation = None
            if group.visible and group.variation_enabled:
                measurements = [
                    self._with_mag(measurement, self._display_mag(measurement, group))
                    for measurement in self._measurements_for_group_view(group, top=True)
                ]
                top_variation = group_variation(
                    measurements,
                    smoothing_fraction=int(self.session.smoothing_fraction or DEFAULT_SMOOTHING),
                )
                if top_variation is not None:
                    top_variations.append((group, top_variation))
                elif len(measurements) < 2:
                    variation_needs_more.add(group.name)
            if group.pinned and group.variation_enabled:
                measurements = [
                    self._with_mag(measurement, self._display_mag(measurement, group))
                    for measurement in self._measurements_for_group_view(group, top=False)
                ]
                bottom_variation = group_variation(
                    measurements,
                    smoothing_fraction=int(self.session.smoothing_fraction or DEFAULT_SMOOTHING),
                )
                if bottom_variation is not None:
                    bottom_variations.append((group, bottom_variation))
                    bottom_items.append(("variation", group, bottom_variation))
                elif len(measurements) < 2:
                    variation_needs_more.add(group.name)
            if not group.variation_enabled:
                top.extend(group_top)
                pinned.extend(group_pinned)
                bottom_items.extend(
                    ("measurement", measurement, (measurement.freqs, mag))
                    for measurement, mag in group_pinned
                )
        target_curve = None
        if (
            self.session.target_visible
            and len(self.session.target_freqs) >= 2
            and len(self.session.target_mag_db) >= 2
        ):
            target_curve = (
                self.session.target_name,
                self.session.target_freqs,
                self.session.target_mag_db + float(self.session.target_offset_db),
            )
        delta_measurements = []
        delta_variations = []
        delta_needs_more = False
        if self.session.delta_mode_enabled:
            delta_measurements, delta_variations, delta_needs_more = self._bottom_delta_items(
                bottom_items
            )
        self._plots.redraw(
            top_measurements=top,
            pinned_measurements=pinned,
            top_group_variations=top_variations,
            bottom_group_variations=bottom_variations,
            delta_mode_active=bool(self.session.delta_mode_enabled),
            delta_measurements=delta_measurements,
            delta_group_variations=delta_variations,
            preference_bounds=self._preference_bounds
            if self.session.preference_bounds_enabled
            else None,
            target_curve=target_curve,
            review_curve=self._review_curve,
        )
        if self._missing_hrtf_names:
            missing = ", ".join(sorted(self._missing_hrtf_names))
            self.set_status(f"Ready - missing HRTF: {missing}")
        elif delta_needs_more:
            self.set_status("Ready - Delta Mode needs at least 2 bottom items")
        elif variation_needs_more:
            groups = ", ".join(sorted(variation_needs_more))
            self.set_status(f"Ready - Var needs 2 measurements: {groups}")
        elif self._status_label.text().startswith("Ready - "):
            self.set_status("Ready")

    def displayed_measurement_curve(
        self,
        measurement: RnDMeasurement,
        *,
        group: RnDGroup | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        freqs = np.array(measurement.freqs, dtype=float, copy=True)
        mag = self._display_mag(measurement, group)
        return self._smooth_curve(freqs, mag)

    def displayed_group_measurements(self, group: RnDGroup) -> list[RnDMeasurement]:
        measurements = (
            self.selected_group_measurements()
            if self.selected_group() is group
            else [
                measurement
                for measurement_id in group.measurement_ids
                if (measurement := self.session.measurement_by_id(measurement_id)) is not None
            ]
        )
        return [
            self._with_mag(
                measurement,
                self._display_mag(measurement, group),
            )
            for measurement in measurements
        ]

    def _bottom_delta_items(
        self,
        items: list[tuple[str, object, object]],
    ) -> tuple[
        list[tuple[RnDMeasurement, np.ndarray, np.ndarray]],
        list[
            tuple[
                RnDGroup,
                VariationBand,
            ]
        ],
        bool,
    ]:
        if len(items) < 2:
            return [], [], True
        _ref_kind, _ref_owner, ref_curve = items[0]
        ref_freqs, ref_mag = self._delta_reference_curve(ref_curve)
        measurement_deltas: list[tuple[RnDMeasurement, np.ndarray, np.ndarray]] = []
        variation_deltas: list[
            tuple[
                RnDGroup,
                VariationBand,
            ]
        ] = []
        for kind, owner, curve in items[1:]:
            if kind == "measurement":
                freqs, mag = curve
                ref = np.interp(freqs, ref_freqs, ref_mag)
                measurement_deltas.append((owner, freqs, mag - ref))
            else:
                ref = np.interp(curve.freqs, ref_freqs, ref_mag)
                variation_deltas.append(
                    (
                        owner,
                        VariationBand(
                            curve.freqs,
                            curve.p10 - ref,
                            curve.p25 - ref,
                            curve.median - ref,
                            curve.p75 - ref,
                            curve.p90 - ref,
                        ),
                    )
                )
        return measurement_deltas, variation_deltas, False

    @staticmethod
    def _delta_reference_curve(curve: object) -> tuple[np.ndarray, np.ndarray]:
        if len(curve) == 2:
            freqs, mag = curve
            return freqs, mag
        return curve.freqs, curve.median

    def _smooth_curve(self, freqs: np.ndarray, mag_db: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        fraction = int(self.session.smoothing_fraction or DEFAULT_SMOOTHING)
        if fraction <= 0:
            return freqs, mag_db
        return smooth_fractional_octave(freqs, mag_db, fraction=fraction)

    def _display_mag(
        self, measurement: RnDMeasurement, group: RnDGroup | None = None
    ) -> np.ndarray:
        mag = np.array(measurement.mag_db, dtype=float, copy=True)
        hrtf_path = self.resolve_hrtf_path(measurement.hrtf_path, measurement.hrtf_name)
        if hrtf_path:
            try:
                mag = cached_hrtf_curve(hrtf_path).apply(measurement.freqs, mag)
            except Exception:
                self._missing_hrtf_names.add(
                    self._hrtf_label(measurement.hrtf_path, measurement.hrtf_name)
                )
        elif measurement.hrtf_path or measurement.hrtf_name:
            self._missing_hrtf_names.add(
                self._hrtf_label(measurement.hrtf_path, measurement.hrtf_name)
            )
        return mag + self.displayed_offset_db(measurement, group)

    def displayed_offset_db(
        self, measurement: RnDMeasurement, group: RnDGroup | None = None
    ) -> float:
        """Vertical offset applied to the displayed curve: measurement plus group."""
        offset = float(measurement.vertical_offset_db)
        if group is None:
            parent_id = self.session.parent_group_id(measurement.id)
            group = self.session.group_by_id(parent_id) if parent_id is not None else None
        if group is not None:
            offset += float(group.vertical_offset_db)
        return offset

    @staticmethod
    def _with_mag(measurement: RnDMeasurement, mag_db: np.ndarray) -> RnDMeasurement:
        copy = RnDMeasurement(
            id=measurement.id,
            name=measurement.name,
            freqs=measurement.freqs,
            mag_db=np.array(mag_db, dtype=float, copy=True),
            metadata=dict(measurement.metadata),
            rig=measurement.rig,
            input_device_label=measurement.input_device_label,
            input_channel_index=measurement.input_channel_index,
            input_channel_label=measurement.input_channel_label,
            output_device_label=measurement.output_device_label,
            timestamp=measurement.timestamp,
            notes=measurement.notes,
            change_status=measurement.change_status,
            milestone=measurement.milestone,
            top_visible=measurement.top_visible,
            pinned=measurement.pinned,
            color=measurement.color,
            hrtf_path=measurement.hrtf_path,
            hrtf_name=measurement.hrtf_name,
            vertical_offset_db=measurement.vertical_offset_db,
        )
        return copy

    def resolve_hrtf_path(self, path: str | None, name: str | None = "") -> str:
        requested_path = str(path or "")
        if requested_path and Path(requested_path).exists():
            return requested_path
        requested_name = str(name or "").strip()
        fallback_name = Path(requested_path).stem if requested_path else ""
        wanted = (requested_name or fallback_name).casefold()
        if wanted:
            for label, value in self._hrtf_options:
                if value and label.casefold() == wanted:
                    return value
        return ""

    @staticmethod
    def _hrtf_label(path: str | None, name: str | None) -> str:
        label = str(name or "").strip()
        if label:
            return label
        requested_path = str(path or "")
        return Path(requested_path).stem if requested_path else "Unknown"

    @staticmethod
    def _combo_hrtf_name(combo: QComboBox) -> str:
        path = str(combo.currentData() or "")
        if not path:
            return ""
        label = combo.currentText().strip()
        if label.startswith("Missing: "):
            return label.removeprefix("Missing: ").strip()
        return label

    def _ensure_hrtf_combo_option(
        self, combo: QComboBox, path: str | None, name: str | None
    ) -> None:
        requested_path = str(path or "")
        if not requested_path and not name:
            return
        resolved = self.resolve_hrtf_path(requested_path, name)
        if resolved and combo.findData(resolved) >= 0:
            return
        if requested_path and combo.findData(requested_path) >= 0:
            return
        label = self._hrtf_label(requested_path, name)
        combo.addItem(f"Missing: {label}", requested_path)

    @staticmethod
    def _load_hrtf_options() -> list[tuple[str, str]]:
        options = [("None", "")]
        for path in sorted(HRTF_DIR.glob("*.txt")):
            options.append((path.stem, str(path)))
        return options

    @staticmethod
    def _load_preference_bounds() -> PreferenceBounds:
        if not (UPPER_BOUNDS_PATH.exists() and LOWER_BOUNDS_PATH.exists()):
            return PreferenceBounds(enabled=False)
        try:
            bounds = load_preference_bounds(UPPER_BOUNDS_PATH, LOWER_BOUNDS_PATH)
        except Exception:
            return PreferenceBounds(enabled=False)
        bounds.enabled = False
        return bounds

    def set_status(self, text: str) -> None:
        self._normal_status = text
        self._status_label.setText(self._recovery_warning or text)

    def set_recovery_warning(self, text: str) -> None:
        self._recovery_warning = text.strip()
        self._status_label.setText(self._recovery_warning or self._normal_status)

    def _select_id(self, item_id: str) -> None:
        for item in self._walk_items():
            if item.data(0, ROLE_ID) == item_id:
                self._tree.setCurrentItem(item)
                return

    def _walk_items(self) -> Iterable[QTreeWidgetItem]:
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            yield item
            for child_index in range(item.childCount()):
                yield item.child(child_index)

    @staticmethod
    def _new_id(existing: set[str]) -> str:
        value = uuid4().hex
        while value in existing:
            value = uuid4().hex
        return value

    @staticmethod
    def _unique_name(base: str, existing: set[str]) -> str:
        if base not in existing:
            return base
        index = 2
        while f"{base} ({index})" in existing:
            index += 1
        return f"{base} ({index})"
