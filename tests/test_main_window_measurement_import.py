from pathlib import Path
from types import SimpleNamespace

import numpy as np

from dms.ui.main_window import AppState, MainWindow


def _make_fake_main_window(state: str = AppState.IDLE):
    calls: dict[str, int] = {
        "recompute_average": 0,
        "recompute_variation": 0,
        "update_queue_progress": 0,
        "update_plots": 0,
    }
    status_messages: list[str] = []
    fake = SimpleNamespace(
        _state=state,
        _kept_curves=[],
        _recompute_average=lambda: calls.__setitem__("recompute_average", calls["recompute_average"] + 1),
        _recompute_variation=lambda: calls.__setitem__("recompute_variation", calls["recompute_variation"] + 1),
        _update_queue_progress=lambda: calls.__setitem__("update_queue_progress", calls["update_queue_progress"] + 1),
        _update_plots=lambda: calls.__setitem__("update_plots", calls["update_plots"] + 1),
        _statusbar=SimpleNamespace(showMessage=lambda msg: status_messages.append(msg)),
    )
    return fake, calls, status_messages


def test_import_dropped_measurement_files_appends_curves(monkeypatch, tmp_path: Path) -> None:
    good = tmp_path / "good.txt"
    bad = tmp_path / "bad.txt"
    good.write_text("100 1\n200 2\n")
    bad.write_text("header only\n")

    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "dms.ui.main_window.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    monkeypatch.setattr(
        "dms.ui.main_window.QMessageBox.information",
        lambda *_args, **_kwargs: None,
    )

    fake, calls, status_messages = _make_fake_main_window()
    MainWindow._import_dropped_measurement_files(fake, [str(good), str(bad)])

    assert len(fake._kept_curves) == 1
    freqs, mags = fake._kept_curves[0]
    assert np.allclose(freqs, np.array([100.0, 200.0]))
    assert np.allclose(mags, np.array([1.0, 2.0]))
    assert calls["recompute_average"] == 1
    assert calls["recompute_variation"] == 1
    assert calls["update_queue_progress"] == 1
    assert calls["update_plots"] == 1
    assert warnings
    assert "loaded 1, failed 1" in status_messages[-1].lower()


def test_import_dropped_measurement_files_blocked_when_busy(monkeypatch, tmp_path: Path) -> None:
    info_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "dms.ui.main_window.QMessageBox.information",
        lambda _parent, title, message: info_calls.append((title, message)),
    )
    monkeypatch.setattr(
        "dms.ui.main_window.QMessageBox.warning",
        lambda *_args, **_kwargs: None,
    )

    fake, calls, status_messages = _make_fake_main_window(state=AppState.QUEUE_RUNNING)
    path = tmp_path / "curve.txt"
    path.write_text("100 1\n200 2\n")
    MainWindow._import_dropped_measurement_files(fake, [str(path)])

    assert len(fake._kept_curves) == 0
    assert info_calls
    assert "only available while idle" in info_calls[0][1].lower()
    assert "blocked" in status_messages[-1].lower()
    assert calls["update_plots"] == 0
