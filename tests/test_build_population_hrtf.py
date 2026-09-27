from __future__ import annotations

from pathlib import Path

import numpy as np

from dms.curator.parser import parse_measurement_txt
from dms.processing import _Z_P75, _Z_P90, F_REF, log_grid
from tools.build_population_hrtf import build, main

GRID = log_grid()
FREQS = np.geomspace(200.0, 16000.0, 60)
KEYS = ("p10", "p25", "median", "p75", "p90", "p0", "p100")


def _write_population(path: Path) -> dict[str, np.ndarray]:
    """Skewed synthetic P0/P25/P50/P75/P100 file; returns the columns on GRID."""
    x = np.log2(FREQS / 1000.0)
    median = 2.0 * np.sin(x)
    p25 = median - 1.0 - 0.3 * np.cos(x)
    p75 = median + 0.5 + 0.2 * np.cos(x) ** 2
    p0 = p25 - 4.0
    p100 = p75 + 3.0
    lines = [
        "* Columns: Frequency_Hz  Minimum_P0_dB  P25_dB  Median_P50_dB  P75_dB  Maximum_P100_dB",
        "* Population: 3 synthetic ears",
        "* IMPORTANT: visualization only",
    ]
    lines += ["\t".join(f"{v:.9f}" for v in row) for row in zip(FREQS, p0, p25, median, p75, p100)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    grid_log, f_log = np.log(GRID), np.log(FREQS)
    cols = {
        k: np.interp(grid_log, f_log, v)
        for k, v in (("p0", p0), ("p25", p25), ("median", median), ("p75", p75), ("p100", p100))
    }
    sigma = (cols["p75"] - cols["p25"]) / (2 * _Z_P75)
    cols["p10"] = cols["median"] - _Z_P90 * sigma
    cols["p90"] = cols["median"] + _Z_P90 * sigma
    return cols


def _write_canal(path: Path, flat: bool) -> np.ndarray:
    freqs = np.geomspace(20.0, 20000.0, 400)
    db = np.zeros_like(freqs) if flat else 9.0 * np.exp(-((np.log2(freqs / 2500.0) / 0.5) ** 2))
    path.write_text(
        "* Freq(Hz)\tSPL(dB)\n" + "".join(f"{f:.6f}\t{v:.6f}\n" for f, v in zip(freqs, db)),
        encoding="utf-8",
    )
    return np.interp(np.log(GRID), np.log(freqs), db)


def _rezero(cols: dict[str, np.ndarray], shift: np.ndarray | float = 0.0) -> dict:
    offset = np.interp(F_REF, GRID, cols["median"] + shift)
    return {k: v + shift - offset for k, v in cols.items()}


def test_flat_canal_zero_sigma_reproduces_input(tmp_path):
    expected = _rezero(_write_population(tmp_path / "pop.txt"))
    _write_canal(tmp_path / "canal.txt", flat=True)
    _grid, out, _header, derived = build(tmp_path / "pop.txt", tmp_path / "canal.txt", 0.0)
    assert derived
    for key in KEYS:
        np.testing.assert_allclose(out[key], expected[key], atol=1e-6, err_msg=key)


def test_zero_sigma_shifts_every_column_by_canal(tmp_path):
    cols = _write_population(tmp_path / "pop.txt")
    canal = _write_canal(tmp_path / "canal.txt", flat=False)
    _grid, out, _header, _derived = build(tmp_path / "pop.txt", tmp_path / "canal.txt", 0.0)
    expected = _rezero(cols, canal)
    for key in KEYS:
        np.testing.assert_allclose(out[key], expected[key], atol=1e-6, err_msg=key)


def test_canal_sigma_widens_band_and_keeps_order(tmp_path):
    _write_population(tmp_path / "pop.txt")
    _write_canal(tmp_path / "canal.txt", flat=False)
    _, narrow, _, _ = build(tmp_path / "pop.txt", tmp_path / "canal.txt", 0.0)
    _, wide, _, _ = build(tmp_path / "pop.txt", tmp_path / "canal.txt", 0.1)
    width0 = narrow["p90"] - narrow["p10"]
    width1 = wide["p90"] - wide["p10"]
    # Canal slope is steepest either side of the 2.5 kHz peak.
    flank = int(np.argmin(np.abs(GRID - 2500.0 * 2**0.35)))
    assert width1[flank] > width0[flank] + 0.5
    assert np.all(width1 >= width0 - 1e-9)
    for out in (narrow, wide):
        stack = np.vstack([out[k] for k in ("p0", "p10", "p25", "median", "p75", "p90", "p100")])
        assert np.all(np.diff(stack, axis=0) >= -1e-9)


def test_written_file_parses_as_band(tmp_path):
    _write_population(tmp_path / "pop.txt")
    _write_canal(tmp_path / "canal.txt", flat=False)
    out_path = tmp_path / "out.txt"
    assert (
        main(
            [
                "--population",
                str(tmp_path / "pop.txt"),
                "--canal",
                str(tmp_path / "canal.txt"),
                "--out",
                str(out_path),
                "--sweeps",
                "3",
            ]
        )
        == 0
    )
    text = out_path.read_text(encoding="utf-8")
    assert "* Variation Sweeps: 3\n" in text
    assert "* Population: 3 synthetic ears" in text
    assert "IMPORTANT" not in text
    rows = np.loadtxt(out_path, comments="*")
    assert rows.shape == (GRID.size, 8)
    band = parse_measurement_txt(out_path)
    assert band.kind == "variation"
    np.testing.assert_allclose(
        np.column_stack(
            [band.freqs, band.p10_db, band.p25_db, band.median_db, band.p75_db, band.p90_db]
        ),
        rows[:, :6],
    )
    assert abs(np.interp(F_REF, rows[:, 0], rows[:, 3])) < 1e-6


def test_bass_taper_converges_onto_the_median_and_leaves_the_rest_alone(tmp_path):
    _write_population(tmp_path / "pop.txt")
    _write_canal(tmp_path / "canal.txt", flat=False)
    grid, plain, _, _ = build(tmp_path / "pop.txt", tmp_path / "canal.txt", 0.0)
    _, tapered, _, _ = build(tmp_path / "pop.txt", tmp_path / "canal.txt", 0.0, (200.0, 500.0))
    low = grid <= 200.0
    high = grid >= 500.0
    for key in ("p0", "p10", "p25", "p75", "p90", "p100"):
        np.testing.assert_allclose(tapered[key][low], tapered["median"][low], atol=1e-9)
        np.testing.assert_allclose(tapered[key][high], plain[key][high], atol=1e-9)
    np.testing.assert_allclose(tapered["median"], plain["median"], atol=1e-9)
    mid = (grid > 200.0) & (grid < 500.0)
    width = tapered["p90"] - tapered["p10"]
    assert np.all(np.diff(width[mid]) >= -1e-9)
