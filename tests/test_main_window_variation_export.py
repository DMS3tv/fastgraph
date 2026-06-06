from pathlib import Path
from types import SimpleNamespace

import numpy as np

from dms.session import SessionData
from dms.ui.main_window import AppState, MainWindow


class _FakeButton:
    def __init__(self) -> None:
        self.text = ""
        self.tooltip = ""
        self.enabled = None

    def setText(self, text: str) -> None:
        self.text = text

    def setToolTip(self, tooltip: str) -> None:
        self.tooltip = tooltip

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = enabled


class _FakeToggle:
    def __init__(self, checked: bool) -> None:
        self._checked = checked

    def isChecked(self) -> bool:
        return self._checked


class _FakeLineEdit:
    def __init__(self, text: str = "") -> None:
        self._text = text

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:
        self._text = text


class _FakeSettings:
    def __init__(self) -> None:
        self.values = {"export_directory": ""}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value) -> None:
        self.values[key] = value


def _fake_window(*, variation_mode: bool = False):
    fake = SimpleNamespace(
        _state=AppState.IDLE,
        _average=(np.array([100.0]), np.array([1.0])),
        _variation=(
            np.array([100.0]),
            np.array([-2.0]),
            np.array([-1.0]),
            np.array([1.0]),
            np.array([2.0]),
            np.array([0.0]),
        ),
        _variation_toggle=_FakeToggle(variation_mode),
        _export_btn=_FakeButton(),
        _upload_btn=_FakeButton(),
    )
    fake._bottom_view_mode = lambda: MainWindow._bottom_view_mode(fake)
    return fake


def test_sync_export_button_switches_label_and_keeps_upload_average_based() -> None:
    fake = _fake_window(variation_mode=True)
    fake._average = (np.array([100.0]), np.array([1.0]))
    fake._variation = None

    MainWindow._sync_export_button(fake)

    assert fake._export_btn.text == "Export Variation…"
    assert "percentile" in fake._export_btn.tooltip.lower()
    assert fake._export_btn.enabled is False
    assert fake._upload_btn.enabled is True

    fake._variation_toggle = _FakeToggle(False)
    MainWindow._sync_export_button(fake)

    assert fake._export_btn.text == "Export Average…"
    assert "rew-style" in fake._export_btn.tooltip.lower()
    assert fake._export_btn.enabled is True
    assert fake._upload_btn.enabled is True


def test_sync_export_button_disables_upload_without_average_even_with_variation() -> None:
    fake = _fake_window(variation_mode=True)
    fake._average = None

    MainWindow._sync_export_button(fake)

    assert fake._export_btn.enabled is True
    assert fake._upload_btn.enabled is False


def test_export_variation_uses_current_variation_data(monkeypatch, tmp_path: Path) -> None:
    written: dict[str, object] = {}
    save_path = tmp_path / "out.txt"

    monkeypatch.setattr(
        "dms.ui.main_window.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(save_path), ""),
    )
    monkeypatch.setattr(
        "dms.ui.main_window.export_variation",
        lambda **kwargs: written.update(kwargs),
    )

    status_messages: list[str] = []
    settings = _FakeSettings()
    fake = SimpleNamespace(
        _variation_toggle=_FakeToggle(True),
        _variation=(
            np.array([100.0]),
            np.array([-2.0]),
            np.array([-1.0]),
            np.array([1.0]),
            np.array([2.0]),
            np.array([0.0]),
        ),
        _average=(np.array([100.0]), np.array([1.0])),
        _session=SessionData(rig="GRAS", brand="DMS", model="Example"),
        _export_dir_input=_FakeLineEdit(""),
        _settings=settings,
        _is_hrtf_active=lambda: False,
        _hrtf=None,
        _kept_curves=[(np.array([100.0]), np.array([1.0]))],
        _statusbar=SimpleNamespace(showMessage=lambda message: status_messages.append(message)),
    )
    fake._bottom_view_mode = lambda: MainWindow._bottom_view_mode(fake)
    fake._export_variation = lambda: MainWindow._export_variation(fake)

    MainWindow._export(fake)

    assert np.array_equal(written["freqs"], np.array([100.0]))
    assert np.array_equal(written["p10_db"], np.array([-2.0]))
    assert np.array_equal(written["p25_db"], np.array([-1.0]))
    assert np.array_equal(written["median_db"], np.array([0.0]))
    assert np.array_equal(written["p75_db"], np.array([1.0]))
    assert np.array_equal(written["p90_db"], np.array([2.0]))
    assert written["output_path"] == save_path
    assert written["n_sweeps"] == 1
    assert written["smoothing_fraction"] == 48
    assert settings.values["export_directory"] == str(tmp_path)
    assert status_messages[-1].startswith("Exported variation:")


def test_export_variation_empty_state_has_variation_copy(monkeypatch) -> None:
    info_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "dms.ui.main_window.QMessageBox.information",
        lambda _parent, title, message: info_calls.append((title, message)),
    )

    fake = SimpleNamespace(
        _variation_toggle=_FakeToggle(True),
        _variation=None,
    )
    fake._bottom_view_mode = lambda: MainWindow._bottom_view_mode(fake)
    fake._export_variation = lambda: MainWindow._export_variation(fake)

    MainWindow._export(fake)

    assert info_calls == [("Nothing to Export", "No variation band available yet.")]
