from pathlib import Path

import numpy as np
import pytest

from dms.measurement_txt import load_two_column_txt_curve


def test_load_two_column_txt_curve_accepts_rew_style(tmp_path: Path) -> None:
    path = tmp_path / "curve.txt"
    path.write_text(
        "# header\n"
        "freq mag\n"
        "100 1.0\n"
        "200,2.0\n"
        "* comment\n"
        "50 -1.0\n"
    )
    freqs, mags = load_two_column_txt_curve(str(path), label="Measurement")
    assert np.allclose(freqs, np.array([50.0, 100.0, 200.0]))
    assert np.allclose(mags, np.array([-1.0, 1.0, 2.0]))


def test_load_two_column_txt_curve_rejects_small_or_invalid(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_text("not data\n1.0 only_one_column\n")
    with pytest.raises(ValueError, match="fewer than 2 valid data rows"):
        load_two_column_txt_curve(str(path), label="Measurement")


def test_load_two_column_txt_curve_drops_non_positive_frequencies(tmp_path: Path) -> None:
    path = tmp_path / "nonpositive.txt"
    path.write_text("0 1\n-10 2\n100 3\n")
    with pytest.raises(ValueError, match="fewer than 2 positive frequency rows"):
        load_two_column_txt_curve(str(path), label="Measurement")
