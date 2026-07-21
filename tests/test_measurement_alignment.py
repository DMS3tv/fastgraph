import numpy as np
import pytest

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
    find_start_alignment,
    format_diagnostics_summary,
    is_retryable_timing_failure,
)
from dms.measurement_layout import build_measurement_layout


def _test_sweep(length: int, taper_edges_only: bool = False) -> np.ndarray:
    rng = np.random.default_rng(1234)
    sweep = rng.normal(0.0, 0.2, length).astype(np.float32)
    if taper_edges_only:
        # Matches production sweeps (dms/processing.py generate_log_sweep), which
        # only cosine-fade a short edge region to avoid clicks rather than
        # windowing the whole waveform. Standard-mode tests need this realistic
        # envelope so the H4 sweep-coverage gate (which compares RMS across 8
        # equal segments) doesn't mistake a full-length taper for dropout.
        fade_n = max(1, length // 40)
        fade = (np.sin(np.linspace(0.0, np.pi / 2, fade_n)) ** 2).astype(np.float32)
        sweep[:fade_n] *= fade
        sweep[-fade_n:] *= fade[::-1]
    else:
        sweep *= np.hanning(length).astype(np.float32)
    return sweep


def _recording_from_layout(layout, delay_samples: int = 0) -> np.ndarray:
    rec = np.zeros(layout.total_samples + delay_samples + 256, dtype=np.float32)
    start = delay_samples + layout.excitation_start_sample
    rec[start:start + len(layout.excitation)] = layout.excitation
    return rec


def _layout(fs: int = 8_000, bluetooth: bool = False):
    # Standard-mode tests use a realistic (edges-only) taper so the H4
    # sweep-coverage gate sees uniform in-band RMS for genuinely clean
    # recordings; Bluetooth-mode tests keep the original full-window taper
    # since those code paths never exercise the new coverage/SNR gates.
    sweep = _test_sweep(2_048, taper_edges_only=not bluetooth)
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
    assert result.diagnostics.marker_confidence is None
    assert result.diagnostics.timing_error_ms is None
    np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)


def test_standard_mode_has_no_timing_marker_failure_when_markers_are_absent() -> None:
    sweep, layout = _layout()
    rec = _recording_from_layout(layout)

    result = align_recording_to_layout(
        rec,
        sweep,
        layout,
        AlignmentSettings(
            # start_alignment_confidence_min left at the production default (9.0):
            # standard mode now enforces this gate too (see H4 fix), so 30.0 would
            # reject this clean synthetic recording. end_marker/timing thresholds
            # stay unreachable to prove standard mode ignores them.
            end_marker_confidence_min=30.0,
            timing_drift_max_ms=5.0,
        ),
    )

    assert result.diagnostics.failure_reason is None
    assert result.diagnostics.bluetooth_headphone_mode is False
    assert result.diagnostics.marker_1_start is None
    assert result.diagnostics.marker_2_start is None
    assert result.diagnostics.marker_confidence is None
    assert result.diagnostics.timing_error_ms is None
    np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)


@pytest.mark.parametrize(
    "reason",
    [
        MeasurementFailureReason.LOW_START_CONFIDENCE,
        MeasurementFailureReason.LOW_END_MARKER_CONFIDENCE,
        MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE,
        MeasurementFailureReason.END_MARKER_UNVERIFIED,
    ],
)
def test_retryable_timing_failure_uses_structured_reasons(reason: str) -> None:
    assert is_retryable_timing_failure("Unrelated user-facing copy", reason) is True


