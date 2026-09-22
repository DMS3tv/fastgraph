import numpy as np
import pytest
from PyQt6.QtCore import QEvent, QSize
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QApplication,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
)

import dms.dither_fonts as dither_fonts
import dms.ui.main_window as main_window_module
from dms.measure_queue import QueueState
from dms.style_tokens import DITHER_TOKENS
from dms.theme import (
    DARK,
    DITHER,
    FASTGRAPH_95,
    FASTGRAPH_95_DARK,
    HACKERMAN_95,
    LIGHT,
)
from dms.two_channel import TwoChannelCurvePair
from dms.ui.main_window import MainWindow
from dms.ui.modern_button import (
    _FLAT_LABEL_HORIZONTAL_INSET,
    _FLAT_PAINT_RECT_WIDTH_LOSS,
)


def _window(make_main_window, *, theme: str = DARK) -> MainWindow:
    return make_main_window(
        theme=theme,
        settings={
            "measure_two_channel_enabled": True,
            "measure_two_channel_bottom_mode": "separate",
        },
    )


def _process_theme_change(qapp) -> None:
    for _ in range(8):
        qapp.processEvents()


def _independent_dither_label_width(label: str) -> int:
    base = QFont()
    base.setPixelSize(DITHER_TOKENS.typography.body_px)
    heading = dither_fonts.dither_heading_font(base)
    label_width = QFontMetrics(heading).horizontalAdvance(label.upper())
    border_width = max(
        DITHER_TOKENS.geometry.border_px,
        DITHER_TOKENS.geometry.focus_border_px,
    )
    return (
        label_width
        + 2 * _FLAT_LABEL_HORIZONTAL_INSET
        + 2 * border_width
        + _FLAT_PAINT_RECT_WIDTH_LOSS
    )


def _curve(level: float):
    return (
        np.array([100.0, 1000.0, 10000.0]),
        np.array([level, level, level]),
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
        if not window._measure_submode_control._width_refresh_pending:
            break
    assert window._measure_submode_control._width_refresh_pending is False


def test_restores_two_channel_layout_but_starts_in_frequency_response(make_main_window) -> None:
    window = _window(make_main_window)

    assert window._two_channel_enabled is True
    assert window._plots._stack.currentWidget() is window._plots.two
    assert window._two_channel_bottom_mode == "separate"
    assert window._measure_frequency_button.isChecked() is True
    assert window._measure_balance_button.isChecked() is False
    assert window._measure_frequency_button.text() == "Frequency Response"
    assert window._measure_balance_button.text() == "Channel Balance"
    assert window._channel_balance_active is False


def test_measure_submode_segments_change_mode_and_stop_generator(
    monkeypatch, make_main_window
) -> None:
    window = _window(make_main_window)
    stop_calls = []
    monkeypatch.setattr(
        window,
        "_stop_channel_balance",
        lambda *_args: stop_calls.append(True),
    )

    assert window._measure_submode_control.isHidden() is False
    window._measure_balance_button.setChecked(True)
    assert window._channel_balance_mode_active() is True
    assert window._measure_frequency_button.isChecked() is False
    assert window._measure_balance_button.isChecked() is True

    window._measure_frequency_button.setChecked(True)
    assert window._channel_balance_mode_active() is False
    assert window._measure_frequency_button.isChecked() is True
    assert window._measure_balance_button.isChecked() is False
    assert stop_calls

    window._two_channel_toggle.setChecked(False)
    assert window._measure_submode_control.isHidden() is True
    assert window._measure_frequency_button.isChecked() is True
    assert window._measure_balance_button.isChecked() is False


def test_measure_submode_segments_are_disabled_while_busy(make_main_window) -> None:
    window = _window(make_main_window)
    window._measure_balance_button.setChecked(True)
    window._state = QueueState.QUEUE_RUNNING
    window._apply_state_ui()

    assert window._measure_submode_control.isEnabled() is False
    assert window._measure_frequency_button.isEnabled() is False
    assert window._measure_balance_button.isEnabled() is False
    assert window._measure_balance_button.isChecked() is True


def test_measure_submode_segments_keep_text_width_in_all_display_profiles(
    qapp, make_main_window
) -> None:
    window = _window(make_main_window)
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
            window._measure_frequency_button,
            window._measure_balance_button,
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
        window._measure_submode_control.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed
    )


