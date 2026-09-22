"""How a population HRTF's spread is merged with the measurement spread."""

from pathlib import Path

import numpy as np
from helpers import population_hrtf

from dms.hrtf import _Z_P75, _Z_P90, HRTFCurve, sigma_from_percentiles
from dms.processing import VariationBand


def _mono_hrtf(tmp_path: Path) -> HRTFCurve:
    path = tmp_path / "mono.txt"
    path.write_text("20 1\n1000 2\n20000 3\n", encoding="utf-8")
    return HRTFCurve(str(path))


def _normal_band(freqs: np.ndarray, median: float, sigma: float) -> tuple[np.ndarray, ...]:
    ones = np.ones_like(freqs)
    return (
        ones * (median - _Z_P90 * sigma),
        ones * (median - _Z_P75 * sigma),
        ones * median,
        ones * (median + _Z_P75 * sigma),
        ones * (median + _Z_P90 * sigma),
    )


def test_sigma_estimate_from_normal_percentiles() -> None:
    sigma = 3.5
    p10, p25, _median, p75, p90 = _normal_band(np.zeros(4), 1.0, sigma)

    estimate = sigma_from_percentiles(p10, p25, p75, p90)

    np.testing.assert_allclose(estimate, sigma, rtol=1e-12)


def test_independent_combination_adds_variance_in_quadrature(tmp_path: Path) -> None:
    hrtf = population_hrtf(tmp_path, sigma=2.0)
    freqs = np.array([100.0, 1000.0, 10000.0])
    p10, p25, median, p75, p90 = _normal_band(freqs, 4.0, 1.5)

    out = hrtf.apply_to_variation(VariationBand(freqs, p10, p25, median, p75, p90))
    out_p10, out_p25, out_median, out_p75, out_p90 = out.p10, out.p25, out.median, out.p75, out.p90

    expected_sigma = np.sqrt(1.5**2 + 2.0**2)
    np.testing.assert_allclose(out_median, 4.0, atol=1e-9)
    np.testing.assert_allclose(out_p90 - out_median, _Z_P90 * expected_sigma, atol=1e-9)
    np.testing.assert_allclose(out_median - out_p10, _Z_P90 * expected_sigma, atol=1e-9)
    np.testing.assert_allclose(out_p75 - out_median, _Z_P75 * expected_sigma, atol=1e-9)
    np.testing.assert_allclose(out_median - out_p25, _Z_P75 * expected_sigma, atol=1e-9)
    # Quadrature is narrower than simply adding the two widths.
    assert np.all((out_p90 - out_p10) < 2.0 * _Z_P90 * (1.5 + 2.0))


def test_mono_hrtf_shifts_the_band_by_its_correction(tmp_path: Path) -> None:
    hrtf = _mono_hrtf(tmp_path)
    freqs = np.array([100.0, 1000.0, 10000.0])
    p10, p25, median, p75, p90 = _normal_band(freqs, 4.0, 1.5)

    independent = hrtf.apply_to_variation(VariationBand(freqs, p10, p25, median, p75, p90))

    correction = hrtf.evaluate(freqs)
    expected = (
        p10 - correction,
        p25 - correction,
        median - correction,
        p75 - correction,
        p90 - correction,
    )
    produced_bands = (
        independent.p10,
        independent.p25,
        independent.median,
        independent.p75,
        independent.p90,
    )
    for produced, want in zip(produced_bands, expected, strict=True):
        np.testing.assert_array_equal(produced, want)
