from pathlib import Path

import numpy as np
import pyqtgraph as pg
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QColorDialog, QGroupBox, QLabel, QMessageBox, QPushButton, QWidget

import dms.ui.curator_widget as main_window_module
from dms.curator.models import CurveData, PreferenceBounds
from dms.processing import VariationBand
from dms.style_tokens import tokens_for
from dms.theme import (
    DARK,
    DITHER,
    FASTGRAPH_95,
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
