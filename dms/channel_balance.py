"""Continuous two-channel generator and capture engine for Channel Balance."""

from __future__ import annotations

from collections import deque
import math
import threading

import numpy as np
import sounddevice as sd
from PyQt6.QtCore import QObject, pyqtSignal


def frequency_limit(sample_rate: int) -> float:
    return min(20000.0, 0.45 * float(sample_rate))


def db_to_gain(level_db: float) -> float:
    return 10.0 ** (float(level_db) / 20.0)


def polyblep_square(phases: np.ndarray, phase_step: float) -> np.ndarray:
    """Return a low-alias square waveform for phases in the range 0 to 1."""

    phase = np.mod(np.asarray(phases, dtype=float), 1.0)
    step = max(1e-9, min(0.5, float(phase_step)))
    output = np.where(phase < 0.5, 1.0, -1.0)
    output += _polyblep(phase, step)
    output -= _polyblep(np.mod(phase + 0.5, 1.0), step)
    return np.clip(output, -1.0, 1.0)


def _polyblep(phase: np.ndarray, step: float) -> np.ndarray:
    correction = np.zeros_like(phase)
    leading = phase < step
    if np.any(leading):
        x = phase[leading] / step
        correction[leading] = x + x - x * x - 1.0
    trailing = phase > 1.0 - step
    if np.any(trailing):
        x = (phase[trailing] - 1.0) / step
        correction[trailing] = x * x + x + x + 1.0
    return correction


class ChannelBalanceEngine(QObject):
    """Run one duplex stream. The audio callback never emits Qt signals."""

    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stop = threading.Event()
        self._ramp_done = threading.Event()
        self._frames: deque[np.ndarray] = deque(maxlen=96)
        self._parameter_lock = threading.Lock()
        self._waveform = "sine"
        self._frequency = 500.0
        self._level_db = -6.0
        self._phase = 0.0
        self._sample_rate = 48000
        self._active_signature: tuple[str, float, float] | None = None
        self._previous_block_tail = 0.0

    def set_parameters(self, waveform: str, frequency: float, level_db: float) -> None:
        selected = str(waveform).strip().lower()
        if selected not in {"sine", "square"}:
            selected = "sine"
        with self._parameter_lock:
            self._waveform = selected
            self._frequency = max(
                20.0,
                min(frequency_limit(self._sample_rate), float(frequency)),
            )
            self._level_db = float(level_db)

    def stop(self) -> None:
        self._stop.set()

    def run(
        self,
        *,
        input_device: int,
        output_device: int,
        sample_rate: int,
        block_size: int,
        latency: str,
    ) -> None:
        self._stop.clear()
        self._ramp_done.clear()
        self._sample_rate = int(sample_rate)
        self.set_parameters(self._waveform, self._frequency, self._level_db)
        try:
            with sd.Stream(
                device=(int(input_device), int(output_device)),
                samplerate=self._sample_rate,
                blocksize=int(block_size),
                channels=(2, 2),
                dtype="float32",
                latency=latency,
                callback=self._callback,
            ):
                while not self._stop.wait(0.05):
                    pass
                self._ramp_done.wait(0.25)
        except Exception as exc:
            self.error.emit(f"Channel Balance stream failed: {exc}")
        finally:
            self.finished.emit()

    def _callback(self, indata, outdata, frames, _time_info, status) -> None:
        if self._stop.is_set():
            ramp = np.linspace(
                float(self._previous_block_tail),
                0.0,
                frames,
                dtype=np.float32,
            )
            outdata[:, 0] = ramp
            outdata[:, 1] = ramp
            if indata.shape[1] >= 2:
                self._frames.append(
                    np.array(indata[:, :2], dtype=np.float32, copy=True)
                )
            self._previous_block_tail = 0.0
            self._ramp_done.set()
            raise sd.CallbackStop

        with self._parameter_lock:
            waveform = self._waveform
            frequency = max(
                20.0,
                min(frequency_limit(self._sample_rate), self._frequency),
            )
            level_db = self._level_db
        phase_step = frequency / float(self._sample_rate)
        phases = self._phase + phase_step * np.arange(frames, dtype=float)
        if waveform == "square":
            signal = polyblep_square(phases, phase_step)
        else:
            signal = np.sin(2.0 * np.pi * phases)
        signal *= db_to_gain(level_db)

        signature = (waveform, frequency, level_db)
        ramp_n = min(frames, max(1, int(round(0.010 * self._sample_rate))))
        if self._active_signature != signature:
            start = float(self._previous_block_tail)
            signal[:ramp_n] = np.linspace(start, signal[ramp_n - 1], ramp_n)
            self._active_signature = signature
        self._previous_block_tail = float(signal[-1]) if frames else 0.0
        self._phase = float((self._phase + phase_step * frames) % 1.0)

        outdata[:, 0] = signal
        outdata[:, 1] = signal
        if indata.shape[1] >= 2:
            self._frames.append(np.array(indata[:, :2], dtype=np.float32, copy=True))

    def snapshot(self, sample_count: int) -> tuple[np.ndarray, np.ndarray, float, float, float]:
        """Return recent L/R samples plus L, R, and signed L-minus-R RMS dB."""

        chunks = list(self._frames)
        if not chunks:
            empty = np.array([], dtype=np.float32)
            return empty, empty, -120.0, -120.0, 0.0
        data = np.concatenate(chunks, axis=0)
        count = max(1, int(sample_count))
        data = data[-count:]
        left = np.asarray(data[:, 0], dtype=np.float32)
        right = np.asarray(data[:, 1], dtype=np.float32)
        left_db = _rms_db(left)
        right_db = _rms_db(right)
        return left, right, left_db, right_db, left_db - right_db


def _rms_db(values: np.ndarray) -> float:
    if len(values) == 0:
        return -120.0
    rms = math.sqrt(float(np.mean(np.square(values, dtype=np.float64))))
    return 20.0 * math.log10(max(rms, 1e-6))
