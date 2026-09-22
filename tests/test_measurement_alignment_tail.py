"""Post-sweep tail extraction and damaged-recording handling in ``dms.measurement_alignment``."""

import numpy as np
import pytest
from helpers import (
    bluetooth_alignment_settings,
    noise_sweep,
    recording_from_layout,
    short_layout,
    write_at,
)

from dms.measurement_alignment import (
    AlignmentSettings,
    MeasurementWarningReason,
    align_recording_to_layout,
)
from dms.measurement_layout import build_measurement_layout


def test_missing_start_audio_fails_or_locks_to_remaining_valid_marker_evidence() -> None:
    sweep, layout = short_layout(bluetooth=True)
    delay = int(round(0.25 * layout.fs))
    rec = recording_from_layout(layout, delay_samples=delay)
    clip_until = delay + layout.sweep_start_sample + int(round(0.08 * layout.fs))
    rec[:clip_until] = 0.0

    result = align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())

    assert result.start.sweep_correlation_candidate == layout.sweep_start_sample + delay
    assert result.start.marker_locked_candidate is not None
    assert result.end.selected_sweep_start == layout.sweep_start_sample + delay
    assert np.max(np.abs(result.aligned_recording[: int(round(0.08 * layout.fs))])) == 0.0


def test_truncated_tail_audio_can_use_bluetooth_sweep_fallback() -> None:
    sweep, layout = short_layout(bluetooth=True)
    delay = int(round(0.2 * layout.fs))
    rec = recording_from_layout(layout, delay_samples=delay)
    trunc_at = delay + layout.sweep_end_sample + int(round(0.01 * layout.fs))
    rec = rec[:trunc_at]

    result = align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())

    assert result.diagnostics.warning_reason == (MeasurementWarningReason.BLUETOOTH_SWEEP_FALLBACK)
    np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)


def test_truncated_sweep_window_still_fails() -> None:
    sweep, layout = short_layout(bluetooth=True)
    delay = int(round(0.2 * layout.fs))
    rec = recording_from_layout(layout, delay_samples=delay)
    trunc_at = delay + layout.sweep_end_sample - int(round(0.05 * layout.fs))
    rec = rec[:trunc_at]

    with pytest.raises(
        ValueError,
        match=(
            "Low end-marker confidence|Unable to verify end marker timing|"
            "Aligned recording shorter than expected|Timing drift too large|"
            "Low start-alignment confidence"
        ),
    ):
        align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())


def test_bluetooth_profile_tail_covers_high_output_latency_recording_window() -> None:
    fs = 8_000
    sweep = noise_sweep(int(round(3.5 * fs)))
    layout = build_measurement_layout(
        sweep=sweep,
        fs=fs,
        pre_silence_s=0.6,
        post_silence_s=2.0,
        bluetooth_headphone_mode=True,
    )
    rec = np.zeros(layout.total_samples, dtype=np.float32)
    bluetooth_delay = int(round(0.9 * fs))
    write_at(rec, layout.excitation_start_sample + bluetooth_delay, layout.excitation)

    result = align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())

    assert result.end.marker_confidence >= 2.5
    assert result.end.timing_error_ms <= 5.0
    assert result.end.spacing_error_samples == 0
    np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)


def test_alignment_returns_post_sweep_tail_in_standard_mode() -> None:
    sweep, layout = short_layout()
    delay = 137
    rec = recording_from_layout(layout, delay_samples=delay)
    decay = 0.01 * np.sin(np.arange(layout.post_silence_samples, dtype=np.float32) * 0.05).astype(
        np.float32
    )
    end_idx = delay + layout.sweep_end_sample
    rec[end_idx : end_idx + layout.post_silence_samples] += decay

    result = align_recording_to_layout(
        rec,
        sweep,
        layout,
        AlignmentSettings(start_alignment_confidence_min=3.0, end_marker_confidence_min=2.0),
    )

    assert len(result.aligned_recording_tail) == layout.post_silence_samples
    np.testing.assert_allclose(result.aligned_recording_tail, decay, atol=1e-6)


def test_alignment_tail_stops_before_end_markers_in_bluetooth_mode() -> None:
    sweep, layout = short_layout(bluetooth=True)
    delay = int(round(0.18 * layout.fs))
    rec = recording_from_layout(layout, delay_samples=delay)

    result = align_recording_to_layout(rec, sweep, layout, bluetooth_alignment_settings())

    gap = layout.end_marker_gap_samples
    assert gap < layout.post_silence_samples
    assert len(result.aligned_recording_tail) == gap
    # The tail must stop at the marker gap: no end-marker energy may leak into
    # the analysis window.
    marker_energy = float(
        np.max(
            np.abs(
                rec[
                    delay + layout.end_marker_1_start_sample : delay
                    + layout.end_marker_1_start_sample
                    + len(layout.end_marker)
                ]
            )
        )
    )
    assert marker_energy > 0.0
    assert float(np.max(np.abs(result.aligned_recording_tail))) == 0.0


def test_aligned_recording_length_is_unchanged() -> None:
    for bluetooth in (False, True):
        sweep, layout = short_layout(bluetooth=bluetooth)
        delay = int(round(0.18 * layout.fs)) if bluetooth else 137
        rec = recording_from_layout(layout, delay_samples=delay)
        settings = (
            bluetooth_alignment_settings()
            if bluetooth
            else AlignmentSettings(
                start_alignment_confidence_min=3.0, end_marker_confidence_min=2.0
            )
        )

        result = align_recording_to_layout(rec, sweep, layout, settings)

        assert len(result.aligned_recording) == layout.sweep_samples
        np.testing.assert_allclose(result.aligned_recording, sweep, atol=1e-6)
