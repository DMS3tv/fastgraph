from PyQt6.QtGui import QKeySequence

from dms.settings_manager import SettingsManager
from dms.shortcuts import DEFAULT_SHORTCUT_BINDINGS
from dms.ui.settings_dialog import SettingsWidget


def test_shortcut_settings_editor_saves_and_resets(qapp) -> None:
    settings = SettingsManager()
    widget = SettingsWidget(settings)

    assert widget._shortcut_edits["start_measurement"].keySequence().toString() == "Enter"
    widget._shortcut_edits["start_measurement"].setKeySequence(QKeySequence("Ctrl+M"))
    widget._save_shortcut("start_measurement", "Ctrl+M")

    assert settings.get("shortcut_bindings")["start_measurement"] == "Ctrl+M"

    widget._reset_shortcuts()

    assert settings.get("shortcut_bindings") == DEFAULT_SHORTCUT_BINDINGS
    widget.close()
    widget.deleteLater()


def test_keyboard_shortcuts_dispatch_to_measurement_tabs(make_main_window) -> None:
    window = make_main_window()
    calls: list[str] = []
    window._shortcut_focus_is_editing = lambda: False
    window.measure.start_queue = lambda: calls.append("measure")
    window.rnd.start_measurement = lambda: calls.append("rnd")

    window._tabs.setCurrentIndex(0)
    window._handle_keyboard_shortcut("start_measurement")
    window._tabs.setCurrentIndex(1)
    window._handle_keyboard_shortcut("start_measurement")
    window._handle_keyboard_shortcut("tab_settings")

    assert calls == ["measure", "rnd"]
    assert window._tabs.currentIndex() == 4


def test_keyboard_shortcuts_ignore_text_editing_focus(make_main_window) -> None:
    window = make_main_window()
    calls: list[str] = []
    window.measure.start_queue = lambda: calls.append("measure")
    window._shortcut_focus_is_editing = lambda: True

    window._tabs.setCurrentIndex(0)
    window._handle_keyboard_shortcut("start_measurement")

    assert calls == []
