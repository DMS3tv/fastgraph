"""
Audio engine: device enumeration, level monitoring, sweep play/record.
Thread-safe; all callbacks communicate via Qt signals.
"""

import time
import threading
import os
from dataclasses import replace
from typing import Any, Optional, Callable

import numpy as np
import sounddevice as sd
from PyQt6.QtCore import QObject, pyqtSignal

from dms.measurement_alignment import (
    AlignmentSettings,
    MeasurementAlignmentError,
    align_recording_to_layout,
)
from dms.measurement_layout import build_measurement_layout, build_output_signal


# ---------------------------------------------------------------------------
# Device helpers
# ---------------------------------------------------------------------------

WINDOWS_HOST_API_PRIORITY = (
    "windows wasapi",
    "windows wdm-ks",
    "windows directsound",
    "mme",
)


def is_windows_audio_host() -> bool:
    return os.name == "nt"


def refresh_audio_backend() -> bool:
    """Force PortAudio to rebuild device/session state when supported."""
    try:
        terminate = getattr(sd, "_terminate", None)
        initialize = getattr(sd, "_initialize", None)
        if not callable(terminate) or not callable(initialize):
            return False
        terminate()
        initialize()
        return True
    except Exception:
        return False


def _hostapi_names() -> dict[int, str]:
    try:
        return {
            idx: str(api.get("name") or f"Host API {idx}")
            for idx, api in enumerate(sd.query_hostapis())
        }
    except Exception:
        return {}


def _device_descriptor(
    index: int,
    device: dict,
    hostapi_names: dict[int, str],
    kind: Optional[str] = None,
) -> dict[str, Any]:
    hostapi = int(device.get("hostapi", -1))
    descriptor = {
        "index": int(index),
        "name": str(device.get("name") or f"Device {index}"),
        "hostapi": hostapi,
        "hostapi_name": hostapi_names.get(hostapi, f"Host API {hostapi}"),
        "max_input_channels": int(device.get("max_input_channels", 0) or 0),
        "max_output_channels": int(device.get("max_output_channels", 0) or 0),
    }
    if kind is not None:
        descriptor["kind"] = kind
    return descriptor


def _devices_for_kind(kind: str) -> list[dict[str, Any]]:
    try:
        hostapi_names = _hostapi_names()
        key = f"max_{kind}_channels"
        return [
            _device_descriptor(idx, d, hostapi_names, kind=kind)
            for idx, d in enumerate(sd.query_devices())
            if int(d.get(key, 0) or 0) > 0
        ]
    except Exception:
        return []


def get_output_devices() -> list[dict]:
    return _devices_for_kind("output")


def get_input_devices() -> list[dict]:
    return _devices_for_kind("input")


def _windows_hostapi_rank(hostapi_name: str) -> tuple[int, str]:
    normalized = " ".join(str(hostapi_name or "").casefold().split())
    for idx, preferred in enumerate(WINDOWS_HOST_API_PRIORITY):
        if normalized == preferred:
            return idx, normalized
    return len(WINDOWS_HOST_API_PRIORITY), normalized


def preferred_windows_hostapi(
    input_devices: list[dict],
    output_devices: list[dict],
) -> Optional[int]:
    input_hostapis = {int(d.get("hostapi", -1)) for d in input_devices}
    output_hostapis = {int(d.get("hostapi", -1)) for d in output_devices}
    compatible_hostapis = input_hostapis & output_hostapis
    if not compatible_hostapis:
        return None
    candidates = []
    for device in input_devices + output_devices:
        hostapi = int(device.get("hostapi", -1))
        if hostapi in compatible_hostapis:
            candidates.append(
                (
                    _windows_hostapi_rank(str(device.get("hostapi_name") or "")),
                    hostapi,
                )
            )
    if not candidates:
        return None
    return min(candidates)[1]


def filter_devices_by_hostapi(devices: list[dict], hostapi: Optional[int]) -> list[dict]:
    if hostapi is None:
        return list(devices)
    return [d for d in devices if int(d.get("hostapi", -1)) == int(hostapi)]


