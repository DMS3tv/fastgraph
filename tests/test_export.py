from pathlib import Path
from types import SimpleNamespace

import numpy as np

from dms.export import (
    build_filename,
    build_variation_filename,
    export_curve,
    export_variation,
)
from dms.session import SessionData


def test_build_variation_filename_uses_brand_model_and_raw_suffix() -> None:
    session = SessionData(
        rig="GRAS",
        brand="DMS",
        model="Example",
    )

    assert build_variation_filename(session, compensated=False) == "DMS Example GRAS RAW VAR.txt"


def test_build_variation_filename_uses_asset_tag_and_comp_suffix() -> None:
    session = SessionData(
        rig="KB501X",
        brand="DMS",
        model="Example",
        asset_tag="Unit 7",
    )

    assert build_variation_filename(session, compensated=True) == "Unit 7 KB501X COMP VAR.txt"


def test_two_channel_filename_labels_are_explicit() -> None:
    session = SessionData(rig="GRAS", brand="DMS", model="Example")

    assert (
        build_filename(session, compensated=False, channel_label="L")
        == "DMS Example GRAS L RAW AVG.txt"
    )
    assert (
        build_filename(session, compensated=True, channel_label="BOTH")
        == "DMS Example GRAS BOTH COMP AVG.txt"
    )
    assert (
        build_variation_filename(session, compensated=False, channel_label="R")
        == "DMS Example GRAS R RAW VAR.txt"
    )


def test_export_variation_writes_metadata_and_six_columns(tmp_path: Path) -> None:
    session = SessionData(
        rig="GRAS",
        brand="DMS",
        model="Example",
        channel_side="L",
    )
    output = tmp_path / "variation.txt"

    export_variation(
        freqs=np.array([100.0, 1000.0]),
        p10_db=np.array([-3.0, -1.0]),
        p25_db=np.array([-2.0, -0.5]),
        median_db=np.array([0.0, 1.0]),
        p75_db=np.array([2.0, 2.5]),
        p90_db=np.array([3.0, 4.0]),
        session=session,
        output_path=output,
        compensated=True,
        hrtf=SimpleNamespace(name="fixture_hrtf.txt"),
        n_sweeps=5,
        smoothing_fraction=48,
    )

    text = output.read_text(encoding="utf-8")
    assert "* Export Type: Variation Band" in text
    assert "* Compensated: Yes" in text
    assert "* Variation Sweeps: 5" in text
    assert "* HRTF File: fixture_hrtf.txt" in text
    assert "* Smoothing: 1/48 octave" in text
    assert "* Normalization: 1 kHz reference offset only (shape preserved)" in text
    assert "* Frequency(Hz)\tP10(dB)\tP25(dB)\tMedian(dB)\tP75(dB)\tP90(dB)" in text

    data_lines = [line for line in text.splitlines() if line and not line.startswith("*")]
    assert data_lines == [
        "100.0000\t-3.000000\t-2.000000\t0.000000\t2.000000\t3.000000",
        "1000.0000\t-1.000000\t-0.500000\t1.000000\t2.500000\t4.000000",
    ]
    parsed = np.loadtxt(data_lines)
    assert parsed.shape == (2, 6)


def test_export_curve_writes_smoothing_header(tmp_path: Path) -> None:
    session = SessionData(rig="GRAS", brand="DMS", model="Example")
    output = tmp_path / "average.txt"

    export_curve(
        freqs=np.array([100.0, 1000.0]),
        mag_db=np.array([-1.0, 0.0]),
        session=session,
        output_path=output,
        compensated=False,
        n_sweeps=5,
        smoothing_fraction=48,
    )

    text = output.read_text(encoding="utf-8")
    assert "* Smoothing: 1/48 octave" in text
    # Without a level mode the historical normalization line is unchanged.
    assert "* Normalization: 1 kHz reference offset only (shape preserved)" in text
    assert "* Level: dB SPL (calibrated)" not in text


def test_export_curve_writes_spl_level_header(tmp_path: Path) -> None:
    session = SessionData(rig="GRAS", brand="DMS", model="Example")
    output = tmp_path / "spl.txt"

    export_curve(
        freqs=np.array([100.0, 1000.0]),
        mag_db=np.array([84.0, 86.0]),
        session=session,
        output_path=output,
        compensated=False,
        level_mode="dbspl",
    )

    text = output.read_text(encoding="utf-8")
    assert "* Level: dB SPL (calibrated)" in text
    assert "* Normalization: 1 kHz" not in text
    assert "* Smoothing:" not in text


