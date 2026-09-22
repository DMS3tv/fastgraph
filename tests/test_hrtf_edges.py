"""HRTF compensation outside the HRTF file's own frequency range."""

from pathlib import Path

import numpy as np

from dms.hrtf import HRTFCurve


def test_mono_hrtf_holds_its_edge_values_instead_of_stepping_to_zero(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hrtf.txt"
    path.write_text("100 3\n1000 5\n", encoding="utf-8")

    curve = HRTFCurve(str(path))
    values = curve.evaluate(np.array([20.0, 100.0, 1000.0, 20000.0]))

    assert values.tolist() == [3.0, 3.0, 5.0, 5.0]


def test_applying_a_mono_hrtf_leaves_no_step_at_the_file_edge(tmp_path: Path) -> None:
    path = tmp_path / "hrtf.txt"
    path.write_text("100 3\n1000 5\n", encoding="utf-8")

    curve = HRTFCurve(str(path))
    freqs = np.array([90.0, 100.0, 1000.0, 1100.0])
    corrected = curve.apply(freqs, np.zeros(4))

    assert corrected.tolist() == [-3.0, -3.0, -5.0, -5.0]


def test_variation_hrtf_holds_every_percentile_at_the_edges(tmp_path: Path) -> None:
    path = tmp_path / "population.txt"
    path.write_text("100 1 2 3 4 5\n1000 10 20 30 40 50\n", encoding="utf-8")

    curve = HRTFCurve(str(path))
    assert curve.is_variation
    variation = curve.evaluate_variation(np.array([20.0, 100.0, 1000.0, 20000.0]))

    assert variation is not None
    assert [values.tolist() for values in variation] == [
        [1.0, 1.0, 10.0, 10.0],
        [2.0, 2.0, 20.0, 20.0],
        [3.0, 3.0, 30.0, 30.0],
        [4.0, 4.0, 40.0, 40.0],
        [5.0, 5.0, 50.0, 50.0],
    ]


def test_variation_hrtf_band_keeps_its_width_below_the_file_range(
    tmp_path: Path,
) -> None:
    path = tmp_path / "population.txt"
    path.write_text("100 -4 -2 0 2 4\n1000 -4 -2 0 2 4\n", encoding="utf-8")

    curve = HRTFCurve(str(path))
    freqs = np.array([20.0, 100.0, 1000.0, 20000.0])
    p10, _p25, median, _p75, p90 = curve.apply_to_magnitude_as_variation(freqs, np.zeros(4))

    assert median.tolist() == [0.0, 0.0, 0.0, 0.0]
    # The band must stay 8 dB wide outside the file, not collapse to 0 dB.
    assert (p90 - p10).tolist() == [8.0, 8.0, 8.0, 8.0]
