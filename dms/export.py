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


def export_curve(
    freqs: np.ndarray,
    mag_db: np.ndarray,
    session: SessionData,
    output_path: Path,
    compensated: bool,
    hrtf: Optional[HRTFCurve] = None,
    hrtf_invert: bool = False,
) -> None:
    """Write REW-compatible TXT file."""
    header = session.to_rew_header()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        header,
        f"* Export Date: {now}",
        f"* Compensated: {'Yes' if compensated else 'No'}",
    ]
    if compensated and hrtf:
        lines.append(f"* HRTF File: {hrtf.name}")
        lines.append(f"* HRTF Sign: {'Inverted (+)' if hrtf_invert else 'Normal (-)'}")

    lines += [
        "* Normalization: 1 kHz reference offset only (shape preserved)",
        "* Points: log-spaced",
        "*",
        "* Frequency(Hz)\tMagnitude(dB)",
    ]

    for f, m in zip(freqs, mag_db):
        lines.append(f"{f:.4f}\t{m:.6f}")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
