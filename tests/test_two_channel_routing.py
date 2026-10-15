"""Two-channel routing check: the sweep must reach the expected ear's input.

Each two-channel stage records the expected input plus the opposite one. A
sweep that only reaches the opposite input, or reaches it far louder, is
reported as crossed routing instead of a generic "no sweep" failure, and is
not retried. Synthetic signals only; no audio hardware.
"""

import numpy as np
import pytest

import dms.ui.measure_controller as measure_controller_module
from dms import audio_engine
from dms.measure_queue import QueueState
from dms.measurement_alignment import (
    AlignmentSettings,
    MeasurementAlignmentError,
    MeasurementDiagnostics,
    MeasurementFailureReason,
    align_recording_to_layout,
    align_two_channel_recording,
    is_retryable_timing_failure,
)
from dms.measurement_layout import build_measurement_layout, build_output_signal
from dms.processing import F_HIGH, F_LOW, generate_log_sweep

FS = 48000
DELAY = 300
SETTINGS = AlignmentSettings()


@pytest.fixture(scope="module")
def rig():
    sweep = (generate_log_sweep(duration=1.0, fs=FS, f_low=F_LOW, f_high=F_HIGH) * 0.5).astype(
        np.float32
    )
    layout = build_measurement_layout(
        sweep=sweep, fs=FS, pre_silence_s=0.2, post_silence_s=0.5, bluetooth_headphone_mode=False
    )
    played = build_output_signal(layout, 1)[:, 0].astype(np.float64)
    return sweep, layout, played


def _rec(played: np.ndarray, gain_db: float | None, seed: int) -> np.ndarray:
    """Delayed, scaled sweep plus -80 dBFS noise; ``None`` is noise only."""
    noise = np.random.default_rng(seed).normal(0.0, 1e-4, len(played))
    if gain_db is None:
        return noise.astype(np.float32)
    delayed = np.concatenate([np.zeros(DELAY), played])[: len(played)]
    return (delayed * 10.0 ** (gain_db / 20.0) + noise).astype(np.float32)


def _check(rig, expected, monitor, *, output=0, expected_input=0, monitor_input=1):
    sweep, layout, _ = rig
    return align_two_channel_recording(
        expected,
        monitor,
        sweep,
        layout,
        SETTINGS,
        output_channel=output,
        expected_input=expected_input,
        monitor_input=monitor_input,
    )


def test_sweep_only_on_the_opposite_input_is_crossed(rig) -> None:
    played = rig[2]
    with pytest.raises(MeasurementAlignmentError) as caught:
        _check(rig, _rec(played, None, 1), _rec(played, 0.0, 2))

    exc = caught.value
    assert exc.reason == MeasurementFailureReason.CHANNELS_CROSSED
    assert "Output 1 (L) was heard on input 2, not input 1" in str(exc)
    assert "Turn on Swap L/R inputs" in str(exc)
    assert exc.diagnostics.failure_reason == MeasurementFailureReason.CHANNELS_CROSSED
    assert exc.diagnostics.monitor_input == 2
    assert exc.diagnostics.monitor_snr_db is not None and exc.diagnostics.monitor_snr_db > 20.0
    assert not is_retryable_timing_failure(str(exc), exc.reason)


def test_crossed_with_swap_on_names_the_other_inputs(rig) -> None:
    played = rig[2]
    with pytest.raises(MeasurementAlignmentError) as caught:
        _check(
            rig,
            _rec(played, None, 1),
            _rec(played, 0.0, 2),
            output=1,
            expected_input=0,
            monitor_input=1,
        )
    assert "Output 2 (R) was heard on input 2, not input 1" in str(caught.value)
    assert "Turn off Swap L/R inputs" in str(caught.value)


def test_much_louder_opposite_input_is_crossed_even_when_expected_passes(rig) -> None:
    played = rig[2]
    expected = _rec(played, -20.0, 1)
    align_recording_to_layout(expected, rig[0], rig[1], SETTINGS)  # passes on its own

    with pytest.raises(MeasurementAlignmentError) as caught:
        _check(rig, expected, _rec(played, 0.0, 2))

    exc = caught.value
    assert exc.reason == MeasurementFailureReason.CHANNELS_CROSSED
    assert exc.diagnostics.monitor_relative_level_db == pytest.approx(20.0, abs=0.5)


def test_normal_crosstalk_passes_and_leaves_the_result_untouched(rig) -> None:
    sweep, layout, played = rig
    expected = _rec(played, 0.0, 1)
    single = align_recording_to_layout(expected, sweep, layout, SETTINGS)

    result = _check(rig, expected, _rec(played, -30.0, 2))

    assert np.array_equal(result.aligned_recording, single.aligned_recording)
    assert np.array_equal(result.aligned_recording_tail, single.aligned_recording_tail)
    assert result.snr_db == single.snr_db
    assert result.diagnostics.monitor_input == 2
    assert result.diagnostics.monitor_relative_level_db == pytest.approx(-30.0, abs=0.5)


def test_silence_on_both_inputs_keeps_the_reason_and_names_both(rig) -> None:
    sweep, layout, played = rig
    expected = _rec(played, None, 1)
    with pytest.raises(MeasurementAlignmentError) as single:
        align_recording_to_layout(expected, sweep, layout, SETTINGS)

    with pytest.raises(MeasurementAlignmentError) as caught:
        _check(rig, expected, _rec(played, None, 2))

    exc = caught.value
    assert exc.reason == single.value.reason
    assert "Output 1 (L) was not heard on input 1 or input 2" in str(exc)
    assert exc.diagnostics.failure_message == str(exc)
    assert exc.diagnostics.monitor_input == 2


# --- SweepWorker -----------------------------------------------------------


