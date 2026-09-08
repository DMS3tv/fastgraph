from pathlib import Path

import numpy as np
from PyQt6.QtTest import QTest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QGroupBox, QToolButton

from dms.curator.transforms import apply_layer_transform
from dms.hrtf import HRTFCurve
from dms.session import SessionData
from dms.ui.main_window import AppState


def _window(make_main_window):
    return make_main_window(
        session=SessionData(rig="Test Rig", brand="DMS", model="Demo"),
        confirm_rnd_close=False,
    )


def test_curator_is_middle_tab(make_main_window) -> None:
    window = _window(make_main_window)
    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "Measure", "R&&D", "Curator", "Automation", "Settings"
    ]
    assert window._queue_level_persist_toggle.minimumSizeHint().width() >= 54


def test_measure_controls_are_embedded_around_plots(make_main_window) -> None:
    window = _window(make_main_window)

    assert window._plots._between_plots_widget.objectName() == "measure_interplot_controls"
    assert window._plots._footer_widget.objectName() == "measure_export_controls"
    plot_layout = window._plots.layout()
    assert [plot_layout.itemAt(index).widget() for index in range(plot_layout.count())] == [
        window._plots._header_widget,
        window._plots._top_frame,
        window._plots._between_plots_widget,
        window._plots._bot_frame,
        window._plots._footer_widget,
    ]
    assert window._clear_btn.objectName() == "btn_danger"
    assert window._level_meter.parent() is window._plots._between_plots_widget
    assert window._export_dir_input.parent() is window._plots._footer_widget
    assert not window._clear_btn.isEnabled()


def test_session_and_bluetooth_controls_precede_tabs(qapp, make_main_window) -> None:
    window = _window(make_main_window)
    window.resize(1280, 800)
    window.show()
    qapp.processEvents()

    header = window._tabs.cornerWidget(Qt.Corner.TopLeftCorner)
    assert header is not None
    assert header.objectName() == "tab_header_controls"
    assert header.geometry().right() <= window._tabs.tabBar().geometry().left()
    assert header.width() == header.sizeHint().width()
    assert window._tabs.tabBar().geometry().left() - header.geometry().right() <= 1
    assert window._metadata_btn.parent() is header
    assert window._clear_metadata_btn.parent() is header
    assert window._clear_metadata_btn.objectName() == "btn_danger"
    assert window._bluetooth_mode_toggle.parent() is header
    assert window._inputs_btn.parent() is header
    assert window._inputs_btn.text() == "Inputs"
    assert window._inputs_btn.property("emphasized") is True
    assert window._metadata_btn.geometry().bottom() <= header.rect().bottom()
    assert window._clear_metadata_btn.geometry().bottom() <= header.rect().bottom()

    section_titles = [button.text() for button in window.findChildren(QToolButton)]
    group_titles = [group.title() for group in window.findChildren(QGroupBox)]
    assert "Session" not in section_titles
    assert "Measurement Mode" not in group_titles

    window._tabs.setCurrentWidget(window._console_widget)
    qapp.processEvents()
    assert header.isVisible()


def test_measure_queue_bar_replaces_sidebar_and_wraps_progress(make_main_window) -> None:
    window = _window(make_main_window)
    measure = window._tabs.widget(0)

    assert measure.layout().count() == 1
    assert window._plots._header_widget.objectName() == "measure_queue_bar"
    assert window._queue_primary_layout.itemAt(0).widget() is window._start_queue_btn
    assert window._start_queue_btn.text() == "Measure"
    assert not hasattr(window, "_queue_hint_label")

    window._set_queue_bar_compact(False)
    assert window._queue_progress_bar.parent() is window._queue_primary_widget
    assert not window._queue_progress_widget.isVisible()

    window._set_queue_bar_compact(True)
    assert window._queue_progress_bar.parent() is window._queue_progress_widget


