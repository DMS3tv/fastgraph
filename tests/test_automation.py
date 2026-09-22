import os
from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QMessageBox

import dms.file_io as file_io
import dms.ui.main_window as main_window_module
from dms.automation import (
    AutomationCondition,
    AutomationDefinition,
    AutomationStep,
    default_automation_directory,
    load_automation,
    save_automation,
    scan_automation_directory,
)
from dms.measure_queue import QueueState
from dms.ui.automation_widget import AutomationWidget


def _automation_window(make_main_window, tmp_path: Path):
    return make_main_window(settings={"automation_directory": str(tmp_path / "automations")})


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

    loaded = scan_automation_directory(tmp_path)

    assert [automation.name for _path, automation in loaded] == ["One"]


def test_default_automation_directory_uses_documents_or_home() -> None:
    path = default_automation_directory()

    assert path.name == "Fastgraph Automations"


def test_automation_tab_splitter_and_library(make_main_window, tmp_path: Path) -> None:
    save_automation(
        tmp_path / "automations" / "demo.fastgraph-automation.json",
        AutomationDefinition(name="Library Demo"),
    )

    window = _automation_window(make_main_window, tmp_path)

    assert [window._tabs.tabText(i) for i in range(window._tabs.count())] == [
        "Measure",
        "R&&D",
        "Curator",
        "Automation",
        "Settings",
    ]
    assert isinstance(window._automation_widget, AutomationWidget)
    assert window._automation_widget.splitter.count() == 2
    assert window._automation_widget.guide_button.objectName() == "btn_danger"
    run_buttons = window._automation_widget.events.findChildren(
        type(window._automation_widget.guide_button), "btn_export"
    )
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


def test_manual_automation_switches_tabs_and_runs_console_command(
    make_main_window, tmp_path: Path
) -> None:
    window = _automation_window(make_main_window, tmp_path)
    automation = AutomationDefinition(
        name="Manual",
        steps=[
            AutomationStep(action="set_variable", target="target_tab", value="curator"),
            AutomationStep(action="navigate", target="{target_tab}"),
            AutomationStep(action="console_command", target="status"),
        ],
    )

    window.commands.run_automation(automation)

    assert window._tabs.currentWidget() is window._curator_widget
    assert any("State:" in event.message for event in window._console_events.events())


def test_manual_automation_switches_input_device_and_channel(
    make_main_window, tmp_path: Path
) -> None:
    window = _automation_window(make_main_window, tmp_path)
    window._state = QueueState.IDLE
    window.devices._input_devices_by_index = {
        5: {"index": 5, "name": "Input A", "hostapi": 0, "max_input_channels": 2}
    }
    window.devices._input_device_labels_by_index = {5: "Input A"}
    window.measure_tab.in_dev_combo.addItem("Input A", 5)
    window.measure_tab.ch_combo.addItem("Ch 1", 0)
    window.measure_tab.ch_combo.addItem("Ch 2", 1)
    automation = AutomationDefinition(
        name="Device",
        steps=[
            AutomationStep(action="switch_input_device", target="Input A"),
            AutomationStep(action="switch_input_channel", target="2"),
        ],
    )

    window.commands.run_automation(automation)

    assert window.devices.current_input_device() == 5
    assert window.devices.current_input_channel() == 1


def test_automation_unavailable_channel_logs_failure(
    make_main_window, monkeypatch, tmp_path: Path
) -> None:
    window = _automation_window(make_main_window, tmp_path)
    window._state = QueueState.IDLE
    monkeypatch.setattr("dms.ui.main_window.QMessageBox.warning", lambda *args, **kwargs: None)
    automation = AutomationDefinition(
        name="Bad Channel",
        steps=[AutomationStep(action="switch_input_channel", target="9")],
    )

    window.commands.run_automation(automation)

    assert any("Automation failed" in event.message for event in window._console_events.events())


def _completed_names(window) -> list[str]:
    return [
        event.details.get("name")
        for event in window._console_events.events()
        if event.message == "Automation complete"
    ]


