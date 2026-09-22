"""
Standard-mode measurement integrity gate.

The synthetic cases below mirror the analysis of 258 real measurements from
the local diagnostics log (2026-09-08). Valid sweeps must be accepted even
with steep band-edge rolloff, strong echoes, clipping, or very low level;
silence, hum, tones and unrelated signals must be rejected.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import butter, sosfilt

from dms.measurement_alignment import (
    AlignmentSettings,
    MeasurementAlignmentError,
    MeasurementFailureReason,
    MeasurementWarningReason,
    align_recording_to_layout,
    compute_sweep_integrity,
    format_diagnostics_summary,
    is_retryable_timing_failure,
)
from dms.measurement_layout import build_measurement_layout
from dms.processing import generate_log_sweep
from dms.settings_manager import SettingsManager

FS = 48_000
_RNG_SEED = 7


def _sweep_layout(duration_s: float = 1.0):
    sweep = generate_log_sweep(duration_s, FS)
    layout = build_measurement_layout(
        sweep=sweep,
        fs=FS,
        pre_silence_s=0.2,
        post_silence_s=0.5,
        bluetooth_headphone_mode=False,
    )
    return sweep, layout


def _bandlimit(signal: np.ndarray, hp_hz: float | None, lp_hz: float | None) -> np.ndarray:
    out = np.asarray(signal, dtype=np.float64)
    if hp_hz is not None:
        out = sosfilt(butter(2, hp_hz, btype="highpass", fs=FS, output="sos"), out)
    if lp_hz is not None:
        out = sosfilt(butter(2, lp_hz, btype="lowpass", fs=FS, output="sos"), out)
    return out


def _noise(n: int, level_dbfs: float, seed: int = _RNG_SEED) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, 10.0 ** (level_dbfs / 20.0), n)


def _recording(
    layout,
    *,
    system=None,
    gain_db: float = -12.0,
    delay_s: float = 0.05,
    noise_dbfs: float = -60.0,
    echo: tuple[float, float] | None = None,
    clip: bool = False,
) -> np.ndarray:
    """Play the layout's excitation through a synthetic system."""
    excitation = layout.excitation.astype(np.float64)
    if system is not None:
        excitation = system(excitation)
    excitation *= 10.0 ** (gain_db / 20.0)
    rec = _noise(layout.total_samples, noise_dbfs)
    start = layout.excitation_start_sample + int(round(delay_s * FS))
    stop = min(len(rec), start + len(excitation))
    rec[start:stop] += excitation[: stop - start]
    if echo is not None:
        echo_delay_s, echo_gain = echo
        echo_start = start + int(round(echo_delay_s * FS))
        echo_stop = min(len(rec), echo_start + len(excitation))
        if echo_stop > echo_start:
            rec[echo_start:echo_stop] += echo_gain * excitation[: echo_stop - echo_start]
    if clip:
        rec = np.clip(rec * 4.0, -1.0, 1.0)
    return rec.astype(np.float32)


VALID_CASES = {
    "full_band": dict(),
    "hp60_lp12k": dict(system=lambda x: _bandlimit(x, 60.0, 12_000.0)),
    "hp40_lp16k": dict(system=lambda x: _bandlimit(x, 40.0, 16_000.0)),
    "hp200_lp5k_extreme_rolloff": dict(system=lambda x: _bandlimit(x, 200.0, 5_000.0)),
    "low_level_minus45_over_minus50_noise": dict(gain_db=-45.0, noise_dbfs=-50.0),
    "strong_echo_40ms": dict(echo=(0.040, 0.9)),
    "strong_echo_500ms": dict(echo=(0.500, 0.9)),
    "hard_clipped": dict(clip=True),
}