def test_inputs_overlay_animates_closes_and_is_read_only_while_busy(
    qapp,
    make_main_window) -> None:
    window = _window(make_main_window)
    window.resize(1280, 800)
    window.show()
    qapp.processEvents()

    window._open_inputs_overlay()
    window._inputs_overlay_animation.setCurrentTime(180)
    qapp.processEvents()
    assert window._inputs_overlay.isVisible()
    assert window._inputs_overlay.height() > 0
    assert window._inputs_overlay.parent() is window._tabs
    assert window._inputs_overlay.geometry().right() <= window._tabs.rect().right()
    assert window._inputs_overlay.geometry().bottom() <= window._tabs.rect().bottom()
    assert window._inputs_btn.role() == "primary"
    assert window._inputs_btn._has_persistent_outline()

    window._state = AppState.QUEUE_RUNNING
    window._apply_state_ui()
    assert window._inputs_btn.isEnabled()
    assert not window._out_dev_combo.isEnabled()
    assert not window._in_dev_combo.isEnabled()
    assert not window._ch_combo.isEnabled()
    assert not window._refresh_devices_btn.isEnabled()

    QTest.keyClick(window, Qt.Key.Key_Escape)
    window._inputs_overlay_animation.setCurrentTime(180)
    qapp.processEvents()
    assert not window._inputs_overlay.isVisible()


def test_inputs_overlay_closes_after_an_outside_click(qapp, make_main_window) -> None:
    window = _window(make_main_window)
    window.resize(1280, 800)
    window.show()
    window._open_inputs_overlay()
    window._inputs_overlay_animation.setCurrentTime(180)
    qapp.processEvents()

    QTest.mouseClick(
        window._plots._bot_plot.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(8, 8),
    )
    window._inputs_overlay_animation.setCurrentTime(180)
    qapp.processEvents()

    assert not window._inputs_overlay.isVisible()


def test_inputs_overlay_closes_on_tab_change(qapp, make_main_window) -> None:
    window = _window(make_main_window)
    window.show()
    window._open_inputs_overlay()
    window._inputs_overlay_animation.setCurrentTime(180)
    window._tabs.setCurrentWidget(window._rnd_widget)
    window._inputs_overlay_animation.setCurrentTime(180)
    qapp.processEvents()
    assert not window._inputs_overlay.isVisible()


def test_metadata_button_opens_dropdown_and_saves_session(qapp, make_main_window) -> None:
    window = _window(make_main_window)
    window.resize(1280, 800)
    window.show()
    qapp.processEvents()

    window._open_metadata_overlay()
    window._metadata_overlay_animation.setCurrentTime(180)
    qapp.processEvents()

    assert window._metadata_overlay.isVisible()
    assert window._metadata_overlay.parent() is window._tabs
    assert window._metadata_overlay.geometry().right() <= window._tabs.rect().right()
    assert window._metadata_overlay.geometry().bottom() <= window._tabs.rect().bottom()
    assert not window._inputs_overlay_open

    editor = window._metadata_editor
    editor._rig.setCurrentText("B&K 5128")
    editor._brand.setText("DMS")
    editor._model.setText("Dropdown Test")
    editor._channel_side.setCurrentText("L")
    window._save_metadata_overlay()
    window._metadata_overlay_animation.setCurrentTime(180)
    qapp.processEvents()

    assert window._session.rig == "B&K 5128"
    assert window._session.model == "Dropdown Test"
    assert window._session.channel_side == "L"
    assert not window._metadata_overlay.isVisible()


def test_measure_plots_keep_frequency_endpoints_inside_view(qapp, make_main_window) -> None:
    window = _window(make_main_window)
    window.resize(1280, 800)
    window.show()
    qapp.processEvents()

    expected_min = np.log10(20.0)
    expected_max = np.log10(20000.0)
    for plot in (window._plots._top_plot, window._plots._bot_plot):
        x_min, x_max = plot.getPlotItem().getViewBox().viewRange()[0]
        assert x_min < expected_min
        assert x_max > expected_max


