"""The packaged app name reaches the window title through FASTGRAPH_APP_NAME."""

from __future__ import annotations


def test_window_title_uses_default_name_without_env(make_main_window, monkeypatch) -> None:
    monkeypatch.delenv("FASTGRAPH_APP_NAME", raising=False)
    window = make_main_window()
    window._refresh_window_title()
    assert window.windowTitle().startswith("DMS fastgraph Beta — ")


def test_window_title_uses_packaged_app_name(make_main_window, monkeypatch) -> None:
    monkeypatch.setenv("FASTGRAPH_APP_NAME", "FastGraph Dev")
    window = make_main_window()
    window._refresh_window_title()
    assert window.windowTitle().startswith("DMS FastGraph Dev — ")
