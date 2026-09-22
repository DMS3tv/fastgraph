from dms import audio_engine


def _window(make_main_window, settings: dict, *, stub_devices: bool = False):
    """A real window built on whatever device functions the test patched.

    Patch ``get_*_devices`` and friends before calling this: the window
    enumerates once while it is built, exactly as it does at startup.
    """
    window = make_main_window(settings=settings, stub_devices=stub_devices)
    window.monitor_count = 0
    window.start_next_sweep_count = 0

    def _count_monitor() -> None:
        window.monitor_count += 1

    def _count_sweep() -> None:
        window.start_next_sweep_count += 1

    window.devices.start_level_monitor = _count_monitor
    window._start_next_sweep = _count_sweep
    return window


def _count_level_monitor_stops(window) -> list[bool]:
    stops: list[bool] = []
    window.devices.level_monitor.stop = lambda: stops.append(True)
    return stops


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
        },
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


def test_windows_normal_mode_shows_only_preferred_wasapi_devices(
    make_main_window, monkeypatch
) -> None:
    outputs, inputs = _devices()
    settings = {
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
    monkeypatch.setattr("dms.ui.device_controller.is_windows_audio_host", lambda: True)
    monkeypatch.setattr(audio_engine, "is_windows_audio_host", lambda: True)
    monkeypatch.setattr("dms.ui.device_controller.get_output_devices", lambda: outputs)
    monkeypatch.setattr("dms.ui.device_controller.get_input_devices", lambda: inputs)
    monkeypatch.setattr("dms.ui.device_controller.device_channel_count", lambda _device, _kind: 2)

    window = _window(make_main_window, settings)
    window.devices.refresh_devices()

    assert window.measure_tab.in_dev_combo.count() == 1
    assert window.measure_tab.out_dev_combo.count() == 1
    assert window.measure_tab.in_dev_combo.currentData() == 43
    assert window.measure_tab.out_dev_combo.currentData() == 5
    assert window._settings.get("input_device")["index"] == 43
    assert window._settings.get("input_device")["kind"] == "input"
    assert window._settings.get("output_device")["index"] == 5


def test_windows_advanced_mode_shows_all_backends(make_main_window, monkeypatch) -> None:
    outputs, inputs = _devices()
    settings = {
        "input_device": None,
        "output_device": None,
        "input_channel": 0,
        "windows_advanced_audio_drivers": True,
    }
    monkeypatch.setattr("dms.ui.device_controller.is_windows_audio_host", lambda: True)
    monkeypatch.setattr(audio_engine, "is_windows_audio_host", lambda: True)
    monkeypatch.setattr("dms.ui.device_controller.get_output_devices", lambda: outputs)
    monkeypatch.setattr("dms.ui.device_controller.get_input_devices", lambda: inputs)
    monkeypatch.setattr("dms.ui.device_controller.device_channel_count", lambda _device, _kind: 2)

    window = _window(make_main_window, settings)
    window.devices.refresh_devices()

    assert window.measure_tab.in_dev_combo.count() == 4
    assert window.measure_tab.out_dev_combo.count() == 3
    assert any(
        "Windows DirectSound" in window.measure_tab.in_dev_combo.itemText(i)
        for i in range(window.measure_tab.in_dev_combo.count())
    )


def test_windows_legacy_duplicate_resolves_to_wasapi_in_normal_mode(
    make_main_window, monkeypatch
) -> None:
    outputs, inputs = _devices()
    settings = {
        "input_device": "in 1-2 (motu m series)",
        "output_device": None,
        "input_channel": 0,
        "windows_advanced_audio_drivers": False,
    }
    monkeypatch.setattr("dms.ui.device_controller.is_windows_audio_host", lambda: True)
    monkeypatch.setattr(audio_engine, "is_windows_audio_host", lambda: True)
    monkeypatch.setattr("dms.ui.device_controller.get_output_devices", lambda: outputs)
    monkeypatch.setattr("dms.ui.device_controller.get_input_devices", lambda: inputs)
    monkeypatch.setattr("dms.ui.device_controller.device_channel_count", lambda _device, _kind: 2)

    window = _window(make_main_window, settings)
    window.devices.refresh_devices()

    assert window.measure_tab.in_dev_combo.currentData() == 43
    assert window._settings.get("input_device")["hostapi_name"] == "Windows WASAPI"


def test_windows_input_selection_auto_matches_output_backend(make_main_window, monkeypatch) -> None:
    outputs, inputs = _devices()
    settings = {
        "input_device": None,
        "output_device": None,
        "input_channel": 0,
        "windows_advanced_audio_drivers": True,
    }
    monkeypatch.setattr("dms.ui.device_controller.is_windows_audio_host", lambda: True)
    monkeypatch.setattr(audio_engine, "is_windows_audio_host", lambda: True)
    monkeypatch.setattr("dms.ui.device_controller.get_output_devices", lambda: outputs)
    monkeypatch.setattr("dms.ui.device_controller.get_input_devices", lambda: inputs)
    monkeypatch.setattr("dms.ui.device_controller.device_channel_count", lambda _device, _kind: 2)

    window = _window(make_main_window, settings)
    window.devices.refresh_devices()
    window.measure_tab.out_dev_combo.setCurrentIndex(window.measure_tab.out_dev_combo.findData(6))
    window.measure_tab.in_dev_combo.setCurrentIndex(window.measure_tab.in_dev_combo.findData(43))
    window.devices._sync_windows_output_to_input(show_status=False)

    assert window.measure_tab.out_dev_combo.currentData() == 5


def test_windows_mismatched_backends_block_queue_start(make_main_window, monkeypatch) -> None:
    outputs, inputs = _devices()
    settings = {
        "input_device": None,
        "output_device": None,
        "input_channel": 0,
        "windows_advanced_audio_drivers": True,
    }
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr("dms.ui.device_controller.is_windows_audio_host", lambda: True)
    monkeypatch.setattr(audio_engine, "is_windows_audio_host", lambda: True)
    monkeypatch.setattr("dms.ui.device_controller.get_output_devices", lambda: outputs)
    monkeypatch.setattr("dms.ui.device_controller.get_input_devices", lambda: inputs)
    monkeypatch.setattr("dms.ui.device_controller.device_channel_count", lambda _device, _kind: 2)
    monkeypatch.setattr(
        "dms.ui.main_window.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    window = _window(make_main_window, settings)
    window.devices.refresh_devices()
    # Input first: choosing an input re-matches the output, so the mismatch
    # only survives when the output is changed afterwards.
    window.measure_tab.in_dev_combo.setCurrentIndex(window.measure_tab.in_dev_combo.findData(43))
    window.measure_tab.out_dev_combo.setCurrentIndex(window.measure_tab.out_dev_combo.findData(6))

    window._start_queue()

    assert warnings
    assert "mismatch" in warnings[0][0].lower()
    assert window.start_next_sweep_count == 0


def test_windows_default_non_bluetooth_latency_is_high_until_user_override(
    make_main_window, monkeypatch
) -> None:
    settings = {
        "latency": "low",
        "latency_user_override": False,
        "bluetooth_headphone_mode": False,
    }
    monkeypatch.setattr("dms.ui.device_controller.is_windows_audio_host", lambda: True)

    window = _window(make_main_window, settings, stub_devices=True)
    assert window.devices.sweep_latency_mode() == "high"

    window._settings.set("latency_user_override", True)
    assert window.devices.sweep_latency_mode() == "low"


def test_non_windows_latency_behavior_is_unchanged(make_main_window, monkeypatch) -> None:
    settings = {
        "latency": "low",
        "latency_user_override": False,
        "bluetooth_headphone_mode": False,
    }
    monkeypatch.setattr("dms.ui.device_controller.is_windows_audio_host", lambda: False)

    window = _window(make_main_window, settings, stub_devices=True)
    assert window.devices.sweep_latency_mode() == "low"


def test_manual_refresh_reinitializes_backend_before_enumerating(
    make_main_window, monkeypatch
) -> None:
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
    settings = {
        "input_device": None,
        "output_device": None,
        "input_channel": 0,
        "windows_advanced_audio_drivers": False,
    }
    calls: list[str] = []
    monkeypatch.setattr("dms.ui.device_controller.is_windows_audio_host", lambda: False)
    monkeypatch.setattr(
        "dms.ui.device_controller.get_output_devices",
        lambda: calls.append("outputs") or current["outputs"],
    )
    monkeypatch.setattr(
        "dms.ui.device_controller.get_input_devices",
        lambda: calls.append("inputs") or current["inputs"],
    )
    monkeypatch.setattr("dms.ui.device_controller.device_channel_count", lambda _device, _kind: 2)

    def refresh_backend() -> bool:
        calls.append("backend")
        current["outputs"] = refreshed_outputs
        current["inputs"] = refreshed_inputs
        return True

    monkeypatch.setattr("dms.ui.device_controller.refresh_audio_backend", refresh_backend)

    window = _window(make_main_window, settings)
    stops = _count_level_monitor_stops(window)
    window.devices.refresh_devices()
    calls.clear()
    window.devices.manual_refresh_devices()

    assert calls[:3] == ["backend", "outputs", "inputs"]
    assert window.measure_tab.out_dev_combo.currentData() == 9
    assert window.measure_tab.in_dev_combo.currentData() == 10
    assert len(stops) == 1
    assert window.monitor_count == 2
    assert window._statusbar.currentMessage() == "Audio devices refreshed; selection changed."


def test_manual_refresh_preserves_valid_device_selection(make_main_window, monkeypatch) -> None:
    outputs, inputs = _devices()
    settings = {
        "input_device": None,
        "output_device": None,
        "input_channel": 1,
        "windows_advanced_audio_drivers": True,
    }
    monkeypatch.setattr("dms.ui.device_controller.is_windows_audio_host", lambda: False)
    monkeypatch.setattr("dms.ui.device_controller.get_output_devices", lambda: outputs)
    monkeypatch.setattr("dms.ui.device_controller.get_input_devices", lambda: inputs)
    monkeypatch.setattr("dms.ui.device_controller.device_channel_count", lambda _device, _kind: 2)
    monkeypatch.setattr("dms.ui.device_controller.refresh_audio_backend", lambda: True)

    window = _window(make_main_window, settings)
    window.devices.refresh_devices()
    window.measure_tab.out_dev_combo.setCurrentIndex(window.measure_tab.out_dev_combo.findData(6))
    window.measure_tab.in_dev_combo.setCurrentIndex(window.measure_tab.in_dev_combo.findData(17))
    # Channel last: choosing an input device rebuilds the channel list.
    window.measure_tab.ch_combo.setCurrentIndex(1)
    window._settings.set("input_channel", 1)
    window._settings.set("output_device", window.devices.current_output_device_setting())
    window._settings.set("input_device", window.devices.current_input_device_setting())

    window.devices.manual_refresh_devices()

    assert window.measure_tab.out_dev_combo.currentData() == 6
    assert window.measure_tab.in_dev_combo.currentData() == 17
    assert window.devices.current_input_channel() == 1
    assert window._statusbar.currentMessage() == "Audio devices refreshed."


def test_manual_refresh_falls_back_when_selected_device_disappears(
    make_main_window, monkeypatch
) -> None:
    outputs, inputs = _devices()
    current = {"outputs": outputs, "inputs": inputs}
    settings = {
        "input_device": None,
        "output_device": None,
        "input_channel": 0,
        "windows_advanced_audio_drivers": True,
    }
    monkeypatch.setattr("dms.ui.device_controller.is_windows_audio_host", lambda: False)
    monkeypatch.setattr("dms.ui.device_controller.get_output_devices", lambda: current["outputs"])
    monkeypatch.setattr("dms.ui.device_controller.get_input_devices", lambda: current["inputs"])
    monkeypatch.setattr("dms.ui.device_controller.device_channel_count", lambda _device, _kind: 2)

    def refresh_backend() -> bool:
        current["outputs"] = [outputs[0]]
        current["inputs"] = [inputs[2]]
        return True

    monkeypatch.setattr("dms.ui.device_controller.refresh_audio_backend", refresh_backend)

    window = _window(make_main_window, settings)
    window.devices.refresh_devices()
    window.measure_tab.out_dev_combo.setCurrentIndex(window.measure_tab.out_dev_combo.findData(6))
    window.measure_tab.in_dev_combo.setCurrentIndex(window.measure_tab.in_dev_combo.findData(17))
    window._settings.set("output_device", window.devices.current_output_device_setting())
    window._settings.set("input_device", window.devices.current_input_device_setting())

    window.devices.manual_refresh_devices()

    assert window.measure_tab.out_dev_combo.currentData() == 5
    assert window.measure_tab.in_dev_combo.currentData() == 43
    assert window._settings.get("output_device")["index"] == 5
    assert window._settings.get("input_device")["index"] == 43
    assert window._statusbar.currentMessage() == "Audio devices refreshed; selection changed."


def _forbid_enumeration(monkeypatch) -> None:
    def _fail():
        raise AssertionError("_check_devices must not enumerate on the GUI thread")

    monkeypatch.setattr("dms.ui.device_controller.get_output_devices", _fail)
    monkeypatch.setattr("dms.ui.device_controller.get_input_devices", _fail)


def test_check_devices_uses_the_lists_the_poller_hands_it(make_main_window, monkeypatch) -> None:
    """E5: the poller thread enumerates; the GUI slot only compares."""
    outputs, inputs = _devices()
    settings = {
        "input_device": None,
        "output_device": None,
        "input_channel": 0,
        "windows_advanced_audio_drivers": True,
    }
    monkeypatch.setattr("dms.ui.device_controller.is_windows_audio_host", lambda: False)
    monkeypatch.setattr("dms.ui.device_controller.get_output_devices", lambda: outputs)
    monkeypatch.setattr("dms.ui.device_controller.get_input_devices", lambda: inputs)
    monkeypatch.setattr("dms.ui.device_controller.device_channel_count", lambda _device, _kind: 2)
    window = _window(make_main_window, settings)
    window.devices.refresh_devices()

    refreshed: list[bool] = []
    window.devices.refresh_devices = lambda: refreshed.append(True)
    _forbid_enumeration(monkeypatch)

    # Same device set as the last refresh: nothing to do, nothing enumerated.
    window.devices.check_devices(outputs, inputs)
    assert refreshed == []

    # One output gone: the window re-selects because it is idle.
    window.devices.check_devices(outputs[1:], inputs)
    assert refreshed == [True]
    assert window.devices.dirty is False


def test_check_devices_defers_while_the_queue_holds_the_devices(
    make_main_window, monkeypatch
) -> None:
    outputs, inputs = _devices()
    settings = {
        "input_device": None,
        "output_device": None,
        "input_channel": 0,
        "windows_advanced_audio_drivers": True,
    }
    monkeypatch.setattr("dms.ui.device_controller.is_windows_audio_host", lambda: False)
    monkeypatch.setattr("dms.ui.device_controller.get_output_devices", lambda: outputs)
    monkeypatch.setattr("dms.ui.device_controller.get_input_devices", lambda: inputs)
    monkeypatch.setattr("dms.ui.device_controller.device_channel_count", lambda _device, _kind: 2)
    window = _window(make_main_window, settings)
    window.devices.refresh_devices()

    refreshed: list[bool] = []
    window.devices.refresh_devices = lambda: refreshed.append(True)
    window._queue_target = 1
    _forbid_enumeration(monkeypatch)

    window.devices.check_devices(outputs[1:], inputs)

    assert refreshed == []
    assert window.devices.dirty is True


def test_sync_device_poller_pauses_while_busy(make_main_window) -> None:
    settings = {"input_channel": 0, "windows_advanced_audio_drivers": True}

    class _Poller:
        def __init__(self) -> None:
            self.paused: list[bool] = []

        def pause(self, paused: bool) -> None:
            self.paused.append(bool(paused))

    poller = _Poller()

    window = _window(make_main_window, settings, stub_devices=True)
    window.devices.device_poller.stop()
    del window.devices.device_poller

    # No poller yet: the guard must not raise during window construction.
    window.devices.sync_device_poller()

    window.devices.device_poller = poller
    window.devices.sync_device_poller()
    window._queue_target = 1
    window.devices.sync_device_poller()
    window._queue_target = 0
    window.rnd.sweep_active = True
    window.devices.sync_device_poller()
    window.rnd.sweep_active = False
    window.devices.sync_device_poller()

    assert poller.paused == [False, True, True, False]
