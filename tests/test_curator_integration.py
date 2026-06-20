import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

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
        "Measure", "Curator", "Console"
    ]
    assert window._queue_level_persist_toggle.minimumSizeHint().width() >= 54
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
