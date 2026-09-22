"""Start alignment, diagnostics and failure reporting in ``dms.measurement_alignment``."""

import numpy as np
import pytest
from helpers import (
    bluetooth_alignment_settings,
    recording_from_layout,
    recording_with_shifted_end_markers,
    short_layout,
)

from dms.measurement_alignment import (
    AlignmentSettings,
    MeasurementAlignmentError,
    MeasurementFailureReason,
    align_recording_to_layout,
    format_diagnostics_summary,
    is_retryable_timing_failure,
)


def test_aligns_clean_non_bluetooth_recording_with_fixed_latency() -> None:
    sweep, layout = short_layout()
    delay = 137
    rec = recording_from_layout(layout, delay_samples=delay)

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
    sweep, layout = short_layout()
    rec = recording_from_layout(layout)

    result = align_recording_to_layout(
        rec,
        sweep,
        layout,
        AlignmentSettings(
            start_alignment_confidence_min=30.0,
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


def test_low_start_confidence_raises_existing_message() -> None:
    sweep, layout = short_layout(bluetooth=True)
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
    sweep, layout = short_layout(bluetooth=True)
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


def test_short_recording_raises_existing_message() -> None:
    sweep, layout = short_layout()

    with pytest.raises(ValueError, match="Recording shorter than expected"):
        align_recording_to_layout(
            np.zeros(layout.sweep_samples - 1, dtype=np.float32),
            sweep,
            layout,
            AlignmentSettings(),
        )


def test_snr_estimation_uses_controlled_pre_and_post_noise() -> None:
    sweep, layout = short_layout()
    rec = recording_from_layout(layout)
    rec[: layout.sweep_start_sample] = 0.01
    noise_start = layout.sweep_end_sample
    rec[noise_start : noise_start + int(round(0.12 * layout.fs))] = 0.01

    result = align_recording_to_layout(
        rec,
        sweep,
        layout,
        AlignmentSettings(start_alignment_confidence_min=3.0, end_marker_confidence_min=2.0),
    )

    expected = 20.0 * np.log10(float(np.sqrt(np.mean(np.square(sweep)))) / 0.01)
    assert result.snr_db == pytest.approx(expected, abs=0.25)


def test_successful_alignment_returns_matching_diagnostics() -> None:
    sweep, layout = short_layout(bluetooth=True)
    delay = int(round(0.2 * layout.fs))
    result = align_recording_to_layout(
        recording_from_layout(layout, delay_samples=delay),
        sweep,
        layout,
        bluetooth_alignment_settings(),
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
    sweep, layout = short_layout(bluetooth=True)
    result = align_recording_to_layout(
        recording_from_layout(layout, delay_samples=123),
        sweep,
        layout,
        bluetooth_alignment_settings(),
    )

    summary = format_diagnostics_summary(result.diagnostics)

    assert "Measurement diagnostics:" in summary
    assert "Mode: Bluetooth" in summary
    assert "Alignment mode: marker" in summary
    assert "Selected sweep start:" in summary
    assert "Drift:" in summary
    assert "SNR:" in summary


def test_standard_diagnostics_summary_omits_marker_timing_fields() -> None:
    sweep, layout = short_layout()
    result = align_recording_to_layout(
        recording_from_layout(layout),
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
    sweep, layout = short_layout(fs=48_000, bluetooth=True)
    drift = int(round(0.1388 * layout.fs))
    result = align_recording_to_layout(
        recording_with_shifted_end_markers(layout, drift),
        sweep,
        layout,
        bluetooth_alignment_settings(),
    )

    summary = format_diagnostics_summary(result.diagnostics)

    assert "Warning reason: bluetooth_marginal_drift" in summary
    assert "Warning: Bluetooth timing drift is marginal" in summary


def test_format_diagnostics_summary_includes_sweep_fallback_reason() -> None:
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

    summary = format_diagnostics_summary(result.diagnostics)

    assert "Warning reason: bluetooth_sweep_fallback" in summary
    assert "Alignment mode: sweep_fallback" in summary
    assert "Marker failure reason: low_end_marker_confidence" in summary


def test_retry_after_bad_run_can_succeed_with_same_layout() -> None:
    sweep, layout = short_layout(bluetooth=True)
    bad_rec = np.zeros(layout.total_samples, dtype=np.float32)

    with pytest.raises(ValueError, match="Low start-alignment confidence"):
        align_recording_to_layout(bad_rec, sweep, layout, bluetooth_alignment_settings())

    delay = int(round(0.18 * layout.fs))
    good_rec = recording_from_layout(layout, delay_samples=delay)
    result = align_recording_to_layout(good_rec, sweep, layout, bluetooth_alignment_settings())

    assert result.start.selected_sweep_start == layout.sweep_start_sample + delay
    assert result.end.timing_error_ms <= 120.0
    np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)
