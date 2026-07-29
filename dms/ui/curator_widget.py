from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QRect, QTimer, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from dms import brand_brand
from dms.curator.bounds import load_preference_bounds
from dms.curator.export_image import export_graph_image
from dms.curator.export_brand import draw_brand_poster, brand_export_warnings
from dms.curator.metadata import automatic_export_values, metadata_has_identity
from dms.curator.models import CurveData, ExportText, GraphState, LayerState, PreferenceBounds
from dms.curator.parser import load_hrtf_txt, parse_measurement_txt
from dms.curator.transforms import (
    apply_layer_transform,
    can_combine_layers,
    combine_variation_layers,
    normalization_offset_at_1khz,
)
from dms.ui.modern_button import ModernButton as QPushButton
from dms.ui.modern_spinbox import ModernDoubleSpinBox as QDoubleSpinBox
from dms.ui.rounded_viewport import RoundedViewportFrame
from dms.brand_brand import default_color_cycle
from dms.brand_fonts import brand_font_status
from dms.ui.curator_graph_widget import AspectRatioWidget, BoundsSnapshot, GraphWidget, LayerSnapshot
from dms.ui.toggle_switch import ToggleSwitch
from dms.console import ConsoleEventStore
from dms.theme import DARK, theme_colors


ACCENT_COLOR = "#FCBE11"
ROOT_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
HRTF_DIR = ROOT_DIR / "HRTFs"
BOUNDS_DIR = ROOT_DIR / "Bounds"
UPPER_BOUNDS_PATH = BOUNDS_DIR / "- Upper Bounds.txt"
LOWER_BOUNDS_PATH = BOUNDS_DIR / "- Lower Bounds.txt"
DEFAULT_Y_MIN = -17.5
DEFAULT_Y_MAX = 17.5
SMOOTHING_OPTIONS = [48, 24, 12, 6, 3]


class LayerListRow(QWidget):
    def __init__(
        self,
        layer: LayerState,
        hrtf_options: list[tuple[str, str]],
        on_visible_changed,
        on_color_clicked,
        on_name_changed,
        on_offset_changed,
        on_hrtf_changed,
    ) -> None:
        super().__init__()
        self.layer_id = layer.id
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.visible_check = ToggleSwitch()
        self.visible_check.setChecked(layer.visible)
        self.visible_check.stateChanged.connect(
            lambda _state, layer_id=layer.id: on_visible_changed(layer_id, self.visible_check.isChecked())
        )
        top.addWidget(self.visible_check)

        self.color_btn = QPushButton()
        self.color_btn.setObjectName("colorSwatch")
        self.color_btn.setToolTip("Choose layer color")
        self.color_btn.setFixedSize(22, 22)
        self.color_btn.setStyleSheet(
            f"background-color: {layer.color}; border: 1px solid #242a35; border-radius: 4px;"
        )
        self.color_btn.clicked.connect(
            lambda _checked=False, layer_id=layer.id: on_color_clicked(layer_id, self.color_btn)
        )
        top.addWidget(self.color_btn)

        self.name_edit = QLineEdit(layer.name)
        self.name_edit.setStyleSheet("font-weight: 600;")
        self.name_edit.setToolTip("Rename this graph layer")
        self.name_edit.editingFinished.connect(
            lambda layer_id=layer.id: on_name_changed(layer_id, self.name_edit.text(), self.name_edit)
        )
        top.addWidget(self.name_edit, 1)

        kind = QLabel("VAR" if layer.curve.kind == "variation" else "FR")
        kind.setStyleSheet(f"color: {ACCENT_COLOR}; font-weight: 700;")
        top.addWidget(kind)
        if layer.is_combined:
            derived = QLabel("COMBO")
            derived.setStyleSheet("color: #aeb7c7; font-weight: 700;")
            top.addWidget(derived)
        layout.addLayout(top)

        controls = QHBoxLayout()
        controls.setSpacing(5)
        controls.addWidget(QLabel("Offset"))

        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(-120.0, 120.0)
        self.offset_spin.setDecimals(2)
        self.offset_spin.setSingleStep(0.5)
        self.offset_spin.setSuffix(" dB")
        self.offset_spin.setFixedWidth(92)
        self.offset_spin.blockSignals(True)
        self.offset_spin.setValue(layer.vertical_offset_db)
        self.offset_spin.blockSignals(False)
        self.offset_spin.valueChanged.connect(
            lambda value, layer_id=layer.id: on_offset_changed(layer_id, float(value))
        )
        controls.addWidget(self.offset_spin)

        controls.addWidget(QLabel("HRTF"))
        self.hrtf_combo = QComboBox()
        self.hrtf_combo.setMinimumWidth(120)
        self.hrtf_combo.blockSignals(True)
        for label, value in hrtf_options:
            if value == "__combined__" and not layer.is_combined:
                continue
            self.hrtf_combo.addItem(label, value)
        if layer.is_combined:
            index = self.hrtf_combo.findData("__combined__")
            self.hrtf_combo.setCurrentIndex(index if index >= 0 else 0)
            self.hrtf_combo.setEnabled(False)
        else:
            selected_path = str(layer.hrtf.path) if layer.hrtf is not None else ""
            index = self.hrtf_combo.findData(selected_path)
            self.hrtf_combo.setCurrentIndex(index if index >= 0 else 0)
        self.hrtf_combo.blockSignals(False)
        self.hrtf_combo.currentIndexChanged.connect(
            lambda _index, layer_id=layer.id: on_hrtf_changed(layer_id, self.hrtf_combo.currentData())
        )
        controls.addWidget(self.hrtf_combo, 1)
        layout.addLayout(controls)