@pytest.mark.parametrize(
    "reason",
    [
        MeasurementFailureReason.SHORT_RECORDING,
        MeasurementFailureReason.SHORT_ALIGNED_RECORDING,
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


def test_start_marker_lock_rejected_by_bounds_check_does_not_boost_confidence() -> None:
    # M2: `start_conf = max(start_conf, min_start_conf)` must only apply when
    # the marker lock passes the `marker_locked_start <= max_start_idx` bounds
    # check. This builds a short BT-mode recording where the start marker is
    # detected strongly (a valid lock candidate on its own) but sits too close
    # to the end of the recording to leave room for a full sweep window, so
    # marker_locked_start > max_start_idx and the lock must be rejected. Before
    # the fix, the boost applied anyway and let the low raw correlation
    # confidence slip past the low-confidence gate; after the fix, the gate
    # fires on the unboosted (low) confidence.
    fs = 8_000
    sweep = _test_sweep(2_048)
    layout = build_measurement_layout(
        sweep=sweep,
        fs=fs,
        pre_silence_s=0.08,
        post_silence_s=0.16,
        bluetooth_headphone_mode=True,
    )

    rng = np.random.default_rng(99)
    # Shorter than sweep_n + the marker search radius, so the marker search
    # window covers the whole recording regardless of where the (noise-only)
    # sweep-correlation candidate lands.
    total_len = int(round(0.3 * fs))
    rec = rng.normal(0.0, 0.02, total_len).astype(np.float32)
    marker_pos = total_len - len(layout.start_marker) - 50
    _write_at(rec, marker_pos, (4.0 * layout.start_marker).astype(np.float32))

    settings = AlignmentSettings(
        bluetooth_headphone_mode=True,
        start_alignment_confidence_min=9.0,
    )

    with pytest.raises(
        MeasurementAlignmentError, match="Low start-alignment confidence"
    ) as exc:
        find_start_alignment(rec, sweep, layout, settings)

    diagnostics = exc.value.diagnostics
    # The marker itself matched strongly (a real lock candidate)...
    assert diagnostics.start_marker_confidence >= 3.5
    # ...but the bounds check correctly rejected using it as the start...
    assert diagnostics.marker_locked_candidate is None
    # ...so the reported confidence must be the raw (unboosted) value, well
    # below the 3.0 floor the (buggy) boost would have produced.
    assert diagnostics.start_confidence < 3.0


def test_low_start_confidence_raises_existing_message() -> None:
    sweep, layout = _layout(bluetooth=True)
    rec = np.zeros(layout.total_samples, dtype=np.float32)

    with pytest.raises(ValueError, match="Low start-alignment confidence"):
        align_recording_to_layout(
            rec,
            sweep,
            layout,
            AlignmentSettings(
                bluetooth_headphone_mode=True,
                start_alignment_confidence_min=3.0,
            ),
        )


def test_low_start_confidence_includes_structured_diagnostics() -> None:
    sweep, layout = _layout(bluetooth=True)
    rec = np.zeros(layout.total_samples, dtype=np.float32)

    with pytest.raises(MeasurementAlignmentError, match="Low start-alignment confidence") as exc:
        align_recording_to_layout(
            rec,
            sweep,
            layout,
            AlignmentSettings(
                bluetooth_headphone_mode=True,
                start_alignment_confidence_min=3.0,
            ),
        )

    err = exc.value
    assert err.reason == MeasurementFailureReason.LOW_START_CONFIDENCE
    assert err.diagnostics.failure_reason == MeasurementFailureReason.LOW_START_CONFIDENCE
    assert err.diagnostics.selected_sweep_start is not None
    assert err.diagnostics.start_confidence is not None
    assert err.diagnostics.marker_1_start is None
    assert str(err) == err.message


def test_missing_end_marker_uses_bluetooth_sweep_fallback() -> None:
    sweep, layout = _layout(bluetooth=True)
    rec = np.zeros(layout.total_samples, dtype=np.float32)
    rec[layout.excitation_start_sample:layout.sweep_end_sample] = layout.excitation[
        :layout.sweep_end_sample - layout.excitation_start_sample
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
    assert result.diagnostics.warning_reason == (
        MeasurementWarningReason.BLUETOOTH_SWEEP_FALLBACK
    )
    assert result.diagnostics.alignment_mode == "sweep_fallback"
    assert (
        result.diagnostics.marker_failure_reason
        == MeasurementFailureReason.LOW_END_MARKER_CONFIDENCE
    )
    np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)


def test_low_snr_missing_markers_still_fails_with_marker_diagnostics() -> None:
    sweep, layout = _layout(bluetooth=True)
    rng = np.random.default_rng(20260524)
    rec = rng.normal(0.0, 0.05, layout.total_samples).astype(np.float32)
    rec[layout.excitation_start_sample:layout.sweep_end_sample] = layout.excitation[
        :layout.sweep_end_sample - layout.excitation_start_sample
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
    sweep, layout = _layout(bluetooth=True)
    drift = int(round(0.18 * layout.fs))
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
        layout.end_marker_2_start_sample + drift + len(layout.end_marker_2)
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
    sweep, layout = _layout(bluetooth=True)
    drift = int(round(0.18 * layout.fs))
    rec = np.zeros(layout.total_samples + drift + 256, dtype=np.float32)
    rec[layout.excitation_start_sample:layout.sweep_end_sample] = layout.excitation[
        :layout.sweep_end_sample - layout.excitation_start_sample
    ]
    _write_at(rec, layout.end_marker_1_start_sample + drift, layout.end_marker)
    _write_at(rec, layout.end_marker_2_start_sample + drift, layout.end_marker_2)

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
    sweep, layout = _layout(fs=48_000, bluetooth=True)
    drift = int(round(0.1388 * layout.fs))
    rec = _recording_with_shifted_end_markers(layout, drift, marker_scale=1.0)

    result = align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())

    assert result.diagnostics.failure_reason is None
    assert (
        result.diagnostics.warning_reason
        == MeasurementWarningReason.BLUETOOTH_MARGINAL_DRIFT
    )
    assert "Bluetooth timing drift is marginal" in result.diagnostics.warning_message
    assert result.end.marker_confidence >= 2.5
    assert result.end.timing_error_ms > result.diagnostics.timing_drift_max_ms
    assert result.end.timing_error_ms <= 160.0


def test_bluetooth_marginal_drift_accepts_weak_but_consistent_end_markers(monkeypatch) -> None:
    sweep, layout = _layout(fs=48_000, bluetooth=True)
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
        _recording_from_layout(layout),
        sweep,
        layout,
        _bluetooth_settings(),
    )

    assert result.diagnostics.failure_reason is None
    assert (
        result.diagnostics.warning_reason
        == MeasurementWarningReason.BLUETOOTH_MARGINAL_DRIFT
    )
    assert result.end.marker_confidence == pytest.approx(2.1)
    assert "end confidence 2.1" in result.diagnostics.warning_message


def test_bluetooth_weak_end_markers_use_sweep_fallback(monkeypatch) -> None:
    sweep, layout = _layout(fs=48_000, bluetooth=True)
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
        _recording_from_layout(layout),
        sweep,
        layout,
        _bluetooth_settings(),
    )

    assert result.diagnostics.failure_reason is None
    assert result.diagnostics.warning_reason == (
        MeasurementWarningReason.BLUETOOTH_SWEEP_FALLBACK
    )
    assert result.diagnostics.alignment_mode == "sweep_fallback"
    assert (
        result.diagnostics.marker_failure_reason
        == MeasurementFailureReason.LOW_END_MARKER_CONFIDENCE
    )


