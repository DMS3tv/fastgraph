import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

import dms.settings_manager as settings_module
import dms.ui.main_window as main_window_module
from dms.session import SessionData
from dms.settings_manager import SettingsManager
from dms.theme import ThemeController
from dms.two_channel import TwoChannelCurvePair
from dms.ui.main_window import AppState, MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(qapp, monkeypatch, tmp_path: Path) -> MainWindow:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(MainWindow, "_refresh_devices", lambda self: None)
    monkeypatch.setattr(MainWindow, "_start_level_monitor", lambda self: None)
    monkeypatch.setattr(MainWindow, "_start_update_check", lambda self: None)
    settings = SettingsManager()
    settings.set("measure_two_channel_enabled", True)
    settings.set("measure_two_channel_bottom_mode", "separate")
    window = MainWindow(
        SessionData(rig="Rig", brand="DMS", model="Demo"),
        settings,
        ThemeController(qapp, settings),
    )
    window._confirm_rnd_close = lambda: True
    return window


def _curve(level: float):
    return (
        np.array([100.0, 1000.0, 10000.0]),
        np.array([level, level, level]),
    )


def test_restores_two_channel_layout_but_starts_in_frequency_response(
    qapp, monkeypatch, tmp_path: Path
) -> None:
    window = _window(qapp, monkeypatch, tmp_path)

    assert window._two_channel_enabled is True
    assert window._plots._stack.currentWidget() is window._plots.two
    assert window._two_channel_bottom_mode == "separate"
    assert window._measure_submode_toggle.isChecked() is False
    assert window._measure_frequency_label.property("tone") == "accent"
    assert window._measure_balance_label.property("tone") == "muted"
    assert window._channel_balance_active is False
    window.close()


def test_measure_submode_switch_changes_mode_and_stops_generator(
    qapp, monkeypatch, tmp_path: Path
) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    stop_calls = []
    monkeypatch.setattr(
        window,
        "_stop_channel_balance",
        lambda *_args: stop_calls.append(True),
    )

    assert window._measure_submode_control.isHidden() is False
    window._measure_submode_toggle.setChecked(True)
    assert window._channel_balance_mode_active() is True
    assert window._measure_frequency_label.property("tone") == "muted"
    assert window._measure_balance_label.property("tone") == "accent"

    window._measure_submode_toggle.setChecked(False)
    assert window._channel_balance_mode_active() is False
    assert stop_calls

    window._two_channel_toggle.setChecked(False)
    assert window._measure_submode_control.isHidden() is True
    assert window._measure_submode_toggle.isChecked() is False
    window.close()


def test_measure_submode_switch_is_disabled_while_busy(
    qapp, monkeypatch, tmp_path: Path
) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    window._state = AppState.QUEUE_RUNNING
    window._apply_state_ui()

    assert window._measure_submode_toggle.isEnabled() is False
    window.close()


def test_single_and_two_channel_workspaces_survive_mode_changes(
    qapp, monkeypatch, tmp_path: Path
) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    window._kept_curves = [_curve(9.0)]
    window._recompute_average()
    window._two_channel_pairs = [TwoChannelCurvePair(_curve(1.0), _curve(-2.0))]
    window._recompute_two_channel_results()

    window._two_channel_toggle.setChecked(False)
    assert len(window._kept_curves) == 1
    assert len(window._two_channel_pairs) == 1
    np.testing.assert_allclose(window._bottom_curve_for_display_and_export()[1], 0.0)

    window._two_channel_toggle.setChecked(True)
    window._plots.two.set_selection("channel_2")
    assert window._active_measure_label() == "R"
    assert window._active_measure_session().channel_side == "R"
    assert window._session.channel_side == ""
    np.testing.assert_allclose(
        window._bottom_curve_for_display_and_export()[1], -2.0
    )
    window.close()


def test_pair_processing_uses_one_shared_reference_offset(
    qapp, monkeypatch, tmp_path: Path
) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    freqs = np.array([100.0, 1000.0, 10000.0])
    responses = iter(
        [
            (freqs, np.array([2.0, 4.0, 6.0])),
            (freqs, np.array([-2.0, 0.0, 2.0])),
        ]
    )
    monkeypatch.setattr(
        main_window_module,
        "compute_frequency_response",
        lambda **_kwargs: next(responses),
    )
    monkeypatch.setattr(main_window_module.QTimer, "singleShot", lambda *_args: None)
    window._queue_target = 1
    window._state = AppState.SWEEPING
    window._two_channel_stage = 1

    window._on_sweep_finished(np.zeros(8), np.zeros(8))
    assert window._pending_pair_first_raw is not None
    assert window._start_second_pair_stage is True

    window._two_channel_stage = 2
    window._state = AppState.SWEEPING
    window._on_sweep_finished(np.zeros(8), np.zeros(8))

    assert window._pending_pair is not None
    first = window._pending_pair.channel_1[1]
    second = window._pending_pair.channel_2[1]
    np.testing.assert_allclose(first - second, 4.0, atol=1e-8)
    pair_freqs = window._pending_pair.channel_1[0]
    first_reference = float(np.interp(1000.0, pair_freqs, first))
    second_reference = float(np.interp(1000.0, pair_freqs, second))
    reference_power = (
        10.0 ** (first_reference / 10.0)
        + 10.0 ** (second_reference / 10.0)
    ) / 2.0
    assert 10.0 * np.log10(reference_power) == pytest.approx(0.0, abs=1e-6)
    window.close()


def test_second_stage_failure_discards_pair_and_schedules_full_retry(
    qapp, monkeypatch, tmp_path: Path
) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    window._queue_target = 1
    window._queue_index = 0
    window._current_sweep_attempts = 1
    window._state = AppState.SWEEPING
    window._two_channel_stage = 2
    window._pending_pair_first_raw = _curve(1.0)
    window._pending_pair_first_diagnostics = object()
    scheduled = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: main_window_module.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        main_window_module.QTimer,
        "singleShot",
        lambda _delay, callback: scheduled.append(callback),
    )

    window._on_sweep_error("Channel 2 stream failed.")

    assert window._pending_pair_first_raw is None
    assert window._pending_pair_first_diagnostics is None
    assert window._two_channel_stage == 0
    assert window._state == AppState.QUEUE_RUNNING
    assert window._start_next_sweep in scheduled
    window._queue_target = 0
    window.close()