def test_console_command_steps_are_treated_as_risky(make_main_window, tmp_path: Path) -> None:
    """C4: the console can start a queue or export, so those ask first."""
    risky = [
        AutomationStep(action="console_command", target="measure start 20"),
        AutomationStep(action="console_command", target="EXPORT average"),
        AutomationStep(action="console_command", target="settings set queue_count 5"),
        AutomationStep(action="console_command", target="curator export png"),
        AutomationStep(action="console_command", target="rnd save"),
        AutomationStep(action="console_command", value="measure start"),
    ]
    safe = [
        AutomationStep(action="console_command", target="status"),
        AutomationStep(action="console_command", target="devices"),
        AutomationStep(action="console_command", target="settings show"),
    ]

    window = _automation_window(make_main_window, tmp_path)
    assert all(window.commands._automation_step_is_risky(step) for step in risky)
    assert not any(window.commands._automation_step_is_risky(step) for step in safe)

    asked: list[str] = []
    automation = AutomationDefinition(
        name="Console",
        steps=[AutomationStep(action="console_command", target="measure start 1")],
    )

    def refuse(_parent, _title, text, *args, **kwargs):
        asked.append(text)
        return QMessageBox.StandardButton.No

    with (
        patch.object(main_window_module.QMessageBox, "question", refuse),
        patch.object(main_window_module.QMessageBox, "warning", lambda *a, **k: None),
    ):
        window.commands.run_automation(automation)

    assert asked and "console_command" in asked[0]
    assert any("Automation failed" in event.message for event in window._console_events.events())


def test_two_automations_on_one_trigger_both_run(make_main_window, tmp_path: Path) -> None:
    """C12: the second automation for a trigger is queued, not dropped."""
    directory = tmp_path / "automations"
    for name in ("First", "Second"):
        save_automation(
            directory / f"{name.lower()}.fastgraph-automation.json",
            AutomationDefinition(
                name=name,
                trigger="queue_complete",
                steps=[AutomationStep(action="set_variable", target="ran", value=name)],
            ),
        )
    window = _automation_window(make_main_window, tmp_path)
    window._automation_widget.events.reload_library()

    window.commands.trigger("queue_complete")
    assert _completed_names(window) == ["First", "Second"]

    # The same trigger raised while a step is still running: both automations
    # are queued and run afterwards instead of being dropped with a warning.
    window._console_events.clear()
    window.commands.running = True
    window.commands.trigger("queue_complete")
    assert len(window.commands._automation_pending()) == 2
    assert _completed_names(window) == []

    window.commands.running = False
    window.commands._drain_automation_queue()
    assert _completed_names(window) == ["First", "Second"]
    assert window.commands._automation_pending() == []


def test_export_complete_trigger_keeps_its_re_entrancy_guard(
    make_main_window, tmp_path: Path
) -> None:
    """C12: a trigger an automation raises itself must not queue itself."""
    directory = tmp_path / "automations"
    save_automation(
        directory / "loop.fastgraph-automation.json",
        AutomationDefinition(
            name="Loop",
            trigger="export_complete",
            steps=[AutomationStep(action="set_variable", target="ran", value="1")],
        ),
    )
    window = _automation_window(make_main_window, tmp_path)
    window._automation_widget.events.reload_library()
    window.commands.running = True

    window.commands.trigger("export_complete")

    assert window.commands._automation_pending() == []
    window.commands.running = False


def test_variable_substitution_is_single_pass(make_main_window, tmp_path: Path) -> None:
    """C14: a value that contains a placeholder is not expanded again."""
    window = _automation_window(make_main_window, tmp_path)

    expanded = window.commands._expand_automation_text(
        "{first}/{second}/{missing}",
        {"first": "{second}", "second": "kept"},
    )

    assert expanded == "{second}/kept/{missing}"


def test_increment_variable_keeps_integers_integral(make_main_window, tmp_path: Path) -> None:
    """C14: a counter that started as an int stays an int."""
    window = _automation_window(make_main_window, tmp_path)
    variables: dict[str, object] = {"count": 1, "ratio": 1.5}

    window.commands._execute_automation_step(
        AutomationStep(action="increment_variable", target="count"), variables
    )
    window.commands._execute_automation_step(
        AutomationStep(action="increment_variable", target="fresh", value="2"), variables
    )
    window.commands._execute_automation_step(
        AutomationStep(action="decrement_variable", target="count"), variables
    )
    window.commands._execute_automation_step(
        AutomationStep(action="increment_variable", target="ratio"), variables
    )

    assert variables["count"] == 1 and isinstance(variables["count"], int)
    assert variables["fresh"] == 2 and isinstance(variables["fresh"], int)
    assert variables["ratio"] == pytest.approx(2.5)


def test_automation_files_are_written_atomically(monkeypatch, tmp_path: Path) -> None:
    """C13: a crash mid-save must leave the previous file readable."""
    path = tmp_path / "demo.fastgraph-automation.json"
    save_automation(path, AutomationDefinition(name="Original"))
    real_replace = os.replace

    def fail_replace(source, destination):
        if Path(destination) == path:
            raise OSError("simulated interruption")
        return real_replace(source, destination)

    monkeypatch.setattr(file_io.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        save_automation(path, AutomationDefinition(name="Replacement"))

    assert load_automation(path).name == "Original"
    assert [item.name for item in tmp_path.iterdir()] == [path.name]
