import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

import dms.settings_manager as settings_module
from dms.automation import (
    AutomationCondition,
    AutomationDefinition,
    AutomationStep,
    default_automation_directory,
    load_automation,
    save_automation,
    scan_automation_directory,
)
from dms.session import SessionData
from dms.settings_manager import SettingsManager
from dms.theme import ThemeController
from dms.ui.automation_widget import AutomationWidget
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
    settings.set("automation_directory", str(tmp_path / "automations"))
    return MainWindow(
        SessionData(rig="Rig", brand="DMS", model="Demo"),
        settings,
        ThemeController(qapp, settings),
    )


def test_automation_round_trip_and_schema_validation(tmp_path: Path) -> None:
    automation = AutomationDefinition(
        name="Demo",
        trigger="queue_complete",
        variables={"answer": "yes"},
        steps=[
            AutomationStep(
                action="navigate",
                target="curator",
                condition=AutomationCondition(kind="variable_equals", left="answer", value="yes"),
                skip_risky_confirmation=True,
            )
        ],
    )
    path = tmp_path / "demo.fastgraph-automation.json"

    save_automation(path, automation)
    loaded = load_automation(path)

    assert loaded.name == "Demo"
    assert loaded.trigger == "queue_complete"
    assert loaded.steps[0].action == "navigate"
    assert loaded.steps[0].skip_risky_confirmation is True
    with pytest.raises(ValueError, match="Unsupported automation schema"):
        AutomationDefinition.from_dict({"schema_version": 999})


def test_scan_automation_directory_loads_json_files(tmp_path: Path) -> None:
    save_automation(tmp_path / "one.fastgraph-automation.json", AutomationDefinition(name="One"))
    (tmp_path / "ignore.json").write_text("{}", encoding="utf-8")

    loaded, skipped = scan_automation_directory(tmp_path)

    assert [automation.name for _path, automation in loaded] == ["One"]
    assert skipped == []


def test_default_automation_directory_uses_documents_or_home() -> None:
    path = default_automation_directory()

    assert path.name == "Fastgraph Automations"


def test_automation_tab_splitter_and_library(qapp, monkeypatch, tmp_path: Path) -> None:
    save_automation(
        tmp_path / "automations" / "demo.fastgraph-automation.json",
        AutomationDefinition(name="Library Demo"),
    )

    window = _window(qapp, monkeypatch, tmp_path)

    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "Measure", "R&&D", "Curator", "Automation", "Settings"
    ]
    assert isinstance(window._automation_widget, AutomationWidget)
    assert window._automation_widget.splitter.count() == 2
    assert window._automation_widget.guide_button.objectName() == "btn_danger"
    run_buttons = window._automation_widget.events.findChildren(type(window._automation_widget.guide_button), "btn_export")
    assert any(button.text() == "Run" for button in run_buttons)
    assert window._automation_widget.left_stack.currentWidget() is window._automation_widget.console
    window._automation_widget.guide_button.click()
    assert window._automation_widget.left_stack.currentWidget() is window._automation_widget.guide
    assert window._automation_widget.guide_button.text() == "Close Guide"
    window._automation_widget.guide.close_requested.emit()
    assert window._automation_widget.left_stack.currentWidget() is window._automation_widget.console
    assert window._automation_widget.events._library.count() == 1
    assert "Library Demo" in window._automation_widget.events._library.item(0).text()
    assert window._settings_widget._automation_dir.text() == str(tmp_path / "automations")
    window.close()


def test_manual_automation_switches_tabs_and_runs_console_command(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    automation = AutomationDefinition(
        name="Manual",
        steps=[
            AutomationStep(action="set_variable", target="target_tab", value="curator"),
            AutomationStep(action="navigate", target="{target_tab}"),
            AutomationStep(action="console_command", target="status"),
        ],
    )

    window._run_automation(automation)

    assert window._tabs.currentWidget() is window._curator_widget
    assert any("State:" in event.message for event in window._console_events.events())
    window.close()


def test_manual_automation_switches_input_device_and_channel(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    window._state = AppState.IDLE
    window._input_devices_by_index = {
        5: {"index": 5, "name": "Input A", "hostapi": 0, "max_input_channels": 2}
    }
    window._input_device_labels_by_index = {5: "Input A"}
    window._in_dev_combo.addItem("Input A", 5)
    window._ch_combo.addItem("Ch 1", 0)
    window._ch_combo.addItem("Ch 2", 1)
    automation = AutomationDefinition(
        name="Device",
        steps=[
            AutomationStep(action="switch_input_device", target="Input A"),
            AutomationStep(action="switch_input_channel", target="2"),
        ],
    )

    window._run_automation(automation)

    assert window._current_input_device() == 5
    assert window._current_input_channel() == 1
    window.close()


def test_automation_unavailable_channel_logs_failure(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)
    window._state = AppState.IDLE
    monkeypatch.setattr("dms.ui.main_window.QMessageBox.warning", lambda *args, **kwargs: None)
    automation = AutomationDefinition(
        name="Bad Channel",
        steps=[AutomationStep(action="switch_input_channel", target="9")],
    )

    window._run_automation(automation)

    assert any("Automation failed" in event.message for event in window._console_events.events())
    window.close()
