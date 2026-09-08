from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from dms.file_io import atomic_write_json


SCHEMA_VERSION = 1
AUTOMATION_SUFFIX = ".fastgraph-automation.json"

TRIGGERS = [
    "manual",
    "app_start",
    "measurement_kept",
    "queue_complete",
    "rnd_measurement_kept",
    "export_complete",
    "app_error",
]

ACTIONS = [
    "navigate",
    "switch_input_device",
    "switch_input_channel",
    "console_command",
    "prompt_info",
    "prompt_warning",
    "prompt_yes_no",
    "set_variable",
    "clear_variable",
    "increment_variable",
    "decrement_variable",
    "measure_start",
    "measure_pass",
    "measure_fail",
    "measure_cancel",
    "rnd_start",
    "rnd_save_session",
    "rnd_load_session",
    "rnd_export_selected",
    "rnd_send_to_curator",
    "curator_send_measure",
    "curator_command",
    "curator_export_png",
    "export_average",
    "export_variation",
    "export_log",
]

CONDITIONS = [
    "always",
    "variable_equals",
    "variable_not_equals",
    "variable_contains",
    "variable_true",
    "variable_false",
    "app_state",
    "kept_count_at_least",
    "rnd_count_at_least",
    "curator_layers_at_least",
]


@dataclass
class AutomationCondition:
    kind: str = "always"
    left: str = ""
    operator: str = "equals"
    value: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "left": self.left,
            "operator": self.operator,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AutomationCondition":
        if not data:
            return cls()
        kind = str(data.get("kind") or "always")
        if kind not in CONDITIONS:
            raise ValueError(f"Unsupported automation condition: {kind}")
        return cls(
            kind=kind,
            left=str(data.get("left") or ""),
            operator=str(data.get("operator") or "equals"),
            value=str(data.get("value") or ""),
        )


@dataclass
class AutomationStep:
    action: str = "navigate"
    target: str = ""
    value: str = ""
    condition: AutomationCondition = field(default_factory=AutomationCondition)
    confirm_risky: bool = True
    skip_risky_confirmation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target": self.target,
            "value": self.value,
            "condition": self.condition.to_dict(),
            "confirm_risky": bool(self.confirm_risky),
            "skip_risky_confirmation": bool(self.skip_risky_confirmation),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutomationStep":
        action = str(data.get("action") or "navigate")
        if action not in ACTIONS:
            raise ValueError(f"Unsupported automation action: {action}")
        return cls(
            action=action,
            target=str(data.get("target") or ""),
            value=str(data.get("value") or ""),
            condition=AutomationCondition.from_dict(data.get("condition")),
            confirm_risky=bool(data.get("confirm_risky", True)),
            skip_risky_confirmation=bool(data.get("skip_risky_confirmation", False)),
        )


@dataclass
class AutomationDefinition:
    name: str = "New Automation"
    id: str = field(default_factory=lambda: uuid4().hex)
    description: str = ""
    enabled: bool = True
    trigger: str = "manual"
    variables: dict[str, Any] = field(default_factory=dict)
    steps: list[AutomationStep] = field(default_factory=list)
    created_app_version: str = ""
    updated_app_version: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "enabled": bool(self.enabled),
            "trigger": self.trigger,
            "variables": dict(self.variables),
            "steps": [step.to_dict() for step in self.steps],
            "created_app_version": self.created_app_version,
            "updated_app_version": self.updated_app_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutomationDefinition":
        version = int(data.get("schema_version") or 0)
        if version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported automation schema version: {version}")
        trigger = str(data.get("trigger") or "manual")
        if trigger not in TRIGGERS:
            raise ValueError(f"Unsupported automation trigger: {trigger}")
        return cls(
            schema_version=version,
            id=str(data.get("id") or uuid4().hex),
            name=str(data.get("name") or "New Automation"),
            description=str(data.get("description") or ""),
            enabled=bool(data.get("enabled", True)),
            trigger=trigger,
            variables=dict(data.get("variables") or {}),
            steps=[AutomationStep.from_dict(item) for item in data.get("steps") or []],
            created_app_version=str(data.get("created_app_version") or ""),
            updated_app_version=str(data.get("updated_app_version") or ""),
            created_at=str(data.get("created_at") or datetime.now(timezone.utc).isoformat()),
            updated_at=str(data.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        )


def default_automation_directory() -> Path:
    documents = Path.home() / "Documents"
    base = documents if documents.exists() else Path.home()
    return base / "Fastgraph Automations"


def safe_automation_filename(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in " ._-()" else "_" for ch in name).strip()
    return (safe or "Automation") + AUTOMATION_SUFFIX


def load_automation(path: Path) -> AutomationDefinition:
    return AutomationDefinition.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_automation(path: Path, automation: AutomationDefinition) -> None:
    # Automations are user-authored work; a crash mid-save must not truncate
    # one. Mode is left at the process default because these files live in the
    # user's Documents folder and carry no secrets.
    atomic_write_json(path, automation.to_dict(), mode=None)


class AutomationScanResult(list):
    """The loaded ``(path, automation)`` pairs, with the skipped files attached.

    Subclassing ``list`` keeps every existing caller (which iterates the result
    or takes its length) working unchanged, while ``warnings`` exposes the
    files that could not be read instead of dropping them silently.
    """

    def __init__(
        self,
        items: list[tuple[Path, AutomationDefinition]] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        super().__init__(items or [])
        self.warnings: list[str] = list(warnings or [])


def scan_automation_directory(directory: Path) -> AutomationScanResult:
    """Load every automation in ``directory``.

    Unreadable or invalid files are skipped, but each one is described in
    ``result.warnings`` so the UI can say which file was ignored and why.
    """
    if not directory.exists():
        return AutomationScanResult()
    loaded: list[tuple[Path, AutomationDefinition]] = []
    warnings: list[str] = []
    for path in sorted(directory.glob(f"*{AUTOMATION_SUFFIX}")):
        try:
            loaded.append((path, load_automation(path)))
        except Exception as exc:
            warnings.append(f"{path.name}: {exc}")
    return AutomationScanResult(loaded, warnings)
