from __future__ import annotations

from dataclasses import replace

import numpy as np
from scipy.special import erf

from dms.curator.models import CurveData, LayerState
from dms.processing import smooth_fractional_octave

NORMALIZATION_FREQ_HZ = 1000.0
COMBINE_GRID_POINTS = 1200
COMBINE_F_MIN = 20.0
COMBINE_F_MAX = 20000.0
# Standard normal quantiles used to turn a percentile band into a sigma.
Z_P90 = 1.2816
Z_P75 = 0.6745
_MIXTURE_QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)
_MIXTURE_GRID_POINTS = 2048
_MIXTURE_SIGMA_FLOOR = 1e-9


def normalization_offset_at_1khz(curve: CurveData) -> float:
    offset, _warning = normalization_offset_at_1khz_with_warning(curve)
    return offset


def normalization_offset_at_1khz_with_warning(
    curve: CurveData,
) -> tuple[float, str | None]:
    """Return the 1 kHz offset, or 0 dB plus a warning when 1 kHz is out of range.

    ``np.interp`` clamps to the nearest endpoint, so a partial curve that stops
    below 1 kHz used to be shifted by an arbitrary endpoint value with no
    warning. A curve that does not cover 1 kHz is left where it is instead.
    """
    if curve.kind == "fr" and curve.mag_db is not None:
        values = curve.mag_db
    elif curve.kind == "variation" and curve.median_db is not None:
        values = curve.median_db
    else:
        return 0.0, None

    freqs = np.asarray(curve.freqs, dtype=float)
    if freqs.size == 0:
        return 0.0, None
    low = float(np.min(freqs))
    high = float(np.max(freqs))
    if not (low <= NORMALIZATION_FREQ_HZ <= high):
        return 0.0, (
            "1 kHz is outside this curve's frequency range "
            f"({low:g}-{high:g} Hz); it was left un-normalized."
        )
    return -float(np.interp(NORMALIZATION_FREQ_HZ, freqs, values)), None


def apply_layer_transform(layer: LayerState) -> CurveData:
    """Compensate one layer. A population HRTF's spread is merged with the
    layer's own - see ``HRTFCurve.apply_to_variation``.
    """
    curve = layer.curve
    if layer.hrtf is not None:
        if getattr(layer.hrtf, "is_variation", False):
            curve = _apply_variation_hrtf(curve, layer.hrtf)
        else:
            correction = layer.hrtf.evaluate(curve.freqs)
            curve = replace(
                curve,
                mag_db=_correct_optional(curve.mag_db, correction),
                p10_db=_correct_optional(curve.p10_db, correction),
                p25_db=_correct_optional(curve.p25_db, correction),
                median_db=_correct_optional(curve.median_db, correction),
                p75_db=_correct_optional(curve.p75_db, correction),
                p90_db=_correct_optional(curve.p90_db, correction),
            )
    return curve.shifted(layer.vertical_offset_db)


def smooth_curve(curve: CurveData, fraction: int) -> CurveData:
    """Return a smoothed display copy without changing the stored source curve."""

    def smooth(values: np.ndarray | None) -> np.ndarray | None:
        if values is None:
            return None
        return smooth_fractional_octave(curve.freqs, values, fraction=fraction)[1]

    return replace(
        curve,
        mag_db=smooth(curve.mag_db),
        p10_db=smooth(curve.p10_db),
        p25_db=smooth(curve.p25_db),
        median_db=smooth(curve.median_db),
        p75_db=smooth(curve.p75_db),
        p90_db=smooth(curve.p90_db),
    )


def visible_display_layers(
    layers: list[LayerState],
    smoothing_fraction: int = 48,
) -> list[tuple[LayerState, CurveData]]:
    return [
        (
            layer,
            smooth_curve(apply_layer_transform(layer), smoothing_fraction),
        )
        for layer in layers
        if layer.visible
    ]


def can_combine_layers(layers: list[LayerState]) -> bool:
    return len(layers) >= 2 and all(_is_complete_variation(layer.curve) for layer in layers)


def combine_grid(
    n_points: int = COMBINE_GRID_POINTS,
    f_min: float = COMBINE_F_MIN,
    f_max: float = COMBINE_F_MAX,
    f_ref: float = NORMALIZATION_FREQ_HZ,
) -> np.ndarray:
    """Log grid with an exact 1 kHz point, matching ``compute_rms_average``."""
    freqs = np.logspace(np.log10(f_min), np.log10(f_max), n_points)
    freqs[int(np.argmin(np.abs(freqs - f_ref)))] = f_ref
    return freqs


def layer_sweep_count(layer: LayerState) -> int | None:
    """Sweep count from the ``Variation Sweeps`` header, when the file has one."""
    metadata = layer.curve.metadata or {}
    for key in ("variation_sweeps", "Variation Sweeps"):
        if key in metadata:
            try:
                count = int(float(str(metadata[key]).strip()))
            except (TypeError, ValueError):
                return None
            return count if count > 0 else None
    return None


def _band_sigma(
    p10: np.ndarray,
    p25: np.ndarray,
    p75: np.ndarray,
    p90: np.ndarray,
) -> np.ndarray:
    """Sigma of the normal that best matches both reported inter-percentile widths."""
    outer = np.abs(p90 - p10) / (2.0 * Z_P90)
    inner = np.abs(p75 - p25) / (2.0 * Z_P75)
    return np.maximum((outer + inner) / 2.0, _MIXTURE_SIGMA_FLOOR)


