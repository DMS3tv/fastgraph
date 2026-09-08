import json
import os
from pathlib import Path
from typing import Any

from dms.shortcuts import DEFAULT_SHORTCUT_BINDINGS


_DEFAULTS: dict[str, Any] = {
    "settings_schema_version": 2,
    "theme": "dark",
    "brand_mode": False,
    "brand_mode_unlocked": False,
    "sweep_duration": 2.0,
    "sample_rate": 48000,
    "buffer_size": 1024,
    "output_device": None,
    "input_device": None,
    "input_channel": 0,
    "measure_two_channel_enabled": False,
    "measure_two_channel_bottom_mode": "combined",
    "windows_advanced_audio_drivers": False,
    "queue_count": 5,
    "queue_output_level_db": -6.0,
    "queue_output_level_persist": False,
    "confirm_clear_measurements": True,
    "confirm_clear_metadata": True,
    "export_directory": "",
    "rnd_session_directory": "",
    "rnd_notes_expanded": True,
    "rnd_splitter_ratio": 0.5,
    "automation_directory": "",
    "shortcut_bindings": dict(DEFAULT_SHORTCUT_BINDINGS),
    "hrtf_path": None,
    "pre_sweep_silence": 0.2,
    "post_sweep_silence": 0.5,
    "latency": "low",
    "latency_user_override": False,
    "bluetooth_headphone_mode": False,
    "standard_measurement_profile_snapshot": None,
    # Peak-to-background confidence of the sweep correlation, all modes.
    # Real measurement logs show valid sweeps at 61 and above and silence or
    # unrelated signals at 4.4 and below; 6.0 sits in that gap. 0 turns the
    # check off. Rejection also needs the noise margin below to fail.
    "start_alignment_confidence_min": 6.0,
    "sweep_noise_margin_min_db": 3.0,
    "snr_warn_db": 10.0,
    # Write the raw recording and a JSON sidecar to the app data folder on
    # every alignment failure so it can be replayed offline.
    "save_failed_recordings": False,
    "end_marker_confidence_min": 7.0,
    "timing_drift_max_ms": 35.0,
    "update_check_enabled": True,
    "update_feed_url": "",
    "squiglink_host": "sftp.squig.link",
    "squiglink_port": 2022,
    "squiglink_remember_credentials": False,
    "squiglink_credentials_encrypted": None,
}


class SettingsManager:
    def __init__(self) -> None:
        self._path = _config_dir() / "settings.json"
        self._data: dict[str, Any] = dict(_DEFAULTS)
        self._session_overrides: dict[str, Any] = {}
        self._load()

    def get(self, key: str) -> Any:
        if key in self._session_overrides:
            return self._session_overrides[key]
        return self._data.get(key, _DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        self._session_overrides.pop(key, None)
        self._data[key] = value
        self._save()

    def update(self, updates: dict[str, Any]) -> None:
        for key in updates:
            self._session_overrides.pop(key, None)
        self._data.update(updates)
        self._save()

    def set_session(self, key: str, value: Any) -> None:
        """Set an in-memory value that takes precedence until saved or cleared."""
        self._session_overrides[key] = value

    def session_overrides(self) -> dict[str, Any]:
        return dict(self._session_overrides)

    def save_session(self, key: str | None = None) -> list[str]:
        """Persist one or all session overrides and return the keys saved."""
        if key is None:
            keys = list(self._session_overrides)
        elif key in self._session_overrides:
            keys = [key]
        else:
            return []
        for override_key in keys:
            self._data[override_key] = self._session_overrides.pop(override_key)
        if keys:
            self._save()
        return keys

    def clear_session(self, key: str | None = None) -> None:
        if key is None:
            self._session_overrides.clear()
        else:
            self._session_overrides.pop(key, None)

    def _load(self) -> None:
        saved: dict[str, Any] = {}
        if self._path.exists():
            try:
                with open(self._path, "r") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    saved = loaded
                self._data.update(saved)
            except Exception:
                pass
        if not bool(self._data.get("brand_mode_unlocked")):
            self._data["brand_mode"] = False
        self._migrate_alignment_confidence(saved)

    _LEGACY_START_CONFIDENCE_DEFAULT = 9.0

    def _migrate_alignment_confidence(self, saved: dict[str, Any]) -> None:
        """
        Move a never-customized start-confidence value onto the new default.

        Before 2026-09 the confidence was min(peak-to-background,
        peak-to-next-best) with a default of 9.0 that only Bluetooth mode
        enforced. It is now peak-to-background alone, enforced in every mode,
        with a default of 6.0. A stored 9.0 from a schema-1 file is the old
        default and is moved; any other stored value was chosen by the user
        and is kept. The schema version is bumped so a deliberate 9.0 chosen
        later is never migrated again.
        """
        if not saved:
            return
        try:
            schema = int(saved.get("settings_schema_version") or 1)
        except (TypeError, ValueError):
            schema = 1
        if schema >= 2:
            return
        try:
            stored = float(saved.get("start_alignment_confidence_min"))
        except (TypeError, ValueError):
            stored = self._LEGACY_START_CONFIDENCE_DEFAULT
        if stored == self._LEGACY_START_CONFIDENCE_DEFAULT:
            self._data["start_alignment_confidence_min"] = _DEFAULTS[
                "start_alignment_confidence_min"
            ]
        self._data["settings_schema_version"] = 2

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass


def _config_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif os.uname().sysname == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "DMSFastgraph"


def config_dir() -> Path:
    """Return Fastgraph's platform-specific local application-data directory."""
    return _config_dir()