def _run_worker(monkeypatch, rig, columns, **kwargs):
    sweep, layout, played = rig
    device = {"max_input_channels": 2, "max_output_channels": 2}
    monkeypatch.setattr(audio_engine, "device_by_index", lambda *_a, **_kw: device)
    times = iter([0.0, 10.0])
    monkeypatch.setattr(audio_engine.time, "monotonic", lambda: next(times, 10.0))
    monkeypatch.setattr(audio_engine.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(audio_engine.sd, "wait", lambda: None)
    calls = []

    def playrec(*_args, **kw):
        calls.append(kw)
        return np.column_stack(columns)

    monkeypatch.setattr(audio_engine.sd, "playrec", playrec)
    worker = audio_engine.SweepWorker()
    finished, errors = [], []
    worker.finished.connect(lambda rec, _swp: finished.append(rec))
    worker.error.connect(errors.append)
    worker.run(sweep=sweep, output_device=1, input_device=2, fs=FS, buffer_size=256, **kwargs)
    return calls, finished, errors


def test_worker_two_channel_stage_records_both_inputs(monkeypatch, rig) -> None:
    played = rig[2]
    calls, finished, errors = _run_worker(
        monkeypatch,
        rig,
        [_rec(played, 0.0, 1), _rec(played, -30.0, 2)],
        input_channel=1,
        output_channel=1,
        monitor_channel=0,
    )
    assert errors == []
    assert len(finished) == 1
    assert calls[0]["input_mapping"] == [2, 1]
    assert calls[0]["output_mapping"] == [2]


def test_worker_single_channel_call_is_unchanged(monkeypatch, rig) -> None:
    calls, finished, errors = _run_worker(monkeypatch, rig, [_rec(rig[2], 0.0, 1)], input_channel=1)
    assert errors == []
    assert len(finished) == 1
    assert calls[0]["input_mapping"] == [2]
    assert calls[0]["output_mapping"] is None


# --- MeasureController ------------------------------------------------------


def _started_channels(make_main_window, monkeypatch, *, swap: bool, stage: int):
    window = make_main_window(
        settings={"measure_two_channel_enabled": True, "measure_two_channel_swap_inputs": swap}
    )
    captured = []
    monkeypatch.setattr(window.devices, "current_output_device", lambda: 1)
    monkeypatch.setattr(window.devices, "current_input_device", lambda: 2)
    monkeypatch.setattr(window.devices, "selected_audio_pair_is_compatible", lambda: True)
    monkeypatch.setattr(
        window.measure.sweep_runner,
        "start",
        lambda _factory, **kw: captured.append(kw) or True,
    )
    window.measure.queue.target = 1
    window.measure.queue.index = 0
    window.measure.queue.state = QueueState.QUEUE_RUNNING
    if stage == 2:
        window.measure.queue.pending_pair_first_raw = object()
    window.measure.start_next_sweep(second_stage=stage == 2)
    kw = captured[0]
    window.measure.queue.reset()
    window.measure.queue.state = QueueState.IDLE
    return kw["output_channel"], kw["input_channel"], kw["monitor_channel"]


@pytest.mark.parametrize(
    ("swap", "stage", "expected"),
    [
        (False, 1, (0, 0, 1)),
        (False, 2, (1, 1, 0)),
        (True, 1, (0, 1, 0)),
        (True, 2, (1, 0, 1)),
    ],
)
def test_controller_routes_stage_inputs(make_main_window, monkeypatch, swap, stage, expected):
    assert _started_channels(make_main_window, monkeypatch, swap=swap, stage=stage) == expected


def test_crossed_failure_stops_the_pair_without_retry(make_main_window, monkeypatch) -> None:
    window = make_main_window(settings={"measure_two_channel_enabled": True})
    calls = {"question": 0, "warning": []}
    monkeypatch.setattr(
        measure_controller_module.QMessageBox,
        "question",
        lambda *_a, **_kw: calls.__setitem__("question", calls["question"] + 1),
    )
    monkeypatch.setattr(
        measure_controller_module.QMessageBox,
        "warning",
        lambda _parent, _title, text: calls["warning"].append(text),
    )
    monkeypatch.setattr(measure_controller_module.QTimer, "singleShot", lambda *_a: None)
    window.measure.queue.target = 2
    window.measure.queue.index = 0
    window.measure.queue.attempts = 1
    window.measure.queue.state = QueueState.SWEEPING
    window.measure.queue.stage = 1
    message = "Output 1 (L) was heard on input 2, not input 1. Turn on Swap L/R inputs."
    window.measure.queue.last_diagnostics = MeasurementDiagnostics(
        fs=FS,
        bluetooth_headphone_mode=False,
        latency="low",
        start_alignment_confidence_min=6.0,
        end_marker_confidence_min=7.0,
        timing_drift_max_ms=35.0,
        failure_reason=MeasurementFailureReason.CHANNELS_CROSSED,
        failure_message=message,
        monitor_input=2,
    )

    window.measure.on_sweep_error(message)

    assert calls["question"] == 0
    assert len(calls["warning"]) == 1
    assert "Swap L/R inputs" in calls["warning"][0]
    assert "Opposite input 2" in calls["warning"][0]
    assert window.measure.queue.state == QueueState.IDLE
    assert window.measure.queue_active() is False


def test_swap_toggle_follows_two_channel_and_persists(make_main_window) -> None:
    window = make_main_window()
    toggle = window.measure_tab.swap_inputs_toggle
    assert toggle.isHidden() is True

    window.measure_tab.two_channel_toggle.setChecked(True)
    assert toggle.isHidden() is False
    toggle.setChecked(True)
    assert window._settings.get("measure_two_channel_swap_inputs") is True
