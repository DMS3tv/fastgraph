from pathlib import Path

import numpy as np
import pytest

from dms.curator.parser import load_two_column_txt_curve, parse_measurement_txt


def test_parse_two_column_fr_sorts_and_skips_headers(tmp_path: Path) -> None:
    path = tmp_path / "curve.txt"
    path.write_text(
        "* Brand: Example\n# comment\n1000\t2\n20, -4\nnot data\n100 0\n",
        encoding="utf-8",
    )

    curve = parse_measurement_txt(path)

    assert curve.kind == "fr"
    assert curve.metadata["Brand"] == "Example"
    assert curve.metadata["brand"] == "Example"
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


def test_parse_normalizes_fastgraph_rew_metadata(tmp_path: Path) -> None:
    path = tmp_path / "curve.txt"
    path.write_text(
        "* Brand: Sony\n"
        "* Model: WH-1000XM5\n"
        "* Rig: B&K 5128\n"
        "* Asset Tag: HP-104\n"
        "* EQ Applied: No\n"
        "* ANC/Transparency: ANC\n"
        "* Connection: Bluetooth\n"
        "100 1\n"
        "1000 0\n",
        encoding="utf-8",
    )

    metadata = parse_measurement_txt(path).metadata

    assert metadata["brand"] == "Sony"
    assert metadata["model"] == "WH-1000XM5"
    assert metadata["rig"] == "B&K 5128"
    assert metadata["asset_tag"] == "HP-104"
    assert metadata["eq_applied"] is False
    assert metadata["anc_mode"] is True
    assert metadata["connection"] == "Bluetooth"


def test_parse_reads_a_utf8_bom_file_without_dropping_the_first_row(tmp_path: Path) -> None:
    path = tmp_path / "bom.txt"
    path.write_text("20\t-4\n100\t0\n1000\t2\n", encoding="utf-8-sig")

    curve = parse_measurement_txt(path)

    assert np.allclose(curve.freqs, [20.0, 100.0, 1000.0])
    assert np.allclose(curve.mag_db, [-4.0, 0.0, 2.0])


def test_parse_reads_utf16_files_with_either_byte_order_mark(tmp_path: Path) -> None:
    text = "* Brand: Example\n20\t-4\n100\t0\n1000\t2\n"
    for name, encoding in (("le.txt", "utf-16-le"), ("be.txt", "utf-16-be")):
        path = tmp_path / name
        path.write_bytes("\ufeff".encode(encoding) + text.encode(encoding))

        curve = parse_measurement_txt(path)

        assert curve.metadata["Brand"] == "Example"
        assert np.allclose(curve.freqs, [20.0, 100.0, 1000.0])
        assert np.allclose(curve.mag_db, [-4.0, 0.0, 2.0])


def test_parse_treats_a_comma_as_a_decimal_separator_when_every_token_is_one(
    tmp_path: Path,
) -> None:
    path = tmp_path / "comma.txt"
    path.write_text("20,0\t-4,5\n100,0\t0,0\n1000,0\t2,25\n", encoding="utf-8")

    curve = parse_measurement_txt(path)

    assert np.allclose(curve.freqs, [20.0, 100.0, 1000.0])
    assert np.allclose(curve.mag_db, [-4.5, 0.0, 2.25])


def test_parse_still_treats_a_comma_as_a_delimiter_when_it_is_one(
    tmp_path: Path,
) -> None:
    path = tmp_path / "delimited.txt"
    path.write_text("20, -4\n100, 0\n1000, 2\n", encoding="utf-8")

    curve = parse_measurement_txt(path)

    assert np.allclose(curve.freqs, [20.0, 100.0, 1000.0])
    assert np.allclose(curve.mag_db, [-4.0, 0.0, 2.0])


def test_parse_reads_semicolon_delimited_decimal_comma_files(tmp_path: Path) -> None:
    path = tmp_path / "semi.txt"
    path.write_text("20,0;-4,5\n100,0;0,0\n1000,0;2,5\n", encoding="utf-8")

    curve = parse_measurement_txt(path)

    assert np.allclose(curve.freqs, [20.0, 100.0, 1000.0])
    assert np.allclose(curve.mag_db, [-4.5, 0.0, 2.5])


def test_one_stray_wide_row_no_longer_rejects_a_two_column_file(tmp_path: Path) -> None:
    path = tmp_path / "stray.txt"
    path.write_text(
        "20 -4\n100 0\n200 1 2 3 4 5\n1000 2\n",
        encoding="utf-8",
    )

    curve = parse_measurement_txt(path)

    assert curve.kind == "fr"
    assert np.allclose(curve.freqs, [20.0, 100.0, 1000.0])
    assert any("unexpected column count" in warning for warning in curve.warnings)


