"""Shared pytest configuration for the Fastgraph test suite.

Responsibilities:

- Force the offscreen Qt platform before any Qt import so no test opens a
  native window regardless of collection order.
- Provide one session-scoped ``qapp``.
- Redirect every on-disk store (settings, calibration, console log, R&D
  recovery) into the test's ``tmp_path`` so the suite can never touch the
  developer's real application data or saved credentials.
- Flush Qt's deferred deletes after every test. Qt 6 does not deliver
  ``DeferredDelete`` events from ``processEvents()``; without a running event
  loop every closed window's C++ widgets stay alive, and each new
  ``ThemeController`` re-applies the application stylesheet to all of them.
  That was the cause of the suite's quadratic slowdown.
- Warn (not fail) when a test leaves a large number of new widgets alive.
- Offer ``make_main_window`` so window tests share one construction path.
"""

from __future__ import annotations

import gc
import os
import warnings
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import contextlib

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication

import dms.calibration as calibration_module
import dms.settings_manager as settings_module

_LEAK_WARN_THRESHOLD = 50


def flush_deferred_deletes() -> None:
    """Deliver pending ``deleteLater()`` requests and collect Python wrappers."""
    gc.collect()
    if QCoreApplication.instance() is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def _live_widget_count() -> int:
    app = QApplication.instance()
    if app is None:
        return 0
    return len(app.allWidgets())


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _isolated_app_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every on-disk store at ``tmp_path``.

    ``dms.calibration`` imports ``_config_dir`` by name, so it is patched
    separately. ``config_dir()`` in ``dms.settings_manager`` looks the private
    function up at call time, so the console log and R&D recovery paths follow
    the settings patch automatically.
    """
    config_dir = tmp_path / "config"
    monkeypatch.setattr(settings_module, "_config_dir", lambda: config_dir)
    monkeypatch.setattr(calibration_module, "_config_dir", lambda: config_dir)
    return config_dir


def _real_confirm_measure_close():
    from dms.ui.measure_io import MeasureIO

    return MeasureIO.__dict__["confirm_close"]


#: The unpatched close-time discard prompt, for the one test that exercises it.
REAL_CONFIRM_MEASURE_CLOSE = _real_confirm_measure_close()


@pytest.fixture(autouse=True)
def _no_modal_close_prompts(monkeypatch: pytest.MonkeyPatch):
    """Windows built outside the factory would block on the discard prompt."""
    from dms.ui.measure_io import MeasureIO

    monkeypatch.setattr(MeasureIO, "confirm_close", lambda self: True)


@pytest.fixture(autouse=True)
def _flush_qt_deletes(request: pytest.FixtureRequest):
    before = _live_widget_count()
    yield
    flush_deferred_deletes()
    after = _live_widget_count()
    grown = after - before
    if grown > _LEAK_WARN_THRESHOLD:
        warnings.warn(
            f"{request.node.nodeid} left {grown} new Qt widgets alive "
            f"({before} -> {after}); close and deleteLater() every top-level widget.",
            pytest.PytestWarning,
            stacklevel=1,
        )


@pytest.fixture
def make_main_window(qapp, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Factory for a ``MainWindow`` with hardware and network paths stubbed.

    Usage::

        window = make_main_window(theme="dark", settings={"queue_count": 3})

    Every window created here is closed and flushed at teardown, even when the
    test fails part-way through.
    """
    from dms.session import SessionData
    from dms.settings_manager import SettingsManager
    from dms.theme import ThemeController
    from dms.ui.main_window import MainWindow
    from dms.ui.update_check import UpdateCheck

    created: list[Any] = []

    def _make(
        *,
        theme: str | None = None,
        settings: dict[str, Any] | None = None,
        session: SessionData | None = None,
        stub_devices: bool = True,
        stub_level_monitor: bool = True,
        stub_update_check: bool = True,
        confirm_rnd_close: bool = True,
        settings_manager: SettingsManager | None = None,
        theme_controller: ThemeController | None = None,
    ) -> Any:
        if stub_devices:
            monkeypatch.setattr(MainWindow, "_refresh_devices", lambda self: None)
        if stub_level_monitor:
            monkeypatch.setattr(MainWindow, "_start_level_monitor", lambda self: None)
        if stub_update_check:
            monkeypatch.setattr(UpdateCheck, "start", lambda self: None)
        manager = settings_manager or SettingsManager()
        if theme is not None:
            manager.set("theme", theme)
        for key, value in (settings or {}).items():
            manager.set(key, value)
        controller = theme_controller or ThemeController(qapp, manager)
        window = MainWindow(
            session or SessionData(rig="Rig", brand="DMS", model="Demo"),
            manager,
            controller,
        )
        if confirm_rnd_close:
            # Both close-time prompts are modal dialogs; in a headless test
            # they would block forever.
            window._confirm_rnd_close = lambda: True
            window.measure_io.confirm_close = lambda: True
        created.append(window)
        return window

    yield _make

    # Release the Python reference instead of calling deleteLater(): the window
    # owns parentless top-level widgets and menus only through its __dict__,
    # and deleting the C++ object first strands about 180 of them per window.
    # Dropping the last reference lets sip destroy the whole tree together.
    while created:
        window = created.pop()
        with contextlib.suppress(Exception):
            window.close()
        del window
    flush_deferred_deletes()
