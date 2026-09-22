from pathlib import Path

import numpy as np
import pyqtgraph as pg
import pytest
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import (
    QColorDialog,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

import dms.curator.export_image as export_image_module
import dms.ui.curator_widget as main_window_module
from dms.curator.export_image import (
    ACCENT_COLOR,
    DITHER_FOOTER_HEIGHT,
    PLOT_INSET_BOTTOM,
    PLOT_INSET_LEFT,
    PLOT_INSET_RIGHT,
    PLOT_INSET_TOP,
    _curve_path,
    _draw_legend,
    _y_for_db,
    aligned_bounds,
    aspect_locked_rect,
    export_graph_image,
    fit_title,
)
from dms.curator.export_brand import brand_display_color
from dms.curator.models import CurveData, GraphState, LayerState, PreferenceBounds
from dms.graph_display import EXPORT_FREQUENCY_MARKERS, FREQUENCY_MARKERS, FREQUENCY_TICKS
from dms.processing import VariationBand
from dms.style_tokens import DITHER_TOKENS, BRAND_TOKENS, THEME_DEFINITIONS, tokens_for
from dms.theme import (
    DARK,
    DITHER,
    FASTGRAPH_95,
    FASTGRAPH_95_DARK,
    HACKERMAN_95,
    ensure_graph_color,
    theme_trace_palette,
)
from dms.ui.dual_plot_widget import DualPlotWidget


def test_main_window_imports_multiple_files_normalizes_and_locks_viewport(
    make_curator, qapp, tmp_path: Path
) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("100 1\n1000 5\n", encoding="utf-8")
    second.write_text(
        "* Export Type: Variation Band\n100 -2 -1 -3 1 2\n1000 -1 0 -3 2 3\n",
        encoding="utf-8",
    )

    window = make_curator()
    window.import_files([first, second])

    assert len(window.graph_state.layers) == 2
    assert window.graph_state.layers[0].vertical_offset_db == -5.0
    assert window.graph_state.layers[1].vertical_offset_db == 3.0
    assert window.graph_state.layers[0].curve.mag_db[1] == 5.0
    view_box = window._graph.getPlotItem().getViewBox()
    assert view_box.state["mouseEnabled"] == [False, False]
    assert window._graph_frame.ratio == 16.0 / 9.0
    assert window._graph_stage._rounded_graph.radius == 10


def test_curator_preview_curves_have_no_glow_items(make_curator, qapp) -> None:
    window = make_curator()
    fr = CurveData(
        kind="fr",
        freqs=np.array([100.0, 1000.0]),
        mag_db=np.array([1.0, 0.0]),
    )
    window.add_curve(fr, "FR", animate=False)
    window._redraw()

    curves = [item for item in window._graph._items if isinstance(item, pg.PlotDataItem)]
    assert len(curves) == 1
    assert curves[0].opts["pen"].widthF() == pytest.approx(2.0)
    window.close()
    assert not hasattr(window, "_visible_check")
    assert not hasattr(window._graph, "_draw_reference_lines")
    assert window.graph_state.aspect_locked_25db is True

    window.set_y_limits(-30.0, 10.0)
    assert window.graph_state.y_min == -30.0
    assert window.graph_state.y_max == 10.0


def test_fastgraph95_curves_use_the_fine_step_display_renderer(make_curator, qapp) -> None:
    window = make_curator(theme=FASTGRAPH_95)
    fr = CurveData(
        kind="fr",
        freqs=np.array([100.0, 1000.0]),
        mag_db=np.array([1.0, 0.0]),
    )
    window.add_curve(fr, "FR", animate=False)
    window._redraw()
    curve = next(item for item in window._graph._items if isinstance(item, pg.PlotDataItem))
    assert curve.opts["antialias"] is True
    np.testing.assert_array_equal(curve.xData, [100.0, 1000.0, 1000.0])
    np.testing.assert_array_equal(curve.yData, [1.0, 1.0, 0.0])

    window.apply_theme(DARK)
    curve = next(item for item in window._graph._items if isinstance(item, pg.PlotDataItem))
    assert curve.opts["antialias"] is True
    np.testing.assert_array_equal(curve.xData, [100.0, 1000.0])
    np.testing.assert_array_equal(curve.yData, [1.0, 0.0])
    window.close()


def test_hackerman95_new_layers_start_green_then_use_distinct_neon_colors(
    make_curator, qapp
) -> None:
    window = make_curator(theme=HACKERMAN_95)
    curve = CurveData(
        kind="fr",
        freqs=np.array([100.0, 1000.0]),
        mag_db=np.array([1.0, 0.0]),
    )

    first = window.add_curve(curve, "First", animate=False)
    second = window.add_curve(curve, "Second", animate=False)

    assert first.color == "#39FF14"
    assert second.color == "#00E5FF"
    assert first.color != second.color
    window.close()


def test_color_dialog_standard_swatches_follow_active_theme_and_background(
    make_curator, qapp, monkeypatch
) -> None:
    standard_colors: dict[int, str] = {}
    opened_palettes: list[dict[int, str]] = []

    def fake_set_standard_color(index: int, color: QColor) -> None:
        standard_colors[index] = color.name()

    def fake_get_color(*_args, **_kwargs) -> QColor:
        opened_palettes.append(dict(standard_colors))
        return QColor()

    monkeypatch.setattr(
        QColorDialog,
        "setStandardColor",
        staticmethod(fake_set_standard_color),
    )
    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(fake_get_color))

    curve = CurveData(
        kind="fr",
        freqs=np.array([100.0, 1000.0]),
        mag_db=np.array([1.0, 0.0]),
    )
    window = make_curator(theme=DITHER)
    layer = window.add_curve(curve, "Layer", animate=False)
    window._choose_layer_color(layer.id, QPushButton())

    dither_palette = theme_trace_palette(DITHER)
    assert [opened_palettes[-1][index] for index in range(len(dither_palette))] == [
        QColor(color).name() for color in dither_palette
    ]

    window.apply_theme(DARK)
    window._choose_background()

    dark_palette = theme_trace_palette(DARK)
    assert [opened_palettes[-1][index] for index in range(len(dark_palette))] == [
        QColor(color).name() for color in dark_palette
    ]
    assert opened_palettes[-1][6] != QColor(dither_palette[6]).name()
    assert opened_palettes[-1][7] != QColor(dither_palette[7]).name()
    window.close()


