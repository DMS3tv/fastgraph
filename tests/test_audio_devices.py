from types import SimpleNamespace

import numpy as np

from dms import audio_engine
from dms.measurement_alignment import MeasurementDiagnostics


def _mock_windows_devices(monkeypatch):
    devices = [
        {
            "name": "in 1-2 (motu m series)",
            "hostapi": 0,
            "max_input_channels": 2,
            "max_output_channels": 0,
        },
        {
            "name": "Main Out",
            "hostapi": 2,
            "max_input_channels": 0,
            "max_output_channels": 2,
        },
        {
            "name": "Main Out",
            "hostapi": 1,
            "max_input_channels": 0,
            "max_output_channels": 2,
        },
        {
            "name": "Unique Mic",
            "hostapi": 2,
            "max_input_channels": 1,
            "max_output_channels": 0,
        },
    ]
    while len(devices) <= 17:
        devices.append(
            {
                "name": f"unused {len(devices)}",
                "hostapi": 0,
                "max_input_channels": 0,
                "max_output_channels": 0,
            }
        )
    devices[17] = {
        "name": "in 1-2 (motu m series)",
        "hostapi": 1,
        "max_input_channels": 2,
        "max_output_channels": 0,
    }
    while len(devices) <= 43:
        devices.append(
            {
                "name": f"unused {len(devices)}",
                "hostapi": 0,
                "max_input_channels": 0,
                "max_output_channels": 0,
            }
        )
    devices[43] = {
        "name": "in 1-2 (motu m series)",
        "hostapi": 2,
        "max_input_channels": 2,
        "max_output_channels": 0,
    }
    devices.append(
        {
            "name": "in 1-2 (motu m series)",
            "hostapi": 3,
            "max_input_channels": 2,
            "max_output_channels": 0,
        }
    )
    devices.append(
        {
            "name": "Main Out",
            "hostapi": 3,
            "max_input_channels": 0,
            "max_output_channels": 2,
        }
    )
    hostapis = [
        {"name": "MME"},
        {"name": "Windows DirectSound"},
        {"name": "Windows WASAPI"},
        {"name": "Windows WDM-KS"},
    ]
    monkeypatch.setattr(audio_engine.sd, "query_devices", lambda: devices)
    monkeypatch.setattr(audio_engine.sd, "query_hostapis", lambda: hostapis)
    return devices


def test_duplicate_windows_device_names_get_distinct_labels(monkeypatch) -> None:
    _mock_windows_devices(monkeypatch)

    devices = audio_engine.get_input_devices()
    duplicates = audio_engine.duplicate_device_names(devices)
    labels = [audio_engine.device_label(d, duplicates) for d in devices]

    assert "in 1-2 (motu m series) (MME)" in labels
    assert "in 1-2 (motu m series) (Windows DirectSound)" in labels
    assert "in 1-2 (motu m series) (Windows WASAPI)" in labels
    assert "in 1-2 (motu m series) (Windows WDM-KS)" in labels
    assert "Unique Mic" in labels


def test_windows_preferred_hostapi_uses_wasapi_when_available(monkeypatch) -> None:
    _mock_windows_devices(monkeypatch)

    preferred = audio_engine.preferred_windows_hostapi(
        audio_engine.get_input_devices(),
        audio_engine.get_output_devices(),
    )
    filtered = audio_engine.filter_devices_by_hostapi(
        audio_engine.get_input_devices(),
        preferred,
    )

    assert preferred == 2
    assert {d["hostapi_name"] for d in filtered} == {"Windows WASAPI"}


def test_windows_device_pair_requires_matching_hostapi(monkeypatch) -> None:
    _mock_windows_devices(monkeypatch)
    monkeypatch.setattr(audio_engine.os, "name", "nt")
    inputs = {d["index"]: d for d in audio_engine.get_input_devices()}
    outputs = {d["index"]: d for d in audio_engine.get_output_devices()}

    assert audio_engine.is_compatible_device_pair(inputs[43], outputs[1]) is True
    assert audio_engine.is_compatible_device_pair(inputs[43], outputs[2]) is False


