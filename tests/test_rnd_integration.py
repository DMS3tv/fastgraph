import os

import numpy as np
import pyqtgraph as pg
import pytest
from helpers import rnd_measurement
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QCheckBox,
    QDialog,
    QMessageBox,
    QToolButton,
    QWidget,
)

import dms.ui.rnd_bridge as rnd_bridge_module
import dms.ui.rnd_widget as rnd_widget_module
from dms.hrtf import HRTFCurve
from dms.measure_queue import QueueState
from dms.processing import VariationBand
from dms.rnd.models import RnDGroup
from dms.rnd.persistence import save_rnd_session
from dms.rnd.photos import RnDPhotoStore
from dms.theme import FASTGRAPH_95_DARK, HACKERMAN_95
from dms.ui.measure_dialogs import RnDReviewDialog
from dms.ui.modern_spinbox import ModernDoubleSpinBox, ModernSpinBox


def test_rnd_tab_and_settings_folder_control(tmp_path, make_main_window) -> None:
    window = make_main_window()

    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "Measure",
        "R&&D",
        "Curator",
        "Automation",
        "Settings",
    ]
    window._settings_widget._rnd_session_dir.setText(str(tmp_path / "sessions"))
    window._settings_widget._rnd_session_dir.editingFinished.emit()
    assert window._settings.get("rnd_session_directory") == str(tmp_path / "sessions")


def test_rnd_rearranged_controls_notes_and_channel_sync(make_main_window) -> None:
    window = make_main_window()

    assert window._rnd_widget._top_toolbar.objectName() == "rnd_top_toolbar"
    assert window._rnd_widget._export_btn.parent().objectName() == "rnd_footer_controls"
    assert window._rnd_widget._notes_toggle.text() == "Notes"
    assert window._rnd_widget._notes_toggle.objectName() == "section_toggle"
    section_titles = {
        toggle.text() for toggle in window.findChildren(QToolButton, "section_toggle")
    }
    assert "Notes" in section_titles
    assert "Devices" not in section_titles
    assert "Queue" not in section_titles
    assert window._plots._header_widget.objectName() == "measure_queue_bar"
    assert window.measure_tab.start_queue_btn.text() == "Measure"
    assert window.measure_tab.start_queue_btn._has_persistent_outline() is True
    assert window._rnd_widget._measure_btn._has_persistent_outline() is True
    assert isinstance(window.measure_tab.queue_n_spin, ModernSpinBox)
    assert isinstance(window.measure_tab.queue_level_spin, ModernDoubleSpinBox)
    assert isinstance(window._rnd_widget._target_offset_spin, ModernDoubleSpinBox)
    assert window._plots.single._top_frame.radius == 10
    assert window._rnd_widget._plots.top_frame.radius == 10
    window._rnd_widget._notes_toggle.setChecked(False)
    window._rnd_widget._notes_toggle.clicked.emit(False)
    assert window._settings.get("rnd_notes_expanded") is False

    window.measure_tab.ch_combo.addItem("Ch 1", 0)
    window.measure_tab.ch_combo.addItem("Ch 2", 1)
    window._rnd_widget.set_input_channels([("Ch 1", 0), ("Ch 2", 1)], 0)
    window._rnd_widget._input_channel_combo.setCurrentIndex(1)
    assert window.devices.current_input_channel() == 1
    window.measure_tab.ch_combo.setCurrentIndex(0)
    assert window._rnd_widget._input_channel_combo.currentData() == 0


def test_rnd_toolbar_uses_compact_stacked_rows(qapp, make_main_window) -> None:
    window = make_main_window()
    window.resize(1280, 700)
    window.show()
    window._tabs.setCurrentIndex(1)
    qapp.processEvents()

    toolbar = window._rnd_widget._top_toolbar
    layout = window._rnd_widget._top_toolbar_layout
    measure = toolbar.findChild(QWidget, "rnd_measure_controls")
    target = toolbar.findChild(QWidget, "rnd_target_controls")
    assert layout.direction() == QBoxLayout.Direction.TopToBottom
    assert layout.stretch(0) == 0
    assert layout.stretch(1) == 0
    assert toolbar.height() <= measure.height() + target.height() + 24
    status_right = window._rnd_widget._status_label.geometry().right()
    measure_left = window._rnd_widget._measure_btn.geometry().left()
    assert measure_left - status_right <= 24

    window.resize(2400, 1100)
    qapp.processEvents()
    assert layout.direction() == QBoxLayout.Direction.LeftToRight
    assert layout.stretch(0) == 1
    assert layout.stretch(1) == 1


def test_rnd_keep_review_creates_snapshot_measurement(make_main_window) -> None:
    window = make_main_window()
    window.measure.queue.state = QueueState.PASS_FAIL
    window.measure.queue.pending_curve = (np.array([100.0, 1000.0]), np.array([1.0, 0.0]))
    window.devices._input_device_labels_by_index = {1: "Input A"}
    window.devices._output_device_labels_by_index = {2: "Output A"}
    window.measure_tab.in_dev_combo.addItem("Input A", 1)
    window.measure_tab.out_dev_combo.addItem("Output A", 2)
    window.measure_tab.ch_combo.clear()
    window.measure_tab.ch_combo.addItem("Channel 1", 0)

    window.rnd._keep_rnd_measurement(change_status="changed", notes="Pad revision")

    measurement = window._rnd_widget.session.measurements[0]
    assert measurement.name == "DMS Demo - Rig - Input A - Channel 1"
    assert measurement.notes == "Pad revision"
    assert measurement.change_status == "changed"
    assert measurement.metadata["brand"] == "DMS"
    assert measurement.top_visible is True
    assert measurement.pinned is False


def test_rnd_review_curve_is_temporary_and_copied(make_main_window) -> None:
    window = make_main_window()
    freqs = np.array([100.0, 1000.0])
    mag_db = np.array([1.0, 0.0])
    window.rnd.dirty = False

    window._rnd_widget.set_review_curve((freqs, mag_db))
    freqs[:] = 0.0
    mag_db[:] = 9.0

    preview = window._rnd_widget._review_curve
    assert preview is not None
    np.testing.assert_array_equal(preview[0], np.array([100.0, 1000.0]))
    np.testing.assert_array_equal(preview[1], np.array([1.0, 0.0]))
    assert window._rnd_widget.session.measurements == []
    assert window.rnd.dirty is False


