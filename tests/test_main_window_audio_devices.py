import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QCheckBox, QComboBox, QLabel

from dms.ui.main_window import MainWindow
from dms.ui.main_window import AppState
from dms import audio_engine


_APP = None


class _Settings:
    def __init__(self, initial: dict | None = None) -> None:
        self.data = dict(initial or {})

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value) -> None:
        self.data[key] = value


class _Status:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def showMessage(self, message: str) -> None:
        self.messages.append(message)


class _LevelMonitor:
    def __init__(self) -> None:
        self.stop_count = 0

    def stop(self) -> None:
        self.stop_count += 1


class _Harness:
    _current_output_device = MainWindow._current_output_device
    _current_input_device = MainWindow._current_input_device
    _current_output_device_info = MainWindow._current_output_device_info
    _current_input_device_info = MainWindow._current_input_device_info
    _current_output_device_label = MainWindow._current_output_device_label
    _current_input_device_label = MainWindow._current_input_device_label
    _current_output_device_setting = MainWindow._current_output_device_setting
    _current_input_device_setting = MainWindow._current_input_device_setting
    _current_input_channel = MainWindow._current_input_channel
    _use_advanced_windows_drivers = MainWindow._use_advanced_windows_drivers
    _selected_audio_pair_is_compatible = MainWindow._selected_audio_pair_is_compatible
    _windows_audio_pair_message = MainWindow._windows_audio_pair_message
    _matching_output_for_input = MainWindow._matching_output_for_input
    _sync_windows_output_to_input = MainWindow._sync_windows_output_to_input
    _sweep_latency_mode = MainWindow._sweep_latency_mode
    _refresh_devices = MainWindow._refresh_devices
    _manual_refresh_devices = MainWindow._manual_refresh_devices
    _refresh_channels = MainWindow._refresh_channels
    _start_queue = MainWindow._start_queue

    def __init__(self, settings: _Settings) -> None:
        self._settings = settings
        self._state = AppState.IDLE
        self._out_dev_combo = QComboBox()
        self._in_dev_combo = QComboBox()
        self._ch_combo = QComboBox()
        self._advanced_windows_drivers_toggle = QCheckBox()
        self._advanced_windows_drivers_toggle.setChecked(
            bool(settings.get("windows_advanced_audio_drivers"))
        )
        self._active_ch_label = QLabel()
        self._statusbar = _Status()
        self._input_devices_by_index = {}
        self._output_devices_by_index = {}
        self._input_device_labels_by_index = {}
        self._output_device_labels_by_index = {}
        self._last_output_devices = []
        self._last_input_devices = []
        self._level_monitor = _LevelMonitor()
        self.apply_count = 0
        self.monitor_count = 0
        self.start_next_sweep_count = 0
        self._last_level_dbfs = -120.0

    def _apply_state_ui(self) -> None:
        self.apply_count += 1

    def _start_level_monitor(self) -> None:
        self.monitor_count += 1

    def _start_next_sweep(self) -> None:
        self.start_next_sweep_count += 1


def _app() -> QApplication:
    global _APP
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    _APP = app
    return app


def _devices():
    outputs = [
        {
            "index": 5,
            "name": "MOTU Out",
            "hostapi": 2,
            "hostapi_name": "Windows WASAPI",
            "max_input_channels": 0,
            "max_output_channels": 2,
        },
        {
            "index": 6,
            "name": "MOTU Out",
            "hostapi": 1,
            "hostapi_name": "Windows DirectSound",
            "max_input_channels": 0,
            "max_output_channels": 2,
        },
        {
            "index": 7,
            "name": "MOTU Out",
            "hostapi": 3,
            "hostapi_name": "Windows WDM-KS",
            "max_input_channels": 0,
            "max_output_channels": 2,
        }
    ]
    inputs = [
        {
            "index": 1,
            "name": "in 1-2 (motu m series)",
            "hostapi": 0,
            "hostapi_name": "MME",
            "max_input_channels": 2,
            "max_output_channels": 0,
        },
        {
            "index": 17,
            "name": "in 1-2 (motu m series)",
            "hostapi": 1,
            "hostapi_name": "Windows DirectSound",
            "max_input_channels": 2,
            "max_output_channels": 0,
        },
        {
            "index": 43,
            "name": "in 1-2 (motu m series)",
            "hostapi": 2,
            "hostapi_name": "Windows WASAPI",
            "max_input_channels": 2,
            "max_output_channels": 0,
        },
        {
            "index": 44,
            "name": "in 1-2 (motu m series)",
            "hostapi": 3,
            "hostapi_name": "Windows WDM-KS",
            "max_input_channels": 2,
            "max_output_channels": 0,
        },
    ]
    return outputs, inputs