def is_compatible_device_pair(
    input_device: Optional[dict],
    output_device: Optional[dict],
) -> bool:
    if input_device is None or output_device is None:
        return False
    if not is_windows_audio_host():
        return True
    return int(input_device.get("hostapi", -1)) == int(output_device.get("hostapi", -1))


def device_label(device: dict, duplicates: Optional[set[str]] = None) -> str:
    name = str(device.get("name") or "")
    if duplicates is not None and name not in duplicates:
        return name
    hostapi_name = str(device.get("hostapi_name") or "").strip()
    if hostapi_name:
        return f"{name} ({hostapi_name})"
    return name


def duplicate_device_names(devices: list[dict]) -> set[str]:
    counts: dict[str, int] = {}
    for device in devices:
        name = str(device.get("name") or "")
        counts[name] = counts.get(name, 0) + 1
    return {name for name, count in counts.items() if count > 1}


def device_setting(device: dict, kind: str) -> dict[str, Any]:
    return {
        "index": int(device["index"]),
        "name": str(device["name"]),
        "hostapi": int(device.get("hostapi", -1)),
        "hostapi_name": str(device.get("hostapi_name") or ""),
        "kind": kind,
    }


def resolve_device_selection(
    selection: Any,
    kind: str,
    devices: Optional[list[dict]] = None,
) -> tuple[Optional[dict], bool]:
    devices = list(devices if devices is not None else _devices_for_kind(kind))
    if selection is None:
        return None, False

    if isinstance(selection, dict):
        want_index = selection.get("index")
        want_name = str(selection.get("name") or "")
        want_kind = selection.get("kind")
        want_hostapi = selection.get("hostapi")

        if want_kind and want_kind != kind:
            return None, False

        if want_index is not None:
            try:
                index = int(want_index)
            except (TypeError, ValueError):
                index = None
            if index is not None:
                for device in devices:
                    if int(device["index"]) != index:
                        continue
                    if want_name and str(device["name"]) != want_name:
                        continue
                    if want_hostapi is not None and int(device.get("hostapi", -1)) != int(want_hostapi):
                        continue
                    return device, False

        if want_name and want_hostapi is not None:
            matches = [
                d for d in devices
                if str(d["name"]) == want_name
                and int(d.get("hostapi", -1)) == int(want_hostapi)
            ]
            if len(matches) == 1:
                return matches[0], False
            if len(matches) > 1:
                return None, True

        if want_name:
            matches = [d for d in devices if str(d["name"]) == want_name]
            if len(matches) == 1:
                return matches[0], False
            if len(matches) > 1:
                return None, True
        return None, False

    if isinstance(selection, int):
        for device in devices:
            if int(device["index"]) == int(selection):
                return device, False
        return None, False

    name = str(selection)
    matches = [d for d in devices if str(d["name"]) == name]
    if len(matches) == 1:
        return matches[0], False
    if len(matches) > 1:
        return None, True
    return None, False


def device_by_index(index: int, kind: Optional[str] = None) -> Optional[dict]:
    try:
        devices = _devices_for_kind(kind) if kind else [
            _device_descriptor(idx, d, _hostapi_names())
            for idx, d in enumerate(sd.query_devices())
        ]
        for device in devices:
            if int(device["index"]) == int(index):
                return device
    except Exception:
        pass
    return None


def device_by_name(name: str, kind: Optional[str] = None) -> Optional[dict]:
    device, ambiguous = resolve_device_selection(name, kind or "input")
    if ambiguous:
        return None
    return device


def device_channel_count(device: Any, kind: str = "input") -> int:
    if isinstance(device, dict):
        d = device
    elif isinstance(device, int):
        d = device_by_index(device, kind=kind)
    else:
        d, ambiguous = resolve_device_selection(device, kind)
        if ambiguous:
            return 0
    if d is None:
        return 0
    return d[f"max_{kind}_channels"]


# ---------------------------------------------------------------------------
# Level monitor — runs as a background InputStream
# ---------------------------------------------------------------------------