def test_rnd_selected_item_photo_panel_tracks_measurement_photos(make_main_window) -> None:
    window = make_main_window()
    measurement = rnd_measurement()
    window._rnd_widget.add_measurement(measurement)

    photo = window._rnd_widget.photo_store.add_image(
        QImage(100, 100, QImage.Format.Format_RGB32), display_name="Pads", caption="New pads"
    )
    measurement.photos.append(photo)
    window._rnd_widget._sync_photo_panel()

    assert window._rnd_widget._photo_count.text() == "Photos (1)"
    assert window._rnd_widget._capture_photo_btn.isEnabled()
    assert window._rnd_widget._photo_strip_layout.count() == 2  # thumbnail + stretch


def test_rnd_fail_review_does_not_keep(make_main_window) -> None:
    window = make_main_window()
    window.measure.queue.state = QueueState.PASS_FAIL
    window.measure.queue.pending_curve = (np.array([100.0, 1000.0]), np.array([1.0, 0.0]))
    window._rnd_widget.set_review_curve(window.measure.queue.pending_curve)
    window.rnd.start_measurement = lambda: None

    class _Dialog:
        def choice(self):
            return RnDReviewDialog.FAIL

    window.rnd._handle_rnd_review_choice(_Dialog())

    assert window._rnd_widget.session.measurements == []
    assert window.measure.queue.pending_curve is None
    assert window._rnd_widget._review_curve is None


def test_rnd_dirty_state_ignores_selection_and_tracks_content(make_main_window) -> None:
    window = make_main_window()
    window.rnd._recovery.enable()

    window._rnd_widget.add_measurement(rnd_measurement())
    assert window.rnd.dirty is True

    window.rnd.dirty = False
    window._rnd_widget.selection_changed.emit()
    assert window.rnd.dirty is False

    window._rnd_widget._notes_edit.setPlainText("Changed")
    assert window.rnd.dirty is True


def test_rnd_manual_save_and_load_modes_update_dirty_state(
    tmp_path, monkeypatch, make_main_window
) -> None:
    window = make_main_window()
    window._rnd_widget.add_measurement(rnd_measurement("current", "Current"))
    save_path = tmp_path / "saved.fastgraph-rnd.json"
    monkeypatch.setattr(
        rnd_bridge_module.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(save_path), ""),
    )

    assert window.rnd.save_session() is True
    assert window.rnd.dirty is False

    incoming_path = tmp_path / "incoming.fastgraph-rnd.json"
    incoming = window._rnd_widget.session.__class__(
        measurements=[rnd_measurement("incoming", "Incoming")],
        ungrouped_order=["incoming"],
    )
    save_rnd_session(incoming, RnDPhotoStore(), incoming_path)
    monkeypatch.setattr(
        rnd_bridge_module.QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(incoming_path), ""),
    )
    window.rnd._choose_rnd_load_mode = lambda: "clear"
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )

    window.rnd.load_session()
    assert window._rnd_widget.session.measurements[0].name == "Incoming"
    assert window.rnd.dirty is False

    merge_path = tmp_path / "merge.fastgraph-rnd.json"
    merge = window._rnd_widget.session.__class__(
        measurements=[rnd_measurement("merge", "Merge")],
        ungrouped_order=["merge"],
    )
    save_rnd_session(merge, RnDPhotoStore(), merge_path)
    monkeypatch.setattr(
        rnd_bridge_module.QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(merge_path), ""),
    )
    window.rnd._choose_rnd_load_mode = lambda: "merge"

    window.rnd.load_session()
    assert window.rnd.dirty is True


def test_rnd_manual_save_completes_partial_extension(
    tmp_path, monkeypatch, make_main_window
) -> None:
    window = make_main_window()
    window._rnd_widget.add_measurement(rnd_measurement())
    selected_path = tmp_path / "prototype.fastgraph-rnd"
    expected_path = tmp_path / "prototype.fastgraph-rnd.json"
    monkeypatch.setattr(
        rnd_bridge_module.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(selected_path), ""),
    )

    assert window.rnd.save_session() is True
    assert expected_path.is_file()
    assert not selected_path.exists()
    assert window.rnd.dirty is False


def test_rnd_recovery_warning_persists_until_success(make_main_window) -> None:
    window = make_main_window()

    window.rnd._on_rnd_recovery_failed("disk full")
    window._rnd_widget.set_status("Ready")
    assert window._rnd_widget._status_label.text() == "R&D recovery save failed"

    window.rnd._on_rnd_recovery_saved()
    assert window._rnd_widget._status_label.text() == "Ready"


@pytest.mark.parametrize(
    ("result", "save_result", "expected"),
    [
        (QMessageBox.StandardButton.Discard, True, True),
        (QMessageBox.StandardButton.Cancel, True, False),
        (QMessageBox.StandardButton.Save, True, True),
        (QMessageBox.StandardButton.Save, False, False),
    ],
)
def test_rnd_close_prompt_paths(
    monkeypatch,
    make_main_window,
    result,
    save_result,
    expected,
) -> None:
    window = make_main_window(confirm_rnd_close=False)
    window._rnd_widget.add_measurement(rnd_measurement())
    monkeypatch.setattr(QMessageBox, "exec", lambda self: result)
    window.rnd.save_session = lambda: save_result

    assert window.rnd.confirm_close() is expected
    window.rnd.confirm_close = lambda: True


def test_startup_recovery_restores_before_app_start_automation(
    monkeypatch,
    make_main_window,
) -> None:
    window = make_main_window()
    recovered = rnd_measurement("recovered", "Recovered")
    session = window._rnd_widget.session.__class__(
        measurements=[recovered],
        ungrouped_order=["recovered"],
    )
    save_rnd_session(
        session,
        RnDPhotoStore(),
        window.rnd._recovery.current_path,
        cleanup_stale_photos=False,
    )
    events: list[tuple[str, int]] = []
    monkeypatch.setattr(
        window.commands,
        "trigger",
        lambda trigger: events.append((trigger, len(window._rnd_widget.session.measurements))),
    )

    class RestoreDialog:
        RESTORE = "restore"
        DISCARD = "discard"
        KEEP = "keep"
        action = RESTORE

        def __init__(self, candidates, parent):
            self._candidate = candidates[0]

        def exec(self):
            return 0

        def selected_candidate(self):
            return self._candidate

    monkeypatch.setattr(rnd_bridge_module, "RnDRecoveryDialog", RestoreDialog)
    window.rnd.initialize_recovery()

    assert window._rnd_widget.session.measurements[0].name == "Recovered"
    assert window.rnd.dirty is True
    assert events == [("app_start", 1)]


