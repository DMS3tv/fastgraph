"""End-marker detection, drift limits and Bluetooth fallbacks in ``dms.measurement_alignment``."""

import numpy as np
import pytest
from helpers import (
    bluetooth_alignment_settings,
    noise_sweep,
    recording_from_layout,
    recording_with_shifted_end_markers,
    short_layout,
    write_at,
)

import dms.measurement_alignment as alignment_module
from dms.measurement_alignment import (
    AlignmentSettings,
    EndMarkerResult,
    MeasurementAlignmentError,
    MeasurementFailureReason,
    MeasurementWarningReason,
    StartAlignmentResult,
    align_recording_to_layout,
    find_end_markers,
)
from dms.measurement_layout import build_measurement_layout


def test_bluetooth_high_latency_recording_can_lock_to_start_marker() -> None:
    sweep, layout = short_layout(bluetooth=True)
    delay = int(round(0.45 * layout.fs))
    rec = recording_from_layout(layout, delay_samples=delay)

    result = align_recording_to_layout(
        rec,
        sweep,
        layout,
        AlignmentSettings(
            latency="high",
            bluetooth_headphone_mode=True,
            start_alignment_confidence_min=9.0,
            end_marker_confidence_min=7.0,
            timing_drift_max_ms=120.0,
        ),
    )

    assert result.start.marker_locked_candidate == layout.sweep_start_sample + delay
    assert result.start.selected_sweep_start == layout.sweep_start_sample + delay
    assert result.start.start_marker_confidence >= 3.5
    np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)


def test_missing_end_marker_uses_bluetooth_sweep_fallback() -> None:
    sweep, layout = short_layout(bluetooth=True)
    rec = np.zeros(layout.total_samples, dtype=np.float32)
    rec[layout.excitation_start_sample : layout.sweep_end_sample] = layout.excitation[
        : layout.sweep_end_sample - layout.excitation_start_sample
    ]

    result = align_recording_to_layout(
        rec,
        sweep,
        layout,
        AlignmentSettings(
            bluetooth_headphone_mode=True,
            start_alignment_confidence_min=3.0,
            end_marker_confidence_min=2.0,
        ),
    )

    assert result.diagnostics.failure_reason is None
    assert result.diagnostics.warning_reason == (MeasurementWarningReason.BLUETOOTH_SWEEP_FALLBACK)
    assert result.diagnostics.alignment_mode == "sweep_fallback"
    assert (
        result.diagnostics.marker_failure_reason
        == MeasurementFailureReason.LOW_END_MARKER_CONFIDENCE
    )
    np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)


def test_low_snr_missing_markers_still_fails_with_marker_diagnostics() -> None:
    sweep, layout = short_layout(bluetooth=True)
    rng = np.random.default_rng(20260524)
    rec = rng.normal(0.0, 0.05, layout.total_samples).astype(np.float32)
    rec[layout.excitation_start_sample : layout.sweep_end_sample] = layout.excitation[
        : layout.sweep_end_sample - layout.excitation_start_sample
    ]

    with pytest.raises(MeasurementAlignmentError, match="Low end-marker confidence") as exc:
        align_recording_to_layout(
            rec,
            sweep,
            layout,
            AlignmentSettings(
                bluetooth_headphone_mode=True,
                start_alignment_confidence_min=3.0,
                end_marker_confidence_min=2.0,
            ),
        )

    diagnostics = exc.value.diagnostics
    assert exc.value.reason == MeasurementFailureReason.LOW_END_MARKER_CONFIDENCE
    assert diagnostics.sweep_correlation_candidate is not None
    assert diagnostics.start_confidence is not None
    assert diagnostics.marker_confidence is not None
    assert diagnostics.marker_1_start is not None


def test_excessive_timing_drift_raises_existing_message() -> None:
    sweep, layout = short_layout(bluetooth=True)
    drift = int(round(0.18 * layout.fs))
    rec = np.zeros(layout.total_samples + drift + 256, dtype=np.float32)
    rec[layout.excitation_start_sample : layout.sweep_end_sample] = layout.excitation[
        : layout.sweep_end_sample - layout.excitation_start_sample
    ]
    rec[
        layout.end_marker_1_start_sample + drift : layout.end_marker_1_start_sample
        + drift
        + len(layout.end_marker)
    ] = layout.end_marker
    rec[
        layout.end_marker_2_start_sample + drift : layout.end_marker_2_start_sample
        + drift
        + len(layout.end_marker_2)
    ] = layout.end_marker_2

    with pytest.raises(ValueError, match="Timing drift too large"):
        align_recording_to_layout(
            rec,
            sweep,
            layout,
            AlignmentSettings(
                bluetooth_headphone_mode=True,
                start_alignment_confidence_min=3.0,
                end_marker_confidence_min=2.0,
                timing_drift_max_ms=5.0,
            ),
        )


