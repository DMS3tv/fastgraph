import numpy as np
import pytest
from helpers import flat_curve
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QSizePolicy, QStyle, QStyleOptionButton

import dms.ui.measure_controller as measure_controller_module
from dms.measure_queue import QueueState
from dms.theme import DARK, DITHER, FASTGRAPH_95, FASTGRAPH_95_DARK, HACKERMAN_95, LIGHT
from dms.two_channel import TwoChannelCurvePair
from dms.ui.main_window import MainWindow


def _two_channel_window(make_main_window, *, theme: str = DARK) -> MainWindow:
    return make_main_window(
        theme=theme,
        settings={
            "measure_two_channel_enabled": True,
            "measure_two_channel_bottom_mode": "separate",
        },
    )


_MEASURE_SEGMENT_STATES = (
    ("resting", QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Off),
    (
        "focus",
        QStyle.StateFlag.State_Enabled
        | QStyle.StateFlag.State_Off
        | QStyle.StateFlag.State_HasFocus,
    ),
    (
        "hover",
        QStyle.StateFlag.State_Enabled
        | QStyle.StateFlag.State_Off
        | QStyle.StateFlag.State_MouseOver,
    ),
    ("checked", QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_On),
    (
        "checked focus",
        QStyle.StateFlag.State_Enabled
        | QStyle.StateFlag.State_On
        | QStyle.StateFlag.State_HasFocus,
    ),
    (
        "checked hover",
        QStyle.StateFlag.State_Enabled
        | QStyle.StateFlag.State_On
        | QStyle.StateFlag.State_MouseOver,
    ),
)


def _resolved_segment_width(button, state: QStyle.StateFlag) -> tuple[int, int]:
    metrics = QFontMetrics(button.font())
    text_width = metrics.horizontalAdvance(button.text())
    option = QStyleOptionButton()
    option.initFrom(button)
    option.text = button.text()
    option.state = state
    required_width = (
        button.style()
        .sizeFromContents(
            QStyle.ContentsType.CT_PushButton,
            option,
            QSize(text_width, metrics.height()),
            button,
        )
        .width()
    )
    return text_width, required_width


def _settle_segment_width_refresh(qapp, window: MainWindow) -> None:
    for _ in range(4):
        qapp.processEvents()
        if not window.measure_tab.measure_submode_control._width_refresh_pending:
            break
    assert window.measure_tab.measure_submode_control._width_refresh_pending is False


def test_restores_two_channel_layout_but_starts_in_frequency_response(make_main_window) -> None:
    window = _two_channel_window(make_main_window)

    assert window.measure.two_channel_enabled is True
    assert window._plots._stack.currentWidget() is window._plots.two
    assert window.measure.two_channel_bottom_mode == "separate"
    assert window.measure_tab.measure_frequency_button.isChecked() is True
    assert window.measure_tab.measure_balance_button.isChecked() is False
    assert window.measure_tab.measure_frequency_button.text() == "Frequency Response"
    assert window.measure_tab.measure_balance_button.text() == "Channel Balance"
    assert window.measure.channel_balance_active is False


def test_measure_submode_segments_change_mode_and_stop_generator(
    monkeypatch, make_main_window
) -> None:
    window = _two_channel_window(make_main_window)
    stop_calls = []
    monkeypatch.setattr(
        window.measure,
        "stop_channel_balance",
        lambda *_args: stop_calls.append(True),
    )

    assert window.measure_tab.measure_submode_control.isHidden() is False
    window.measure_tab.measure_balance_button.setChecked(True)
    assert window.measure.channel_balance_mode_active() is True
    assert window.measure_tab.measure_frequency_button.isChecked() is False
    assert window.measure_tab.measure_balance_button.isChecked() is True

    window.measure_tab.measure_frequency_button.setChecked(True)
    assert window.measure.channel_balance_mode_active() is False
    assert window.measure_tab.measure_frequency_button.isChecked() is True
    assert window.measure_tab.measure_balance_button.isChecked() is False
    assert stop_calls

    window.measure_tab.two_channel_toggle.setChecked(False)
    assert window.measure_tab.measure_submode_control.isHidden() is True
    assert window.measure_tab.measure_frequency_button.isChecked() is True
    assert window.measure_tab.measure_balance_button.isChecked() is False