def test_rnd_group_toggles_and_curator_send(make_main_window) -> None:
    window = make_main_window()
    first = rnd_measurement("m1", "First")
    second = rnd_measurement("m2", "Second")
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

    window.rnd.send_rnd_to_curator()

    layer = window._curator_widget.graph_state.layers[0]
    assert layer.name == "Prototype A VAR"
    assert layer.curve.kind == "variation"
    assert layer.curve.metadata["brand"] == "DMS"
    assert layer.curve.metadata["model"] == "Demo"
    assert layer.curve.metadata["rig"] == "Rig"
    assert "hrtf_name" not in layer.curve.metadata
    assert layer.curve.metadata["compensated"] is False
    assert window._tabs.currentWidget() is window._curator_widget


def test_rnd_group_variation_follows_group_viewport_visibility(make_main_window) -> None:
    window = make_main_window()
    first = rnd_measurement("m1", "First")
    second = rnd_measurement("m2", "Second")
    second.mag_db = np.array([2.0, 1.0])
    # View 2 draws the rows that are pinned, so the band needs pinned rows.
    first.pinned = True
    second.pinned = True
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


def test_rnd_var_enabled_hides_traces_when_group_has_one_measurement(make_main_window) -> None:
    window = make_main_window()
    measurement = rnd_measurement("m1", "Only")
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
    assert (
        window._rnd_widget._status_label.text() == "Ready - Var needs 2 measurements: Prototype A"
    )


def test_rnd_group_view_2_honours_each_row_checkbox(make_main_window) -> None:
    """C2: a group's View 2 toggle gates the group; the row gates the row."""
    window = make_main_window()
    first = rnd_measurement("m1", "First")
    second = rnd_measurement("m2", "Second")
    first.pinned = True
    second.pinned = False
    group = RnDGroup(id="g1", name="Prototype A", pinned=True, measurement_ids=["m1", "m2"])
    window._rnd_widget.session.measurements = [first, second]
    window._rnd_widget.session.groups = [group]
    window._rnd_widget.session.ungrouped_order = []

    calls: list[dict] = []
    window._rnd_widget._plots.redraw = lambda **kwargs: calls.append(kwargs)
    window._rnd_widget._redraw()

    assert [measurement.name for measurement, _mag in calls[-1]["pinned_measurements"]] == ["First"]

    second.pinned = True
    window._rnd_widget._redraw()
    assert [measurement.name for measurement, _mag in calls[-1]["pinned_measurements"]] == [
        "First",
        "Second",
    ]

    group.pinned = False
    window._rnd_widget._redraw()
    assert calls[-1]["pinned_measurements"] == []


def test_rnd_offsets_are_additive_for_display_and_curator_send(make_main_window) -> None:
    window = make_main_window()
    measurement = rnd_measurement("m1", "Offset Target")
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

    window.rnd.send_rnd_to_curator()

    layer = window._curator_widget.graph_state.layers[0]
    assert layer.name == "Offset Target"
    assert np.allclose(layer.curve.mag_db, [6.0, 5.0])
    assert layer.curve.metadata["brand"] == "DMS"
    assert layer.curve.metadata["model"] == "Demo"
    assert layer.curve.metadata["rig"] == "Rig"


def test_rnd_offset_rows_update_selected_measurement_and_group(make_main_window) -> None:
    window = make_main_window()
    measurement = rnd_measurement("m1", "Offset Target")
    group = RnDGroup(id="g1", name="Prototype A", measurement_ids=["m1"])
    window._rnd_widget.session.measurements = [measurement]
    window._rnd_widget.session.groups = [group]
    window._rnd_widget.session.ungrouped_order = []
    window._rnd_widget.replace_session(window._rnd_widget.session)

    window._rnd_widget._set_measurement_offset("m1", 4.5)
    assert measurement.vertical_offset_db == 4.5

    window._rnd_widget._set_group_offset("g1", -1.5)
    assert group.vertical_offset_db == -1.5


def test_rnd_group_variation_uses_displayed_offsets(monkeypatch, make_main_window) -> None:
    window = make_main_window()
    first = rnd_measurement("m1", "First")
    second = rnd_measurement("m2", "Second")
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
        return VariationBand(freqs, freqs * 0, freqs * 0, freqs * 0, freqs * 0, freqs * 0)

    monkeypatch.setattr(rnd_widget_module, "group_variation", fake_group_variation)
    window._rnd_widget._plots.redraw = lambda **_kwargs: None

    window._rnd_widget._redraw()

    assert np.allclose(captured[0][0], [6.0, 5.0])
    assert np.allclose(captured[0][1], [4.0, 3.0])


def test_rnd_bounds_toggle_passes_enabled_bounds_to_both_viewports(make_main_window) -> None:
    window = make_main_window()
    calls: list[dict] = []
    window._rnd_widget._plots.redraw = lambda **kwargs: calls.append(kwargs)

    window._rnd_widget._bounds_enabled.setChecked(True)

    assert window._rnd_widget.session.preference_bounds_enabled is True
    assert calls[-1]["preference_bounds"].enabled is True


def test_rnd_smoothing_control_changes_displayed_curve(monkeypatch, make_main_window) -> None:
    window = make_main_window()
    measurement = rnd_measurement("m1", "Smooth Me")
    calls: list[int] = []

    def fake_smooth(freqs, mag_db, fraction):
        calls.append(fraction)
        return freqs, mag_db + fraction

    monkeypatch.setattr(rnd_widget_module, "smooth_fractional_octave", fake_smooth)
    window._rnd_widget.session.smoothing_fraction = 12

    _freqs, mag = window._rnd_widget.displayed_measurement_curve(measurement)

    assert calls == [12]
    assert np.allclose(mag, [13.0, 12.0])


