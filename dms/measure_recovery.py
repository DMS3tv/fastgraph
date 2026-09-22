"""Automatic crash recovery for the Measure workspace.

This is the R&D recovery manager's structure (``dms/rnd/recovery.py``) minus
photos: a debounced snapshot writer, two generations on disk, deferred bundles
the user asked to keep for later, and a quarantine for files that cannot be
read at all.

What it guarantees:

- The newest snapshot is written to a staging file *first* and only then
  promoted, so a failure while copying ``current`` → ``previous`` costs the
  second generation and never the newest state.
- Snapshots are taken on the caller's thread (``schedule`` receives an already
  serialized dictionary) and written on one background worker, so a slow disk
  cannot stall the UI and two writes can never interleave.
- A file saved by a newer Fastgraph is *reported*, not quarantined: it is
  intact, and hiding it would be worse than saying "update Fastgraph".
- A clean exit leaves a marker. On the next start the active generations are
  ignored, so the user is only ever asked to recover after a crash. Deferred
  bundles are deliberately unaffected: those were kept on purpose.

Qt appears only as ``QObject``/``QTimer`` for the debounce and the two result
signals; everything the manager actually does with files is plain Python.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from dms.measure_persistence import load_measure_session, save_measure_snapshot
from dms.measure_session import (
    MEASURE_SESSION_EXTENSION,
    MeasureSession,
    UnsupportedMeasureSessionVersion,
)

_LOG = logging.getLogger(__name__)

#: Debounce for a burst of edits, and the longest a continuous stream of edits
#: can go without a save.
DEFAULT_DEBOUNCE_MS = 1500
DEFAULT_MAXIMUM_MS = 10000

CURRENT_NAME = f"current{MEASURE_SESSION_EXTENSION}"
PREVIOUS_NAME = f"previous{MEASURE_SESSION_EXTENSION}"
STAGING_NAME = f"staging{MEASURE_SESSION_EXTENSION}"
DEFERRED_NAME = f"session{MEASURE_SESSION_EXTENSION}"
CLEAN_EXIT_MARKER = "clean-exit.marker"


def copy_session_file(source: Path, destination: Path) -> None:
    """Copy one valid session file to another location, atomically.

    Module-level so a caller (and the test suite) can substitute it; the
    manager looks it up through the module globals on every use.
    """
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(f".{destination.name}.copy-tmp")
    try:
        shutil.copyfile(source, temp_path)
        temp_path.replace(destination)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()


def remove_session_file(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        Path(path).unlink()


@dataclass(frozen=True)
class MeasureRecoveryCandidate:
    """One recoverable Measure session found on disk."""

    path: Path
    kind: str
    preserved_at: datetime
    sweep_count: int
    pair_count: int
    two_channel: bool = False
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
        if self.two_channel:
            return f"{kind} — {stamp} — {self.pair_count} pairs"
        return f"{kind} — {stamp} — {self.sweep_count} sweeps"


class MeasureRecoveryManager(QObject):
    """Debounce Measure snapshots and persist the newest state in one worker."""

    save_succeeded = pyqtSignal()
    save_failed = pyqtSignal(str)
    _worker_finished = pyqtSignal(object)

    def __init__(
        self,
        root: Path,
        *,
        debounce_ms: int = DEFAULT_DEBOUNCE_MS,
        maximum_ms: int = DEFAULT_MAXIMUM_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        #: Everything this manager owns lives under ``<root>/measure/`` so the
        #: R&D and Measure recovery stores can share one recovery directory.
        self.root = Path(root) / "measure"
        self.current_path = self.root / CURRENT_NAME
        self.previous_path = self.root / PREVIOUS_NAME
        self.staging_path = self.root / STAGING_NAME
        self.deferred_root = self.root / "deferred"
        self.quarantine_root = self.root / "quarantine"
        self.clean_exit_path = self.root / CLEAN_EXIT_MARKER
        #: True while the previous-generation copy is failing. The newest
        #: snapshot is still saved; only the second generation is missing.
        self.rotation_degraded = False

        self._enabled = False
        self._closing = False
        self._pending: dict[str, Any] | None = None
        self._future: Future | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="measure-recovery",
        )

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(int(debounce_ms))
        self._debounce_timer.timeout.connect(self._dispatch)
        self._maximum_timer = QTimer(self)
        self._maximum_timer.setSingleShot(True)
        self._maximum_timer.setInterval(int(maximum_ms))
        self._maximum_timer.timeout.connect(self._dispatch)
        self._worker_finished.connect(self._on_worker_finished)

    # -- scheduling ------------------------------------------------------

    def enable(self) -> None:
        self._enabled = True

    def schedule(self, snapshot: Mapping[str, Any]) -> None:
        """Queue ``snapshot`` (already serialized) for the next debounced save.

        A later snapshot replaces an earlier pending one: only the newest
        state is worth writing.
        """
        if self._closing:
            return
        self._pending = dict(snapshot)
        if not self._enabled:
            return
        self._debounce_timer.start()
        if not self._maximum_timer.isActive():
            self._maximum_timer.start()

    def flush(self) -> None:
        """Write any pending snapshot now instead of waiting for the timers."""
        self._dispatch()

    def clear_active(self) -> None:
        """Drop the active generations: the workspace is empty or saved."""
        self._pending = None
        self._debounce_timer.stop()
        self._maximum_timer.stop()
        if self._future is None:
            remove_session_file(self.current_path)
            remove_session_file(self.previous_path)
            remove_session_file(self.staging_path)

    def shutdown_clean(self) -> None:
        """Mark this exit clean and stop saving.

        The active generations are removed *and* a marker is written, so a
        start that finds leftovers without the marker still offers them: the
        marker is the positive signal, the leftovers alone are not.
        """
        self._closing = True
        self._enabled = False
        self._pending = None
        self._debounce_timer.stop()
        self._maximum_timer.stop()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._future = None
        remove_session_file(self.current_path)
        remove_session_file(self.previous_path)
        remove_session_file(self.staging_path)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.clean_exit_path.write_text(
                datetime.now().isoformat(),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover - best effort only
            _LOG.warning("Could not record a clean Measure exit: %s", exc)

    # -- candidates ------------------------------------------------------

    def candidates(self) -> list[MeasureRecoveryCandidate]:
        """Recoverable sessions, newest first.

        After a clean exit only deferred bundles are offered; the active
        generations are skipped even if files somehow survived.
        """
        found: list[MeasureRecoveryCandidate] = []
        if not self.clean_exit_path.exists():
            active = self._valid_candidate(self.current_path, "current")
            if active is not None:
                found.append(active)
            else:
                previous = self._valid_candidate(self.previous_path, "previous")
                if previous is not None:
                    found.append(previous)
        if self.deferred_root.exists():
            for path in sorted(self.deferred_root.glob(f"*/{DEFERRED_NAME}")):
                candidate = self._valid_candidate(path, "deferred")
                if candidate is not None:
                    found.append(candidate)
        return sorted(found, key=lambda item: item.preserved_at, reverse=True)

    def restore(self, candidate: MeasureRecoveryCandidate) -> MeasureSession:
        """Load one candidate back into a live session."""
        return load_measure_session(candidate.path)

    def discard(self, candidate: MeasureRecoveryCandidate) -> None:
        """Delete one candidate for good."""
        remove_session_file(candidate.path)
        if candidate.kind in {"current", "previous"}:
            remove_session_file(self.current_path)
            remove_session_file(self.previous_path)
        self._remove_empty_parent(candidate.path.parent)

    def defer(self, candidate: MeasureRecoveryCandidate) -> MeasureRecoveryCandidate:
        """Move one candidate into ``deferred/`` so it survives this session."""
        if candidate.kind == "deferred":
            return candidate
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination = self.deferred_root / stamp / DEFERRED_NAME
        destination.parent.mkdir(parents=True, exist_ok=False)
        copy_session_file(candidate.path, destination)
        self.discard(candidate)
        kept = self._valid_candidate(destination, "deferred")
        if kept is None:
            raise OSError("Could not validate the deferred Measure recovery session.")
        return kept

    #: Kept as an alias of the R&D wording so both workspaces read the same.
    keep_for_later = defer

    # -- worker ----------------------------------------------------------

    def _dispatch(self) -> None:
        if not self._enabled or self._closing or self._pending is None:
            return
        if self._future is not None:
            return
        snapshot = self._pending
        self._pending = None
        self._debounce_timer.stop()
        self._maximum_timer.stop()
        if not snapshot.get("sweeps") and not snapshot.get("pairs"):
            self.clear_active()
            self.save_succeeded.emit()
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            # Any new state means this run is no longer a clean exit.
            remove_session_file(self.clean_exit_path)
        except OSError as exc:
            self.save_failed.emit(str(exc))
            return
        self._future = self._executor.submit(self._save_snapshot, snapshot)
        self._future.add_done_callback(self._worker_finished.emit)

    def _save_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        """Stage the newest snapshot, rotate the old generation, then promote.

        Order matters: the new state exists on disk before ``current`` is
        touched, so a failure part-way through can only cost the second
        generation.
        """
        remove_session_file(self.staging_path)
        save_measure_snapshot(snapshot, self.staging_path)
        try:
            if self.current_path.is_file():
                copy_session_file(self.current_path, self.previous_path)
        except Exception as exc:
            self._note_rotation_failure(exc)
        else:
            self._note_rotation_success()
        copy_session_file(self.staging_path, self.current_path)
        remove_session_file(self.staging_path)

    def _note_rotation_failure(self, exc: BaseException) -> None:
        """Log one failure per degraded stretch instead of on every debounce."""
        if not self.rotation_degraded:
            _LOG.warning(
                "Measure recovery could not keep a previous generation (%s -> %s): %s",
                self.current_path,
                self.previous_path,
                exc,
            )
        self.rotation_degraded = True

    def _note_rotation_success(self) -> None:
        if self.rotation_degraded:
            _LOG.info("Measure recovery generation rotation recovered.")
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
        if self._pending is not None and not self._closing:
            self._dispatch()

    # -- helpers ---------------------------------------------------------

    def _valid_candidate(
        self,
        path: Path,
        kind: str,
    ) -> MeasureRecoveryCandidate | None:
        if not path.is_file():
            return None
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            modified = datetime.now()
        try:
            # Read straight through instead of via ``load_measure_session``:
            # that helper renames a damaged file aside for the user, and here
            # the file has to stay put so it can be quarantined instead.
            session = MeasureSession.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except UnsupportedMeasureSessionVersion:
            # The file is intact; this build is simply too old to read it.
            return MeasureRecoveryCandidate(
                path=path,
                kind=kind,
                preserved_at=modified,
                sweep_count=0,
                pair_count=0,
                error="saved by a newer Fastgraph",
            )
        except Exception as exc:
            self._quarantine(path, exc)
            return None
        if session.is_empty():
            return None
        return MeasureRecoveryCandidate(
            path=path,
            kind=kind,
            preserved_at=modified,
            sweep_count=len(session.sweeps),
            pair_count=len(session.pairs),
            two_channel=session.two_channel,
        )

    def _quarantine(self, path: Path, error: BaseException | None = None) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination_dir = self.quarantine_root / stamp
        destination_dir.mkdir(parents=True, exist_ok=True)
        _LOG.warning(
            "Quarantined an unreadable Measure recovery file: %s -> %s (%s)",
            path,
            destination_dir / path.name,
            error,
        )
        try:
            shutil.move(str(path), str(destination_dir / path.name))
        except FileNotFoundError:
            return

    @staticmethod
    def _remove_empty_parent(path: Path) -> None:
        with contextlib.suppress(OSError):
            path.rmdir()