def test_windows_normal_mode_shows_only_preferred_wasapi_devices(monkeypatch) -> None:
    _app()
    outputs, inputs = _devices()
    settings = _Settings(
        {
            "input_device": {
                "index": 43,
                "name": "in 1-2 (motu m series)",
                "hostapi": 2,
                "hostapi_name": "Windows WASAPI",
                "kind": "input",
            },
            "output_device": None,
            "input_channel": 0,
            "windows_advanced_audio_drivers": False,
        }
    )
    harness = _Harness(settings)
    monkeypatch.setattr("dms.ui.main_window.is_windows_audio_host", lambda: True)
    monkeypatch.setattr(audio_engine.os, "name", "nt")
    monkeypatch.setattr("dms.ui.main_window.get_output_devices", lambda: outputs)
    monkeypatch.setattr("dms.ui.main_window.get_input_devices", lambda: inputs)
    monkeypatch.setattr("dms.ui.main_window.device_channel_count", lambda _device, _kind: 2)

    harness._refresh_devices()

    assert harness._in_dev_combo.count() == 1
    assert harness._out_dev_combo.count() == 1
    assert harness._in_dev_combo.currentData() == 43
    assert harness._out_dev_combo.currentData() == 5
    assert settings.data["input_device"]["index"] == 43
    assert settings.data["input_device"]["kind"] == "input"
    assert settings.data["output_device"]["index"] == 5


def test_windows_advanced_mode_shows_all_backends(monkeypatch) -> None:
    _app()
    outputs, inputs = _devices()
    settings = _Settings(
        {
            "input_device": None,
            "output_device": None,
            "input_channel": 0,
            "windows_advanced_audio_drivers": True,
        }
    )
    harness = _Harness(settings)
    monkeypatch.setattr("dms.ui.main_window.is_windows_audio_host", lambda: True)
    monkeypatch.setattr(audio_engine.os, "name", "nt")
    monkeypatch.setattr("dms.ui.main_window.get_output_devices", lambda: outputs)
    monkeypatch.setattr("dms.ui.main_window.get_input_devices", lambda: inputs)
    monkeypatch.setattr("dms.ui.main_window.device_channel_count", lambda _device, _kind: 2)

    harness._refresh_devices()

    assert harness._in_dev_combo.count() == 4
    assert harness._out_dev_combo.count() == 3
    assert any(
        "Windows DirectSound" in harness._in_dev_combo.itemText(i)
        for i in range(harness._in_dev_combo.count())
    )


def test_windows_legacy_duplicate_resolves_to_wasapi_in_normal_mode(monkeypatch) -> None:
    _app()
    outputs, inputs = _devices()
    settings = _Settings(
        {
            "input_device": "in 1-2 (motu m series)",
            "output_device": None,
            "input_channel": 0,
            "windows_advanced_audio_drivers": False,
        }
    )
    harness = _Harness(settings)
    monkeypatch.setattr("dms.ui.main_window.is_windows_audio_host", lambda: True)
    monkeypatch.setattr(audio_engine.os, "name", "nt")
    monkeypatch.setattr("dms.ui.main_window.get_output_devices", lambda: outputs)
    monkeypatch.setattr("dms.ui.main_window.get_input_devices", lambda: inputs)
    monkeypatch.setattr("dms.ui.main_window.device_channel_count", lambda _device, _kind: 2)

    harness._refresh_devices()

    assert harness._in_dev_combo.currentData() == 43
    assert settings.data["input_device"]["hostapi_name"] == "Windows WASAPI"