def test_bluetooth_extreme_drift_still_fails() -> None:
    sweep, layout = _layout(fs=48_000, bluetooth=True)
    drift = int(round(0.180 * layout.fs))
    rec = _recording_with_shifted_end_markers(layout, drift)

    with pytest.raises(MeasurementAlignmentError) as exc:
        align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())

    assert exc.value.diagnostics.warning_reason is None
    assert exc.value.reason in {
        MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE,
        MeasurementFailureReason.LOW_END_MARKER_CONFIDENCE,
        MeasurementFailureReason.END_MARKER_UNVERIFIED,
    }


def test_bluetooth_end_marker_search_covers_marginal_ceiling_plus_slack() -> None:
    sweep, layout = _layout(fs=48_000, bluetooth=True)
    drift = int(round(0.165 * layout.fs))
    rec = _recording_with_shifted_end_markers(layout, drift, marker_scale=1.0)

    with pytest.raises(MeasurementAlignmentError, match="Timing drift too large") as exc:
        align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())

    assert exc.value.reason == MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE
    assert exc.value.diagnostics.marker_1_start is not None
    assert exc.value.diagnostics.marker_2_start is not None
    assert exc.value.diagnostics.timing_error_ms > 160.0


def test_bluetooth_marginal_drift_with_excessive_spacing_error_fails() -> None:
    sweep, layout = _layout(fs=48_000, bluetooth=True)
    drift = int(round(0.1388 * layout.fs))
    spacing_extra = int(round(0.030 * layout.fs))
    rec = _recording_with_shifted_end_markers(
        layout,
        drift,
        marker_2_extra_shift=spacing_extra,
    )

    with pytest.raises(MeasurementAlignmentError, match="Timing drift too large") as exc:
        align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())

    assert exc.value.reason == MeasurementFailureReason.TIMING_DRIFT_TOO_LARGE
    assert exc.value.diagnostics.spacing_error_samples > int(
        round(960.0 * layout.fs / 48000.0)
    )


