"""
Audio engine: device enumeration, level monitoring, sweep play/record.
Thread-safe; all callbacks communicate via Qt signals.
"""

import contextlib
import logging
import os
import threading
import time
from dataclasses import replace
from typing import Any

import numpy as np
import sounddevice as sd
from PyQt6.QtCore import QCoreApplication, QObject, QThread, QTimer, pyqtSignal, pyqtSlot

from dms.measurement_alignment import (
    AlignmentSettings,
    MeasurementAlignmentError,
    align_recording_to_layout,
)
from dms.measurement_layout import build_measurement_layout, build_output_signal
from dms.processing import F_HIGH, F_LOW
from dms.recording_dump import save_failed_recording

logger = logging.getLogger(__name__)

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
        # _terminate/_initialize are private sounddevice API with no documented errors.
        logger.warning("Audio backend refresh failed", exc_info=True)
        return False


def _hostapi_names() -> dict[int, str]:
    try:
        return {
            idx: str(api.get("name") or f"Host API {idx}")
            for idx, api in enumerate(sd.query_hostapis())
        }
    except sd.PortAudioError:
        logger.warning("Could not list audio host APIs", exc_info=True)
        return {}


def _device_descriptor(
    index: int,
    device: dict,
    hostapi_names: dict[int, str],
    kind: str | None = None,
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
    except (sd.PortAudioError, ValueError):
        logger.warning("Could not list %s devices", kind, exc_info=True)
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
) -> int | None:
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


def filter_devices_by_hostapi(devices: list[dict], hostapi: int | None) -> list[dict]:
    if hostapi is None:
        return list(devices)
    return [d for d in devices if int(d.get("hostapi", -1)) == int(hostapi)]


def is_compatible_device_pair(
    input_device: dict | None,
    output_device: dict | None,
) -> bool:
    if input_device is None or output_device is None:
        return False
    if not is_windows_audio_host():
        return True
    return int(input_device.get("hostapi", -1)) == int(output_device.get("hostapi", -1))


def device_label(device: dict, duplicates: set[str] | None = None) -> str:
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


def _unique_match(matches: list[dict]) -> tuple[dict | None, bool]:
    """Return the single match, or (None, ambiguous)."""
    if len(matches) == 1:
        return matches[0], False
    return None, len(matches) > 1


def _match_by_index(
    devices: list[dict], want_index: Any, want_name: str, want_hostapi: Any
) -> dict | None:
    try:
        index = int(want_index)
    except (TypeError, ValueError):
        return None
    for device in devices:
        if int(device["index"]) != index:
            continue
        if want_name and str(device["name"]) != want_name:
            continue
        if want_hostapi is not None and int(device.get("hostapi", -1)) != int(want_hostapi):
            continue
        return device
    return None


def _resolve_dict_selection(
    selection: dict, kind: str, devices: list[dict]
) -> tuple[dict | None, bool]:
    want_index = selection.get("index")
    want_name = str(selection.get("name") or "")
    want_kind = selection.get("kind")
    want_hostapi = selection.get("hostapi")

    if want_kind and want_kind != kind:
        return None, False

    if want_index is not None:
        device = _match_by_index(devices, want_index, want_name, want_hostapi)
        if device is not None:
            return device, False

    if want_name and want_hostapi is not None:
        found, ambiguous = _unique_match(
            [
                d
                for d in devices
                if str(d["name"]) == want_name and int(d.get("hostapi", -1)) == int(want_hostapi)
            ]
        )
        if found is not None or ambiguous:
            return found, ambiguous

    if want_name:
        return _unique_match([d for d in devices if str(d["name"]) == want_name])
    return None, False


def resolve_device_selection(
    selection: Any,
    kind: str,
    devices: list[dict] | None = None,
) -> tuple[dict | None, bool]:
    devices = list(devices if devices is not None else _devices_for_kind(kind))
    if selection is None:
        return None, False

    if isinstance(selection, dict):
        return _resolve_dict_selection(selection, kind, devices)

    if isinstance(selection, int):
        for device in devices:
            if int(device["index"]) == int(selection):
                return device, False
        return None, False

    name = str(selection)
    return _unique_match([d for d in devices if str(d["name"]) == name])