def test_timing_drift_failure_includes_marker_diagnostics() -> None:
    sweep, layout = short_layout(bluetooth=True)
    drift = int(round(0.18 * layout.fs))
    rec = np.zeros(layout.total_samples + drift + 256, dtype=np.float32)
    rec[layout.excitation_start_sample : layout.sweep_end_sample] = layout.excitation[
        : layout.sweep_end_sample - layout.excitation_start_sample
    ]
    write_at(rec, layout.end_marker_1_start_sample + drift, layout.end_marker)
    write_at(rec, layout.end_marker_2_start_sample + drift, layout.end_marker_2)

    with pytest.raises(MeasurementAlignmentError, match="Timing drift too large") as exc:
        align_recording_to_layout(
            rec,
            sweep,
            layout,
            AlignmentSettings(
                bluetooth_headphone_mode=True,
                start_alignment_confidence_min=3.0,
                end_marker_confidence_min=2.0,
                timing_drift_max_ms=5.0,
            ),
        )

    diagnostics = exc.value.diagnostics
    assert exc.value.reason == MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE
    assert diagnostics.marker_1_start == pytest.approx(
        layout.end_marker_1_start_sample + drift,
        abs=4,
    )
    assert diagnostics.marker_2_start == pytest.approx(
        layout.end_marker_2_start_sample + drift,
        abs=4,
    )
    assert diagnostics.timing_error_ms == pytest.approx(180.0, abs=1.0)


def test_bluetooth_marginal_drift_succeeds_with_warning() -> None:
    sweep, layout = short_layout(fs=48_000, bluetooth=True)
    drift = int(round(0.1388 * layout.fs))
    rec = recording_with_shifted_end_markers(layout, drift, marker_scale=1.0)

    result = align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())

    assert result.diagnostics.failure_reason is None
    assert result.diagnostics.warning_reason == MeasurementWarningReason.BLUETOOTH_MARGINAL_DRIFT
    assert "Bluetooth timing drift is marginal" in result.diagnostics.warning_message
    assert result.end.marker_confidence >= 2.5
    assert result.end.timing_error_ms > result.diagnostics.timing_drift_max_ms
    assert result.end.timing_error_ms <= 160.0


def test_bluetooth_marginal_drift_accepts_weak_but_consistent_end_markers(monkeypatch) -> None:
    sweep, layout = short_layout(fs=48_000, bluetooth=True)
    drift = int(round(0.1391 * layout.fs))
    start_result = StartAlignmentResult(
        selected_sweep_start=layout.sweep_start_sample,
        sweep_correlation_candidate=layout.sweep_start_sample,
        marker_locked_candidate=None,
        start_confidence=3.0,
        start_marker_confidence=52.5,
    )
    end_result = EndMarkerResult(
        selected_sweep_start=layout.sweep_start_sample,
        marker_1_start=layout.end_marker_1_start_sample + drift,
        marker_2_start=layout.end_marker_2_start_sample + drift - 857,
        marker_confidence=2.1,
        timing_error_samples=drift,
        timing_error_ms=1000.0 * drift / layout.fs,
        spacing_error_samples=857,
    )

    monkeypatch.setattr(
        alignment_module,
        "find_start_alignment",
        lambda *_args, **_kwargs: start_result,
    )
    monkeypatch.setattr(
        alignment_module,
        "find_end_markers",
        lambda *_args, **_kwargs: end_result,
    )

    result = align_recording_to_layout(
        recording_from_layout(layout),
        sweep,
        layout,
        bluetooth_alignment_settings(),
    )

    assert result.diagnostics.failure_reason is None
    assert result.diagnostics.warning_reason == MeasurementWarningReason.BLUETOOTH_MARGINAL_DRIFT
    assert result.end.marker_confidence == pytest.approx(2.1)
    assert "end confidence 2.1" in result.diagnostics.warning_message