class _PenRecorder:
    def __init__(self) -> None:
        self.widths: list[float] = []
        self.pens = []

    def setBrush(self, _brush) -> None:
        pass

    def save(self) -> None:
        pass

    def restore(self) -> None:
        pass

    def setRenderHint(self, _hint, _enabled=True) -> None:
        pass

    def setPen(self, pen) -> None:
        if hasattr(pen, "widthF"):
            self.widths.append(pen.widthF())
            self.pens.append(pen)

    def drawPath(self, _path) -> None:
        pass


def test_standard_export_curve_uses_only_solid_stroke(monkeypatch) -> None:
    painter = _PenRecorder()
    curve = CurveData(
        kind="variation",
        freqs=np.array([100.0, 1000.0]),
        p10_db=np.array([-2.0, -1.0]),
        p25_db=np.array([-1.0, 0.0]),
        median_db=np.array([0.0, 1.0]),
        p75_db=np.array([1.0, 2.0]),
        p90_db=np.array([2.0, 3.0]),
    )
    monkeypatch.setattr(export_image_module, "_fill_between", lambda *_args, **_kwargs: None)

    export_image_module._draw_curve_data(
        painter,
        QRectF(0.0, 0.0, 100.0, 100.0),
        curve,
        QColor("#6E6E6E"),
        -10.0,
        10.0,
        trace_index=1,
        trace_tokens=DITHER_TOKENS,
    )

    assert painter.widths == [pytest.approx(3.0)]
    assert painter.pens[0].style() == Qt.PenStyle.SolidLine
    assert painter.pens[0].dashPattern() == []


def test_dither_curator_variation_median_matches_solid_measure_median(make_curator, qapp) -> None:
    variation = CurveData(
        kind="variation",
        freqs=np.array([100.0, 1000.0]),
        p10_db=np.array([-2.0, -1.0]),
        p25_db=np.array([-1.0, 0.0]),
        median_db=np.array([0.0, 1.0]),
        p75_db=np.array([1.0, 2.0]),
        p90_db=np.array([2.0, 3.0]),
    )
    curator = make_curator(theme=DITHER)
    curator.add_curve(variation, "Variation", animate=False)
    curator._redraw()
    curator_median = next(
        item
        for item in curator._graph._items
        if isinstance(item, pg.PlotDataItem) and item.opts["pen"].widthF() == pytest.approx(2.2)
    )

    measure = DualPlotWidget()
    measure.apply_theme(DITHER)
    measure.update_curves(
        [],
        None,
        variation=VariationBand(variation.freqs, *variation.bands()),
        bottom_mode="variation",
    )
    measure_median = measure._bot_extra_items[-1]

    assert curator_median.opts["pen"].style() == Qt.PenStyle.SolidLine
    assert curator_median.opts["pen"].dashPattern() == []
    assert measure_median.opts["pen"].style() == Qt.PenStyle.SolidLine
    assert measure_median.opts["pen"].dashPattern() == []
    curator.close()
    measure.close()


def test_layer_row_checkbox_toggles_visibility(make_curator, qapp, tmp_path: Path) -> None:
    source = tmp_path / "curve.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")
    window = make_curator()
    window.import_files([source])

    item = window._layer_list.item(0)
    row = window._layer_list.itemWidget(item)
    row.visible_check.setChecked(False)

    assert window.graph_state.layers[0].visible is False


def test_data_rows_expose_inline_layer_controls(make_curator, qapp, tmp_path: Path) -> None:
    source = tmp_path / "curve.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")
    window = make_curator()
    window.import_files([source])

    group_titles = {box.title() for box in window.findChildren(QGroupBox)}
    assert "Data" in group_titles
    assert window._view_section_toggle.text() == "View"
    assert "Selected Layer" not in group_titles
    assert "Preference Bounds" not in group_titles
    assert "Export Text" not in group_titles

    row = window._layer_list.itemWidget(window._layer_list.item(0))
    assert row.visible_check is not None
    assert row.color_btn is not None
    assert row.offset_spin is not None
    assert row.hrtf_combo is not None
    assert row.name_edit is not None

    row.offset_spin.setValue(-7.5)
    assert window.graph_state.layers[0].vertical_offset_db == -7.5

    row.name_edit.setText("Renamed Layer")
    row.name_edit.editingFinished.emit()
    assert window.graph_state.layers[0].name == "Renamed Layer"
    row.name_edit.setText("   ")
    row.name_edit.editingFinished.emit()
    assert window.graph_state.layers[0].name == "Renamed Layer"
    assert row.name_edit.text() == "Renamed Layer"


