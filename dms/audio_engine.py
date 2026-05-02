"""
Audio engine: device enumeration, level monitoring, sweep play/record.
Thread-safe; all callbacks communicate via Qt signals.
"""

import time
import threading
from typing import Optional, Callable

import numpy as np
import sounddevice as sd
from PyQt6.QtCore import QObject, pyqtSignal


# ---------------------------------------------------------------------------
# Device helpers
# ---------------------------------------------------------------------------

def get_output_devices() -> list[dict]:
    try:
        return [
            d for d in sd.query_devices()
            if d["max_output_channels"] > 0
        ]
    except Exception:
        return []


def get_input_devices() -> list[dict]:
    try:
        return [
            d for d in sd.query_devices()
            if d["max_input_channels"] > 0
        ]
    except Exception:
        return []


def device_by_name(name: str) -> Optional[dict]:
    try:
        for d in sd.query_devices():
            if d["name"] == name:
                return d
    except Exception:
        pass
    return None


def device_channel_count(device_name: str, kind: str = "input") -> int:
    d = device_by_name(device_name)
    if d is None:
        return 0
    return d[f"max_{kind}_channels"]


def _extract_aligned_sweep_recording(
    recording: np.ndarray,
    sweep: np.ndarray,
) -> np.ndarray:
    """
    Find the sweep's actual start in the recording and return the aligned slice.

    This avoids buffer-size-dependent truncation when stream latency shifts the
    captured sweep away from the nominal pre-silence boundary.
    """
    rec = recording.astype(np.float64, copy=False)
    sw = sweep.astype(np.float64, copy=False)

    if len(rec) < len(sw):
        raise ValueError("Recording shorter than expected.")

    full_len = len(rec) + len(sw) - 1
    nfft = int(2 ** np.ceil(np.log2(full_len)))

    # Convolution with the time-reversed sweep gives us the valid correlation
    # sequence, offset by len(sw) - 1 samples.
    corr_full = np.fft.irfft(
        np.fft.rfft(rec, n=nfft) * np.fft.rfft(sw[::-1], n=nfft),
        n=nfft,
    )[:full_len]
    corr_valid = corr_full[len(sw) - 1: len(rec)]

    if len(corr_valid) == 0:
        raise ValueError("Unable to align recording to sweep.")

    start_idx = int(np.argmax(np.abs(corr_valid)))
    end_idx = start_idx + len(sw)
    if end_idx > len(rec):
        raise ValueError("Aligned recording shorter than expected.")

    return rec[start_idx:end_idx].astype(np.float32, copy=False)


# ---------------------------------------------------------------------------
# Level monitor — runs as a background InputStream
# ---------------------------------------------------------------------------

