import json
import stat
from pathlib import Path

from helpers import corrupt_backups

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


# --- type coercion on load -------------------------------------------------


def _write_settings(tmp_path: Path, payload: dict) -> None:
    (tmp_path / "settings.json").write_text(json.dumps(payload), encoding="utf-8")


def test_numeric_strings_are_coerced_to_their_declared_types(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    _write_settings(
        tmp_path,
        {
            "sample_rate": "44100",
            "buffer_size": 512.0,
            "queue_count": "3",
            "squiglink_port": "2022",
            "sweep_duration": "3.5",
            "snr_warn_db": 12,
            "queue_output_level_db": "-9",
        },
    )
    settings = SettingsManager()

    assert settings.get("sample_rate") == 44100
    assert isinstance(settings.get("sample_rate"), int)
    assert settings.get("buffer_size") == 512
    assert isinstance(settings.get("buffer_size"), int)
    assert settings.get("queue_count") == 3
    assert settings.get("squiglink_port") == 2022
    assert settings.get("sweep_duration") == 3.5
    assert isinstance(settings.get("snr_warn_db"), float)
    assert settings.get("queue_output_level_db") == -9.0
    assert settings.corrected_keys == []


def test_boolean_strings_are_coerced(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    _write_settings(
        tmp_path,
        {
            "update_check_enabled": "false",
            "measure_two_channel_enabled": "yes",
            "confirm_clear_measurements": 0,
            "bluetooth_headphone_mode": "on",
        },
    )
    settings = SettingsManager()

    assert settings.get("update_check_enabled") is False
    assert settings.get("measure_two_channel_enabled") is True
    assert settings.get("confirm_clear_measurements") is False
    assert settings.get("bluetooth_headphone_mode") is True
    assert settings.corrected_keys == []


def test_string_mode_settings_are_not_coerced_to_bool(monkeypatch, tmp_path: Path) -> None:
    """`*_bottom_mode` ends in "_mode" but is a string; it must survive."""
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    _write_settings(tmp_path, {"measure_two_channel_bottom_mode": "separate"})
    assert SettingsManager().get("measure_two_channel_bottom_mode") == "separate"


def test_uncoercible_values_fall_back_to_defaults_and_are_recorded(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    _write_settings(
        tmp_path,
        {
            "sample_rate": "forty eight thousand",
            "sweep_duration": None,
            "update_check_enabled": "maybe",
            "queue_count": [],
            "theme": "light",
        },
    )
    settings = SettingsManager()

    assert settings.get("sample_rate") == 48000
    assert settings.get("sweep_duration") == 2.0
    assert settings.get("update_check_enabled") is True
    assert settings.get("queue_count") == 5
    # Untouched keys are still loaded normally.
    assert settings.get("theme") == "light"
    assert sorted(settings.corrected_keys) == [
        "queue_count",
        "sample_rate",
        "sweep_duration",
        "update_check_enabled",
    ]


def test_mapping_settings_that_are_not_mappings_are_reset(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    _write_settings(tmp_path, {"squiglink_host_keys": "sha256:oops"})
    settings = SettingsManager()
    assert settings.get("squiglink_host_keys") == {}
    assert "squiglink_host_keys" in settings.corrected_keys


def test_host_key_pins_round_trip(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    settings = SettingsManager()
    settings.set("squiglink_host_keys", {"sftp.squig.link:2022": "sha256:abc"})
    assert SettingsManager().get("squiglink_host_keys") == {"sftp.squig.link:2022": "sha256:abc"}


def test_alignment_confidence_migration_still_runs_after_coercion(
    monkeypatch, tmp_path: Path
) -> None:
    """A schema-1 file storing the legacy 9.0 as a string still migrates."""
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    _write_settings(
        tmp_path,
        {"settings_schema_version": 1, "start_alignment_confidence_min": "9.0"},
    )
    settings = SettingsManager()
    assert settings.get("start_alignment_confidence_min") == 6.0
    assert settings.get("settings_schema_version") == 3


def test_deliberate_confidence_value_is_preserved(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    _write_settings(
        tmp_path,
        {"settings_schema_version": 1, "start_alignment_confidence_min": "12.5"},
    )
    assert SettingsManager().get("start_alignment_confidence_min") == 12.5


def test_removed_variation_combination_key_is_dropped(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    _write_settings(
        tmp_path,
        {"settings_schema_version": 2, "hrtf_variation_combination": "worst_case"},
    )
    settings = SettingsManager()
    assert settings.get("hrtf_variation_combination") is None
    assert settings.get("settings_schema_version") == 3

    settings.set("theme", "dark")
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert "hrtf_variation_combination" not in saved


def test_settings_write_failure_is_logged(monkeypatch, caplog) -> None:
    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(settings_module, "atomic_write_json", fail)
    settings = SettingsManager()

    settings.set("buffer_size", 2048)

    assert settings._save() is False
    assert any(
        record.levelname == "ERROR" and "could not be saved" in record.getMessage()
        for record in caplog.records
    )


def test_settings_manager_reports_a_corrupt_file_and_keeps_a_backup(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text("{truncated", encoding="utf-8")

    settings = SettingsManager()

    assert settings.load_error is not None
    assert "settings.json" in settings.load_error
    assert len(corrupt_backups(tmp_path, "settings.json")) == 1
    # Defaults are in use, and the next write starts a clean file.
    assert settings.get("sample_rate") == 48000
    settings.set("sample_rate", 44100)
    assert json.loads((tmp_path / "settings.json").read_text())["sample_rate"] == 44100


def test_settings_manager_has_no_load_error_for_a_healthy_file(monkeypatch, tmp_path: Path) -> None:
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


def test_brand_settings_migration_is_saved(fake_brand, monkeypatch, tmp_path: Path) -> None:
    from dataclasses import replace

    from dms import branding

    def migrate(data: dict) -> None:
        data["brand_mode"] = bool(data.pop("old_mode_key", False))

    monkeypatch.setattr(branding, "_brand", replace(fake_brand, on_settings_load=migrate))
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"old_mode_key": True}))

    settings = SettingsManager()

    assert settings.get("brand_mode") is True
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert "old_mode_key" not in saved
    assert saved["brand_mode"] is True