def device_by_index(index: int, kind: str | None = None) -> dict | None:
    try:
        devices = (
            _devices_for_kind(kind)
            if kind
            else [
                _device_descriptor(idx, d, _hostapi_names())
                for idx, d in enumerate(sd.query_devices())
            ]
        )
        for device in devices:
            if int(device["index"]) == int(index):
                return device
    except (sd.PortAudioError, ValueError):
        logger.warning("Could not look up audio device %s", index, exc_info=True)
    return None


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

#: Silence floor reported by every level reader, in dBFS.
SILENCE_DBFS = -120.0

#: How often the monitors re-emit their latest level on the GUI thread.
LEVEL_EMIT_INTERVAL_MS = 50


def _block_dbfs(block: np.ndarray) -> float:
    """RMS of one mono block in dBFS. Cheap enough for a realtime callback."""
    rms = float(np.sqrt(np.mean(block**2)))
    if rms <= 0.0:
        return SILENCE_DBFS
    return 20.0 * float(np.log10(rms))


class LevelMonitor(QObject):
    level_updated = pyqtSignal(float)  # RMS in dBFS (-inf … 0)
    error_occurred = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stream: sd.InputStream | None = None
        self._device: int | None = None
        self._channel: int = 0
        self._running = False
        self._lock = threading.Lock()
        self._latest_dbfs = SILENCE_DBFS
        # The PortAudio callback only stores a float; the signal is emitted
        # from this timer on the thread that owns the monitor, so nothing
        # allocates or locks a Qt event queue inside the realtime callback.
        self._emit_timer = QTimer(self)
        self._emit_timer.setInterval(LEVEL_EMIT_INTERVAL_MS)
        self._emit_timer.timeout.connect(self._emit_latest)

    def latest_dbfs(self) -> float:
        """Most recent block level in dBFS (``SILENCE_DBFS`` when silent)."""
        with self._lock:
            return self._latest_dbfs

    def _emit_latest(self) -> None:
        self.level_updated.emit(self.latest_dbfs())

    def _start_emit_timer(self) -> None:
        if QCoreApplication.instance() is None:
            return
        with contextlib.suppress(Exception):
            self._emit_timer.start()

    def _stop_emit_timer(self) -> None:
        with contextlib.suppress(Exception):
            self._emit_timer.stop()

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
            self._latest_dbfs = SILENCE_DBFS
        try:
            dev = device_by_index(device_index, kind="input")
            if dev is None:
                with self._lock:
                    self._running = False
                self.error_occurred.emit(f"Device not found: {device_label}")
                return
            n_ch = dev["max_input_channels"]
            if channel_index < 0 or channel_index >= n_ch:
                with self._lock:
                    self._running = False
                self.error_occurred.emit(f"Channel {channel_index} not available on {device_label}")
                return

            self._stream = sd.InputStream(
                device=device_index,
                channels=channel_index + 1,
                samplerate=fs,
                blocksize=buffer_size,
                dtype="float32",
                callback=self._callback,
                finished_callback=self._on_finished,
                latency="low",
            )
            self._stream.start()
            self._start_emit_timer()
        except Exception as e:
            logger.warning("Level monitor failed to start", exc_info=True)
            with self._lock:
                self._running = False
            self._stop_emit_timer()
            self.error_occurred.emit(f"Level monitor error: {e}")

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._latest_dbfs = SILENCE_DBFS
        self._stop_emit_timer()
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop(ignore_errors=True)
                stream.close(ignore_errors=True)
            except sd.PortAudioError:
                logger.warning("Level monitor stream did not close cleanly", exc_info=True)

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        with self._lock:
            if not self._running:
                return
            ch = min(self._channel, indata.shape[1] - 1)
        db = _block_dbfs(indata[:, ch])
        # No signal emission here: this runs on the PortAudio callback thread.
        with self._lock:
            self._latest_dbfs = db

    def _on_finished(self) -> None:
        pass


