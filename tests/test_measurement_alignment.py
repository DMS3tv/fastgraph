import numpy as np
import pytest

from dms.measurement_alignment import (
    AlignmentSettings,
    MeasurementAlignmentError,
    MeasurementFailureReason,
    StartAlignmentResult,
    align_recording_to_layout,
    find_end_markers,
    format_diagnostics_summary,
    is_retryable_timing_failure,
)
from dms.measurement_layout import build_measurement_layout


def _test_sweep(length: int) -> np.ndarray:
    rng = np.random.default_rng(1234)
    sweep = rng.normal(0.0, 0.2, length).astype(np.float32)
    sweep *= np.hanning(length).astype(np.float32)
    return sweep


def _recording_from_layout(layout, delay_samples: int = 0) -> np.ndarray:
    rec = np.zeros(layout.total_samples + delay_samples + 256, dtype=np.float32)
    start = delay_samples + layout.excitation_start_sample
    rec[start:start + len(layout.excitation)] = layout.excitation
    return rec


def _layout(fs: int = 8_000, bluetooth: bool = False):
    sweep = _test_sweep(2_048)
    layout = build_measurement_layout(
        sweep=sweep,
        fs=fs,
        pre_silence_s=0.08,
        post_silence_s=0.16,
        bluetooth_headphone_mode=bluetooth,
    )
    return sweep, layout


def test_aligns_clean_non_bluetooth_recording_with_fixed_latency() -> None:
    sweep, layout = _layout()
    delay = 137
    rec = _recording_from_layout(layout, delay_samples=delay)

    result = align_recording_to_layout(
        rec,
        sweep,
        layout,
        AlignmentSettings(start_alignment_confidence_min=3.0, end_marker_confidence_min=2.0),
    )

    assert result.start.selected_sweep_start == layout.sweep_start_sample + delay
    assert result.end.selected_sweep_start == layout.sweep_start_sample + delay
    assert result.end.timing_error_samples == 0
    np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)


@pytest.mark.parametrize(
    "reason",
    [
        MeasurementFailureReason.LOW_START_CONFIDENCE,
        MeasurementFailureReason.LOW_END_MARKER_CONFIDENCE,
        MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE,
    ],
)
def test_retryable_timing_failure_uses_structured_reasons(reason: str) -> None:
    assert is_retryable_timing_failure("Unrelated user-facing copy", reason) is True


@pytest.mark.parametrize(
    "reason",
    [
        MeasurementFailureReason.SHORT_RECORDING,
        MeasurementFailureReason.SHORT_ALIGNED_RECORDING,
        MeasurementFailureReason.END_MARKER_UNVERIFIED,
    ],
)
def test_retryable_timing_failure_rejects_non_retryable_reasons(reason: str) -> None:
    assert is_retryable_timing_failure("Timing drift string is ignored", reason) is False


def test_retryable_timing_failure_keeps_string_fallback_without_reason() -> None:
    assert is_retryable_timing_failure("Low end-marker confidence (2.0)", None) is True
    assert is_retryable_timing_failure("Selected device is unavailable.", None) is False


def test_bluetooth_high_latency_recording_can_lock_to_start_marker() -> None:
    sweep, layout = _layout(bluetooth=True)
    delay = int(round(0.45 * layout.fs))
    rec = _recording_from_layout(layout, delay_samples=delay)

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


def test_low_start_confidence_raises_existing_message() -> None:
    sweep, layout = _layout()
    rec = np.zeros(layout.total_samples, dtype=np.float32)

    with pytest.raises(ValueError, match="Low start-alignment confidence"):
        align_recording_to_layout(
            rec,
            sweep,
            layout,
            AlignmentSettings(start_alignment_confidence_min=3.0),
        )


def test_low_start_confidence_includes_structured_diagnostics() -> None:
    sweep, layout = _layout()
    rec = np.zeros(layout.total_samples, dtype=np.float32)

    with pytest.raises(MeasurementAlignmentError, match="Low start-alignment confidence") as exc:
        align_recording_to_layout(
            rec,
            sweep,
            layout,
            AlignmentSettings(start_alignment_confidence_min=3.0),
        )

    err = exc.value
    assert err.reason == MeasurementFailureReason.LOW_START_CONFIDENCE
    assert err.diagnostics.failure_reason == MeasurementFailureReason.LOW_START_CONFIDENCE
    assert err.diagnostics.selected_sweep_start is not None
    assert err.diagnostics.start_confidence is not None
    assert err.diagnostics.marker_1_start is None
    assert str(err) == err.message


