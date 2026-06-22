import json
import os
from pathlib import Path
from typing import Any


_DEFAULTS: dict[str, Any] = {
    "theme": "dark",
    "sweep_duration": 2.0,
    "sample_rate": 48000,
    "buffer_size": 1024,
    "f_low": 20.0,
    "f_high": 20000.0,
    "output_device": None,
    "input_device": None,
    "input_channel": 0,
    "windows_advanced_audio_drivers": False,
    "queue_count": 5,
    "queue_output_level_db": -6.0,
    "queue_output_level_persist": False,
    "confirm_clear_measurements": True,
    "confirm_clear_metadata": True,
    "export_directory": "",
    "hrtf_path": None,
    "pre_sweep_silence": 0.2,
    "post_sweep_silence": 0.5,
    "latency": "low",
    "latency_user_override": False,
    "bluetooth_headphone_mode": False,
    "standard_measurement_profile_snapshot": None,
    "start_alignment_confidence_min": 9.0,
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
        if self._path.exists():
            try:
                with open(self._path, "r") as f:
                    saved = json.load(f)
                self._data.update(saved)
            except Exception:
                pass

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
