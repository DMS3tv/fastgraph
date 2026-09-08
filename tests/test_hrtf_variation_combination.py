"""How a population HRTF's spread is merged with the measurement spread."""

from pathlib import Path

import numpy as np

from dms.hrtf import _Z_P75, _Z_P90, HRTFCurve, sigma_from_percentiles


def _population_hrtf(tmp_path: Path, sigma: float = 2.0) -> HRTFCurve:
    """A population HRTF whose band is an exact normal of width ``sigma``."""
    path = tmp_path / "population.txt"
    lines = []
    for freq in (20.0, 1000.0, 20000.0):
        median = 0.0
        lines.append(
            f"{freq} {median - _Z_P90 * sigma} {median - _Z_P75 * sigma} "
            f"{median} {median + _Z_P75 * sigma} {median + _Z_P90 * sigma}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return HRTFCurve(str(path))


def _mono_hrtf(tmp_path: Path) -> HRTFCurve:
    path = tmp_path / "mono.txt"
    path.write_text("20 1\n1000 2\n20000 3\n", encoding="utf-8")
    return HRTFCurve(str(path))


def _normal_band(
    freqs: np.ndarray, median: float, sigma: float
) -> tuple[np.ndarray, ...]:
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
    hrtf = _population_hrtf(tmp_path, sigma=2.0)
    freqs = np.array([100.0, 1000.0, 10000.0])
    p10, p25, median, p75, p90 = _normal_band(freqs, 4.0, 1.5)

    out = hrtf.apply_to_variation(freqs, p10, p25, median, p75, p90)
    out_p10, out_p25, out_median, out_p75, out_p90 = out

    expected_sigma = np.sqrt(1.5**2 + 2.0**2)
    np.testing.assert_allclose(out_median, 4.0, atol=1e-9)
    np.testing.assert_allclose(out_p90 - out_median, _Z_P90 * expected_sigma, atol=1e-9)
    np.testing.assert_allclose(out_median - out_p10, _Z_P90 * expected_sigma, atol=1e-9)
    np.testing.assert_allclose(out_p75 - out_median, _Z_P75 * expected_sigma, atol=1e-9)
    np.testing.assert_allclose(out_median - out_p25, _Z_P75 * expected_sigma, atol=1e-9)
    # Quadrature is narrower than simply adding the two widths.
    assert np.all((out_p90 - out_p10) < 2.0 * _Z_P90 * (1.5 + 2.0))


def test_worst_case_flag_reproduces_legacy_output(tmp_path: Path) -> None:
    hrtf = _population_hrtf(tmp_path, sigma=2.0)
    freqs = np.array([100.0, 1000.0, 10000.0])
    p10, p25, median, p75, p90 = _normal_band(freqs, 4.0, 1.5)

    out = hrtf.apply_to_variation(
        freqs, p10, p25, median, p75, p90, combination="worst_case"
    )

    comp_p10, comp_p25, comp_median, comp_p75, comp_p90 = hrtf.evaluate_variation(freqs)
    legacy = (
        p10 - comp_p90,
        p25 - comp_p75,
        median - comp_median,
        p75 - comp_p25,
        p90 - comp_p10,
    )
    for produced, expected in zip(out, legacy):
        np.testing.assert_array_equal(produced, expected)


def test_mono_hrtf_path_is_unchanged_by_either_mode(tmp_path: Path) -> None:
    hrtf = _mono_hrtf(tmp_path)
    freqs = np.array([100.0, 1000.0, 10000.0])
    p10, p25, median, p75, p90 = _normal_band(freqs, 4.0, 1.5)

    independent = hrtf.apply_to_variation(freqs, p10, p25, median, p75, p90)
    worst_case = hrtf.apply_to_variation(
        freqs, p10, p25, median, p75, p90, combination="worst_case"
    )

    correction = hrtf.evaluate(freqs)
    expected = (p10 - correction, p25 - correction, median - correction,
                p75 - correction, p90 - correction)
    for produced, other, want in zip(independent, worst_case, expected):
        np.testing.assert_array_equal(produced, want)
        np.testing.assert_array_equal(other, want)