def test_windows_input_selection_auto_matches_output_backend(monkeypatch) -> None:
    _app()
    outputs, inputs = _devices()
    settings = _Settings(
        {
            "input_device": None,
            "output_device": None,
            "input_channel": 0,
            "windows_advanced_audio_drivers": True,
        }
    )
    harness = _Harness(settings)
    monkeypatch.setattr("dms.ui.main_window.is_windows_audio_host", lambda: True)
    monkeypatch.setattr(audio_engine.os, "name", "nt")
    monkeypatch.setattr("dms.ui.main_window.get_output_devices", lambda: outputs)
    monkeypatch.setattr("dms.ui.main_window.get_input_devices", lambda: inputs)
    monkeypatch.setattr("dms.ui.main_window.device_channel_count", lambda _device, _kind: 2)

    harness._refresh_devices()
    harness._out_dev_combo.setCurrentIndex(harness._out_dev_combo.findData(6))
    harness._in_dev_combo.setCurrentIndex(harness._in_dev_combo.findData(43))
    harness._sync_windows_output_to_input(show_status=False)

    assert harness._out_dev_combo.currentData() == 5


def test_windows_mismatched_backends_block_queue_start(monkeypatch) -> None:
    _app()
    outputs, inputs = _devices()
    settings = _Settings(
        {
            "input_device": None,
            "output_device": None,
            "input_channel": 0,
            "windows_advanced_audio_drivers": True,
        }
    )
    harness = _Harness(settings)
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr("dms.ui.main_window.is_windows_audio_host", lambda: True)
    monkeypatch.setattr(audio_engine.os, "name", "nt")
    monkeypatch.setattr("dms.ui.main_window.get_output_devices", lambda: outputs)
    monkeypatch.setattr("dms.ui.main_window.get_input_devices", lambda: inputs)
    monkeypatch.setattr("dms.ui.main_window.device_channel_count", lambda _device, _kind: 2)
    monkeypatch.setattr(
        "dms.ui.main_window.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    harness._refresh_devices()
    harness._out_dev_combo.setCurrentIndex(harness._out_dev_combo.findData(6))
    harness._in_dev_combo.setCurrentIndex(harness._in_dev_combo.findData(43))

    harness._start_queue()

    assert warnings
    assert "mismatch" in warnings[0][0].lower()
    assert harness.start_next_sweep_count == 0


def test_windows_default_non_bluetooth_latency_is_high_until_user_override(monkeypatch) -> None:
    _app()
    settings = _Settings(
        {
            "latency": "low",
            "latency_user_override": False,
            "bluetooth_headphone_mode": False,
        }
    )
    harness = _Harness(settings)
    monkeypatch.setattr("dms.ui.main_window.is_windows_audio_host", lambda: True)

    assert harness._sweep_latency_mode() == "high"

    settings.set("latency_user_override", True)
    assert harness._sweep_latency_mode() == "low"


def test_non_windows_latency_behavior_is_unchanged(monkeypatch) -> None:
    _app()
    settings = _Settings(
        {
            "latency": "low",
            "latency_user_override": False,
            "bluetooth_headphone_mode": False,
        }
    )
    harness = _Harness(settings)
    monkeypatch.setattr("dms.ui.main_window.is_windows_audio_host", lambda: False)

    assert harness._sweep_latency_mode() == "low"


def test_manual_refresh_reinitializes_backend_before_enumerating(monkeypatch) -> None:
    _app()
    outputs, inputs = _devices()
    refreshed_outputs = [
        {
            "index": 9,
            "name": "Fresh Out",
            "hostapi": 2,
            "hostapi_name": "Windows WASAPI",
            "max_input_channels": 0,
            "max_output_channels": 2,
        }
    ]
    refreshed_inputs = [
        {
            "index": 10,
            "name": "Fresh In",
            "hostapi": 2,
            "hostapi_name": "Windows WASAPI",
            "max_input_channels": 2,
            "max_output_channels": 0,
        }
    ]
    current = {"outputs": outputs, "inputs": inputs}
    settings = _Settings(
        {
            "input_device": None,
            "output_device": None,
            "input_channel": 0,
            "windows_advanced_audio_drivers": False,
        }
    )
    harness = _Harness(settings)
    calls: list[str] = []
    monkeypatch.setattr("dms.ui.main_window.is_windows_audio_host", lambda: False)
    monkeypatch.setattr(
        "dms.ui.main_window.get_output_devices",
        lambda: calls.append("outputs") or current["outputs"],
    )
    monkeypatch.setattr(
        "dms.ui.main_window.get_input_devices",
        lambda: calls.append("inputs") or current["inputs"],
    )
    monkeypatch.setattr("dms.ui.main_window.device_channel_count", lambda _device, _kind: 2)

    def refresh_backend() -> bool:
        calls.append("backend")
        current["outputs"] = refreshed_outputs
        current["inputs"] = refreshed_inputs
        return True

    monkeypatch.setattr("dms.ui.main_window.refresh_audio_backend", refresh_backend)

    harness._refresh_devices()
    calls.clear()
    harness._manual_refresh_devices()

    assert calls[:3] == ["backend", "outputs", "inputs"]
    assert harness._out_dev_combo.currentData() == 9
    assert harness._in_dev_combo.currentData() == 10
    assert harness._level_monitor.stop_count == 1
    assert harness.monitor_count == 2
    assert harness._statusbar.messages[-1] == "Audio devices refreshed; selection changed."


def test_manual_refresh_preserves_valid_device_selection(monkeypatch) -> None:
    _app()
    outputs, inputs = _devices()
    settings = _Settings(
        {
            "input_device": None,
            "output_device": None,
            "input_channel": 1,
            "windows_advanced_audio_drivers": True,
        }
    )
    harness = _Harness(settings)
    monkeypatch.setattr("dms.ui.main_window.is_windows_audio_host", lambda: False)
    monkeypatch.setattr("dms.ui.main_window.get_output_devices", lambda: outputs)
    monkeypatch.setattr("dms.ui.main_window.get_input_devices", lambda: inputs)
    monkeypatch.setattr("dms.ui.main_window.device_channel_count", lambda _device, _kind: 2)
    monkeypatch.setattr("dms.ui.main_window.refresh_audio_backend", lambda: True)

    harness._refresh_devices()
    harness._ch_combo.setCurrentIndex(1)
    settings.set("input_channel", 1)
    harness._out_dev_combo.setCurrentIndex(harness._out_dev_combo.findData(6))
    harness._in_dev_combo.setCurrentIndex(harness._in_dev_combo.findData(17))
    settings.set("output_device", harness._current_output_device_setting())
    settings.set("input_device", harness._current_input_device_setting())

    harness._manual_refresh_devices()

    assert harness._out_dev_combo.currentData() == 6
    assert harness._in_dev_combo.currentData() == 17
    assert harness._current_input_channel() == 1
    assert harness._statusbar.messages[-1] == "Audio devices refreshed."


def test_manual_refresh_falls_back_when_selected_device_disappears(monkeypatch) -> None:
    _app()
    outputs, inputs = _devices()
    current = {"outputs": outputs, "inputs": inputs}
    settings = _Settings(
        {
            "input_device": None,
            "output_device": None,
            "input_channel": 0,
            "windows_advanced_audio_drivers": True,
        }
    )
    harness = _Harness(settings)
    monkeypatch.setattr("dms.ui.main_window.is_windows_audio_host", lambda: False)
    monkeypatch.setattr("dms.ui.main_window.get_output_devices", lambda: current["outputs"])
    monkeypatch.setattr("dms.ui.main_window.get_input_devices", lambda: current["inputs"])
    monkeypatch.setattr("dms.ui.main_window.device_channel_count", lambda _device, _kind: 2)

    def refresh_backend() -> bool:
        current["outputs"] = [outputs[0]]
        current["inputs"] = [inputs[2]]
        return True

    monkeypatch.setattr("dms.ui.main_window.refresh_audio_backend", refresh_backend)

    harness._refresh_devices()
    harness._out_dev_combo.setCurrentIndex(harness._out_dev_combo.findData(6))
    harness._in_dev_combo.setCurrentIndex(harness._in_dev_combo.findData(17))
    settings.set("output_device", harness._current_output_device_setting())
    settings.set("input_device", harness._current_input_device_setting())

    harness._manual_refresh_devices()

    assert harness._out_dev_combo.currentData() == 5
    assert harness._in_dev_combo.currentData() == 43
    assert settings.data["output_device"]["index"] == 5
    assert settings.data["input_device"]["index"] == 43
    assert harness._statusbar.messages[-1] == "Audio devices refreshed; selection changed."
