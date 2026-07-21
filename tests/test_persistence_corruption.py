from pathlib import Path

import dms.calibration as calibration_module
import dms.settings_manager as settings_module
from dms.automation import (
    AutomationDefinition,
    save_automation,
    scan_automation_directory,
)
from dms.calibration import CalibrationStore
from dms.settings_manager import SettingsManager


def test_settings_manager_resets_and_backs_up_corrupt_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{not valid json", encoding="utf-8")

    settings = SettingsManager()

    assert settings.get("theme") == "dark"
    assert settings.load_error is not None
    assert "corrupt" in settings.load_error.lower()
    corrupt_path = tmp_path / "settings.json.corrupt"
    assert corrupt_path.read_text(encoding="utf-8") == "{not valid json"
    assert not settings_path.exists()


def test_settings_manager_no_error_when_file_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)

    settings = SettingsManager()

    assert settings.load_error is None


def test_settings_manager_no_error_on_valid_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    settings = SettingsManager()
    settings.set("sweep_duration", 3.5)

    reloaded = SettingsManager()

    assert reloaded.load_error is None
    assert reloaded.get("sweep_duration") == 3.5


def test_calibration_store_resets_and_backs_up_corrupt_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(calibration_module, "_config_dir", lambda: tmp_path)
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text("not json at all", encoding="utf-8")

    store = CalibrationStore()

    assert store.get_sensitivity("mic") is None
    assert store.load_error is not None
    assert "corrupt" in store.load_error.lower()
    corrupt_path = tmp_path / "calibration.json.corrupt"
    assert corrupt_path.read_text(encoding="utf-8") == "not json at all"
    assert not calibration_path.exists()


def test_calibration_store_no_error_when_never_calibrated(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(calibration_module, "_config_dir", lambda: tmp_path)

    store = CalibrationStore()

    assert store.load_error is None


def test_scan_automation_directory_skips_corrupt_file_with_reason(tmp_path: Path) -> None:
    save_automation(tmp_path / "good.fastgraph-automation.json", AutomationDefinition(name="Good"))
    bad_path = tmp_path / "bad.fastgraph-automation.json"
    bad_path.write_text("{not valid json", encoding="utf-8")

    loaded, skipped = scan_automation_directory(tmp_path)

    assert [automation.name for _path, automation in loaded] == ["Good"]
    assert len(skipped) == 1
    skipped_path, reason = skipped[0]
    assert skipped_path == bad_path
    assert reason
