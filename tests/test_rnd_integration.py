import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QToolButton

import dms.settings_manager as settings_module
import dms.ui.main_window as main_window_module
import dms.ui.rnd_widget as rnd_widget_module
from dms.rnd.models import RnDGroup, RnDMeasurement
from dms.session import SessionData
from dms.settings_manager import SettingsManager
from dms.theme import ThemeController
from dms.ui.main_window import AppState, MainWindow, RnDReviewDialog
from dms.ui.modern_spinbox import ModernDoubleSpinBox, ModernSpinBox


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _measurement(mid: str = "m1", name: str = "R&D One") -> RnDMeasurement:
    return RnDMeasurement(
        id=mid,
        name=name,
        freqs=np.array([100.0, 1000.0]),
        mag_db=np.array([1.0, 0.0]),
        metadata={"brand": "DMS", "model": "Demo", "rig": "Rig"},
        rig="Rig",
        input_device_label="Input",
        input_channel_index=0,
        input_channel_label="Channel 1",
        output_device_label="Output",
    )


def _window(qapp, monkeypatch, tmp_path: Path) -> MainWindow:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(MainWindow, "_refresh_devices", lambda self: None)
    monkeypatch.setattr(MainWindow, "_start_level_monitor", lambda self: None)
    monkeypatch.setattr(MainWindow, "_start_update_check", lambda self: None)
    settings = SettingsManager()
    return MainWindow(
        SessionData(rig="Rig", brand="DMS", model="Demo"),
        settings,
        ThemeController(qapp, settings),
    )


def test_rnd_tab_and_settings_folder_control(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)

    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "Measure", "R&&D", "Curator", "Automation", "Settings"
    ]
    window._settings_widget._rnd_session_dir.setText(str(tmp_path / "sessions"))
    window._settings_widget._rnd_session_dir.editingFinished.emit()
    assert window._settings.get("rnd_session_directory") == str(tmp_path / "sessions")
    window.close()


def test_rnd_rearranged_controls_notes_and_channel_sync(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)

    assert window._rnd_widget._top_toolbar.objectName() == "rnd_top_toolbar"
    assert window._rnd_widget._export_btn.parent().objectName() == "rnd_footer_controls"
    assert window._rnd_widget._notes_toggle.text() == "Notes"
    assert window._rnd_widget._notes_toggle.objectName() == "section_toggle"
    section_titles = {
        toggle.text()
        for toggle in window.findChildren(QToolButton, "section_toggle")
    }
    assert {"Devices", "Queue", "Notes"} <= section_titles
    assert window._start_queue_btn._has_persistent_outline() is True
    assert window._rnd_widget._measure_btn._has_persistent_outline() is True
    assert isinstance(window._queue_n_spin, ModernSpinBox)
    assert isinstance(window._queue_level_spin, ModernDoubleSpinBox)
    assert isinstance(window._rnd_widget._target_offset_spin, ModernDoubleSpinBox)
    assert window._plots._top_frame.radius == 10
    assert window._rnd_widget._plots.top_frame.radius == 10
    window._rnd_widget._notes_toggle.setChecked(False)
    window._rnd_widget._notes_toggle.clicked.emit(False)
    assert window._settings.get("rnd_notes_expanded") is False

    window._ch_combo.addItem("Ch 1", 0)
    window._ch_combo.addItem("Ch 2", 1)
    window._rnd_widget.set_input_channels([("Ch 1", 0), ("Ch 2", 1)], 0)
    window._rnd_widget._input_channel_combo.setCurrentIndex(1)
    assert window._current_input_channel() == 1
    window._ch_combo.setCurrentIndex(0)
    assert window._rnd_widget._input_channel_combo.currentData() == 0
    window.close()


