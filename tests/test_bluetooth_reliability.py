"""
Bluetooth reliability against codec-like degradation.

The local diagnostics log showed that every real Bluetooth failure had a
healthy sweep alignment and was killed only by the end-marker quality check,
which also blocked the sweep-correlation fallback. The synthetic codec model
below (lowpass, per-frame time jitter, latency, a muted first stretch, and a
realistic noise floor) reproduces that pattern: on the previous code the
heaviest case hard-failed with chip agreement near 0.03 while the sweep
aligned with confidence above 100.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from scipy.signal import butter, iirpeak, lfilter, sosfilt

from dms.measurement_alignment import (
    AlignmentSettings,
    MeasurementAlignmentError,
    MeasurementFailureReason,
    MeasurementWarningReason,
    align_recording_to_layout,
)
from dms.measurement_layout import build_measurement_layout
from dms.processing import generate_log_sweep
from dms.recording_dump import load_failed_recording, save_failed_recording

FS = 48_000


def _headphone(x: np.ndarray) -> np.ndarray:
    y = sosfilt(butter(2, [40.0, 16_000.0], btype="bandpass", fs=FS, output="sos"), x)
    b, a = iirpeak(8_000.0, 6.0, fs=FS)
    return y + lfilter(b, a, y)


def _codec(
    x: np.ndarray, *, lp_hz: float, jitter_ms: float, frame_ms: float = 23.0, seed: int = 3
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    y = sosfilt(butter(4, lp_hz, btype="lowpass", fs=FS, output="sos"), x)
    n = int(round(frame_ms * 1e-3 * FS))
    t = np.arange(len(y), dtype=np.float64)
    out = np.zeros_like(y)
    for lo in range(0, len(y), n):
        hi = min(len(y), lo + n)
        shift = rng.uniform(-jitter_ms, jitter_ms) * 1e-3 * FS
        out[lo:hi] = np.interp(t[lo:hi] + shift, t, y)
    return out


def _bluetooth_recording(
    *,
    lp_hz: float = 14_000.0,
    jitter_ms: float = 0.3,
    noise_dbfs: float = -55.0,
    latency_ms: float = 150.0,
    mute_ms: float = 120.0,
    gain_db: float = -12.0,
    seed: int = 1,
):
    sweep = generate_log_sweep(1.0, FS)
    layout = build_measurement_layout(sweep, FS, 0.2, 1.2, True)
    excitation = _codec(
        _headphone(layout.excitation.astype(np.float64)), lp_hz=lp_hz, jitter_ms=jitter_ms
    )
    excitation *= 10.0 ** (gain_db / 20.0)
    rng = np.random.default_rng(seed)
    rec = rng.normal(0.0, 10.0 ** (noise_dbfs / 20.0), layout.total_samples + FS // 2)
    start = layout.excitation_start_sample + int(round(latency_ms * 1e-3 * FS))
    segment = excitation.copy()
    segment[: int(round(mute_ms * 1e-3 * FS))] = 0.0
    rec[start : start + len(segment)] += segment
    return sweep, layout, rec.astype(np.float32)


def _settings() -> AlignmentSettings:
    return AlignmentSettings(
        latency="high",
        bluetooth_headphone_mode=True,
        start_alignment_confidence_min=6.0,
        end_marker_confidence_min=7.0,
        timing_drift_max_ms=120.0,
    )


MARKER_CASES = {
    "light_codec": dict(lp_hz=14_000.0, jitter_ms=0.3, noise_dbfs=-55.0),
    "medium_codec": dict(lp_hz=12_000.0, jitter_ms=0.6, noise_dbfs=-55.0),
    "medium_codec_real_snr": dict(lp_hz=12_000.0, jitter_ms=0.6, noise_dbfs=-40.0),
}

FALLBACK_CASES = {
    "heavy_codec_real_snr": dict(lp_hz=10_000.0, jitter_ms=0.8, noise_dbfs=-40.0),
    "heavy_codec_low_snr": dict(lp_hz=10_000.0, jitter_ms=1.0, noise_dbfs=-35.0),
    "very_heavy_codec": dict(lp_hz=8_000.0, jitter_ms=1.0, noise_dbfs=-35.0),
}


@pytest.mark.parametrize("name", sorted(MARKER_CASES))
def test_light_codec_degradation_keeps_marker_timing(name: str) -> None:
    sweep, layout, rec = _bluetooth_recording(**MARKER_CASES[name])

    result = align_recording_to_layout(rec, sweep, layout, _settings())

    assert result.diagnostics.alignment_mode == "marker"
    assert result.end.marker_confidence > 0.0
    assert result.diagnostics.failure_reason is None


@pytest.mark.parametrize("name", sorted(FALLBACK_CASES))
def test_heavy_codec_degradation_falls_back_to_sweep_with_warning(name: str) -> None:
    sweep, layout, rec = _bluetooth_recording(**FALLBACK_CASES[name])

    result = align_recording_to_layout(rec, sweep, layout, _settings())

    assert result.diagnostics.alignment_mode == "sweep_fallback"
    assert result.diagnostics.warning_reason == (MeasurementWarningReason.BLUETOOTH_SWEEP_FALLBACK)
    assert result.diagnostics.raw_marker_confidence is not None
    assert result.start.background_confidence > 20.0
    assert result.start.peak_correlation >= 0.10
    assert result.diagnostics.snr_db is not None and result.diagnostics.snr_db > 10.0


def test_bluetooth_failure_diagnostics_carry_snr_and_integrity() -> None:
    """Every Bluetooth failure now reports the level evidence it was judged on."""
    sweep, layout, rec = _bluetooth_recording(**FALLBACK_CASES["heavy_codec_real_snr"])
    # Force a hard failure by disabling the fallback's SNR reachability.
    import dms.measurement_alignment as alignment_module

    original = alignment_module._BLUETOOTH_FALLBACK_MIN_SNR_DB
    alignment_module._BLUETOOTH_FALLBACK_MIN_SNR_DB = 200.0
    try:
        with pytest.raises(MeasurementAlignmentError) as exc:
            align_recording_to_layout(rec, sweep, layout, _settings())
    finally:
        alignment_module._BLUETOOTH_FALLBACK_MIN_SNR_DB = original

    assert exc.value.reason == MeasurementFailureReason.LOW_END_MARKER_CONFIDENCE
    assert exc.value.diagnostics.snr_db is not None
    assert exc.value.diagnostics.midband_margin_db is not None
    assert exc.value.diagnostics.raw_marker_confidence is not None


def test_bluetooth_silence_is_still_rejected() -> None:
    sweep, layout, _rec = _bluetooth_recording()
    rng = np.random.default_rng(9)
    silence = rng.normal(0.0, 10.0 ** (-50.0 / 20.0), layout.total_samples).astype(np.float32)

    with pytest.raises(MeasurementAlignmentError) as exc:
        align_recording_to_layout(silence, sweep, layout, _settings())

    assert exc.value.reason in {
        MeasurementFailureReason.LOW_START_CONFIDENCE,
        MeasurementFailureReason.LOW_SNR,
        MeasurementFailureReason.LOW_END_MARKER_CONFIDENCE,
        MeasurementFailureReason.END_MARKER_UNVERIFIED,
    }


def test_failed_recording_dump_round_trips_and_replays(tmp_path) -> None:
    sweep, layout, _rec = _bluetooth_recording()
    rng = np.random.default_rng(11)
    silence = rng.normal(0.0, 10.0 ** (-50.0 / 20.0), layout.total_samples).astype(np.float32)
    settings = _settings()
    with pytest.raises(MeasurementAlignmentError) as exc:
        align_recording_to_layout(silence, sweep, layout, settings)

    json_path = save_failed_recording(
        tmp_path / "failed_recordings",
        silence,
        fs=FS,
        sweep_duration_s=1.0,
        f_low=20.0,
        f_high=20_000.0,
        pre_silence_s=0.2,
        post_silence_s=1.2,
        bluetooth_headphone_mode=True,
        alignment_settings=settings,
        diagnostics=exc.value.diagnostics,
        failure_message=str(exc.value),
        failure_reason=exc.value.reason,
        extra={"input_device": "Fixture"},
    )

    assert json_path.exists()
    assert json_path.with_suffix(".wav").exists()
    payload = json.loads(json_path.read_text())
    assert payload["failure"]["reason"] == exc.value.reason
    # Reason enums serialise as their plain string codes.
    assert f'"reason": "{exc.value.reason.value}"' in json_path.read_text()
    assert payload["alignment_settings"]["bluetooth_headphone_mode"] is True
    assert payload["extra"]["input_device"] == "Fixture"

    loaded_payload, recording = load_failed_recording(json_path)
    assert loaded_payload["fs"] == FS
    np.testing.assert_allclose(recording, silence, atol=1e-6)

    # Replaying reproduces the same decision.
    from tools.replay_failed_recording import replay

    assert replay(json_path, {}) == 1