def test_settings_tab_saves_immediately_and_disables_edits_while_busy(make_main_window) -> None:
    window = _window(make_main_window)
    settings_widget = window._settings_widget

    settings_widget._fs.setCurrentIndex(settings_widget._fs.findData(96000))
    assert window._settings.get("sample_rate") == 96000

    settings_widget._confirm_clear.setChecked(False)
    assert window._settings.get("confirm_clear_measurements") is False
    settings_widget._confirm_clear_metadata.setChecked(False)
    assert window._settings.get("confirm_clear_metadata") is False

    window._state = "queue_running"
    window._apply_state_ui()
    assert window._tabs.isTabEnabled(window._tabs.indexOf(window._settings_scroll))
    assert not settings_widget._sweep_group.isEnabled()
    assert not settings_widget._audio_tools_group.isEnabled()
    assert not window._metadata_btn.isEnabled()
    assert not window._clear_metadata_btn.isEnabled()
    assert not window._bluetooth_mode_toggle.isEnabled()


def test_settings_column_is_compact_and_left_aligned(qapp, make_main_window) -> None:
    window = _window(make_main_window)
    window.resize(1280, 800)
    window.show()
    window._tabs.setCurrentWidget(window._settings_scroll)
    qapp.processEvents()

    column = window._settings_widget._settings_column
    assert column.x() == 0
    assert column.width() == 560
    assert column.geometry().right() < window._settings_widget.width()


def test_window_title_refreshes_when_metadata_is_cleared(make_main_window) -> None:
    window = _window(make_main_window)
    window._confirm_clear_metadata = lambda: (True, False)
    window._clear_metadata()

    assert "Unknown Unknown @ Unknown Rig" in window.windowTitle()


def test_metadata_clear_confirmation_and_preference(make_main_window) -> None:
    window = _window(make_main_window)

    window._confirm_clear_metadata = lambda: (False, True)
    window._clear_metadata()
    assert window._session.display_name() == "DMS Demo"
    assert window._settings.get("confirm_clear_metadata") is True

    window._confirm_clear_metadata = lambda: (True, True)
    window._clear_metadata()
    assert window._session.display_name() == "Unknown Unknown"
    assert window._settings.get("confirm_clear_metadata") is False
    assert not window._settings_widget._confirm_clear_metadata.isChecked()

    window._settings_widget._confirm_clear_metadata.setChecked(True)
    assert window._settings.get("confirm_clear_metadata") is True


def test_clear_confirmation_preference_and_tab_isolation(make_main_window) -> None:
    window = _window(make_main_window)
    curve = (np.array([100.0, 1000.0]), np.array([1.0, 0.0]))
    window._kept_curves = [curve]
    window._recompute_average()
    window._update_plots()
    curator_marker = object()
    window._curator_widget.graph_state.layers.append(curator_marker)
    window._console_events.publish("INFO", "test", "keep me")
    event_count = len(window._console_events.events())

    window._confirm_clear_all = lambda: (False, True)
    window._clear_all()
    assert len(window._kept_curves) == 1
    assert window._kept_curves[0] is curve
    assert window._settings.get("confirm_clear_measurements") is True

    window._confirm_clear_all = lambda: (True, True)
    window._clear_all()
    assert window._kept_curves == []
    assert window._settings.get("confirm_clear_measurements") is False
    assert window._curator_widget.graph_state.layers == [curator_marker]
    assert len(window._console_events.events()) >= event_count
    assert not window._clear_btn.isEnabled()

    window._settings_widget._confirm_clear.setChecked(True)
    assert window._settings.get("confirm_clear_measurements") is True