def test_create_combined_variation_hides_sources_and_disables_hrtf(
    make_curator, qapp, tmp_path: Path
) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text(
        "100 0 1 2 3 4\n1000 10 11 12 13 14\n",
        encoding="utf-8",
    )
    second.write_text(
        "100 5 6 7 8 9\n1000 15 16 17 18 19\n",
        encoding="utf-8",
    )
    window = make_curator()
    window.import_files([first, second])

    for index in range(window._layer_list.count()):
        window._layer_list.item(index).setSelected(True)

    assert window._combine_btn.isEnabled()
    window._create_combined_variation()

    assert len(window.graph_state.layers) == 3
    source_ids = [layer.id for layer in window.graph_state.layers[:2]]
    combined = window.graph_state.layers[-1]
    assert combined.is_combined
    assert combined.source_layer_ids == source_ids
    assert combined.visible is True
    assert [layer.visible for layer in window.graph_state.layers[:2]] == [False, False]
    assert window._selected_layer_id == combined.id
    row = window._layer_list.itemWidget(window._layer_list.item(2))
    assert not row.hrtf_combo.isEnabled()
    assert row.color_btn.isEnabled()
    assert row.offset_spin.isEnabled()


def test_combine_button_ignores_fr_layers(make_curator, qapp, tmp_path: Path) -> None:
    variation = tmp_path / "variation.txt"
    fr = tmp_path / "fr.txt"
    variation.write_text(
        "100 0 1 2 3 4\n1000 10 11 12 13 14\n",
        encoding="utf-8",
    )
    fr.write_text("100 1\n1000 2\n", encoding="utf-8")
    window = make_curator()
    window.import_files([variation, fr])

    for index in range(window._layer_list.count()):
        window._layer_list.item(index).setSelected(True)

    assert not window._combine_btn.isEnabled()


def test_hrtf_dropdown_reads_hrtf_folder(make_curator, qapp, tmp_path: Path, monkeypatch) -> None:
    hrtf_dir = tmp_path / "HRTFs"
    bounds_dir = tmp_path / "Bounds"
    hrtf_dir.mkdir()
    bounds_dir.mkdir()
    (hrtf_dir / "Fixture A.txt").write_text("100 1\n1000 2\n", encoding="utf-8")
    (bounds_dir / "- Upper Bounds.txt").write_text("100 5\n1000 5\n", encoding="utf-8")
    (bounds_dir / "- Lower Bounds.txt").write_text("100 -5\n1000 -5\n", encoding="utf-8")
    monkeypatch.setattr(main_window_module, "HRTF_DIR", hrtf_dir)
    monkeypatch.setattr(main_window_module, "UPPER_BOUNDS_PATH", bounds_dir / "- Upper Bounds.txt")
    monkeypatch.setattr(main_window_module, "LOWER_BOUNDS_PATH", bounds_dir / "- Lower Bounds.txt")

    source = tmp_path / "curve.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")
    window = make_curator()
    window.import_files([source])

    row = window._layer_list.itemWidget(window._layer_list.item(0))
    assert row.hrtf_combo.count() == 2
    assert row.hrtf_combo.itemText(1) == "Fixture A"
    row.hrtf_combo.setCurrentIndex(1)
    assert window.graph_state.layers[0].hrtf.name == "Fixture A"


def test_bounds_switch_uses_bounds_folder(make_curator, qapp, tmp_path: Path, monkeypatch) -> None:
    hrtf_dir = tmp_path / "HRTFs"
    bounds_dir = tmp_path / "Bounds"
    hrtf_dir.mkdir()
    bounds_dir.mkdir()
    upper = bounds_dir / "- Upper Bounds.txt"
    lower = bounds_dir / "- Lower Bounds.txt"
    upper.write_text("100 4\n1000 5\n", encoding="utf-8")
    lower.write_text("100 -4\n1000 -5\n", encoding="utf-8")
    monkeypatch.setattr(main_window_module, "HRTF_DIR", hrtf_dir)
    monkeypatch.setattr(main_window_module, "UPPER_BOUNDS_PATH", upper)
    monkeypatch.setattr(main_window_module, "LOWER_BOUNDS_PATH", lower)

    window = make_curator()

    assert window.graph_state.bounds.upper_path == upper
    assert window.graph_state.bounds.enabled is False
    window._bounds_enabled.setChecked(True)
    assert window.graph_state.bounds.enabled is True


def test_export_button_lives_inside_view_box(make_curator, qapp) -> None:
    window = make_curator()

    assert window._export_btn.parent() == window._view_box
    assert window._export_btn.objectName() == "exportButton"
    assert window._export_btn in window._view_box.findChildren(QPushButton)


def test_view_aspect_toggle_and_reset(make_curator, qapp) -> None:
    window = make_curator()

    assert window._bounds_enabled.minimumSizeHint().width() >= 54
    assert window._aspect_lock_enabled.minimumSizeHint().width() >= 54
    assert window.graph_state.aspect_locked_25db is True
    window._aspect_lock_enabled.setChecked(False)
    window._smoothing_combo.setCurrentIndex(window._smoothing_combo.findData(3))
    window._show_names_enabled.setChecked(False)
    assert window.graph_state.aspect_locked_25db is False

    window.set_y_limits(-40.0, 12.0)
    window._reset_view()

    assert window.graph_state.aspect_locked_25db is True
    assert window._aspect_lock_enabled.isChecked()
    assert window.graph_state.y_min == -17.5
    assert window.graph_state.y_max == 17.5
    assert window.graph_state.smoothing_fraction == 48
    assert window.graph_state.show_layer_names is True


def test_curator_uses_right_sidebar_and_drop_import(
    make_curator, qapp, tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "drop.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")
    unsupported = tmp_path / "ignore.csv"
    unsupported.write_text("100,1\n", encoding="utf-8")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    window = make_curator()

    class _Mime:
        def urls(self):
            from PyQt6.QtCore import QUrl

            return [QUrl.fromLocalFile(str(source)), QUrl.fromLocalFile(str(unsupported))]

    class _Drop:
        accepted = False

        def mimeData(self):
            return _Mime()

        def acceptProposedAction(self):
            self.accepted = True

        def ignore(self):
            self.accepted = False

    event = _Drop()
    window.dropEvent(event)

    assert event.accepted
    assert len(window.graph_state.layers) == 1
    assert warnings and "unsupported file type" in warnings[0]
    assert window._data_box.parent() is window.findChild(QWidget, "controlPanel")
    assert window._view_section.parent() is window.findChild(QWidget, "controlPanel")


