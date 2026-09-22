import numpy as np
import pytest
from helpers import rnd_measurement

import dms.ui.rnd_bridge as rnd_bridge_module
import dms.ui.rnd_widget as rnd_widget_module
from dms.hrtf import HRTFCurve
from dms.measure_queue import QueueState
from dms.rnd.models import RnDGroup


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