def test_export_curve_writes_offset_header_only_when_nonzero(tmp_path: Path) -> None:
    session = SessionData(rig="GRAS", brand="DMS", model="Example")
    texts = []
    for offset in (2.5, 0.0, None):
        output = tmp_path / f"offset_{offset}.txt"
        export_curve(
            freqs=np.array([100.0, 1000.0]),
            mag_db=np.array([-1.0, 0.0]),
            session=session,
            output_path=output,
            compensated=False,
            offset_db=offset,
        )
        texts.append(output.read_text(encoding="utf-8"))

    assert "* Offset: 2.5 dB" in texts[0]
    assert "* Offset:" not in texts[1]
    assert "* Offset:" not in texts[2]


def test_export_variation_writes_spl_level_header(tmp_path: Path) -> None:
    session = SessionData(rig="GRAS", brand="DMS", model="Example")
    output = tmp_path / "variation_spl.txt"
    band = np.array([84.0, 86.0])

    export_variation(
        freqs=np.array([100.0, 1000.0]),
        p10_db=band,
        p25_db=band,
        median_db=band,
        p75_db=band,
        p90_db=band,
        session=session,
        output_path=output,
        compensated=False,
        level_mode="dbspl",
    )

    text = output.read_text(encoding="utf-8")
    assert "* Level: dB SPL (calibrated)" in text
    assert "* Normalization:" not in text


_GOLDEN_SESSION_HEADER = (
    "* DMS Fastgraph measurement\n* Rig: GRAS\n* Brand: DMS\n* Model: Example\n"
    "* Asset Tag: Unit 7\n* Firmware: 1.0\n* EQ Applied: No\n* ANC/Transparency: Off\n"
    "* Form Factor: over-ear\n* Acoustic Type: Open Back\n* Connection: wired analog\n"
)


def test_exports_match_the_former_writers_byte_for_byte(tmp_path: Path, monkeypatch) -> None:
    import dms.export as export_module

    class _Now:
        @staticmethod
        def now():
            return SimpleNamespace(strftime=lambda _fmt: "2026-01-02 03:04:05")

    monkeypatch.setattr(export_module, "datetime", _Now)
    freqs = np.array([20.0, 1000.0, 19999.87654321])
    mag = np.array([-3.14159265, 0.0, 2.718281828])
    session = SessionData(
        rig="GRAS", brand="DMS", model="Example", asset_tag="Unit 7", firmware="1.0"
    )
    export_curve(
        freqs,
        mag,
        session,
        tmp_path / "curve.txt",
        True,
        hrtf=SimpleNamespace(name="Fixture"),
        n_sweeps=5,
        smoothing_fraction=12,
        level_mode="dbspl",
        offset_db=-1.5,
    )
    export_variation(
        freqs,
        mag - 2,
        mag - 1,
        mag,
        mag + 1,
        mag + 2,
        session,
        tmp_path / "var.txt",
        False,
        n_sweeps=4,
        smoothing_fraction=48,
    )

    assert (tmp_path / "curve.txt").read_bytes().decode("utf-8") == (
        _GOLDEN_SESSION_HEADER + "* Export Date: 2026-01-02 03:04:05\n* Compensated: Yes\n"
        "* Average Sweeps: 5\n* HRTF File: Fixture\n* Smoothing: 1/12 octave\n"
        "* Offset: -1.5 dB\n* Level: dB SPL (calibrated)\n* Points: log-spaced\n*\n"
        "* Frequency(Hz)\tMagnitude(dB)\n"
        "20.0000\t-3.141593\n1000.0000\t0.000000\n19999.8765\t2.718282\n"
    )
    assert (tmp_path / "var.txt").read_bytes().decode("utf-8") == (
        _GOLDEN_SESSION_HEADER + "* Export Type: Variation Band\n"
        "* Export Date: 2026-01-02 03:04:05\n* Compensated: No\n* Variation Sweeps: 4\n"
        "* Smoothing: 1/48 octave\n"
        "* Percentiles: p10/p25/median/p75/p90 across kept measurements\n"
        "* Normalization: 1 kHz reference offset only (shape preserved)\n"
        "* Points: log-spaced\n*\n"
        "* Frequency(Hz)\tP10(dB)\tP25(dB)\tMedian(dB)\tP75(dB)\tP90(dB)\n"
        "20.0000\t-5.141593\t-4.141593\t-3.141593\t-2.141593\t-1.141593\n"
        "1000.0000\t-2.000000\t-1.000000\t0.000000\t1.000000\t2.000000\n"
        "19999.8765\t0.718282\t1.718282\t2.718282\t3.718282\t4.718282\n"
    )