def test_send_average_offsets_display_and_preserves_editable_hrtf(make_main_window, tmp_path: Path) -> None:
    window = _window(make_main_window)
    hrtf_path = tmp_path / "fixture.txt"
    hrtf_path.write_text("100 1\n1000 2\n10000 3\n", encoding="utf-8")
    window._hrtf = HRTFCurve(str(hrtf_path))
    window._hrtf_toggle.setChecked(True)
    freqs = np.array([100.0, 1000.0, 10000.0])
    source_mag = np.array([4.0, 0.0, -4.0])
    window._average = (freqs, source_mag)
    window._update_plots()
    expected_freqs, expected_mag = window._bottom_curve_for_display()

    window._send_to_curator()

    layer = window._curator_widget.graph_state.layers[0]
    displayed = apply_layer_transform(layer)
    assert layer.name == "DMS Demo COMP AVG"
    assert layer.hrtf is window._hrtf
    assert np.allclose(displayed.freqs, expected_freqs)
    offset = -float(np.interp(1000.0, expected_freqs, expected_mag))
    assert np.allclose(displayed.mag_db, expected_mag + offset)
    assert np.isclose(np.interp(1000.0, displayed.freqs, displayed.mag_db), 0.0)
    assert window._tabs.currentWidget() is window._curator_widget
    assert layer.curve.metadata["brand"] == "DMS"
    assert layer.curve.metadata["model"] == "Demo"
    assert layer.curve.metadata["rig"] == "Test Rig"
    assert layer.curve.metadata["hrtf_name"] == "fixture"
    assert layer.curve.metadata["compensated"] is True
    source_mag[:] = 99.0
    assert not np.allclose(layer.curve.mag_db, source_mag)


def test_send_variation_offsets_display_with_editable_hrtf(make_main_window, tmp_path: Path) -> None:
    window = _window(make_main_window)
    hrtf_path = tmp_path / "fixture.txt"
    hrtf_path.write_text("100 1\n1000 2\n", encoding="utf-8")
    window._hrtf = HRTFCurve(str(hrtf_path))
    window._hrtf_toggle.setChecked(True)
    window._variation_toggle.setChecked(True)
    freqs = np.array([100.0, 1000.0])
    rows = [np.array([value, value + 1.0]) for value in (-2.0, -1.0, 1.0, 2.0, 0.0)]
    window._variation = (freqs, *rows)
    expected = tuple(np.array(values, copy=True) for values in window._variation[1:])

    window._send_to_curator()

    layer = window._curator_widget.graph_state.layers[0]
    displayed = apply_layer_transform(layer)
    assert layer.name == "DMS Demo COMP VAR"
    median_offset = -float(np.interp(1000.0, freqs, expected[-1]))
    for actual, wanted in zip(
        (displayed.p10_db, displayed.p25_db, displayed.p75_db, displayed.p90_db, displayed.median_db),
        expected,
    ):
        assert np.allclose(actual, wanted + median_offset)
    for _ in range(20):
        if window._curator_widget._graph.wipeProgress >= 0.98:
            break
        QTest.qWait(50)
    assert window._curator_widget._graph.wipeProgress >= 0.98
    assert len(window._curator_widget._graph._items) > 3


def test_send_population_compensation_to_curator_keeps_editable_var_hrtf(
    make_main_window,
    tmp_path: Path,
) -> None:
    window = _window(make_main_window)
    hrtf_path = tmp_path / "population.txt"
    hrtf_path.write_text(
        "100 1 2 3 4 5\n"
        "1000 10 20 30 40 50\n",
        encoding="utf-8",
    )
    window._hrtf = HRTFCurve(str(hrtf_path))
    window._hrtf_toggle.setChecked(True)
    freqs = np.array([100.0, 1000.0])
    window._kept_curves = [(freqs, np.array([10.0, 100.0]))]
    window._recompute_average()
    window._update_plots()
    expected = tuple(np.array(values, copy=True) for values in window._variation[1:])

    window._send_to_curator()

    layer = window._curator_widget.graph_state.layers[0]
    displayed = apply_layer_transform(layer)
    assert layer.name == "DMS Demo COMP VAR"
    assert layer.hrtf is window._hrtf
    assert displayed.kind == "variation"
    median_offset = -float(np.interp(1000.0, displayed.freqs, expected[-1]))
    for actual, wanted in zip(
        (
            displayed.p10_db,
            displayed.p25_db,
            displayed.p75_db,
            displayed.p90_db,
            displayed.median_db,
        ),
        expected,
    ):
        assert np.allclose(actual, wanted + median_offset)


