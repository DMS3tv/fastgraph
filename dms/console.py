"""Structured, session-only event storage for the Fastgraph console."""

from __future__ import annotations

import logging
import platform
import sys
import traceback
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from importlib import import_module, metadata
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from PyQt6.QtCore import QObject, pyqtSignal

_SECRET_PARTS = ("password", "credential", "encrypted", "secret", "token")
_MAX_LOG_BYTES = 2 * 1024 * 1024


def _redact(value: Any, key: str = "") -> Any:
    if any(part in key.lower() for part in _SECRET_PARTS):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, StrEnum):
        # Keep reason codes printing as the plain strings they used to be.
        return str(value)
    return value


def runtime_diagnostics() -> dict[str, Any]:
    """Return support details that do not include credentials or user files."""
    packages: dict[str, str] = {}
    for name in ("numpy", "scipy", "sounddevice", "paramiko", "cryptography", "PyQt6"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            try:
                module = import_module(name)
                version = getattr(module, "__version__", None)
                if name == "PyQt6" and not version:
                    version = import_module("PyQt6.QtCore").PYQT_VERSION_STR
                packages[name] = str(version or "bundled")
            except (ImportError, AttributeError):
                packages[name] = "not installed"
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "frozen_app": bool(getattr(sys, "frozen", False)),
        "packages": packages,
    }


def exception_diagnostics(exc: BaseException) -> dict[str, Any]:
    """Return a compact exception chain and traceback without local variables."""
    chain: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(f"{type(current).__module__}.{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    frames = traceback.extract_tb(exc.__traceback__)
    return {
        "exception_type": f"{type(exc).__module__}.{type(exc).__name__}",
        "exception_chain": chain,
        "traceback": [
            f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}" for frame in frames[-8:]
        ],
    }


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
        rendered = ", ".join(f"{key}={value!r}" for key, value in sorted(self.details.items()))
        return f"{prefix} | {rendered}"


class ConsoleEventStore(QObject):
    event_added = pyqtSignal(object)
    cleared = pyqtSignal()

    def __init__(
        self,
        capacity: int = 5000,
        parent: QObject | None = None,
        log_path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._events: deque[ConsoleEvent] = deque(maxlen=max(1, capacity))
        self._lock = RLock()
        self.session_id = uuid4().hex[:12]
        self.log_path = log_path

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
            self._append_persistent(event)
        self.event_added.emit(event)
        return event

    def _append_persistent(self, event: ConsoleEvent) -> None:
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists() and self.log_path.stat().st_size >= _MAX_LOG_BYTES:
                rotated = self.log_path.with_suffix(self.log_path.suffix + ".1")
                self.log_path.replace(rotated)
            is_new = not self.log_path.exists()
            with self.log_path.open("a", encoding="utf-8") as stream:
                if is_new:
                    stream.write(f"# FastGraph diagnostic log | session={self.session_id}\n")
                stream.write(event.format() + "\n")
        except OSError:
            # Diagnostics must never interrupt measurement or export work.
            return

    def capacity(self) -> int:
        """Maximum retained events; the console view trims its document to it."""
        return int(self._events.maxlen or 0)

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


class ConsoleLogHandler(logging.Handler):
    """Publish ``dms`` log records to a :class:`ConsoleEventStore`.

    ``extra={"source": ..., "details": {...}}`` sets the event's source and
    details; without them the source is the last logger-name component.
    ``publish`` is lock-guarded and ``event_added`` is queued to the receiver's
    thread, so records logged from worker threads are safe.
    """

    def __init__(self, store: ConsoleEventStore) -> None:
        super().__init__(logging.DEBUG)
        self._store = store

    def emit(self, record: logging.LogRecord) -> None:
        details = dict(getattr(record, "details", None) or {})
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            details.setdefault("error", f"{type(exc).__name__}: {exc}")
        self._store.publish(
            record.levelname,
            getattr(record, "source", record.name.rsplit(".", 1)[-1]),
            record.getMessage(),
            details,
        )


def install_console_handler(store: ConsoleEventStore) -> ConsoleLogHandler:
    """Route every ``dms.*`` logger into ``store`` until the store is destroyed."""
    logger = logging.getLogger("dms")
    logger.setLevel(logging.DEBUG)
    handler = ConsoleLogHandler(store)
    logger.addHandler(handler)
    store.destroyed.connect(lambda: logger.removeHandler(handler))
    return handler