def test_rnd_export_uses_selected_smoothing(tmp_path, monkeypatch, make_main_window) -> None:
    window = make_main_window()
    measurement = rnd_measurement("m1", "Export Smooth")
    window._rnd_widget.session.smoothing_fraction = 6
    captured: dict[str, object] = {}

    def fake_smooth(freqs, mag_db, fraction):
        return freqs, mag_db + fraction

    def fake_export_curve(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(rnd_widget_module, "smooth_fractional_octave", fake_smooth)
    monkeypatch.setattr(rnd_bridge_module, "export_curve", fake_export_curve)

    window.rnd._export_rnd_measurement(measurement, str(tmp_path / "smooth.txt"))

    assert np.allclose(captured["mag_db"], [7.0, 6.0])


def test_rnd_export_header_records_smoothing_and_offset(tmp_path, make_main_window) -> None:
    window = make_main_window()
    measurement = rnd_measurement("m1", "Export Offset")
    measurement.vertical_offset_db = -3.5
    window._rnd_widget.session.smoothing_fraction = 12
    output = tmp_path / "offset.txt"

    window.rnd._export_rnd_measurement(measurement, str(output))

    text = output.read_text(encoding="utf-8")
    assert "* Smoothing: 1/12 octave" in text
    assert "* Offset: -3.5 dB" in text


def test_rnd_delta_mode_uses_first_bottom_measurement_as_reference(make_main_window) -> None:
    window = make_main_window()
    first = rnd_measurement("m1", "Reference")
    first.pinned = True
    second = rnd_measurement("m2", "Delta")
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


def test_rnd_delta_mode_with_one_bottom_item_hides_normal_bottom(make_main_window) -> None:
    window = make_main_window()
    measurement = rnd_measurement("m1", "Reference")
    measurement.pinned = True
    window._rnd_widget.session.measurements = [measurement]
    window._rnd_widget.session.ungrouped_order = ["m1"]
    window._rnd_widget.session.delta_mode_enabled = True
    calls: list[dict] = []
    window._rnd_widget._plots.redraw = lambda **kwargs: calls.append(kwargs)

    window._rnd_widget._redraw()

    assert calls[-1]["delta_mode_active"] is True
    assert calls[-1]["delta_measurements"] == []
    assert (
        window._rnd_widget._status_label.text()
        == "Ready - Delta Mode needs at least 2 bottom items"
    )


def test_rnd_delta_mode_supports_group_variation_bands(monkeypatch, make_main_window) -> None:
    window = make_main_window()
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
        rnd_measurement("a1", "A 1"),
        rnd_measurement("a2", "A 2"),
        rnd_measurement("b1", "B 1"),
        rnd_measurement("b2", "B 2"),
    ]
    for measurement in measurements:
        measurement.pinned = True
    window._rnd_widget.session.measurements = measurements
    window._rnd_widget.session.groups = [group_a, group_b]
    window._rnd_widget.session.delta_mode_enabled = True

    def fake_group_variation(items, smoothing_fraction=48):
        freqs = np.array([100.0, 1000.0])
        base = 0.0 if items[0].name.startswith("A") else 5.0
        values = np.array([base, base])
        return VariationBand(freqs, values - 2.0, values - 1.0, values, values + 1.0, values + 2.0)

    monkeypatch.setattr(rnd_widget_module, "group_variation", fake_group_variation)
    calls: list[dict] = []
    window._rnd_widget._plots.redraw = lambda **kwargs: calls.append(kwargs)

    window._rnd_widget._redraw()

    variations = calls[-1]["delta_group_variations"]
    assert len(variations) == 1
    _group, band = variations[0]
    assert np.allclose(band.p10, [3.0, 3.0])
    assert np.allclose(band.p25, [4.0, 4.0])
    assert np.allclose(band.median, [5.0, 5.0])
    assert np.allclose(band.p75, [6.0, 6.0])
    assert np.allclose(band.p90, [7.0, 7.0])


def test_rnd_tree_multiselect_and_scrollbar_are_enabled(qapp, make_main_window) -> None:
    window = make_main_window()

    assert (
        window._rnd_widget._tree.selectionMode()
        == QAbstractItemView.SelectionMode.ExtendedSelection
    )
    assert (
        window._rnd_widget._tree.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )
    assert window._rnd_widget._tree.styleSheet() == ""
    assert "QScrollBar:vertical" in qapp.styleSheet()
    assert "QScrollBar:horizontal" in qapp.styleSheet()


def test_rnd_new_group_moves_selected_measurements_in_visual_order(make_main_window) -> None:
    window = make_main_window()
    first = rnd_measurement("m1", "First")
    second = rnd_measurement("m2", "Second")
    third = rnd_measurement("m3", "Third")
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


def test_rnd_target_is_passed_to_viewports_with_offset(make_main_window) -> None:
    window = make_main_window()
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


def test_rnd_default_hrtf_is_applied_to_new_measurements(make_main_window) -> None:
    window = make_main_window()
    window._rnd_widget._default_hrtf_combo.addItem("Fixture", "fixture.txt")
    window._rnd_widget._default_hrtf_combo.setCurrentIndex(
        window._rnd_widget._default_hrtf_combo.findData("fixture.txt")
    )

    window._rnd_widget.add_measurement(rnd_measurement("m1", "Default HRTF"))

    assert window._rnd_widget.session.hrtf_path == "fixture.txt"
    assert window._rnd_widget.session.measurements[0].hrtf_path == "fixture.txt"


def test_rnd_hrtf_path_resolves_by_saved_name_on_new_machine(
    tmp_path, monkeypatch, make_main_window
) -> None:
    hrtf_dir = tmp_path / "HRTFs"
    hrtf_dir.mkdir()
    hrtf_path = hrtf_dir / "Fixture A.txt"
    hrtf_path.write_text("100 1\n1000 2\n", encoding="utf-8")
    monkeypatch.setattr(rnd_widget_module, "HRTF_DIR", hrtf_dir)
    window = make_main_window()
    measurement = rnd_measurement("m1", "Portable HRTF")
    measurement.hrtf_name = "Fixture A"
    measurement.hrtf_path = str(tmp_path / "old" / "Fixture A.txt")

    resolved = window._rnd_widget.resolve_hrtf_path(measurement.hrtf_path, measurement.hrtf_name)
    _freqs, mag = window._rnd_widget.displayed_measurement_curve(measurement)

    assert resolved == str(hrtf_path)
    assert np.allclose(mag, [0.0, -2.0])


def test_rnd_export_blocks_missing_hrtf(tmp_path, monkeypatch, make_main_window) -> None:
    window = make_main_window()
    measurement = rnd_measurement("m1", "Missing HRTF")
    measurement.hrtf_name = "Fixture Gone"
    measurement.hrtf_path = str(tmp_path / "missing" / "Fixture Gone.txt")
    warnings: list[tuple] = []
    monkeypatch.setattr(
        rnd_bridge_module.QMessageBox, "warning", lambda *args: warnings.append(args)
    )

    window.rnd._export_rnd_measurement(measurement, str(tmp_path / "out.txt"))

    assert warnings
    assert not (tmp_path / "out.txt").exists()


def test_rnd_group_rows_receive_distinct_default_colors(make_main_window) -> None:
    window = make_main_window()

    window._rnd_widget._new_group()
    window._rnd_widget._new_group()

    colors = [group.color for group in window._rnd_widget.session.groups]
    assert len(set(colors)) == 2