def test_export_title_shrinks_then_elides(qapp) -> None:
    short, short_font = fit_title("Short title", 1200)
    long, long_font = fit_title("Very long title " * 40, 500)

    assert short == "Short title"
    assert short_font.pointSize() == 42
    assert long_font.pointSize() == 24
    assert long.endswith("…")


def test_export_legend_expands_and_never_elides_layer_names(qapp) -> None:
    class _Layer:
        color = "#39FF14"

        def __init__(self, name: str) -> None:
            self.name = name

    class _Painter:
        def __init__(self) -> None:
            self.boxes = []
            self.text = []

        def setFont(self, *_args) -> None:
            pass

        def setPen(self, *_args) -> None:
            pass

        def setBrush(self, *_args) -> None:
            pass

        def drawRoundedRect(self, rect, *_args) -> None:
            self.boxes.append(QRectF(rect))

        def drawLine(self, *_args) -> None:
            pass

        def drawText(self, _rect, _flags, text) -> None:
            self.text.append(text)

    long_name = "Unknown Unknown COMP with a complete measurement description"
    painter = _Painter()
    _draw_legend(
        painter,
        QRectF(0, 0, 1800, 700),
        [(_Layer(long_name), None)],
        False,
    )

    assert painter.text == [long_name]
    assert painter.boxes[0].width() > 290


def test_viewport_text_inputs_update_export_text(make_curator, qapp) -> None:
    window = make_curator()

    assert window._graph_stage.fixture_input.text() == ""
    assert window._graph_stage.hrtf_note_input.text() == "Test Fixture"
    assert not hasattr(window._graph_stage, "notes_input")

    window._graph_stage.title_input.setText("Fresh Title")
    window._graph_stage.fixture_input.setText("Fixture X")
    window._graph_stage.hrtf_note_input.setText("Test Fixture X")

    assert window.graph_state.export_text.title == "Fresh Title"
    assert window.graph_state.export_text.fixture == "Fixture X"
    assert window.graph_state.export_text.hrtf_note == "Test Fixture X"
    assert window.graph_state.export_text.notes == ""


def test_wipe_runs_for_bounds_and_measurement_changes(
    make_curator, qapp, tmp_path: Path, monkeypatch
) -> None:
    hrtf_dir = tmp_path / "HRTFs"
    bounds_dir = tmp_path / "Bounds"
    hrtf_dir.mkdir()
    bounds_dir.mkdir()
    upper = bounds_dir / "- Upper Bounds.txt"
    lower = bounds_dir / "- Lower Bounds.txt"
    upper.write_text("100 4\n1000 5\n", encoding="utf-8")
    lower.write_text("100 -4\n1000 -5\n", encoding="utf-8")
    monkeypatch.setattr(main_window_module, "HRTF_DIR", hrtf_dir)
    monkeypatch.setattr(main_window_module, "UPPER_BOUNDS_PATH", upper)
    monkeypatch.setattr(main_window_module, "LOWER_BOUNDS_PATH", lower)

    window = make_curator()
    wipes: list[dict] = []
    assert not hasattr(window._graph_stage, "_wipe_overlay")
    window._graph.start_data_wipe = lambda **kwargs: wipes.append(kwargs)

    source = tmp_path / "curve.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")
    window.import_files([source])
    assert len(wipes) == 1
    assert wipes[-1]["entering_layer_ids"] == {window.graph_state.layers[0].id}
    assert not wipes[-1]["entering_bounds"]

    row = window._layer_list.itemWidget(window._layer_list.item(0))
    row.visible_check.setChecked(False)
    assert len(wipes) == 2
    assert wipes[-1]["entering_layer_ids"] is None
    assert len(wipes[-1]["exiting_layers"]) == 1

    window._bounds_enabled.setChecked(True)
    assert len(wipes) == 3
    assert wipes[-1]["entering_bounds"] is True

    window._remove_selected_layer()
    assert len(wipes) == 4


def test_export_graph_image_writes_16_by_9_png(make_curator, qapp, tmp_path: Path) -> None:
    source = tmp_path / "curve.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")
    window = make_curator()
    window.import_files([source])
    output = tmp_path / "poster.png"

    export_graph_image(window.graph_state, output, size=(1920, 1080))

    image = QImage(str(output))
    assert output.stat().st_size > 0
    assert image.width() == 1920
    assert image.height() == 1080


def test_fastgraph95_dark_export_uses_classic_frame_and_safe_bottom_margin(
    make_curator, qapp, tmp_path: Path
) -> None:
    source = tmp_path / "curve.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")
    window = make_curator(theme=FASTGRAPH_95_DARK)
    window.import_files([source])
    output = tmp_path / "poster-dark.png"

    export_graph_image(
        window.graph_state,
        output,
        size=(1920, 1080),
        theme=FASTGRAPH_95_DARK,
    )

    image = QImage(str(output))
    assert image.pixelColor(1880, 40).name().upper() == "#315B85"
    assert image.pixelColor(10, 1068).name().upper() == "#3C3C3C"
    assert image.pixelColor(100, 1018).name().upper() != "#202020"
    window.close()


def test_existing_export_themes_keep_matching_classic_and_retro_flags() -> None:
    for definition in THEME_DEFINITIONS:
        if definition.key == DITHER:
            continue
        assert definition.tokens.classic_controls is definition.tokens.retro_graph
    assert BRAND_TOKENS.classic_controls is BRAND_TOKENS.retro_graph