def test_short_recording_raises_existing_message() -> None:
    sweep, layout = _layout()

    with pytest.raises(ValueError, match="Recording shorter than expected"):
        align_recording_to_layout(
            np.zeros(layout.sweep_samples - 1, dtype=np.float32),
            sweep,
            layout,
            AlignmentSettings(),
        )


def test_nan_laced_recording_raises_invalid_recording_instead_of_propagating() -> None:
    # M3: NaN samples make rejection comparisons evaluate False (NaN-comparison
    # semantics), so without an explicit finite-ness guard a NaN-laced
    # recording could otherwise slip through as a "successful" NaN-laden
    # result instead of being rejected outright.
    sweep, layout = _layout()
    rec = _recording_from_layout(layout)
    rec[layout.sweep_start_sample + 10] = np.nan

    with pytest.raises(
        MeasurementAlignmentError, match="invalid samples"
    ) as exc:
        align_recording_to_layout(rec, sweep, layout, AlignmentSettings())

    assert exc.value.reason == MeasurementFailureReason.INVALID_RECORDING
    assert is_retryable_timing_failure(
        exc.value.message, exc.value.reason
    ) is True


def test_snr_estimation_uses_controlled_pre_and_post_noise() -> None:
    sweep, layout = _layout()
    rec = _recording_from_layout(layout)
    rec[:layout.sweep_start_sample] = 0.01
    noise_start = layout.sweep_end_sample
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
    assert diagnostics.alignment_mode == "marker"
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
    assert "Alignment mode: marker" in summary
    assert "Selected sweep start:" in summary
    assert "Drift:" in summary
    assert "SNR:" in summary


def test_standard_diagnostics_summary_omits_marker_timing_fields() -> None:
    sweep, layout = _layout()
    result = align_recording_to_layout(
        _recording_from_layout(layout),
        sweep,
        layout,
        AlignmentSettings(),
    )

    summary = format_diagnostics_summary(result.diagnostics)

    assert "Mode: Standard" in summary
    assert "Start confidence:" in summary
    assert "SNR:" in summary
    assert "End markers:" not in summary
    assert "Drift:" not in summary


def test_format_diagnostics_summary_includes_warning_text() -> None:
    sweep, layout = _layout(fs=48_000, bluetooth=True)
    drift = int(round(0.1388 * layout.fs))
    result = align_recording_to_layout(
        _recording_with_shifted_end_markers(layout, drift),
        sweep,
        layout,
        _bluetooth_settings(),
    )

    summary = format_diagnostics_summary(result.diagnostics)

    assert "Warning reason: bluetooth_marginal_drift" in summary
    assert "Warning: Bluetooth timing drift is marginal" in summary