def test_bluetooth_weak_end_markers_use_sweep_fallback(monkeypatch) -> None:
    sweep, layout = short_layout(fs=48_000, bluetooth=True)
    drift = int(round(0.1391 * layout.fs))
    start_result = StartAlignmentResult(
        selected_sweep_start=layout.sweep_start_sample,
        sweep_correlation_candidate=layout.sweep_start_sample,
        marker_locked_candidate=None,
        start_confidence=3.0,
        start_marker_confidence=52.5,
        background_confidence=3.0,
        nextbest_confidence=3.0,
        peak_correlation=0.9,
    )
    end_result = EndMarkerResult(
        selected_sweep_start=layout.sweep_start_sample,
        marker_1_start=layout.end_marker_1_start_sample + drift,
        marker_2_start=layout.end_marker_2_start_sample + drift,
        marker_confidence=1.9,
        timing_error_samples=drift,
        timing_error_ms=1000.0 * drift / layout.fs,
        spacing_error_samples=0,
    )

    monkeypatch.setattr(
        alignment_module,
        "find_start_alignment",
        lambda *_args, **_kwargs: start_result,
    )
    monkeypatch.setattr(
        alignment_module,
        "find_end_markers",
        lambda *_args, **_kwargs: end_result,
    )

    result = align_recording_to_layout(
        recording_from_layout(layout),
        sweep,
        layout,
        bluetooth_alignment_settings(),
    )

    assert result.diagnostics.failure_reason is None
    assert result.diagnostics.warning_reason == (MeasurementWarningReason.BLUETOOTH_SWEEP_FALLBACK)
    assert result.diagnostics.alignment_mode == "sweep_fallback"
    assert (
        result.diagnostics.marker_failure_reason
        == MeasurementFailureReason.LOW_END_MARKER_CONFIDENCE
    )


def test_bluetooth_extreme_drift_still_fails() -> None:
    sweep, layout = short_layout(fs=48_000, bluetooth=True)
    drift = int(round(0.180 * layout.fs))
    rec = recording_with_shifted_end_markers(layout, drift)

    with pytest.raises(MeasurementAlignmentError) as exc:
        align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())

    assert exc.value.diagnostics.warning_reason is None
    assert exc.value.reason in {
        MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE,
        MeasurementFailureReason.LOW_END_MARKER_CONFIDENCE,
        MeasurementFailureReason.END_MARKER_UNVERIFIED,
    }


def test_bluetooth_end_marker_search_covers_marginal_ceiling_plus_slack() -> None:
    sweep, layout = short_layout(fs=48_000, bluetooth=True)
    drift = int(round(0.165 * layout.fs))
    rec = recording_with_shifted_end_markers(layout, drift, marker_scale=1.0)

    with pytest.raises(MeasurementAlignmentError, match="Timing drift too large") as exc:
        align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())

    assert exc.value.reason == MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE
    assert exc.value.diagnostics.marker_1_start is not None
    assert exc.value.diagnostics.marker_2_start is not None
    assert exc.value.diagnostics.timing_error_ms > 160.0


def test_bluetooth_marginal_drift_with_excessive_spacing_error_fails() -> None:
    sweep, layout = short_layout(fs=48_000, bluetooth=True)
    drift = int(round(0.1388 * layout.fs))
    spacing_extra = int(round(0.030 * layout.fs))
    rec = recording_with_shifted_end_markers(
        layout,
        drift,
        marker_2_extra_shift=spacing_extra,
    )

    with pytest.raises(MeasurementAlignmentError, match="Timing drift too large") as exc:
        align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())

    assert exc.value.reason == MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE
    assert exc.value.diagnostics.spacing_error_samples > int(round(960.0 * layout.fs / 48000.0))


def test_end_marker_choice_prefers_acceptable_lower_drift_candidate() -> None:
    sweep, layout = short_layout(bluetooth=True)
    rec = recording_from_layout(layout)
    actual = layout.sweep_start_sample
    misleading_candidate = actual - int(round(0.04 * layout.fs))
    start_result = StartAlignmentResult(
        selected_sweep_start=actual,
        sweep_correlation_candidate=misleading_candidate,
        marker_locked_candidate=actual,
        start_confidence=3.0,
        start_marker_confidence=5.0,
    )

    result = find_end_markers(
        rec,
        layout,
        AlignmentSettings(
            bluetooth_headphone_mode=True,
            end_marker_confidence_min=2.0,
        ),
        start_result,
    )

    assert result.selected_sweep_start == actual
    assert result.timing_error_samples == 0


def _ringing_kernel(fs: int) -> np.ndarray:
    n = int(round(0.018 * fs))
    t = np.arange(n, dtype=np.float64) / float(fs)
    kernel = np.exp(-t * 180.0) * np.cos(2.0 * np.pi * 1700.0 * t)
    kernel[0] += 1.0
    kernel /= np.sum(np.abs(kernel))
    return kernel.astype(np.float32)


