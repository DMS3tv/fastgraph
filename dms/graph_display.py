"""Display-only graph transforms that do not modify stored curve data."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPen

from dms.processing import VariationBand
from dms.ui.style_tokens import ThemeTokens, tokens_for

RETRO_GRAPH_MAX_BINS = 256
STIPPLE_DASH_PATTERNS: tuple[tuple[float, ...] | None, ...] = (
    None,
    (6.0, 3.0),
    (1.0, 3.0),
    (6.0, 3.0, 1.0, 3.0),
    (3.0, 3.0),
    (6.0, 3.0, 1.0, 3.0, 1.0, 3.0),
    (1.0, 2.0),
    (10.0, 3.0, 1.0, 3.0),
)


def stipple_trace_pen(
    pen_or_color: QPen | QColor | str | tuple[int, ...],
    trace_index: int,
    theme_or_tokens: object,
) -> QPen:
    """Return a trace pen with the active theme's display-only line pattern."""
    if isinstance(pen_or_color, QPen):
        pen = QPen(pen_or_color)
    elif isinstance(pen_or_color, QColor):
        pen = QPen(QColor(pen_or_color))
    elif isinstance(pen_or_color, tuple):
        pen = QPen(QColor(*pen_or_color))
    else:
        pen = QPen(QColor(str(pen_or_color)))

    tokens = (
        theme_or_tokens
        if isinstance(theme_or_tokens, ThemeTokens)
        else tokens_for(str(theme_or_tokens or "dark"))
    )
    if not tokens.stipple_traces:
        return pen

    pattern = STIPPLE_DASH_PATTERNS[int(trace_index) % len(STIPPLE_DASH_PATTERNS)]
    if pattern is None:
        pen.setStyle(Qt.PenStyle.SolidLine)
    else:
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern(list(pattern))
    return pen


def uses_retro_steps(theme: object, *, brand_mode: bool = False) -> bool:
    """Return true when a graph must use the fine staircase renderer."""
    return not brand_mode and tokens_for(str(theme or "dark")).retro_graph


def retro_step_group(
    freqs: Sequence[float] | np.ndarray,
    value_groups: Sequence[Sequence[float] | np.ndarray],
    *,
    max_bins: int = RETRO_GRAPH_MAX_BINS,
) -> tuple[np.ndarray, ...]:
    """Reduce a curve group with extrema retention, then make staircase points.

    The source arrays are never changed. All curves in the group use the same
    retained frequency indexes so variation bands and bounds stay aligned.
    """
    source_freqs = np.asarray(freqs, dtype=float)
    source_values = [np.asarray(values, dtype=float) for values in value_groups]
    if not source_values:
        return (np.array(source_freqs, copy=True),)
    length = min([len(source_freqs), *(len(values) for values in source_values)])
    if length == 0:
        return (
            np.array(source_freqs[:0], copy=True),
            *(np.array(values[:0], copy=True) for values in source_values),
        )

    source_freqs = source_freqs[:length]
    source_values = [values[:length] for values in source_values]
    indexes = _retained_indexes(source_freqs, source_values, max_bins=max_bins)
    reduced_freqs = source_freqs[indexes]
    reduced_values = [values[indexes] for values in source_values]
    if len(reduced_freqs) <= 1:
        return (
            np.array(reduced_freqs, copy=True),
            *(np.array(values, copy=True) for values in reduced_values),
        )

    output_length = len(reduced_freqs) * 2 - 1
    stepped_freqs = np.empty(output_length, dtype=float)
    stepped_freqs[0::2] = reduced_freqs
    stepped_freqs[1::2] = reduced_freqs[1:]
    stepped_values: list[np.ndarray] = []
    for values in reduced_values:
        stepped = np.empty(output_length, dtype=float)
        stepped[0::2] = values
        stepped[1::2] = values[:-1]
        stepped_values.append(stepped)
    return (stepped_freqs, *stepped_values)


def retro_step_band(band: VariationBand) -> VariationBand:
    """``retro_step_group`` for a whole variation band."""
    return VariationBand(*retro_step_group(band.freqs, band[1:]))


def retro_step_series(
    freqs: Sequence[float] | np.ndarray,
    values: Sequence[float] | np.ndarray,
    *,
    max_bins: int = RETRO_GRAPH_MAX_BINS,
) -> tuple[np.ndarray, np.ndarray]:
    stepped = retro_step_group(freqs, (values,), max_bins=max_bins)
    return stepped[0], stepped[1]


def _retained_indexes(
    freqs: np.ndarray,
    value_groups: list[np.ndarray],
    *,
    max_bins: int,
) -> np.ndarray:
    length = len(freqs)
    if length <= 2:
        return np.arange(length, dtype=int)
    positive = np.isfinite(freqs) & (freqs > 0.0)
    if not np.all(positive):
        return np.arange(length, dtype=int)
    bin_count = max(1, min(int(max_bins), length))
    if length <= bin_count:
        return np.arange(length, dtype=int)

    log_freqs = np.log10(freqs)
    low = float(log_freqs[0])
    high = float(log_freqs[-1])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return np.arange(length, dtype=int)

    edges = np.linspace(low, high, bin_count + 1)
    bin_ids = np.clip(np.searchsorted(edges, log_freqs, side="right") - 1, 0, bin_count - 1)
    matrix = np.vstack(value_groups)
    selected = {0, length - 1}
    for bin_index in range(bin_count):
        members = np.flatnonzero(bin_ids == bin_index)
        if len(members) == 0:
            continue
        window = matrix[:, members]
        lower = np.nanmin(window, axis=0)
        upper = np.nanmax(window, axis=0)
        center = np.nanmean(window, axis=0)
        for series in (lower, upper, center):
            if np.any(np.isfinite(series)):
                selected.add(int(members[int(np.nanargmin(series))]))
                selected.add(int(members[int(np.nanargmax(series))]))
    return np.array(sorted(selected), dtype=int)