def _garbage(layout, kind: str) -> np.ndarray:
    n = layout.total_samples
    t = np.arange(n) / FS
    if kind == "silence_minus60_noise":
        rec = _noise(n, -60.0)
    elif kind == "silence_minus40_noise":
        rec = _noise(n, -40.0)
    elif kind == "loud_noise_minus20":
        rec = _noise(n, -20.0)
    elif kind == "mains_hum":
        rec = 0.05 * np.sin(2 * np.pi * 60.0 * t) + _noise(n, -50.0)
    elif kind == "steady_tone_1khz":
        rec = 0.2 * np.sin(2 * np.pi * 1000.0 * t) + _noise(n, -60.0)
    elif kind == "reversed_sweep":
        rec = _noise(n, -60.0)
        start = layout.excitation_start_sample
        rec[start : start + len(layout.excitation)] += 0.25 * layout.excitation[::-1]
    else:
        raise ValueError(kind)
    return rec.astype(np.float32)


GARBAGE_CASES = [
    "silence_minus60_noise",
    "silence_minus40_noise",
    "loud_noise_minus20",
    "mains_hum",
    "steady_tone_1khz",
    "reversed_sweep",
]


@pytest.mark.parametrize("name", sorted(VALID_CASES))
def test_valid_sweeps_are_accepted_with_default_gate(name: str) -> None:
    sweep, layout = _sweep_layout()
    rec = _recording(layout, **VALID_CASES[name])

    result = align_recording_to_layout(rec, sweep, layout, AlignmentSettings())

    assert result.diagnostics.failure_reason is None
    assert result.start.peak_correlation >= 0.10
    assert result.start.background_confidence >= 6.0
    assert len(result.diagnostics.coverage_db) == 10


@pytest.mark.parametrize("name", GARBAGE_CASES)
def test_garbage_recordings_are_rejected_with_default_gate(name: str) -> None:
    sweep, layout = _sweep_layout()
    rec = _garbage(layout, name)

    with pytest.raises(MeasurementAlignmentError) as exc:
        align_recording_to_layout(rec, sweep, layout, AlignmentSettings())

    assert exc.value.reason in {
        MeasurementFailureReason.LOW_START_CONFIDENCE,
        MeasurementFailureReason.LOW_SNR,
    }
    assert is_retryable_timing_failure(str(exc.value), exc.value.reason)


def test_echo_lowers_nextbest_but_not_background_confidence() -> None:
    """The regression that made the old min(bg, next) metric reject good sweeps."""
    sweep, layout = _sweep_layout()
    rec = _recording(layout, echo=(0.040, 0.9))

    result = align_recording_to_layout(rec, sweep, layout, AlignmentSettings())

    assert result.start.nextbest_confidence < 2.0
    assert result.start.background_confidence > 20.0
    assert result.start.start_confidence == pytest.approx(result.start.background_confidence)


def test_rolloff_raises_confidence_and_keeps_midband_margin() -> None:
    sweep, layout = _sweep_layout()
    full = align_recording_to_layout(_recording(layout), sweep, layout, AlignmentSettings())
    rolled = align_recording_to_layout(
        _recording(layout, system=lambda x: _bandlimit(x, 200.0, 5_000.0)),
        sweep,
        layout,
        AlignmentSettings(),
    )

    assert rolled.start.background_confidence >= full.start.background_confidence
    assert rolled.diagnostics.midband_margin_db > 20.0
    # The first and last tenths carry little energy, the middle carries most.
    coverage = rolled.diagnostics.coverage_db
    assert max(coverage[3:7]) > coverage[0] + 10.0
    assert max(coverage[3:7]) > coverage[-1] + 10.0


def test_low_level_sweep_is_accepted_with_low_snr_warning() -> None:
    sweep, layout = _sweep_layout()
    rec = _recording(layout, gain_db=-45.0, noise_dbfs=-50.0)

    result = align_recording_to_layout(rec, sweep, layout, AlignmentSettings())

    assert result.snr_db < 10.0
    assert result.diagnostics.warning_reason == MeasurementWarningReason.LOW_SNR
    assert "signal-to-noise" in (result.diagnostics.warning_message or "")


