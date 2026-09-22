"""The Events editor's table must be a lossless view of an automation file."""

from pathlib import Path

from PyQt6.QtWidgets import QMessageBox

import dms.ui.automation_widget as automation_widget_module
from dms.automation import (
    AutomationCondition,
    AutomationDefinition,
    AutomationStep,
    load_automation,
    save_automation,
)
from dms.ui.automation_widget import EventsWidget


def _events_widget(qapp, tmp_path: Path) -> EventsWidget:
    directory = tmp_path / "automations"
    directory.mkdir(parents=True, exist_ok=True)
    return EventsWidget(lambda: str(directory), lambda: "9.9.9")


def _full_automation() -> AutomationDefinition:
    return AutomationDefinition(
        name="Round Trip",
        description="Every field filled in.",
        trigger="rnd_measurement_kept",
        variables={"answer": "yes"},
        steps=[
            AutomationStep(
                action="prompt_yes_no",
                target="do_export",
                value="Export the current average?",
                condition=AutomationCondition(
                    kind="variable_equals",
                    left="answer",
                    operator="not_equals",
                    value="no",
                ),
                confirm_risky=True,
                skip_risky_confirmation=False,
            ),
            AutomationStep(
                action="export_average",
                target="/tmp/out.txt",
                value="step value",
                condition=AutomationCondition(
                    kind="kept_count_at_least",
                    left="",
                    operator="at_least",
                    value="3",
                ),
                confirm_risky=False,
                skip_risky_confirmation=True,
            ),
        ],
    )


def test_editor_round_trips_every_step_field(qapp, tmp_path: Path) -> None:
    """C6: load then save must not rewrite operator, values or confirm flags."""
    automation = _full_automation()
    path = tmp_path / "automations" / "round-trip.fastgraph-automation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    save_automation(path, automation)
    widget = _events_widget(qapp, tmp_path)

    widget.load_file(path)
    saved_path = widget.save_current()
    reloaded = load_automation(saved_path)

    assert saved_path == path
    # Only the app-version stamps may change on a save.
    stamps = {"updated_app_version": "", "created_app_version": ""}
    assert reloaded.to_dict() | stamps == automation.to_dict() | stamps
    first, second = reloaded.steps
    assert first.condition.operator == "not_equals"
    assert first.condition.value == "no"
    assert first.value == "Export the current average?"
    assert first.confirm_risky is True
    assert second.condition.value == "3"
    assert second.value == "step value"
    assert second.confirm_risky is False
    assert second.skip_risky_confirmation is True
    widget.deleteLater()


def test_save_as_asks_before_replacing_another_automation(
    qapp,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Save As builds its filename from the name, so it can hit another file."""
    directory = tmp_path / "automations"
    directory.mkdir(parents=True, exist_ok=True)
    existing = directory / "Round Trip.fastgraph-automation.json"
    save_automation(existing, AutomationDefinition(name="Round Trip", description="original"))
    widget = _events_widget(qapp, tmp_path)
    widget._load_into_editor(_full_automation(), None)
    prompts: list[str] = []

    def decline(_parent, _title, text, *args, **kwargs):
        prompts.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(automation_widget_module.QMessageBox, "question", decline)
    assert widget.save_current(force_new=True) is None
    assert prompts and "Replace Round Trip.fastgraph-automation.json?" in prompts[0]
    assert load_automation(existing).description == "original"

    monkeypatch.setattr(
        automation_widget_module.QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    assert widget.save_current(force_new=True) == existing
    assert load_automation(existing).description == "Every field filled in."
    widget.deleteLater()


def test_duplicate_copies_the_automation_and_leaves_the_source_alone(
    qapp,
    tmp_path: Path,
) -> None:
    """Duplicate used to blank the open automation's id as a side effect."""
    widget = _events_widget(qapp, tmp_path)
    original = _full_automation()
    widget._load_into_editor(original, None)
    original_id = original.id

    widget.duplicate_automation()
    copy = widget.current_automation()

    assert original.id == original_id
    assert copy.id and copy.id != original_id
    assert copy.name == "Round Trip Copy"
    assert [step.to_dict() for step in copy.steps] == [
        step.to_dict() for step in original.steps
    ]
    widget.deleteLater()


def test_new_row_keeps_the_step_defaults(qapp, tmp_path: Path) -> None:
    widget = _events_widget(qapp, tmp_path)
    widget.new_automation()

    widget.add_step()
    step = widget.current_automation().steps[0]

    assert step.to_dict() == AutomationStep().to_dict()
    widget.deleteLater()
