from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

import numpy as np

from dms.hrtf import HRTFCurve
from dms.session import SessionData


def build_filename(
    session: SessionData,
    compensated: bool,
    channel_label: str = "",
) -> str:
    """Build export filename per spec."""
    suffix = "COMP AVG" if compensated else "RAW AVG"
    label = " ".join(str(channel_label).strip().split())
    if label:
        suffix = f"{label} {suffix}"
    rig = session.rig.strip()

    if session.asset_tag.strip():
        tag = session.asset_tag.strip()
        return f"{tag} {rig} {suffix}.txt"
    else:
        brand = session.brand.strip()
        model = session.model.strip()
        return f"{brand} {model} {rig} {suffix}.txt"


def build_variation_filename(
    session: SessionData,
    compensated: bool,
    channel_label: str = "",
) -> str:
    """Build variation export filename per spec."""
    suffix = "COMP VAR" if compensated else "RAW VAR"
    label = " ".join(str(channel_label).strip().split())
    if label:
        suffix = f"{label} {suffix}"
    rig = session.rig.strip()

    if session.asset_tag.strip():
        tag = session.asset_tag.strip()
        return f"{tag} {rig} {suffix}.txt"
    else:
        brand = session.brand.strip()
        model = session.model.strip()
        return f"{brand} {model} {rig} {suffix}.txt"


def _level_line(level_mode: str) -> str:
    if str(level_mode) == "dbspl":
        return "* Level: dB SPL (calibrated)"
    return "* Normalization: 1 kHz reference offset only (shape preserved)"


def _write_rew_file(
    path: Path,
    header_lines: list[str],
    rows: Iterable[Sequence[float]],
) -> None:
    """Write ``header_lines`` then one tab-separated line per row.

    The frequency column keeps 4 decimals and every value column 6.
    """
    lines = list(header_lines)
    for frequency, *values in rows:
        lines.append("\t".join([f"{frequency:.4f}", *(f"{value:.6f}" for value in values)]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_curve(
    freqs: np.ndarray,
    mag_db: np.ndarray,
    session: SessionData,
    output_path: Path,
    compensated: bool,
    hrtf: HRTFCurve | None = None,
    n_sweeps: int | None = None,
    smoothing_fraction: int | None = None,
    level_mode: str = "ref_1khz",
    offset_db: float | None = None,
) -> None:
    """Write REW-compatible TXT file.

    ``smoothing_fraction`` records the fractional-octave smoothing that was
    applied to the exported curve, so the file states what was displayed.
    ``level_mode="dbspl"`` means the magnitudes are absolute calibrated SPL
    rather than a 1 kHz-referenced shape. ``offset_db`` records a vertical
    offset already applied to the magnitudes.
    """
    header = session.to_rew_header()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        header,
        f"* Export Date: {now}",
        f"* Compensated: {'Yes' if compensated else 'No'}",
    ]
    if n_sweeps is not None and n_sweeps > 0:
        lines.append(f"* Average Sweeps: {int(n_sweeps)}")
    if compensated and hrtf:
        lines.append(f"* HRTF File: {hrtf.name}")
    if smoothing_fraction is not None and smoothing_fraction > 0:
        lines.append(f"* Smoothing: 1/{int(smoothing_fraction)} octave")
    if offset_db:
        lines.append(f"* Offset: {float(offset_db):g} dB")

    lines += [
        _level_line(level_mode),
        "* Points: log-spaced",
        "*",
        "* Frequency(Hz)\tMagnitude(dB)",
    ]

    _write_rew_file(output_path, lines, zip(freqs, mag_db))


def export_variation(
    freqs: np.ndarray,
    p10_db: np.ndarray,
    p25_db: np.ndarray,
    median_db: np.ndarray,
    p75_db: np.ndarray,
    p90_db: np.ndarray,
    session: SessionData,
    output_path: Path,
    compensated: bool,
    hrtf: HRTFCurve | None = None,
    n_sweeps: int | None = None,
    smoothing_fraction: int | None = None,
    level_mode: str = "ref_1khz",
) -> None:
    """Write DMS Fastgraph variation-band TXT file."""
    header = session.to_rew_header()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        header,
        "* Export Type: Variation Band",
        f"* Export Date: {now}",
        f"* Compensated: {'Yes' if compensated else 'No'}",
    ]
    if n_sweeps is not None and n_sweeps > 0:
        lines.append(f"* Variation Sweeps: {int(n_sweeps)}")
    if compensated and hrtf:
        lines.append(f"* HRTF File: {hrtf.name}")
    if smoothing_fraction is not None and smoothing_fraction > 0:
        lines.append(f"* Smoothing: 1/{int(smoothing_fraction)} octave")

    lines += [
        "* Percentiles: p10/p25/median/p75/p90 across kept measurements",
        _level_line(level_mode),
        "* Points: log-spaced",
        "*",
        "* Frequency(Hz)\tP10(dB)\tP25(dB)\tMedian(dB)\tP75(dB)\tP90(dB)",
    ]

    _write_rew_file(output_path, lines, zip(freqs, p10_db, p25_db, median_db, p75_db, p90_db))