def _single_tone_marker_like_peak(layout, frequency: float = 2050.0) -> np.ndarray:
    n = len(layout.end_marker)
    t = np.arange(n, dtype=np.float64) / float(layout.fs)
    tone = np.sin(2.0 * np.pi * frequency * t) * np.hanning(n)
    tone /= max(float(np.max(np.abs(tone))), 1e-12)
    return (2.5 * np.max(np.abs(layout.end_marker)) * tone).astype(np.float32)


def _time_stretch(signal: np.ndarray, stretch: float) -> np.ndarray:
    target_n = max(1, int(round(len(signal) * stretch)))
    src_x = np.arange(len(signal), dtype=np.float64)
    dst_x = np.linspace(0.0, float(len(signal) - 1), target_n)
    return np.interp(dst_x, src_x, signal).astype(np.float32)


def test_random_bluetooth_latency_jitter_aligns_when_markers_are_intact() -> None:
    sweep, layout = short_layout(bluetooth=True)
    rng = np.random.default_rng(20260505)
    base_delay = int(round(0.35 * layout.fs))
    jitters = rng.integers(
        low=-int(round(0.08 * layout.fs)),
        high=int(round(0.08 * layout.fs)),
        size=9,
    )

    for jitter in jitters:
        delay = base_delay + int(jitter)
        rec = recording_from_layout(layout, delay_samples=delay)

        result = align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())

        expected_start = layout.sweep_start_sample + delay
        assert result.start.selected_sweep_start == expected_start
        assert result.end.selected_sweep_start == expected_start
        assert result.end.timing_error_samples == 0
        assert result.end.spacing_error_samples == 0
        np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)


def test_false_marker_peak_does_not_beat_valid_marker_pair() -> None:
    sweep, layout = short_layout(bluetooth=True)
    delay = int(round(0.32 * layout.fs))
    rec = recording_from_layout(layout, delay_samples=delay)
    false_offset = int(round(0.16 * layout.fs))
    false_marker = (1.35 * layout.end_marker).astype(np.float32)
    write_at(rec, delay + layout.end_marker_1_start_sample - false_offset, false_marker)
    write_at(rec, delay + layout.end_marker_2_start_sample + false_offset, false_marker)

    result = align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())

    expected_start = layout.sweep_start_sample + delay
    assert result.end.selected_sweep_start == expected_start
    assert result.end.marker_1_start == layout.end_marker_1_start_sample + delay
    assert result.end.marker_2_start == layout.end_marker_2_start_sample + delay
    assert result.end.timing_error_samples == 0
    assert result.end.spacing_error_samples == 0


def test_single_band_marker_like_peak_does_not_beat_coded_marker_pair() -> None:
    sweep, layout = short_layout(bluetooth=True)
    delay = int(round(0.31 * layout.fs))
    rec = recording_from_layout(layout, delay_samples=delay)
    false_offset = int(round(0.14 * layout.fs))
    false_peak = _single_tone_marker_like_peak(layout)
    write_at(rec, delay + layout.end_marker_1_start_sample - false_offset, false_peak)
    write_at(rec, delay + layout.end_marker_2_start_sample + false_offset, false_peak)

    result = align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())

    assert result.end.marker_1_start == layout.end_marker_1_start_sample + delay
    assert result.end.marker_2_start == layout.end_marker_2_start_sample + delay
    assert result.end.timing_error_samples == 0
    assert result.end.spacing_error_samples == 0


def test_marker_identity_rejects_reversed_coded_marker_order() -> None:
    sweep, layout = short_layout(bluetooth=True)
    delay = int(round(0.22 * layout.fs))
    rec = recording_from_layout(layout, delay_samples=delay)
    rec[
        delay + layout.end_marker_1_start_sample : delay
        + layout.end_marker_1_start_sample
        + len(layout.end_marker)
    ] = 0.0
    rec[
        delay + layout.end_marker_2_start_sample : delay
        + layout.end_marker_2_start_sample
        + len(layout.end_marker_2)
    ] = 0.0
    write_at(rec, delay + layout.end_marker_1_start_sample, 2.0 * layout.end_marker_2)
    write_at(rec, delay + layout.end_marker_2_start_sample, 2.0 * layout.end_marker)

    # The marker pair must not be trusted for timing. Because the sweep itself
    # aligned with full confidence, the result is the sweep-correlation
    # fallback with a visible warning rather than a hard failure.
    result = align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())

    assert result.diagnostics.alignment_mode == "sweep_fallback"
    assert result.diagnostics.warning_reason == (MeasurementWarningReason.BLUETOOTH_SWEEP_FALLBACK)
    assert result.end.marker_confidence == 0.0
    assert result.end.selected_sweep_start == layout.sweep_start_sample + delay
    np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)