class DualLevelMonitor(QObject):
    """Monitor the first two input channels through one PortAudio stream."""

    levels_updated = pyqtSignal(float, float)
    error_occurred = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stream: sd.InputStream | None = None
        self._running = False
        self._lock = threading.Lock()
        self._latest_pair = (SILENCE_DBFS, SILENCE_DBFS)
        self._emit_timer = QTimer(self)
        self._emit_timer.setInterval(LEVEL_EMIT_INTERVAL_MS)
        self._emit_timer.timeout.connect(self._emit_latest)

    def latest_pair_dbfs(self) -> tuple[float, float]:
        """Most recent (left, right) block levels in dBFS."""
        with self._lock:
            return self._latest_pair

    def _emit_latest(self) -> None:
        left, right = self.latest_pair_dbfs()
        self.levels_updated.emit(left, right)

    def _start_emit_timer(self) -> None:
        if QCoreApplication.instance() is None:
            return
        with contextlib.suppress(Exception):
            self._emit_timer.start()

    def _stop_emit_timer(self) -> None:
        with contextlib.suppress(Exception):
            self._emit_timer.stop()

    def start(self, device_index: int, device_label: str, fs: int, buffer_size: int) -> None:
        self.stop()
        dev = device_by_index(device_index, kind="input")
        if dev is None or int(dev.get("max_input_channels", 0)) < 2:
            self.error_occurred.emit(f"Two input channels are unavailable on {device_label}")
            return
        self._running = True
        try:
            self._stream = sd.InputStream(
                device=device_index,
                channels=2,
                samplerate=fs,
                blocksize=buffer_size,
                dtype="float32",
                callback=self._callback,
                latency="low",
            )
            self._stream.start()
            self._start_emit_timer()
        except Exception as exc:
            logger.warning("Two-channel level monitor failed to start", exc_info=True)
            self._running = False
            self._stream = None
            self._stop_emit_timer()
            self.error_occurred.emit(f"Two-channel level monitor error: {exc}")

    def stop(self) -> None:
        self._running = False
        with self._lock:
            self._latest_pair = (SILENCE_DBFS, SILENCE_DBFS)
        self._stop_emit_timer()
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop(ignore_errors=True)
                stream.close(ignore_errors=True)
            except sd.PortAudioError:
                logger.warning("Level monitor stream did not close cleanly", exc_info=True)

    def _callback(self, indata: np.ndarray, _frames: int, _time_info, _status) -> None:
        if not self._running or indata.shape[1] < 2:
            return
        pair = (_block_dbfs(indata[:, 0]), _block_dbfs(indata[:, 1]))
        # No signal emission here: this runs on the PortAudio callback thread.
        with self._lock:
            self._latest_pair = pair


# ---------------------------------------------------------------------------
# Device poller — enumerates devices off the GUI thread
# ---------------------------------------------------------------------------

#: How often the device poller re-enumerates PortAudio.
DEVICE_POLL_INTERVAL_MS = 1500


