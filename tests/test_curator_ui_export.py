from pathlib import Path

import numpy as np
import pyqtgraph as pg
import pytest
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import (
    QApplication,
    QColorDialog,
    QGroupBox,
    QMessageBox,
    QPushButton,
    QWidget,
)

from dms.curator.export_image import (
    ACCENT_COLOR,
    DITHER_FOOTER_HEIGHT,
    FREQUENCY_TICKS as EXPORT_FREQUENCY_TICKS,
    _draw_legend,
    export_graph_image,
    fit_title,
)
import dms.curator.export_image as export_image_module
from dms.curator.models import CurveData, GraphState, PreferenceBounds
from dms.theme import (
    DARK,
    DITHER,
    FASTGRAPH_95,
    FASTGRAPH_95_DARK,
    HACKERMAN_95,
    theme_trace_palette,
)
from dms.ui.style_tokens import DITHER_TOKENS, BRAND_TOKENS, theme_definitions
from dms.ui.curator_graph_widget import FREQUENCY_MARKERS, FREQUENCY_TICKS as GRAPH_FREQUENCY_TICKS
import dms.ui.curator_widget as main_window_module
from dms.ui.curator_widget import CuratorWidget
from dms.ui.dual_plot_widget import DualPlotWidget
from dms.console import ConsoleEventStore


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_main_window_imports_multiple_files_normalizes_and_locks_viewport(qapp, tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("100 1\n1000 5\n", encoding="utf-8")
    second.write_text(
        "* Export Type: Variation Band\n"
        "100 -2 -1 -3 1 2\n"
        "1000 -1 0 -3 2 3\n",
        encoding="utf-8",
    )

    window = CuratorWidget(ConsoleEventStore())
    window.import_files([first, second])

    assert len(window.graph_state.layers) == 2
    assert window.graph_state.layers[0].vertical_offset_db == -5.0
    assert window.graph_state.layers[1].vertical_offset_db == 3.0
    assert window.graph_state.layers[0].curve.mag_db[1] == 5.0
    view_box = window._graph.getPlotItem().getViewBox()
    assert view_box.state["mouseEnabled"] == [False, False]
    assert window._graph_frame.ratio == 16.0 / 9.0
    assert window._graph_stage._rounded_graph.radius == 10


def test_curator_preview_curves_have_no_glow_items(qapp) -> None:
    window = CuratorWidget(ConsoleEventStore())
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


def test_fastgraph95_curves_use_the_fine_step_display_renderer(qapp) -> None:
    window = CuratorWidget(ConsoleEventStore(), theme=FASTGRAPH_95)
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


def test_hackerman95_new_layers_start_green_then_use_distinct_neon_colors(qapp) -> None:
    window = CuratorWidget(ConsoleEventStore(), theme=HACKERMAN_95)
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
    qapp, monkeypatch
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
    window = CuratorWidget(ConsoleEventStore(), theme=DITHER)
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


def test_dither_curator_variation_median_matches_solid_measure_median(qapp) -> None:
    variation = CurveData(
        kind="variation",
        freqs=np.array([100.0, 1000.0]),
        p10_db=np.array([-2.0, -1.0]),
        p25_db=np.array([-1.0, 0.0]),
        median_db=np.array([0.0, 1.0]),
        p75_db=np.array([1.0, 2.0]),
        p90_db=np.array([2.0, 3.0]),
    )
    curator = CuratorWidget(ConsoleEventStore(), theme=DITHER)
    curator.add_curve(variation, "Variation", animate=False)
    curator._redraw()
    curator_median = next(
        item
        for item in curator._graph._items
        if isinstance(item, pg.PlotDataItem)
        and item.opts["pen"].widthF() == pytest.approx(2.2)
    )

    measure = DualPlotWidget()
    measure.apply_theme(DITHER)
    measure.update_curves([], None, variation=(
        variation.freqs,
        variation.p10_db,
        variation.p25_db,
        variation.p75_db,
        variation.p90_db,
        variation.median_db,
    ), bottom_mode="variation")
    measure_median = measure._bot_extra_items[-1]

    assert curator_median.opts["pen"].style() == Qt.PenStyle.SolidLine
    assert curator_median.opts["pen"].dashPattern() == []
    assert measure_median.opts["pen"].style() == Qt.PenStyle.SolidLine
    assert measure_median.opts["pen"].dashPattern() == []
    curator.close()
    measure.close()


def test_layer_row_checkbox_toggles_visibility(qapp, tmp_path: Path) -> None:
    source = tmp_path / "curve.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")
    window = CuratorWidget(ConsoleEventStore())
    window.import_files([source])

    item = window._layer_list.item(0)
    row = window._layer_list.itemWidget(item)
    row.visible_check.setChecked(False)

    assert window.graph_state.layers[0].visible is False


def test_data_rows_expose_inline_layer_controls(qapp, tmp_path: Path) -> None:
    source = tmp_path / "curve.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")
    window = CuratorWidget(ConsoleEventStore())
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