def test_missing_end_marker_raises_low_confidence_message() -> None:
    sweep, layout = _layout()
    rec = np.zeros(layout.total_samples, dtype=np.float32)
    rec[layout.excitation_start_sample:layout.sweep_end_sample] = layout.excitation[
        :layout.sweep_end_sample - layout.excitation_start_sample
    ]

    with pytest.raises(ValueError, match="Low end-marker confidence"):
        align_recording_to_layout(
            rec,
            sweep,
            layout,
            AlignmentSettings(start_alignment_confidence_min=3.0, end_marker_confidence_min=2.0),
        )


def test_low_end_marker_confidence_includes_start_and_marker_diagnostics() -> None:
    sweep, layout = _layout()
    rec = np.zeros(layout.total_samples, dtype=np.float32)
    rec[layout.excitation_start_sample:layout.sweep_end_sample] = layout.excitation[
        :layout.sweep_end_sample - layout.excitation_start_sample
    ]

    with pytest.raises(MeasurementAlignmentError, match="Low end-marker confidence") as exc:
        align_recording_to_layout(
            rec,
            sweep,
            layout,
            AlignmentSettings(start_alignment_confidence_min=3.0, end_marker_confidence_min=2.0),
        )

    diagnostics = exc.value.diagnostics
    assert exc.value.reason == MeasurementFailureReason.LOW_END_MARKER_CONFIDENCE
    assert diagnostics.sweep_correlation_candidate is not None
    assert diagnostics.start_confidence is not None
    assert diagnostics.marker_confidence is not None
    assert diagnostics.marker_1_start is not None


def test_excessive_timing_drift_raises_existing_message() -> None:
    sweep, layout = _layout()
    drift = int(round(0.04 * layout.fs))
    rec = np.zeros(layout.total_samples + drift + 256, dtype=np.float32)
    rec[layout.excitation_start_sample:layout.sweep_end_sample] = layout.excitation[
        :layout.sweep_end_sample - layout.excitation_start_sample
    ]
    rec[
        layout.end_marker_1_start_sample + drift:
        layout.end_marker_1_start_sample + drift + len(layout.end_marker)
    ] = layout.end_marker
    rec[
        layout.end_marker_2_start_sample + drift:
        layout.end_marker_2_start_sample + drift + len(layout.end_marker)
    ] = layout.end_marker

    with pytest.raises(ValueError, match="Timing drift too large"):
        align_recording_to_layout(
            rec,
            sweep,
            layout,
            AlignmentSettings(
                start_alignment_confidence_min=3.0,
                end_marker_confidence_min=2.0,
                timing_drift_max_ms=5.0,
            ),
        )


def test_timing_drift_failure_includes_marker_diagnostics() -> None:
    sweep, layout = _layout()
    drift = int(round(0.04 * layout.fs))
    rec = np.zeros(layout.total_samples + drift + 256, dtype=np.float32)
    rec[layout.excitation_start_sample:layout.sweep_end_sample] = layout.excitation[
        :layout.sweep_end_sample - layout.excitation_start_sample
    ]
    _write_at(rec, layout.end_marker_1_start_sample + drift, layout.end_marker)
    _write_at(rec, layout.end_marker_2_start_sample + drift, layout.end_marker)

    with pytest.raises(MeasurementAlignmentError, match="Timing drift too large") as exc:
        align_recording_to_layout(
            rec,
            sweep,
            layout,
            AlignmentSettings(
                start_alignment_confidence_min=3.0,
                end_marker_confidence_min=2.0,
                timing_drift_max_ms=5.0,
            ),
        )

    diagnostics = exc.value.diagnostics
    assert exc.value.reason == MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE
    assert diagnostics.marker_1_start == layout.end_marker_1_start_sample + drift
    assert diagnostics.marker_2_start == layout.end_marker_2_start_sample + drift
    assert diagnostics.timing_error_ms == pytest.approx(40.0)


def test_short_recording_raises_existing_message() -> None:
    sweep, layout = _layout()

    with pytest.raises(ValueError, match="Recording shorter than expected"):
        align_recording_to_layout(
            np.zeros(layout.sweep_samples - 1, dtype=np.float32),
            sweep,
            layout,
            AlignmentSettings(),
        )


def test_snr_estimation_uses_controlled_pre_and_post_noise() -> None:
    sweep, layout = _layout()
    rec = _recording_from_layout(layout)
    rec[:layout.sweep_start_sample] = 0.01
    noise_start = layout.end_marker_2_start_sample + len(layout.end_marker)
    rec[noise_start:noise_start + int(round(0.12 * layout.fs))] = 0.01

    result = align_recording_to_layout(
        rec,
        sweep,
        layout,
        AlignmentSettings(start_alignment_confidence_min=3.0, end_marker_confidence_min=2.0),
    )

    expected = 20.0 * np.log10(float(np.sqrt(np.mean(np.square(sweep)))) / 0.01)
    assert result.snr_db == pytest.approx(expected, abs=0.25)