def test_structured_device_setting_resolves_wasapi_duplicate(monkeypatch) -> None:
    _mock_windows_devices(monkeypatch)
    selection = {
        "index": 43,
        "name": "in 1-2 (motu m series)",
        "hostapi": 2,
        "hostapi_name": "Windows WASAPI",
        "kind": "input",
    }

    device, ambiguous = audio_engine.resolve_device_selection(selection, "input")

    assert ambiguous is False
    assert device is not None
    assert device["index"] == 43
    assert device["hostapi_name"] == "Windows WASAPI"


def test_legacy_string_selection_resolves_only_when_unique(monkeypatch) -> None:
    _mock_windows_devices(monkeypatch)

    unique, unique_ambiguous = audio_engine.resolve_device_selection("Unique Mic", "input")
    duplicate, duplicate_ambiguous = audio_engine.resolve_device_selection(
        "in 1-2 (motu m series)",
        "input",
    )

    assert unique_ambiguous is False
    assert unique is not None
    assert unique["name"] == "Unique Mic"
    assert duplicate is None
    assert duplicate_ambiguous is True


def test_sweep_worker_passes_numeric_portaudio_indices(monkeypatch) -> None:
    _mock_windows_devices(monkeypatch)
    playrec_calls = []
    monkeypatch.setattr(
        audio_engine,
        "build_measurement_layout",
        lambda **_kwargs: SimpleNamespace(total_samples=1, fs=48000),
    )
    monkeypatch.setattr(
        audio_engine,
        "build_output_signal",
        lambda _layout, n_out_ch: np.zeros((1, n_out_ch), dtype=np.float32),
    )
    monkeypatch.setattr(
        audio_engine,
        "align_recording_to_layout",
        lambda **_kwargs: SimpleNamespace(
            aligned_recording=np.zeros(1, dtype=np.float32),
            diagnostics=MeasurementDiagnostics(
                fs=48000,
                bluetooth_headphone_mode=False,
                latency="low",
                start_alignment_confidence_min=9.0,
                end_marker_confidence_min=7.0,
                timing_drift_max_ms=35.0,
            ),
            start=SimpleNamespace(start_confidence=10.0),
            end=SimpleNamespace(marker_confidence=8.0, timing_error_ms=0.0),
            snr_db=60.0,
        ),
    )
    times = iter([0.0, 1.0])
    monkeypatch.setattr(audio_engine.time, "monotonic", lambda: next(times, 1.0))
    monkeypatch.setattr(audio_engine.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(audio_engine.sd, "wait", lambda: None)

    def fake_playrec(*args, **kwargs):
        playrec_calls.append(kwargs)
        return np.zeros((1, 1), dtype=np.float32)

    monkeypatch.setattr(audio_engine.sd, "playrec", fake_playrec)

    worker = audio_engine.SweepWorker()
    worker._run_inner(
        sweep=np.zeros(1, dtype=np.float32),
        output_device=1,
        input_device=43,
        input_channel=0,
        fs=48000,
        buffer_size=256,
        pre_silence=0.1,
        post_silence=0.1,
        latency="low",
        output_device_label="Main Out",
        input_device_label="in 1-2 (motu m series) (Windows WASAPI)",
        bluetooth_headphone_mode=False,
        start_alignment_confidence_min=9.0,
        end_marker_confidence_min=7.0,
        timing_drift_max_ms=35.0,
    )

    assert playrec_calls
    assert playrec_calls[0]["device"] == (43, 1)


def test_level_monitor_passes_numeric_portaudio_index(monkeypatch) -> None:
    _mock_windows_devices(monkeypatch)
    stream_calls = []

    class _FakeInputStream:
        def __init__(self, **kwargs):
            stream_calls.append(kwargs)

        def start(self) -> None:
            pass

    monkeypatch.setattr(audio_engine.sd, "InputStream", _FakeInputStream)

    monitor = audio_engine.LevelMonitor()
    monitor.start(
        device_index=43,
        device_label="in 1-2 (motu m series) (Windows WASAPI)",
        channel_index=0,
        fs=48000,
        buffer_size=256,
    )

    assert stream_calls
    assert stream_calls[0]["device"] == 43


def _fake_indata(n_frames: int = 32, n_channels: int = 2, value: float = 0.5) -> np.ndarray:
    return np.full((n_frames, n_channels), value, dtype=np.float32)


def test_level_monitor_callback_throttles_level_updated(monkeypatch) -> None:
    monitor = audio_engine.LevelMonitor()
    monitor._cb_running = True
    monitor._cb_channel = 0

    fake_now = [0.0]
    monkeypatch.setattr(audio_engine.time, "monotonic", lambda: fake_now[0])

    emitted = []
    monitor.level_updated.connect(emitted.append)

    indata = _fake_indata()

    # First call always emits (last_emit_time starts at 0.0, well in the past).
    fake_now[0] = 1.0
    monitor._callback(indata, indata.shape[0], None, None)
    assert len(emitted) == 1

    # Calls within the throttle window (< 50ms later) must not emit again.
    fake_now[0] = 1.01
    monitor._callback(indata, indata.shape[0], None, None)
    fake_now[0] = 1.04
    monitor._callback(indata, indata.shape[0], None, None)
    assert len(emitted) == 1

    # Once >= 50ms have elapsed, the next callback emits again.
    fake_now[0] = 1.051
    monitor._callback(indata, indata.shape[0], None, None)
    assert len(emitted) == 2


def test_level_monitor_callback_takes_no_lock(monkeypatch) -> None:
    monitor = audio_engine.LevelMonitor()
    monitor._cb_running = True
    monitor._cb_channel = 0
    monkeypatch.setattr(audio_engine.time, "monotonic", lambda: 1.0)

    class _ExplodingLock:
        def acquire(self, *args, **kwargs):
            raise AssertionError("audio callback must not take the lock")

        def release(self, *args, **kwargs):
            raise AssertionError("audio callback must not take the lock")

        def __enter__(self):
            raise AssertionError("audio callback must not take the lock")

        def __exit__(self, *args):
            raise AssertionError("audio callback must not take the lock")

    monitor._lock = _ExplodingLock()

    emitted = []
    monitor.level_updated.connect(emitted.append)

    # Must not raise even though self._lock would blow up if touched.
    monitor._callback(_fake_indata(), 32, None, None)
    assert len(emitted) == 1

    # A monitor that isn't "running" must also return before touching the lock.
    monitor._cb_running = False
    monitor._callback(_fake_indata(), 32, None, None)
    assert len(emitted) == 1


def test_level_monitor_stopped_unexpectedly_only_without_stop() -> None:
    monitor = audio_engine.LevelMonitor()
    monitor._stream = None  # avoid touching a real InputStream in stop()

    signals = []
    monitor.stopped_unexpectedly.connect(signals.append)

    # Simulate the InputStream's finished_callback firing without stop()
    # ever being requested (e.g. device unplugged).
    monitor._on_finished()
    assert len(signals) == 1

    # After an explicit stop(), the same callback firing must NOT re-signal.
    monitor._stop_requested = False  # reset as if a fresh start() happened
    monitor.stop()
    monitor._on_finished()
    assert len(signals) == 1


def test_device_poll_worker_emits_only_on_change(monkeypatch) -> None:
    devices_v1 = [
        {"index": 0, "name": "Speakers", "hostapi": 0, "max_output_channels": 2, "max_input_channels": 0},
    ]
    devices_v2 = devices_v1 + [
        {"index": 1, "name": "USB Mic", "hostapi": 0, "max_output_channels": 0, "max_input_channels": 1},
    ]

    current = {"out": devices_v1, "in": []}
    monkeypatch.setattr(audio_engine, "get_output_devices", lambda: current["out"])
    monkeypatch.setattr(audio_engine, "get_input_devices", lambda: current["in"])

    worker = audio_engine.DevicePollWorker(interval_ms=1500)
    emissions = []
    worker.devices_changed.connect(lambda out, inp: emissions.append((out, inp)))

    worker.poll_once()
    assert len(emissions) == 1

    # Same devices again -> no emission.
    worker.poll_once()
    worker.poll_once()
    assert len(emissions) == 1

    # Device list actually changes -> emits again.
    current["out"] = devices_v2
    worker.poll_once()
    assert len(emissions) == 2
    assert emissions[1][0] == devices_v2