def test_duplicated_same_coded_marker_does_not_pass_as_valid_pair() -> None:
    sweep, layout = short_layout(bluetooth=True)
    delay = int(round(0.22 * layout.fs))
    rec = recording_from_layout(layout, delay_samples=delay)
    rec[
        delay + layout.end_marker_2_start_sample : delay
        + layout.end_marker_2_start_sample
        + len(layout.end_marker_2)
    ] = 0.0
    write_at(rec, delay + layout.end_marker_2_start_sample, 2.0 * layout.end_marker)

    # A duplicated marker cannot be a valid pair, so marker timing is
    # discarded; the perfectly aligned sweep is kept through the fallback.
    result = align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())

    assert result.diagnostics.alignment_mode == "sweep_fallback"
    assert result.diagnostics.warning_reason == (MeasurementWarningReason.BLUETOOTH_SWEEP_FALLBACK)
    assert result.end.marker_confidence == 0.0
    np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)


def test_loud_reversed_marker_artifacts_do_not_displace_ordered_pair() -> None:
    sweep, layout = short_layout(bluetooth=True)
    delay = int(round(0.24 * layout.fs))
    rec = recording_from_layout(layout, delay_samples=delay)
    spacing = len(layout.end_marker) + layout.end_marker_pair_gap_samples
    loud_marker_1 = (1.6 * layout.end_marker).astype(np.float32)
    loud_marker_2 = (1.6 * layout.end_marker_2).astype(np.float32)
    write_at(rec, delay + layout.end_marker_1_start_sample + spacing, loud_marker_2)
    write_at(rec, delay + layout.end_marker_2_start_sample - spacing, loud_marker_1)

    result = align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())

    assert result.end.selected_sweep_start == layout.sweep_start_sample + delay
    assert result.end.marker_1_start < result.end.marker_2_start
    assert result.end.spacing_error_samples == 0


def test_codec_like_marker_ringing_keeps_drift_near_true_marker() -> None:
    sweep, layout = short_layout(bluetooth=True)
    delay = int(round(0.28 * layout.fs))
    rec = recording_from_layout(layout, delay_samples=delay)
    rec = np.convolve(rec, _ringing_kernel(layout.fs), mode="full").astype(np.float32)

    result = align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())

    expected_start = layout.sweep_start_sample + delay
    assert abs(result.end.selected_sweep_start - expected_start) <= 2
    assert result.end.timing_error_ms <= 5.0
    assert np.sqrt(np.mean(np.square(result.aligned_recording))) > 0.0


def test_sample_rate_drift_produces_drift_failure_when_large() -> None:
    sweep, layout = short_layout(bluetooth=True)
    rec = recording_from_layout(layout)
    drift = int(round(0.18 * layout.fs))
    rec[
        layout.end_marker_1_start_sample : layout.end_marker_2_start_sample
        + len(layout.end_marker_2)
    ] = 0.0
    write_at(rec, layout.end_marker_1_start_sample + drift, layout.end_marker)
    write_at(rec, layout.end_marker_2_start_sample + drift, layout.end_marker_2)

    with pytest.raises(
        ValueError,
        match="Timing drift too large|Low end-marker confidence|Unable to verify end marker timing",
    ):
        align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())


def test_bluetooth_stretched_playback_keeps_end_marker_confidence() -> None:
    fs = 8_000
    sweep = noise_sweep(int(round(3.5 * fs)))
    layout = build_measurement_layout(
        sweep=sweep,
        fs=fs,
        pre_silence_s=0.6,
        post_silence_s=0.8,
        bluetooth_headphone_mode=True,
    )
    stretched_excitation = _time_stretch(layout.excitation, 1.04)
    rec = np.zeros(
        layout.excitation_start_sample
        + len(stretched_excitation)
        + layout.post_silence_samples
        + 512,
        dtype=np.float32,
    )
    write_at(rec, layout.excitation_start_sample, stretched_excitation)

    result = align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())

    assert result.end.marker_confidence >= 2.5
    assert result.diagnostics.raw_marker_confidence == pytest.approx(result.end.marker_confidence)
    assert result.end.spacing_error_samples <= int(round(960.0 * fs / 48000.0))
    assert result.diagnostics.marker_template_stretch is not None
    assert result.diagnostics.marker_template_stretch > 1.0
    assert result.diagnostics.warning_reason == (MeasurementWarningReason.BLUETOOTH_MARGINAL_DRIFT)
