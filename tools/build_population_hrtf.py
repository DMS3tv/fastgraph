#!/usr/bin/env python3
"""
Build a 5128 eardrum population variation file from a blocked-canal population.

Usage:
    PYTHONPATH=. .venv/bin/python tools/build_population_hrtf.py \\
        --population <blocked-canal VAR.txt> --canal <canal transfer.txt> \\
        --out <output.txt> [--canal-sigma 0.06] [--sweeps 1690] [--name "..."]

The population file holds five value columns, either P10/P25/Median/P75/P90
or, when its ``Columns:`` header line names ``Minimum_P0``, the extrema order
P0/P25/Median/P75/P100. In the extrema case P10/P90 are derived from P25/P75
under a normal assumption.

The canal file (frequency, dB) moves a blocked-canal response to the 5128
eardrum. Its resonance is varied between listeners by shifting the curve along
log2 frequency by ``normal(0, canal_sigma)`` octaves; the spread of that
ensemble is added in quadrature to the population spread.

Output: eight columns P10/P25/Median/P75/P90/P0/P100 on the shared 1200-point
grid, median 0 dB at 1 kHz, edges held flat outside the population's range.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dms.curator.parser import load_two_column_txt_curve  # noqa: E402
from dms.export import _write_rew_file  # noqa: E402
from dms.processing import (  # noqa: E402
    _Z_P75,
    _Z_P90,
    F_REF,
    log_grid,
    sigma_from_percentiles,
)

CANAL_DRAWS = 4000
SEED = 20260927
# Input header keys copied into the output for provenance.
PROVENANCE_KEYS = (
    "Population",
    "Weighting",
    "Minimum and maximum",
    "Direction handling",
    "Frequency range",
    "Preprocessing",
    "Source manifests",
)


def _resample(grid: np.ndarray, freqs: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Linear in log frequency; edge values held outside the input range."""
    return np.interp(np.log(grid), np.log(freqs), values)


def read_population(path: Path) -> tuple[dict[str, np.ndarray], list[str], bool]:
    """Return ({p0,p10,p25,median,p75,p90,p100: raw column}, header lines, derived)."""
    header = [
        line.rstrip("\n")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("*")
    ]
    data = np.loadtxt(path, comments="*")
    data = data[np.argsort(data[:, 0])]
    freqs, c1, p25, median, p75, c5 = data[:, :6].T
    extrema = any("Columns:" in line and "Minimum_P0" in line for line in header)
    if extrema:
        sigma = (p75 - p25) / (2.0 * _Z_P75)
        p10, p90 = median - _Z_P90 * sigma, median + _Z_P90 * sigma
        p0, p100 = c1, c5
    else:
        # ponytail: no extrema in a P10..P90 file, so the envelope is the 10-90 band.
        p10, p90 = c1, c5
        p0, p100 = c1, c5
    cols = {
        "freqs": freqs,
        "p0": p0,
        "p10": p10,
        "p25": p25,
        "median": median,
        "p75": p75,
        "p90": p90,
        "p100": p100,
    }
    return cols, header, extrema


def canal_ensemble(
    grid: np.ndarray,
    canal_f: np.ndarray,
    canal_db: np.ndarray,
    sigma_oct: float,
    draws: int = CANAL_DRAWS,
) -> dict[str, np.ndarray]:
    """Percentiles of the canal curve under a log2-frequency resonance shift."""
    shifts = np.random.default_rng(SEED).normal(0.0, sigma_oct, draws)
    curves = _resample(grid[None, :] * 2.0 ** (-shifts[:, None]), canal_f, canal_db)
    p10, p25, median, p75, p90 = np.percentile(curves, [10, 25, 50, 75, 90], axis=0)
    return {"p10": p10, "p25": p25, "median": median, "p75": p75, "p90": p90}


def spread_weight(grid: np.ndarray, f_zero: float, f_full: float) -> np.ndarray:
    """0 at/below ``f_zero``, 1 at/above ``f_full``, raised cosine in log frequency between."""
    x = np.clip(np.log(grid / f_zero) / np.log(f_full / f_zero), 0.0, 1.0)
    return 0.5 - 0.5 * np.cos(np.pi * x)


