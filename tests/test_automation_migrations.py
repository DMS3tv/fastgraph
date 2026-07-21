"""Schema-migration tests for dms.automation.

Deliberately avoids importing Qt/sounddevice-backed modules (unlike
tests/test_automation.py) so it stays runnable in headless/CI environments
that lack those runtime dependencies.
"""

from pathlib import Path

import pytest

from dms.automation import (
    AutomationDefinition,
    save_automation,
    scan_automation_directory,
)


def test_automation_definition_current_schema_loads() -> None:
    automation = AutomationDefinition(name="Existing")

    loaded = AutomationDefinition.from_dict(automation.to_dict())

    assert loaded.schema_version == 1
    assert loaded.name == "Existing"


def test_automation_definition_no_upgrade_path_for_old_schema() -> None:
    with pytest.raises(ValueError, match="no upgrade path from version 0"):
        AutomationDefinition.from_dict({"schema_version": 0})


def test_automation_definition_newer_schema_message_is_distinct() -> None:
    with pytest.raises(ValueError, match="newer version of Fastgraph"):
        AutomationDefinition.from_dict({"schema_version": 99})


def test_scan_automation_directory_surfaces_migration_error_in_skipped_list(
    tmp_path: Path,
) -> None:
    save_automation(tmp_path / "good.fastgraph-automation.json", AutomationDefinition(name="Good"))
    (tmp_path / "too-old.fastgraph-automation.json").write_text(
        '{"schema_version": 0, "name": "Too Old"}', encoding="utf-8"
    )
    (tmp_path / "too-new.fastgraph-automation.json").write_text(
        '{"schema_version": 99, "name": "Too New"}', encoding="utf-8"
    )

    loaded, skipped = scan_automation_directory(tmp_path)

    assert [automation.name for _path, automation in loaded] == ["Good"]
    reasons = {path.name: reason for path, reason in skipped}
    assert "no upgrade path from version 0" in reasons["too-old.fastgraph-automation.json"]
    assert "newer version of Fastgraph" in reasons["too-new.fastgraph-automation.json"]