def test_format_diagnostics_summary_includes_sweep_fallback_reason() -> None:
    sweep, layout = _layout(bluetooth=True)
    rec = np.zeros(layout.total_samples, dtype=np.float32)
    rec[layout.excitation_start_sample:layout.sweep_end_sample] = layout.excitation[
        :layout.sweep_end_sample - layout.excitation_start_sample
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

    summary = format_diagnostics_summary(result.diagnostics)

    assert "Warning reason: bluetooth_sweep_fallback" in summary
    assert "Alignment mode: sweep_fallback" in summary
    assert "Marker failure reason: low_end_marker_confidence" in summary


def test_end_marker_choice_prefers_acceptable_lower_drift_candidate() -> None:
    sweep, layout = _layout(bluetooth=True)
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
        AlignmentSettings(
            bluetooth_headphone_mode=True,
            end_marker_confidence_min=2.0,
        ),
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


def _recording_with_shifted_end_markers(
    layout,
    drift_samples: int,
    marker_2_extra_shift: int = 0,
    marker_scale: float = 3.0,
) -> np.ndarray:
    rec = _recording_from_layout(layout)
    marker_len = len(layout.end_marker)
    marker_2_len = len(layout.end_marker_2)
    rec[
        layout.end_marker_1_start_sample:
        layout.end_marker_1_start_sample + marker_len
    ] = 0.0
    rec[
        layout.end_marker_2_start_sample:
        layout.end_marker_2_start_sample + marker_2_len
    ] = 0.0
    marker_1 = (marker_scale * layout.end_marker).astype(np.float32)
    marker_2 = (marker_scale * layout.end_marker_2).astype(np.float32)
    _write_at(rec, layout.end_marker_1_start_sample + drift_samples, marker_1)
    _write_at(
        rec,
        layout.end_marker_2_start_sample + drift_samples + marker_2_extra_shift,
        marker_2,
    )
    return rec


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


def test_truncated_tail_audio_can_use_bluetooth_sweep_fallback() -> None:
    sweep, layout = _layout(bluetooth=True)
    delay = int(round(0.2 * layout.fs))
    rec = _recording_from_layout(layout, delay_samples=delay)
    trunc_at = delay + layout.sweep_end_sample + int(round(0.01 * layout.fs))
    rec = rec[:trunc_at]

    result = align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())

    assert result.diagnostics.warning_reason == (
        MeasurementWarningReason.BLUETOOTH_SWEEP_FALLBACK
    )
    np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)


def test_truncated_sweep_window_still_fails() -> None:
    sweep, layout = _layout(bluetooth=True)
    delay = int(round(0.2 * layout.fs))
    rec = _recording_from_layout(layout, delay_samples=delay)
    trunc_at = delay + layout.sweep_end_sample - int(round(0.05 * layout.fs))
    rec = rec[:trunc_at]

    # M2: this recording is deliberately 0.05*fs short of the room a full
    # sweep window needs past the true onset, which is exactly the
    # marker-locked-start-exceeds-max_start_idx bounds-rejection geometry.
    # Before the M2 fix, the confidence boost applied despite that rejection
    # and let a bogus start position slip past the start gate, so this test
    # only ever observed the downstream end-marker/timing failures. With the
    # fix, the (correct, unboosted) low raw confidence is now what's reported
    # -- also a legitimate "still fails" outcome for a recording that
    # genuinely cannot support a full sweep window.
    with pytest.raises(
        ValueError,
        match=(
            "Low start-alignment confidence|Low end-marker confidence|"
            "Unable to verify end marker timing|"
            "Aligned recording shorter than expected|Timing drift too large"
        ),
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


def test_single_band_marker_like_peak_does_not_beat_coded_marker_pair() -> None:
    sweep, layout = _layout(bluetooth=True)
    delay = int(round(0.31 * layout.fs))
    rec = _recording_from_layout(layout, delay_samples=delay)
    false_offset = int(round(0.14 * layout.fs))
    false_peak = _single_tone_marker_like_peak(layout)
    _write_at(rec, delay + layout.end_marker_1_start_sample - false_offset, false_peak)
    _write_at(rec, delay + layout.end_marker_2_start_sample + false_offset, false_peak)

    result = align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())

    assert result.end.marker_1_start == layout.end_marker_1_start_sample + delay
    assert result.end.marker_2_start == layout.end_marker_2_start_sample + delay
    assert result.end.timing_error_samples == 0
    assert result.end.spacing_error_samples == 0