def test_one_stray_narrow_row_no_longer_rejects_a_variation_file(tmp_path: Path) -> None:
    path = tmp_path / "stray_var.txt"
    path.write_text(
        "20 -5 -4 -3 -2 -1\n100 -4 -3 -2 -1 0\n150 9\n1000 -1 0 1 2 3\n",
        encoding="utf-8",
    )

    curve = parse_measurement_txt(path)

    assert curve.kind == "variation"
    assert np.allclose(curve.freqs, [20.0, 100.0, 1000.0])
    assert any("unexpected column count" in warning for warning in curve.warnings)


def test_a_tied_row_count_follows_the_first_numeric_row(tmp_path: Path) -> None:
    path = tmp_path / "tie.txt"
    path.write_text(
        "20 -5 -4 -3 -2 -1\n100 -4 -3 -2 -1 0\n150 9\n200 8\n",
        encoding="utf-8",
    )

    assert parse_measurement_txt(path).kind == "variation"


def test_repeated_frequencies_are_averaged(tmp_path: Path) -> None:
    path = tmp_path / "dupes.txt"
    path.write_text("100 2\n100 4\n1000 1\n2000 3\n", encoding="utf-8")

    curve = parse_measurement_txt(path)

    assert np.allclose(curve.freqs, [100.0, 1000.0, 2000.0])
    assert np.allclose(curve.mag_db, [3.0, 1.0, 3.0])
    assert any("repeated frequency" in warning for warning in curve.warnings)


def test_repeated_frequencies_are_averaged_in_every_variation_column(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dupes_var.txt"
    path.write_text(
        "100 0 1 2 3 4\n100 2 3 4 5 6\n1000 1 2 3 4 5\n",
        encoding="utf-8",
    )

    curve = parse_measurement_txt(path)

    assert np.allclose(curve.freqs, [100.0, 1000.0])
    assert np.allclose(curve.p10_db, [1.0, 1.0])
    assert np.allclose(curve.p90_db, [5.0, 5.0])


def test_parse_reads_the_variation_sweeps_header(tmp_path: Path) -> None:
    path = tmp_path / "sweeps.txt"
    path.write_text(
        "* Variation Sweeps: 12\n100 0 1 2 3 4\n1000 1 2 3 4 5\n",
        encoding="utf-8",
    )

    metadata = parse_measurement_txt(path).metadata

    assert metadata["Variation Sweeps"] == "12"
    assert metadata["variation_sweeps"] == "12"


def test_a_two_column_csv_still_parses_when_a_row_looks_like_a_decimal_comma(
    tmp_path: Path,
) -> None:
    path = tmp_path / "csv.txt"
    path.write_text("20,-4\n100,0\n1000,2\n", encoding="utf-8")

    curve = parse_measurement_txt(path)

    assert np.allclose(curve.freqs, [20.0, 100.0, 1000.0])
    assert np.allclose(curve.mag_db, [-4.0, 0.0, 2.0])


def test_rew_decimal_comma_with_comma_delimiter_and_phase(tmp_path) -> None:
    path = tmp_path / "rew.txt"
    path.write_text("* Freq(Hz), SPL(dB), Phase(degrees)\n20,5, 103,4, -12,0\n1000,0, 94,0, 3,5\n")
    curve = parse_measurement_txt(path)
    assert curve.kind == "fr"
    assert curve.freqs.tolist() == [20.5, 1000.0]
    assert curve.mag_db.tolist() == [103.4, 94.0]


def test_wide_file_that_is_not_percentiles_imports_as_response(tmp_path) -> None:
    path = tmp_path / "rew_distortion.txt"
    rows = [f"{f}, 94.0, -40.0, -45.0, -60.0, -62.0, -70.0" for f in (100, 1000, 10000)]
    path.write_text("* Freq(Hz), Fundamental, THD, H2, H3, H4, H5\n" + "\n".join(rows) + "\n")
    curve = parse_measurement_txt(path)
    assert curve.kind == "fr"
    assert curve.mag_db.tolist() == [94.0, 94.0, 94.0]
    assert any("not a variation band" in warning for warning in curve.warnings)


def test_two_column_loader_shares_the_curator_parser(tmp_path) -> None:
    path = tmp_path / "rew_locale.txt"
    path.write_bytes("20,5; 103,4; -12,0\n1000,0; 94,0; 3,5\n".encode("utf-16"))
    freqs, mags = load_two_column_txt_curve(str(path))
    assert freqs.tolist() == [20.5, 1000.0]
    assert mags.tolist() == [103.4, 94.0]


def test_load_two_column_txt_curve_accepts_rew_style(tmp_path: Path) -> None:
    path = tmp_path / "curve.txt"
    path.write_text("# header\nfreq mag\n100 1.0\n200,2.0\n* comment\n50 -1.0\n")
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
