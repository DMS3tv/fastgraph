import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QComboBox, QLabel

from dms.ui.main_window import MainWindow


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
    _refresh_devices = MainWindow._refresh_devices
    _refresh_channels = MainWindow._refresh_channels

    def __init__(self, settings: _Settings) -> None:
        self._settings = settings
        self._out_dev_combo = QComboBox()
        self._in_dev_combo = QComboBox()
        self._ch_combo = QComboBox()
        self._active_ch_label = QLabel()
        self._statusbar = _Status()
        self._input_devices_by_index = {}
        self._output_devices_by_index = {}
        self._input_device_labels_by_index = {}
        self._output_device_labels_by_index = {}
        self._last_output_devices = []
        self._last_input_devices = []
        self.apply_count = 0
        self.monitor_count = 0

    def _apply_state_ui(self) -> None:
        self.apply_count += 1

    def _start_level_monitor(self) -> None:
        self.monitor_count += 1


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
    ]
    return outputs, inputs


def test_refresh_devices_uses_numeric_indices_and_structured_settings(monkeypatch) -> None:
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
        }
    )
    harness = _Harness(settings)
    monkeypatch.setattr("dms.ui.main_window.get_output_devices", lambda: outputs)
    monkeypatch.setattr("dms.ui.main_window.get_input_devices", lambda: inputs)
    monkeypatch.setattr("dms.ui.main_window.device_channel_count", lambda _device, _kind: 2)

    harness._refresh_devices()

    assert harness._in_dev_combo.currentData() == 43
    assert harness._out_dev_combo.currentData() == 5
    assert "Windows WASAPI" in harness._in_dev_combo.currentText()
    assert settings.data["input_device"]["index"] == 43
    assert settings.data["input_device"]["kind"] == "input"
    assert settings.data["output_device"]["index"] == 5


def test_refresh_devices_leaves_legacy_duplicate_unselected(monkeypatch) -> None:
    _app()
    outputs, inputs = _devices()
    settings = _Settings(
        {
            "input_device": "in 1-2 (motu m series)",
            "output_device": None,
            "input_channel": 0,
        }
    )
    harness = _Harness(settings)
    monkeypatch.setattr("dms.ui.main_window.get_output_devices", lambda: outputs)
    monkeypatch.setattr("dms.ui.main_window.get_input_devices", lambda: inputs)
    monkeypatch.setattr("dms.ui.main_window.device_channel_count", lambda _device, _kind: 2)

    harness._refresh_devices()

    assert harness._in_dev_combo.currentData() is None
    assert settings.data["input_device"] is None
    assert "ambiguous" in harness._statusbar.messages[-1].lower()
