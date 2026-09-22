"""
Owner of the sweep worker thread.

``SweepRunner`` is the single place that starts, aborts and — crucially —
*joins* the background sweep thread. It does no audio work of its own and never
touches ``sounddevice``; it only owns a :class:`~dms.audio_engine.SweepWorker`
running inside a ``QThread`` and re-emits that worker's five signals.

Two review findings are closed here (``FASTGRAPH_REVIEW_2026-09-08.md``
section B):

- **B1** — :meth:`SweepRunner.shutdown` aborts *and waits*, so ``closeEvent``
  can no longer tear the window down while ``sd.playrec`` is live.
- **B2** — :meth:`SweepRunner.start` refuses to start a new sweep until the
  previous thread has actually terminated. ``sd.playrec``/``sd.stop()`` are
  process-global, so a stale thread that is still polling would otherwise call
  ``sd.stop()`` in the middle of the new sweep and truncate it.

The worker is built by a ``worker_factory`` callable rather than passed in, so
tests can inject a fake worker without any audio hardware.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import numpy as np
from PyQt6.QtCore import QObject, QThread, pyqtSignal

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps sounddevice out
    from dms.audio_engine import SweepWorker


#: How long, in milliseconds, to wait for a sweep thread to terminate.
DEFAULT_WAIT_MS = 4000


class _SweepThread(QThread):
    """Run ``worker.run(**kwargs)`` on a background thread.

    Moved verbatim from ``dms/ui/main_window.py`` so the queue no longer has to
    know how the worker is hosted.
    """

    def __init__(self, worker: Any, **kwargs: Any) -> None:
        super().__init__()
        self._worker = worker
        self._kwargs = kwargs

    def run(self) -> None:
        self._worker.run(**self._kwargs)

    def abort(self) -> None:
        self._worker.abort()


class SweepRunner(QObject):
    """Start, abort and join the sweep thread; re-emit the worker's signals."""

    finished = pyqtSignal(np.ndarray, np.ndarray)  # recording, sweep
    error = pyqtSignal(str)
    progress = pyqtSignal(float)  # 0.0 … 1.0
    timing_quality = pyqtSignal(float, float, float, float)
    measurement_diagnostics = pyqtSignal(object)
    #: Emitted once the thread has been joined and its objects released.
    idle = pyqtSignal()

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        wait_ms: int = DEFAULT_WAIT_MS,
    ) -> None:
        super().__init__(parent)
        self._wait_ms = int(wait_ms)
        self._thread: _SweepThread | None = None
        self._worker: Any = None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        """Whether a sweep thread is currently executing."""
        thread = self._thread
        return thread is not None and thread.isRunning()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        worker_factory: Callable[[], SweepWorker],
        **worker_kwargs: Any,
    ) -> bool:
        """Start a sweep, joining any previous thread first.

        Returns ``True`` when the new thread was started. Returns ``False``,
        having emitted nothing and started nothing, when a previous thread
        refused to terminate within the runner's wait budget — the caller must
        surface that as an error rather than letting two threads share the
        process-global PortAudio stream.
        """
        if not self._release(wait_ms=self._wait_ms):
            return False

        worker = worker_factory()
        worker.finished.connect(self.finished)
        worker.error.connect(self.error)
        worker.progress.connect(self.progress)
        worker.timing_quality.connect(self.timing_quality)
        worker.measurement_diagnostics.connect(self.measurement_diagnostics)

        thread = _SweepThread(worker, **worker_kwargs)
        thread.finished.connect(self._on_thread_finished)
        self._worker = worker
        self._thread = thread
        thread.start()
        return True

    def abort(self, *, wait_ms: int = DEFAULT_WAIT_MS) -> bool:
        """Ask the worker to stop and wait for the thread to terminate.

        Returns ``True`` when nothing is running any more.
        """
        return self._release(wait_ms=wait_ms)

    def wait(self, ms: int = DEFAULT_WAIT_MS) -> bool:
        """Block until the sweep thread terminates. ``True`` when it has."""
        thread = self._thread
        if thread is None:
            return True
        if thread.isRunning() and not thread.wait(int(ms)):
            return False
        self._finalize(thread)
        return True

    def shutdown(self) -> None:
        """Abort, join and release everything. Safe to call from closeEvent.

        A thread that will not stop is deliberately *not* deleted: destroying a
        running ``QThread`` is what produces "Destroyed while thread is still
        running" and PortAudio crashes at exit.
        """
        self._release(wait_ms=self._wait_ms)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _request_abort(self) -> None:
        thread = self._thread
        if thread is None:
            return
        with contextlib.suppress(Exception):
            thread.abort()

    def _release(self, *, wait_ms: int) -> bool:
        """Abort and join the current thread. ``False`` if it will not stop."""
        thread = self._thread
        if thread is None:
            return True
        if thread.isRunning():
            self._request_abort()
            if not thread.wait(int(wait_ms)):
                return False
        self._finalize(thread)
        return True

    def _on_thread_finished(self) -> None:
        thread = self.sender()
        if thread is not self._thread:
            # Already released synchronously by start()/abort()/shutdown().
            return
        self._finalize(self._thread)

    def _finalize(self, thread: _SweepThread | None) -> None:
        """Disconnect, drop and delete a terminated thread and its worker."""
        if thread is None or thread is not self._thread:
            return
        worker = self._worker
        self._thread = None
        self._worker = None

        with contextlib.suppress(TypeError, RuntimeError):
            thread.finished.disconnect(self._on_thread_finished)
        if worker is not None:
            for signal, slot in (
                (worker.finished, self.finished),
                (worker.error, self.error),
                (worker.progress, self.progress),
                (worker.timing_quality, self.timing_quality),
                (worker.measurement_diagnostics, self.measurement_diagnostics),
            ):
                with contextlib.suppress(TypeError, RuntimeError):
                    signal.disconnect(slot)
            with contextlib.suppress(RuntimeError):
                worker.deleteLater()
        with contextlib.suppress(RuntimeError):
            thread.deleteLater()
        self.idle.emit()
