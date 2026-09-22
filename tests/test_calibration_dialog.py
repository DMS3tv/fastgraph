import pytest

from dms.ui import calibration_dialog


def _dialog(qapp, channel: int) -> calibration_dialog.CalibrationDialog:
    return calibration_dialog.CalibrationDialog(
        device_index=8,
        device_name="pipewire",
        device_label="pipewire (ALSA)",
        channel=channel,
        fs=48000,
        buffer_size=256,
        cal_store=object(),
    )


def test_calibration_opens_only_through_selected_channel(qapp, monkeypatch) -> None:
    stream_calls = []

    class _FakeInputStream:
        def __init__(self, **kwargs):
            stream_calls.append(kwargs)

        def start(self) -> None:
            pass

    monkeypatch.setattr(
        calibration_dialog,
        "device_by_index",
        lambda device_index, kind="input": {"max_input_channels": 128},
    )
    monkeypatch.setattr(calibration_dialog.sd, "InputStream", _FakeInputStream)
    monkeypatch.setattr(calibration_dialog.QTimer, "singleShot", lambda *args: None)

    dialog = _dialog(qapp, channel=1)
    dialog._start_capture()

    assert stream_calls
    assert stream_calls[0]["device"] == 8
    assert stream_calls[0]["channels"] == 2
    assert dialog._capturing is True
    assert dialog._start_btn.isEnabled() is False


@pytest.mark.parametrize("channel", [-1, 128])
def test_calibration_rejects_invalid_channel_and_restores_controls(
    qapp, monkeypatch, channel
) -> None:
    stream_calls = []

    monkeypatch.setattr(
        calibration_dialog,
        "device_by_index",
        lambda device_index, kind="input": {"max_input_channels": 128},
    )
    monkeypatch.setattr(
        calibration_dialog.sd,
        "InputStream",
        lambda **kwargs: stream_calls.append(kwargs),
    )

    dialog = _dialog(qapp, channel=channel)
    dialog._start_capture()

    assert stream_calls == []
    assert dialog._capturing is False
    assert dialog._start_btn.isEnabled() is True
    assert dialog._accept_btn.isEnabled() is False
    assert "is not available" in dialog._status.text()


def test_calibration_write_failure_is_logged(monkeypatch, caplog) -> None:
    import dms.calibration as calibration_module

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(calibration_module, "atomic_write_json", fail)
    store = calibration_module.CalibrationStore()

    store.set_sensitivity("Mic", 1.5)

    assert store._save() is False
    assert any(
        record.levelname == "ERROR" and "could not be saved" in record.getMessage()
        for record in caplog.records
    )
