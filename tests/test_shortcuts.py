import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication

import dms.settings_manager as settings_module
from dms.session import SessionData
from dms.settings_manager import SettingsManager
from dms.shortcuts import DEFAULT_SHORTCUT_BINDINGS
from dms.theme import ThemeController
from dms.ui.main_window import MainWindow
from dms.ui.settings_dialog import SettingsWidget


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _settings(monkeypatch, tmp_path: Path) -> SettingsManager:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path / "config")
    return SettingsManager()


def _window(qapp, monkeypatch, tmp_path: Path) -> MainWindow:
    settings = _settings(monkeypatch, tmp_path)
    monkeypatch.setattr(MainWindow, "_refresh_devices", lambda self: None)
    monkeypatch.setattr(MainWindow, "_start_level_monitor", lambda self: None)
    monkeypatch.setattr(MainWindow, "_start_update_check", lambda self: None)
    return MainWindow(
        SessionData(rig="Rig", brand="DMS", model="Demo"),
        settings,
        ThemeController(qapp, settings),
    )


def test_shortcut_settings_editor_saves_and_resets(qapp, monkeypatch, tmp_path: Path) -> None:
    settings = _settings(monkeypatch, tmp_path)
    widget = SettingsWidget(settings)

    assert widget._shortcut_edits["start_measurement"].keySequence().toString() == "Enter"
    widget._shortcut_edits["start_measurement"].setKeySequence(QKeySequence("Ctrl+M"))
    widget._save_shortcut("start_measurement", "Ctrl+M")

    assert settings.get("shortcut_bindings")["start_measurement"] == "Ctrl+M"

    widget._reset_shortcuts()

    assert settings.get("shortcut_bindings") == DEFAULT_SHORTCUT_BINDINGS
    widget.close()


def test_keyboard_shortcuts_dispatch_to_measurement_tabs(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(window, "_shortcut_focus_is_editing", lambda: False)
    monkeypatch.setattr(window, "_start_queue", lambda: calls.append("measure"))
    monkeypatch.setattr(window, "_start_rnd_measurement", lambda: calls.append("rnd"))

    window._tabs.setCurrentIndex(0)
    window._handle_keyboard_shortcut("start_measurement")
    window._tabs.setCurrentIndex(1)
    window._handle_keyboard_shortcut("start_measurement")
    window._handle_keyboard_shortcut("tab_settings")

    assert calls == ["measure", "rnd"]
    assert window._tabs.currentIndex() == 4
    window.close()


def test_keyboard_shortcuts_ignore_text_editing_focus(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(window, "_start_queue", lambda: calls.append("measure"))
    monkeypatch.setattr(window, "_shortcut_focus_is_editing", lambda: True)

    window._tabs.setCurrentIndex(0)
    window._handle_keyboard_shortcut("start_measurement")

    assert calls == []
    window.close()