def _normal_cdf(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + erf(x / np.sqrt(2.0)))


def _mixture_percentiles(
    means: np.ndarray,
    sigmas: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    """Invert a weighted mixture-of-normals CDF on a fine dB grid.

    ``means``/``sigmas``/``weights`` are (n_layers, n_freqs); the result is
    (5, n_freqs) holding p10, p25, median, p75 and p90 of the pooled mixture.
    """
    n_freqs = means.shape[1]
    result = np.empty((len(_MIXTURE_QUANTILES), n_freqs), dtype=float)
    normalized = weights / np.sum(weights, axis=0, keepdims=True)
    low = np.min(means - 6.0 * sigmas, axis=0)
    high = np.max(means + 6.0 * sigmas, axis=0)
    for index in range(n_freqs):
        lo = float(low[index])
        hi = float(high[index])
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            result[:, index] = float(np.average(means[:, index], weights=normalized[:, index]))
            continue
        grid = np.linspace(lo, hi, _MIXTURE_GRID_POINTS)
        z = (grid[None, :] - means[:, index][:, None]) / sigmas[:, index][:, None]
        cdf = normalized[:, index] @ _normal_cdf(z)
        cdf = np.maximum.accumulate(cdf)
        for row, quantile in enumerate(_MIXTURE_QUANTILES):
            result[row, index] = float(np.interp(quantile, cdf, grid))
    return result


def combine_variation_layers(layers: list[LayerState]) -> CurveData:
    """Pool variation layers as a sweep-count-weighted mixture of normals.

    Each layer's band is modelled at every frequency as a normal with the
    layer's median as its mean and a sigma averaged from the two reported
    inter-percentile widths. The layers form a weighted mixture whose quantile
    function is inverted numerically, so a wide layer widens the pooled band
    instead of being averaged away by a percentile-of-percentiles.
    """
    if not can_combine_layers(layers):
        raise ValueError("Select at least two complete variation layers to combine.")

    freqs = combine_grid()
    means: list[np.ndarray] = []
    sigmas: list[np.ndarray] = []
    coverage: list[np.ndarray] = []
    for layer in layers:
        curve = apply_layer_transform(layer)
        if not _is_complete_variation(curve):
            raise ValueError("Only complete variation layers can be combined.")
        assert curve.p10_db is not None
        assert curve.p25_db is not None
        assert curve.median_db is not None
        assert curve.p75_db is not None
        assert curve.p90_db is not None
        source = np.asarray(curve.freqs, dtype=float)
        p10, p25, median, p75, p90 = (
            np.interp(freqs, source, values)
            for values in (
                curve.p10_db,
                curve.p25_db,
                curve.median_db,
                curve.p75_db,
                curve.p90_db,
            )
        )
        means.append(median)
        sigmas.append(_band_sigma(p10, p25, p75, p90))
        coverage.append(
            ((freqs >= float(np.min(source))) & (freqs <= float(np.max(source)))).astype(float)
        )

    counts = [layer_sweep_count(layer) for layer in layers]
    if all(count is not None for count in counts):
        base_weights = np.asarray([float(count) for count in counts], dtype=float)
    else:
        base_weights = np.ones(len(layers), dtype=float)

    weights = base_weights[:, None] * np.vstack(coverage)
    # Frequencies no layer covers fall back to every layer's edge-held value.
    uncovered = np.sum(weights, axis=0) <= 0.0
    if np.any(uncovered):
        weights[:, uncovered] = base_weights[:, None]

    percentiles = _mixture_percentiles(np.vstack(means), np.vstack(sigmas), weights)
    return CurveData(
        kind="variation",
        freqs=freqs,
        p10_db=percentiles[0],
        p25_db=percentiles[1],
        median_db=percentiles[2],
        p75_db=percentiles[3],
        p90_db=percentiles[4],
        metadata={"Derived": "Combined variation"},
    )


def _correct_optional(values: np.ndarray | None, correction: np.ndarray) -> np.ndarray | None:
    if values is None:
        return None
    return values - correction


def _apply_variation_hrtf(curve: CurveData, hrtf) -> CurveData:
    if curve.kind == "fr" and curve.mag_db is not None:
        p10, p25, median, p75, p90 = hrtf.apply_to_magnitude_as_variation(
            curve.freqs,
            curve.mag_db,
        )
    elif _is_complete_variation(curve):
        assert curve.p10_db is not None
        assert curve.p25_db is not None
        assert curve.median_db is not None
        assert curve.p75_db is not None
        assert curve.p90_db is not None
        p10, p25, median, p75, p90 = hrtf.apply_to_variation(
            curve.freqs,
            curve.p10_db,
            curve.p25_db,
            curve.median_db,
            curve.p75_db,
            curve.p90_db,
        )
    else:
        return curve
    return replace(
        curve,
        kind="variation",
        mag_db=None,
        p10_db=p10,
        p25_db=p25,
        median_db=median,
        p75_db=p75,
        p90_db=p90,
    )


def _is_complete_variation(curve: CurveData) -> bool:
    return curve.kind == "variation" and all(
        values is not None
        for values in (
            curve.p10_db,
            curve.p25_db,
            curve.median_db,
            curve.p75_db,
            curve.p90_db,
        )
    )
