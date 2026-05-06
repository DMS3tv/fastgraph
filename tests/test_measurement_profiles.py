from dms.measurement_profiles import (
    BLUETOOTH_PROFILE_DEFAULTS,
    MEASUREMENT_PROFILE_KEYS,
    STANDARD_PROFILE_DEFAULTS,
    bluetooth_profile_updates,
    restore_standard_profile_updates,
    snapshot_measurement_profile,
)


def test_snapshot_measurement_profile_includes_only_profile_keys() -> None:
    settings = {
        "sweep_duration": 1.75,
        "latency": "low",
        "buffer_size": 256,
        "pre_sweep_silence": 0.3,
        "post_sweep_silence": 0.7,
        "start_alignment_confidence_min": 8.5,
        "end_marker_confidence_min": 6.5,
        "timing_drift_max_ms": 42.0,
        "output_device": "not part of profile",
        "squiglink_host": "not part of profile",
    }

    snapshot = snapshot_measurement_profile(settings)

    assert set(snapshot) == set(MEASUREMENT_PROFILE_KEYS)
    assert snapshot["sweep_duration"] == 1.75
    assert "output_device" not in snapshot
    assert "squiglink_host" not in snapshot


def test_bluetooth_profile_updates_preserve_current_defaults_exactly() -> None:
    assert bluetooth_profile_updates() == BLUETOOTH_PROFILE_DEFAULTS
    assert bluetooth_profile_updates() is not BLUETOOTH_PROFILE_DEFAULTS


def test_restore_standard_profile_uses_custom_snapshot() -> None:
    custom_snapshot = {
        "sweep_duration": 4.25,
        "latency": "high",
        "buffer_size": 2048,
        "pre_sweep_silence": 0.45,
        "post_sweep_silence": 0.95,
        "start_alignment_confidence_min": 5.5,
        "end_marker_confidence_min": 4.5,
        "timing_drift_max_ms": 88.0,
    }

    updates, used_fallback = restore_standard_profile_updates(custom_snapshot)

    assert used_fallback is False
    assert updates == custom_snapshot
    assert updates is not custom_snapshot


def test_restore_standard_profile_falls_back_for_missing_snapshot() -> None:
    updates, used_fallback = restore_standard_profile_updates(None)

    assert used_fallback is True
    assert updates == STANDARD_PROFILE_DEFAULTS
    assert updates is not STANDARD_PROFILE_DEFAULTS


def test_restore_standard_profile_falls_back_for_incomplete_snapshot() -> None:
    incomplete_snapshot = {"sweep_duration": 9.0}

    updates, used_fallback = restore_standard_profile_updates(incomplete_snapshot)

    assert used_fallback is True
    assert updates == STANDARD_PROFILE_DEFAULTS


def test_snapshot_clear_pattern_prevents_stale_restore() -> None:
    first_snapshot = dict(STANDARD_PROFILE_DEFAULTS)
    first_snapshot["buffer_size"] = 256
    restored, used_fallback = restore_standard_profile_updates(first_snapshot)

    assert used_fallback is False
    assert restored["buffer_size"] == 256

    fallback, used_fallback = restore_standard_profile_updates(None)

    assert used_fallback is True
    assert fallback["buffer_size"] == STANDARD_PROFILE_DEFAULTS["buffer_size"]
