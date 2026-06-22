import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QCheckBox, QComboBox, QLabel

import dms.ui.main_window as main_window_module
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


def _app() -> QApplication:
    global _APP
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    _APP = app
    return app


def _write_hrtf(path: Path) -> None:
    path.write_text("100 1\n1000 2\n", encoding="utf-8")


def _fake_window(settings: _Settings):
    fake = SimpleNamespace(
        _settings=settings,
        _hrtf=None,
        _hrtf_options=[],
        _hrtf_combo=QComboBox(),
        _hrtf_toggle=QCheckBox(),
        _hrtf_label=QLabel(),
        _statusbar=_Status(),
        update_count=0,
    )
    fake._built_in_hrtf_paths = lambda: MainWindow._built_in_hrtf_paths(fake)
    fake._refresh_hrtf_options = lambda: MainWindow._refresh_hrtf_options(fake)
    fake._sync_hrtf_ui = lambda: MainWindow._sync_hrtf_ui(fake)
    fake._restore_hrtf_state = lambda: MainWindow._restore_hrtf_state(fake)
    fake._on_hrtf_selected = lambda: MainWindow._on_hrtf_selected(fake)
    fake._update_plots = lambda: setattr(fake, "update_count", fake.update_count + 1)
    fake._hrtf_combo.currentIndexChanged.connect(lambda _index: fake._on_hrtf_selected())
    return fake


def test_hrtf_dropdown_reads_fastgraph_hrtf_folder(monkeypatch, tmp_path: Path) -> None:
    _app()
    hrtf_dir = tmp_path / "HRTFs"
    hrtf_dir.mkdir()
    _write_hrtf(hrtf_dir / "Beta.txt")
    _write_hrtf(hrtf_dir / "Alpha.txt")
    monkeypatch.setattr(main_window_module, "HRTF_DIR", hrtf_dir)
    fake = _fake_window(_Settings())

    fake._refresh_hrtf_options()

    assert [fake._hrtf_combo.itemText(i) for i in range(fake._hrtf_combo.count())] == [
        "None",
        "Alpha",
        "Beta",
    ]


def test_selecting_built_in_hrtf_loads_and_enables_compensation(
    monkeypatch, tmp_path: Path
) -> None:
    _app()
    hrtf_dir = tmp_path / "HRTFs"
    hrtf_dir.mkdir()
    hrtf_path = hrtf_dir / "Fixture A.txt"
    _write_hrtf(hrtf_path)
    monkeypatch.setattr(main_window_module, "HRTF_DIR", hrtf_dir)
    settings = _Settings()
    fake = _fake_window(settings)
    fake._refresh_hrtf_options()

    fake._hrtf_combo.setCurrentIndex(fake._hrtf_combo.findData(str(hrtf_path)))

    assert fake._hrtf is not None
    assert fake._hrtf.name == "Fixture A"
    assert settings.data["hrtf_path"] == str(hrtf_path)
    assert fake._hrtf_toggle.isEnabled()
    assert fake._hrtf_toggle.isChecked()
    assert fake._hrtf_label.text() == "Fixture A"
    assert fake._hrtf_label.toolTip() == str(hrtf_path)
    assert fake.update_count == 1


def test_selecting_none_clears_hrtf_and_disables_compensation(
    monkeypatch, tmp_path: Path
) -> None:
    _app()
    hrtf_dir = tmp_path / "HRTFs"
    hrtf_dir.mkdir()
    hrtf_path = hrtf_dir / "Fixture A.txt"
    _write_hrtf(hrtf_path)
    monkeypatch.setattr(main_window_module, "HRTF_DIR", hrtf_dir)
    settings = _Settings()
    fake = _fake_window(settings)
    fake._refresh_hrtf_options()
    fake._hrtf_combo.setCurrentIndex(fake._hrtf_combo.findData(str(hrtf_path)))

    fake._hrtf_combo.setCurrentIndex(0)

    assert fake._hrtf is None
    assert settings.data["hrtf_path"] is None
    assert not fake._hrtf_toggle.isEnabled()
    assert not fake._hrtf_toggle.isChecked()
    assert fake._hrtf_label.text() == "None"
    assert fake._hrtf_label.toolTip() == ""


def test_restore_ignores_missing_or_legacy_custom_hrtf_path(
    monkeypatch, tmp_path: Path
) -> None:
    _app()
    hrtf_dir = tmp_path / "HRTFs"
    hrtf_dir.mkdir()
    _write_hrtf(hrtf_dir / "Built In.txt")
    custom_path = tmp_path / "Custom.txt"
    _write_hrtf(custom_path)
    monkeypatch.setattr(main_window_module, "HRTF_DIR", hrtf_dir)
    settings = _Settings({"hrtf_path": str(custom_path)})
    fake = _fake_window(settings)

    fake._restore_hrtf_state()

    assert fake._hrtf is None
    assert settings.data["hrtf_path"] is None
    assert fake._hrtf_combo.currentText() == "None"
