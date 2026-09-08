"""Fractional-octave smoothing on log and non-log frequency grids."""

import numpy as np

from dms.processing import smooth_fractional_octave


def _legacy_smooth(freqs: np.ndarray, mag_db: np.ndarray, fraction: int) -> np.ndarray:
    """The smoothing kernel exactly as it was before non-log grids were handled."""
    log_freqs = np.log2(freqs)
    step = float(np.median(np.diff(log_freqs)))
    fwhm_oct = 1.0 / float(fraction)
    sigma_oct = fwhm_oct / (2.0 * np.sqrt(2.0 * np.log(2.0)))
    sigma_idx = sigma_oct / step
    radius = max(2, int(np.ceil(sigma_idx * 4.0)))
    offsets = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-0.5 * (offsets / sigma_idx) ** 2)
    kernel /= np.sum(kernel)
    padded = np.pad(mag_db.astype(np.float64), (radius, radius), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _log_grid_signal() -> tuple[np.ndarray, np.ndarray]:
    freqs = np.logspace(np.log10(20.0), np.log10(20000.0), 600)
    values = np.random.default_rng(7).normal(size=600) * 3.0
    return freqs, values


def test_log_spaced_input_is_byte_identical_to_the_original_kernel() -> None:
    freqs, values = _log_grid_signal()

    for fraction in (3, 12, 48):
        _, smoothed = smooth_fractional_octave(freqs, values, fraction=fraction)
        assert np.array_equal(smoothed, _legacy_smooth(freqs, values, fraction))


def test_log_spaced_regression_values_are_unchanged() -> None:
    """Captured from the implementation before the non-log path was added."""
    freqs, values = _log_grid_signal()

    _, smoothed = smooth_fractional_octave(freqs, values, fraction=12)

    assert smoothed[:3].tolist() == [
        -0.20694204097495492,
        -0.46566632738355096,
        -0.7754552495519318,
    ]
    assert float(np.sum(smoothed)) == -242.21730712461624


def test_the_600_point_export_grid_is_treated_as_log_spaced() -> None:
    freqs = np.logspace(np.log10(20.0), np.log10(20000.0), 600)
    freqs[int(np.argmin(np.abs(freqs - 1000.0)))] = 1000.0
    values = np.zeros(600)
    values[300] = 10.0

    _, smoothed = smooth_fractional_octave(freqs, values, fraction=48)

    assert np.array_equal(smoothed, _legacy_smooth(freqs, values, 48))


def test_a_linear_grid_no_longer_smooths_across_octaves_at_low_frequencies() -> None:
    """A 1 Hz-bin file has ~3.7 octaves per kernel radius at 20 Hz if untreated."""
    freqs = np.arange(20.0, 20000.0, 1.0)
    values = np.zeros_like(freqs)
    values[(freqs > 995.0) & (freqs < 1005.0)] = 12.0

    _, smoothed = smooth_fractional_octave(freqs, values, fraction=48)

    assert float(np.max(smoothed[freqs < 200.0])) == 0.0
    assert float(np.max(smoothed[freqs > 3000.0])) == 0.0
    peak_freq = float(freqs[int(np.argmax(smoothed))])
    assert 950.0 < peak_freq < 1050.0
    assert float(np.max(smoothed)) > 3.0


def test_a_linear_grid_keeps_a_low_frequency_feature_local() -> None:
    freqs = np.arange(20.0, 2000.0, 1.0)
    values = np.zeros_like(freqs)
    values[(freqs > 49.0) & (freqs < 51.0)] = 10.0

    _, smoothed = smooth_fractional_octave(freqs, values, fraction=6)

    # 1/6 octave around 50 Hz reaches roughly 47-53 Hz, not down to 20 Hz.
    assert float(smoothed[0]) < 0.05
    assert float(np.max(smoothed)) > 0.5
    assert 45.0 < float(freqs[int(np.argmax(smoothed))]) < 55.0


def test_smoothing_returns_the_caller_frequencies_unchanged() -> None:
    freqs = np.arange(20.0, 1000.0, 1.0)
    values = np.sin(freqs / 50.0)

    out_freqs, smoothed = smooth_fractional_octave(freqs, values, fraction=24)

    assert np.array_equal(out_freqs, freqs)
    assert smoothed.shape == values.shape
    assert np.all(np.isfinite(smoothed))


def test_short_or_invalid_inputs_are_returned_untouched() -> None:
    freqs = np.array([100.0, 200.0])
    values = np.array([1.0, 2.0])

    assert smooth_fractional_octave(freqs, values, fraction=48)[1] is values
    assert smooth_fractional_octave(
        np.array([100.0, 200.0, 300.0]), np.array([1.0, 2.0, 3.0]), fraction=0
    )[1].tolist() == [1.0, 2.0, 3.0]
