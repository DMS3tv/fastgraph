"""E5: device enumeration runs off the GUI thread and reports only changes."""

import time

import pytest
from helpers import pump_until

from dms import audio_engine


def _pump_for(qapp, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()


class _FakeDevices:
    """Stands in for the four PortAudio enumerations, and counts them."""

    def __init__(self, monkeypatch) -> None:
        self.outputs = [{"index": 1, "name": "Out", "hostapi": 0}]
        self.inputs = [{"index": 2, "name": "In", "hostapi": 0}]
        self.polls = 0
        monkeypatch.setattr(audio_engine, "get_output_devices", self._get_outputs)
        monkeypatch.setattr(audio_engine, "get_input_devices", self._get_inputs)

    def _get_outputs(self) -> list[dict]:
        self.polls += 1
        return list(self.outputs)

    def _get_inputs(self) -> list[dict]:
        return list(self.inputs)


@pytest.fixture
def poller_devices(monkeypatch) -> _FakeDevices:
    return _FakeDevices(monkeypatch)


def _collect(poller) -> list[tuple[list, list]]:
    seen: list[tuple[list, list]] = []
    poller.devices_changed.connect(lambda out, ins: seen.append((out, ins)))
    return seen


def test_poller_emits_only_when_the_device_set_changes(qapp, poller_devices) -> None:
    poller = audio_engine.DevicePoller(interval_ms=10)
    seen = _collect(poller)
    poller.start()
    try:
        # The first poll is the baseline; an unchanged set never notifies.
        assert pump_until(qapp, lambda: poller_devices.polls >= 3)
        assert seen == []

        poller_devices.outputs.append({"index": 3, "name": "Out 2", "hostapi": 0})
        assert pump_until(qapp, lambda: len(seen) == 1)
        assert [d["index"] for d in seen[0][0]] == [1, 3]
        assert [d["index"] for d in seen[0][1]] == [2]

        polls_before = poller_devices.polls
        _pump_for(qapp, 0.2)
        assert poller_devices.polls > polls_before
        assert len(seen) == 1

        poller_devices.inputs.append({"index": 4, "name": "In 2", "hostapi": 0})
        assert pump_until(qapp, lambda: len(seen) == 2)
        assert [d["index"] for d in seen[1][1]] == [2, 4]

        poller_devices.outputs.pop()
        assert pump_until(qapp, lambda: len(seen) == 3)
        assert [d["index"] for d in seen[2][0]] == [1]
    finally:
        poller.stop()


def test_poller_can_report_its_first_snapshot_on_request(qapp, poller_devices) -> None:
    poller = audio_engine.DevicePoller(interval_ms=10, report_initial=True)
    seen = _collect(poller)
    poller.start()
    try:
        assert pump_until(qapp, lambda: len(seen) == 1)
        assert [d["index"] for d in seen[0][0]] == [1]
        assert [d["index"] for d in seen[0][1]] == [2]
        _pump_for(qapp, 0.15)
        assert len(seen) == 1
    finally:
        poller.stop()


def test_poller_ignores_fields_that_are_not_device_identity(qapp, poller_devices) -> None:
    poller = audio_engine.DevicePoller(interval_ms=10)
    seen = _collect(poller)
    poller.start()
    try:
        assert pump_until(qapp, lambda: poller_devices.polls >= 2)
        poller_devices.outputs[0]["default_samplerate"] = 44100.0
        _pump_for(qapp, 0.2)
        assert seen == []
    finally:
        poller.stop()


def test_pause_suppresses_both_polling_and_emission(qapp, poller_devices) -> None:
    poller = audio_engine.DevicePoller(interval_ms=10)
    seen = _collect(poller)
    poller.pause(True)
    poller.start()
    try:
        assert poller.is_paused() is True
        _pump_for(qapp, 0.2)
        assert poller_devices.polls == 0
        assert seen == []

        poller.pause(False)
        assert poller.is_paused() is False
        assert pump_until(qapp, lambda: poller_devices.polls >= 2)
        assert seen == []

        # Pausing again holds off a hotplug until the window resumes.
        poller.pause(True)
        polls_before = poller_devices.polls
        poller_devices.outputs.append({"index": 3, "name": "Out 2", "hostapi": 0})
        _pump_for(qapp, 0.2)
        assert poller_devices.polls == polls_before
        assert seen == []

        poller.pause(False)
        assert pump_until(qapp, lambda: len(seen) == 1)
        assert [d["index"] for d in seen[0][0]] == [1, 3]
    finally:
        poller.stop()


def test_stop_joins_the_thread_and_ends_polling(qapp, poller_devices) -> None:
    poller = audio_engine.DevicePoller(interval_ms=10)
    seen = _collect(poller)
    poller.start()
    thread = poller._thread
    assert thread is not None
    assert pump_until(qapp, lambda: poller_devices.polls >= 2)

    poller.stop()

    assert poller.is_running() is False
    assert thread.isFinished()
    assert poller._orphaned_threads == []

    polls_after_stop = poller_devices.polls
    poller_devices.outputs.append({"index": 3, "name": "Out 2", "hostapi": 0})
    _pump_for(qapp, 0.15)
    assert poller_devices.polls == polls_after_stop
    assert seen == []

    # stop() is idempotent, and start() after stop() polls again from a fresh
    # baseline (the hotplug missed while stopped is absorbed, not replayed).
    poller.stop()
    poller.start()
    try:
        assert pump_until(qapp, lambda: poller_devices.polls > polls_after_stop + 1)
        assert seen == []
        poller_devices.outputs.append({"index": 9, "name": "Out 3", "hostapi": 0})
        assert pump_until(qapp, lambda: len(seen) == 1)
        assert [d["index"] for d in seen[0][0]] == [1, 3, 9]
    finally:
        poller.stop()


def test_poller_survives_an_enumeration_failure(qapp, monkeypatch) -> None:
    def _boom() -> list[dict]:
        raise RuntimeError("PortAudio is unhappy")

    monkeypatch.setattr(audio_engine, "get_output_devices", _boom)
    monkeypatch.setattr(audio_engine, "get_input_devices", lambda: [])

    poller = audio_engine.DevicePoller(interval_ms=10)
    seen = _collect(poller)
    poller.start()
    try:
        _pump_for(qapp, 0.15)
        assert seen == []
    finally:
        poller.stop()
    assert poller.is_running() is False