class LevelMonitor(QObject):
    level_updated = pyqtSignal(float)  # RMS in dBFS (-inf … 0)
    error_occurred = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._stream: Optional[sd.InputStream] = None
        self._device: Optional[int] = None
        self._channel: int = 0
        self._running = False
        self._lock = threading.Lock()

    def start(
        self,
        device_index: int,
        device_label: str,
        channel_index: int,
        fs: int,
        buffer_size: int,
    ) -> None:
        self.stop()
        with self._lock:
            self._device = device_index
            self._channel = channel_index
            self._running = True
        try:
            dev = device_by_index(device_index, kind="input")
            if dev is None:
                self.error_occurred.emit(f"Device not found: {device_label}")
                return
            n_ch = dev["max_input_channels"]
            if channel_index >= n_ch:
                self.error_occurred.emit(
                    f"Channel {channel_index} not available on {device_label}"
                )
                return

            self._stream = sd.InputStream(
                device=device_index,
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
    measurement_diagnostics = pyqtSignal(object)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._abort = threading.Event()

    def abort(self) -> None:
        self._abort.set()

    def run(
        self,
        sweep: np.ndarray,
        output_device: int,
        input_device: int,
        input_channel: int,
        fs: int,
        buffer_size: int,
        output_device_label: str = "",
        input_device_label: str = "",
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
                output_device_label, input_device_label,
                bluetooth_headphone_mode,
                start_alignment_confidence_min, end_marker_confidence_min, timing_drift_max_ms,
            )
        except sd.PortAudioError as e:
            self.error.emit(f"PortAudio error: {e}")
        except Exception as e:
            self.error.emit(f"Sweep error: {e}")

    def _run_inner(
        self, sweep, output_device, input_device, input_channel,
        fs, buffer_size, pre_silence, post_silence, latency,
        output_device_label, input_device_label, bluetooth_headphone_mode,
        start_alignment_confidence_min, end_marker_confidence_min, timing_drift_max_ms,
    ) -> None:
        input_device_label = input_device_label or str(input_device)
        output_device_label = output_device_label or str(output_device)
        in_dev = device_by_index(input_device, kind="input")
        out_dev = device_by_index(output_device, kind="output")
        if in_dev is None:
            self.error.emit(f"Input device unavailable: {input_device_label}")
            return
        if out_dev is None:
            self.error.emit(f"Output device unavailable: {output_device_label}")
            return

        n_in_ch = in_dev["max_input_channels"]
        n_out_ch = out_dev["max_output_channels"]

        if input_channel >= n_in_ch:
            self.error.emit(
                f"Input channel {input_channel} not available "
                f"(device has {n_in_ch} ch)."
            )
            return

        layout = build_measurement_layout(
            sweep=sweep,
            fs=fs,
            pre_silence_s=pre_silence,
            post_silence_s=post_silence,
            bluetooth_headphone_mode=bluetooth_headphone_mode,
        )
        out_signal = build_output_signal(layout, n_out_ch)
        total_n = layout.total_samples

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

        try:
            alignment = align_recording_to_layout(
                rec_mono=rec_mono,
                sweep=sweep,
                layout=layout,
                settings=AlignmentSettings(
                    latency=latency,
                    bluetooth_headphone_mode=bluetooth_headphone_mode,
                    start_alignment_confidence_min=start_alignment_confidence_min,
                    end_marker_confidence_min=end_marker_confidence_min,
                    timing_drift_max_ms=timing_drift_max_ms,
                ),
            )
        except MeasurementAlignmentError as exc:
            self.measurement_diagnostics.emit(
                replace(exc.diagnostics, buffer_size=int(buffer_size))
            )
            self.error.emit(str(exc))
            return
        except ValueError as exc:
            self.error.emit(str(exc))
            return

        self.measurement_diagnostics.emit(
            replace(alignment.diagnostics, buffer_size=int(buffer_size))
        )
        self.timing_quality.emit(
            float(alignment.start.start_confidence),
            float(alignment.end.marker_confidence),
            float(alignment.end.timing_error_ms),
            float(alignment.snr_db),
        )
        self.finished.emit(alignment.aligned_recording, sweep)