def test_rnd_keep_review_creates_snapshot_measurement(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    window._state = AppState.PASS_FAIL
    window._pending_curve = (np.array([100.0, 1000.0]), np.array([1.0, 0.0]))
    window._input_device_labels_by_index = {1: "Input A"}
    window._output_device_labels_by_index = {2: "Output A"}
    window._in_dev_combo.addItem("Input A", 1)
    window._out_dev_combo.addItem("Output A", 2)
    window._ch_combo.addItem("Channel 1", 0)

    window._keep_rnd_measurement(change_status="changed", notes="Pad revision")

    measurement = window._rnd_widget.session.measurements[0]
    assert measurement.name == "DMS Demo - Rig - Input A - Channel 1"
    assert measurement.notes == "Pad revision"
    assert measurement.change_status == "changed"
    assert measurement.metadata["brand"] == "DMS"
    assert measurement.top_visible is True
    assert measurement.pinned is False
    window.close()


def test_rnd_selected_item_photo_panel_tracks_measurement_photos(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    measurement = _measurement()
    window._rnd_widget.add_measurement(measurement)

    photo = window._rnd_widget.photo_store.add_image(
        QImage(100, 100, QImage.Format.Format_RGB32), display_name="Pads", caption="New pads"
    )
    measurement.photos.append(photo)
    window._rnd_widget._sync_photo_panel()

    assert window._rnd_widget._photo_count.text() == "Photos (1)"
    assert window._rnd_widget._capture_photo_btn.isEnabled()
    assert window._rnd_widget._photo_strip_layout.count() == 2  # thumbnail + stretch
    window.close()


def test_rnd_fail_review_does_not_keep(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    window._state = AppState.PASS_FAIL
    window._pending_curve = (np.array([100.0, 1000.0]), np.array([1.0, 0.0]))
    monkeypatch.setattr(window, "_start_rnd_measurement", lambda: None)

    class _Dialog:
        def choice(self):
            return RnDReviewDialog.FAIL

    window._handle_rnd_review_choice(_Dialog())

    assert window._rnd_widget.session.measurements == []
    assert window._pending_curve is None
    window.close()


def test_rnd_group_toggles_and_curator_send(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    first = _measurement("m1", "First")
    second = _measurement("m2", "Second")
    second.mag_db = np.array([2.0, 1.0])
    group = RnDGroup(
        id="g1",
        name="Prototype A",
        visible=True,
        variation_enabled=True,
        measurement_ids=["m1", "m2"],
    )
    window._rnd_widget.session.measurements = [first, second]
    window._rnd_widget.session.groups = [group]
    window._rnd_widget.session.ungrouped_order = []
    window._rnd_widget.replace_session(window._rnd_widget.session)
    window._rnd_widget._select_id("g1")

    window._send_rnd_to_curator()

    layer = window._curator_widget.graph_state.layers[0]
    assert layer.name == "Prototype A VAR"
    assert layer.curve.kind == "variation"
    assert layer.curve.metadata["brand"] == "DMS"
    assert layer.curve.metadata["model"] == "Demo"
    assert layer.curve.metadata["rig"] == "Rig"
    assert "hrtf_name" not in layer.curve.metadata
    assert layer.curve.metadata["compensated"] is False
    assert window._tabs.currentWidget() is window._curator_widget
    window.close()


def test_rnd_group_variation_follows_group_viewport_visibility(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    first = _measurement("m1", "First")
    second = _measurement("m2", "Second")
    second.mag_db = np.array([2.0, 1.0])
    group = RnDGroup(
        id="g1",
        name="Prototype A",
        visible=True,
        pinned=False,
        variation_enabled=True,
        measurement_ids=["m1", "m2"],
    )
    window._rnd_widget.session.measurements = [first, second]
    window._rnd_widget.session.groups = [group]
    window._rnd_widget.session.ungrouped_order = []

    calls: list[dict] = []
    window._rnd_widget._plots.redraw = lambda **kwargs: calls.append(kwargs)
    window._rnd_widget._redraw()

    assert len(calls[-1]["top_group_variations"]) == 1
    assert calls[-1]["bottom_group_variations"] == []
    assert calls[-1]["top_measurements"] == []
    assert calls[-1]["pinned_measurements"] == []

    group.pinned = True
    window._rnd_widget._redraw()

    assert len(calls[-1]["top_group_variations"]) == 1
    assert len(calls[-1]["bottom_group_variations"]) == 1
    assert calls[-1]["top_measurements"] == []
    assert calls[-1]["pinned_measurements"] == []
    window.close()


def test_rnd_var_enabled_hides_traces_when_group_has_one_measurement(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    measurement = _measurement("m1", "Only")
    group = RnDGroup(
        id="g1",
        name="Prototype A",
        visible=True,
        variation_enabled=True,
        measurement_ids=["m1"],
    )
    window._rnd_widget.session.measurements = [measurement]
    window._rnd_widget.session.groups = [group]
    window._rnd_widget.session.ungrouped_order = []

    calls: list[dict] = []
    window._rnd_widget._plots.redraw = lambda **kwargs: calls.append(kwargs)
    window._rnd_widget._redraw()

    assert calls[-1]["top_group_variations"] == []
    assert calls[-1]["top_measurements"] == []
    assert window._rnd_widget._status_label.text() == "Ready - Var needs 2 measurements: Prototype A"
    window.close()


def test_rnd_group_bottom_toggle_shows_all_group_children(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    first = _measurement("m1", "First")
    second = _measurement("m2", "Second")
    group = RnDGroup(id="g1", name="Prototype A", pinned=True, measurement_ids=["m1", "m2"])
    window._rnd_widget.session.measurements = [first, second]
    window._rnd_widget.session.groups = [group]
    window._rnd_widget.session.ungrouped_order = []

    calls: list[dict] = []
    window._rnd_widget._plots.redraw = lambda **kwargs: calls.append(kwargs)
    window._rnd_widget._redraw()

    assert [measurement.name for measurement, _mag in calls[-1]["pinned_measurements"]] == ["First", "Second"]
    window.close()


def test_rnd_offsets_are_additive_for_display_and_curator_send(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    measurement = _measurement("m1", "Offset Target")
    measurement.vertical_offset_db = 3.0
    group = RnDGroup(
        id="g1",
        name="Prototype A",
        vertical_offset_db=2.0,
        measurement_ids=["m1"],
    )
    window._rnd_widget.session.measurements = [measurement]
    window._rnd_widget.session.groups = [group]
    window._rnd_widget.session.ungrouped_order = []
    window._rnd_widget.replace_session(window._rnd_widget.session)
    window._rnd_widget._select_id("m1")

    freqs, mag = window._rnd_widget.displayed_measurement_curve(measurement)

    assert np.allclose(freqs, [100.0, 1000.0])
    assert np.allclose(mag, [6.0, 5.0])

    window._send_rnd_to_curator()

    layer = window._curator_widget.graph_state.layers[0]
    assert layer.name == "Offset Target"
    assert np.allclose(layer.curve.mag_db, [6.0, 5.0])
    assert layer.curve.metadata["brand"] == "DMS"
    assert layer.curve.metadata["model"] == "Demo"
    assert layer.curve.metadata["rig"] == "Rig"
    window.close()


def test_rnd_offset_rows_update_selected_measurement_and_group(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    measurement = _measurement("m1", "Offset Target")
    group = RnDGroup(id="g1", name="Prototype A", measurement_ids=["m1"])
    window._rnd_widget.session.measurements = [measurement]
    window._rnd_widget.session.groups = [group]
    window._rnd_widget.session.ungrouped_order = []
    window._rnd_widget.replace_session(window._rnd_widget.session)

    window._rnd_widget._set_measurement_offset("m1", 4.5)
    assert measurement.vertical_offset_db == 4.5

    window._rnd_widget._set_group_offset("g1", -1.5)
    assert group.vertical_offset_db == -1.5
    window.close()


def test_rnd_group_variation_uses_displayed_offsets(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    first = _measurement("m1", "First")
    second = _measurement("m2", "Second")
    first.vertical_offset_db = 2.0
    second.mag_db = np.array([2.0, 1.0])
    second.vertical_offset_db = -1.0
    group = RnDGroup(
        id="g1",
        name="Prototype A",
        visible=True,
        variation_enabled=True,
        vertical_offset_db=3.0,
        measurement_ids=["m1", "m2"],
    )
    window._rnd_widget.session.measurements = [first, second]
    window._rnd_widget.session.groups = [group]
    window._rnd_widget.session.ungrouped_order = []

    captured: list[list[np.ndarray]] = []

    def fake_group_variation(measurements, smoothing_fraction=48):
        captured.append([np.array(item.mag_db, copy=True) for item in measurements])
        freqs = np.array([100.0, 1000.0])
        return (freqs, freqs * 0, freqs * 0, freqs * 0, freqs * 0, freqs * 0)

    monkeypatch.setattr(rnd_widget_module, "group_variation", fake_group_variation)
    window._rnd_widget._plots.redraw = lambda **_kwargs: None

    window._rnd_widget._redraw()

    assert np.allclose(captured[0][0], [6.0, 5.0])
    assert np.allclose(captured[0][1], [4.0, 3.0])
    window.close()


def test_rnd_bounds_toggle_passes_enabled_bounds_to_both_viewports(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    calls: list[dict] = []
    window._rnd_widget._plots.redraw = lambda **kwargs: calls.append(kwargs)

    window._rnd_widget._bounds_enabled.setChecked(True)

    assert window._rnd_widget.session.preference_bounds_enabled is True
    assert calls[-1]["preference_bounds"].enabled is True
    window.close()


def test_rnd_smoothing_control_changes_displayed_curve(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    measurement = _measurement("m1", "Smooth Me")
    calls: list[int] = []

    def fake_smooth(freqs, mag_db, fraction):
        calls.append(fraction)
        return freqs, mag_db + fraction

    monkeypatch.setattr(rnd_widget_module, "smooth_fractional_octave", fake_smooth)
    window._rnd_widget.session.smoothing_fraction = 12

    _freqs, mag = window._rnd_widget.displayed_measurement_curve(measurement)

    assert calls == [12]
    assert np.allclose(mag, [13.0, 12.0])
    window.close()


def test_rnd_export_uses_selected_smoothing(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    measurement = _measurement("m1", "Export Smooth")
    window._rnd_widget.session.smoothing_fraction = 6
    captured: dict[str, object] = {}

    def fake_smooth(freqs, mag_db, fraction):
        return freqs, mag_db + fraction

    def fake_export_curve(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(rnd_widget_module, "smooth_fractional_octave", fake_smooth)
    monkeypatch.setattr(main_window_module, "export_curve", fake_export_curve)

    window._export_rnd_measurement(measurement, str(tmp_path / "smooth.txt"))

    assert np.allclose(captured["mag_db"], [7.0, 6.0])
    window.close()


def test_rnd_delta_mode_uses_first_bottom_measurement_as_reference(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    first = _measurement("m1", "Reference")
    first.pinned = True
    second = _measurement("m2", "Delta")
    second.pinned = True
    second.mag_db = np.array([4.0, 1.5])
    window._rnd_widget.session.measurements = [first, second]
    window._rnd_widget.session.ungrouped_order = ["m1", "m2"]
    window._rnd_widget.session.delta_mode_enabled = True
    calls: list[dict] = []
    window._rnd_widget._plots.redraw = lambda **kwargs: calls.append(kwargs)

    window._rnd_widget._redraw()

    deltas = calls[-1]["delta_measurements"]
    assert calls[-1]["delta_mode_active"] is True
    assert len(deltas) == 1
    assert deltas[0][0].name == "Delta"
    assert np.allclose(deltas[0][2], [3.0, 1.5])
    window.close()


def test_rnd_delta_mode_with_one_bottom_item_hides_normal_bottom(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    measurement = _measurement("m1", "Reference")
    measurement.pinned = True
    window._rnd_widget.session.measurements = [measurement]
    window._rnd_widget.session.ungrouped_order = ["m1"]
    window._rnd_widget.session.delta_mode_enabled = True
    calls: list[dict] = []
    window._rnd_widget._plots.redraw = lambda **kwargs: calls.append(kwargs)

    window._rnd_widget._redraw()

    assert calls[-1]["delta_mode_active"] is True
    assert calls[-1]["delta_measurements"] == []
    assert window._rnd_widget._status_label.text() == "Ready - Delta Mode needs at least 2 bottom items"
    window.close()


def test_rnd_delta_mode_supports_group_variation_bands(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    group_a = RnDGroup(
        id="ga",
        name="A",
        pinned=True,
        variation_enabled=True,
        measurement_ids=["a1", "a2"],
    )
    group_b = RnDGroup(
        id="gb",
        name="B",
        pinned=True,
        variation_enabled=True,
        measurement_ids=["b1", "b2"],
    )
    measurements = [
        _measurement("a1", "A 1"),
        _measurement("a2", "A 2"),
        _measurement("b1", "B 1"),
        _measurement("b2", "B 2"),
    ]
    window._rnd_widget.session.measurements = measurements
    window._rnd_widget.session.groups = [group_a, group_b]
    window._rnd_widget.session.delta_mode_enabled = True

    def fake_group_variation(items, smoothing_fraction=48):
        freqs = np.array([100.0, 1000.0])
        base = 0.0 if items[0].name.startswith("A") else 5.0
        values = np.array([base, base])
        return (freqs, values - 2.0, values - 1.0, values + 1.0, values + 2.0, values)

    monkeypatch.setattr(rnd_widget_module, "group_variation", fake_group_variation)
    calls: list[dict] = []
    window._rnd_widget._plots.redraw = lambda **kwargs: calls.append(kwargs)

    window._rnd_widget._redraw()

    variations = calls[-1]["delta_group_variations"]
    assert len(variations) == 1
    _group, (_freqs, p10, p25, p75, p90, median) = variations[0]
    assert np.allclose(p10, [3.0, 3.0])
    assert np.allclose(p25, [4.0, 4.0])
    assert np.allclose(median, [5.0, 5.0])
    assert np.allclose(p75, [6.0, 6.0])
    assert np.allclose(p90, [7.0, 7.0])
    window.close()


def test_rnd_tree_multiselect_and_scrollbar_are_enabled(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)

    assert window._rnd_widget._tree.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
    assert window._rnd_widget._tree.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert window._rnd_widget._tree.styleSheet() == ""
    assert "QScrollBar:vertical" in qapp.styleSheet()
    assert "QScrollBar:horizontal" in qapp.styleSheet()
    window.close()


def test_rnd_new_group_moves_selected_measurements_in_visual_order(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    first = _measurement("m1", "First")
    second = _measurement("m2", "Second")
    third = _measurement("m3", "Third")
    window._rnd_widget.session.measurements = [first, second, third]
    window._rnd_widget.session.ungrouped_order = ["m1", "m2", "m3"]
    window._rnd_widget.replace_session(window._rnd_widget.session)
    for item in window._rnd_widget._walk_items():
        if item.data(0, rnd_widget_module.ROLE_ID) in {"m1", "m3"}:
            item.setSelected(True)

    window._rnd_widget._new_group()

    group = window._rnd_widget.session.groups[0]
    assert group.measurement_ids == ["m1", "m3"]
    assert window._rnd_widget.session.ungrouped_order == ["m2"]
    window.close()


def test_rnd_target_is_passed_to_viewports_with_offset(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    calls: list[dict] = []
    window._rnd_widget._plots.redraw = lambda **kwargs: calls.append(kwargs)

    window._rnd_widget.session.target_visible = True
    window._rnd_widget.session.target_name = "House Target"
    window._rnd_widget.session.target_freqs = np.array([100.0, 1000.0])
    window._rnd_widget.session.target_mag_db = np.array([1.0, 2.0])
    window._rnd_widget.session.target_offset_db = -3.0
    window._rnd_widget._redraw()

    name, freqs, mag_db = calls[-1]["target_curve"]
    assert name == "House Target"
    assert np.allclose(freqs, [100.0, 1000.0])
    assert np.allclose(mag_db, [-2.0, -1.0])
    window.close()


def test_rnd_default_hrtf_is_applied_to_new_measurements(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    window._rnd_widget._default_hrtf_combo.addItem("Fixture", "fixture.txt")
    window._rnd_widget._default_hrtf_combo.setCurrentIndex(
        window._rnd_widget._default_hrtf_combo.findData("fixture.txt")
    )

    window._rnd_widget.add_measurement(_measurement("m1", "Default HRTF"))

    assert window._rnd_widget.session.hrtf_path == "fixture.txt"
    assert window._rnd_widget.session.measurements[0].hrtf_path == "fixture.txt"
    window.close()


def test_rnd_hrtf_path_resolves_by_saved_name_on_new_machine(qapp, monkeypatch, tmp_path: Path) -> None:
    hrtf_dir = tmp_path / "HRTFs"
    hrtf_dir.mkdir()
    hrtf_path = hrtf_dir / "Fixture A.txt"
    hrtf_path.write_text("100 1\n1000 2\n", encoding="utf-8")
    monkeypatch.setattr(rnd_widget_module, "HRTF_DIR", hrtf_dir)
    window = _window(qapp, monkeypatch, tmp_path)
    measurement = _measurement("m1", "Portable HRTF")
    measurement.hrtf_name = "Fixture A"
    measurement.hrtf_path = str(tmp_path / "old" / "Fixture A.txt")

    resolved = window._rnd_widget.resolve_hrtf_path(measurement.hrtf_path, measurement.hrtf_name)
    _freqs, mag = window._rnd_widget.displayed_measurement_curve(measurement)

    assert resolved == str(hrtf_path)
    assert np.allclose(mag, [0.0, -2.0])
    window.close()


def test_rnd_export_blocks_missing_hrtf(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    measurement = _measurement("m1", "Missing HRTF")
    measurement.hrtf_name = "Fixture Gone"
    measurement.hrtf_path = str(tmp_path / "missing" / "Fixture Gone.txt")
    warnings: list[tuple] = []
    monkeypatch.setattr(main_window_module.QMessageBox, "warning", lambda *args: warnings.append(args))

    window._export_rnd_measurement(measurement, str(tmp_path / "out.txt"))

    assert warnings
    assert not (tmp_path / "out.txt").exists()
    window.close()


def test_rnd_group_rows_receive_distinct_default_colors(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)

    window._rnd_widget._new_group()
    window._rnd_widget._new_group()

    colors = [group.color for group in window._rnd_widget.session.groups]
    assert len(set(colors)) == 2
    window.close()


def test_rnd_variation_renderer_draws_outer_inner_and_median(qapp) -> None:
    plot_widget = rnd_widget_module.RnDPlotWidget()
    freqs = np.array([100.0, 1000.0, 10000.0])
    variation = (
        freqs,
        np.array([-5.0, -4.0, -3.0]),
        np.array([-2.0, -1.0, 0.0]),
        np.array([2.0, 3.0, 4.0]),
        np.array([5.0, 6.0, 7.0]),
        np.array([0.0, 1.0, 2.0]),
    )

    curves = plot_widget._draw_variation(
        plot_widget.top_plot,
        RnDGroup(id="g1", name="Prototype A"),
        variation,
    )

    assert len(curves) == 2
    assert len(plot_widget._items) == 8
    plot_widget.close()


def test_rnd_merge_session_remaps_colliding_ids_and_names(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    window._rnd_widget.add_measurement(_measurement("same", "Duplicate"))
    incoming = window._rnd_widget.session.__class__(
        measurements=[_measurement("same", "Duplicate")],
        groups=[RnDGroup(id="group", name="Imported", measurement_ids=["same"])],
        ungrouped_order=[],
    )

    window._rnd_widget.merge_session(incoming)

    ids = [item.id for item in window._rnd_widget.session.measurements]
    names = [item.name for item in window._rnd_widget.session.measurements]
    assert len(set(ids)) == 2
    assert names == ["Duplicate", "Duplicate (2)"]
    assert window._rnd_widget.session.groups[0].measurement_ids[0] != "same"
    window.close()
