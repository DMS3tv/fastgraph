"""Thread-ownership tests for ``dms.ui.sweep_runner.SweepRunner``.

These use a fake worker with the same five signals and the same
``threading.Event`` abort contract as ``dms.audio_engine.SweepWorker``
(``audio_engine.py:441-446``), so no audio device is touched.
"""

from __future__ import annotations

import contextlib
import threading
import time

import numpy as np
import pytest
from PyQt6.QtCore import QObject, pyqtSignal

from dms.ui.sweep_runner import SweepRunner


class FakeSweepWorker(QObject):
    """Stand-in for ``SweepWorker``: same signals, same abort contract."""

    finished = pyqtSignal(np.ndarray, np.ndarray)
    error = pyqtSignal(str)
    progress = pyqtSignal(float)
    timing_quality = pyqtSignal(float, float, float, float)
    measurement_diagnostics = pyqtSignal(object)

    def __init__(
        self,
        *,
        duration: float = 0.2,
        honour_abort: bool = True,
        emit_signals: bool = False,
    ) -> None:
        super().__init__()
        self._abort = threading.Event()
        self._duration = duration
        self._honour_abort = honour_abort
        self._emit_signals = emit_signals
        self.started = threading.Event()
        self.aborted_early = False
        self.kwargs: dict = {}

    def abort(self) -> None:
        self._abort.set()

    def abort_requested(self) -> bool:
        return self._abort.is_set()

    def run(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.started.set()
        deadline = time.monotonic() + self._duration
        while time.monotonic() < deadline:
            if self._honour_abort and self._abort.is_set():
                self.aborted_early = True
                return
            time.sleep(0.01)
        if self._emit_signals:
            self.progress.emit(0.5)
            self.timing_quality.emit(12.0, 9.0, 1.5, 22.0)
            self.measurement_diagnostics.emit({"failure_reason": None})
            self.finished.emit(np.zeros(4, dtype=np.float32), np.ones(4, dtype=np.float32))


def pump_until(qapp, predicate, timeout: float = 5.0) -> bool:
    """Run the event loop until ``predicate`` is true or the timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return bool(predicate())


@pytest.fixture
def runner(qapp):
    made = SweepRunner(wait_ms=3000)
    yield made
    made.shutdown()
    qapp.processEvents()


def test_start_refuses_while_previous_thread_runs(qapp):
    """B2: a stale thread must never share the process-global audio stream."""
    stubborn = FakeSweepWorker(duration=0.6, honour_abort=False)
    impatient = SweepRunner(wait_ms=40)
    calls: list[FakeSweepWorker] = []

    def factory():
        calls.append(stubborn)
        return stubborn

    assert impatient.start(factory, sweep="first") is True
    assert stubborn.started.wait(2.0)

    second_factory_calls = 0

    def second_factory():
        nonlocal second_factory_calls
        second_factory_calls += 1
        return FakeSweepWorker()

    assert impatient.start(second_factory) is False
    # Nothing was built and nothing was started.
    assert second_factory_calls == 0
    assert impatient.is_running() is True
    assert calls == [stubborn]

    # The stubborn worker still has to be joined before the test ends.
    assert pump_until(qapp, lambda: not impatient.is_running(), timeout=5.0)
    assert impatient.abort(wait_ms=3000) is True
    qapp.processEvents()


def test_start_waits_for_previous_thread_then_starts(qapp, runner):
    first = FakeSweepWorker(duration=5.0, honour_abort=True)
    second = FakeSweepWorker(duration=0.05, honour_abort=True)

    assert runner.start(lambda: first, sweep="first") is True
    assert first.started.wait(2.0)

    started_at = time.monotonic()
    assert runner.start(lambda: second, sweep="second") is True
    elapsed = time.monotonic() - started_at

    # The first thread was aborted and joined, not left running.
    assert first.abort_requested() is True
    assert first.aborted_early is True
    assert elapsed < 4.0  # it stopped on abort, not after its 5 s duration
    assert second.started.wait(2.0)
    assert second.kwargs == {"sweep": "second"}
    assert pump_until(qapp, lambda: not runner.is_running())


def test_shutdown_joins_thread(qapp, runner):
    worker = FakeSweepWorker(duration=5.0, honour_abort=True)
    assert runner.start(lambda: worker) is True
    assert worker.started.wait(2.0)
    assert runner.is_running() is True

    runner.shutdown()

    assert runner.is_running() is False
    assert worker.abort_requested() is True
    assert worker.aborted_early is True


def test_abort_sets_worker_event_and_waits(qapp, runner):
    worker = FakeSweepWorker(duration=5.0, honour_abort=True)
    idle_seen: list[int] = []
    runner.idle.connect(lambda: idle_seen.append(1))

    assert runner.start(lambda: worker) is True
    assert worker.started.wait(2.0)

    assert runner.abort(wait_ms=3000) is True
    assert worker.abort_requested() is True
    assert runner.is_running() is False
    assert idle_seen == [1]
    # Aborting again with nothing running is a harmless no-op.
    assert runner.abort(wait_ms=10) is True
    assert idle_seen == [1]


def test_runner_reemits_worker_signals(qapp, runner):
    worker = FakeSweepWorker(duration=0.05, emit_signals=True)
    finished: list[tuple] = []
    progress: list[float] = []
    timing: list[tuple] = []
    diagnostics: list[object] = []
    errors: list[str] = []

    runner.finished.connect(lambda rec, swp: finished.append((rec, swp)))
    runner.progress.connect(progress.append)
    runner.timing_quality.connect(lambda *args: timing.append(args))
    runner.measurement_diagnostics.connect(diagnostics.append)
    runner.error.connect(errors.append)

    assert runner.start(lambda: worker, sweep="s", fs=48000) is True
    assert pump_until(qapp, lambda: bool(finished) and not runner.is_running())

    assert progress == [0.5]
    assert timing == [(12.0, 9.0, 1.5, 22.0)]
    assert diagnostics == [{"failure_reason": None}]
    assert errors == []
    recording, sweep = finished[0]
    assert len(recording) == 4 and len(sweep) == 4
    assert worker.kwargs == {"sweep": "s", "fs": 48000}


def test_error_signal_is_reemitted(qapp, runner):
    class ErroringWorker(FakeSweepWorker):
        def run(self, **kwargs):
            self.kwargs = kwargs
            self.started.set()
            self.error.emit("PortAudio error: no device")

    worker = ErroringWorker()
    errors: list[str] = []
    runner.error.connect(errors.append)

    assert runner.start(lambda: worker) is True
    assert pump_until(qapp, lambda: bool(errors) and not runner.is_running())
    assert errors == ["PortAudio error: no device"]


def test_signals_stop_after_the_thread_is_released(qapp, runner):
    """A released worker must not be able to reach the runner's listeners."""
    worker = FakeSweepWorker(duration=0.05, emit_signals=True)
    progress: list[float] = []
    runner.progress.connect(progress.append)

    assert runner.start(lambda: worker) is True
    assert pump_until(qapp, lambda: not runner.is_running())
    qapp.processEvents()
    before = len(progress)

    # The worker's C++ object may already be released by the runner, which is
    # the strongest possible form of "it can no longer reach a listener".
    with contextlib.suppress(RuntimeError):
        worker.progress.emit(0.99)
    qapp.processEvents()
    assert len(progress) == before


def test_is_running_false_before_any_start(qapp):
    idle_runner = SweepRunner()
    assert idle_runner.is_running() is False
    assert idle_runner.wait(10) is True
    assert idle_runner.abort(wait_ms=10) is True
    idle_runner.shutdown()