def test_dither_export_uses_tokens_and_excludes_gold_accent(
    make_curator, qapp, tmp_path: Path
) -> None:
    source = tmp_path / "dither-curve.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")
    window = make_curator(theme=DITHER)
    window.import_files([source])
    window.graph_state.export_text.title = "Dither Export"
    window.graph_state.export_text.fixture = "Fixture"
    output = tmp_path / "poster-dither.png"

    export_graph_image(
        window.graph_state,
        output,
        size=(1920, 1080),
        theme=DITHER,
    )

    image = QImage(str(output)).convertToFormat(QImage.Format.Format_RGBA8888)
    assert image.pixelColor(10, 1068).name().upper() == DITHER_TOKENS.text
    assert image.pixelColor(0, 0).name().upper() == DITHER_TOKENS.text
    assert image.pixelColor(1919, 117).name().upper() == DITHER_TOKENS.text
    assert image.pixelColor(100, 124).name().upper() == DITHER_TOKENS.background
    assert image.pixelColor(200, 200).name().upper() == DITHER_TOKENS.plot_bg
    assert image.pixelColor(1800, 1018).name().upper() == DITHER_TOKENS.text

    bits = image.constBits()
    bits.setsize(image.sizeInBytes())
    rows = np.frombuffer(bits, dtype=np.uint8).reshape(image.height(), image.bytesPerLine())
    pixels = rows[:, : image.width() * 4].reshape(image.height(), image.width(), 4)
    gold = np.array(QColor(ACCENT_COLOR).getRgb()[:3], dtype=np.uint8)
    assert not np.any(np.all(pixels[:, :, :3] == gold, axis=2))
    window.close()


def test_dither_export_masthead_is_filled_with_knocked_out_text(qapp, tmp_path: Path) -> None:
    state = GraphState()
    state.export_text.title = "Test Masthead"
    state.export_text.fixture = "711 Fixture"
    output = tmp_path / "masthead.png"
    export_graph_image(state, output, size=(800, 600), theme=DITHER)

    image = QImage(str(output)).convertToFormat(QImage.Format.Format_RGBA8888)
    block = DITHER_TOKENS.text
    knockout = QColor(DITHER_TOKENS.background).rgba()
    assert image.pixelColor(0, 0).name().upper() == block
    assert image.pixelColor(799, 0).name().upper() == block
    assert image.pixelColor(0, 117).name().upper() == block
    assert image.pixelColor(799, 117).name().upper() == block
    assert (
        sum(
            image.pixelColor(x, y).rgba() == knockout
            for y in range(20, 100)
            for x in range(30, 770)
        )
        > 500
    )
    assert image.pixelColor(100, 124).name().upper() == DITHER_TOKENS.background


def test_dither_export_footer_is_square_filled_and_uses_knockout_text(qapp, tmp_path: Path) -> None:
    state = GraphState()
    state.export_text.hrtf_note = "Test Fixture"
    state.export_text.notes = ""
    output = tmp_path / "footer.png"
    export_graph_image(state, output, size=(800, 600), theme=DITHER)

    image = QImage(str(output)).convertToFormat(QImage.Format.Format_RGBA8888)
    footer_top = image.height() - int(DITHER_FOOTER_HEIGHT)
    ink = QColor(DITHER_TOKENS.text).rgba()
    knockout = QColor(DITHER_TOKENS.background).rgba()

    assert all(image.pixelColor(x, footer_top).rgba() == ink for x in range(image.width()))
    assert all(image.pixelColor(x, image.height() - 1).rgba() == ink for x in range(image.width()))
    assert all(
        image.pixelColor(0, y).rgba() == ink
        and image.pixelColor(image.width() - 1, y).rgba() == ink
        for y in range(footer_top, image.height())
    )

    text_pixels = [
        (x, y)
        for y in range(footer_top + 1, image.height() - 1)
        for x in range(1, image.width() - 1)
        if image.pixelColor(x, y).rgba() == knockout
    ]
    assert text_pixels
    assert 40 <= min(x for x, _y in text_pixels) <= 45
    assert max(x for x, _y in text_pixels) < image.width() - 40
    assert min(y for _x, y in text_pixels) > footer_top
    assert max(y for _x, y in text_pixels) < image.height() - 1


def test_dither_export_bounds_density_varies_from_edge_to_center(qapp, tmp_path: Path) -> None:
    freqs = np.array([20.0, 20000.0])
    state = GraphState(y_min=-10.0, y_max=10.0)
    state.bounds = PreferenceBounds(
        enabled=True,
        upper=CurveData(kind="fr", freqs=freqs, mag_db=np.array([5.0, 5.0])),
        lower=CurveData(kind="fr", freqs=freqs, mag_db=np.array([-5.0, -5.0])),
    )
    output = tmp_path / "bounds-dither.png"
    export_graph_image(state, output, size=(800, 600), theme=DITHER)

    image = QImage(str(output)).convertToFormat(QImage.Format.Format_RGBA8888)
    ink = QColor(DITHER_TOKENS.plot_grid).rgba()
    plot_rect = _poster_plot_rect(state, (800, 600))
    upper_y = round(_y_for_db(plot_rect, 5.0, state.y_min, state.y_max))
    lower_y = round(_y_for_db(plot_rect, -5.0, state.y_min, state.y_max))
    center_y = round(_y_for_db(plot_rect, 0.0, state.y_min, state.y_max))

    def coverage(y_start: int, y_stop: int) -> int:
        return sum(
            image.pixelColor(x, y).rgba() == ink
            for y in range(y_start, y_stop)
            for x in range(140, 640)
        )

    near_edges = coverage(upper_y + 2, upper_y + 10) + coverage(lower_y - 10, lower_y - 2)
    center = 2 * coverage(center_y - 4, center_y + 4)
    assert near_edges > center * 2


