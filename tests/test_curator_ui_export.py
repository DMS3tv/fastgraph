from pathlib import Path

import pytest
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QGroupBox, QPushButton

from dms.curator.export_image import ACCENT_COLOR, FREQUENCY_TICKS as EXPORT_FREQUENCY_TICKS, export_graph_image
from dms.ui.curator_graph_widget import FREQUENCY_MARKERS, FREQUENCY_TICKS as GRAPH_FREQUENCY_TICKS
import dms.ui.curator_widget as main_window_module
from dms.ui.curator_widget import CuratorWidget
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
    assert not hasattr(window, "_visible_check")
    assert not hasattr(window._graph, "_draw_reference_lines")
    assert window.graph_state.aspect_locked_25db is True

    window.set_y_limits(-30.0, 10.0)
    assert window.graph_state.y_min == -30.0
    assert window.graph_state.y_max == 10.0


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
    assert "View" in group_titles
    assert "Selected Layer" not in group_titles
    assert "Preference Bounds" not in group_titles
    assert "Export Text" not in group_titles

    row = window._layer_list.itemWidget(window._layer_list.item(0))
    assert row.visible_check is not None
    assert row.color_btn is not None
    assert row.offset_spin is not None
    assert row.hrtf_combo is not None

    row.offset_spin.setValue(-7.5)
    assert window.graph_state.layers[0].vertical_offset_db == -7.5


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
    assert window.graph_state.aspect_locked_25db is False

    window.set_y_limits(-40.0, 12.0)
    window._reset_view()

    assert window.graph_state.aspect_locked_25db is True
    assert window._aspect_lock_enabled.isChecked()
    assert window.graph_state.y_min == -20.0
    assert window.graph_state.y_max == 20.0


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
