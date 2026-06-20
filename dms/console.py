"""Structured, session-only event storage for the Fastgraph console."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from PyQt6.QtCore import QObject, pyqtSignal


_SECRET_PARTS = ("password", "credential", "encrypted", "secret", "token")


def _redact(value: Any, key: str = "") -> Any:
    if any(part in key.lower() for part in _SECRET_PARTS):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


@dataclass(frozen=True)
class ConsoleEvent:
    timestamp: datetime
    severity: str
    source: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def format(self) -> str:
        prefix = (
            f"[{self.timestamp.strftime('%H:%M:%S.%f')[:-3]}] "
            f"{self.severity.upper():<7} {self.source}: {self.message}"
        )
        if not self.details:
            return prefix
        rendered = ", ".join(
            f"{key}={value!r}" for key, value in sorted(self.details.items())
        )
        return f"{prefix} | {rendered}"


class ConsoleEventStore(QObject):
    event_added = pyqtSignal(object)
    cleared = pyqtSignal()

    def __init__(self, capacity: int = 5000, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._events: deque[ConsoleEvent] = deque(maxlen=max(1, capacity))
        self._lock = RLock()

    def publish(
        self,
        severity: str,
        source: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> ConsoleEvent:
        event = ConsoleEvent(
            timestamp=datetime.now(),
            severity=str(severity).upper(),
            source=str(source),
            message=str(message),
            details=_redact(details or {}),
        )
        with self._lock:
            self._events.append(event)
        self.event_added.emit(event)
        return event

    def events(self) -> list[ConsoleEvent]:
        with self._lock:
            return list(self._events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
        self.cleared.emit()

    def formatted(self, events: Iterable[ConsoleEvent] | None = None) -> str:
        selected = self.events() if events is None else list(events)
        return "\n".join(event.format() for event in selected)

    def export(self, path: Path, events: Iterable[ConsoleEvent] | None = None) -> None:
        text = self.formatted(events)
        path.write_text(text + ("\n" if text else ""), encoding="utf-8")

