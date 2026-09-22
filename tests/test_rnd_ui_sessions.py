import os

import numpy as np
import pytest
from helpers import rnd_measurement
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QDialog, QMessageBox

import dms.ui.rnd_bridge as rnd_bridge_module
import dms.ui.rnd_widget as rnd_widget_module
from dms.measure_queue import QueueState
from dms.rnd.models import RnDGroup
from dms.rnd.persistence import save_rnd_session
from dms.rnd.photos import RnDPhotoStore
from dms.ui.measure_dialogs import RnDReviewDialog


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
