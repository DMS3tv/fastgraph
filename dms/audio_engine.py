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


def _normalized_corrcoef_valid(signal: np.ndarray, pattern: np.ndarray) -> np.ndarray:
    """
    Return valid normalized cross-correlation coefficient sequence.
    Each lag is normalized by local signal-window energy and pattern energy.
    """
    corr = _normalized_corr_valid(signal, pattern).astype(np.float64, copy=False)
    sig = signal.astype(np.float64, copy=False)
    pat = pattern.astype(np.float64, copy=False)
    m = len(pat)
    if m <= 0 or len(sig) < m:
        return np.array([], dtype=np.float64)

    pat_norm = float(np.sqrt(np.sum(np.square(pat))))
    if pat_norm <= 1e-12:
        return np.array([], dtype=np.float64)

    sig_sq = np.square(sig)
    csum = np.concatenate(([0.0], np.cumsum(sig_sq)))
    win_energy = csum[m:] - csum[:-m]
    win_norm = np.sqrt(np.maximum(win_energy, 1e-24))

    denom = np.maximum(win_norm * pat_norm, 1e-12)
    return corr / denom


def _peak_to_rms_confidence(values: np.ndarray) -> float:
    if len(values) == 0:
        return 0.0
    peak = float(np.max(np.abs(values)))
    rms = float(np.sqrt(np.mean(np.square(values))))
    return peak / max(rms, 1e-12)


def _peak_to_background_confidence(
    values: np.ndarray,
    peak_idx: int,
    exclusion_radius: int,
) -> float:
    """
    Confidence metric using sidelobe floor instead of full-trace RMS.
    This is more robust when Bluetooth codec ringing broadens correlation energy.
    """
    if len(values) == 0:
        return 0.0
    mag = np.abs(values).astype(np.float64, copy=False)
    peak_idx = int(np.clip(peak_idx, 0, len(mag) - 1))
    peak = float(mag[peak_idx])

    lo = max(0, peak_idx - int(exclusion_radius))
    hi = min(len(mag), peak_idx + int(exclusion_radius) + 1)
    if lo >= hi:
        return peak / 1e-12

    mask = np.ones(len(mag), dtype=bool)
    mask[lo:hi] = False
    bg = mag[mask]
    if len(bg) < 16:
        floor = float(np.sqrt(np.mean(np.square(mag))))
    else:
        floor = float(np.sqrt(np.mean(np.square(bg))))
    return peak / max(floor, 1e-12)


def _peak_to_nextbest_confidence(
    values: np.ndarray,
    peak_idx: int,
    exclusion_radius: int,
) -> float:
    """
    Confidence metric comparing the best peak with the next-best alternative.
    Useful when broad codec artifacts raise overall correlation floor.
    """
    if len(values) == 0:
        return 0.0
    mag = np.abs(values).astype(np.float64, copy=False)
    peak_idx = int(np.clip(peak_idx, 0, len(mag) - 1))
    peak = float(mag[peak_idx])

    lo = max(0, peak_idx - int(exclusion_radius))
    hi = min(len(mag), peak_idx + int(exclusion_radius) + 1)
    mask = np.ones(len(mag), dtype=bool)
    mask[lo:hi] = False
    others = mag[mask]
    if len(others) == 0:
        return peak / 1e-12
    next_best = float(np.max(others))
    return peak / max(next_best, 1e-12)


def _build_end_marker(fs: int) -> np.ndarray:
    """
    Build a short broadband chirp marker used to validate end timing.
    """
    dur_s = 0.032
    n = max(8, int(round(dur_s * fs)))
    t = np.arange(n, dtype=np.float64) / float(fs)
    # Keep energy in a Bluetooth/ANC-friendly range.
    f0 = 1400.0
    f1 = 6800.0
    k = (f1 - f0) / max(dur_s, 1e-9)
    phase = 2.0 * np.pi * (f0 * t + 0.5 * k * t * t)
    marker = np.sin(phase)
    marker *= np.hanning(n)
    return (0.60 * marker).astype(np.float32)


