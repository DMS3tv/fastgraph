"""HRTF compensation outside the HRTF file's own frequency range."""

import re
from pathlib import Path

import numpy as np
import pytest

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
    band = curve.apply_to_magnitude_as_variation(freqs, np.zeros(4))

    assert band.median.tolist() == [0.0, 0.0, 0.0, 0.0]
    # The band must stay 8 dB wide outside the file, not collapse to 0 dB.
    assert (band.p90 - band.p10).tolist() == [8.0, 8.0, 8.0, 8.0]


def _former_hrtf_rows(path: Path) -> tuple[np.ndarray, tuple[np.ndarray, ...]]:
    """The regex reader ``hrtf._load_hrtf_data`` used before the shared parser."""
    rows: list[list[float]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "*")):
            continue
        try:
            values = [float(part) for part in re.split(r"[\s,]+", line) if part]
        except ValueError:
            continue
        if len(values) >= 2 and all(np.isfinite(value) for value in values):
            rows.append(values)
    width = 6 if max(len(row) for row in rows) >= 6 else 2
    data = np.asarray([row[:width] for row in rows if len(row) >= width], dtype=float)
    data = data[data[:, 0] > 0.0]
    data = data[np.argsort(data[:, 0], kind="stable")]
    return data[:, 0], tuple(data[:, index] for index in range(1, width))


_BUNDLED_HRTFS = sorted((Path(__file__).resolve().parents[1] / "HRTFs").glob("*.txt"))


@pytest.mark.parametrize("path", _BUNDLED_HRTFS, ids=lambda path: path.stem)
def test_bundled_hrtfs_load_exactly_as_the_former_reader_did(path: Path) -> None:
    freqs, columns = _former_hrtf_rows(path)
    curve = HRTFCurve(str(path))

    assert curve.is_variation == (len(columns) == 5)
    assert np.array_equal(curve.freqs, freqs)
    assert np.array_equal(curve.mags, columns[2] if curve.is_variation else columns[0])
    if curve.is_variation:
        evaluated = curve.evaluate_variation(freqs)
        for got, expected in zip(evaluated, columns, strict=True):
            assert np.array_equal(got, expected)
