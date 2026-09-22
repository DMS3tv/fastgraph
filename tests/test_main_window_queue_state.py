"""
Measure queue behaviour through the window, after the MeasurementQueue and
SweepRunner integration. Each test names the audit finding it pins.
"""

from __future__ import annotations

import numpy as np
import pytest

import dms.ui.main_window as main_window_module
from dms.measure_queue import MeasurementQueue, QueueState
from dms.two_channel import TwoChannelCurvePair


def _curve(level: float = 0.0):
    freqs = np.array([20.0, 1000.0, 20000.0])
    return freqs, np.full(3, level)


def _pair():
    return TwoChannelCurvePair(channel_1=_curve(0.0), channel_2=_curve(1.0))


def _silence_dialogs(monkeypatch, *, expect_question: bool = True):
    calls = {"question": 0, "warning": 0}

    def question(*_args, **_kwargs):
        calls["question"] += 1
        return main_window_module.QMessageBox.StandardButton.Yes

    def warning(*_args, **_kwargs):
        calls["warning"] += 1
        return main_window_module.QMessageBox.StandardButton.Ok

    monkeypatch.setattr(main_window_module.QMessageBox, "question", question)
    monkeypatch.setattr(main_window_module.QMessageBox, "warning", warning)
    monkeypatch.setattr(main_window_module.QTimer, "singleShot", lambda _delay, callback: None)
    return calls


def test_state_shims_round_trip_through_the_queue(make_main_window) -> None:
    window = make_main_window()
    assert isinstance(window._queue, MeasurementQueue)

    window._state = QueueState.QUEUE_RUNNING
    assert window._queue.state is QueueState.QUEUE_RUNNING
    assert window._state == QueueState.QUEUE_RUNNING

    window._queue_target = 4
    window._queue_index = 2
    window._current_sweep_attempts = 1
    window._pending_curve = _curve()
    assert (window._queue.target, window._queue.index, window._queue.attempts) == (4, 2, 1)
    assert window._queue.pending_curve is not None

    window._queue.reset()
    assert window._queue_target == 0
    assert window._pending_curve is None
    window._state = QueueState.IDLE


def test_terminal_error_resets_queue_counters(make_main_window, monkeypatch) -> None:
    """B4: a non-retryable error must not leave a phantom queue behind."""
    window = make_main_window()
    calls = _silence_dialogs(monkeypatch)
    window._queue_target = 3
    window._queue_index = 1
    window._current_sweep_attempts = 1
    window._state = QueueState.SWEEPING

    window._on_sweep_error("Selected device is unavailable.")

    assert window._state == QueueState.IDLE
    assert window._queue_active() is False
    assert (window._queue_target, window._queue_index, window._current_sweep_attempts) == (0, 0, 0)
    assert calls["question"] == 0
    assert calls["warning"] == 1


def test_manual_fail_restores_the_retry_budget(make_main_window, monkeypatch) -> None:
    """B5: two manual fails must not consume the timing-retry budget."""
    window = make_main_window()
    started = []
    window._start_next_sweep = lambda **_kwargs: started.append(True)
    window._queue_target = 2
    window._queue_index = 0
    window._current_sweep_attempts = 2
    window._pending_curve = _curve()
    window._state = QueueState.PASS_FAIL

    window._on_fail()

    assert window._current_sweep_attempts == 0
    assert window._queue_index == 0
    assert window._pending_curve is None
    assert window._state == QueueState.QUEUE_RUNNING
    assert started == [True]
    window._queue.reset()
    window._state = QueueState.IDLE


def test_cancel_clears_two_channel_pending_state(make_main_window) -> None:
    """B6: Cancel Queue must drop the half-finished pair."""
    window = make_main_window(settings={"measure_two_channel_enabled": True})
    window._queue_target = 2
    window._state = QueueState.PASS_FAIL
    window._two_channel_stage = 2
    window._pending_pair = _pair()
    window._pending_pair_first_raw = _curve()
    window._pending_pair_first_diagnostics = object()

    window._cancel_queue()

    assert window._state == QueueState.IDLE
    assert window._pending_pair is None
    assert window._pending_pair_first_raw is None
    assert window._pending_pair_first_diagnostics is None
    assert window._two_channel_stage == 0
    assert window._clear_btn.isEnabled() is False


def test_device_error_is_terminal_in_two_channel_mode(make_main_window, monkeypatch) -> None:
    """B8: an unplugged interface must not prompt three pair retries."""
    window = make_main_window(settings={"measure_two_channel_enabled": True})
    calls = _silence_dialogs(monkeypatch)
    window._queue_target = 2
    window._queue_index = 0
    window._current_sweep_attempts = 1
    window._state = QueueState.SWEEPING
    window._two_channel_stage = 1

    window._on_sweep_error("Input device unavailable: Scarlett 2i2")

    assert calls["question"] == 0
    assert calls["warning"] == 1
    assert window._state == QueueState.IDLE
    assert window._queue_active() is False


def test_stream_error_still_offers_pair_retry(make_main_window, monkeypatch) -> None:
    window = make_main_window(settings={"measure_two_channel_enabled": True})
    calls = _silence_dialogs(monkeypatch)
    window._queue_target = 2
    window._queue_index = 0
    window._current_sweep_attempts = 1
    window._state = QueueState.SWEEPING
    window._two_channel_stage = 2
    window._pending_pair_first_raw = _curve()

    window._on_sweep_error("Channel 2 stream failed.")

    assert calls["question"] == 1
    assert window._state == QueueState.QUEUE_RUNNING
    assert window._pending_pair_first_raw is None
    window._queue.reset()
    window._state = QueueState.IDLE