def test_measure_submode_segments_are_disabled_while_busy(make_main_window) -> None:
    window = _two_channel_window(make_main_window)
    window.measure_tab.measure_balance_button.setChecked(True)
    window.measure.queue.state = QueueState.QUEUE_RUNNING
    window._apply_state_ui()

    assert window.measure_tab.measure_submode_control.isEnabled() is False
    assert window.measure_tab.measure_frequency_button.isEnabled() is False
    assert window.measure_tab.measure_balance_button.isEnabled() is False
    assert window.measure_tab.measure_balance_button.isChecked() is True


def test_measure_submode_segments_keep_text_width_in_all_display_profiles(
    qapp, make_main_window
) -> None:
    window = _two_channel_window(make_main_window)
    window.show()
    _settle_segment_width_refresh(qapp, window)

    profiles = (
        (DARK, False),
        (LIGHT, False),
        (FASTGRAPH_95, False),
        (FASTGRAPH_95_DARK, False),
        (HACKERMAN_95, False),
        (DITHER, False),
        (DARK, True),
    )
    for theme, brand_mode in profiles:
        window._theme_controller.set_brand_mode(brand_mode)
        if not brand_mode:
            window._theme_controller.set_theme(theme)
        _settle_segment_width_refresh(qapp, window)

        for button in (
            window.measure_tab.measure_frequency_button,
            window.measure_tab.measure_balance_button,
        ):
            for state_name, state in _MEASURE_SEGMENT_STATES:
                text_width, required_width = _resolved_segment_width(button, state)
                chrome_width = required_width - text_width
                context = f"{theme=} {brand_mode=} {button.text()=} {state_name=}"
                assert chrome_width > 0, context
                assert button.width() - text_width > chrome_width, context
                assert button.minimumWidth() > required_width, context
            assert button.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed

    assert (
        window.measure_tab.measure_submode_control.sizePolicy().horizontalPolicy()
        == QSizePolicy.Policy.Fixed
    )


def test_measure_submode_accessibility_and_responsive_width(make_main_window) -> None:
    window = _two_channel_window(make_main_window)

    assert window.measure_tab.measure_submode_control.accessibleName() == "Measure mode"
    assert window.measure_tab.measure_submode_control.toolTip()
    assert window.measure_tab.measure_frequency_button.accessibleName()
    assert window.measure_tab.measure_frequency_button.toolTip()
    assert window.measure_tab.measure_balance_button.accessibleName()
    assert window.measure_tab.measure_balance_button.toolTip()
    assert window.measure_tab.queue_bar.compact_breakpoint == (
        window.measure_tab.queue_bar._BASE_COMPACT_WIDTH
        + window.measure_tab.measure_submode_control.minimum_control_width
    )
    expanded_breakpoint = window.measure_tab.queue_bar.compact_breakpoint
    window.measure_tab.queue_bar._update_compact_state(expanded_breakpoint - 1)
    assert window.measure_tab.queue_bar_compact is True
    window.measure_tab.queue_bar._update_compact_state(expanded_breakpoint)
    assert window.measure_tab.queue_bar_compact is False

    window.measure_tab.two_channel_toggle.setChecked(False)
    assert (
        window.measure_tab.queue_bar.compact_breakpoint
        == window.measure_tab.queue_bar._BASE_COMPACT_WIDTH
    )


