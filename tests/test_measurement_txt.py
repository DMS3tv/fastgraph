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


def test_load_two_column_txt_curve_handles_decimal_comma_whitespace_rows(tmp_path: Path) -> None:
    path = tmp_path / "decimal_comma.txt"
    path.write_text("1000,00\t-3,25\n2000,50\t-4,75\n")
    freqs, mags = load_two_column_txt_curve(str(path), label="Measurement")
    assert np.allclose(freqs, np.array([1000.0, 2000.5]))
    assert np.allclose(mags, np.array([-3.25, -4.75]))


def test_load_two_column_txt_curve_accepts_plain_comma_delimited_rows(tmp_path: Path) -> None:
    path = tmp_path / "comma.txt"
    path.write_text("1000,-3.25\n2000,-4.5\n")
    freqs, mags = load_two_column_txt_curve(str(path), label="Measurement")
    assert np.allclose(freqs, np.array([1000.0, 2000.0]))
    assert np.allclose(mags, np.array([-3.25, -4.5]))


def test_load_two_column_txt_curve_raises_on_wrong_column_count_with_line_number(
    tmp_path: Path,
) -> None:
    path = tmp_path / "junk.txt"
    path.write_text("100 1.0\n1,2,3\n200 2.0\n")
    with pytest.raises(ValueError, match="line 2"):
        load_two_column_txt_curve(str(path), label="Measurement")


def test_load_two_column_txt_curve_preserves_three_column_whitespace_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "three_col.txt"
    path.write_text("100 1.0 45.0\n200 2.0 -10.0\n50 -1.0 0.0\n")
    freqs, mags = load_two_column_txt_curve(str(path), label="Measurement")
    assert np.allclose(freqs, np.array([50.0, 100.0, 200.0]))
    assert np.allclose(mags, np.array([-1.0, 1.0, 2.0]))
