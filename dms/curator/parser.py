from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from dms.curator.metadata import canonicalize_metadata
from dms.curator.models import CurveData
from dms.hrtf import HRTFCurve


# A token is a decimal-comma number when the comma separates two digit runs.
_DECIMAL_COMMA = re.compile(r"^[-+]?\d+,\d+([eE][-+]?\d+)?$")
# Tokens are split on tab / semicolon / whitespace first so the comma can still
# be tested as a decimal separator; commas only become delimiters afterwards.
_FIELD_SPLIT = re.compile(r"[\s;]+")
_FIELD_SPLIT_WITH_COMMA = re.compile(r"[\s;,]+")
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


def parse_measurement_txt(path: str | Path) -> CurveData:
    file_path = Path(path)
    rows, metadata, warnings = _numeric_rows(file_path)
    if len(rows) < 2:
        raise ValueError(f"{file_path.name} has fewer than 2 valid data rows.")

    kept, dropped, wide = _classify_rows(rows)
    if dropped:
        warnings.append(
            f"{file_path.name}: dropped {dropped} row(s) with an unexpected column count."
        )
    if len(kept) < 2:
        raise ValueError(f"{file_path.name} has fewer than 2 consistent data rows.")

    declared_variation = "variation" in metadata.get("Export Type", "").lower()
    if wide and not declared_variation and not _looks_like_percentiles(kept):
        # Six or more columns that are not ordered percentiles: a REW
        # distortion export, for example. Read it as a plain response.
        wide = False
        warnings.append(
            f"{file_path.name}: extra columns are not a variation band; "
            "imported the first two columns as a frequency response."
        )

    if wide:
        data = np.asarray([row[:6] for row in kept], dtype=float)
        data = _positive_sorted(data, file_path)
        data, merged = _average_duplicate_frequencies(data)
        if merged:
            warnings.append(
                f"{file_path.name}: averaged {merged} repeated frequency row(s)."
            )
        return CurveData(
            kind="variation",
            freqs=data[:, 0],
            p10_db=data[:, 1],
            p25_db=data[:, 2],
            median_db=data[:, 3],
            p75_db=data[:, 4],
            p90_db=data[:, 5],
            metadata=canonicalize_metadata(metadata),
            warnings=tuple(warnings),
        )

    data = np.asarray([row[:2] for row in kept], dtype=float)
    data = _positive_sorted(data, file_path)
    data, merged = _average_duplicate_frequencies(data)
    if merged:
        warnings.append(
            f"{file_path.name}: averaged {merged} repeated frequency row(s)."
        )
    return CurveData(
        kind="fr",
        freqs=data[:, 0],
        mag_db=data[:, 1],
        metadata=canonicalize_metadata(metadata),
        warnings=tuple(warnings),
    )


def parse_fr_txt(path: str | Path, *, label: str = "FR") -> CurveData:
    curve = parse_measurement_txt(path)
    if curve.kind != "fr":
        raise ValueError(f"{label} file '{Path(path).name}' must be a two-column FR file.")
    return curve


def load_hrtf_txt(path: str | Path) -> HRTFCurve:
    return HRTFCurve(str(path))


def read_measurement_text(path: Path) -> str:
    """Decode a measurement file, honouring UTF-8 and UTF-16 byte-order marks."""
    raw = path.read_bytes()
    if raw[:2] in _UTF16_BOMS:
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8-sig", errors="replace")


def _split_numeric_fields(line: str) -> list[str]:
    """Split one data line, treating ``,`` as a decimal point when it is one."""
    # REW with a decimal-comma locale and a comma delimiter writes
    # "20,141602, 103,464": drop the delimiter left hanging on each token.
    parts = [part.rstrip(",;") for part in _FIELD_SPLIT.split(line) if part.rstrip(",;")]
    # A single "100,0" is ambiguous; only read it as a decimal comma when the
    # line still yields at least two columns that way.
    if len(parts) >= 2 and all(_DECIMAL_COMMA.match(part) for part in parts):
        return [part.replace(",", ".") for part in parts]
    return [part for part in _FIELD_SPLIT_WITH_COMMA.split(line) if part]


def _numeric_rows(path: Path) -> tuple[list[list[float]], dict[str, str], list[str]]:
    rows: list[list[float]] = []
    metadata: dict[str, str] = {}
    warnings: list[str] = []
    for raw_line in read_measurement_text(path).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("*") or line.startswith("#"):
            _capture_metadata(line, metadata)
            continue
        values: list[float] = []
        for part in _split_numeric_fields(line):
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
    return rows, metadata, warnings


def _classify_rows(rows: list[list[float]]) -> tuple[list[list[float]], int, bool]:
    """Keep the dominant row width; return (kept rows, dropped count, is_wide)."""
    wide_rows = [row for row in rows if len(row) >= 6]
    narrow_rows = [row for row in rows if len(row) < 6]
    if not wide_rows:
        return narrow_rows, 0, False
    if not narrow_rows:
        return wide_rows, 0, True
    if len(wide_rows) == len(narrow_rows):
        wide = len(rows[0]) >= 6
    else:
        wide = len(wide_rows) > len(narrow_rows)
    kept = wide_rows if wide else narrow_rows
    dropped = len(rows) - len(kept)
    return kept, dropped, wide


def _looks_like_percentiles(rows: list[list[float]]) -> bool:
    """True when columns 1-5 rise left to right (p10..p90) on nearly every row."""
    bands = np.asarray([row[1:6] for row in rows], dtype=float)
    ordered = np.all(np.diff(bands, axis=1) >= -0.01, axis=1)
    return float(np.mean(ordered)) >= 0.9


def _average_duplicate_frequencies(data: np.ndarray) -> tuple[np.ndarray, int]:
    """Collapse repeated frequencies by averaging, preserving ascending order."""
    freqs = data[:, 0]
    unique_freqs, inverse, counts = np.unique(freqs, return_inverse=True, return_counts=True)
    if unique_freqs.size == freqs.size:
        return data, 0
    merged = np.zeros((unique_freqs.size, data.shape[1]), dtype=float)
    merged[:, 0] = unique_freqs
    for column in range(1, data.shape[1]):
        sums = np.zeros(unique_freqs.size, dtype=float)
        np.add.at(sums, inverse, data[:, column])
        merged[:, column] = sums / counts
    return merged, int(freqs.size - unique_freqs.size)


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
