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


def _extrema_hrtf(monkeypatch, sigma: float = 2.0, median: float = 1.0) -> HRTFCurve:
    """A population HRTF with P0/P100 at median -/+ 6 dB."""
    freqs = np.array([20.0, 1000.0, 20000.0])
    p10, p25, mid, p75, p90 = _normal_band(freqs, median, sigma)
    columns = (p10, p25, mid, p75, p90, mid - 6.0, mid + 6.0)
    monkeypatch.setattr("dms.hrtf._load_hrtf_data", lambda _path: (freqs, columns))
    return HRTFCurve("population.txt")


def test_extrema_shift_by_the_median_offset_and_clamp(monkeypatch) -> None:
    hrtf = _extrema_hrtf(monkeypatch)
    freqs = np.array([100.0, 1000.0, 10000.0])
    p10, p25, median, p75, p90 = _normal_band(freqs, 4.0, 1.5)
    # Wide at the first point, inside the combined P10-P90 at the others.
    p0 = np.array([-10.0, 3.0, 3.0])
    p100 = np.array([20.0, 5.0, 5.0])

    out = hrtf.apply_to_variation(VariationBand(freqs, p10, p25, median, p75, p90, p0, p100))

    shifted_p0 = p0 - 1.0
    shifted_p100 = p100 - 1.0
    np.testing.assert_allclose(out.p0, np.minimum(shifted_p0, out.p10))
    np.testing.assert_allclose(out.p100, np.maximum(shifted_p100, out.p90))
    assert out.p0[0] == shifted_p0[0] and out.p100[0] == shifted_p100[0]
    np.testing.assert_allclose(out.p0[1:], out.p10[1:])
    np.testing.assert_allclose(out.p100[1:], out.p90[1:])
    # The percentiles are the same with or without the extrema.
    without = hrtf.apply_to_variation(VariationBand(freqs, p10, p25, median, p75, p90))
    for name in ("p10", "p25", "median", "p75", "p90"):
        np.testing.assert_array_equal(getattr(out, name), getattr(without, name))
    assert without.p0 is None and without.p100 is None


def test_six_column_hrtf_leaves_band_extrema_empty(tmp_path: Path) -> None:
    hrtf = population_hrtf(tmp_path)
    freqs = np.array([100.0, 1000.0])
    assert hrtf.evaluate_extrema(freqs) is None
    band = hrtf.apply_to_magnitude_as_variation(freqs, np.zeros(2))
    assert band.p0 is None and band.p100 is None


def test_mono_hrtf_shifts_band_extrema(tmp_path: Path) -> None:
    hrtf = _mono_hrtf(tmp_path)
    freqs = np.array([100.0, 1000.0, 10000.0])
    p10, p25, median, p75, p90 = _normal_band(freqs, 4.0, 1.5)
    out = hrtf.apply_to_variation(
        VariationBand(freqs, p10, p25, median, p75, p90, p10 - 3.0, p90 + 3.0)
    )
    correction = hrtf.evaluate(freqs)
    np.testing.assert_array_equal(out.p0, p10 - 3.0 - correction)
    np.testing.assert_array_equal(out.p100, p90 + 3.0 - correction)


def test_magnitude_with_extrema_hrtf_mirrors_the_extrema(monkeypatch) -> None:
    hrtf = _extrema_hrtf(monkeypatch)
    freqs = np.array([100.0, 1000.0])
    mag = np.array([2.0, 3.0])
    band = hrtf.apply_to_magnitude_as_variation(freqs, mag)
    hrtf_p0, hrtf_p100 = hrtf.evaluate_extrema(freqs)
    np.testing.assert_allclose(band.p0, mag - hrtf_p100)
    np.testing.assert_allclose(band.p100, mag - hrtf_p0)
    assert np.all(band.p0 <= band.p10) and np.all(band.p100 >= band.p90)


def test_eight_column_population_file_loads_its_extrema(tmp_path: Path) -> None:
    path = tmp_path / "population8.txt"
    path.write_text("20 -2 -1 0 1 2 -5 5\n1000 -3 -1 0 1 3 -6 6\n", encoding="utf-8")
    hrtf = HRTFCurve(str(path))
    assert hrtf.is_variation
    p0, p100 = hrtf.evaluate_extrema(np.array([20.0, 1000.0]))
    np.testing.assert_array_equal(p0, [-5.0, -6.0])
    np.testing.assert_array_equal(p100, [5.0, 6.0])
    np.testing.assert_array_equal(hrtf.mags, [0.0, 0.0])