def test_rnd_variation_renderer_draws_outer_inner_and_median(qapp) -> None:
    plot_widget = rnd_widget_module.RnDPlotWidget()
    freqs = np.array([100.0, 1000.0, 10000.0])
    variation = VariationBand(
        freqs,
        p10=np.array([-5.0, -4.0, -3.0]),
        p25=np.array([-2.0, -1.0, 0.0]),
        median=np.array([0.0, 1.0, 2.0]),
        p75=np.array([2.0, 3.0, 4.0]),
        p90=np.array([5.0, 6.0, 7.0]),
    )

    curves = plot_widget._draw_variation(
        plot_widget.top_plot,
        RnDGroup(id="g1", name="Prototype A"),
        variation,
    )

    assert len(curves) == 2
    assert len(plot_widget._items) == 8
    plot_widget.close()


def test_rnd_merge_session_remaps_colliding_ids_and_names(make_main_window) -> None:
    window = make_main_window()
    window._rnd_widget.add_measurement(rnd_measurement("same", "Duplicate"))
    incoming = window._rnd_widget.session.__class__(
        measurements=[rnd_measurement("same", "Duplicate")],
        groups=[RnDGroup(id="group", name="Imported", measurement_ids=["same"])],
        ungrouped_order=[],
    )

    window._rnd_widget.merge_session(incoming)

    ids = [item.id for item in window._rnd_widget.session.measurements]
    names = [item.name for item in window._rnd_widget.session.measurements]
    assert len(set(ids)) == 2
    assert names == ["Duplicate", "Duplicate (2)"]
    assert window._rnd_widget.session.groups[0].measurement_ids[0] != "same"


def test_measure_export_row_has_send_to_rnd_and_compact_directory(make_main_window) -> None:
    window = make_main_window()
    layout = window._plots._footer_widget.layout()

    assert layout.indexOf(window.measure_tab.send_to_rnd_btn) < layout.indexOf(
        window.measure_tab.export_btn
    )
    assert window.measure_tab.export_dir_input.minimumWidth() == 140
    assert window.measure_tab.export_dir_input.maximumWidth() == 240

    window.measure_io.sync_export_button()
    assert not window.measure_tab.send_to_rnd_btn.isEnabled()
    assert "average" in window.measure_tab.send_to_rnd_btn.toolTip().lower()

    window.measure.average = (
        np.array([100.0, 1000.0]),
        np.array([1.0, 0.0]),
    )
    window.measure_io.sync_export_button()
    assert window.measure_tab.send_to_rnd_btn.isEnabled()

    window.measure.queue.state = QueueState.SWEEPING
    window.measure_io.sync_export_button()
    assert not window.measure_tab.send_to_rnd_btn.isEnabled()
    assert "idle" in window.measure_tab.send_to_rnd_btn.toolTip().lower()
    window.measure.queue.state = QueueState.IDLE


def test_measure_average_sends_one_raw_ungrouped_curve_to_rnd(
    monkeypatch,
    make_main_window,
) -> None:
    window = make_main_window()
    freqs = np.array([100.0, 1000.0])
    mag_db = np.array([3.0, -1.0])
    window.measure.average = (freqs, mag_db)
    window.measure.hrtf = object()
    window.measure_tab.hrtf_toggle.setChecked(False)
    window._rnd_widget.session.hrtf_path = "rnd-default.txt"
    window._rnd_widget.session.hrtf_name = "R&D Default"
    window.devices.current_input_device_label = lambda: "Input A"
    window.devices.current_output_device_label = lambda: "Output B"
    window.devices.current_input_channel = lambda: 1
    window.measure_tab.ch_combo.clear()
    window.measure_tab.ch_combo.addItem("Channel 2", 1)
    recovery_calls: list[bool] = []
    monkeypatch.setattr(window.rnd._recovery, "schedule", lambda: recovery_calls.append(True))
    log_calls: list[tuple[str, dict]] = []
    window._console_events.event_added.connect(
        lambda event: event.source == "rnd" and log_calls.append((event.message, event.details))
    )

    window.rnd.send_measure_to_rnd()

    session = window._rnd_widget.session
    assert len(session.measurements) == 1
    assert session.groups == []
    measurement = session.measurements[0]
    assert measurement.name == "DMS Demo AVG"
    assert session.ungrouped_order == [measurement.id]
    assert np.allclose(measurement.freqs, freqs)
    assert np.allclose(measurement.mag_db, mag_db)
    assert not np.shares_memory(measurement.freqs, freqs)
    assert not np.shares_memory(measurement.mag_db, mag_db)
    assert measurement.metadata["brand"] == "DMS"
    assert measurement.rig == "Rig"
    assert measurement.input_device_label == "Input A"
    assert measurement.input_channel_index == 1
    assert measurement.input_channel_label == "Channel 2"
    assert measurement.output_device_label == "Output B"
    assert measurement.hrtf_path == ""
    assert measurement.hrtf_name == ""
    assert measurement.top_visible is True
    assert measurement.pinned is False
    assert window._tabs.currentWidget() is window._rnd_widget
    assert window.rnd.dirty is True
    assert recovery_calls == [True]
    assert log_calls == [
        (
            "Measure view sent to R&D",
            {
                "name": "DMS Demo AVG",
                "mode": "average",
                "measurement_count": 1,
                "hrtf": None,
            },
        )
    ]

    mag_db[:] = 99.0
    assert not np.allclose(measurement.mag_db, mag_db)


def test_measure_average_copies_active_hrtf_as_editable_state(tmp_path, make_main_window) -> None:
    window = make_main_window()
    hrtf_path = tmp_path / "average-hrtf.txt"
    hrtf_path.write_text("100 1\n1000 2\n", encoding="utf-8")
    window.measure.hrtf = HRTFCurve(str(hrtf_path))
    window.measure_tab.hrtf_toggle.setChecked(True)
    source_mag = np.array([4.0, 0.0])
    window.measure.average = (np.array([100.0, 1000.0]), source_mag)

    window.rnd.send_measure_to_rnd()

    measurement = window._rnd_widget.session.measurements[0]
    assert measurement.hrtf_path == str(hrtf_path)
    assert measurement.hrtf_name == "average-hrtf"
    assert np.allclose(measurement.mag_db, source_mag)


