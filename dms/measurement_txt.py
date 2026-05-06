from __future__ import annotations

import re

import numpy as np


def load_two_column_txt_curve(path: str, *, label: str = "Curve") -> tuple[np.ndarray, np.ndarray]:
    """Load a permissive REW-style two-column TXT curve.

    Accepts whitespace or comma delimiters, skips comments/header-like lines,
    and requires at least two valid numeric rows.
    """
    x_vals: list[float] = []
    y_vals: list[float] = []

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("*"):
                continue
            parts = [p for p in re.split(r"[\s,]+", line) if p]
            if len(parts) < 2:
                continue
            try:
                x = float(parts[0])
                y = float(parts[1])
            except ValueError:
                continue
            if not (np.isfinite(x) and np.isfinite(y)):
                continue
            x_vals.append(x)
            y_vals.append(y)

    if len(x_vals) < 2:
        raise ValueError(f"{label} file '{path}' has fewer than 2 valid data rows.")

    x_arr = np.asarray(x_vals, dtype=float)
    y_arr = np.asarray(y_vals, dtype=float)
    positive = x_arr > 0.0
    if not np.any(positive):
        raise ValueError(f"{label} file '{path}' has no positive frequency rows.")

    x_arr = x_arr[positive]
    y_arr = y_arr[positive]
    if x_arr.size < 2:
        raise ValueError(f"{label} file '{path}' has fewer than 2 positive frequency rows.")

    order = np.argsort(x_arr, kind="stable")
    x_arr = x_arr[order]
    y_arr = y_arr[order]
    return x_arr, y_arr