def build(
    population: Path,
    canal: Path,
    canal_sigma: float,
    taper: tuple[float, float] | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray], list[str], bool]:
    """Return (grid, output columns, input header, P10/P90 derived).

    ``taper=(f_zero, f_full)`` shrinks every column's distance from the median
    below ``f_full`` so the band converges onto the median by ``f_zero``: the
    blocked-canal corpora are less reliable in the bass. A measured band
    compensated with the file keeps its own bass spread (spreads add in
    quadrature).
    """
    raw, header, derived = read_population(population)
    grid = log_grid()
    bc = {k: _resample(grid, raw["freqs"], v) for k, v in raw.items() if k != "freqs"}
    canal_f, canal_db = load_two_column_txt_curve(str(canal), label="Canal")
    cn = canal_ensemble(grid, canal_f, canal_db, canal_sigma)

    median = bc["median"] + cn["median"]
    sigma_bc = sigma_from_percentiles(bc["p10"], bc["p25"], bc["p75"], bc["p90"])
    sigma_cn = sigma_from_percentiles(cn["p10"], cn["p25"], cn["p75"], cn["p90"])
    sigma = np.sqrt(sigma_bc**2 + sigma_cn**2)
    # Widen each input offset from the median by sigma/sigma_bc: identical to
    # median -/+ Z*sigma for a symmetric input, but keeps the measured skew.
    ok = sigma_bc > 1e-9
    scale = np.divide(sigma, sigma_bc, out=np.zeros_like(sigma), where=ok)
    out = {"median": median}
    for key, z in (("p10", -_Z_P90), ("p25", -_Z_P75), ("p75", _Z_P75), ("p90", _Z_P90)):
        out[key] = np.where(ok, median + (bc[key] - bc["median"]) * scale, median + z * sigma)
    out["p0"] = np.minimum(bc["p0"] + cn["median"], out["p10"])
    out["p100"] = np.maximum(bc["p100"] + cn["median"], out["p90"])

    if taper is not None:
        w = spread_weight(grid, *taper)
        for key in ("p0", "p10", "p25", "p75", "p90", "p100"):
            out[key] = out["median"] + w * (out[key] - out["median"])

    offset = np.interp(F_REF, grid, out["median"])
    return grid, {k: v - offset for k, v in out.items()}, header, derived


def write(
    out_path: Path,
    grid: np.ndarray,
    cols: dict[str, np.ndarray],
    input_header: list[str],
    derived: bool,
    *,
    canal_name: str,
    canal_sigma: float,
    sweeps: int,
    name: str | None,
    taper: tuple[float, float] | None = None,
) -> None:
    lines = ["* DMS Fastgraph population variation export", "* Rig: B&K 5128"]
    if name:
        lines.append(f"* Dataset: {name}")
    lines += ["* Export Type: Variation Band", f"* Variation Sweeps: {int(sweeps)}"]
    if derived:
        lines.append(
            "* Percentiles: p0/p10/p25/median/p75/p90/p100 "
            "(P10/P90 derived from P25/P75 under a normal assumption)"
        )
    else:
        lines.append("* Percentiles: p10/p25/median/p75/p90; P0/P100 set to P10/P90")
    lines += [
        f"* Canal Transfer: {canal_name}, resonance shift sigma {canal_sigma:g} octaves, "
        f"{CANAL_DRAWS} draws",
        "* Listener Normalization: median 0 dB at 1 kHz",
        "* Frequency Extension: held flat below 200 Hz and above 16 kHz",
    ]
    if taper is not None:
        lines.append(
            f"* Bass Taper: spread converges onto the median from {taper[1]:g} Hz down to "
            f"{taper[0]:g} Hz (raised cosine in log frequency); the source corpora are "
            "less reliable below that"
        )
    lines += [
        line
        for line in input_header
        if any(line.lstrip("* ").startswith(key) for key in PROVENANCE_KEYS)
    ]
    lines += [
        f"* Generated: {date.today().isoformat()}",
        "* Frequency(Hz)\tP10(dB)\tP25(dB)\tMedian(dB)\tP75(dB)\tP90(dB)\tP0(dB)\tP100(dB)",
    ]
    order = ("p10", "p25", "median", "p75", "p90", "p0", "p100")
    _write_rew_file(out_path, lines, zip(grid, *(cols[k] for k in order)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--canal", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--canal-sigma", type=float, default=0.06, help="octaves")
    parser.add_argument("--sweeps", type=int, default=1690)
    parser.add_argument("--name", default=None, help="optional Dataset header value")
    parser.add_argument(
        "--taper-full", type=float, default=500.0, help="full spread above this Hz; 0 disables"
    )
    parser.add_argument(
        "--taper-zero", type=float, default=200.0, help="no spread at/below this Hz"
    )
    args = parser.parse_args(argv)

    taper = (args.taper_zero, args.taper_full) if args.taper_full > 0 else None
    grid, cols, header, derived = build(args.population, args.canal, args.canal_sigma, taper)
    write(
        args.out,
        grid,
        cols,
        header,
        derived,
        canal_name=args.canal.name,
        canal_sigma=args.canal_sigma,
        sweeps=args.sweeps,
        name=args.name,
        taper=taper,
    )
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