def test_export_uses_gold_accent_constant() -> None:
    assert ACCENT_COLOR == "#FCBE11"


def test_frequency_markers_include_1k_3k_8k_and_10k_weights() -> None:
    ticks = dict(FREQUENCY_TICKS)

    assert ticks[1000] == "1k"
    assert ticks[3000] == "3k"
    assert ticks[8000] == "8k"
    assert ticks[10000] == "10k"
    for markers in (FREQUENCY_MARKERS, EXPORT_FREQUENCY_MARKERS):
        assert 8000 not in markers
        assert markers[1000][4] > markers[3000][4]
        assert markers[10000][4] > markers[3000][4]


def _poster_plot_rect(state: GraphState, size: tuple[int, int]) -> QRectF:
    """The data rectangle ``_draw_poster`` uses for the given state and size."""
    width, height = size
    frame = QRectF(72, 150, width - 144, height - 280).adjusted(
        PLOT_INSET_LEFT,
        PLOT_INSET_TOP,
        -PLOT_INSET_RIGHT,
        -PLOT_INSET_BOTTOM,
    )
    if not state.aspect_locked_25db:
        return frame
    return aspect_locked_rect(frame, state.y_min, state.y_max)


def _variation_state(**kwargs) -> GraphState:
    freqs = np.array([20.0, 1000.0, 20000.0])
    state = GraphState(**kwargs)
    state.layers.append(
        LayerState(
            curve=CurveData(
                kind="variation",
                freqs=freqs,
                p10_db=np.array([-4.0, -4.0, -4.0]),
                p25_db=np.array([-2.0, -2.0, -2.0]),
                median_db=np.array([0.0, 0.0, 0.0]),
                p75_db=np.array([2.0, 2.0, 2.0]),
                p90_db=np.array([4.0, 4.0, 4.0]),
            ),
            source_path=Path("variation.txt"),
            name="Variation",
            color="#15f4ee",
        )
    )
    return state


def test_out_of_band_points_are_dropped_instead_of_clamped() -> None:
    rect = QRectF(0.0, 0.0, 300.0, 100.0)
    freqs = np.array([5.0, 20.0, 1000.0, 20000.0, 48000.0])
    mags = np.array([-40.0, 0.0, 1.0, 0.0, 40.0])

    path = _curve_path(rect, freqs, mags, -10.0, 10.0)

    assert path.elementCount() == 3
    xs = [path.elementAt(index).x for index in range(path.elementCount())]
    assert xs[0] == pytest.approx(rect.left())
    assert xs[-1] == pytest.approx(rect.right())
    # The clamped version drew a vertical spike from the 5 Hz point at x = left.
    ys = [path.elementAt(index).y for index in range(path.elementCount())]
    assert all(rect.top() <= y <= rect.bottom() for y in ys)


def test_out_of_band_points_do_not_spike_the_exported_png(qapp, tmp_path: Path) -> None:
    freqs = np.array([5.0, 20.0, 1000.0, 20000.0, 40000.0])
    state = GraphState(y_min=-10.0, y_max=10.0)
    state.layers.append(
        LayerState(
            curve=CurveData(
                kind="fr",
                freqs=freqs,
                mag_db=np.array([-9.0, 0.0, 0.0, 0.0, 9.0]),
            ),
            source_path=Path("spike.txt"),
            name="Spike",
            color="#ff0000",
        )
    )
    state.show_layer_names = False
    output = tmp_path / "spike.png"

    export_graph_image(state, output, size=(800, 600), theme=DARK)

    image = QImage(str(output)).convertToFormat(QImage.Format.Format_RGBA8888)
    plot_rect = _poster_plot_rect(state, (800, 600))
    top = int(plot_rect.top()) + 1
    bottom = int(plot_rect.bottom()) - 1

    def trace_pixels(x: int) -> int:
        return sum(
            1
            for y in range(top, bottom)
            if image.pixelColor(x, y).red() > 150 and image.pixelColor(x, y).green() < 80
        )

    left = int(round(plot_rect.left()))
    right = int(round(plot_rect.right())) - 1
    assert trace_pixels(left) > 0
    assert trace_pixels(right) > 0
    # Clamping the 5 Hz and 40 kHz points onto the edges drew a tall vertical
    # spike there; only the pen width should be lit now.
    assert trace_pixels(left) <= 6
    assert trace_pixels(right) <= 6


def test_poster_honours_the_25db_per_decade_lock(qapp, tmp_path: Path) -> None:
    locked = _variation_state(y_min=-10.0, y_max=10.0, aspect_locked_25db=True)
    free = _variation_state(y_min=-10.0, y_max=10.0, aspect_locked_25db=False)
    locked.show_layer_names = False
    free.show_layer_names = False
    locked_path = tmp_path / "locked.png"
    free_path = tmp_path / "free.png"

    export_graph_image(locked, locked_path, size=(800, 600), theme=DARK)
    export_graph_image(free, free_path, size=(800, 600), theme=DARK)

    assert locked_path.read_bytes() != free_path.read_bytes()
    locked_rect = _poster_plot_rect(locked, (800, 600))
    free_rect = _poster_plot_rect(free, (800, 600))
    assert locked_rect.height() < free_rect.height()
    # 25 dB must span exactly one decade of width.
    db_per_pixel = (locked.y_max - locked.y_min) / locked_rect.height()
    decades_per_pixel = 3.0 / locked_rect.width()
    assert db_per_pixel / decades_per_pixel == pytest.approx(25.0, rel=1e-6)