def _build_start_marker(fs: int) -> np.ndarray:
    """
    Build a short chirp marker used to lock sweep start timing robustly.
    """
    dur_s = 0.022
    n = max(8, int(round(dur_s * fs)))
    t = np.arange(n, dtype=np.float64) / float(fs)
    f0 = 1200.0
    f1 = 5600.0
    k = (f1 - f0) / max(dur_s, 1e-9)
    phase = 2.0 * np.pi * (f0 * t + 0.5 * k * t * t)
    marker = np.sin(phase)
    marker *= np.hanning(n)
    return (0.45 * marker).astype(np.float32)


def _build_wake_primer(fs: int) -> np.ndarray:
    """
    Build a short non-measurement primer to wake Bluetooth headphones before sweep start.
    """
    dur_s = 0.20
    n = max(16, int(round(dur_s * fs)))
    t = np.arange(n, dtype=np.float64) / float(fs)
    tone = (
        0.5 * np.sin(2.0 * np.pi * 900.0 * t)
        + 0.35 * np.sin(2.0 * np.pi * 2300.0 * t)
        + 0.25 * np.sin(2.0 * np.pi * 5200.0 * t)
    )
    tone *= np.hanning(n)
    return (0.30 * tone).astype(np.float32)


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
        bluetooth_headphone_mode: bool = False,
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
                bluetooth_headphone_mode,
                start_alignment_confidence_min, end_marker_confidence_min, timing_drift_max_ms,
            )
        except sd.PortAudioError as e:
            self.error.emit(f"PortAudio error: {e}")
        except Exception as e:
            self.error.emit(f"Sweep error: {e}")

    def _run_inner(
        self, sweep, output_device, input_device, input_channel,
        fs, buffer_size, pre_silence, post_silence, latency, bluetooth_headphone_mode,
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
        primer_gap_n = int(round(0.24 * fs))
        wake_primer = _build_wake_primer(fs) if bool(bluetooth_headphone_mode) else None
        primer_n = len(wake_primer) if wake_primer is not None else 0
        sweep_n = len(sweep)
        start_marker = _build_start_marker(fs)
        start_marker_gap_n = int(round(0.025 * fs))
        marker = _build_end_marker(fs)
        marker_gap_n = int(round(0.03 * fs))
        marker_pair_gap_n = int(round(0.05 * fs))
        excitation = np.concatenate(
            [
                start_marker,
                np.zeros(start_marker_gap_n, dtype=np.float32),
                sweep.astype(np.float32, copy=False),
                np.zeros(marker_gap_n, dtype=np.float32),
                marker,
                np.zeros(marker_pair_gap_n, dtype=np.float32),
                marker,
            ]
        )
        lead_n = primer_n + (primer_gap_n if wake_primer is not None else 0)
        sweep_start_n = lead_n + pre_n
        total_n = sweep_start_n + len(excitation) + post_n

        # Build output signal (stereo if needed, sweep on both channels)
        if n_out_ch >= 2:
            out_signal = np.zeros((total_n, 2), dtype=np.float32)
            if wake_primer is not None:
                out_signal[:primer_n, 0] = wake_primer
                out_signal[:primer_n, 1] = wake_primer
            out_signal[sweep_start_n: sweep_start_n + len(excitation), 0] = excitation
            out_signal[sweep_start_n: sweep_start_n + len(excitation), 1] = excitation
        else:
            out_signal = np.zeros((total_n, 1), dtype=np.float32)
            if wake_primer is not None:
                out_signal[:primer_n, 0] = wake_primer
            out_signal[sweep_start_n: sweep_start_n + len(excitation), 0] = excitation

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
            corr_valid = _normalized_corrcoef_valid(
                rec_mono, sweep.astype(np.float32, copy=False)
            )
            if len(corr_valid) == 0:
                raise ValueError("Unable to align recording to sweep.")

            # Search only around the expected sweep start to avoid false peaks.
            # Bluetooth paths can have larger fixed latency, so give them extra room.
            # Bluetooth can introduce large and variable transport delay.
            # Keep a broad search window so alignment does not miss true onset.
            max_extra_latency_s = 2.0 if str(latency).lower() == "high" else 1.2
            start_search_radius = int(round(max_extra_latency_s * fs))
            start_search_lo = max(0, sweep_start_n - start_search_radius)
            start_search_hi = min(len(corr_valid), sweep_start_n + start_search_radius)
            if start_search_hi - start_search_lo < 32:
                start_search_lo = 0
                start_search_hi = len(corr_valid)
            corr_start = corr_valid[start_search_lo:start_search_hi]
            start_offset = int(np.argmax(np.abs(corr_start)))
            sweep_start_candidate = start_search_lo + start_offset
            start_idx = sweep_start_candidate

            exclusion = max(int(round(0.025 * fs)), int(round(0.01 * sweep_n)))
            conf_bg = _peak_to_background_confidence(
                corr_valid, start_idx, exclusion
            )
            conf_next = _peak_to_nextbest_confidence(
                corr_valid, start_idx, exclusion
            )
            start_conf = min(conf_bg, conf_next)
            min_start_conf = float(start_alignment_confidence_min)
            if bool(bluetooth_headphone_mode):
                # Wireless codec/ANC paths can depress sweep-correlation confidence.
                min_start_conf = min(min_start_conf, 3.0)
            # Prefer dedicated start-marker detection to locate true sweep onset.
            start_marker_search = int(round(0.35 * fs))
            sm_lo = max(0, start_idx - start_marker_search)
            sm_hi = min(len(rec_mono), start_idx + start_marker_search + len(start_marker))
            sm_region = rec_mono[sm_lo:sm_hi]
            start_marker_conf = 0.0
            sm_corr = _normalized_corr_valid(sm_region, start_marker)
            if len(sm_corr) > 0:
                sm_offset = int(np.argmax(np.abs(sm_corr)))
                sm_idx = sm_lo + sm_offset
                start_marker_conf = _peak_to_rms_confidence(sm_corr)
                if start_marker_conf >= max(3.5, min_start_conf * 0.7):
                    marker_locked_start = sm_idx + len(start_marker) + start_marker_gap_n
                    max_start_idx = len(rec_mono) - sweep_n
                    if marker_locked_start <= max_start_idx:
                        start_idx = marker_locked_start
                    # Marker-backed lock: relax low sweep-only confidence failure.
                    start_conf = max(start_conf, min_start_conf)

            if start_conf < min_start_conf:
                raise ValueError(
                    f"Low start-alignment confidence ({start_conf:.1f}; marker {start_marker_conf:.1f}). "
                    "Please reduce noise, increase playback level, or use higher latency."
                )

            max_start_idx = len(rec_mono) - sweep_n
            if max_start_idx < 0:
                raise ValueError("Recording shorter than expected.")

            candidates = [int(np.clip(sweep_start_candidate, 0, max_start_idx))]
            if start_idx != sweep_start_candidate:
                candidates.append(int(np.clip(start_idx, 0, max_start_idx)))

            marker_search = int(round(0.14 * fs)) if bool(bluetooth_headphone_mode) else int(round(0.08 * fs))
            end_conf_min = float(end_marker_confidence_min)
            if bool(bluetooth_headphone_mode):
                end_conf_min = min(end_conf_min, 2.5)
            best_result = None
            best_err_result = None
            for cand in candidates:
                expected_marker_1 = cand + sweep_n + marker_gap_n
                expected_marker_2 = expected_marker_1 + len(marker) + marker_pair_gap_n

                search_start_1 = max(0, expected_marker_1 - marker_search)
                search_stop_1 = min(len(rec_mono), expected_marker_1 + marker_search + len(marker))
                marker_region_1 = rec_mono[search_start_1:search_stop_1]
                marker_corr_1 = _normalized_corr_valid(marker_region_1, marker)
                if len(marker_corr_1) == 0:
                    continue
                marker_offset_1 = int(np.argmax(np.abs(marker_corr_1)))
                marker_start_1 = search_start_1 + marker_offset_1
                timing_err_1 = abs(marker_start_1 - expected_marker_1)
                marker_conf_1 = _peak_to_rms_confidence(marker_corr_1)

                search_start_2 = max(0, expected_marker_2 - marker_search)
                search_stop_2 = min(len(rec_mono), expected_marker_2 + marker_search + len(marker))
                marker_region_2 = rec_mono[search_start_2:search_stop_2]
                marker_corr_2 = _normalized_corr_valid(marker_region_2, marker)
                if len(marker_corr_2) > 0:
                    marker_offset_2 = int(np.argmax(np.abs(marker_corr_2)))
                    marker_start_2 = search_start_2 + marker_offset_2
                    timing_err_2 = abs(marker_start_2 - expected_marker_2)
                    marker_conf_2 = _peak_to_rms_confidence(marker_corr_2)
                else:
                    marker_start_2 = marker_start_1
                    timing_err_2 = timing_err_1
                    marker_conf_2 = marker_conf_1

                timing_err = max(timing_err_1, timing_err_2)
                marker_conf = min(marker_conf_1, marker_conf_2)
                spacing_expected = len(marker) + marker_pair_gap_n
                spacing_observed = abs(marker_start_2 - marker_start_1)
                spacing_err = abs(spacing_observed - spacing_expected)
                score = marker_conf - (timing_err / max(1.0, 0.01 * fs)) - (spacing_err / max(1.0, 0.01 * fs))
                result = (
                    score,
                    cand,
                    marker_start_1,
                    marker_start_2,
                    timing_err,
                    marker_conf,
                    spacing_err,
                )
                if best_result is None or result[0] > best_result[0]:
                    best_result = result
                # Prefer minimum drift candidate when confidence is acceptable.
                if marker_conf >= max(1.8, end_conf_min * 0.85):
                    if (
                        best_err_result is None
                        or timing_err < best_err_result[4]
                        or (
                            timing_err == best_err_result[4]
                            and spacing_err < best_err_result[6]
                        )
                    ):
                        best_err_result = result

            if best_result is None and best_err_result is None:
                raise ValueError("Unable to verify end marker timing.")

            chosen = best_err_result if best_err_result is not None else best_result
            _, start_idx, marker_start_1, marker_start_2, timing_err, marker_conf, _spacing_err = chosen
            end_idx = start_idx + sweep_n
            if end_idx > len(rec_mono):
                raise ValueError(
                    "Aligned recording shorter than expected. "
                    "Please increase post-sweep silence or use Bluetooth mode."
                )
            sweep_rec = rec_mono[start_idx:end_idx].astype(np.float32, copy=False)

            if marker_conf < end_conf_min:
                raise ValueError(
                    f"Low end-marker confidence ({marker_conf:.1f}). "
                    "Timing reliability is low; retrying is recommended."
                )

            max_drift_samples = int(round((float(timing_drift_max_ms) / 1000.0) * fs))
            if timing_err > max_drift_samples:
                ms = 1000.0 * timing_err / float(fs)
                if bool(bluetooth_headphone_mode):
                    hint = "Please retry; Bluetooth timing jitter exceeded the current tolerance."
                else:
                    hint = "Please retry and consider high latency mode."
                raise ValueError(
                    f"Timing drift too large ({ms:.1f} ms). "
                    f"{hint}"
                )
            # In Bluetooth mode, timing drift is the primary reliability gate.
            # Keep confidence metrics informative, but avoid failing solely on low marker confidence
            # after drift has already passed.
            if bool(bluetooth_headphone_mode):
                marker_conf = max(marker_conf, end_conf_min)
            drift_ms = 1000.0 * timing_err / float(fs)

            # Estimate simple measurement SNR from ambient segments near the sweep.
            noise_win_n = int(round(0.12 * fs))
            pre_noise = rec_mono[max(0, start_idx - noise_win_n):start_idx]
            post_noise_start = marker_start_2 + len(marker)
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