def test_successful_alignment_returns_matching_diagnostics() -> None:
    sweep, layout = _layout(bluetooth=True)
    delay = int(round(0.2 * layout.fs))
    result = align_recording_to_layout(
        _recording_from_layout(layout, delay_samples=delay),
        sweep,
        layout,
        _bluetooth_settings(),
    )

    diagnostics = result.diagnostics
    assert diagnostics.failure_reason is None
    assert diagnostics.bluetooth_headphone_mode is True
    assert diagnostics.latency == "high"
    assert diagnostics.selected_sweep_start == result.end.selected_sweep_start
    assert diagnostics.start_confidence == result.start.start_confidence
    assert diagnostics.marker_confidence == result.end.marker_confidence
    assert diagnostics.timing_error_ms == result.end.timing_error_ms
    assert diagnostics.snr_db == result.snr_db


def test_format_diagnostics_summary_is_plain_actionable_text() -> None:
    sweep, layout = _layout(bluetooth=True)
    result = align_recording_to_layout(
        _recording_from_layout(layout, delay_samples=123),
        sweep,
        layout,
        _bluetooth_settings(),
    )

    summary = format_diagnostics_summary(result.diagnostics)

    assert "Measurement diagnostics:" in summary
    assert "Mode: Bluetooth" in summary
    assert "Selected sweep start:" in summary
    assert "Drift:" in summary
    assert "SNR:" in summary


def test_end_marker_choice_prefers_acceptable_lower_drift_candidate() -> None:
    sweep, layout = _layout()
    rec = _recording_from_layout(layout)
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
        AlignmentSettings(end_marker_confidence_min=2.0),
        start_result,
    )

    assert result.selected_sweep_start == actual
    assert result.timing_error_samples == 0


def _bluetooth_settings() -> AlignmentSettings:
    return AlignmentSettings(
        latency="high",
        bluetooth_headphone_mode=True,
        start_alignment_confidence_min=9.0,
        end_marker_confidence_min=7.0,
        timing_drift_max_ms=120.0,
    )


def _write_at(rec: np.ndarray, start: int, values: np.ndarray) -> None:
    lo = max(0, int(start))
    hi = min(len(rec), int(start) + len(values))
    if hi <= lo:
        return
    src_lo = lo - int(start)
    src_hi = src_lo + (hi - lo)
    rec[lo:hi] += values[src_lo:src_hi]


def _ringing_kernel(fs: int) -> np.ndarray:
    n = int(round(0.018 * fs))
    t = np.arange(n, dtype=np.float64) / float(fs)
    kernel = np.exp(-t * 180.0) * np.cos(2.0 * np.pi * 1700.0 * t)
    kernel[0] += 1.0
    kernel /= np.sum(np.abs(kernel))
    return kernel.astype(np.float32)


def _time_stretch(signal: np.ndarray, stretch: float) -> np.ndarray:
    target_n = max(1, int(round(len(signal) * stretch)))
    src_x = np.arange(len(signal), dtype=np.float64)
    dst_x = np.linspace(0.0, float(len(signal) - 1), target_n)
    return np.interp(dst_x, src_x, signal).astype(np.float32)


def test_random_bluetooth_latency_jitter_aligns_when_markers_are_intact() -> None:
    sweep, layout = _layout(bluetooth=True)
    rng = np.random.default_rng(20260505)
    base_delay = int(round(0.35 * layout.fs))
    jitters = rng.integers(
        low=-int(round(0.08 * layout.fs)),
        high=int(round(0.08 * layout.fs)),
        size=9,
    )

    for jitter in jitters:
        delay = base_delay + int(jitter)
        rec = _recording_from_layout(layout, delay_samples=delay)

        result = align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())

        expected_start = layout.sweep_start_sample + delay
        assert result.start.selected_sweep_start == expected_start
        assert result.end.selected_sweep_start == expected_start
        assert result.end.timing_error_samples == 0
        assert result.end.spacing_error_samples == 0
        np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)


def test_missing_start_audio_fails_or_locks_to_remaining_valid_marker_evidence() -> None:
    sweep, layout = _layout(bluetooth=True)
    delay = int(round(0.25 * layout.fs))
    rec = _recording_from_layout(layout, delay_samples=delay)
    clip_until = delay + layout.sweep_start_sample + int(round(0.08 * layout.fs))
    rec[:clip_until] = 0.0

    result = align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())

    assert result.start.sweep_correlation_candidate == layout.sweep_start_sample + delay
    assert result.start.marker_locked_candidate is not None
    assert result.end.selected_sweep_start == layout.sweep_start_sample + delay
    assert np.max(np.abs(result.aligned_recording[:int(round(0.08 * layout.fs))])) == 0.0


