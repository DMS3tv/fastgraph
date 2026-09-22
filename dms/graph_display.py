"""Display-only graph transforms that do not modify stored curve data."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen

from dms.processing import VariationBand
from dms.style_tokens import ThemeTokens, tokens_for

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


def aperiodic_dither_band_image(
    width: int,
    height: int,
    upper_points: Sequence[tuple[float, float]],
    lower_points: Sequence[tuple[float, float]],
    *,
    foreground: QColor,
    minimum_density: float = 0.045,
    maximum_density: float = 0.34,
    seed: int = 0xD17E3,
) -> QImage:
    """Return a transparent, aperiodic dither band.

    Point coordinates are normalized to the image. The Y coordinate is zero at
    the top. Ink density is highest at each boundary and lowest at the center.
    """

    image_width = max(1, int(width))
    image_height = max(1, int(height))
    rgba = np.zeros((image_height, image_width, 4), dtype=np.uint8)
    if not upper_points or not lower_points:
        return _rgba_image(rgba)

    upper = _sample_normalized_curve(upper_points, image_width)
    lower = _sample_normalized_curve(lower_points, image_width)
    top = np.minimum(upper, lower) * max(0, image_height - 1)
    bottom = np.maximum(upper, lower) * max(0, image_height - 1)

    rows = np.arange(image_height, dtype=np.float64)[:, None]
    inside = (rows >= top[None, :]) & (rows <= bottom[None, :])
    nearest_edge = np.minimum(rows - top[None, :], bottom[None, :] - rows)
    half_span = np.maximum((bottom - top) * 0.5, 1.0)[None, :]
    center_distance = np.clip(nearest_edge / half_span, 0.0, 1.0)
    edge_weight = np.square(1.0 - center_distance)
    low = max(0.0, min(1.0, float(minimum_density)))
    high = max(low, min(1.0, float(maximum_density)))
    density = low + (high - low) * edge_weight

    columns_u32 = np.arange(image_width, dtype=np.uint32)[None, :]
    rows_u32 = np.arange(image_height, dtype=np.uint32)[:, None]
    hashed = (columns_u32 * np.uint32(0x9E3779B1)) ^ (rows_u32 * np.uint32(0x85EBCA77))
    hashed ^= np.uint32(seed & 0xFFFFFFFF)
    hashed ^= hashed >> np.uint32(16)
    hashed *= np.uint32(0x7FEB352D)
    hashed ^= hashed >> np.uint32(15)
    hashed *= np.uint32(0x846CA68B)
    hashed ^= hashed >> np.uint32(16)
    threshold = hashed.astype(np.float64) / float(np.iinfo(np.uint32).max)
    ink = inside & (threshold < density)

    color = QColor(foreground)
    rgba[ink] = np.array(color.getRgb(), dtype=np.uint8)
    return _rgba_image(rgba)


def paint_aperiodic_dither_band(
    painter: QPainter,
    rect: QRectF,
    upper_points: Sequence[tuple[float, float]],
    lower_points: Sequence[tuple[float, float]],
    *,
    foreground: QColor,
    minimum_density: float = 0.045,
    maximum_density: float = 0.34,
) -> None:
    """Paint the shared aperiodic dither band into a target rectangle."""

    width = max(1, round(rect.width()))
    height = max(1, round(rect.height()))
    image = aperiodic_dither_band_image(
        width,
        height,
        upper_points,
        lower_points,
        foreground=foreground,
        minimum_density=minimum_density,
        maximum_density=maximum_density,
    )
    painter.drawImage(rect, image)


def _sample_normalized_curve(points: Sequence[tuple[float, float]], width: int) -> np.ndarray:
    values = np.asarray(points, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 2:
        raise ValueError("Dither boundary points must contain X and Y pairs")
    finite = np.isfinite(values).all(axis=1)
    values = values[finite]
    if len(values) == 0:
        return np.zeros(width, dtype=np.float64)
    order = np.argsort(values[:, 0], kind="stable")
    values = values[order]
    sample_x = np.linspace(0.0, 1.0, width)
    return np.clip(np.interp(sample_x, values[:, 0], values[:, 1]), 0.0, 1.0)


def _rgba_image(rgba: np.ndarray) -> QImage:
    height, width, _channels = rgba.shape
    image = QImage(
        rgba.data,
        width,
        height,
        int(rgba.strides[0]),
        QImage.Format.Format_RGBA8888,
    )
    return image.copy()
