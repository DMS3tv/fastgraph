from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from dms.curator.models import CurveData
from dms.hrtf import HRTFCurve


def parse_measurement_txt(path: str | Path) -> CurveData:
    file_path = Path(path)
    rows, metadata = _numeric_rows(file_path)
    if len(rows) < 2:
        raise ValueError(f"{file_path.name} has fewer than 2 valid data rows.")

    width = max(len(row) for row in rows)
    if width >= 6:
        data = np.asarray([row[:6] for row in rows if len(row) >= 6], dtype=float)
        if data.shape[0] < 2:
            raise ValueError(f"{file_path.name} has fewer than 2 complete variation rows.")
        data = _positive_sorted(data, file_path)
        return CurveData(
            kind="variation",
            freqs=data[:, 0],
            p10_db=data[:, 1],
            p25_db=data[:, 2],
            median_db=data[:, 3],
            p75_db=data[:, 4],
            p90_db=data[:, 5],
            metadata=metadata,
        )

    data = np.asarray([row[:2] for row in rows if len(row) >= 2], dtype=float)
    if data.shape[0] < 2:
        raise ValueError(f"{file_path.name} has fewer than 2 complete FR rows.")
    data = _positive_sorted(data, file_path)
    return CurveData(
        kind="fr",
        freqs=data[:, 0],
        mag_db=data[:, 1],
        metadata=metadata,
    )


def parse_fr_txt(path: str | Path, *, label: str = "FR") -> CurveData:
    curve = parse_measurement_txt(path)
    if curve.kind != "fr":
        raise ValueError(f"{label} file '{Path(path).name}' must be a two-column FR file.")
    return curve


def load_hrtf_txt(path: str | Path) -> HRTFCurve:
    return HRTFCurve(str(path))


def _numeric_rows(path: Path) -> tuple[list[list[float]], dict[str, str]]:
    rows: list[list[float]] = []
    metadata: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("*") or line.startswith("#"):
                _capture_metadata(line, metadata)
                continue
            parts = [part for part in re.split(r"[\s,]+", line) if part]
            values: list[float] = []
            for part in parts:
                try:
                    value = float(part)
                except ValueError:
                    values = []
                    break
                if not np.isfinite(value):
                    values = []
                    break
                values.append(value)
            if len(values) >= 2:
                rows.append(values)
    return rows, metadata


def _capture_metadata(line: str, metadata: dict[str, str]) -> None:
    clean = line.lstrip("*#").strip()
    if ":" not in clean:
        return
    key, value = clean.split(":", 1)
    key = key.strip()
    value = value.strip()
    if key and value:
        metadata[key] = value


def _positive_sorted(data: np.ndarray, path: Path) -> np.ndarray:
    data = data[data[:, 0] > 0.0]
    if data.shape[0] < 2:
        raise ValueError(f"{path.name} has fewer than 2 positive frequency rows.")
    order = np.argsort(data[:, 0], kind="stable")
    return data[order]