def test_truncated_tail_audio_reports_end_or_aligned_length_failure() -> None:
    sweep, layout = _layout(bluetooth=True)
    delay = int(round(0.2 * layout.fs))
    rec = _recording_from_layout(layout, delay_samples=delay)
    trunc_at = delay + layout.sweep_end_sample + int(round(0.01 * layout.fs))
    rec = rec[:trunc_at]

    with pytest.raises(
        ValueError,
        match="Low end-marker confidence|Unable to verify end marker timing|Aligned recording shorter than expected",
    ):
        align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())


def test_false_marker_peak_does_not_beat_valid_marker_pair() -> None:
    sweep, layout = _layout(bluetooth=True)
    delay = int(round(0.32 * layout.fs))
    rec = _recording_from_layout(layout, delay_samples=delay)
    false_offset = int(round(0.16 * layout.fs))
    false_marker = (1.35 * layout.end_marker).astype(np.float32)
    _write_at(rec, delay + layout.end_marker_1_start_sample - false_offset, false_marker)
    _write_at(rec, delay + layout.end_marker_2_start_sample + false_offset, false_marker)

    result = align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())

    expected_start = layout.sweep_start_sample + delay
    assert result.end.selected_sweep_start == expected_start
    assert result.end.marker_1_start == layout.end_marker_1_start_sample + delay
    assert result.end.marker_2_start == layout.end_marker_2_start_sample + delay
    assert result.end.timing_error_samples == 0
    assert result.end.spacing_error_samples == 0


def test_loud_reversed_marker_artifacts_do_not_displace_ordered_pair() -> None:
    sweep, layout = _layout(bluetooth=True)
    delay = int(round(0.24 * layout.fs))
    rec = _recording_from_layout(layout, delay_samples=delay)
    spacing = len(layout.end_marker) + layout.end_marker_pair_gap_samples
    loud_marker = (1.6 * layout.end_marker).astype(np.float32)
    _write_at(rec, delay + layout.end_marker_1_start_sample + spacing, loud_marker)
    _write_at(rec, delay + layout.end_marker_2_start_sample - spacing, loud_marker)

    result = align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())

    assert result.end.selected_sweep_start == layout.sweep_start_sample + delay
    assert result.end.marker_1_start < result.end.marker_2_start
    assert result.end.spacing_error_samples == 0


def test_codec_like_marker_ringing_keeps_drift_near_true_marker() -> None:
    sweep, layout = _layout(bluetooth=True)
    delay = int(round(0.28 * layout.fs))
    rec = _recording_from_layout(layout, delay_samples=delay)
    rec = np.convolve(rec, _ringing_kernel(layout.fs), mode="full").astype(np.float32)

    result = align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())

    expected_start = layout.sweep_start_sample + delay
    assert abs(result.end.selected_sweep_start - expected_start) <= 2
    assert result.end.timing_error_ms <= 5.0
    assert np.sqrt(np.mean(np.square(result.aligned_recording))) > 0.0


def test_sample_rate_drift_produces_drift_failure_when_large() -> None:
    sweep, layout = _layout(bluetooth=True)
    rec = _recording_from_layout(layout)
    drift = int(round(0.14 * layout.fs))
    rec[
        layout.end_marker_1_start_sample:
        layout.end_marker_2_start_sample + len(layout.end_marker)
    ] = 0.0
    _write_at(rec, layout.end_marker_1_start_sample + drift, layout.end_marker)
    _write_at(rec, layout.end_marker_2_start_sample + drift, layout.end_marker)

    with pytest.raises(ValueError, match="Timing drift too large"):
        align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())


def test_retry_after_bad_run_can_succeed_with_same_layout() -> None:
    sweep, layout = _layout(bluetooth=True)
    bad_rec = np.zeros(layout.total_samples, dtype=np.float32)

    with pytest.raises(ValueError, match="Low start-alignment confidence"):
        align_recording_to_layout(bad_rec, sweep, layout, _bluetooth_settings())

    delay = int(round(0.18 * layout.fs))
    good_rec = _recording_from_layout(layout, delay_samples=delay)
    result = align_recording_to_layout(good_rec, sweep, layout, _bluetooth_settings())

    assert result.start.selected_sweep_start == layout.sweep_start_sample + delay
    assert result.end.timing_error_ms <= 120.0
    np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)