def test_shortcut_and_console_start_are_blocked_in_channel_balance(
    make_main_window, monkeypatch
) -> None:
    """B7: only the button used to be guarded."""
    window = make_main_window(settings={"measure_two_channel_enabled": True})
    monkeypatch.setattr(
        main_window_module.MainWindow, "_channel_balance_mode_active", lambda self: True
    )
    window._tabs.setCurrentIndex(0)

    window._shortcut_start_measurement()
    assert window._state == QueueState.IDLE
    assert "Channel Balance" in window._statusbar.currentMessage()

    with pytest.raises(ValueError, match="Channel Balance"):
        window._run_measure_command(["start"])
    assert window._state == QueueState.IDLE


def test_starting_a_sweep_stops_the_channel_balance_generator(
    make_main_window, monkeypatch
) -> None:
    window = make_main_window()
    stopped = []
    monkeypatch.setattr(window, "_stop_channel_balance", lambda: stopped.append(True))
    window._queue_target = 0
    window._state = QueueState.QUEUE_RUNNING

    # No active queue: returns to idle without starting hardware, but the
    # generator stop is unconditional on any sweep start path.
    window._queue_target = 1
    window._queue_index = 0
    monkeypatch.setattr(window, "_current_output_device", lambda: None)
    calls = _silence_dialogs(monkeypatch)
    window._start_next_sweep()

    assert stopped == [True]
    assert window._state == QueueState.IDLE
    assert calls["warning"] == 1


def test_device_poll_is_deferred_while_the_queue_runs(make_main_window, monkeypatch) -> None:
    """B3: a hotplug between the two halves of a pair must not re-select devices."""
    window = make_main_window()
    refreshed = []
    monkeypatch.setattr(window, "_refresh_devices", lambda: refreshed.append(True))
    monkeypatch.setattr(
        main_window_module,
        "get_output_devices",
        lambda: [{"index": 1, "name": "Out", "hostapi": 0}],
    )
    monkeypatch.setattr(
        main_window_module, "get_input_devices", lambda: [{"index": 2, "name": "In", "hostapi": 0}]
    )
    monkeypatch.setattr(window, "_current_output_device", lambda: 1)
    monkeypatch.setattr(window, "_current_input_device", lambda: 2)
    window._last_output_devices = []
    window._last_input_devices = []
    window._queue_target = 2
    window._state = QueueState.QUEUE_RUNNING

    window._check_devices()

    assert refreshed == []
    assert window._devices_dirty is True

    window._queue.reset()
    window._state = QueueState.IDLE
    window._apply_state_ui()

    assert refreshed == [True]
    assert window._devices_dirty is False


def test_vanished_device_aborts_even_during_review(make_main_window, monkeypatch) -> None:
    window = make_main_window()
    monkeypatch.setattr(window, "_refresh_devices", lambda: None)
    monkeypatch.setattr(main_window_module, "get_output_devices", lambda: [])
    monkeypatch.setattr(main_window_module, "get_input_devices", lambda: [])
    monkeypatch.setattr(window, "_current_output_device", lambda: 1)
    monkeypatch.setattr(window, "_current_input_device", lambda: 2)
    window._last_output_devices = [(1, "Out", 0)]
    window._last_input_devices = [(2, "In", 0)]
    window._queue_target = 2
    window._pending_curve = _curve()
    window._state = QueueState.PASS_FAIL

    window._check_devices()

    assert window._state == QueueState.IDLE
    assert window._queue_active() is False
    assert window._pending_curve is None


def test_close_joins_the_sweep_runner(make_main_window, monkeypatch) -> None:
    """B1: the window must wait for the sweep thread before Qt tears it down."""
    window = make_main_window()
    joined = []
    monkeypatch.setattr(window._sweep_runner, "shutdown", lambda: joined.append(True))
    window._confirm_measure_close = lambda: True

    window.close()

    assert joined == [True]


def test_close_prompts_before_discarding_kept_curves(make_main_window, monkeypatch) -> None:
    """B11: kept Measure curves are not silently discarded on close."""
    from conftest import REAL_CONFIRM_MEASURE_CLOSE

    window = make_main_window()
    confirm = lambda: REAL_CONFIRM_MEASURE_CLOSE(window)  # noqa: E731
    assert confirm() is True

    window._kept_curves.append(_curve())
    # The prompt is about *unsaved* work now, so keeping a curve has to mark
    # the Measure session dirty the way ``_on_keep`` does.
    window._mark_measure_dirty()
    answers = []

    class _Dialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

        def exec(self):
            return answers.pop()

    monkeypatch.setattr(
        main_window_module,
        "QMessageBox",
        type(
            "QMessageBoxStub",
            (),
            {
                "Icon": main_window_module.QMessageBox.Icon,
                "StandardButton": main_window_module.QMessageBox.StandardButton,
                "__new__": lambda cls, *a, **k: _Dialog(),
            },
        ),
    )
    answers.append(main_window_module.QMessageBox.StandardButton.Cancel)
    assert confirm() is False
    answers.append(main_window_module.QMessageBox.StandardButton.Discard)
    assert confirm() is True

    window._settings.set("confirm_discard_measurements", False)
    assert confirm() is True