def test_aspect_lock_widens_instead_of_cropping_a_tall_db_range() -> None:
    rect = QRectF(0.0, 0.0, 900.0, 300.0)

    locked = aspect_locked_rect(rect, -60.0, 60.0)

    assert locked.height() == pytest.approx(rect.height())
    assert locked.width() < rect.width()
    assert locked.center().x() == pytest.approx(rect.center().x())


def test_bounds_renderers_interpolate_the_lower_bound_onto_the_upper_grid() -> None:
    upper = CurveData(
        kind="fr",
        freqs=np.array([20.0, 200.0, 2000.0, 20000.0]),
        mag_db=np.array([5.0, 5.0, 5.0, 5.0]),
    )
    lower = CurveData(
        kind="fr",
        freqs=np.array([20.0, 20000.0]),
        mag_db=np.array([-5.0, -1.0]),
    )

    freqs, upper_values, lower_values = aligned_bounds(upper, lower)

    assert np.array_equal(freqs, upper.freqs)
    assert np.array_equal(upper_values, upper.mag_db)
    assert lower_values.shape == freqs.shape
    assert np.allclose(lower_values, np.interp(upper.freqs, lower.freqs, lower.mag_db))
    assert lower_values[0] == pytest.approx(-5.0)
    assert lower_values[-1] == pytest.approx(-1.0)


def test_mismatched_bounds_grids_no_longer_zip_by_index(qapp, tmp_path: Path) -> None:
    state = GraphState(y_min=-10.0, y_max=10.0)
    state.show_layer_names = False
    state.bounds = PreferenceBounds(
        enabled=True,
        upper=CurveData(
            kind="fr",
            freqs=np.array([20.0, 200.0, 2000.0, 20000.0]),
            mag_db=np.array([5.0, 5.0, 5.0, 5.0]),
        ),
        lower=CurveData(
            kind="fr",
            freqs=np.array([20.0, 20000.0]),
            mag_db=np.array([-5.0, -5.0]),
        ),
    )
    output = tmp_path / "bounds.png"

    export_graph_image(state, output, size=(800, 600), theme=DARK)

    image = QImage(str(output)).convertToFormat(QImage.Format.Format_RGBA8888)
    plot_rect = _poster_plot_rect(state, (800, 600))
    right = int(round(plot_rect.right())) - 3
    inside = int(round(_y_for_db(plot_rect, -4.0, state.y_min, state.y_max)))
    outside = int(round(_y_for_db(plot_rect, -8.0, state.y_min, state.y_max)))
    background = image.pixelColor(right, outside)
    band = image.pixelColor(right, inside)
    assert band != background


def test_brand_layer_colors_go_through_the_contrast_guard() -> None:
    dark = "#101010"

    assert brand_display_color(dark) != QColor(dark)
    assert brand_display_color("#15f4ee") == QColor("#15f4ee")


def test_brand_show_names_works_outside_clean_slate(qapp, tmp_path: Path) -> None:
    def render(show_names: bool, clean_slate: bool) -> bytes:
        state = _variation_state()
        state.layers[0].name = "Layer Name Here"
        state.show_layer_names = show_names
        state.brand_clean_slate = clean_slate
        output = tmp_path / f"brand-{show_names}-{clean_slate}.png"
        export_graph_image(state, output, size=(960, 540), brand_mode=True)
        return output.read_bytes()

    assert render(True, False) != render(False, False)
    assert render(True, True) != render(False, True)


def _curator_events(store) -> list:
    return [event for event in store.events() if event.source == "curator"]


def test_remove_acts_on_every_selected_layer(make_curator, qapp) -> None:
    window = make_curator()
    curve = CurveData(kind="fr", freqs=np.array([100.0, 1000.0]), mag_db=np.array([1.0, 0.0]))
    first = window.add_curve(curve, "First", animate=False)
    second = window.add_curve(curve, "Second", animate=False)
    window.add_curve(curve, "Third", animate=False)
    window._sync_ui()
    for index in range(window._layer_list.count()):
        item = window._layer_list.item(index)
        item.setSelected(item.data(256) in {first.id, second.id})

    window._remove_selected_layer()

    assert [layer.name for layer in window.graph_state.layers] == ["Third"]


def test_remove_falls_back_to_the_focused_row_when_nothing_is_selected(make_curator, qapp) -> None:
    window = make_curator()
    curve = CurveData(kind="fr", freqs=np.array([100.0, 1000.0]), mag_db=np.array([1.0, 0.0]))
    window.add_curve(curve, "First", animate=False)
    second = window.add_curve(curve, "Second", animate=False)
    window._sync_ui()
    for index in range(window._layer_list.count()):
        window._layer_list.item(index).setSelected(False)
    window._selected_layer_id = second.id

    window._remove_selected_layer()

    assert [layer.name for layer in window.graph_state.layers] == ["First"]


def test_move_up_and_down_reorder_layers(make_curator, qapp) -> None:
    window = make_curator()
    curve = CurveData(kind="fr", freqs=np.array([100.0, 1000.0]), mag_db=np.array([1.0, 0.0]))
    window.add_curve(curve, "First", animate=False)
    window.add_curve(curve, "Second", animate=False)
    window.add_curve(curve, "Third", animate=False)
    window._selected_layer_id = window.graph_state.layers[2].id

    window._move_up_btn.click()
    assert [layer.name for layer in window.graph_state.layers] == [
        "First",
        "Third",
        "Second",
    ]

    window._move_down_btn.click()
    assert [layer.name for layer in window.graph_state.layers] == [
        "First",
        "Second",
        "Third",
    ]

    # Moving past either end is a no-op rather than an error.
    window._selected_layer_id = window.graph_state.layers[0].id
    window._move_up_btn.click()
    assert [layer.name for layer in window.graph_state.layers][0] == "First"
    window._selected_layer_id = window.graph_state.layers[2].id
    window._move_down_btn.click()
    assert [layer.name for layer in window.graph_state.layers][2] == "Third"


