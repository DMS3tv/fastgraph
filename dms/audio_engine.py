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


def device_by_name(name: str, kind: Optional[str] = None) -> Optional[dict]:
    try:
        matches = [d for d in sd.query_devices() if d["name"] == name]
        if not matches:
            return None
        if kind == "input":
            input_matches = [d for d in matches if d.get("max_input_channels", 0) > 0]
            if input_matches:
                return max(input_matches, key=lambda d: int(d.get("max_input_channels", 0)))
        elif kind == "output":
            output_matches = [d for d in matches if d.get("max_output_channels", 0) > 0]
            if output_matches:
                return max(output_matches, key=lambda d: int(d.get("max_output_channels", 0)))
        return matches[0]
    except Exception:
        pass
    return None


def device_channel_count(device_name: str, kind: str = "input") -> int:
    d = device_by_name(device_name, kind=kind)
    if d is None:
        return 0
    return d[f"max_{kind}_channels"]


def _normalized_corr_valid(signal: np.ndarray, pattern: np.ndarray) -> np.ndarray:
    """Return valid cross-correlation sequence between signal and pattern."""
    sig = signal.astype(np.float64, copy=False)
    pat = pattern.astype(np.float64, copy=False)
    full_len = len(sig) + len(pat) - 1
    nfft = int(2 ** np.ceil(np.log2(full_len)))
    corr_full = np.fft.irfft(
        np.fft.rfft(sig, n=nfft) * np.fft.rfft(pat[::-1], n=nfft),
        n=nfft,
    )[:full_len]
    return corr_full[len(pat) - 1: len(sig)]


def _peak_to_rms_confidence(values: np.ndarray) -> float:
    if len(values) == 0:
        return 0.0
    peak = float(np.max(np.abs(values)))
    rms = float(np.sqrt(np.mean(np.square(values))))
    return peak / max(rms, 1e-12)