class LevelMonitor(QObject):
    level_updated = pyqtSignal(float)  # RMS in dBFS (-inf … 0)
    error_occurred = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._stream: Optional[sd.InputStream] = None
        self._device: Optional[str] = None
        self._channel: int = 0
        self._running = False
        self._lock = threading.Lock()

    def start(self, device_name: str, channel_index: int, fs: int,
               buffer_size: int) -> None:
        self.stop()
        with self._lock:
            self._device = device_name
            self._channel = channel_index
            self._running = True
        try:
            dev = device_by_name(device_name)
            if dev is None:
                self.error_occurred.emit(f"Device not found: {device_name}")
                return
            n_ch = dev["max_input_channels"]
            if channel_index >= n_ch:
                self.error_occurred.emit(
                    f"Channel {channel_index} not available on {device_name}"
                )
                return

            self._stream = sd.InputStream(
                device=device_name,
                channels=n_ch,
                samplerate=fs,
                blocksize=buffer_size,
                dtype="float32",
                callback=self._callback,
                finished_callback=self._on_finished,
                latency="low",
            )
            self._stream.start()
        except Exception as e:
            self._running = False
            self.error_occurred.emit(f"Level monitor error: {e}")

    def stop(self) -> None:
        with self._lock:
            self._running = False
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop(ignore_errors=True)
                stream.close(ignore_errors=True)
            except Exception:
                pass

    def _callback(self, indata: np.ndarray, frames: int,
                  time_info, status) -> None:
        with self._lock:
            if not self._running:
                return
            ch = min(self._channel, indata.shape[1] - 1)
        mono = indata[:, ch]
        rms = float(np.sqrt(np.mean(mono ** 2)))
        if rms > 0:
            db = 20.0 * np.log10(rms)
        else:
            db = -120.0
        self.level_updated.emit(db)

    def _on_finished(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Sweep worker — runs measurement in background thread
# ---------------------------------------------------------------------------

class SweepWorker(QObject):
    finished = pyqtSignal(np.ndarray, np.ndarray)   # recording, sweep
    error = pyqtSignal(str)
    progress = pyqtSignal(float)                     # 0.0 … 1.0

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._abort = threading.Event()

    def abort(self) -> None:
        self._abort.set()

    def run(
        self,
        sweep: np.ndarray,
        output_device: str,
        input_device: str,
        input_channel: int,
        fs: int,
        buffer_size: int,
        pre_silence: float = 0.2,
        post_silence: float = 0.5,
        latency: str = "low",
    ) -> None:
        """Call from a QThread or thread pool."""
        self._abort.clear()
        try:
            self._run_inner(
                sweep, output_device, input_device, input_channel,
                fs, buffer_size, pre_silence, post_silence, latency,
            )
        except sd.PortAudioError as e:
            self.error.emit(f"PortAudio error: {e}")
        except Exception as e:
            self.error.emit(f"Sweep error: {e}")

    def _run_inner(
        self, sweep, output_device, input_device, input_channel,
        fs, buffer_size, pre_silence, post_silence, latency,
    ) -> None:
        in_dev = device_by_name(input_device)
        out_dev = device_by_name(output_device)
        if in_dev is None:
            self.error.emit(f"Input device unavailable: {input_device}")
            return
        if out_dev is None:
            self.error.emit(f"Output device unavailable: {output_device}")
            return

        n_in_ch = in_dev["max_input_channels"]
        n_out_ch = out_dev["max_output_channels"]

        if input_channel >= n_in_ch:
            self.error.emit(
                f"Input channel {input_channel} not available "
                f"(device has {n_in_ch} ch)."
            )
            return

        pre_n = int(pre_silence * fs)
        post_n = int(post_silence * fs)
        sweep_n = len(sweep)
        total_n = pre_n + sweep_n + post_n

        # Build output signal (stereo if needed, sweep on both channels)
        if n_out_ch >= 2:
            out_signal = np.zeros((total_n, 2), dtype=np.float32)
            out_signal[pre_n: pre_n + sweep_n, 0] = sweep
            out_signal[pre_n: pre_n + sweep_n, 1] = sweep
        else:
            out_signal = np.zeros((total_n, 1), dtype=np.float32)
            out_signal[pre_n: pre_n + sweep_n, 0] = sweep

        if self._abort.is_set():
            return

        try:
            recording = sd.playrec(
                out_signal,
                samplerate=fs,
                input_mapping=[input_channel + 1],  # 1-based
                device=(input_device, output_device),
                dtype="float32",
                blocksize=buffer_size,
                latency=latency,
            )
        except sd.PortAudioError as e:
            self.error.emit(f"PortAudio error starting stream: {e}")
            return

        # Poll for completion with abort support
        total_time = total_n / fs
        start = time.monotonic()
        while True:
            if self._abort.is_set():
                try:
                    sd.stop()
                except Exception:
                    pass
                return
            elapsed = time.monotonic() - start
            self.progress.emit(min(elapsed / total_time, 0.99))
            if elapsed >= total_time + 0.1:
                break
            time.sleep(0.05)

        try:
            sd.wait()
        except Exception:
            pass

        self.progress.emit(1.0)

        # Extract the recording aligned to the actual sweep start.
        rec_mono = recording[:, 0]
        try:
            sweep_rec = _extract_aligned_sweep_recording(rec_mono, sweep)
        except ValueError as exc:
            self.error.emit(str(exc))
            return

        self.finished.emit(sweep_rec, sweep)
