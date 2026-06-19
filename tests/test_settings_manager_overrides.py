import json
from pathlib import Path

import dms.settings_manager as settings_module
from dms.settings_manager import SettingsManager


def test_session_overrides_are_temporary_until_saved(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    settings = SettingsManager()

    original = settings.get("sweep_duration")
    settings.set_session("sweep_duration", 4.0)
    assert settings.get("sweep_duration") == 4.0
    assert not (tmp_path / "settings.json").exists()

    fresh = SettingsManager()
    assert fresh.get("sweep_duration") == original

    assert settings.save_session("sweep_duration") == ["sweep_duration"]
    assert json.loads((tmp_path / "settings.json").read_text())["sweep_duration"] == 4.0


def test_persistent_ui_write_replaces_session_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    settings = SettingsManager()
    settings.set_session("buffer_size", 512)
    settings.set("buffer_size", 2048)

    assert settings.get("buffer_size") == 2048
    assert "buffer_size" not in settings.session_overrides()