class BrandPosterPreview(QWidget):
    """Scaled preview that uses the final BRAND export renderer."""

    def __init__(self, state: GraphState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            draw_brand_poster(painter, self._state, (self.width(), self.height()))
        finally:
            painter.end()


class GraphStage(QWidget):
    def __init__(self, graph: GraphWidget, state: GraphState, on_text_changed) -> None:
        super().__init__()
        self._graph = graph
        self._rounded_graph = RoundedViewportFrame(graph)
        self._graph_frame = AspectRatioWidget(
            self._rounded_graph,
            ratio=16.0 / 9.0,
        )
        self._graph_frame.setParent(self)
        self._brand_preview = BrandPosterPreview(state, self)
        self._brand_preview.hide()
        self._brand_mode = False
        self._on_text_changed = on_text_changed

        self.title_input = QLineEdit(state.export_text.title, self)
        self.fixture_input = QLineEdit(state.export_text.fixture, self)
        self.hrtf_note_input = QLineEdit(state.export_text.hrtf_note, self)
        for widget in (self.title_input, self.fixture_input, self.hrtf_note_input):
            widget.textChanged.connect(self._on_text_changed)
            widget.setObjectName("viewportTextInput")
        self.title_input.setObjectName("viewportTitleInput")
        self.fixture_input.setObjectName("viewportFixtureInput")
        self.hrtf_note_input.setObjectName("viewportFooterInput")

        self._default_placeholders = {
            "title": self.title_input.placeholderText(),
            "fixture": self.fixture_input.placeholderText(),
            "hrtf_note": self.hrtf_note_input.placeholderText(),
        }
        self._default_tooltips = {
            "fixture": self.fixture_input.toolTip(),
            "hrtf_note": self.hrtf_note_input.toolTip(),
        }

    @property
    def graph_frame(self) -> AspectRatioWidget:
        return self._graph_frame

    def set_brand_mode(self, enabled: bool) -> None:
        self._brand_mode = bool(enabled)
        self._graph_frame.setVisible(not self._brand_mode)
        self._brand_preview.setVisible(self._brand_mode)
        if enabled:
            self.title_input.setPlaceholderText("FREQUENCY RESPONSE & VARIATION")
            self.fixture_input.setPlaceholderText("MODEL | ANC ON | STANDARD | BLUETOOTH")
            self.fixture_input.setToolTip("Pipe-separated subtitle segments")
            self.hrtf_note_input.setPlaceholderText("B&K 5128, miniDSP EARS PRO(711)")
            self.hrtf_note_input.setToolTip("Footer center text")
        else:
            self.title_input.setPlaceholderText(self._default_placeholders["title"])
            self.fixture_input.setPlaceholderText(self._default_placeholders["fixture"])
            self.fixture_input.setToolTip(self._default_tooltips["fixture"])
            self.hrtf_note_input.setPlaceholderText(self._default_placeholders["hrtf_note"])
            self.hrtf_note_input.setToolTip(self._default_tooltips["hrtf_note"])
        self._brand_preview.update()

    def refresh_preview(self) -> None:
        if self._brand_mode:
            self._brand_preview.update()

    def start_wipe(
        self,
        *,
        entering_layer_ids: set[str] | None = None,
        entering_bounds: bool = False,
        exiting_layers: list[LayerSnapshot] | None = None,
        exiting_bounds: BoundsSnapshot | None = None,
    ) -> None:
        if self._brand_mode:
            self._graph.wipeProgress = 1.0
            self._brand_preview.update()
            return
        self._graph.start_data_wipe(
            entering_layer_ids=entering_layer_ids,
            entering_bounds=entering_bounds,
            exiting_layers=exiting_layers,
            exiting_bounds=exiting_bounds,
        )
        QTimer.singleShot(250, self._finish_stalled_wipe)

    def _finish_stalled_wipe(self) -> None:
        if self._graph.wipeProgress < 0.98:
            self._graph.wipeProgress = 1.0

    def resizeEvent(self, event) -> None:
        width = self.width()
        height = self.height()
        graph_height = max(240, height - 104)
        graph_width = min(width, int(round(graph_height * (16.0 / 9.0))))
        if graph_width > width:
            graph_width = width
            graph_height = int(round(graph_width / (16.0 / 9.0)))
        graph_left = (width - graph_width) // 2
        graph_top = 92
        self._graph_frame.setGeometry(QRect(graph_left, graph_top, graph_width, graph_height))
        self._brand_preview.setGeometry(QRect(graph_left, graph_top, graph_width, graph_height))

        left = graph_left + 36
        top_width = min(520, max(260, graph_width - 72))
        self.title_input.setGeometry(QRect(left, 8, top_width, 34))
        self.fixture_input.setGeometry(QRect(left, 46, min(420, top_width), 30))
        footer_top = min(height - 38, graph_top + graph_height + 12)
        self.hrtf_note_input.setGeometry(QRect(left, footer_top, min(420, graph_width - 72), 30))
        super().resizeEvent(event)


class CuratorWidget(QWidget):
    def __init__(
        self,
        events: ConsoleEventStore,
        theme: str = DARK,
        parent: QWidget | None = None,
        *,
        brand_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self._events = events
        self._theme = theme
        self._brand_mode = bool(brand_mode)
        self._custom_background = False
        self._manual_export_fields: set[str] = set()
        self._primary_metadata_layer_id: str | None = None
        self._applying_auto_text = False
        self._state = GraphState()
        self._state.background = brand_brand.BACKGROUND if self._brand_mode else theme_colors(theme)["plot_bg"]
        self._selected_layer_id: str | None = None
        self._hrtf_options: list[tuple[str, str]] = []
        self.setAcceptDrops(True)
        self._build_ui()
        self._configure_export_fields()
        self._refresh_hrtf_options()
        self._load_default_bounds()
        self._sync_ui()
        self.apply_theme(theme, brand_mode=self._brand_mode)
        self._redraw()

    @property
    def graph_state(self) -> GraphState:
        return self._state

    def _log(self, severity: str, message: str, **details) -> None:
        self._events.publish(severity, "curator", message, details)

    def _show_status(self, message: str) -> None:
        self._log("INFO", message)
        window = self.window()
        if hasattr(window, "statusBar"):
            window.statusBar().showMessage(message)

    def apply_theme(self, theme: str, brand_mode: bool = False) -> None:
        self._theme = theme
        self._brand_mode = bool(brand_mode)
        if not self._custom_background:
            self._state.background = (
                brand_brand.BACKGROUND if self._brand_mode else theme_colors(theme)["plot_bg"]
            )
        self._graph.apply_theme(theme, brand_mode=self._brand_mode)
        self._export_btn.setText("Export 4K PNG..." if self._brand_mode else "Export 1080p PNG...")
        self._brand_poster_box.setVisible(self._brand_mode)
        self._graph_stage.set_brand_mode(self._brand_mode)
        if self._brand_mode:
            self._apply_auto_export_text()
        self._redraw()

    def reset_background_to_theme(self) -> None:
        self._custom_background = False
        self._state.background = (
            brand_brand.BACKGROUND if self._brand_mode else theme_colors(self._theme)["plot_bg"]
        )
        self._redraw()
        self._log("INFO", "Graph background reset to theme", theme=self._theme)

    def add_curve(
        self,
        curve: CurveData,
        name: str,
        *,
        source_path: str | Path = "<fastgraph>",
        hrtf=None,
        normalize: bool = True,
        animate: bool = True,
    ) -> LayerState:
        copied = CurveData(
            kind=curve.kind,
            freqs=np.array(curve.freqs, dtype=float, copy=True),
            mag_db=_copy_optional(curve.mag_db),
            p10_db=_copy_optional(curve.p10_db),
            p25_db=_copy_optional(curve.p25_db),
            median_db=_copy_optional(curve.median_db),
            p75_db=_copy_optional(curve.p75_db),
            p90_db=_copy_optional(curve.p90_db),
            metadata=dict(curve.metadata),
        )
        colors = default_color_cycle(self._brand_mode)
        color = colors[len(self._state.layers) % len(colors)]
        layer = LayerState(
            curve=copied,
            source_path=Path(source_path),
            name=str(name),
            color=color,
            vertical_offset_db=normalization_offset_at_1khz(copied) if normalize else 0.0,
            hrtf=hrtf,
        )
        self._state.layers.append(layer)
        self._selected_layer_id = layer.id
        if self._primary_metadata_layer_id is None:
            self._primary_metadata_layer_id = layer.id
        if animate:
            self._sync_ui()
            self._redraw_with_wipe(entering_layer_ids={layer.id})
        else:
            self._refresh_metadata_source_combo()
        self._apply_auto_export_text()
        self._log(
            "INFO",
            "Layer added",
            layer=len(self._state.layers),
            name=layer.name,
            kind=layer.curve.kind,
            hrtf=layer.hrtf.name if layer.hrtf else None,
        )
        return layer

    def offset_layer_to_zero_at_1khz(self, layer: LayerState) -> None:
        """Normalize the displayed layer with an offset, preserving source arrays."""
        transformed = apply_layer_transform(layer)
        layer.vertical_offset_db += normalization_offset_at_1khz(transformed)
        self._sync_ui()
        self._redraw()
        self._log(
            "INFO",
            "Transferred layer offset to 0 dB at 1 kHz",
            layer=self._layer_number(layer),
            offset_db=layer.vertical_offset_db,
        )

    def import_files(
        self, paths: list[str | Path], *, show_errors: bool = True
    ) -> tuple[int, list[str]]:
        loaded = 0
        loaded_layer_ids: set[str] = set()
        failures: list[str] = []
        for path in paths:
            try:
                curve = parse_measurement_txt(path)
            except Exception as exc:
                failures.append(f"{Path(path).name}: {exc}")
                continue
            layer = self.add_curve(
                curve,
                Path(path).stem,
                source_path=path,
                normalize=True,
                animate=False,
            )
            loaded_layer_ids.add(layer.id)
            loaded += 1

        if loaded:
            self._selected_layer_id = self._state.layers[-1].id
            self._sync_ui()
            self._redraw_with_wipe(entering_layer_ids=loaded_layer_ids)
        if failures and show_errors:
            QMessageBox.warning(self, "Import Warnings", "\n".join(failures[:8]))
        self._show_status(f"Imported {loaded} file(s).")
        if failures:
            self._log("WARNING", "Some Curator imports failed", failures=failures)
        return loaded, failures

    def layer_at(self, number: int) -> LayerState:
        if number < 1 or number > len(self._state.layers):
            raise ValueError(f"Layer number must be between 1 and {len(self._state.layers)}.")
        return self._state.layers[number - 1]

    def layer_summary(self) -> str:
        if not self._state.layers:
            return "No Curator layers."
        lines = []
        for number, layer in enumerate(self._state.layers, 1):
            flags = ["visible" if layer.visible else "hidden", layer.curve.kind]
            if layer.is_combined:
                flags.append("combined")
            hrtf = layer.hrtf.name if layer.hrtf else "none"
            lines.append(
                f"{number}: {layer.name} [{', '.join(flags)}] "
                f"offset={layer.vertical_offset_db:g} dB hrtf={hrtf} color={layer.color}"
            )
        return "\n".join(lines)

    def remove_layer_number(self, number: int) -> None:
        layer = self.layer_at(number)
        self._selected_layer_id = layer.id
        self._remove_selected_layer()

    def set_layer_number_visible(self, number: int, visible: bool) -> None:
        self._set_layer_visible(self.layer_at(number).id, visible)

    def set_layer_number_offset(self, number: int, value: float) -> None:
        if not -120.0 <= value <= 120.0:
            raise ValueError("Layer offset must be between -120 and 120 dB.")
        self._set_layer_offset(self.layer_at(number).id, value)
        self._sync_ui()

    def set_layer_number_color(self, number: int, color: str) -> None:
        if not QColor.isValidColor(color) or not color.startswith("#"):
            raise ValueError("Color must be a valid hex color such as #15f4ee.")
        layer = self.layer_at(number)
        layer.color = QColor(color).name()
        self._sync_ui()
        self._redraw()
        self._log("INFO", "Layer color changed", layer=number, color=layer.color)

    def set_layer_number_hrtf(self, number: int, name: str) -> None:
        layer = self.layer_at(number)
        if layer.is_combined:
            raise ValueError("Combined variation layers cannot receive another HRTF.")
        requested = name.strip()
        if requested.lower() == "none":
            self._set_layer_hrtf(layer.id, "")
            self._sync_ui()
            return
        matches = [
            path for label, path in self._hrtf_options
            if path and path != "__combined__" and label.lower() == requested.lower()
        ]
        if len(matches) != 1:
            raise ValueError(f"Unknown HRTF: {name}")
        self._set_layer_hrtf(layer.id, matches[0])

    def combine_layer_numbers(self, numbers: list[int]) -> LayerState:
        layers = [self.layer_at(number) for number in numbers]
        if len({layer.id for layer in layers}) != len(layers):
            raise ValueError("Each layer may be listed only once.")
        return self._combine_layers(layers)

    def set_bounds_enabled(self, enabled: bool) -> None:
        self._bounds_enabled.setChecked(bool(enabled))

    def set_aspect_locked(self, enabled: bool) -> None:
        self._aspect_lock_enabled.setChecked(bool(enabled))

    def set_background(self, color: str) -> None:
        if not QColor.isValidColor(color) or not color.startswith("#"):
            raise ValueError("Background must be a valid hex color.")
        self._state.background = QColor(color).name()
        self._custom_background = True
        self._redraw()
        self._log("INFO", "Graph background changed", color=self._state.background)

    def set_export_text(self, field: str, value: str) -> None:
        widgets = {
            "title": self._graph_stage.title_input,
            "fixture": self._graph_stage.fixture_input,
            "footer": self._graph_stage.hrtf_note_input,
            "footer1": self._brand_footer1_edit,
            "footer2": self._brand_footer2_edit,
            "legend_bounds": self._brand_legend_bounds_edit,
            "legend_variation": self._brand_legend_variation_edit,
        }
        if field not in widgets:
            raise ValueError(
                "Text field must be title, fixture, footer, footer1, footer2, "
                "legend_bounds, or legend_variation."
            )
        self._manual_export_fields.add(field)
        widgets[field].setText(value)
        if field in ("footer1", "footer2", "legend_bounds", "legend_variation"):
            self._on_brand_text_changed()
        else:
            self._on_export_text_changed()
        self._set_export_field_state(field)
        self._update_metadata_status()
        self._graph_stage.refresh_preview()
        self._log("INFO", "Export text changed", field=field, value=value)

    def clear_layers(self) -> None:
        self._clear_layers()

    def reset_view(self) -> None:
        self._reset_view()

    def export_png(self, path: str | Path) -> Path:
        output = Path(path).expanduser()
        if not output.parent.exists():
            raise ValueError(f"Export directory does not exist: {output.parent}")
        if output.suffix.lower() != ".png":
            output = output.with_suffix(".png")
        self._on_export_text_changed()
        if self._brand_mode:
            warnings = brand_export_warnings(self._state)
            if warnings:
                self._log(
                    "WARNING",
                    "BRAND export text is below the preferred readable size",
                    warnings=warnings,
                )
                self._show_status("BRAND export has a text-size warning.")
            export_graph_image(self._state, output, size=(3840, 2160), brand_mode=True)
        else:
            export_graph_image(self._state, output, size=(1920, 1080))
        self._show_status(f"Exported Curator PNG: {output}")
        self._log("INFO", "PNG exported", path=str(output))
        return output

    def set_y_limits(self, y_min: float, y_max: float) -> None:
        if y_max <= y_min:
            raise ValueError("Upper dB limit must be greater than lower dB limit.")
        self._state.y_min = float(y_min)
        self._state.y_max = float(y_max)
        self._y_min_spin.blockSignals(True)
        self._y_max_spin.blockSignals(True)
        self._y_min_spin.setValue(self._state.y_min)
        self._y_max_spin.setValue(self._state.y_max)
        self._y_min_spin.blockSignals(False)
        self._y_max_spin.blockSignals(False)
        self._redraw()
        self._log("INFO", "Graph limits changed", y_min=self._state.y_min, y_max=self._state.y_max)

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)
        self._splitter = splitter

        self._graph = GraphWidget()
        self._graph_stage = GraphStage(self._graph, self._state, self._on_export_text_changed)
        self._graph_stage.setProperty("surfaceLevel", "viewport")
        self._graph_frame = self._graph_stage.graph_frame
        self._graph_frame.setMinimumHeight(430)
        self._graph_stage.setMinimumHeight(540)
        splitter.addWidget(self._graph_stage)
        controls_scroll = QScrollArea()
        controls_scroll.setObjectName("curatorControlsScroll")
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        controls_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        controls_scroll.setMinimumWidth(410)
        controls_scroll.setWidget(self._build_panel())
        splitter.addWidget(controls_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([1100, 500])


    def _build_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("controlPanel")
        panel.setProperty("surfaceLevel", "panel")
        panel.setMinimumWidth(390)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._data_box = QGroupBox("Data")
        import_box = self._data_box
        import_layout = QVBoxLayout(import_box)
        import_layout.setContentsMargins(8, 8, 8, 8)
        import_layout.setSpacing(5)
        add_btn = QPushButton("Add TXT Files...")
        add_btn.clicked.connect(self._choose_import_files)
        import_layout.addWidget(add_btn)
        self._layer_list = QListWidget()
        self._layer_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._layer_list.currentItemChanged.connect(self._on_layer_selected)
        self._layer_list.itemSelectionChanged.connect(self._sync_combine_button)
        import_layout.addWidget(self._layer_list, 1)
        row = QHBoxLayout()
        self._combine_btn = QPushButton("Create Combined Variation")
        self._combine_btn.clicked.connect(self._create_combined_variation)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_selected_layer)
        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(self._clear_layers)
        row.addWidget(self._combine_btn)
        row.addWidget(remove_btn)
        row.addWidget(clear_btn)
        import_layout.addLayout(row)
        layout.addWidget(import_box, 1)

        self._view_box = QGroupBox("View")
        view_box = self._view_box
        view_form = QFormLayout(view_box)
        view_form.setContentsMargins(8, 8, 8, 8)
        view_form.setVerticalSpacing(5)
        self._y_min_spin = QDoubleSpinBox()
        self._y_max_spin = QDoubleSpinBox()
        for spin in (self._y_min_spin, self._y_max_spin):
            spin.setRange(-180.0, 180.0)
            spin.setDecimals(1)
            spin.setSingleStep(1.0)
            spin.setSuffix(" dB")
            spin.valueChanged.connect(self._on_y_limit_changed)
        view_form.addRow("Lower", self._y_min_spin)
        view_form.addRow("Upper", self._y_max_spin)
        bg_btn = QPushButton("Choose Background")
        bg_btn.clicked.connect(self._choose_background)
        view_form.addRow("Background", bg_btn)
        self._bounds_enabled = ToggleSwitch()
        self._bounds_enabled.stateChanged.connect(self._on_bounds_enabled_changed)
        view_form.addRow("Preference Bounds", self._bounds_enabled)
        self._aspect_lock_enabled = ToggleSwitch()
        self._aspect_lock_enabled.stateChanged.connect(self._on_aspect_lock_changed)
        view_form.addRow("25 dB/decade", self._aspect_lock_enabled)
        self._smoothing_combo = QComboBox()
        for fraction in SMOOTHING_OPTIONS:
            self._smoothing_combo.addItem(f"1/{fraction}", fraction)
        self._smoothing_combo.currentIndexChanged.connect(self._on_smoothing_changed)
        view_form.addRow("Smoothing", self._smoothing_combo)
        self._show_names_enabled = ToggleSwitch()
        self._show_names_enabled.stateChanged.connect(self._on_show_names_changed)
        view_form.addRow("Show Names", self._show_names_enabled)
        reset_btn = QPushButton("Reset View")
        reset_btn.clicked.connect(self._reset_view)
        view_form.addRow("Reset", reset_btn)
        self._export_btn = QPushButton("Export 1080p PNG...")
        self._export_btn.setObjectName("exportButton")
        self._export_btn.clicked.connect(self._choose_export_path)
        view_form.addRow("Export", self._export_btn)
        layout.addWidget(view_box, 0)

        self._brand_poster_box = QGroupBox("BRAND Poster Text")
        self._brand_poster_box.setObjectName("brandPosterBox")
        brand_form = QFormLayout(self._brand_poster_box)
        self._brand_form = brand_form
        brand_form.setContentsMargins(12, 12, 12, 12)
        brand_form.setHorizontalSpacing(12)
        brand_form.setVerticalSpacing(8)
        brand_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        brand_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        brand_form.setFormAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        brand_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._brand_metadata_source_combo = QComboBox()
        self._brand_metadata_source_combo.currentIndexChanged.connect(
            self._on_metadata_source_changed
        )
        brand_form.addRow("Metadata source", self._brand_metadata_source_combo)

        self._brand_fill_metadata_btn = QPushButton("Fill from Metadata")
        self._brand_fill_metadata_btn.clicked.connect(self._fill_from_metadata)
        brand_form.addRow("Automatic text", self._brand_fill_metadata_btn)

        self._brand_metadata_status = QLabel("Field status: All fields are automatic.")
        self._brand_metadata_status.setObjectName("brandMetadataStatus")
        self._brand_metadata_status.setWordWrap(False)
        self._brand_metadata_status.setProperty("tone", "muted")
        brand_form.addRow(self._brand_metadata_status)

        status = brand_font_status()
        if status.uses_fallback:
            missing = ", ".join(status.missing_families)
            font_text = (
                f"Missing fonts: {missing}. Fallbacks: {status.heading_family} headings, "
                f"{status.mono_family} body."
            )
        else:
            font_text = (
                f"Fonts: {status.heading_family} headings, "
                f"{status.mono_family} body text."
            )
        self._brand_font_status = QLabel(font_text)
        self._brand_font_status.setObjectName("brandFontStatus")
        self._brand_font_status.setWordWrap(True)
        self._brand_font_status.setMinimumHeight(30 if not status.uses_fallback else 48)
        self._brand_font_status.setProperty(
            "tone",
            "warning" if status.uses_fallback else "muted",
        )
        brand_form.addRow(self._brand_font_status)

        self._brand_footer1_edit = QLineEdit(self._state.export_text.brand_footer_left_1)
        self._brand_footer2_edit = QLineEdit(self._state.export_text.brand_footer_left_2)
        self._brand_legend_bounds_edit = QLineEdit(self._state.export_text.brand_legend_bounds_label)
        self._brand_legend_variation_edit = QLineEdit(self._state.export_text.brand_legend_variation_label)
        self._brand_footer1_edit.textChanged.connect(self._on_brand_text_changed)
        self._brand_footer2_edit.textChanged.connect(self._on_brand_text_changed)
        self._brand_legend_bounds_edit.textChanged.connect(self._on_brand_text_changed)
        self._brand_legend_variation_edit.textChanged.connect(self._on_brand_text_changed)
        brand_form.addRow("Footer line 1", self._brand_footer1_edit)
        brand_form.addRow("Footer line 2", self._brand_footer2_edit)
        brand_form.addRow("Legend: bounds", self._brand_legend_bounds_edit)
        brand_form.addRow("Legend: variation", self._brand_legend_variation_edit)
        self._brand_poster_box.setVisible(False)
        layout.addWidget(self._brand_poster_box, 0)
        return panel

    def _configure_export_fields(self) -> None:
        self._export_field_widgets: dict[str, QLineEdit] = {
            "title": self._graph_stage.title_input,
            "fixture": self._graph_stage.fixture_input,
            "footer": self._graph_stage.hrtf_note_input,
            "footer1": self._brand_footer1_edit,
            "footer2": self._brand_footer2_edit,
            "legend_bounds": self._brand_legend_bounds_edit,
            "legend_variation": self._brand_legend_variation_edit,
        }
        for field, editor in self._export_field_widgets.items():
            editor.textEdited.connect(
                lambda _text, field_name=field: self._mark_export_field_manual(field_name)
            )
            editor.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            editor.customContextMenuRequested.connect(
                lambda position, field_name=field, widget=editor: self._show_export_field_menu(
                    field_name,
                    widget,
                    position,
                )
            )
            self._set_export_field_state(field)

    def _show_export_field_menu(self, field: str, editor: QLineEdit, position) -> None:
        menu = editor.createStandardContextMenu()
        menu.addSeparator()
        reset_action = menu.addAction("Reset to Metadata")
        reset_action.triggered.connect(
            lambda _checked=False, field_name=field: self._reset_export_field(field_name)
        )
        menu.exec(editor.mapToGlobal(position))

    def _mark_export_field_manual(self, field: str) -> None:
        if self._applying_auto_text:
            return
        self._manual_export_fields.add(field)
        self._set_export_field_state(field)
        self._update_metadata_status()
        self._graph_stage.refresh_preview()

    def _reset_export_field(self, field: str) -> None:
        self._manual_export_fields.discard(field)
        self._apply_auto_export_text(fields={field})

    def _fill_from_metadata(self) -> None:
        self._manual_export_fields.clear()
        self._apply_auto_export_text()
        self._show_status("BRAND poster text filled from metadata.")

    def _set_export_field_state(self, field: str) -> None:
        editor = self._export_field_widgets.get(field)
        if editor is None:
            return
        state = "manual" if field in self._manual_export_fields else "auto"
        editor.setProperty("metadataState", state)
        editor.setToolTip(
            f"{'Manually edited' if state == 'manual' else 'Filled from metadata when available'}. "
            "Right-click to reset this field."
        )
        editor.style().unpolish(editor)
        editor.style().polish(editor)

    def _update_metadata_status(self) -> None:
        labels = {
            "title": "Title",
            "fixture": "Headphone details",
            "footer": "Center footer",
            "footer1": "Footer line 1",
            "footer2": "Footer line 2",
            "legend_bounds": "Bounds legend",
            "legend_variation": "Variation legend",
        }
        manual = [labels[field] for field in labels if field in self._manual_export_fields]
        if manual:
            automatic_count = len(labels) - len(manual)
            self._brand_metadata_status.setText(
                f"Field status: {len(manual)} manual, {automatic_count} automatic."
            )
            self._brand_metadata_status.setToolTip(
                "Manual fields: " + ", ".join(manual) + "."
            )
        else:
            self._brand_metadata_status.setText("Field status: All fields are automatic.")
            self._brand_metadata_status.setToolTip(
                "Each field updates from the selected metadata source."
            )
        for field in self._export_field_widgets:
            self._set_export_field_state(field)

    def _primary_metadata_layer(self) -> LayerState | None:
        layer = self._layer_by_id(self._primary_metadata_layer_id or "")
        if layer is not None:
            return layer
        layer = next(
            (item for item in self._state.layers if metadata_has_identity(item.curve.metadata)),
            self._state.layers[0] if self._state.layers else None,
        )
        self._primary_metadata_layer_id = layer.id if layer is not None else None
        return layer

    def _refresh_metadata_source_combo(self) -> None:
        if not hasattr(self, "_brand_metadata_source_combo"):
            return
        primary = self._primary_metadata_layer()
        self._brand_metadata_source_combo.blockSignals(True)
        self._brand_metadata_source_combo.clear()
        if not self._state.layers:
            self._brand_metadata_source_combo.addItem("No layers", "")
            self._brand_metadata_source_combo.setEnabled(False)
        else:
            self._brand_metadata_source_combo.setEnabled(True)
            for number, layer in enumerate(self._state.layers, 1):
                self._brand_metadata_source_combo.addItem(
                    f"Layer {number}: {layer.name}",
                    layer.id,
                )
            index = self._brand_metadata_source_combo.findData(primary.id if primary else "")
            self._brand_metadata_source_combo.setCurrentIndex(max(0, index))
        self._brand_metadata_source_combo.blockSignals(False)

    def _on_metadata_source_changed(self, _index: int) -> None:
        layer_id = str(self._brand_metadata_source_combo.currentData() or "")
        self._primary_metadata_layer_id = layer_id or None
        self._apply_auto_export_text()

    def _apply_auto_export_text(self, fields: set[str] | None = None) -> None:
        if not self._brand_mode or not hasattr(self, "_export_field_widgets"):
            return
        primary = self._primary_metadata_layer()
        values = automatic_export_values(self._state, primary)
        selected_fields = fields or set(values)
        self._applying_auto_text = True
        try:
            for field in selected_fields:
                if field in self._manual_export_fields:
                    continue
                value = values.get(field, "")
                editor = self._export_field_widgets.get(field)
                if editor is None:
                    continue
                if editor.text() != value:
                    editor.setText(value)
            self._on_export_text_changed()
            self._on_brand_text_changed()
        finally:
            self._applying_auto_text = False
        self._update_metadata_status()
        self._graph_stage.refresh_preview()

    def _sync_ui(self) -> None:
        self._layer_list.blockSignals(True)
        self._layer_list.clear()
        selected_row = -1
        for index, layer in enumerate(self._state.layers):
            item = QListWidgetItem()
            item.setData(256, layer.id)
            row = LayerListRow(
                layer,
                self._hrtf_options,
                self._set_layer_visible,
                self._choose_layer_color,
                self._set_layer_name,
                self._set_layer_offset,
                self._set_layer_hrtf,
            )
            item.setSizeHint(row.sizeHint())
            self._layer_list.addItem(item)
            self._layer_list.setItemWidget(item, row)
            if layer.id == self._selected_layer_id:
                selected_row = index
        if selected_row >= 0:
            self._layer_list.setCurrentRow(selected_row)
        self._layer_list.blockSignals(False)

        self._y_min_spin.blockSignals(True)
        self._y_max_spin.blockSignals(True)
        self._y_min_spin.setValue(self._state.y_min)
        self._y_max_spin.setValue(self._state.y_max)
        self._y_min_spin.blockSignals(False)
        self._y_max_spin.blockSignals(False)

        self._sync_bounds_controls()
        self._sync_aspect_lock_controls()
        self._smoothing_combo.blockSignals(True)
        self._smoothing_combo.setCurrentIndex(
            max(0, self._smoothing_combo.findData(self._state.smoothing_fraction))
        )
        self._smoothing_combo.blockSignals(False)
        self._show_names_enabled.blockSignals(True)
        self._show_names_enabled.setChecked(self._state.show_layer_names)
        self._show_names_enabled.blockSignals(False)
        self._sync_combine_button()
        self._refresh_metadata_source_combo()

    def _sync_bounds_controls(self) -> None:
        self._bounds_enabled.blockSignals(True)
        self._bounds_enabled.setChecked(self._state.bounds.enabled)
        self._bounds_enabled.blockSignals(False)

    def _sync_aspect_lock_controls(self) -> None:
        self._aspect_lock_enabled.blockSignals(True)
        self._aspect_lock_enabled.setChecked(self._state.aspect_locked_25db)
        self._aspect_lock_enabled.blockSignals(False)

    def _refresh_hrtf_options(self) -> None:
        self._hrtf_options = [
            ("None", ""),
            ("Combined (HRTF locked)", "__combined__"),
        ]
        for path in sorted(HRTF_DIR.glob("*.txt")):
            self._hrtf_options.append((path.stem, str(path)))

    def _load_default_bounds(self) -> None:
        if not (UPPER_BOUNDS_PATH.exists() and LOWER_BOUNDS_PATH.exists()):
            self._state.bounds = PreferenceBounds(enabled=False)
            return
        try:
            self._state.bounds = load_preference_bounds(
                UPPER_BOUNDS_PATH,
                LOWER_BOUNDS_PATH,
            )
            self._state.bounds.enabled = False
        except Exception as exc:
            self._state.bounds = PreferenceBounds(enabled=False)
            self._log("ERROR", "Preference bounds could not load", error=str(exc))
            self._show_status(f"Preference bounds could not load: {exc}")

    def _redraw(self) -> None:
        self._graph.redraw(self._state)
        self._graph_stage.refresh_preview()

    def _redraw_with_wipe(
        self,
        *,
        entering_layer_ids: set[str] | None = None,
        entering_bounds: bool = False,
        exiting_layers: list[LayerSnapshot] | None = None,
        exiting_bounds: BoundsSnapshot | None = None,
    ) -> None:
        self._redraw()
        self._graph_stage.start_wipe(
            entering_layer_ids=entering_layer_ids,
            entering_bounds=entering_bounds,
            exiting_layers=exiting_layers,
            exiting_bounds=exiting_bounds,
        )

    def _selected_layer(self) -> LayerState | None:
        for layer in self._state.layers:
            if layer.id == self._selected_layer_id:
                return layer
        return None

    def _choose_import_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import FR or Variation TXT",
            "",
            "Text Files (*.txt);;All Files (*)",
        )
        if paths:
            self.import_files(paths)

    def _on_layer_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        self._selected_layer_id = current.data(256) if current else None
        self._sync_combine_button()

    def _remove_selected_layer(self) -> None:
        layer = self._selected_layer()
        if layer is None:
            return
        number = self._layer_number(layer)
        exiting = self._graph.snapshot_visible_layer(layer.id)
        self._state.layers = [item for item in self._state.layers if item.id != layer.id]
        if self._primary_metadata_layer_id == layer.id:
            self._primary_metadata_layer_id = None
        self._selected_layer_id = self._state.layers[-1].id if self._state.layers else None
        self._sync_ui()
        self._apply_auto_export_text()
        self._redraw_with_wipe(exiting_layers=[exiting] if exiting is not None else None)
        self._log("INFO", "Layer removed", layer=number, name=layer.name)

    def _clear_layers(self) -> None:
        count = len(self._state.layers)
        exiting_layers = [
            snapshot
            for layer in self._state.layers
            if (snapshot := self._graph.snapshot_visible_layer(layer.id)) is not None
        ]
        self._state.layers.clear()
        self._selected_layer_id = None
        self._primary_metadata_layer_id = None
        self._manual_export_fields.clear()
        self._sync_ui()
        self._apply_auto_export_text()
        self._redraw_with_wipe(exiting_layers=exiting_layers)
        self._log("INFO", "All layers cleared", count=count)

    def _set_layer_visible(self, layer_id: str, visible: bool) -> None:
        layer = next((item for item in self._state.layers if item.id == layer_id), None)
        if layer is None:
            return
        exiting = self._graph.snapshot_visible_layer(layer_id) if not visible else None
        layer.visible = visible
        self._sync_ui()
        self._apply_auto_export_text()
        if visible:
            self._redraw_with_wipe(entering_layer_ids={layer_id})
        else:
            self._redraw_with_wipe(exiting_layers=[exiting] if exiting is not None else None)
        self._log(
            "INFO",
            "Layer visibility changed",
            layer=self._layer_number(layer),
            visible=visible,
        )

    def _selected_layers(self) -> list[LayerState]:
        selected_ids = {
            item.data(256)
            for item in self._layer_list.selectedItems()
            if item.data(256)
        }
        return [layer for layer in self._state.layers if layer.id in selected_ids]

    def _sync_combine_button(self) -> None:
        if not hasattr(self, "_combine_btn"):
            return
        self._combine_btn.setEnabled(can_combine_layers(self._selected_layers()))

    def _create_combined_variation(self) -> None:
        selected = self._selected_layers()
        if not can_combine_layers(selected):
            QMessageBox.warning(
                self,
                "Cannot Combine",
                "Select at least two complete variation layers to create a combined variation.",
            )
            return
        try:
            self._combine_layers(selected)
        except Exception as exc:
            self._log("ERROR", "Combined variation failed", error=str(exc))
            QMessageBox.warning(self, "Cannot Combine", str(exc))
        return

    def _combine_layers(self, selected: list[LayerState]) -> LayerState:
        if not can_combine_layers(selected):
            raise ValueError("Select at least two complete variation layers to combine.")
        combined_curve = combine_variation_layers(selected)

        exiting_layers = [
            snapshot
            for layer in selected
            if (snapshot := self._graph.snapshot_visible_layer(layer.id)) is not None
        ]
        for layer in selected:
            layer.visible = False

        existing = sum(1 for layer in self._state.layers if layer.is_combined)
        colors = default_color_cycle(self._brand_mode)
        color = colors[len(self._state.layers) % len(colors)]
        combined = LayerState(
            curve=combined_curve,
            source_path=Path("<combined>"),
            name=f"Combined Variation {existing + 1}",
            color=color,
            vertical_offset_db=0.0,
            hrtf=None,
            is_combined=True,
            source_layer_ids=[layer.id for layer in selected],
        )
        self._state.layers.append(combined)
        self._selected_layer_id = combined.id
        self._sync_ui()
        self._apply_auto_export_text()
        for index in range(self._layer_list.count()):
            item = self._layer_list.item(index)
            item.setSelected(item.data(256) == combined.id)
        self._redraw_with_wipe(entering_layer_ids={combined.id}, exiting_layers=exiting_layers)
        self._log(
            "INFO",
            "Combined variation created",
            layer=len(self._state.layers),
            sources=[self._layer_number(layer) for layer in selected],
        )
        return combined

    def _choose_layer_color(self, layer_id: str, button: QPushButton) -> None:
        layer = self._layer_by_id(layer_id)
        if layer is None:
            return
        if self._brand_mode:
            menu = QMenu(self)
            for hex_color in brand_brand.brand_menu_colors(layer.color):
                pixmap = QPixmap(14, 14)
                pixmap.fill(QColor(hex_color))
                label = hex_color
                if hex_color.lower() == layer.color.lower():
                    label = f"{hex_color} (current)"
                action = menu.addAction(QIcon(pixmap), label)
                action.triggered.connect(
                    lambda _checked=False, c=hex_color: self._apply_layer_color(layer, c)
                )
            menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
            return
        color = QColorDialog.getColor(QColor(layer.color), self, "Choose Layer Color")
        if not color.isValid():
            return
        self._apply_layer_color(layer, color.name())

    def _apply_layer_color(self, layer: LayerState, hex_color: str) -> None:
        layer.color = hex_color
        self._sync_ui()
        self._redraw()
        self._log(
            "INFO", "Layer color changed",
            layer=self._layer_number(layer), color=layer.color,
        )

    def _set_layer_name(self, layer_id: str, value: str, editor: QLineEdit | None = None) -> None:
        layer = self._layer_by_id(layer_id)
        if layer is None:
            return
        name = value.strip()
        if not name:
            if editor is not None:
                editor.setText(layer.name)
            return
        if name == layer.name:
            return
        layer.name = name
        self._redraw()
        self._log("INFO", "Layer renamed", layer=self._layer_number(layer), name=name)

    def _set_layer_offset(self, layer_id: str, value: float) -> None:
        layer = self._layer_by_id(layer_id)
        if layer is None:
            return
        layer.vertical_offset_db = float(value)
        self._redraw()
        self._log(
            "INFO",
            "Layer offset changed",
            layer=self._layer_number(layer),
            offset_db=layer.vertical_offset_db,
        )

    def _set_layer_hrtf(self, layer_id: str, path: str) -> None:
        layer = self._layer_by_id(layer_id)
        if layer is None:
            return
        if layer.is_combined:
            return
        if not path or path == "__combined__":
            layer.hrtf = None
            self._apply_auto_export_text()
            self._redraw()
            self._log(
                "INFO", "Layer HRTF changed",
                layer=self._layer_number(layer), hrtf=None,
            )
            return
        try:
            layer.hrtf = load_hrtf_txt(path)
        except Exception as exc:
            self._log(
                "ERROR", "Layer HRTF failed",
                layer=self._layer_number(layer), error=str(exc),
            )
            QMessageBox.warning(self, "HRTF Error", str(exc))
            layer.hrtf = None
            self._sync_ui()
            return
        self._sync_ui()
        self._apply_auto_export_text()
        self._redraw()
        self._log(
            "INFO", "Layer HRTF changed",
            layer=self._layer_number(layer), hrtf=layer.hrtf.name,
        )

    def _clear_layer_hrtf(self) -> None:
        layer = self._selected_layer()
        if layer is None:
            return
        layer.hrtf = None
        self._sync_ui()
        self._apply_auto_export_text()
        self._redraw()

    def _layer_by_id(self, layer_id: str) -> LayerState | None:
        return next((item for item in self._state.layers if item.id == layer_id), None)

    def _layer_number(self, layer: LayerState) -> int:
        return next(
            index for index, candidate in enumerate(self._state.layers, 1)
            if candidate.id == layer.id
        )

    def _on_y_limit_changed(self, _value: float) -> None:
        try:
            self.set_y_limits(self._y_min_spin.value(), self._y_max_spin.value())
        except ValueError:
            self._show_status("Upper dB limit must be greater than lower limit.")

    def _choose_background(self) -> None:
        color = QColorDialog.getColor(QColor(self._state.background), self, "Choose Background")
        if not color.isValid():
            return
        self._state.background = color.name()
        self._custom_background = True
        self._redraw()
        self._log("INFO", "Graph background changed", color=self._state.background)

    def _on_bounds_enabled_changed(self, _state: int) -> None:
        enabling = self._bounds_enabled.isChecked()
        exiting_bounds = self._graph.snapshot_bounds() if not enabling else None
        if self._bounds_enabled.isChecked() and (
            self._state.bounds.upper is None or self._state.bounds.lower is None
        ):
            self._load_default_bounds()
        self._state.bounds.enabled = enabling
        self._sync_bounds_controls()
        self._apply_auto_export_text()
        self._redraw_with_wipe(entering_bounds=enabling, exiting_bounds=exiting_bounds)
        self._log("INFO", "Preference bounds changed", enabled=enabling)

    def _on_aspect_lock_changed(self, _state: int) -> None:
        self._state.aspect_locked_25db = self._aspect_lock_enabled.isChecked()
        self._redraw()
        self._log("INFO", "25 dB/decade aspect changed", enabled=self._state.aspect_locked_25db)

    def _on_smoothing_changed(self, _index: int) -> None:
        self._state.smoothing_fraction = int(self._smoothing_combo.currentData() or 48)
        self._redraw()
        self._log("INFO", "Curator smoothing changed", fraction=self._state.smoothing_fraction)

    def _on_show_names_changed(self, _state: int) -> None:
        self._state.show_layer_names = self._show_names_enabled.isChecked()
        self._redraw()
        self._log("INFO", "Layer names changed", visible=self._state.show_layer_names)

    def _reset_view(self) -> None:
        self._state.aspect_locked_25db = True
        self._state.smoothing_fraction = 48
        self._state.show_layer_names = True
        self.set_y_limits(DEFAULT_Y_MIN, DEFAULT_Y_MAX)
        self.reset_background_to_theme()
        self._sync_aspect_lock_controls()
        self._sync_ui()
        self._log("INFO", "View reset")

    def dragEnterEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasUrls() and any(url.isLocalFile() for url in mime.urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        local_paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        txt_paths = [path for path in local_paths if path.suffix.lower() == ".txt"]
        unsupported = [path.name for path in local_paths if path.suffix.lower() != ".txt"]
        loaded, failures = self.import_files(txt_paths, show_errors=False) if txt_paths else (0, [])
        messages = [f"{name}: unsupported file type" for name in unsupported] + failures
        if messages:
            QMessageBox.warning(self, "Import Warnings", "\n".join(messages[:8]))
        if loaded or local_paths:
            event.acceptProposedAction()
        else:
            event.ignore()

    def _on_export_text_changed(self) -> None:
        previous = self._state.export_text
        self._state.export_text = ExportText(
            title=self._graph_stage.title_input.text(),
            fixture=self._graph_stage.fixture_input.text(),
            hrtf_note=self._graph_stage.hrtf_note_input.text(),
            notes=previous.notes,
            brand_footer_left_1=previous.brand_footer_left_1,
            brand_footer_left_2=previous.brand_footer_left_2,
            brand_legend_bounds_label=previous.brand_legend_bounds_label,
            brand_legend_variation_label=previous.brand_legend_variation_label,
        )
        if hasattr(self, "_graph_stage"):
            self._graph_stage.refresh_preview()

    def _on_brand_text_changed(self, _value: str = "") -> None:
        previous = self._state.export_text
        self._state.export_text = ExportText(
            title=previous.title,
            fixture=previous.fixture,
            hrtf_note=previous.hrtf_note,
            notes=previous.notes,
            brand_footer_left_1=self._brand_footer1_edit.text(),
            brand_footer_left_2=self._brand_footer2_edit.text(),
            brand_legend_bounds_label=self._brand_legend_bounds_edit.text(),
            brand_legend_variation_label=self._brand_legend_variation_edit.text(),
        )
        if hasattr(self, "_graph_stage"):
            self._graph_stage.refresh_preview()

    def _choose_export_path(self) -> None:
        self._on_export_text_changed()
        default_name = "curator_export_4k.png" if self._brand_mode else "curator_export.png"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Curator PNG",
            default_name,
            "PNG Image (*.png)",
        )
        if not path:
            return
        if self._brand_mode:
            warnings = brand_export_warnings(self._state)
            if warnings:
                QMessageBox.warning(
                    self,
                    "BRAND Export Text Warning",
                    "\n".join(warnings),
                )
        try:
            self.export_png(path)
        except Exception as exc:
            self._log("ERROR", "PNG export failed", path=path, error=str(exc))
            QMessageBox.warning(self, "Export Error", str(exc))
            return
        self._show_status(f"Exported Curator PNG: {path}")


def _copy_optional(values: np.ndarray | None) -> np.ndarray | None:
    if values is None:
        return None
    return np.array(values, dtype=float, copy=True)