def test_marker_identity_rejects_reversed_coded_marker_order() -> None:
    sweep, layout = _layout(bluetooth=True)
    delay = int(round(0.22 * layout.fs))
    rec = _recording_from_layout(layout, delay_samples=delay)
    rec[
        delay + layout.end_marker_1_start_sample:
        delay + layout.end_marker_1_start_sample + len(layout.end_marker)
    ] = 0.0
    rec[
        delay + layout.end_marker_2_start_sample:
        delay + layout.end_marker_2_start_sample + len(layout.end_marker_2)
    ] = 0.0
    _write_at(rec, delay + layout.end_marker_1_start_sample, 2.0 * layout.end_marker_2)
    _write_at(rec, delay + layout.end_marker_2_start_sample, 2.0 * layout.end_marker)

    with pytest.raises(
        MeasurementAlignmentError,
        match="Low end-marker confidence|Unable to verify end marker timing|Timing drift too large",
    ):
        align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())


def test_duplicated_same_coded_marker_does_not_pass_as_valid_pair() -> None:
    sweep, layout = _layout(bluetooth=True)
    delay = int(round(0.22 * layout.fs))
    rec = _recording_from_layout(layout, delay_samples=delay)
    rec[
        delay + layout.end_marker_2_start_sample:
        delay + layout.end_marker_2_start_sample + len(layout.end_marker_2)
    ] = 0.0
    _write_at(rec, delay + layout.end_marker_2_start_sample, 2.0 * layout.end_marker)

    with pytest.raises(
        MeasurementAlignmentError,
        match="Low end-marker confidence|Unable to verify end marker timing|Timing drift too large",
    ):
        align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())


def test_loud_reversed_marker_artifacts_do_not_displace_ordered_pair() -> None:
    sweep, layout = _layout(bluetooth=True)
    delay = int(round(0.24 * layout.fs))
    rec = _recording_from_layout(layout, delay_samples=delay)
    spacing = len(layout.end_marker) + layout.end_marker_pair_gap_samples
    loud_marker_1 = (1.6 * layout.end_marker).astype(np.float32)
    loud_marker_2 = (1.6 * layout.end_marker_2).astype(np.float32)
    _write_at(rec, delay + layout.end_marker_1_start_sample + spacing, loud_marker_2)
    _write_at(rec, delay + layout.end_marker_2_start_sample - spacing, loud_marker_1)

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
    drift = int(round(0.18 * layout.fs))
    rec[
        layout.end_marker_1_start_sample:
        layout.end_marker_2_start_sample + len(layout.end_marker_2)
    ] = 0.0
    _write_at(rec, layout.end_marker_1_start_sample + drift, layout.end_marker)
    _write_at(rec, layout.end_marker_2_start_sample + drift, layout.end_marker_2)

    with pytest.raises(
        ValueError,
        match="Timing drift too large|Low end-marker confidence|Unable to verify end marker timing",
    ):
        align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())


def test_bluetooth_stretched_playback_keeps_end_marker_confidence() -> None:
    fs = 8_000
    sweep = _test_sweep(int(round(3.5 * fs)))
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
    _write_at(rec, layout.excitation_start_sample, stretched_excitation)

    result = align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())

    assert result.end.marker_confidence >= 2.5
    assert result.diagnostics.raw_marker_confidence == pytest.approx(
        result.end.marker_confidence
    )
    assert result.end.spacing_error_samples <= int(round(960.0 * fs / 48000.0))
    assert result.diagnostics.marker_template_stretch is not None
    assert result.diagnostics.marker_template_stretch > 1.0
    assert result.diagnostics.warning_reason == (
        MeasurementWarningReason.BLUETOOTH_MARGINAL_DRIFT
    )


def test_bluetooth_profile_tail_covers_high_output_latency_recording_window() -> None:
    fs = 8_000
    sweep = _test_sweep(int(round(3.5 * fs)))
    layout = build_measurement_layout(
        sweep=sweep,
        fs=fs,
        pre_silence_s=0.6,
        post_silence_s=2.0,
        bluetooth_headphone_mode=True,
    )
    rec = np.zeros(layout.total_samples, dtype=np.float32)
    bluetooth_delay = int(round(0.9 * fs))
    _write_at(rec, layout.excitation_start_sample + bluetooth_delay, layout.excitation)

    result = align_recording_to_layout(rec, sweep, layout, _bluetooth_settings())

    assert result.end.marker_confidence >= 2.5
    assert result.end.timing_error_ms <= 5.0
    assert result.end.spacing_error_samples == 0
    np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)


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


