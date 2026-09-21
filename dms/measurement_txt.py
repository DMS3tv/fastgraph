from __future__ import annotations

from pathlib import Path

import numpy as np


def load_two_column_txt_curve(path: str, *, label: str = "Curve") -> tuple[np.ndarray, np.ndarray]:
    """Load the first two columns of a REW-style TXT curve.

    Reads rows through the Curator parser so every import path accepts the same
    files: any delimiter, decimal commas, byte-order marks, UTF-16, extra
    columns such as phase, and repeated frequencies.
    """
    # Imported here because dms.curator.parser imports dms.hrtf, which imports this.
    from dms.curator.parser import _average_duplicate_frequencies, _numeric_rows

    rows, _metadata, _warnings = _numeric_rows(Path(path))
    if len(rows) < 2:
        raise ValueError(f"{label} file '{path}' has fewer than 2 valid data rows.")

    data = np.asarray([row[:2] for row in rows], dtype=float)
    data = data[data[:, 0] > 0.0]
    if data.shape[0] == 0:
        raise ValueError(f"{label} file '{path}' has no positive frequency rows.")
    if data.shape[0] < 2:
        raise ValueError(f"{label} file '{path}' has fewer than 2 positive frequency rows.")

    data = data[np.argsort(data[:, 0], kind="stable")]
    data, _merged = _average_duplicate_frequencies(data)
    return data[:, 0], data[:, 1]