@pytest.mark.parametrize(
    "hrtf_rows",
    [
        "100 1\n1000 2\n",
        "100 1 2 3 4 5\n1000 10 20 30 40 50\n",
    ],
)
def test_measure_var_sends_all_kept_curves_as_one_group(
    tmp_path,
    monkeypatch,
    make_main_window,
    hrtf_rows: str,
) -> None:
    window = make_main_window()
    hrtf_path = tmp_path / "transfer-hrtf.txt"
    hrtf_path.write_text(hrtf_rows, encoding="utf-8")
    window.measure.hrtf = HRTFCurve(str(hrtf_path))
    window.measure_tab.hrtf_toggle.setChecked(True)
    window.measure_tab.variation_toggle.setChecked(True)
    freqs = np.array([100.0, 1000.0])
    first_mag = np.array([1.0, 0.0])
    second_mag = np.array([2.0, 1.0])
    window.measure.kept_curves = [(freqs, first_mag), (freqs, second_mag)]
    existing = RnDGroup(name="DMS Demo VAR", expanded=False)
    window._rnd_widget.session.groups = [existing]
    window._rnd_widget._sync_tree()
    recovery_calls: list[bool] = []
    monkeypatch.setattr(window.rnd._recovery, "schedule", lambda: recovery_calls.append(True))

    window.rnd.send_measure_to_rnd()

    session = window._rnd_widget.session
    assert len(session.groups) == 2
    assert existing.expanded is False
    group = session.groups[-1]
    assert group.name == "DMS Demo VAR (2)"
    assert group.variation_enabled is True
    assert group.visible is True
    assert group.pinned is False
    assert group.expanded is True
    assert group.measurement_ids == [item.id for item in session.measurements]
    assert len(session.measurements) == 2
    for index, measurement in enumerate(session.measurements, start=1):
        assert measurement.name == f"DMS Demo VAR (2) Sweep {index}"
        assert measurement.hrtf_path == str(hrtf_path)
        assert measurement.hrtf_name == "transfer-hrtf"
        assert measurement.top_visible is True
        assert measurement.pinned is False
        assert not np.shares_memory(measurement.freqs, freqs)
    assert not np.shares_memory(session.measurements[0].mag_db, first_mag)
    assert not np.shares_memory(session.measurements[1].mag_db, second_mag)
    assert recovery_calls == [True]
    assert window._tabs.currentWidget() is window._rnd_widget

    first_mag[:] = 99.0
    second_mag[:] = 99.0
    assert not np.allclose(session.measurements[0].mag_db, first_mag)
    assert not np.allclose(session.measurements[1].mag_db, second_mag)


def test_rnd_tree_uses_view_checkboxes_without_show_column(make_main_window) -> None:
    window = make_main_window()
    measurement = rnd_measurement("m1", "First")
    group = RnDGroup(
        id="g1",
        name="Prototype",
        measurement_ids=[measurement.id],
    )
    window._rnd_widget.session.measurements = [measurement]
    window._rnd_widget.session.groups = [group]
    window._rnd_widget.session.ungrouped_order = []
    window._rnd_widget._sync_tree()
    tree = window._rnd_widget._tree

    assert [tree.headerItem().text(index) for index in range(tree.columnCount())] == [
        "Name",
        "View 1",
        "View 2",
        "Var",
        "Milestone",
        "Offset",
        "HRTF",
    ]
    assert tree.findChildren(rnd_widget_module.ToggleSwitch) == []

    group_item = tree.topLevelItem(0)
    child_item = group_item.child(0)
    group_boxes = {
        column: tree.itemWidget(group_item, column).findChild(QCheckBox) for column in (1, 2, 3, 4)
    }
    measurement_boxes = {
        column: tree.itemWidget(child_item, column).findChild(QCheckBox) for column in (1, 2, 4)
    }
    assert all(group_boxes.values())
    assert all(measurement_boxes.values())

    group_boxes[1].setChecked(False)
    group_boxes[2].setChecked(True)
    measurement_boxes[1].setChecked(False)
    measurement_boxes[2].setChecked(True)
    assert group.visible is False
    assert group.pinned is True
    assert measurement.top_visible is False
    assert measurement.pinned is True


def test_rnd_group_collapse_survives_tree_rebuilds_without_dirtying(
    qapp,
    monkeypatch,
    make_main_window,
) -> None:
    window = make_main_window()
    group = RnDGroup(id="g1", name="Prototype", expanded=True)
    window._rnd_widget.session.groups = [group]
    window._rnd_widget._sync_tree()
    window.rnd.dirty = False
    recovery_calls: list[bool] = []
    monkeypatch.setattr(window.rnd._recovery, "schedule", lambda: recovery_calls.append(True))

    group_item = window._rnd_widget._tree.topLevelItem(0)
    group_item.setExpanded(False)
    qapp.processEvents()
    assert group.expanded is False
    assert window.rnd.dirty is False
    assert recovery_calls == [True]

    window._rnd_widget._select_id(group.id)
    window._rnd_widget._name_edit.setText("Renamed")
    window._rnd_widget._save_selected_name()
    renamed_item = window._rnd_widget._tree.topLevelItem(0)
    assert renamed_item.isExpanded() is False

    window._rnd_widget._new_group()
    original_item = next(
        window._rnd_widget._tree.topLevelItem(index)
        for index in range(window._rnd_widget._tree.topLevelItemCount())
        if window._rnd_widget._tree.topLevelItem(index).data(0, rnd_widget_module.ROLE_ID)
        == group.id
    )
    assert original_item.isExpanded() is False

    window._rnd_widget._on_tree_structure_changed()
    original_item = next(
        window._rnd_widget._tree.topLevelItem(index)
        for index in range(window._rnd_widget._tree.topLevelItemCount())
        if window._rnd_widget._tree.topLevelItem(index).data(0, rnd_widget_module.ROLE_ID)
        == group.id
    )
    assert original_item.isExpanded() is False


