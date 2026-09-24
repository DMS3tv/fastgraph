import logging
import os
from pathlib import Path
from typing import Any

from dms import branding
from dms.file_io import atomic_write_json, load_json_with_backup
from dms.shortcuts import DEFAULT_SHORTCUT_BINDINGS

logger = logging.getLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "settings_schema_version": 3,
    "theme": "dark",
    "brand_mode": False,
    "sweep_duration": 2.0,
    "sample_rate": 48000,
    "buffer_size": 1024,
    "output_device": None,
    "input_device": None,
    "input_channel": 0,
    "measure_two_channel_enabled": False,
    "measure_two_channel_bottom_mode": "combined",
    # Draw THD/H2/H3 against a secondary axis in the bottom viewport.
    "measure_distortion_overlay": False,
    # "ref_1khz" normalizes every curve to 0 dB at 1 kHz; "dbspl" keeps the
    # absolute level, and needs a calibrated input device.
    "measure_level_mode": "ref_1khz",
    "windows_advanced_audio_drivers": False,
    "queue_count": 5,
    "queue_output_level_db": -6.0,
    "queue_output_level_persist": False,
    "confirm_clear_measurements": True,
    "confirm_clear_metadata": True,
    "confirm_discard_measurements": True,
    "export_directory": "",
    "rnd_session_directory": "",
    "measure_session_directory": "",
    # Target comparison. The target path is reloaded at startup when the file
    # is still there and silently dropped when it is not; delta view and the
    # offset mode are remembered the same way the other Measure toggles are.
    "measure_target_path": "",
    "measure_delta_view": False,
    "measure_delta_offset_mode": "1khz",
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
    # Trust-on-first-use SSH host-key pins: {"host:port": "sha256:<base64>"}.
    "squiglink_host_keys": {},
}


# Keys whose stored value must be a number. A settings.json edited by hand (or
# written by an older build) can hold "48000" or 2.0 where an int is required;
# every consumer would then raise deep inside a measurement instead of at load.
_INT_KEYS = ("sample_rate", "buffer_size", "queue_count", "squiglink_port")
_FLOAT_KEYS = (
    "sweep_duration",
    "pre_sweep_silence",
    "post_sweep_silence",
    "start_alignment_confidence_min",
    "sweep_noise_margin_min_db",
    "snr_warn_db",
    "end_marker_confidence_min",
    "timing_drift_max_ms",
    "queue_output_level_db",
)
_BOOL_TRUE = {"1", "true", "yes", "on", "y", "t"}
_BOOL_FALSE = {"0", "false", "no", "off", "n", "f", ""}


# Every key whose default is a bool, which covers the *_enabled, *_mode and
# confirm_* switches. Selecting by default type rather than by name keeps
# string settings that merely end in "_mode" (measure_two_channel_bottom_mode)
# out of the boolean coercion.
_BOOL_KEYS = tuple(key for key, value in _DEFAULTS.items() if isinstance(value, bool))


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not a valid integer setting")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite float")
        return int(value)
    if isinstance(value, str):
        return int(float(value.strip()))
    raise ValueError(f"cannot coerce {type(value).__name__} to int")


def _coerce_float(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a valid float setting")
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        result = float(value.strip())
    else:
        raise ValueError(f"cannot coerce {type(value).__name__} to float")
    if result != result or result in (float("inf"), float("-inf")):
        raise ValueError("non-finite float")
    return result


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _BOOL_TRUE:
            return True
        if text in _BOOL_FALSE:
            return False
    raise ValueError(f"cannot coerce {value!r} to bool")


class SettingsManager:
    def __init__(self) -> None:
        self._path = _config_dir() / "settings.json"
        self._data: dict[str, Any] = dict(_DEFAULTS)
        self._session_overrides: dict[str, Any] = {}
        # Set when settings.json was damaged; the main window shows it once at
        # startup instead of silently reverting the user to defaults.
        self.load_error: str | None = None
        # Keys whose stored value could not be coerced and fell back to default.
        self.corrected_keys: list[str] = []
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
        loaded, error = load_json_with_backup(self._path)
        self.load_error = error
        if loaded is not None:
            saved = loaded
            self._data.update(saved)
            self._coerce_types(saved)
        self._migrate_alignment_confidence(saved)
        self._migrate_drop_variation_combination(saved)
        brand = branding.active()
        if brand is not None and brand.on_settings_load is not None:
            before = dict(self._data)
            brand.on_settings_load(self._data)
            if self._data != before:
                self._save()

    def _coerce_types(self, saved: dict[str, Any]) -> None:
        """Force stored values back onto their declared type.

        A key whose stored value cannot be coerced falls back to the built-in
        default and is recorded in ``corrected_keys`` so startup can report it.
        Keys absent from the file are left alone: they already hold defaults.
        """
        corrected: list[str] = []

        def _apply(keys: tuple[str, ...], coerce) -> None:
            for key in keys:
                if key not in saved:
                    continue
                try:
                    self._data[key] = coerce(saved[key])
                except (TypeError, ValueError, OverflowError):
                    self._data[key] = _DEFAULTS[key]
                    corrected.append(key)

        _apply(_INT_KEYS, _coerce_int)
        _apply(_FLOAT_KEYS, _coerce_float)
        _apply(_BOOL_KEYS, _coerce_bool)

        # Mapping settings must stay mappings; anything else would raise at the
        # first .get() in the shortcut or host-key lookup paths.
        for key in ("shortcut_bindings", "squiglink_host_keys"):
            if key in saved and not isinstance(self._data.get(key), dict):
                self._data[key] = dict(_DEFAULTS[key])
                corrected.append(key)

        self.corrected_keys = corrected

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

    def _migrate_drop_variation_combination(self, saved: dict[str, Any]) -> None:
        """Drop the removed ``hrtf_variation_combination`` key (schema 3).

        The "worst_case" option had no UI control and was removed; the
        independent combination is now the only behaviour.
        """
        if not saved:
            return
        try:
            schema = int(saved.get("settings_schema_version") or 1)
        except (TypeError, ValueError):
            schema = 1
        if schema >= 3:
            return
        self._data.pop("hrtf_variation_combination", None)
        self._data["settings_schema_version"] = 3

    def _save(self) -> bool:
        # settings.json holds the encrypted Squiglink credentials, so it is
        # written owner-only and swapped in atomically: a crash mid-write can
        # no longer truncate the user's saved folders, shortcuts and login.
        try:
            atomic_write_json(self._path, self._data, mode=0o600)
        except Exception:
            logger.error("Settings could not be saved to %s", self._path, exc_info=True)
            return False
        return True


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