def test_measure_button_width_matches_dither_startup_and_switch_paths(
    qapp,
    make_main_window,
) -> None:
    startup_window = _window(make_main_window, theme=DITHER)
    startup_window.show()
    _process_theme_change(qapp)
    startup_button = startup_window._start_queue_btn
    startup_image = startup_button.grab().toImage()
    startup_width = startup_image.width()
    startup_hint_width = startup_button.sizeHint().width()

    assert startup_image.isNull() is False
    assert startup_width >= _independent_dither_label_width("Measure")

    startup_window.close()
    startup_window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    switch_window = _window(make_main_window, theme=DARK)
    switch_window.show()
    _process_theme_change(qapp)
    switch_button = switch_window._start_queue_btn
    dark_width = switch_button.grab().toImage().width()
    dark_hint_width = switch_button.sizeHint().width()

    switch_window._theme_controller.set_theme(DITHER, persist=False)
    _process_theme_change(qapp)
    switch_image = switch_button.grab().toImage()

    assert switch_image.isNull() is False
    assert switch_image.width() >= _independent_dither_label_width("Measure")
    assert switch_image.width() == startup_width
    assert switch_button.sizeHint().width() == startup_hint_width

    switch_window._theme_controller.set_theme(DARK, persist=False)
    _process_theme_change(qapp)

    assert switch_button.grab().toImage().width() == dark_width
    assert switch_button.sizeHint().width() == dark_hint_width


def test_measure_submode_accessibility_and_responsive_width(make_main_window) -> None:
    window = _window(make_main_window)

    assert window._measure_submode_control.accessibleName() == "Measure mode"
    assert window._measure_submode_control.toolTip()
    assert window._measure_frequency_button.accessibleName()
    assert window._measure_frequency_button.toolTip()
    assert window._measure_balance_button.accessibleName()
    assert window._measure_balance_button.toolTip()
    assert window._queue_bar.compact_breakpoint == (
        window._queue_bar._BASE_COMPACT_WIDTH
        + window._measure_submode_control.minimum_control_width
    )
    expanded_breakpoint = window._queue_bar.compact_breakpoint
    window._queue_bar._update_compact_state(expanded_breakpoint - 1)
    assert window._queue_bar_compact is True
    window._queue_bar._update_compact_state(expanded_breakpoint)
    assert window._queue_bar_compact is False

    window._two_channel_toggle.setChecked(False)
    assert window._queue_bar.compact_breakpoint == window._queue_bar._BASE_COMPACT_WIDTH


def test_single_and_two_channel_workspaces_survive_mode_changes(make_main_window) -> None:
    window = _window(make_main_window)
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
    np.testing.assert_allclose(window._bottom_curve_for_display_and_export()[1], -2.0)


def test_pair_processing_uses_one_shared_reference_offset(monkeypatch, make_main_window) -> None:
    window = _window(make_main_window)
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
    window._state = QueueState.SWEEPING
    window._two_channel_stage = 1

    window._on_sweep_finished(np.zeros(8), np.zeros(8))
    assert window._pending_pair_first_raw is not None
    assert window._start_second_pair_stage is True

    window._two_channel_stage = 2
    window._state = QueueState.SWEEPING
    window._on_sweep_finished(np.zeros(8), np.zeros(8))

    assert window._pending_pair is not None
    first = window._pending_pair.channel_1[1]
    second = window._pending_pair.channel_2[1]
    np.testing.assert_allclose(first - second, 4.0, atol=1e-8)
    pair_freqs = window._pending_pair.channel_1[0]
    first_reference = float(np.interp(1000.0, pair_freqs, first))
    second_reference = float(np.interp(1000.0, pair_freqs, second))
    reference_power = (10.0 ** (first_reference / 10.0) + 10.0 ** (second_reference / 10.0)) / 2.0
    assert 10.0 * np.log10(reference_power) == pytest.approx(0.0, abs=1e-6)


def test_second_stage_failure_discards_pair_and_schedules_full_retry(
    monkeypatch, make_main_window
) -> None:
    window = _window(make_main_window)
    window._queue_target = 1
    window._queue_index = 0
    window._current_sweep_attempts = 1
    window._state = QueueState.SWEEPING
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
    assert window._state == QueueState.QUEUE_RUNNING
    assert window._start_next_sweep in scheduled
    window._queue_target = 0