def _variation_curve(offset: float = 0.0) -> CurveData:
    freqs = np.array([20.0, 1000.0, 20000.0])
    ones = np.ones(3)
    return CurveData(
        kind="variation",
        freqs=freqs,
        p10_db=ones * (offset - 4.0),
        p25_db=ones * (offset - 2.0),
        median_db=ones * offset,
        p75_db=ones * (offset + 2.0),
        p90_db=ones * (offset + 4.0),
    )


def test_combined_layer_is_marked_stale_when_a_source_changes(make_curator, qapp) -> None:
    window = make_curator()
    first = window.add_curve(_variation_curve(), "First", animate=False, normalize=False)
    second = window.add_curve(_variation_curve(2.0), "Second", animate=False, normalize=False)

    combined = window._combine_layers([first, second])
    assert combined.is_combined
    assert combined.stale is False

    window._set_layer_offset(first.id, 5.0)

    assert combined.stale is True
    row = window._layer_list.itemWidget(window._layer_list.item(len(window.graph_state.layers) - 1))
    assert row.findChild(QLabel, "layerStaleBadge") is not None


def test_combined_layer_is_marked_stale_when_a_source_is_removed(make_curator, qapp) -> None:
    window = make_curator()
    first = window.add_curve(_variation_curve(), "First", animate=False, normalize=False)
    second = window.add_curve(_variation_curve(2.0), "Second", animate=False, normalize=False)
    combined = window._combine_layers([first, second])

    window._selected_layer_id = first.id
    for index in range(window._layer_list.count()):
        item = window._layer_list.item(index)
        item.setSelected(item.data(256) == first.id)
    window._remove_selected_layer()

    assert combined.stale is True


def test_bounds_toggle_refuses_to_latch_without_bounds_files(
    make_curator, qapp, monkeypatch, console_events
) -> None:
    window = make_curator()
    window.graph_state.bounds = PreferenceBounds(enabled=False)
    monkeypatch.setattr(main_window_module, "UPPER_BOUNDS_PATH", Path("/nonexistent/upper.txt"))
    monkeypatch.setattr(main_window_module, "LOWER_BOUNDS_PATH", Path("/nonexistent/lower.txt"))
    warned: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, text, *args, **kwargs: warned.append((title, text)),
    )

    window._bounds_enabled.setChecked(True)

    assert window.graph_state.bounds.enabled is False
    assert window._bounds_enabled.isChecked() is False
    assert warned and "bounds" in warned[0][0].lower()
    assert any(
        event.severity == "WARNING" and "Preference bounds are unavailable" in event.message
        for event in _curator_events(console_events)
    )


def test_bounds_toggle_still_latches_when_the_files_exist(make_curator, qapp) -> None:
    window = make_curator()

    window._bounds_enabled.setChecked(True)

    assert window.graph_state.bounds.enabled is True
    assert window.graph_state.bounds.upper is not None


def test_layer_row_swatch_uses_the_contrast_corrected_colour(make_curator, qapp) -> None:
    window = make_curator()
    curve = CurveData(kind="fr", freqs=np.array([100.0, 1000.0]), mag_db=np.array([1.0, 0.0]))
    layer = window.add_curve(curve, "Dark", animate=False)
    layer.color = "#101010"
    window._sync_ui()

    row = window._layer_list.itemWidget(window._layer_list.item(0))
    assert row.swatch_color.lower() != "#101010"
    assert (
        row.swatch_color.lower()
        == ensure_graph_color("#101010", window.graph_state.background).name().lower()
    )
    assert "#242a35" not in row.color_btn.styleSheet()


def test_graph_legend_uses_display_colours_and_a_theme_chip(make_curator, qapp) -> None:
    window = make_curator()
    curve = CurveData(kind="fr", freqs=np.array([100.0, 1000.0]), mag_db=np.array([1.0, 0.0]))
    layer = window.add_curve(curve, "Dark Layer", animate=False)
    layer.color = "#101010"
    window.graph_state.show_layer_names = True
    window._redraw()

    labels = [item for item in window._graph._items if isinstance(item, pg.TextItem)]
    assert labels
    expected = ensure_graph_color("#101010", window.graph_state.background)
    assert labels[0].color.name().lower() == expected.name().lower()
    fill, border = window._graph._legend_chip_colors()
    tokens = tokens_for(window._theme, brand_mode=False)
    assert fill.name().lower() == QColor(tokens.panel).name().lower()
    assert border.name().lower() == QColor(tokens.border).name().lower()


def test_import_status_is_error_toned_when_every_file_fails(
    make_curator, qapp, tmp_path: Path, monkeypatch, console_events
) -> None:
    window = make_curator()
    bad = tmp_path / "bad.txt"
    bad.write_text("not data at all\n", encoding="utf-8")
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)

    loaded, failures = window.import_files([bad])

    assert loaded == 0
    assert failures
    statuses = [
        event
        for event in _curator_events(console_events)
        if event.message.startswith("Import failed")
    ]
    assert statuses and statuses[-1].severity == "ERROR"


def test_import_surfaces_parser_and_normalization_warnings(
    make_curator, qapp, tmp_path: Path, monkeypatch
) -> None:
    partial = tmp_path / "partial.txt"
    partial.write_text("20 1\n100 2\n100 4\n500 3\n", encoding="utf-8")
    window = make_curator()
    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, text, *args, **kwargs: shown.append(text),
    )

    loaded, failures = window.import_files([partial])

    assert loaded == 1
    assert not failures
    assert shown
    assert "repeated frequency" in shown[0]
    assert "1 kHz" in shown[0]
    assert window.graph_state.layers[0].vertical_offset_db == 0.0
