from pathlib import Path

from dms.console import ConsoleEventStore, exception_diagnostics, runtime_diagnostics
from dms.ui.main_window import MainWindow


def test_console_event_store_is_bounded_and_redacts_secrets(tmp_path: Path) -> None:
    store = ConsoleEventStore(capacity=2)
    store.publish("INFO", "test", "one")
    store.publish("WARNING", "test", "two", {"password": "nope", "value": 3})
    store.publish("ERROR", "test", "three")

    events = store.events()
    assert [event.message for event in events] == ["two", "three"]
    assert events[0].details == {"password": "<redacted>", "value": 3}

    path = tmp_path / "console.log"
    store.export(path)
    text = path.read_text(encoding="utf-8")
    assert "two" in text
    assert "three" in text
    assert "nope" not in text


def test_console_setting_validation() -> None:
    assert MainWindow._parse_console_setting("queue_count", "7") == 7
    assert MainWindow._parse_console_setting("output_level", "-12.5") == -12.5
    assert MainWindow._parse_console_setting("bluetooth_mode", "on") is True
    assert MainWindow._parse_console_setting("latency", "HIGH") == "high"


def test_console_persistent_log_is_written_and_redacted(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "fastgraph-console.log"
    store = ConsoleEventStore(log_path=log_path)
    store.publish("ERROR", "upload", "failed", {"password": "private", "stage": "auth"})

    text = log_path.read_text(encoding="utf-8")
    assert store.session_id in text
    assert "stage='auth'" in text
    assert "private" not in text
    assert "<redacted>" in text


def test_runtime_and_exception_diagnostics_are_support_safe() -> None:
    runtime = runtime_diagnostics()
    assert runtime["python"]
    assert runtime["packages"]["paramiko"]

    try:
        raise ValueError("sample failure")
    except ValueError as exc:
        details = exception_diagnostics(exc)
    assert details["exception_type"].endswith("ValueError")
    assert any("sample failure" in item for item in details["exception_chain"])
    assert details["traceback"]


def test_console_help_contains_review_and_export_commands() -> None:
    help_text = MainWindow._console_help()
    assert "measure pass" in help_text
    assert "measure fail" in help_text
    assert "export average" in help_text
    assert "export variation" in help_text
    assert "export squiglink" in help_text
