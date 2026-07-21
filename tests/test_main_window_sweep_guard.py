import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

import dms.settings_manager as settings_module
import dms.ui.main_window as main_window_module
from dms.session import SessionData
from dms.settings_manager import SettingsManager
from dms.theme import ThemeController
from dms.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(qapp, monkeypatch, tmp_path: Path) -> MainWindow:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(MainWindow, "_refresh_devices", lambda self: None)
    monkeypatch.setattr(MainWindow, "_start_level_monitor", lambda self: None)
    monkeypatch.setattr(MainWindow, "_start_update_check", lambda self: None)
    settings = SettingsManager()
    return MainWindow(
        SessionData(rig="Rig", brand="DMS", model="Demo"),
        settings,
        ThemeController(qapp, settings),
    )


class _FakeRunningThread:
    def isRunning(self) -> bool:
        return True


def test_start_rnd_sweep_is_noop_while_sweep_thread_running(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)

    constructed = []
    monkeypatch.setattr(
        main_window_module,
        "SweepWorker",
        lambda *a, **k: constructed.append((a, k)) or pytest.fail("SweepWorker should not be constructed"),
    )

    window._sweep_thread = _FakeRunningThread()
    window._current_sweep_attempts = 0

    window._start_rnd_sweep()

    assert constructed == []
    assert window._current_sweep_attempts == 0
    window.close()


def test_start_next_sweep_is_noop_while_sweep_thread_running(qapp, monkeypatch, tmp_path: Path) -> None:
    window = _window(qapp, monkeypatch, tmp_path)

    constructed = []
    monkeypatch.setattr(
        main_window_module,
        "SweepWorker",
        lambda *a, **k: constructed.append((a, k)) or pytest.fail("SweepWorker should not be constructed"),
    )

    window._sweep_thread = _FakeRunningThread()
    window._queue_target = 1
    window._queue_index = 0
    window._current_sweep_attempts = 0

    window._start_next_sweep()

    assert constructed == []
    assert window._current_sweep_attempts == 0
    window.close()
