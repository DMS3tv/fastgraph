import re
from pathlib import Path
from datetime import datetime
from typing import Optional

import numpy as np

from dms.session import SessionData
from dms.hrtf import HRTFCurve


def build_filename(
    session: SessionData,
    compensated: bool,
) -> str:
    """Build export filename per spec."""
    suffix = "COMP AVG" if compensated else "RAW AVG"
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
) -> str:
    """Build variation export filename per spec."""
    suffix = "COMP VAR" if compensated else "RAW VAR"
    rig = session.rig.strip()

    if session.asset_tag.strip():
        tag = session.asset_tag.strip()
        return f"{tag} {rig} {suffix}.txt"
    else:
        brand = session.brand.strip()
        model = session.model.strip()
        return f"{brand} {model} {rig} {suffix}.txt"


def export_curve(
    freqs: np.ndarray,
    mag_db: np.ndarray,
    session: SessionData,
    output_path: Path,
    compensated: bool,
    hrtf: Optional[HRTFCurve] = None,
    n_sweeps: Optional[int] = None,
) -> None:
    """Write REW-compatible TXT file."""
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

    lines += [
        "* Normalization: 1 kHz reference offset only (shape preserved)",
        "* Points: log-spaced",
        "*",
        "* Frequency(Hz)\tMagnitude(dB)",
    ]

    for f, m in zip(freqs, mag_db):
        lines.append(f"{f:.4f}\t{m:.6f}")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    hrtf: Optional[HRTFCurve] = None,
    n_sweeps: Optional[int] = None,
    smoothing_fraction: Optional[int] = None,
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
        "* Normalization: follows displayed bottom viewport data",
        "* Points: log-spaced",
        "*",
        "* Frequency(Hz)\tP10(dB)\tP25(dB)\tMedian(dB)\tP75(dB)\tP90(dB)",
    ]

    for f, p10, p25, median, p75, p90 in zip(
        freqs,
        p10_db,
        p25_db,
        median_db,
        p75_db,
        p90_db,
    ):
        lines.append(
            f"{f:.4f}\t{p10:.6f}\t{p25:.6f}\t{median:.6f}\t{p75:.6f}\t{p90:.6f}"
        )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