def test_rnd_view_titles_use_mode_accent_and_splitter_uses_saved_ratio(
    monkeypatch,
    make_main_window,
) -> None:
    plot_widget = rnd_widget_module.RnDPlotWidget()
    expected_colors = [
        ("dark", False, "#66ccff"),
        ("light", False, "#176ea6"),
        (HACKERMAN_95, False, "#39ff14"),
        ("dark", True, "#7A7A7A"),
    ]
    for theme, brand_mode, expected_color in expected_colors:
        plot_widget.apply_theme(theme, brand_mode)
        assert plot_widget.top_plot.getPlotItem().titleLabel.text == "View 1"
        assert plot_widget.bottom_plot.getPlotItem().titleLabel.text == "View 2"
        assert plot_widget.top_plot.getPlotItem().titleLabel.opts["color"] == expected_color
        assert plot_widget.bottom_plot.getPlotItem().titleLabel.opts["color"] == expected_color
    measurement = rnd_measurement()
    plot_widget.redraw(
        top_measurements=[(measurement, measurement.mag_db)],
        pinned_measurements=[],
        top_group_variations=[],
        bottom_group_variations=[],
    )
    plot_widget.apply_theme(FASTGRAPH_95_DARK)
    curve = next(item for item in plot_widget._items if isinstance(item, pg.PlotDataItem))
    assert curve.opts["antialias"] is True
    np.testing.assert_array_equal(curve.xData, [100.0, 1000.0, 1000.0])
    np.testing.assert_array_equal(curve.yData, [1.0, 1.0, 0.0])
    np.testing.assert_array_equal(measurement.freqs, [100.0, 1000.0])
    np.testing.assert_array_equal(measurement.mag_db, [1.0, 0.0])
    plot_widget.close()

    window = make_main_window()
    splitter = window._rnd_widget._splitter
    splitter.resize(1400, 600)
    size_calls: list[list[int]] = []
    monkeypatch.setattr(splitter, "setSizes", lambda sizes: size_calls.append(list(sizes)))
    window._rnd_widget._splitter_ratio = 0.5
    window._rnd_widget._apply_splitter_ratio()
    available = splitter.width() - splitter.handleWidth()
    assert size_calls == [[round(available * 0.5), available - round(available * 0.5)]]

    size_calls.clear()
    window._rnd_widget._splitter_ratio = 0.63
    window._rnd_widget._apply_splitter_ratio()
    assert size_calls == [[round(available * 0.63), available - round(available * 0.63)]]


def test_rnd_splitter_user_ratio_is_saved_and_reused(monkeypatch, make_main_window) -> None:
    window = make_main_window()
    splitter = window._rnd_widget._splitter
    splitter.resize(1400, 600)
    monkeypatch.setattr(splitter, "sizes", lambda: [560, 840])
    window._rnd_widget._on_splitter_moved(560, 1)
    window._rnd_widget._splitter_save_timer.stop()
    window._rnd_widget.splitter_ratio_changed.emit(window._rnd_widget._splitter_ratio)

    assert window._rnd_widget._splitter_ratio == pytest.approx(0.4, abs=0.01)
    assert window._settings.get("rnd_splitter_ratio") == pytest.approx(0.4, abs=0.01)

    splitter.resize(1800, 600)
    size_calls: list[list[int]] = []
    monkeypatch.setattr(splitter, "setSizes", lambda sizes: size_calls.append(list(sizes)))
    window._rnd_widget._apply_splitter_ratio()
    applied = size_calls[-1]
    assert applied[0] / sum(applied) == pytest.approx(0.4, abs=0.01)


@pytest.mark.parametrize("width", [1200, 1800, 2600])
def test_rnd_default_splitter_stays_half_across_window_sizes(
    qapp,
    make_main_window,
    width: int,
) -> None:
    window = make_main_window()
    window._rnd_widget._splitter_ratio = 0.5
    window.resize(width, 900)
    window.show()
    qapp.processEvents()
    window._rnd_widget._apply_splitter_ratio()
    qapp.processEvents()

    sizes = window._rnd_widget._splitter.sizes()
    assert sizes[0] / sum(sizes) == pytest.approx(0.5, abs=0.02)


def test_rnd_rows_reject_drops_onto_items(make_main_window) -> None:
    """C3: measurements reorder between rows; a group stays a drop target."""
    window = make_main_window()
    measurement = rnd_measurement("m1", "First")
    group = RnDGroup(id="g1", name="Prototype", measurement_ids=["m1"])
    window._rnd_widget.session.measurements = [measurement]
    window._rnd_widget.session.groups = [group]
    window._rnd_widget.session.ungrouped_order = []
    window._rnd_widget._sync_tree()

    group_item = window._rnd_widget._tree.topLevelItem(0)
    child_item = group_item.child(0)

    assert group_item.flags() & Qt.ItemFlag.ItemIsDropEnabled
    assert not child_item.flags() & Qt.ItemFlag.ItemIsDropEnabled
    assert group_item.flags() & Qt.ItemFlag.ItemIsDragEnabled
    assert child_item.flags() & Qt.ItemFlag.ItemIsDragEnabled


def test_rnd_nested_group_returns_its_measurements_to_the_parent(
    caplog,
    make_main_window,
) -> None:
    """C3: a nested group can only ever appear by accident; salvage its rows."""
    window = make_main_window()
    outer = RnDGroup(id="outer", name="Outer", measurement_ids=[])
    inner = RnDGroup(id="inner", name="Inner", measurement_ids=[])
    measurement = rnd_measurement("m1", "Nested")
    window._rnd_widget.session.measurements = [measurement]
    window._rnd_widget.session.groups = [outer, inner]
    window._rnd_widget.session.ungrouped_order = ["m1"]
    window._rnd_widget._sync_tree()

    tree = window._rnd_widget._tree
    outer_item = next(
        tree.topLevelItem(index)
        for index in range(tree.topLevelItemCount())
        if tree.topLevelItem(index).data(0, rnd_widget_module.ROLE_ID) == "outer"
    )
    inner_item = next(
        tree.topLevelItem(index)
        for index in range(tree.topLevelItemCount())
        if tree.topLevelItem(index).data(0, rnd_widget_module.ROLE_ID) == "inner"
    )
    measurement_item = next(
        tree.topLevelItem(index)
        for index in range(tree.topLevelItemCount())
        if tree.topLevelItem(index).data(0, rnd_widget_module.ROLE_ID) == "m1"
    )
    tree.takeTopLevelItem(tree.indexOfTopLevelItem(measurement_item))
    inner_item.addChild(measurement_item)
    tree.takeTopLevelItem(tree.indexOfTopLevelItem(inner_item))
    outer_item.addChild(inner_item)

    with caplog.at_level("WARNING", logger="dms.ui.rnd_widget"):
        window._rnd_widget._on_tree_structure_changed()

    assert outer.measurement_ids == ["m1"]
    assert window._rnd_widget.session.measurement_by_id("m1") is not None
    assert any("nested group" in record.getMessage() for record in caplog.records)


