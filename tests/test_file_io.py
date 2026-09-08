"""Crash safety and corrupt-file recovery for the shared persistence helpers."""

import json
import os
import stat
from pathlib import Path

import pytest

import dms.settings_manager as settings_module
from dms.calibration import CalibrationStore
from dms.file_io import (
    atomic_write_json,
    atomic_write_text,
    backup_corrupt_file,
    load_json_with_backup,
)
from dms.settings_manager import SettingsManager


def _corrupt_backups(directory: Path, stem: str) -> list[Path]:
    return sorted(directory.glob(f"{stem}.corrupt-*"))


# --- atomic writes ---------------------------------------------------------


def test_atomic_write_json_creates_parents_and_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper" / "data.json"
    atomic_write_json(target, {"a": 1, "b": [2, 3]})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "b": [2, 3]}


def test_atomic_write_json_applies_owner_only_mode(tmp_path: Path) -> None:
    target = tmp_path / "secret.json"
    atomic_write_json(target, {"token": "x"}, mode=0o600)
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode & 0o077 == 0, f"group/other bits set: {oct(mode)}"


def test_atomic_write_json_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    atomic_write_json(target, {"a": 1})
    assert [p.name for p in tmp_path.iterdir()] == ["data.json"]


def test_crash_mid_write_leaves_the_original_intact(tmp_path: Path, monkeypatch) -> None:
    """A failure after the temp file is written must not touch the real file."""
    target = tmp_path / "settings.json"
    atomic_write_json(target, {"generation": 1})
    original = target.read_text(encoding="utf-8")

    def _boom(_src, _dst):
        raise OSError("simulated crash between fsync and rename")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        atomic_write_json(target, {"generation": 2})

    assert target.read_text(encoding="utf-8") == original
    # And the half-written temp file is cleaned up rather than left as litter.
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_unserializable_value_never_touches_the_target(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    atomic_write_json(target, {"ok": True})
    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": object()})
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_atomic_write_text_replaces_content(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    atomic_write_text(target, "first", mode=None)
    atomic_write_text(target, "second", mode=None)
    assert target.read_text(encoding="utf-8") == "second"


# --- corrupt-file recovery -------------------------------------------------


def test_load_json_with_backup_returns_none_for_a_missing_file(tmp_path: Path) -> None:
    assert load_json_with_backup(tmp_path / "nope.json") == (None, None)


def test_load_json_with_backup_reads_an_object(tmp_path: Path) -> None:
    path = tmp_path / "good.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    data, error = load_json_with_backup(path)
    assert data == {"a": 1}
    assert error is None


def test_invalid_json_is_moved_aside_and_reported(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{not json at all", encoding="utf-8")

    data, error = load_json_with_backup(path)

    assert data is None
    assert not path.exists()
    backups = _corrupt_backups(tmp_path, "settings.json")
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{not json at all"
    assert backups[0].name in error


def test_non_object_json_is_treated_as_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    data, error = load_json_with_backup(path)
    assert data is None
    assert "list" in error
    assert len(_corrupt_backups(tmp_path, "settings.json")) == 1


def test_undecodable_bytes_are_treated_as_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_bytes(b"\xff\xfe\x00\x00 not utf-8")
    data, error = load_json_with_backup(path)
    assert data is None
    assert "UTF-8" in error
    assert len(_corrupt_backups(tmp_path, "settings.json")) == 1


def test_repeated_corruption_never_overwrites_an_earlier_backup(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    for content in ("{bad one", "{bad two"):
        path.write_text(content, encoding="utf-8")
        load_json_with_backup(path)
    assert len(_corrupt_backups(tmp_path, "settings.json")) == 2


def test_backup_corrupt_file_returns_none_when_the_file_is_gone(tmp_path: Path) -> None:
    assert backup_corrupt_file(tmp_path / "absent.json") is None


# --- stores built on the helpers -------------------------------------------


def test_settings_manager_reports_a_corrupt_file_and_keeps_a_backup(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text("{truncated", encoding="utf-8")

    settings = SettingsManager()

    assert settings.load_error is not None
    assert "settings.json" in settings.load_error
    assert len(_corrupt_backups(tmp_path, "settings.json")) == 1
    # Defaults are in use, and the next write starts a clean file.
    assert settings.get("sample_rate") == 48000
    settings.set("sample_rate", 44100)
    assert json.loads((tmp_path / "settings.json").read_text())["sample_rate"] == 44100


def test_settings_manager_has_no_load_error_for_a_healthy_file(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    SettingsManager().set("theme", "light")
    reloaded = SettingsManager()
    assert reloaded.load_error is None
    assert reloaded.corrected_keys == []
    assert reloaded.get("theme") == "light"


def test_settings_file_is_written_owner_only(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    SettingsManager().set("theme", "light")
    mode = stat.S_IMODE((tmp_path / "settings.json").stat().st_mode)
    assert mode & 0o077 == 0, f"credentials file is group/world readable: {oct(mode)}"


def test_calibration_store_backs_up_a_corrupt_file(monkeypatch, tmp_path: Path) -> None:
    import dms.calibration as calibration_module

    monkeypatch.setattr(calibration_module, "_config_dir", lambda: tmp_path)
    (tmp_path / "calibration.json").write_text("]]not json[[", encoding="utf-8")

    store = CalibrationStore()

    assert store.load_error is not None
    assert len(_corrupt_backups(tmp_path, "calibration.json")) == 1
    assert store.get_sensitivity("Mic") is None
    store.set_sensitivity("Mic", 0.5)
    assert CalibrationStore().get_sensitivity("Mic") == 0.5


def test_calibration_store_drops_non_numeric_entries(monkeypatch, tmp_path: Path) -> None:
    import dms.calibration as calibration_module

    monkeypatch.setattr(calibration_module, "_config_dir", lambda: tmp_path)
    (tmp_path / "calibration.json").write_text(
        json.dumps({"Good": 1.5, "Bad": "not a number"}), encoding="utf-8"
    )
    store = CalibrationStore()
    assert store.get_sensitivity("Good") == 1.5
    assert store.get_sensitivity("Bad") is None
    assert store.load_error is None