def _device_identity(devices: list[dict]) -> list[tuple[int, str, int]]:
    """The fields a hotplug can change; used to decide whether to notify."""
    identity: list[tuple[int, str, int]] = []
    for device in devices:
        try:
            identity.append(
                (
                    int(device["index"]),
                    str(device["name"]),
                    int(device.get("hostapi", -1)),
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping malformed device descriptor %r", device)
            continue
    return identity


class _DevicePollWorker(QObject):
    """Lives on the poller's QThread and owns the polling timer."""

    devices_changed = pyqtSignal(list, list)

    def __init__(self, interval_ms: int, report_initial: bool = False) -> None:
        super().__init__()
        self._interval_ms = int(interval_ms)
        self._timer: QTimer | None = None
        self._paused = False
        self._stopped = False
        self._silent_first = not report_initial
        self._last: tuple[list, list] | None = None

    def set_paused(self, paused: bool) -> None:
        # A single bool assignment; safe to call from the GUI thread.
        self._paused = bool(paused)

    def request_stop(self) -> None:
        self._stopped = True

    @pyqtSlot()
    def begin(self) -> None:
        if self._stopped:
            return
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(self._interval_ms)
            self._timer.timeout.connect(self.poll)
        self._timer.start()
        self.poll()

    @pyqtSlot()
    def poll(self) -> None:
        if self._stopped or self._paused:
            return
        try:
            outputs = get_output_devices()
            inputs = get_input_devices()
        except Exception:
            # Last guard on the poll thread: an escaped exception would abort the app.
            logger.warning("Device poll failed", exc_info=True)
            return
        identity = (_device_identity(outputs), _device_identity(inputs))
        if identity == self._last:
            return
        self._last = identity
        if self._silent_first:
            # The first poll only establishes the baseline: the caller already
            # knows the device set it started with.
            self._silent_first = False
            return
        if self._stopped:
            return
        self.devices_changed.emit(outputs, inputs)


class DevicePoller(QObject):
    """Poll PortAudio for hotplugs on a worker thread.

    ``devices_changed`` carries the output and input device lists and fires
    only when the visible set of devices actually differs from the previous
    poll, so the GUI thread never enumerates devices on a timer. The poll that
    ``start()`` kicks off is silent — it records the baseline the caller
    already enumerated for itself — unless ``report_initial`` is set.
    """

    devices_changed = pyqtSignal(list, list)

    def __init__(
        self,
        interval_ms: int = DEVICE_POLL_INTERVAL_MS,
        parent: QObject | None = None,
        report_initial: bool = False,
    ) -> None:
        super().__init__(parent)
        self._interval_ms = int(interval_ms)
        self._report_initial = bool(report_initial)
        self._thread: QThread | None = None
        self._worker: _DevicePollWorker | None = None
        self._paused = False
        # A QThread that refused to join is never destroyed; deleting a
        # running QThread aborts the process.
        self._orphaned_threads: list[QThread] = []

    def is_running(self) -> bool:
        return self._thread is not None

    def is_paused(self) -> bool:
        return self._paused

    def start(self) -> None:
        if self._thread is not None:
            return
        worker = _DevicePollWorker(self._interval_ms, self._report_initial)
        worker.set_paused(self._paused)
        worker.devices_changed.connect(self._on_devices_changed)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.begin)
        thread.finished.connect(worker.deleteLater)
        self._worker = worker
        self._thread = thread
        thread.start()

    def pause(self, paused: bool) -> None:
        """Suspend polling; PortAudio must not be enumerated under a stream."""
        self._paused = bool(paused)
        worker = self._worker
        if worker is not None:
            worker.set_paused(self._paused)

    def stop(self) -> None:
        thread = self._thread
        worker = self._worker
        self._thread = None
        self._worker = None
        if worker is not None:
            worker.request_stop()
            with contextlib.suppress(Exception):
                worker.devices_changed.disconnect(self._on_devices_changed)
        if thread is None:
            return
        try:
            thread.quit()
            if not thread.wait(5000):
                self._orphaned_threads.append(thread)
        except RuntimeError:
            # The QThread's C++ object is already gone.
            logger.warning("Device poll thread could not be stopped", exc_info=True)
            self._orphaned_threads.append(thread)

    def _on_devices_changed(self, outputs: list, inputs: list) -> None:
        self.devices_changed.emit(outputs, inputs)


# ---------------------------------------------------------------------------
# Sweep worker — runs measurement in background thread
# ---------------------------------------------------------------------------


class SweepWorker(QObject):
    finished = pyqtSignal(np.ndarray, np.ndarray)  # recording, sweep
    error = pyqtSignal(str)
    progress = pyqtSignal(float)  # 0.0 … 1.0
    timing_quality = pyqtSignal(
        float, float, float, float
    )  # start_conf, end_conf, drift_ms, snr_db
    measurement_diagnostics = pyqtSignal(object)

    def __init__(self, parent: QObject | None = None) -> None:
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
        start_alignment_confidence_min: float = 6.0,
        end_marker_confidence_min: float = 7.0,
        timing_drift_max_ms: float = 35.0,
        output_channel: int | None = None,
        sweep_noise_margin_min_db: float = 3.0,
        snr_warn_db: float = 10.0,
        failed_recording_dir: str | None = None,
        sweep_f_low: float = F_LOW,
        sweep_f_high: float = F_HIGH,
    ) -> None:
        """Call from a QThread or thread pool."""
        self._abort.clear()
        try:
            self._run_inner(
                sweep,
                output_device,
                input_device,
                input_channel,
                fs,
                buffer_size,
                pre_silence,
                post_silence,
                latency,
                output_device_label,
                input_device_label,
                bluetooth_headphone_mode,
                start_alignment_confidence_min,
                end_marker_confidence_min,
                timing_drift_max_ms,
                output_channel,
                sweep_noise_margin_min_db,
                snr_warn_db,
                failed_recording_dir,
                sweep_f_low,
                sweep_f_high,
            )
        except sd.PortAudioError as e:
            self.error.emit(f"PortAudio error: {e}")
        except Exception as e:
            logger.warning("Sweep failed", exc_info=True)
            self.error.emit(f"Sweep error: {e}")

    def _run_inner(
        self,
        sweep,
        output_device,
        input_device,
        input_channel,
        fs,
        buffer_size,
        pre_silence,
        post_silence,
        latency,
        output_device_label,
        input_device_label,
        bluetooth_headphone_mode,
        start_alignment_confidence_min,
        end_marker_confidence_min,
        timing_drift_max_ms,
        output_channel=None,
        sweep_noise_margin_min_db=3.0,
        snr_warn_db=10.0,
        failed_recording_dir=None,
        sweep_f_low=F_LOW,
        sweep_f_high=F_HIGH,
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
                f"Input channel {input_channel} not available (device has {n_in_ch} ch)."
            )
            return
        if output_channel is not None and (output_channel < 0 or output_channel >= n_out_ch):
            self.error.emit(
                f"Output channel {output_channel} not available (device has {n_out_ch} ch)."
            )
            return

        layout = build_measurement_layout(
            sweep=sweep,
            fs=fs,
            pre_silence_s=pre_silence,
            post_silence_s=post_silence,
            bluetooth_headphone_mode=bluetooth_headphone_mode,
        )
        routed_output = output_channel is not None
        out_signal = build_output_signal(
            layout,
            1 if routed_output else n_out_ch,
        )
        total_n = layout.total_samples

        if self._abort.is_set():
            return

        try:
            recording = sd.playrec(
                out_signal,
                samplerate=fs,
                input_mapping=[input_channel + 1],  # 1-based
                output_mapping=[output_channel + 1] if routed_output else None,
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
                with contextlib.suppress(Exception):
                    sd.stop()
                return
            elapsed = time.monotonic() - start
            self.progress.emit(min(elapsed / total_time, 0.99))
            if elapsed >= total_time + 0.1:
                break
            time.sleep(0.05)

        with contextlib.suppress(Exception):
            sd.wait()

        self.progress.emit(1.0)

        rec_mono = recording[:, 0]

        alignment_settings = AlignmentSettings(
            latency=latency,
            bluetooth_headphone_mode=bluetooth_headphone_mode,
            start_alignment_confidence_min=start_alignment_confidence_min,
            end_marker_confidence_min=end_marker_confidence_min,
            timing_drift_max_ms=timing_drift_max_ms,
            sweep_noise_margin_min_db=sweep_noise_margin_min_db,
            snr_warn_db=snr_warn_db,
        )
        try:
            alignment = align_recording_to_layout(
                rec_mono=rec_mono,
                sweep=sweep,
                layout=layout,
                settings=alignment_settings,
            )
        except MeasurementAlignmentError as exc:
            diagnostics = replace(exc.diagnostics, buffer_size=int(buffer_size))
            if failed_recording_dir:
                # Diagnostics must never turn a failed sweep into a crash.
                with contextlib.suppress(Exception):
                    save_failed_recording(
                        failed_recording_dir,
                        rec_mono,
                        fs=int(fs),
                        sweep_duration_s=len(sweep) / float(fs),
                        f_low=float(sweep_f_low),
                        f_high=float(sweep_f_high),
                        pre_silence_s=float(pre_silence),
                        post_silence_s=float(post_silence),
                        bluetooth_headphone_mode=bool(bluetooth_headphone_mode),
                        alignment_settings=alignment_settings,
                        diagnostics=diagnostics,
                        failure_message=str(exc),
                        failure_reason=exc.reason,
                        extra={
                            "input_device": input_device_label,
                            "output_device": output_device_label,
                            "input_channel": int(input_channel),
                            "output_channel": output_channel,
                            "buffer_size": int(buffer_size),
                        },
                    )
            self.measurement_diagnostics.emit(diagnostics)
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
        # The impulse-response window needs the decay that follows the sweep;
        # the aligned window itself is exactly sweep-length, so the recorded
        # tail is appended here rather than widening the alignment result.
        recording = alignment.aligned_recording
        tail = getattr(alignment, "aligned_recording_tail", None)
        if tail is not None and len(tail) > 0:
            recording = np.concatenate([recording, tail])
        self.finished.emit(recording, sweep)