def _build_end_marker(fs: int) -> np.ndarray:
    """
    Build a short broadband chirp marker used to validate end timing.
    """
    dur_s = 0.018
    n = max(8, int(round(dur_s * fs)))
    t = np.arange(n, dtype=np.float64) / float(fs)
    f0 = 3500.0
    f1 = 10500.0
    k = (f1 - f0) / max(dur_s, 1e-9)
    phase = 2.0 * np.pi * (f0 * t + 0.5 * k * t * t)
    marker = np.sin(phase)
    marker *= np.hanning(n)
    return (0.5 * marker).astype(np.float32)


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
            dev = device_by_name(device_name, kind="input")
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
    timing_quality = pyqtSignal(float, float, float, float)  # start_conf, end_conf, drift_ms, snr_db

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
        start_alignment_confidence_min: float = 9.0,
        end_marker_confidence_min: float = 7.0,
        timing_drift_max_ms: float = 35.0,
    ) -> None:
        """Call from a QThread or thread pool."""
        self._abort.clear()
        try:
            self._run_inner(
                sweep, output_device, input_device, input_channel,
                fs, buffer_size, pre_silence, post_silence, latency,
                start_alignment_confidence_min, end_marker_confidence_min, timing_drift_max_ms,
            )
        except sd.PortAudioError as e:
            self.error.emit(f"PortAudio error: {e}")
        except Exception as e:
            self.error.emit(f"Sweep error: {e}")

    def _run_inner(
        self, sweep, output_device, input_device, input_channel,
        fs, buffer_size, pre_silence, post_silence, latency,
        start_alignment_confidence_min, end_marker_confidence_min, timing_drift_max_ms,
    ) -> None:
        in_dev = device_by_name(input_device, kind="input")
        out_dev = device_by_name(output_device, kind="output")
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
        marker = _build_end_marker(fs)
        marker_gap_n = int(round(0.03 * fs))
        excitation = np.concatenate(
            [sweep.astype(np.float32, copy=False), np.zeros(marker_gap_n, dtype=np.float32), marker]
        )
        total_n = pre_n + len(excitation) + post_n

        # Build output signal (stereo if needed, sweep on both channels)
        if n_out_ch >= 2:
            out_signal = np.zeros((total_n, 2), dtype=np.float32)
            out_signal[pre_n: pre_n + len(excitation), 0] = excitation
            out_signal[pre_n: pre_n + len(excitation), 1] = excitation
        else:
            out_signal = np.zeros((total_n, 1), dtype=np.float32)
            out_signal[pre_n: pre_n + len(excitation), 0] = excitation

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

        rec_mono = recording[:, 0]
        if len(rec_mono) < sweep_n:
            self.error.emit("Recording shorter than expected.")
            return

        try:
            corr_valid = _normalized_corr_valid(rec_mono, sweep.astype(np.float32, copy=False))
            if len(corr_valid) == 0:
                raise ValueError("Unable to align recording to sweep.")
            start_conf = _peak_to_rms_confidence(corr_valid)
            if start_conf < float(start_alignment_confidence_min):
                raise ValueError(
                    f"Low start-alignment confidence ({start_conf:.1f}). "
                    "Please reduce noise, increase playback level, or use higher latency."
                )

            start_idx = int(np.argmax(np.abs(corr_valid)))
            end_idx = start_idx + sweep_n
            if end_idx > len(rec_mono):
                raise ValueError("Aligned recording shorter than expected.")
            sweep_rec = rec_mono[start_idx:end_idx].astype(np.float32, copy=False)

            # Validate end timing using a dedicated marker near the sweep end.
            expected_marker = start_idx + sweep_n + marker_gap_n
            marker_search = int(round(0.08 * fs))
            search_start = max(0, expected_marker - marker_search)
            search_stop = min(len(rec_mono), expected_marker + marker_search + len(marker))
            marker_region = rec_mono[search_start:search_stop]
            marker_corr = _normalized_corr_valid(marker_region, marker)
            if len(marker_corr) == 0:
                raise ValueError("Unable to verify end marker timing.")
            marker_conf = _peak_to_rms_confidence(marker_corr)
            if marker_conf < float(end_marker_confidence_min):
                raise ValueError(
                    f"Low end-marker confidence ({marker_conf:.1f}). "
                    "Timing reliability is low; retrying is recommended."
                )

            marker_offset = int(np.argmax(np.abs(marker_corr)))
            marker_start = search_start + marker_offset
            timing_err = abs(marker_start - expected_marker)
            max_drift_samples = int(round((float(timing_drift_max_ms) / 1000.0) * fs))
            if timing_err > max_drift_samples:
                ms = 1000.0 * timing_err / float(fs)
                raise ValueError(
                    f"Timing drift too large ({ms:.1f} ms). "
                    "Please retry and consider high latency mode."
                )
            drift_ms = 1000.0 * timing_err / float(fs)

            # Estimate simple measurement SNR from ambient segments near the sweep.
            noise_win_n = int(round(0.12 * fs))
            pre_noise = rec_mono[max(0, start_idx - noise_win_n):start_idx]
            post_noise_start = marker_start + len(marker)
            post_noise = rec_mono[
                post_noise_start:min(len(rec_mono), post_noise_start + noise_win_n)
            ]
            noise_parts = [seg for seg in (pre_noise, post_noise) if len(seg) > 8]
            if noise_parts:
                noise_concat = np.concatenate(noise_parts)
                noise_rms = float(np.sqrt(np.mean(np.square(noise_concat))))
            else:
                noise_rms = 0.0
            signal_rms = float(np.sqrt(np.mean(np.square(sweep_rec))))
            if noise_rms > 1e-12 and signal_rms > 0.0:
                snr_db = 20.0 * np.log10(signal_rms / noise_rms)
            elif signal_rms > 0.0:
                snr_db = 120.0
            else:
                snr_db = 0.0
        except ValueError as exc:
            self.error.emit(str(exc))
            return

        self.timing_quality.emit(
            float(start_conf), float(marker_conf), float(drift_ms), float(snr_db)
        )
        self.finished.emit(sweep_rec, sweep)
