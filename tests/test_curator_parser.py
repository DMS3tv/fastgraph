from pathlib import Path

import numpy as np
import pytest

from dms.curator.parser import parse_measurement_txt


def test_parse_two_column_fr_sorts_and_skips_headers(tmp_path: Path) -> None:
    path = tmp_path / "curve.txt"
    path.write_text(
        "* Brand: Example\n"
        "# comment\n"
        "1000\t2\n"
        "20, -4\n"
        "not data\n"
        "100 0\n",
        encoding="utf-8",
    )

    curve = parse_measurement_txt(path)

    assert curve.kind == "fr"
    assert curve.metadata["Brand"] == "Example"
    assert np.allclose(curve.freqs, [20.0, 100.0, 1000.0])
    assert np.allclose(curve.mag_db, [-4.0, 0.0, 2.0])


def test_parse_fastgraph_variation_six_columns(tmp_path: Path) -> None:
    path = tmp_path / "variation.txt"
    path.write_text(
        "* Export Type: Variation Band\n"
        "* Frequency(Hz)\tP10(dB)\tP25(dB)\tMedian(dB)\tP75(dB)\tP90(dB)\n"
        "1000\t-1\t0\t1\t2\t3\n"
        "100\t-5\t-4\t-3\t-2\t-1\n",
        encoding="utf-8",
    )

    curve = parse_measurement_txt(path)

    assert curve.kind == "variation"
    assert np.allclose(curve.freqs, [100.0, 1000.0])
    assert np.allclose(curve.p10_db, [-5.0, -1.0])
    assert np.allclose(curve.p25_db, [-4.0, 0.0])
    assert np.allclose(curve.median_db, [-3.0, 1.0])
    assert np.allclose(curve.p75_db, [-2.0, 2.0])
    assert np.allclose(curve.p90_db, [-1.0, 3.0])


def test_parse_rejects_too_few_rows(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_text("* header\n100 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="fewer than 2"):
        parse_measurement_txt(path)


def test_parse_rejects_missing_positive_frequencies(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_text("-100 1\n0 2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="positive frequency"):
        parse_measurement_txt(path)
