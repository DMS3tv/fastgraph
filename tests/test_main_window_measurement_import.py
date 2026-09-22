from pathlib import Path

import numpy as np

from dms.measure_queue import QueueState

_COUNTED = ("_recompute_average", "_recompute_variation", "_update_queue_progress", "_update_plots")


def _counting_window(make_main_window, state: str = QueueState.IDLE):
    """A real window whose follow-up refreshes are replaced by counters."""
    window = make_main_window()
    window._state = state
    calls = dict.fromkeys(_COUNTED, 0)
    for name in _COUNTED:
        setattr(window, name, lambda name=name: calls.__setitem__(name, calls[name] + 1))
    return window, calls


def test_import_dropped_measurement_files_appends_curves(
    make_main_window, monkeypatch, tmp_path: Path
) -> None:
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

    window, calls = _counting_window(make_main_window)
    window._import_dropped_measurement_files([str(good), str(bad)])

    assert len(window._kept_curves) == 1
    freqs, mags = window._kept_curves[0]
    assert np.allclose(freqs, np.array([100.0, 200.0]))
    assert np.allclose(mags, np.array([1.0, 2.0]))
    assert calls["_recompute_average"] == 1
    assert calls["_recompute_variation"] == 1
    assert calls["_update_queue_progress"] == 1
    assert calls["_update_plots"] == 1
    assert warnings
    assert "loaded 1, failed 1" in window._statusbar.currentMessage().lower()


def test_import_dropped_measurement_files_blocked_when_busy(
    make_main_window, monkeypatch, tmp_path: Path
) -> None:
    info_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "dms.ui.main_window.QMessageBox.information",
        lambda _parent, title, message: info_calls.append((title, message)),
    )
    monkeypatch.setattr(
        "dms.ui.main_window.QMessageBox.warning",
        lambda *_args, **_kwargs: None,
    )

    window, calls = _counting_window(make_main_window, state=QueueState.QUEUE_RUNNING)
    path = tmp_path / "curve.txt"
    path.write_text("100 1\n200 2\n")
    window._import_dropped_measurement_files([str(path)])

    assert len(window._kept_curves) == 0
    assert info_calls
    assert "only available while idle" in info_calls[0][1].lower()
    assert "blocked" in window._statusbar.currentMessage().lower()
    assert calls["_update_plots"] == 0