def test_rnd_remove_takes_every_selected_row_after_one_prompt(
    monkeypatch,
    make_main_window,
) -> None:
    """C9: multi-select Remove asks once and removes everything selected."""
    window = make_main_window()
    measurements = [rnd_measurement(f"m{i}", f"Row {i}") for i in range(1, 4)]
    group = RnDGroup(id="g1", name="Prototype", measurement_ids=["m3"])
    window._rnd_widget.session.measurements = measurements
    window._rnd_widget.session.groups = [group]
    window._rnd_widget.session.ungrouped_order = ["m1", "m2"]
    window._rnd_widget._sync_tree()

    prompts: list[str] = []

    def fake_question(_parent, _title, text, *args, **kwargs):
        prompts.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(rnd_widget_module.QMessageBox, "question", fake_question)
    tree = window._rnd_widget._tree
    for index in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(index)
        if item.data(0, rnd_widget_module.ROLE_ID) in {"m1", "m2"}:
            item.setSelected(True)

    window._rnd_widget._remove_selected()

    assert len(prompts) == 1
    assert "2 measurements" in prompts[0]
    assert [item.id for item in window._rnd_widget.session.measurements] == ["m3"]
    # The group and its member survive: neither was selected.
    assert [item.id for item in window._rnd_widget.session.groups] == ["g1"]
    assert group.measurement_ids == ["m3"]


def test_rnd_remove_can_be_declined_and_keeps_single_row_behaviour(
    monkeypatch,
    make_main_window,
) -> None:
    window = make_main_window()
    measurement = rnd_measurement("m1", "Only")
    window._rnd_widget.session.measurements = [measurement]
    window._rnd_widget.session.ungrouped_order = ["m1"]
    window._rnd_widget._sync_tree()
    window._rnd_widget._select_id("m1")

    monkeypatch.setattr(
        rnd_widget_module.QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )
    window._rnd_widget._remove_selected()
    assert [item.id for item in window._rnd_widget.session.measurements] == ["m1"]

    monkeypatch.setattr(
        rnd_widget_module.QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    window._rnd_widget._remove_selected()
    assert window._rnd_widget.session.measurements == []


def test_rnd_group_rename_keeps_parenthesised_names(make_main_window) -> None:
    """C10: only the row's own " (3)" count suffix is stripped."""
    window = make_main_window()
    measurement = rnd_measurement("m1", "First")
    group = RnDGroup(id="g1", name="Prototype", measurement_ids=["m1"])
    window._rnd_widget.session.measurements = [measurement]
    window._rnd_widget.session.groups = [group]
    window._rnd_widget.session.ungrouped_order = []
    window._rnd_widget._sync_tree()

    group_item = window._rnd_widget._tree.topLevelItem(0)
    assert group_item.text(0) == "Prototype (1)"

    group_item.setText(0, "Prototype (v2) (1)")
    window._rnd_widget._on_item_changed(group_item, 0)
    assert group.name == "Prototype (v2)"

    group_item.setText(0, "Prototype (v2)")
    window._rnd_widget._on_item_changed(group_item, 0)
    assert group.name == "Prototype (v2)"


def test_rnd_hrtf_files_are_parsed_once_per_file_version(tmp_path, monkeypatch) -> None:
    """C11: a redraw reuses one parsed HRTF instead of re-reading per row."""
    path = tmp_path / "hrtf.txt"
    path.write_text("100 1\n1000 2\n", encoding="utf-8")
    rnd_widget_module._parsed_hrtf_curve.cache_clear()
    reads: list[str] = []

    real_curve = rnd_widget_module.HRTFCurve

    class CountingCurve(real_curve):
        def __init__(self, curve_path: str) -> None:
            reads.append(curve_path)
            super().__init__(curve_path)

    monkeypatch.setattr(rnd_widget_module, "HRTFCurve", CountingCurve)

    first = rnd_widget_module.cached_hrtf_curve(str(path))
    second = rnd_widget_module.cached_hrtf_curve(str(path))

    assert first is second
    assert reads == [str(path)]

    os.utime(path, (0, 0))
    third = rnd_widget_module.cached_hrtf_curve(str(path))
    assert third is not first
    assert len(reads) == 2
    rnd_widget_module._parsed_hrtf_curve.cache_clear()


def test_rnd_photo_caption_survives_removing_another_photo(
    monkeypatch,
    make_main_window,
) -> None:
    """A caption typed in the viewer is kept even when a photo is removed."""
    window = make_main_window()
    measurement = rnd_measurement("m1", "With Photos")
    store = window._rnd_widget.photo_store
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    measurement.photos.append(store.add_image(image, display_name="One"))
    measurement.photos.append(store.add_image(image, display_name="Two"))
    window._rnd_widget.session.measurements = [measurement]
    window._rnd_widget.session.ungrouped_order = ["m1"]
    window._rnd_widget._sync_tree()
    window._rnd_widget._select_id("m1")

    class RemovingViewer:
        remove_requested = True
        remove_index = 0
        captions = ["gone", "kept caption"]

        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(rnd_widget_module, "PhotoViewerDialog", RemovingViewer)

    window._rnd_widget._open_photo(measurement.photos[0])

    assert [photo.caption for photo in measurement.photos] == ["kept caption"]


def test_rnd_save_as_over_another_session_asks_first(
    monkeypatch,
    make_main_window,
    tmp_path,
) -> None:
    """C1: the canonical extension is added after the dialog's own check."""
    window = make_main_window()
    window._rnd_widget.add_measurement(rnd_measurement())
    existing = tmp_path / "prototype.fastgraph-rnd.json"
    existing.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        rnd_bridge_module.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(tmp_path / "prototype"), ""),
    )
    prompts: list[str] = []

    def decline(_parent, _title, text, *args, **kwargs):
        prompts.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(rnd_bridge_module.QMessageBox, "question", decline)

    assert window.rnd.save_session() is False
    assert prompts and "Replace prototype.fastgraph-rnd.json?" in prompts[0]
    assert existing.read_text(encoding="utf-8") == "{}"

    monkeypatch.setattr(
        rnd_bridge_module.QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    assert window.rnd.save_session() is True
    assert "measurements" in existing.read_text(encoding="utf-8")


def test_rnd_status_line_reports_a_degraded_recovery_rotation(make_main_window) -> None:
    """C5: the newest snapshot still saves; the status line says it is degraded."""
    window = make_main_window()

    window.rnd._recovery.rotation_degraded = True
    window.rnd._on_rnd_recovery_saved()
    assert window._rnd_widget._status_label.text() == "R&D recovery degraded"

    window.rnd._recovery.rotation_degraded = False
    window.rnd._on_rnd_recovery_saved()
    assert window._rnd_widget._status_label.text() != "R&D recovery degraded"