def test_single_and_two_channel_workspaces_survive_mode_changes(make_main_window) -> None:
    window = _two_channel_window(make_main_window)
    window.measure.kept_curves = [flat_curve(9.0)]
    window.measure.recompute_average()
    window.measure.two_channel_pairs = [TwoChannelCurvePair(flat_curve(1.0), flat_curve(-2.0))]
    window.measure.recompute_two_channel_results()

    window.measure_tab.two_channel_toggle.setChecked(False)
    assert len(window.measure.kept_curves) == 1
    assert len(window.measure.two_channel_pairs) == 1
    np.testing.assert_allclose(window.measure.bottom_curve_for_display_and_export()[1], 0.0)

    window.measure_tab.two_channel_toggle.setChecked(True)
    window._plots.two.set_selection("channel_2")
    assert window.measure.active_measure_label() == "R"
    assert window.measure.active_measure_session().channel_side == "R"
    assert window._session.channel_side == ""
    np.testing.assert_allclose(window.measure.bottom_curve_for_display_and_export()[1], -2.0)


def test_pair_processing_uses_one_shared_reference_offset(monkeypatch, make_main_window) -> None:
    window = _two_channel_window(make_main_window)
    freqs = np.array([100.0, 1000.0, 10000.0])
    responses = iter(
        [
            (freqs, np.array([2.0, 4.0, 6.0])),
            (freqs, np.array([-2.0, 0.0, 2.0])),
        ]
    )
    monkeypatch.setattr(
        measure_controller_module,
        "compute_frequency_response",
        lambda **_kwargs: next(responses),
    )
    monkeypatch.setattr(measure_controller_module.QTimer, "singleShot", lambda *_args: None)
    window.measure.queue.target = 1
    window.measure.queue.state = QueueState.SWEEPING
    window.measure.queue.stage = 1

    window.measure.on_sweep_finished(np.zeros(8), np.zeros(8))
    assert window.measure.queue.pending_pair_first_raw is not None
    assert window.measure.queue.start_second_stage is True

    window.measure.queue.stage = 2
    window.measure.queue.state = QueueState.SWEEPING
    window.measure.on_sweep_finished(np.zeros(8), np.zeros(8))

    assert window.measure.queue.pending_pair is not None
    first = window.measure.queue.pending_pair.channel_1[1]
    second = window.measure.queue.pending_pair.channel_2[1]
    np.testing.assert_allclose(first - second, 4.0, atol=1e-8)
    pair_freqs = window.measure.queue.pending_pair.channel_1[0]
    first_reference = float(np.interp(1000.0, pair_freqs, first))
    second_reference = float(np.interp(1000.0, pair_freqs, second))
    reference_power = (10.0 ** (first_reference / 10.0) + 10.0 ** (second_reference / 10.0)) / 2.0
    assert 10.0 * np.log10(reference_power) == pytest.approx(0.0, abs=1e-6)


def test_second_stage_failure_discards_pair_and_schedules_full_retry(
    monkeypatch, make_main_window
) -> None:
    window = _two_channel_window(make_main_window)
    window.measure.queue.target = 1
    window.measure.queue.index = 0
    window.measure.queue.attempts = 1
    window.measure.queue.state = QueueState.SWEEPING
    window.measure.queue.stage = 2
    window.measure.queue.pending_pair_first_raw = flat_curve(1.0)
    window.measure.queue.pending_pair_first_diagnostics = object()
    scheduled = []
    monkeypatch.setattr(
        measure_controller_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: measure_controller_module.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        measure_controller_module.QTimer,
        "singleShot",
        lambda _delay, callback: scheduled.append(callback),
    )

    window.measure.on_sweep_error("Channel 2 stream failed.")

    assert window.measure.queue.pending_pair_first_raw is None
    assert window.measure.queue.pending_pair_first_diagnostics is None
    assert window.measure.queue.stage == 0
    assert window.measure.queue.state == QueueState.QUEUE_RUNNING
    assert window.measure.start_next_sweep in scheduled
    window.measure.queue.target = 0
