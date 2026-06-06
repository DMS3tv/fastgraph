from pathlib import Path
from types import SimpleNamespace

import numpy as np

from dms.export import build_variation_filename, export_variation
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
    assert "* Frequency(Hz)\tP10(dB)\tP25(dB)\tMedian(dB)\tP75(dB)\tP90(dB)" in text

    data_lines = [line for line in text.splitlines() if line and not line.startswith("*")]
    assert data_lines == [
        "100.0000\t-3.000000\t-2.000000\t0.000000\t2.000000\t3.000000",
        "1000.0000\t-1.000000\t-0.500000\t1.000000\t2.500000\t4.000000",
    ]
    parsed = np.loadtxt(data_lines)
    assert parsed.shape == (2, 6)
