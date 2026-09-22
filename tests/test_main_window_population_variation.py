from pathlib import Path

import numpy as np

from dms.hrtf import _Z_P75, _Z_P90, HRTFCurve, sigma_from_percentiles


def _variation_hrtf(tmp_path: Path) -> HRTFCurve:
    path = tmp_path / "population.txt"
    path.write_text(
        "100 1 2 3 4 5\n1000 10 20 30 40 50\n",
        encoding="utf-8",
    )
    return HRTFCurve(str(path))


def test_population_compensation_forces_measure_view_to_variation(
    make_main_window,
    tmp_path: Path,
) -> None:
    window = make_main_window()
    window.measure.hrtf = _variation_hrtf(tmp_path)
    window.measure_tab.hrtf_toggle.setChecked(True)
    window.measure_tab.variation_toggle.setChecked(False)

    assert window.measure.bottom_view_mode() == "variation"


def test_one_measurement_gets_population_compensation_band(
    make_main_window,
    tmp_path: Path,
) -> None:
    freqs = np.array([100.0, 1000.0])
    window = make_main_window()
    window.measure.kept_curves = [(freqs, np.array([10.0, 100.0]))]
    window.measure.average = (freqs, np.array([10.0, 100.0]))

    variation = window.measure.variation_from_kept_curves(hrtf=_variation_hrtf(tmp_path))

    assert variation is not None
    p10, p25, median, p75, p90 = (
        variation.p10,
        variation.p25,
        variation.median,
        variation.p75,
        variation.p90,
    )
    # One measurement has no spread of its own, so the band is the population
    # sigma alone, centred on the compensated measurement.
    comp = np.array([[1.0, 2.0, 3.0, 4.0, 5.0], [10.0, 20.0, 30.0, 40.0, 50.0]])
    sigma = sigma_from_percentiles(comp[:, 0], comp[:, 1], comp[:, 3], comp[:, 4])
    expected_median = np.array([10.0, 100.0]) - comp[:, 2]
    assert np.allclose(median, expected_median)
    assert np.allclose(p10, expected_median - _Z_P90 * sigma)
    assert np.allclose(p25, expected_median - _Z_P75 * sigma)
    assert np.allclose(p75, expected_median + _Z_P75 * sigma)
    assert np.allclose(p90, expected_median + _Z_P90 * sigma)