def test_create_combined_variation_hides_sources_and_disables_hrtf(qapp, tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text(
        "100 0 1 2 3 4\n"
        "1000 10 11 12 13 14\n",
        encoding="utf-8",
    )
    second.write_text(
        "100 5 6 7 8 9\n"
        "1000 15 16 17 18 19\n",
        encoding="utf-8",
    )
    window = CuratorWidget(ConsoleEventStore())
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


def test_combine_button_ignores_fr_layers(qapp, tmp_path: Path) -> None:
    variation = tmp_path / "variation.txt"
    fr = tmp_path / "fr.txt"
    variation.write_text(
        "100 0 1 2 3 4\n"
        "1000 10 11 12 13 14\n",
        encoding="utf-8",
    )
    fr.write_text("100 1\n1000 2\n", encoding="utf-8")
    window = CuratorWidget(ConsoleEventStore())
    window.import_files([variation, fr])

    for index in range(window._layer_list.count()):
        window._layer_list.item(index).setSelected(True)

    assert not window._combine_btn.isEnabled()


def test_hrtf_dropdown_reads_hrtf_folder(qapp, tmp_path: Path, monkeypatch) -> None:
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
    window = CuratorWidget(ConsoleEventStore())
    window.import_files([source])

    row = window._layer_list.itemWidget(window._layer_list.item(0))
    assert row.hrtf_combo.count() == 2
    assert row.hrtf_combo.itemText(1) == "Fixture A"
    row.hrtf_combo.setCurrentIndex(1)
    assert window.graph_state.layers[0].hrtf.name == "Fixture A"


def test_bounds_switch_uses_bounds_folder(qapp, tmp_path: Path, monkeypatch) -> None:
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

    window = CuratorWidget(ConsoleEventStore())

    assert window.graph_state.bounds.upper_path == upper
    assert window.graph_state.bounds.enabled is False
    window._bounds_enabled.setChecked(True)
    assert window.graph_state.bounds.enabled is True


def test_export_button_lives_inside_view_box(qapp) -> None:
    window = CuratorWidget(ConsoleEventStore())

    assert window._export_btn.parent() == window._view_box
    assert window._export_btn.objectName() == "exportButton"
    assert window._export_btn in window._view_box.findChildren(QPushButton)


def test_view_aspect_toggle_and_reset(qapp) -> None:
    window = CuratorWidget(ConsoleEventStore())

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


def test_curator_uses_right_sidebar_and_drop_import(qapp, tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "drop.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")
    unsupported = tmp_path / "ignore.csv"
    unsupported.write_text("100,1\n", encoding="utf-8")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    window = CuratorWidget(ConsoleEventStore())

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


def test_viewport_text_inputs_update_export_text(qapp) -> None:
    window = CuratorWidget(ConsoleEventStore())

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


def test_wipe_runs_for_bounds_and_measurement_changes(qapp, tmp_path: Path, monkeypatch) -> None:
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

    window = CuratorWidget(ConsoleEventStore())
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


def test_export_graph_image_writes_16_by_9_png(qapp, tmp_path: Path) -> None:
    source = tmp_path / "curve.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")
    window = CuratorWidget(ConsoleEventStore())
    window.import_files([source])
    output = tmp_path / "poster.png"

    export_graph_image(window.graph_state, output, size=(1920, 1080))

    image = QImage(str(output))
    assert output.stat().st_size > 0
    assert image.width() == 1920
    assert image.height() == 1080


def test_fastgraph95_dark_export_uses_classic_frame_and_safe_bottom_margin(
    qapp, tmp_path: Path
) -> None:
    source = tmp_path / "curve.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")
    window = CuratorWidget(ConsoleEventStore(), theme=FASTGRAPH_95_DARK)
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
    for definition in theme_definitions():
        if definition.key == DITHER:
            continue
        assert definition.tokens.classic_controls is definition.tokens.retro_graph
    assert BRAND_TOKENS.classic_controls is BRAND_TOKENS.retro_graph


def test_dither_export_uses_tokens_and_excludes_gold_accent(qapp, tmp_path: Path) -> None:
    source = tmp_path / "dither-curve.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")
    window = CuratorWidget(ConsoleEventStore(), theme=DITHER)
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


def test_dither_export_masthead_is_filled_with_knocked_out_text(
    qapp, tmp_path: Path
) -> None:
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
    assert sum(
        image.pixelColor(x, y).rgba() == knockout
        for y in range(20, 100)
        for x in range(30, 770)
    ) > 500
    assert image.pixelColor(100, 124).name().upper() == DITHER_TOKENS.background


def test_dither_export_footer_is_square_filled_and_uses_knockout_text(
    qapp, tmp_path: Path
) -> None:
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


def test_dither_export_bounds_density_varies_from_edge_to_center(
    qapp, tmp_path: Path
) -> None:
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

    def coverage(y_start: int, y_stop: int) -> int:
        return sum(
            image.pixelColor(x, y).rgba() == ink
            for y in range(y_start, y_stop)
            for x in range(140, 640)
        )

    near_edges = coverage(229, 237) + coverage(344, 352)
    center = 2 * coverage(286, 294)
    assert near_edges > center * 2


def test_export_uses_gold_accent_constant() -> None:
    assert ACCENT_COLOR == "#FCBE11"


def test_frequency_markers_include_1k_3k_8k_and_10k_weights() -> None:
    graph_ticks = dict(GRAPH_FREQUENCY_TICKS)
    export_ticks = dict(EXPORT_FREQUENCY_TICKS)

    assert graph_ticks[1000] == "1k"
    assert graph_ticks[3000] == "3k"
    assert graph_ticks[8000] == "8k"
    assert graph_ticks[10000] == "10k"
    assert export_ticks[3000] == "3k"
    assert export_ticks[8000] == "8k"
    assert export_ticks[10000] == "10k"
    assert 8000 not in FREQUENCY_MARKERS
    assert FREQUENCY_MARKERS[1000][4] > FREQUENCY_MARKERS[3000][4]
    assert FREQUENCY_MARKERS[10000][4] > FREQUENCY_MARKERS[3000][4]
