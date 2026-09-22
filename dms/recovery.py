"""Automatic crash recovery shared by the Measure and R&D workspaces.

One :class:`RecoveryManager` does the work for both: a debounced snapshot
writer, two generations on disk, deferred bundles the user asked to keep for
later, and a quarantine for files that cannot be read at all. The workspaces
differ only in how a snapshot is saved, copied, loaded and summarised, which
they pass in as callables (see :func:`measure_recovery_manager` and
:func:`rnd_recovery_manager`).

What it guarantees:

- The newest snapshot is written to a staging file *first* and only then
  promoted, so a failure while copying ``current`` → ``previous`` costs the
  second generation and never the newest state.
- Snapshots are written on one background worker, so a slow disk cannot stall
  the UI and two writes can never interleave.
- A file saved by a newer Fastgraph is *reported*, not quarantined: it is
  intact, and hiding it would be worse than saying "update Fastgraph".
- With a clean-exit marker (Measure), a clean exit means the next start
  ignores the active generations, so the user is only asked after a crash.
  Deferred bundles are deliberately unaffected: those were kept on purpose.

Qt appears only as ``QObject``/``QTimer`` for the debounce and the two result
signals; everything the manager actually does with files is plain Python.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
from collections.abc import Callable, Mapping
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
from dms.rnd.models import RnDSession, UnsupportedSessionVersion
from dms.rnd.persistence import (
    RND_SESSION_EXTENSION,
    copy_session_bundle,
    load_rnd_session,
    save_rnd_snapshot,
)
from dms.rnd.photos import attachment_directory

_LOG = logging.getLogger(__name__)

CLEAN_EXIT_MARKER = "clean-exit.marker"


def copy_session_file(source: Path, destination: Path) -> None:
    """Copy one valid session file to another location, atomically."""
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


@dataclass(frozen=True)
class RecoveryCandidate:
    """One recoverable session found on disk."""

    path: Path
    kind: str
    preserved_at: datetime
    #: What the session holds, e.g. ``"3 sweeps"``, shown in the dialog.
    summary: str = ""
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
        return f"{kind} — {stamp} — {self.error or self.summary}"


#: ``_pending`` value meaning "ask the snapshot provider when the save runs".
_PULL = object()


class RecoveryManager(QObject):
    """Debounce snapshots and persist the newest state in one worker.

    ``save(snapshot, sources, path)`` writes one snapshot, ``copy(source,
    destination)`` copies a saved bundle, ``load(path, *args)`` restores one,
    and ``validate(payload)`` returns a short summary of a parsed file (``None``
    when there is nothing to recover) or raises ``unsupported`` for a file
    from a newer Fastgraph. ``sidecar(path)`` names a directory that travels
    with each session file.
    """

    save_succeeded = pyqtSignal()
    save_failed = pyqtSignal(str)
    _worker_finished = pyqtSignal(object)

    def __init__(
        self,
        root: Path,
        *,
        name: str,
        suffix: str,
        content_keys: tuple[str, ...],
        save: Callable[[Mapping[str, Any], Mapping[str, Path], Path], None],
        copy: Callable[[Path, Path], None],
        load: Callable[..., Any],
        validate: Callable[[dict[str, Any]], str | None],
        unsupported: type[Exception],
        sidecar: Callable[[Path], Path] | None = None,
        snapshot_provider: Callable[[], tuple[dict[str, Any], dict[str, Path]]] | None = None,
        clean_exit_marker: bool = False,
        debounce_ms: int = 1500,
        maximum_ms: int = 10000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.root = Path(root)
        self.current_path = self.root / f"current{suffix}"
        self.previous_path = self.root / f"previous{suffix}"
        self.staging_path = self.root / f"staging{suffix}"
        self.deferred_root = self.root / "deferred"
        self.quarantine_root = self.root / "quarantine"
        self.clean_exit_path = self.root / CLEAN_EXIT_MARKER if clean_exit_marker else None
        #: True while the previous-generation copy is failing. The newest
        #: snapshot is still saved; only the second generation is missing.
        self.rotation_degraded = False

        self._name = name
        self._deferred_name = f"session{suffix}"
        self._content_keys = content_keys
        self._save = save
        self._copy = copy
        self._load = load
        self._validate = validate
        self._unsupported = unsupported
        self._sidecar = sidecar
        self._snapshot_provider = snapshot_provider
        self._enabled = False
        self._closing = False
        self._pending: Any = None
        self._future: Future | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"{name} recovery")

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

    def schedule(self, snapshot: Mapping[str, Any] | None = None) -> None:
        """Queue a save for the next debounce.

        With ``snapshot`` (already serialized) that exact state is saved;
        without it the snapshot provider is asked when the save runs. A later
        call replaces an earlier pending one: only the newest state is worth
        writing.
        """
        if self._closing:
            return
        self._pending = _PULL if snapshot is None else dict(snapshot)
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
            self._remove(self.current_path)
            self._remove(self.previous_path)
            self._remove(self.staging_path)

    def shutdown_clean(self) -> None:
        """Stop saving, drop the active generations and mark the exit clean.

        The marker (when enabled) is the positive signal: a start that finds
        leftovers without it still offers them.
        """
        self._closing = True
        self._enabled = False
        self._pending = None
        self._debounce_timer.stop()
        self._maximum_timer.stop()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._future = None
        self._remove(self.current_path)
        self._remove(self.previous_path)
        self._remove(self.staging_path)
        if self.clean_exit_path is None:
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.clean_exit_path.write_text(datetime.now().isoformat(), encoding="utf-8")
        except OSError as exc:  # pragma: no cover - best effort only
            _LOG.warning("Could not record a clean %s exit: %s", self._name, exc)

    # -- candidates ------------------------------------------------------

    def candidates(self) -> list[RecoveryCandidate]:
        """Recoverable sessions, newest first.

        After a clean exit only deferred bundles are offered; the active
        generations are skipped even if files somehow survived.
        """
        found: list[RecoveryCandidate] = []
        if self.clean_exit_path is None or not self.clean_exit_path.exists():
            active = self._valid_candidate(self.current_path, "current")
            if active is not None:
                found.append(active)
            else:
                previous = self._valid_candidate(self.previous_path, "previous")
                if previous is not None:
                    found.append(previous)
        if self.deferred_root.exists():
            for path in sorted(self.deferred_root.glob(f"*/{self._deferred_name}")):
                candidate = self._valid_candidate(path, "deferred")
                if candidate is not None:
                    found.append(candidate)
        return sorted(found, key=lambda item: item.preserved_at, reverse=True)

    def restore(self, candidate: RecoveryCandidate, *args: Any) -> Any:
        """Load one candidate back into a live session."""
        return self._load(candidate.path, *args)

    def discard(self, candidate: RecoveryCandidate) -> None:
        """Delete one candidate for good."""
        self._remove(candidate.path)
        if candidate.kind in {"current", "previous"}:
            self._remove(self.current_path)
            self._remove(self.previous_path)
        self._remove_empty_parent(candidate.path.parent)

    def keep_for_later(self, candidate: RecoveryCandidate) -> RecoveryCandidate:
        """Move one candidate into ``deferred/`` so it survives this session."""
        if candidate.kind == "deferred":
            return candidate
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination = self.deferred_root / stamp / self._deferred_name
        destination.parent.mkdir(parents=True, exist_ok=False)
        self._copy(candidate.path, destination)
        self.discard(candidate)
        kept = self._valid_candidate(destination, "deferred")
        if kept is None:
            raise OSError(f"Could not validate the deferred {self._name} recovery session.")
        return kept

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
        sources: dict[str, Path] = {}
        if snapshot is _PULL:
            try:
                snapshot, sources = self._snapshot_provider()
            except Exception as exc:
                self.save_failed.emit(str(exc))
                return
        if not any(snapshot.get(key) for key in self._content_keys):
            self.clear_active()
            self.save_succeeded.emit()
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            # Any new state means this run is no longer a clean exit.
            if self.clean_exit_path is not None:
                self._remove(self.clean_exit_path)
        except OSError as exc:
            self.save_failed.emit(str(exc))
            return
        self._future = self._executor.submit(self._save_snapshot, snapshot, sources)
        self._future.add_done_callback(self._worker_finished.emit)

    def _save_snapshot(
        self,
        snapshot: Mapping[str, Any],
        sources: Mapping[str, Path] | None = None,
    ) -> None:
        """Stage the newest snapshot, rotate the old generation, then promote.

        Order matters: the new state exists on disk before ``current`` is
        touched, so a failure part-way through can only cost the second
        generation.
        """
        self._remove(self.staging_path)
        self._save(snapshot, sources or {}, self.staging_path)
        try:
            if self.current_path.is_file():
                self._copy(self.current_path, self.previous_path)
        except Exception as exc:
            self._note_rotation_failure(exc)
        else:
            self._note_rotation_success()
        self._copy(self.staging_path, self.current_path)
        self._remove(self.staging_path)

    def _note_rotation_failure(self, exc: BaseException) -> None:
        """Log one failure per degraded stretch instead of on every debounce."""
        if not self.rotation_degraded:
            _LOG.warning(
                "%s recovery could not keep a previous generation (%s -> %s): %s",
                self._name,
                self.current_path,
                self.previous_path,
                exc,
            )
        self.rotation_degraded = True

    def _note_rotation_success(self) -> None:
        if self.rotation_degraded:
            _LOG.info("%s recovery generation rotation recovered.", self._name)
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

    def _valid_candidate(self, path: Path, kind: str) -> RecoveryCandidate | None:
        if not path.is_file():
            return None
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            modified = datetime.now()
        try:
            # Parse directly rather than through the workspace loader: a
            # damaged file has to stay put here so it can be quarantined.
            summary = self._validate(json.loads(path.read_text(encoding="utf-8")))
        except self._unsupported:
            # The file is intact; this build is simply too old to read it.
            return RecoveryCandidate(
                path=path,
                kind=kind,
                preserved_at=modified,
                error="saved by a newer Fastgraph",
            )
        except Exception as exc:
            self._quarantine(path, exc)
            return None
        if summary is None:
            return None
        return RecoveryCandidate(path=path, kind=kind, preserved_at=modified, summary=summary)

    def _quarantine(self, path: Path, error: BaseException | None = None) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination_dir = self.quarantine_root / stamp
        destination_dir.mkdir(parents=True, exist_ok=True)
        _LOG.warning(
            "Quarantined an unreadable %s recovery file: %s -> %s (%s)",
            self._name,
            path,
            destination_dir / path.name,
            error,
        )
        try:
            shutil.move(str(path), str(destination_dir / path.name))
        except FileNotFoundError:
            return
        if self._sidecar is not None:
            sidecar = self._sidecar(path)
            if sidecar.exists():
                shutil.move(str(sidecar), str(destination_dir / sidecar.name))

    def _remove(self, path: Path) -> None:
        with contextlib.suppress(FileNotFoundError):
            Path(path).unlink()
        if self._sidecar is not None:
            sidecar = self._sidecar(Path(path))
            if sidecar.exists():
                shutil.rmtree(sidecar)

    @staticmethod
    def _remove_empty_parent(path: Path) -> None:
        with contextlib.suppress(OSError):
            path.rmdir()


def _measure_summary(payload: dict[str, Any]) -> str | None:
    session = MeasureSession.from_dict(payload)
    if session.is_empty():
        return None
    if session.two_channel:
        return f"{len(session.pairs)} pairs"
    return f"{len(session.sweeps)} sweeps"


def _rnd_summary(payload: dict[str, Any]) -> str:
    session = RnDSession.from_dict(payload)
    return f"{len(session.measurements)} measurements, {len(session.groups)} groups"


def measure_recovery_manager(
    root: Path,
    *,
    debounce_ms: int = 1500,
    maximum_ms: int = 10000,
    parent: QObject | None = None,
) -> RecoveryManager:
    """Recovery for the Measure workspace, under ``<root>/measure/``.

    The extra segment lets the R&D and Measure stores share one recovery
    directory. Snapshots are pushed with ``schedule(snapshot)``.
    """
    return RecoveryManager(
        Path(root) / "measure",
        name="Measure",
        suffix=MEASURE_SESSION_EXTENSION,
        content_keys=("sweeps", "pairs"),
        save=lambda snapshot, _sources, path: save_measure_snapshot(snapshot, path),
        copy=copy_session_file,
        load=load_measure_session,
        validate=_measure_summary,
        unsupported=UnsupportedMeasureSessionVersion,
        clean_exit_marker=True,
        debounce_ms=debounce_ms,
        maximum_ms=maximum_ms,
        parent=parent,
    )


def rnd_recovery_manager(
    root: Path,
    snapshot_provider: Callable[[], tuple[dict[str, Any], dict[str, Path]]],
    *,
    debounce_ms: int = 1500,
    maximum_ms: int = 15000,
    parent: QObject | None = None,
) -> RecoveryManager:
    """Recovery for the R&D workspace, photo sidecars included.

    ``schedule()`` takes no snapshot: ``snapshot_provider`` is asked for the
    session and its photo sources when the save runs.
    """
    return RecoveryManager(
        root,
        name="R&D",
        suffix=RND_SESSION_EXTENSION,
        content_keys=("measurements", "groups"),
        save=lambda snapshot, sources, path: save_rnd_snapshot(
            snapshot, sources, path, cleanup_stale_photos=False
        ),
        copy=copy_session_bundle,
        load=load_rnd_session,
        validate=_rnd_summary,
        unsupported=UnsupportedSessionVersion,
        sidecar=attachment_directory,
        snapshot_provider=snapshot_provider,
        debounce_ms=debounce_ms,
        maximum_ms=maximum_ms,
        parent=parent,
    )
