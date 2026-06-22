import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtTest import QTest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QGroupBox, QToolButton

import dms.settings_manager as settings_module
from dms.curator.transforms import apply_layer_transform
from dms.hrtf import HRTFCurve
from dms.session import SessionData
from dms.settings_manager import SettingsManager
from dms.theme import ThemeController
from dms.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(qapp, monkeypatch, tmp_path: Path) -> MainWindow:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(MainWindow, "_refresh_devices", lambda self: None)
    monkeypatch.setattr(MainWindow, "_start_level_monitor", lambda self: None)
    monkeypatch.setattr(MainWindow, "_start_update_check", lambda self: None)
    settings = SettingsManager()
    return MainWindow(
        SessionData(rig="Test Rig", brand="DMS", model="Demo"),
        settings,
        ThemeController(qapp, settings),
    )


def test_curator_is_middle_tab(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "Measure", "Curator", "Console", "Settings"
    ]
    assert window._queue_level_persist_toggle.minimumSizeHint().width() >= 54
    window.close()


def test_measure_controls_are_embedded_around_plots(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)

    assert window._plots._between_plots_widget.objectName() == "measure_interplot_controls"
    assert window._plots._footer_widget.objectName() == "measure_export_controls"
    assert window._clear_btn.objectName() == "btn_danger"
    assert window._level_meter.parent() is window._plots._between_plots_widget
    assert window._export_dir_input.parent() is window._plots._footer_widget
    assert not window._clear_btn.isEnabled()
    window.close()


def test_session_and_bluetooth_controls_precede_tabs(
    qapp, monkeypatch, tmp_path: Path
) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
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
    assert window._session_summary_label.text() == "DMS Demo · Test Rig"
    assert window._metadata_btn.geometry().bottom() <= header.rect().bottom()
    assert window._clear_metadata_btn.geometry().bottom() <= header.rect().bottom()

    section_titles = [button.text() for button in window.findChildren(QToolButton)]
    group_titles = [group.title() for group in window.findChildren(QGroupBox)]
    assert "Session" not in section_titles
    assert "Measurement Mode" not in group_titles

    window._tabs.setCurrentWidget(window._console_widget)
    qapp.processEvents()
    assert header.isVisible()
    window.close()


def test_measure_plots_keep_frequency_endpoints_inside_view(
    qapp, monkeypatch, tmp_path: Path
) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    window.resize(1280, 800)
    window.show()
    qapp.processEvents()

    expected_min = np.log10(20.0)
    expected_max = np.log10(20000.0)
    for plot in (window._plots._top_plot, window._plots._bot_plot):
        x_min, x_max = plot.getPlotItem().getViewBox().viewRange()[0]
        assert x_min < expected_min
        assert x_max > expected_max
    window.close()


def test_settings_tab_saves_immediately_and_disables_edits_while_busy(
    qapp, monkeypatch, tmp_path: Path
) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
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
    window.close()


def test_settings_column_is_compact_and_left_aligned(
    qapp, monkeypatch, tmp_path: Path
) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    window.resize(1280, 800)
    window.show()
    window._tabs.setCurrentWidget(window._settings_scroll)
    qapp.processEvents()

    column = window._settings_widget._settings_column
    assert column.x() == 0
    assert column.width() == 560
    assert column.geometry().right() < window._settings_widget.width()
    window.close()


def test_header_summary_refreshes_when_metadata_is_cleared(
    qapp, monkeypatch, tmp_path: Path
) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    monkeypatch.setattr(window, "_confirm_clear_metadata", lambda: (True, False))
    window._clear_metadata()

    assert window._session_summary_label.text() == "Unknown Unknown · Unknown Rig"
    assert "Headphone: Unknown Unknown" in window._session_summary_label.toolTip()
    assert "Unknown Unknown @ Unknown Rig" in window.windowTitle()
    window.close()


def test_metadata_clear_confirmation_and_preference(
    qapp, monkeypatch, tmp_path: Path
) -> None:
    window = _window(qapp, monkeypatch, tmp_path)

    monkeypatch.setattr(window, "_confirm_clear_metadata", lambda: (False, True))
    window._clear_metadata()
    assert window._session.display_name() == "DMS Demo"
    assert window._settings.get("confirm_clear_metadata") is True

    monkeypatch.setattr(window, "_confirm_clear_metadata", lambda: (True, True))
    window._clear_metadata()
    assert window._session.display_name() == "Unknown Unknown"
    assert window._settings.get("confirm_clear_metadata") is False
    assert not window._settings_widget._confirm_clear_metadata.isChecked()

    window._settings_widget._confirm_clear_metadata.setChecked(True)
    assert window._settings.get("confirm_clear_metadata") is True
    window.close()


def test_clear_confirmation_preference_and_tab_isolation(
    qapp, monkeypatch, tmp_path: Path
) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    curve = (np.array([100.0, 1000.0]), np.array([1.0, 0.0]))
    window._kept_curves = [curve]
    window._recompute_average()
    window._update_plots()
    curator_marker = object()
    window._curator_widget.graph_state.layers.append(curator_marker)
    window._console_events.publish("INFO", "test", "keep me")
    event_count = len(window._console_events.events())

    monkeypatch.setattr(window, "_confirm_clear_all", lambda: (False, True))
    window._clear_all()
    assert len(window._kept_curves) == 1
    assert window._kept_curves[0] is curve
    assert window._settings.get("confirm_clear_measurements") is True

    monkeypatch.setattr(window, "_confirm_clear_all", lambda: (True, True))
    window._clear_all()
    assert window._kept_curves == []
    assert window._settings.get("confirm_clear_measurements") is False
    assert window._curator_widget.graph_state.layers == [curator_marker]
    assert len(window._console_events.events()) >= event_count
    assert not window._clear_btn.isEnabled()

    window._settings_widget._confirm_clear.setChecked(True)
    assert window._settings.get("confirm_clear_measurements") is True
    window.close()


def test_send_average_offsets_display_and_preserves_editable_hrtf(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
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
    source_mag[:] = 99.0
    assert not np.allclose(layer.curve.mag_db, source_mag)
    window.close()


def test_send_variation_offsets_display_with_editable_hrtf(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
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
    QTest.qWait(250)
    assert window._curator_widget._graph.wipeProgress == 1.0
    assert len(window._curator_widget._graph._items) > 3
    window.close()


def test_send_variation_offsets_to_zero_without_changing_source_shape(
    qapp, monkeypatch, tmp_path: Path
) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
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
    assert state.y_min == -20.0
    assert state.y_max == 20.0
    assert np.isclose(np.interp(1000.0, displayed.freqs, displayed.median_db), 0.0)
    assert np.allclose(np.diff(layer.curve.median_db), [1.0, 1.0])
    assert np.allclose(np.diff(displayed.median_db), [1.0, 1.0])
    QTest.qWait(250)
    assert len(window._curator_widget._graph._items) > 3
    window.close()


def test_curator_console_commands_update_workspace_and_log(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
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
    window.close()


def test_curator_console_full_command_surface(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
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
    window.close()
