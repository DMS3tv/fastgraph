"""Tests for M18: ConsoleEventStore must redact secret *values* identified by
a sibling "name"/"key" entry (main_window logs settings changes as
{"name": <setting_key>, "value": <setting_value>}), not just secret-named
dict keys.

This module deliberately imports only dms.console (never dms.ui.main_window,
which drags in PyQt6.QtMultimedia and is unimportable in this environment)."""

from dms.console import ConsoleEventStore


def test_name_value_pair_with_secret_name_redacts_value() -> None:
    store = ConsoleEventStore()
    event = store.publish(
        "INFO",
        "settings",
        "Session setting changed",
        {"name": "squiglink_credentials_encrypted", "value": "xyz"},
    )
    assert event.details == {
        "name": "squiglink_credentials_encrypted",
        "value": "<redacted>",
    }


def test_name_value_pair_with_benign_name_untouched() -> None:
    store = ConsoleEventStore()
    event = store.publish(
        "INFO",
        "settings",
        "Session setting changed",
        {"name": "sweep_duration", "value": 2.0},
    )
    assert event.details == {"name": "sweep_duration", "value": 2.0}


def test_key_field_with_secret_name_also_redacts_value() -> None:
    store = ConsoleEventStore()
    event = store.publish(
        "INFO",
        "settings",
        "Session setting changed",
        {"key": "squiglink_password", "value": "hunter2"},
    )
    assert event.details == {"key": "squiglink_password", "value": "<redacted>"}


def test_existing_key_based_redaction_still_works() -> None:
    store = ConsoleEventStore(capacity=2)
    store.publish("INFO", "test", "one")
    store.publish("WARNING", "test", "two", {"password": "nope", "value": 3})
    store.publish("ERROR", "test", "three")

    events = store.events()
    assert [event.message for event in events] == ["two", "three"]
    assert events[0].details == {"password": "<redacted>", "value": 3}
