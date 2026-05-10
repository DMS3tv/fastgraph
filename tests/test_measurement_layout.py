import numpy as np
import pytest

from dms.measurement_layout import (
    build_coded_timing_marker,
    build_measurement_layout,
    build_output_signal,
)


def test_coded_timing_markers_are_deterministic_and_distinct() -> None:
    fs = 48_000

    marker_a_1 = build_coded_timing_marker(fs, "end_a")
    marker_a_2 = build_coded_timing_marker(fs, "end_a")
    marker_b = build_coded_timing_marker(fs, "end_b")

    np.testing.assert_array_equal(marker_a_1, marker_a_2)
    assert marker_a_1.dtype == np.float32
    assert marker_b.dtype == np.float32
    assert len(marker_a_1) == len(marker_b)
    assert not np.array_equal(marker_a_1, marker_b)

    corr = float(
        np.dot(marker_a_1, marker_b)
        / (
            np.sqrt(np.sum(np.square(marker_a_1)))
            * np.sqrt(np.sum(np.square(marker_b)))
        )
    )
    assert abs(corr) < 0.65


def test_non_bluetooth_layout_positions_are_explicit() -> None:
    fs = 48_000
    sweep = np.linspace(-1.0, 1.0, 4_800, dtype=np.float32)

    layout = build_measurement_layout(
        sweep=sweep,
        fs=fs,
        pre_silence_s=0.2,
        post_silence_s=0.5,
        bluetooth_headphone_mode=False,
    )

    assert layout.wake_primer is None
    assert layout.pre_silence_samples == int(0.2 * fs)
    assert layout.post_silence_samples == int(0.5 * fs)
    assert layout.excitation_start_sample == layout.pre_silence_samples
    assert layout.sweep_start_sample == layout.pre_silence_samples
    assert layout.sweep_end_sample == layout.sweep_start_sample + len(sweep)
    assert layout.end_marker_1_start_sample == layout.sweep_end_sample
    assert layout.end_marker_2_start_sample == layout.sweep_end_sample
    assert layout.primer_gap_samples == 0
    assert layout.start_marker_gap_samples == 0
    assert layout.end_marker_gap_samples == 0
    assert layout.end_marker_pair_gap_samples == 0
    assert len(layout.start_marker) == 0
    assert len(layout.end_marker) == 0
    assert len(layout.end_marker_2) == 0
    np.testing.assert_array_equal(layout.excitation, sweep)
    assert (
        layout.total_samples
        == layout.excitation_start_sample
        + len(layout.excitation)
        + layout.post_silence_samples
    )
    assert layout.excitation.dtype == np.float32


def test_bluetooth_layout_includes_primer_and_primer_gap() -> None:
    fs = 44_100
    sweep = np.ones(2_205, dtype=np.float32)

    layout = build_measurement_layout(
        sweep=sweep,
        fs=fs,
        pre_silence_s=0.2,
        post_silence_s=0.5,
        bluetooth_headphone_mode=True,
    )

    assert layout.wake_primer is not None
    assert layout.primer_gap_samples == int(round(0.24 * fs))
    assert (
        layout.excitation_start_sample
        == len(layout.wake_primer)
        + layout.primer_gap_samples
        + layout.pre_silence_samples
    )
    assert layout.sweep_start_sample > layout.excitation_start_sample
    assert layout.end_marker_gap_samples == int(round(0.12 * fs))
    assert layout.end_marker_pair_gap_samples == int(round(0.12 * fs))
    assert layout.end_marker_2_start_sample > layout.end_marker_1_start_sample
    assert layout.total_samples > layout.end_marker_2_start_sample


def test_output_signal_matches_mono_and_stereo_playback_shapes() -> None:
    fs = 48_000
    sweep = np.linspace(-0.5, 0.5, 1_000, dtype=np.float32)
    layout = build_measurement_layout(
        sweep=sweep,
        fs=fs,
        pre_silence_s=0.1,
        post_silence_s=0.1,
        bluetooth_headphone_mode=True,
    )

    mono = build_output_signal(layout, output_channels=1)
    stereo = build_output_signal(layout, output_channels=8)

    assert mono.shape == (layout.total_samples, 1)
    assert stereo.shape == (layout.total_samples, 2)
    assert mono.dtype == np.float32
    assert stereo.dtype == np.float32
    np.testing.assert_allclose(stereo[:, 0], stereo[:, 1])
    np.testing.assert_allclose(
        stereo[
            layout.excitation_start_sample:
            layout.excitation_start_sample + len(layout.excitation),
            0,
        ],
        layout.excitation,
    )
    assert np.max(np.abs(stereo[:len(layout.wake_primer), 0])) > 0.0


def test_standard_output_signal_contains_only_sweep_between_silence() -> None:
    fs = 48_000
    sweep = np.linspace(-0.5, 0.5, 1_000, dtype=np.float32)
    layout = build_measurement_layout(
        sweep=sweep,
        fs=fs,
        pre_silence_s=0.1,
        post_silence_s=0.1,
        bluetooth_headphone_mode=False,
    )

    out = build_output_signal(layout, output_channels=2)

    assert np.max(np.abs(out[:layout.sweep_start_sample, 0])) == 0.0
    np.testing.assert_allclose(
        out[layout.sweep_start_sample:layout.sweep_end_sample, 0],
        sweep,
    )
    assert np.max(np.abs(out[layout.sweep_end_sample:, 0])) == 0.0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"fs": 0}, "Sample rate"),
        ({"sweep": np.array([], dtype=np.float32)}, "non-empty"),
        ({"pre_silence_s": -0.1}, "non-negative"),
        ({"post_silence_s": -0.1}, "non-negative"),
        ({"sweep": np.array([0.0, np.nan], dtype=np.float32)}, "finite"),
    ],
)
def test_layout_validation_rejects_bad_inputs(kwargs: dict, message: str) -> None:
    params = {
        "sweep": np.ones(8, dtype=np.float32),
        "fs": 48_000,
        "pre_silence_s": 0.1,
        "post_silence_s": 0.1,
        "bluetooth_headphone_mode": False,
    }
    params.update(kwargs)

    with pytest.raises(ValueError, match=message):
        build_measurement_layout(**params)


def test_output_signal_rejects_non_positive_output_channels() -> None:
    layout = build_measurement_layout(
        sweep=np.ones(8, dtype=np.float32),
        fs=48_000,
        pre_silence_s=0.1,
        post_silence_s=0.1,
        bluetooth_headphone_mode=False,
    )

    with pytest.raises(ValueError, match="Output channel"):
        build_output_signal(layout, output_channels=0)
