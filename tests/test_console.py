import logging
import threading
from pathlib import Path

from dms.console import ConsoleEventStore, exception_diagnostics, runtime_diagnostics
from dms.ui.console_widget import ConsoleWidget


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


def test_log_records_become_console_events(console_events) -> None:
    logger = logging.getLogger("dms.ui.measure_io")
    logger.warning(
        "Average export failed", extra={"source": "export", "details": {"password": "x", "n": 2}}
    )
    logger.debug("No extras")
    worker = threading.Thread(target=lambda: logger.error("From a thread"))
    worker.start()
    worker.join()

    first, second, third = console_events.events()
    assert (first.severity, first.source, first.message, first.details) == (
        "WARNING",
        "export",
        "Average export failed",
        {"password": "<redacted>", "n": 2},
    )
    assert (second.severity, second.source, second.message, second.details) == (
        "DEBUG",
        "measure_io",
        "No extras",
        {},
    )
    assert (third.severity, third.message) == ("ERROR", "From a thread")


def test_console_setting_validation(make_main_window) -> None:
    window = make_main_window()
    assert window.commands._parse_console_setting("queue_count", "7") == 7
    assert window.commands._parse_console_setting("output_level", "-12.5") == -12.5
    assert window.commands._parse_console_setting("bluetooth_mode", "on") is True
    assert window.commands._parse_console_setting("latency", "HIGH") == "high"


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


def test_console_help_contains_review_and_export_commands(make_main_window) -> None:
    help_text = make_main_window().commands._console_help()
    assert "measure pass" in help_text
    assert "measure fail" in help_text
    assert "export average" in help_text
    assert "export variation" in help_text
    assert "export squiglink" in help_text


# --- incremental rendering -------------------------------------------------


def _widget(qapp, capacity: int = 5000):
    """A ConsoleWidget plus a counter of full document rebuilds.

    Only ``_refresh`` re-serializes the whole store, so counting calls to
    ``ConsoleEventStore.formatted`` counts rebuilds.
    """
    store = ConsoleEventStore(capacity=capacity)
    rebuilds = {"count": 0}
    original = store.formatted

    def _counting_formatted(events=None) -> str:
        rebuilds["count"] += 1
        return original(events)

    store.formatted = _counting_formatted
    widget = ConsoleWidget(store)
    return store, widget, rebuilds


def _lines(widget) -> list[str]:
    text = widget._output.toPlainText()
    return text.split("\n") if text else []


def test_new_events_append_without_rebuilding_the_document(qapp) -> None:
    store, widget, rebuilds = _widget(qapp)
    try:
        store.publish("INFO", "sweep", "first")
        # The first event introduces a new source, which does force one rebuild.
        rebuilds["count"] = 0
        for index in range(20):
            store.publish("INFO", "sweep", f"event {index}")
        assert rebuilds["count"] == 0, "the whole document was re-rendered"
        lines = _lines(widget)
        assert len(lines) == 21
        assert "event 19" in lines[-1]
        assert "first" in lines[0]
    finally:
        widget.deleteLater()


def test_events_that_fail_the_filter_are_not_appended(qapp) -> None:
    store, widget, rebuilds = _widget(qapp)
    try:
        store.publish("INFO", "sweep", "visible")
        store.publish("ERROR", "sweep", "also visible")
        widget._level.setCurrentText("ERROR")
        rebuilds["count"] = 0
        assert len(_lines(widget)) == 1

        store.publish("INFO", "sweep", "filtered out")
        assert len(_lines(widget)) == 1
        store.publish("ERROR", "sweep", "kept")
        assert len(_lines(widget)) == 2
        assert "kept" in _lines(widget)[-1]
        assert rebuilds["count"] == 0
    finally:
        widget.deleteLater()


def test_search_change_rebuilds_the_document(qapp) -> None:
    store, widget, rebuilds = _widget(qapp)
    try:
        store.publish("INFO", "sweep", "alpha")
        store.publish("INFO", "sweep", "beta")
        rebuilds["count"] = 0
        widget._search.setText("alpha")
        assert rebuilds["count"] >= 1
        assert len(_lines(widget)) == 1
        assert "alpha" in _lines(widget)[0]
    finally:
        widget.deleteLater()


def test_a_new_source_rebuilds_so_the_filter_combo_stays_current(qapp) -> None:
    store, widget, rebuilds = _widget(qapp)
    try:
        store.publish("INFO", "sweep", "one")
        rebuilds["count"] = 0
        store.publish("INFO", "upload", "two")
        assert rebuilds["count"] == 1
        assert widget._source.findText("upload") >= 0
        assert len(_lines(widget)) == 2
    finally:
        widget.deleteLater()


def test_the_document_is_trimmed_when_the_store_trims(qapp) -> None:
    store, widget, _rebuilds = _widget(qapp, capacity=5)
    try:
        for index in range(12):
            store.publish("INFO", "sweep", f"event {index}")
        lines = _lines(widget)
        assert len(lines) == 5 == len(store.events())
        assert "event 7" in lines[0]
        assert "event 11" in lines[-1]
    finally:
        widget.deleteLater()


def test_clear_empties_the_view(qapp) -> None:
    store, widget, _rebuilds = _widget(qapp)
    try:
        store.publish("INFO", "sweep", "one")
        store.clear()
        assert _lines(widget) == []
        store.publish("INFO", "sweep", "after clear")
        assert len(_lines(widget)) == 1
    finally:
        widget.deleteLater()
