from pathlib import Path

import numpy as np
import pytest

from dms.curator.export_image import aligned_bounds
from dms.curator.parser import load_preference_bounds


def test_load_preference_bounds_uses_raw_fr_files(tmp_path: Path) -> None:
    upper = tmp_path / "upper.txt"
    lower = tmp_path / "lower.txt"
    upper.write_text("100 5\n1000 6\n", encoding="utf-8")
    lower.write_text("100 -5\n1000 -6\n", encoding="utf-8")

    bounds = load_preference_bounds(upper, lower)

    assert bounds.enabled is True
    assert bounds.upper_path == upper
    assert bounds.lower_path == lower
    assert np.allclose(bounds.upper.mag_db, [5.0, 6.0])
    assert np.allclose(bounds.lower.mag_db, [-5.0, -6.0])


def test_load_preference_bounds_rejects_invalid_file(tmp_path: Path) -> None:
    upper = tmp_path / "upper.txt"
    lower = tmp_path / "lower.txt"
    upper.write_text("100 5\n1000 6\n", encoding="utf-8")
    lower.write_text("not data\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_preference_bounds(upper, lower)


def test_bounds_files_may_use_different_frequency_grids(tmp_path: Path) -> None:
    upper = tmp_path / "upper.txt"
    lower = tmp_path / "lower.txt"
    upper.write_text("20 5\n200 5\n2000 5\n20000 5\n", encoding="utf-8")
    lower.write_text("20 -5\n20000 -1\n", encoding="utf-8")

    bounds = load_preference_bounds(upper, lower)
    freqs, upper_values, lower_values = aligned_bounds(bounds.upper, bounds.lower)

    assert np.array_equal(freqs, bounds.upper.freqs)
    assert np.allclose(upper_values, [5.0, 5.0, 5.0, 5.0])
    assert lower_values.shape == freqs.shape
    assert lower_values[0] == pytest.approx(-5.0)
    assert lower_values[-1] == pytest.approx(-1.0)
    # Zipping by index would have paired 200 Hz with the lower bound's 20 kHz.
    assert lower_values[1] < -3.0


def test_bounds_files_with_a_utf8_bom_and_decimal_commas_load(tmp_path: Path) -> None:
    upper = tmp_path / "upper.txt"
    lower = tmp_path / "lower.txt"
    upper.write_text("20,0\t5,5\n20000,0\t5,5\n", encoding="utf-8-sig")
    lower.write_text("20,0\t-5,5\n20000,0\t-5,5\n", encoding="utf-8-sig")

    bounds = load_preference_bounds(upper, lower)

    assert np.allclose(bounds.upper.freqs, [20.0, 20000.0])
    assert np.allclose(bounds.upper.mag_db, [5.5, 5.5])
    assert np.allclose(bounds.lower.mag_db, [-5.5, -5.5])