def test_standard_mode_clean_recording_still_aligns_at_default_start_confidence() -> None:
    # H4: standard mode now enforces start_alignment_confidence_min too. A clean
    # recording must still align successfully at the production default (9.0).
    sweep, layout = _layout()
    delay = 137
    rec = _recording_from_layout(layout, delay_samples=delay)

    result = align_recording_to_layout(rec, sweep, layout, AlignmentSettings())

    assert result.diagnostics.failure_reason is None
    assert result.start.start_confidence >= 9.0
    assert result.start.selected_sweep_start == layout.sweep_start_sample + delay
    np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)


def test_standard_mode_rejects_silent_recording() -> None:
    # H4: an all-zeros recording must no longer silently argmax-align to noise.
    sweep, layout = _layout()
    rec = np.zeros(layout.total_samples, dtype=np.float32)

    with pytest.raises(MeasurementAlignmentError, match="Low start-alignment confidence") as exc:
        align_recording_to_layout(rec, sweep, layout, AlignmentSettings())

    assert exc.value.reason == MeasurementFailureReason.LOW_START_CONFIDENCE
    assert exc.value.diagnostics.start_confidence is not None
    assert exc.value.diagnostics.start_confidence < 9.0


def test_standard_mode_rejects_pure_noise_recording() -> None:
    # H4: a recording with no sweep content at all (e.g. wrong input channel)
    # must be rejected rather than aligning to whatever correlates best with noise.
    sweep, layout = _layout()
    rng = np.random.default_rng(42)
    rec = rng.normal(0.0, 0.05, layout.total_samples).astype(np.float32)

    with pytest.raises(MeasurementAlignmentError, match="Low start-alignment confidence") as exc:
        align_recording_to_layout(rec, sweep, layout, AlignmentSettings())

    assert exc.value.reason == MeasurementFailureReason.LOW_START_CONFIDENCE
    assert exc.value.diagnostics.start_confidence is not None
    assert exc.value.diagnostics.start_confidence < 9.0


def test_standard_mode_rejects_dropout_recording() -> None:
    # H4-residual: simulate a dropout where the recording is clean up to the
    # sweep onset but goes silent partway through (zero-padded tail, same
    # total length). An SNR floor cannot catch this -- the zeroed tail makes
    # the measured noise floor ~0, so SNR looks artificially high -- which is
    # exactly why the sweep-coverage gate exists. With the default settings,
    # onset confidence (~9.1) stays above start_alignment_confidence_min
    # (9.0), so the start gate does NOT fire here; the coverage gate (segment
    # RMS ratio ~0.0, far below sweep_coverage_min=0.05) is what catches it.
    sweep, layout = _layout()
    rec = _recording_from_layout(layout)
    mid = layout.excitation_start_sample + len(layout.excitation) // 2
    rec[mid:] = 0.0

    with pytest.raises(MeasurementAlignmentError) as exc:
        align_recording_to_layout(rec, sweep, layout, AlignmentSettings())

    assert exc.value.reason == MeasurementFailureReason.INCOMPLETE_SWEEP
    assert exc.value.diagnostics.start_confidence is not None
    assert exc.value.diagnostics.start_confidence >= 9.0


def _segment_rms_ratio(aligned_recording: np.ndarray) -> float:
    n = len(aligned_recording)
    edges = np.linspace(0, n, 9, dtype=int)
    segment_rms = [
        float(np.sqrt(np.mean(np.square(aligned_recording[edges[i]:edges[i + 1]]))))
        for i in range(8)
    ]
    median_rms = float(np.median(segment_rms))
    return float(np.min(segment_rms)) / median_rms if median_rms > 0 else 0.0