def test_gate_needs_both_metrics_to_fail() -> None:
    """Disabling either half of the conjunction disables the rejection."""
    sweep, layout = _sweep_layout()
    rec = _garbage(layout, "silence_minus40_noise")
    relaxed = AlignmentSettings(peak_correlation_min=0.0)

    with pytest.raises(MeasurementAlignmentError) as exc:
        align_recording_to_layout(rec, sweep, layout, relaxed)
    assert exc.value.reason == MeasurementFailureReason.LOW_SNR

    for settings in (
        AlignmentSettings(peak_correlation_min=0.0, start_alignment_confidence_min=0.0),
        AlignmentSettings(peak_correlation_min=0.0, sweep_noise_margin_min_db=0.0),
    ):
        result = align_recording_to_layout(rec, sweep, layout, settings)
        assert result.diagnostics.failure_reason is None


def test_peak_correlation_floor_rejects_silence_even_when_gate_is_off() -> None:
    sweep, layout = _sweep_layout()
    rec = _garbage(layout, "silence_minus60_noise")
    settings = AlignmentSettings(
        start_alignment_confidence_min=0.0,
        sweep_noise_margin_min_db=0.0,
    )

    with pytest.raises(MeasurementAlignmentError) as exc:
        align_recording_to_layout(rec, sweep, layout, settings)

    assert exc.value.reason == MeasurementFailureReason.LOW_START_CONFIDENCE
    assert "No sweep signal was detected" in str(exc.value)


def test_non_finite_samples_are_rejected() -> None:
    sweep, layout = _sweep_layout()
    rec = _recording(layout)
    rec[len(rec) // 2] = np.nan

    with pytest.raises(MeasurementAlignmentError) as exc:
        align_recording_to_layout(rec, sweep, layout, AlignmentSettings())

    assert exc.value.reason == MeasurementFailureReason.INVALID_RECORDING


def test_failure_diagnostics_carry_integrity_metrics() -> None:
    sweep, layout = _sweep_layout()
    rec = _garbage(layout, "silence_minus40_noise")

    with pytest.raises(MeasurementAlignmentError) as exc:
        align_recording_to_layout(rec, sweep, layout, AlignmentSettings(peak_correlation_min=0.0))

    diagnostics = exc.value.diagnostics
    assert diagnostics.snr_db is not None
    assert diagnostics.midband_margin_db is not None
    assert diagnostics.peak_correlation is not None
    summary = format_diagnostics_summary(diagnostics)
    assert "Mid-band level above noise" in summary
    assert "Sweep coverage by tenth" in summary
    assert "Sweep correlation peak" in summary


def test_compute_sweep_integrity_reports_margin_and_coverage() -> None:
    aligned = np.concatenate([np.zeros(1000), 0.1 * np.ones(8000), np.zeros(1000)])
    integrity = compute_sweep_integrity(aligned, noise_rms=0.001)

    assert integrity.midband_margin_db == pytest.approx(40.0, abs=0.1)
    assert len(integrity.coverage_db) == 10
    assert integrity.coverage_db[0] == pytest.approx(0.0)
    assert integrity.coverage_db[5] == pytest.approx(40.0, abs=0.1)


def test_settings_migrate_old_default_confidence(tmp_path, monkeypatch) -> None:
    import json

    import dms.settings_manager as settings_module

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr(settings_module, "_config_dir", lambda: config_dir)

    (config_dir / "settings.json").write_text(json.dumps({"start_alignment_confidence_min": 9.0}))
    assert SettingsManager().get("start_alignment_confidence_min") == 6.0

    (config_dir / "settings.json").write_text(json.dumps({"start_alignment_confidence_min": 7.5}))
    assert SettingsManager().get("start_alignment_confidence_min") == 7.5

    (config_dir / "settings.json").write_text(
        json.dumps({"start_alignment_confidence_min": 9.0, "settings_schema_version": 2})
    )
    assert SettingsManager().get("start_alignment_confidence_min") == 9.0


def test_settings_defaults_include_new_gate_keys() -> None:
    manager = SettingsManager()
    assert manager.get("sweep_noise_margin_min_db") == 3.0
    assert manager.get("snr_warn_db") == 10.0
    assert manager.get("start_alignment_confidence_min") == 6.0
    assert manager.get("f_low") is None
