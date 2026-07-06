from __future__ import annotations

from typing import Any


SHORTCUT_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("start_measurement", "Start Measurement", "Enter"),
    ("fail_review", "Fail / Redo Review", "F"),
    ("tab_measure", "Tab: Measure", "Shift+1"),
    ("tab_rnd", "Tab: R&D", "Shift+2"),
    ("tab_curator", "Tab: Curator", "Shift+3"),
    ("tab_automation", "Tab: Automation", "Shift+4"),
    ("tab_settings", "Tab: Settings", "Shift+5"),
)

DEFAULT_SHORTCUT_BINDINGS: dict[str, str] = {
    action: default for action, _label, default in SHORTCUT_ACTIONS
}


def shortcut_bindings_from_settings(value: Any) -> dict[str, str]:
    bindings = dict(DEFAULT_SHORTCUT_BINDINGS)
    if isinstance(value, dict):
        for action, sequence in value.items():
            if action in bindings and isinstance(sequence, str):
                bindings[action] = sequence.strip()
    return bindings