def test_h4_residual_clean_sweep_passes_snr_and_coverage_gates_by_default(
    capsys,
) -> None:
    # H4-residual (a): a clean recording with realistic edge-only sweep
    # tapering must pass both new gates at production defaults (snr_min_db=6.0,
    # sweep_coverage_min=0.05).
    sweep, layout = _layout()
    rec = _recording_from_layout(layout)

    result = align_recording_to_layout(rec, sweep, layout, AlignmentSettings())

    ratio = _segment_rms_ratio(result.aligned_recording)
    print(
        f"[H4-residual clean case] snr_db={result.snr_db:.1f} "
        f"segment_rms_ratio={ratio:.3f}"
    )
    assert result.diagnostics.failure_reason is None
    assert result.snr_db >= 6.0
    assert ratio >= 0.25


def test_h4_residual_mid_sweep_dropout_is_caught_by_coverage_not_snr() -> None:
    # H4-residual (b): pinned in test_standard_mode_rejects_dropout_recording
    # above. This test additionally documents the measured segment-RMS ratio
    # for reviewers, and confirms the physics claim that a zeroed tail keeps
    # SNR looking artificially high (noise floor ~0) so the SNR gate alone
    # would never catch this -- only the coverage gate does.
    sweep, layout = _layout()
    rec = _recording_from_layout(layout)
    mid = layout.excitation_start_sample + len(layout.excitation) // 2
    rec[mid:] = 0.0

    with pytest.raises(MeasurementAlignmentError) as exc:
        align_recording_to_layout(rec, sweep, layout, AlignmentSettings())

    assert exc.value.reason == MeasurementFailureReason.INCOMPLETE_SWEEP

    # With the coverage gate disabled, the same recording proceeds to SNR
    # estimation, which reports an artificially high SNR -- proving an SNR
    # floor cannot substitute for the coverage gate on this failure mode.
    result = align_recording_to_layout(
        rec, sweep, layout, AlignmentSettings(sweep_coverage_min=0.0)
    )
    assert result.snr_db >= 100.0


def test_h4_residual_heavy_noise_with_confident_start_triggers_low_snr() -> None:
    # H4-residual (c): additive noise (seeded) heavy enough to push snr_db
    # below the 6.0 default floor while start-alignment confidence stays
    # comfortably above the 9.0 default gate, isolating the SNR gate.
    sweep, layout = _layout()
    rec = _recording_from_layout(layout)
    rng = np.random.default_rng(777)
    noisy_rec = (rec + rng.normal(0.0, 0.13, len(rec)).astype(np.float32)).astype(
        np.float32
    )

    with pytest.raises(MeasurementAlignmentError, match="Low measurement SNR") as exc:
        align_recording_to_layout(noisy_rec, sweep, layout, AlignmentSettings())

    diagnostics = exc.value.diagnostics
    assert exc.value.reason == MeasurementFailureReason.LOW_SNR
    assert diagnostics.start_confidence is not None
    assert diagnostics.start_confidence >= 9.0
    assert diagnostics.snr_db is not None
    assert diagnostics.snr_db < 6.0


def test_h4_residual_zero_thresholds_disable_both_gates() -> None:
    # H4-residual (d): snr_min_db=0 and sweep_coverage_min=0 disable both new
    # gates, so the degraded recordings from (b)/(c) succeed again.
    sweep, layout = _layout()
    disabled_settings = AlignmentSettings(snr_min_db=0.0, sweep_coverage_min=0.0)

    dropout_rec = _recording_from_layout(layout)
    mid = layout.excitation_start_sample + len(layout.excitation) // 2
    dropout_rec[mid:] = 0.0
    dropout_result = align_recording_to_layout(
        dropout_rec, sweep, layout, disabled_settings
    )
    assert dropout_result.diagnostics.failure_reason is None

    rng = np.random.default_rng(777)
    noisy_rec = (
        _recording_from_layout(layout)
        + rng.normal(0.0, 0.13, layout.total_samples + 256).astype(np.float32)
    ).astype(np.float32)
    noisy_result = align_recording_to_layout(
        noisy_rec, sweep, layout, disabled_settings
    )
    assert noisy_result.diagnostics.failure_reason is None
