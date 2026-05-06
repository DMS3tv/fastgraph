"""Pure measurement profile helpers.

Bluetooth mode is a reversible measurement profile: entering it should save the
user's current standard settings, and leaving it should restore those settings.
"""

from collections.abc import Mapping
from typing import Any


MEASUREMENT_PROFILE_KEYS = (
    "sweep_duration",
    "latency",
    "buffer_size",
    "pre_sweep_silence",
    "post_sweep_silence",
    "start_alignment_confidence_min",
    "end_marker_confidence_min",
    "timing_drift_max_ms",
)

PROFILE_SNAPSHOT_SETTING = "standard_measurement_profile_snapshot"

STANDARD_PROFILE_DEFAULTS: dict[str, Any] = {
    "sweep_duration": 2.0,
    "latency": "low",
    "buffer_size": 1024,
    "pre_sweep_silence": 0.2,
    "post_sweep_silence": 0.5,
    "start_alignment_confidence_min": 9.0,
    "end_marker_confidence_min": 7.0,
    "timing_drift_max_ms": 35.0,
}

BLUETOOTH_PROFILE_DEFAULTS: dict[str, Any] = {
    "sweep_duration": 3.5,
    "latency": "high",
    "buffer_size": 512,
    "pre_sweep_silence": 0.6,
    "post_sweep_silence": 0.8,
    "start_alignment_confidence_min": 3.0,
    "end_marker_confidence_min": 2.5,
    "timing_drift_max_ms": 120.0,
}


def snapshot_measurement_profile(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Capture only known measurement profile fields from a settings mapping."""
    return {
        key: settings[key]
        for key in MEASUREMENT_PROFILE_KEYS
        if key in settings
    }


def is_complete_measurement_profile(profile: object) -> bool:
    if not isinstance(profile, Mapping):
        return False
    return all(key in profile for key in MEASUREMENT_PROFILE_KEYS)


def bluetooth_profile_updates() -> dict[str, Any]:
    return dict(BLUETOOTH_PROFILE_DEFAULTS)


def restore_standard_profile_updates(
    snapshot: object,
) -> tuple[dict[str, Any], bool]:
    """Return standard-profile updates and whether fallback defaults were used."""
    if is_complete_measurement_profile(snapshot):
        assert isinstance(snapshot, Mapping)
        return {key: snapshot[key] for key in MEASUREMENT_PROFILE_KEYS}, False
    return dict(STANDARD_PROFILE_DEFAULTS), True