def test_send_variation_offsets_to_zero_without_changing_source_shape(make_main_window) -> None:
    window = _window(make_main_window)
    window._variation_toggle.setChecked(True)
    freqs = np.array([100.0, 1000.0, 10000.0])
    window._variation = (
        freqs,
        np.array([72.0, 73.0, 74.0]),
        np.array([74.0, 75.0, 76.0]),
        np.array([78.0, 79.0, 80.0]),
        np.array([80.0, 81.0, 82.0]),
        np.array([76.0, 77.0, 78.0]),
    )

    window._send_to_curator()

    state = window._curator_widget.graph_state
    layer = state.layers[0]
    displayed = apply_layer_transform(layer)
    assert state.y_min == -17.5
    assert state.y_max == 17.5
    assert np.isclose(np.interp(1000.0, displayed.freqs, displayed.median_db), 0.0)
    assert np.allclose(np.diff(layer.curve.median_db), [1.0, 1.0])
    assert np.allclose(np.diff(displayed.median_db), [1.0, 1.0])
    QTest.qWait(250)
    assert len(window._curator_widget._graph._items) > 3


def test_curator_console_commands_update_workspace_and_log(make_main_window, tmp_path: Path) -> None:
    window = _window(make_main_window)
    source = tmp_path / "curve.txt"
    source.write_text("100 1\n1000 2\n", encoding="utf-8")

    window._run_console_command(f'curator import "{source}"')
    window._run_console_command("curator layer 1 offset -3")
    window._run_console_command("curator layer 1 hide")
    window._run_console_command("curator view limits -30 10")
    window._run_console_command("curator view background #ffffff")
    window._run_console_command("curator text title Demo Graph")

    state = window._curator_widget.graph_state
    assert len(state.layers) == 1
    assert state.layers[0].vertical_offset_db == -3.0
    assert state.layers[0].visible is False
    assert (state.y_min, state.y_max) == (-30.0, 10.0)
    assert state.background == "#ffffff"
    assert state.export_text.title == "Demo Graph"
    assert any(event.source == "curator" for event in window._console_events.events())
    assert "curator help" in window._console_help()
    before = list(state.layers)
    window._run_console_command("curator layer 99 hide")
    assert state.layers == before
    assert any(
        event.source == "curator" and event.severity == "ERROR"
        for event in window._console_events.events()
    )


def test_curator_console_full_command_surface(make_main_window, tmp_path: Path) -> None:
    window = _window(make_main_window)
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("100 0 1 2 3 4\n1000 1 2 3 4 5\n", encoding="utf-8")
    second.write_text("100 5 6 7 8 9\n1000 6 7 8 9 10\n", encoding="utf-8")

    window._run_console_command(f'curator import "{first}" "{second}"')
    window._run_console_command("curator layer 1 color #ff0000")
    window._run_console_command("curator layer 1 hrtf none")
    window._run_console_command("curator combine 1 2")
    window._run_console_command("curator bounds on")
    window._run_console_command("curator view aspect off")
    window._run_console_command("curator text fixture Console Fixture")
    window._run_console_command("curator text footer Console Footer")
    output = tmp_path / "console export.png"
    window._run_console_command(f'curator export "{output}"')

    state = window._curator_widget.graph_state
    assert len(state.layers) == 3
    assert state.layers[0].color == "#ff0000"
    assert state.layers[2].is_combined
    assert state.bounds.enabled is True
    assert state.aspect_locked_25db is False
    assert state.export_text.fixture == "Console Fixture"
    assert state.export_text.hrtf_note == "Console Footer"
    assert output.exists()

    window._run_console_command("curator reset")
    window._run_console_command("curator clear")
    assert window._curator_widget.graph_state.layers == []
