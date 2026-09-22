"""Automatic crash recovery for the R&D workspace."""

from __future__ import annotations

import contextlib
import logging
import shutil
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from dms.rnd.models import UnsupportedSessionVersion
from dms.rnd.persistence import (
    copy_session_bundle,
    load_rnd_session,
    remove_session_bundle,
    save_rnd_snapshot,
)
from dms.rnd.photos import attachment_directory

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecoveryCandidate:
    path: Path
    kind: str
    preserved_at: datetime
    measurement_count: int
    group_count: int
    #: Set when the file is readable JSON that this Fastgraph cannot open, so
    #: the recovery dialog can explain it instead of the file being quarantined.
    error: str = ""

    @property
    def unsupported(self) -> bool:
        return bool(self.error)

    @property
    def label(self) -> str:
        kind = "Kept for later" if self.kind == "deferred" else "Crash recovery"
        stamp = self.preserved_at.astimezone().strftime("%Y-%m-%d %I:%M:%S %p")
        if self.error:
            return f"{kind} — {stamp} — {self.error}"
        return (
            f"{kind} — {stamp} — {self.measurement_count} measurements, {self.group_count} groups"
        )


class RnDRecoveryManager(QObject):
    """Debounce snapshots and persist the newest state in one worker."""

    save_succeeded = pyqtSignal()
    save_failed = pyqtSignal(str)
    _worker_finished = pyqtSignal(object)

    def __init__(
        self,
        root: Path,
        snapshot_provider: Callable[[], tuple[dict[str, Any], dict[str, Path]]],
        *,
        debounce_ms: int = 1500,
        maximum_ms: int = 15000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.root = Path(root)
        self.current_path = self.root / "current.fastgraph-rnd.json"
        self.previous_path = self.root / "previous.fastgraph-rnd.json"
        self.staging_path = self.root / "staging.fastgraph-rnd.json"
        #: True while the previous-generation copy is failing. The newest
        #: snapshot is still saved; only the second generation is missing.
        self.rotation_degraded = False
        self.deferred_root = self.root / "deferred"
        self.quarantine_root = self.root / "quarantine"
        self._snapshot_provider = snapshot_provider
        self._enabled = False
        self._pending = False
        self._closing = False
        self._future: Future | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rnd-recovery")

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(debounce_ms)
        self._debounce_timer.timeout.connect(self._dispatch)
        self._maximum_timer = QTimer(self)
        self._maximum_timer.setSingleShot(True)
        self._maximum_timer.setInterval(maximum_ms)
        self._maximum_timer.timeout.connect(self._dispatch)
        self._worker_finished.connect(self._on_worker_finished)

    def enable(self) -> None:
        self._enabled = True

    def schedule(self) -> None:
        if not self._enabled or self._closing:
            return
        self._pending = True
        self._debounce_timer.start()
        if not self._maximum_timer.isActive():
            self._maximum_timer.start()

    def clear_active(self) -> None:
        self._pending = False
        self._debounce_timer.stop()
        self._maximum_timer.stop()
        if self._future is None:
            remove_session_bundle(self.current_path)
            remove_session_bundle(self.previous_path)
            remove_session_bundle(self.staging_path)

    def candidates(self) -> list[RecoveryCandidate]:
        found: list[RecoveryCandidate] = []
        active = self._valid_candidate(self.current_path, "current")
        if active is not None:
            found.append(active)
        else:
            previous = self._valid_candidate(self.previous_path, "previous")
            if previous is not None:
                found.append(previous)
        if self.deferred_root.exists():
            for path in self.deferred_root.glob("*/session.fastgraph-rnd.json"):
                candidate = self._valid_candidate(path, "deferred")
                if candidate is not None:
                    found.append(candidate)
        return sorted(found, key=lambda item: item.preserved_at, reverse=True)

    def load_candidate(self, candidate: RecoveryCandidate, photo_store):
        return load_rnd_session(candidate.path, photo_store)

    def discard(self, candidate: RecoveryCandidate) -> None:
        remove_session_bundle(candidate.path)
        if candidate.kind in {"current", "previous"}:
            remove_session_bundle(self.current_path)
            remove_session_bundle(self.previous_path)
        self._remove_empty_parent(candidate.path.parent)

    def keep_for_later(self, candidate: RecoveryCandidate) -> RecoveryCandidate:
        if candidate.kind == "deferred":
            return candidate
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination = self.deferred_root / stamp / "session.fastgraph-rnd.json"
        destination.parent.mkdir(parents=True, exist_ok=False)
        copy_session_bundle(candidate.path, destination)
        self.discard(candidate)
        kept = self._valid_candidate(destination, "deferred")
        if kept is None:
            raise OSError("Could not validate the deferred R&D recovery session.")
        return kept

    def shutdown_clean(self) -> None:
        self._closing = True
        self._enabled = False
        self._pending = False
        self._debounce_timer.stop()
        self._maximum_timer.stop()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._future = None
        remove_session_bundle(self.current_path)
        remove_session_bundle(self.previous_path)
        remove_session_bundle(self.staging_path)

    def _dispatch(self) -> None:
        if not self._enabled or self._closing or not self._pending:
            return
        if self._future is not None:
            return
        self._pending = False
        self._debounce_timer.stop()
        self._maximum_timer.stop()
        try:
            snapshot, sources = self._snapshot_provider()
        except Exception as exc:
            self.save_failed.emit(str(exc))
            return
        if not snapshot.get("measurements") and not snapshot.get("groups"):
            self.clear_active()
            self.save_succeeded.emit()
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self._future = self._executor.submit(self._save_snapshot, snapshot, sources)
        self._future.add_done_callback(self._worker_finished.emit)

    def _save_snapshot(self, snapshot: dict[str, Any], sources: dict[str, Path]) -> None:
        """Write the newest snapshot first, then rotate the older generation.

        The new state is written to a staging bundle before anything else is
        touched, so a failure while copying current → previous can never cost
        the user the newest snapshot: the staging bundle still becomes the new
        current, and only the second generation is skipped.
        """
        remove_session_bundle(self.staging_path)
        save_rnd_snapshot(
            snapshot,
            sources,
            self.staging_path,
            cleanup_stale_photos=False,
        )
        try:
            if self.current_path.is_file():
                copy_session_bundle(self.current_path, self.previous_path)
        except Exception as exc:
            self._note_rotation_failure(exc)
        else:
            self._note_rotation_success()
        copy_session_bundle(self.staging_path, self.current_path)
        remove_session_bundle(self.staging_path)

    def _note_rotation_failure(self, exc: BaseException) -> None:
        """Log one failure per degraded stretch instead of on every debounce."""
        if not self.rotation_degraded:
            _LOG.warning(
                "R&D recovery could not keep a previous generation (%s -> %s): %s",
                self.current_path,
                self.previous_path,
                exc,
            )
        self.rotation_degraded = True

    def _note_rotation_success(self) -> None:
        if self.rotation_degraded:
            _LOG.info("R&D recovery generation rotation recovered.")
        self.rotation_degraded = False

    def _on_worker_finished(self, future: Future) -> None:
        if future is not self._future:
            return
        self._future = None
        try:
            future.result()
        except Exception as exc:
            self.save_failed.emit(str(exc))
        else:
            self.save_succeeded.emit()
        if self._pending and not self._closing:
            self._dispatch()

    def _valid_candidate(self, path: Path, kind: str) -> RecoveryCandidate | None:
        if not path.is_file():
            return None
        try:
            session, _missing = load_rnd_session(path)
            modified = datetime.fromtimestamp(path.stat().st_mtime)
        except UnsupportedSessionVersion:
            # The file is intact; this build is simply too old to read it.
            # Quarantining it would hide a perfectly good session, so report it.
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                modified = datetime.now()
            return RecoveryCandidate(
                path=path,
                kind=kind,
                preserved_at=modified,
                measurement_count=0,
                group_count=0,
                error="saved by a newer Fastgraph",
            )
        except Exception as exc:
            self._quarantine(path, exc)
            return None
        return RecoveryCandidate(
            path=path,
            kind=kind,
            preserved_at=modified,
            measurement_count=len(session.measurements),
            group_count=len(session.groups),
        )

    def _quarantine(self, path: Path, error: BaseException | None = None) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination_dir = self.quarantine_root / stamp
        destination_dir.mkdir(parents=True, exist_ok=True)
        _LOG.warning(
            "Quarantined an unreadable R&D recovery file: %s -> %s (%s)",
            path,
            destination_dir / path.name,
            error,
        )
        try:
            shutil.move(str(path), str(destination_dir / path.name))
        except FileNotFoundError:
            return
        sidecar = attachment_directory(path)
        if sidecar.exists():
            shutil.move(str(sidecar), str(destination_dir / sidecar.name))

    @staticmethod
    def _remove_empty_parent(path: Path) -> None:
        with contextlib.suppress(OSError):
            path.rmdir()
