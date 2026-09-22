"""Target comparison: deltas, deviation scoring, EQ suggestion and A/B layers.

This module is the pure, non-UI half of the comparison feature. Everything
here works on plain NumPy arrays and file paths, so it can be unit-tested
without Qt and reused by exporters as well as by the window.

The vocabulary, in one place:

``delta``
    ``measurement - target`` in dB, matching the sign convention the R&D
    workspace already uses for its delta mode: a positive delta means the
    measurement is *louder* than the reference at that frequency.

``correction``
    ``-delta``: what an equalizer would have to add to bring the measurement
    onto the target.

``offset``
    The level shift applied to the measurement before subtracting the target.
    Two curves normalized at different points are otherwise not comparable,
    and the shift is not a property of the headphone.

Every curve that leaves this module is sampled on :data:`COMMON_GRID`, the
same 1200-point log grid (20 Hz - 20 kHz, with an exact 1 kHz point) that
``dms.processing.compute_rms_average`` builds, so curves from different
sources can be subtracted without further resampling.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from dms.processing import (
    F_REF,
    GRID_POINTS,
    compute_rms_average,
    log_grid,
    smooth_fractional_octave,
    value_at,
)

# ---------------------------------------------------------------------------
# The common grid
# ---------------------------------------------------------------------------

#: Sample rate the suggested peaking filters are defined at. Equalizer APO,
#: every AutoEQ preset and the usual convolution hosts all assume 48 kHz, and
#: a bell's dB response only depends on the sample rate through the bilinear
#: frequency warping near Nyquist.
EQ_SAMPLE_RATE = 48000.0


#: The shared abscissa for every curve this module returns. Read-only: it is
#: a module-level singleton that several callers hold references to.
COMMON_GRID = log_grid()
COMMON_GRID.flags.writeable = False

_LOG_COMMON_GRID = np.log10(np.asarray(COMMON_GRID, dtype=float))


# ---------------------------------------------------------------------------
# Small array helpers
# ---------------------------------------------------------------------------


def _as_sorted_pair(
    freqs: Any,
    values: Any,
) -> tuple[np.ndarray, np.ndarray]:
    f = np.asarray(freqs, dtype=np.float64).ravel()
    v = np.asarray(values, dtype=np.float64).ravel()
    if f.size == 0 or f.size != v.size:
        raise ValueError("freqs and values must be non-empty and the same length.")
    finite = np.isfinite(f) & np.isfinite(v) & (f > 0.0)
    if not np.any(finite):
        raise ValueError("no finite, positive-frequency samples.")
    f = f[finite]
    v = v[finite]
    if not np.all(np.diff(f) >= 0.0):
        order = np.argsort(f, kind="stable")
        f = f[order]
        v = v[order]
    return f, v


def resample_to_common(freqs: Any, mag_db: Any) -> np.ndarray:
    """Resample one curve onto :data:`COMMON_GRID`.

    Linear interpolation on log frequency, holding the first and last value
    outside the source range rather than extrapolating - the same edge
    behaviour the HRTF loader uses, so a target that stops at 10 kHz does not
    fall off a cliff.
    """
    f, v = _as_sorted_pair(freqs, mag_db)
    if f.size == 1:
        return np.full(COMMON_GRID.shape, float(v[0]))
    return np.interp(_LOG_COMMON_GRID, np.log10(f), v)


def _rms(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(values))))


def _band_mask(freqs: np.ndarray, f_low: float, f_high: float) -> np.ndarray:
    return (freqs >= float(f_low)) & (freqs <= float(f_high))


# ---------------------------------------------------------------------------
# Target curves
# ---------------------------------------------------------------------------


def load_target_curve(path: str | Path) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Load a target curve, normalized so 1 kHz reads 0 dB.

    Uses the Curator parser rather than ``load_two_column_txt_curve`` because
    target files come from everywhere: UTF-16 exports with a BOM, European
    locales with decimal commas, and six-column variation exports whose median
    column is the target. The parser's warnings are handed back so the caller
    can surface them instead of silently trusting a file it half understood.

    Returns ``(freqs, mag_db, warnings)`` on the file's own frequencies - call
    :func:`resample_to_common` to put it on the shared grid.
    """
    from dms.curator.parser import parse_measurement_txt

    curve = parse_measurement_txt(path)
    warnings = list(curve.warnings)

    if curve.kind == "variation":
        values = curve.median_db
        warnings.append(
            f"{Path(path).name}: six-column file, using the median column as the target."
        )
    else:
        values = curve.mag_db
    if values is None:
        raise ValueError(f"{Path(path).name} carries no magnitude column.")

    freqs, mag_db = _as_sorted_pair(curve.freqs, values)
    if freqs[0] > F_REF or freqs[-1] < F_REF:
        warnings.append(
            f"{Path(path).name}: covers {freqs[0]:.0f}-{freqs[-1]:.0f} Hz, so the "
            "1 kHz normalization used the nearest end of the file."
        )
    # Deliberately clamped: a partial file is anchored on its nearest end.
    anchor = min(max(F_REF, float(freqs[0])), float(freqs[-1]))
    return freqs, mag_db - value_at(freqs, mag_db, anchor, log_x=True), warnings


# ---------------------------------------------------------------------------
# Delta
# ---------------------------------------------------------------------------

OFFSET_MODES = ("1khz", "mean_200_2k", "none")

#: The band ``offset_mode="mean_200_2k"`` aligns on. Target matching is
#: conventionally judged with the midrange levels lined up: the bass and the
#: top octave are exactly where headphones legitimately differ, so anchoring
#: on a single 1 kHz sample lets one noisy point rotate the whole verdict.
OFFSET_BAND = (200.0, 2000.0)


@dataclass(frozen=True)
class DeltaResult:
    """``measurement - target`` on :data:`COMMON_GRID`.

    ``offset_db`` is the level that was subtracted from the measurement before
    the target was taken away; positive means the measurement was turned down
    to meet the target.
    """

    freqs: np.ndarray
    delta_db: np.ndarray
    offset_db: float


def delta_curve(
    measure_freqs: Any,
    measure_db: Any,
    target_freqs: Any,
    target_db: Any,
    *,
    smoothing_fraction: int | None = 12,
    offset_mode: str = "1khz",
) -> DeltaResult:
    """Subtract a target from a measurement on the common grid.

    ``smoothing_fraction`` smooths with the usual fractional-octave Gaussian
    (``None`` or ``<= 0`` disables it). Smoothing the difference is identical
    to smoothing both curves first - the kernel is linear and both curves are
    already on the same grid - so it is done once, on the delta.

    ``offset_mode`` picks how the two curves are levelled before subtracting:

    ``"1khz"``
        The delta is forced to 0 dB at 1 kHz.
    ``"mean_200_2k"``
        The mean delta over :data:`OFFSET_BAND` is removed.
    ``"none"``
        No realignment; whatever normalization the inputs carried survives.
    """
    if offset_mode not in OFFSET_MODES:
        raise ValueError(f"offset_mode must be one of {OFFSET_MODES!r}, got {offset_mode!r}.")

    measured = resample_to_common(measure_freqs, measure_db)
    target = resample_to_common(target_freqs, target_db)
    delta = measured - target

    if smoothing_fraction is not None and int(smoothing_fraction) > 0:
        _, delta = smooth_fractional_octave(
            np.asarray(COMMON_GRID, dtype=float), delta, fraction=int(smoothing_fraction)
        )
        delta = np.asarray(delta, dtype=np.float64)

    if offset_mode == "1khz":
        offset = value_at(COMMON_GRID, delta, F_REF, log_x=True)
    elif offset_mode == "mean_200_2k":
        mask = _band_mask(COMMON_GRID, *OFFSET_BAND)
        offset = float(np.mean(delta[mask])) if np.any(mask) else 0.0
    else:
        offset = 0.0

    return DeltaResult(
        freqs=np.asarray(COMMON_GRID, dtype=float),
        delta_db=delta - offset,
        offset_db=float(offset),
    )


# ---------------------------------------------------------------------------
# Deviation scoring
# ---------------------------------------------------------------------------

DEFAULT_BANDS: tuple[tuple[str, float, float], ...] = (
    ("Bass", 20.0, 200.0),
    ("Mids", 200.0, 2000.0),
    ("Treble", 2000.0, 10000.0),
    ("Air", 10000.0, 20000.0),
)

#: The overall figure deliberately stops at 10 kHz. Above it, coupler
#: measurements and targets disagree for reasons that have nothing to do with
#: how the headphone sounds, and letting that dominate a single score would
#: make the number useless.
OVERALL_RANGE = (20.0, 10000.0)

#: dB of overall RMS deviation that costs 10 match points.
MATCH_DB_PER_10_PERCENT = 1.0


@dataclass(frozen=True)
class BandDeviation:
    """How far one band strays from the target."""

    name: str
    f_low: float
    f_high: float
    rms_db: float
    mean_db: float
    max_abs_db: float
    max_abs_freq_hz: float


@dataclass(frozen=True)
class DeviationScore:
    """Per-band and overall deviation, plus the headline match percentage."""

    bands: tuple[BandDeviation, ...]
    overall_rms_db: float
    match_percent: float


def deviation_score(
    delta: DeltaResult,
    bands: Sequence[tuple[str, float, float]] = DEFAULT_BANDS,
) -> DeviationScore:
    """Score a delta curve band by band.

    ``match_percent`` is ``clamp(100 - 10 * overall_rms_db, 0, 100)``. It is a
    presentation heuristic and nothing more: it has no perceptual model behind
    it, it is not comparable across different targets, and 1 dB of RMS
    deviation costing exactly 10 points is a choice, not a measurement. It
    exists so a list of measurements can be ordered at a glance.
    """
    freqs = np.asarray(delta.freqs, dtype=np.float64)
    values = np.asarray(delta.delta_db, dtype=np.float64)

    results: list[BandDeviation] = []
    for name, f_low, f_high in bands:
        mask = _band_mask(freqs, f_low, f_high)
        if not np.any(mask):
            results.append(
                BandDeviation(
                    name=str(name),
                    f_low=float(f_low),
                    f_high=float(f_high),
                    rms_db=0.0,
                    mean_db=0.0,
                    max_abs_db=0.0,
                    max_abs_freq_hz=float("nan"),
                )
            )
            continue
        band_freqs = freqs[mask]
        band_values = values[mask]
        peak = int(np.argmax(np.abs(band_values)))
        results.append(
            BandDeviation(
                name=str(name),
                f_low=float(f_low),
                f_high=float(f_high),
                rms_db=_rms(band_values),
                mean_db=float(np.mean(band_values)),
                max_abs_db=float(np.abs(band_values[peak])),
                max_abs_freq_hz=float(band_freqs[peak]),
            )
        )

    overall_mask = _band_mask(freqs, *OVERALL_RANGE)
    overall = _rms(values[overall_mask]) if np.any(overall_mask) else _rms(values)
    match = 100.0 - 10.0 * overall / MATCH_DB_PER_10_PERCENT
    return DeviationScore(
        bands=tuple(results),
        overall_rms_db=float(overall),
        match_percent=float(min(100.0, max(0.0, match))),
    )


def format_deviation_summary(score: DeviationScore) -> str:
    """Plain-text summary: one line per band, then the overall line."""
    lines: list[str] = []
    width = max((len(band.name) for band in score.bands), default=0)
    for band in score.bands:
        if np.isfinite(band.max_abs_freq_hz):
            worst = f"{band.max_abs_db:.1f} dB at {band.max_abs_freq_hz:.0f} Hz"
        else:
            worst = "no data"
        lines.append(
            f"{band.name:<{width}}  "
            f"{band.f_low:>6.0f}-{band.f_high:<6.0f} Hz  "
            f"RMS {band.rms_db:5.2f} dB  "
            f"mean {band.mean_db:+6.2f} dB  "
            f"max {worst}"
        )
    lines.append(
        f"Overall ({OVERALL_RANGE[0]:.0f}-{OVERALL_RANGE[1]:.0f} Hz)  "
        f"RMS {score.overall_rms_db:.2f} dB  "
        f"match {score.match_percent:.0f}%"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Peaking filters
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PeakingFilter:
    """One RBJ peaking (bell) filter, as Equalizer APO understands it."""

    freq_hz: float
    gain_db: float
    q: float


def _peaking_response_matrix(
    freq_hz: np.ndarray,
    gain_db: np.ndarray,
    q: np.ndarray,
    freqs: np.ndarray,
    fs: float = EQ_SAMPLE_RATE,
) -> np.ndarray:
    """dB magnitude of N peaking filters at M frequencies -> shape (N, M).

    The RBJ cookbook peaking biquad, evaluated analytically on the unit circle
    rather than run through a filter, so the answer is exact and the cost is
    one complex expression.
    """
    f0 = np.atleast_1d(np.asarray(freq_hz, dtype=np.float64))
    gain = np.atleast_1d(np.asarray(gain_db, dtype=np.float64))
    quality = np.atleast_1d(np.asarray(q, dtype=np.float64))
    nyquist = 0.5 * float(fs)
    f0 = np.clip(f0, 1e-6, nyquist * 0.999999)
    quality = np.clip(quality, 1e-6, None)

    amp = 10.0 ** (gain / 40.0)
    w0 = 2.0 * np.pi * f0 / float(fs)
    alpha = np.sin(w0) / (2.0 * quality)
    cos_w0 = np.cos(w0)

    b0 = 1.0 + alpha * amp
    b1 = -2.0 * cos_w0
    b2 = 1.0 - alpha * amp
    a0 = 1.0 + alpha / amp
    a1 = b1
    a2 = 1.0 - alpha / amp

    w = (
        2.0
        * np.pi
        * np.clip(np.asarray(freqs, dtype=np.float64), 0.0, nyquist * 0.999999)
        / float(fs)
    )
    z1 = np.exp(-1j * w)[None, :]
    z2 = z1 * z1

    num = b0[:, None] + b1[:, None] * z1 + b2[:, None] * z2
    den = a0[:, None] + a1[:, None] * z1 + a2[:, None] * z2
    with np.errstate(divide="ignore", invalid="ignore"):
        response = 20.0 * np.log10(np.abs(num / den))
    return np.nan_to_num(response, nan=0.0, posinf=0.0, neginf=0.0)


def eq_response_db(
    filters: Iterable[PeakingFilter],
    freqs: Any = COMMON_GRID,
    fs: float = EQ_SAMPLE_RATE,
) -> np.ndarray:
    """Combined dB response of a filter set (cascaded filters add in dB)."""
    grid = np.asarray(freqs, dtype=np.float64).ravel()
    items = list(filters)
    if not items:
        return np.zeros(grid.shape)
    matrix = _peaking_response_matrix(
        np.array([f.freq_hz for f in items], dtype=np.float64),
        np.array([f.gain_db for f in items], dtype=np.float64),
        np.array([f.q for f in items], dtype=np.float64),
        grid,
        fs=fs,
    )
    return np.sum(matrix, axis=0)


def compute_preamp_db(
    filters: Iterable[PeakingFilter],
    freqs: Any = COMMON_GRID,
    fs: float = EQ_SAMPLE_RATE,
) -> float:
    """The attenuation that keeps the filter set from clipping.

    ``-max(0, peak of the combined response)``: enough headroom for the
    loudest point of the EQ, and never a boost.
    """
    response = eq_response_db(filters, freqs, fs=fs)
    if response.size == 0:
        return 0.0
    peak = float(np.max(response))
    return -peak if peak > 0.0 else 0.0


# ---------------------------------------------------------------------------
# EQ suggestion
# ---------------------------------------------------------------------------

#: Stop adding filters once one buys less than this much residual RMS.
EQ_MIN_IMPROVEMENT_DB = 0.1

_COARSE_FREQ_SPAN_OCT = 0.4
_COARSE_FREQ_STEPS = 13
_COARSE_Q_STEPS = 18
_FINE_FREQ_SPAN_OCT = 0.08
_FINE_FREQ_STEPS = 9
_FINE_Q_RATIO = 1.3
_FINE_Q_STEPS = 9


@dataclass(frozen=True)
class EqSuggestion:
    """A suggested parametric EQ and what it leaves behind.

    ``residual_delta_db`` is the delta that would remain after applying the
    filters - ``delta_db + eq_response_db(filters, COMMON_GRID)`` - and is
    sampled on :data:`COMMON_GRID`. ``residual_rms_db`` is its RMS.
    """

    filters: list[PeakingFilter] = field(default_factory=list)
    preamp_db: float = 0.0
    residual_rms_db: float = 0.0
    residual_delta_db: np.ndarray = field(default_factory=lambda: np.zeros(GRID_POINTS))


def _fit_candidates(
    residual: np.ndarray,
    grid: np.ndarray,
    freq_candidates: np.ndarray,
    q_candidates: np.ndarray,
    max_gain_db: float,
) -> tuple[PeakingFilter, np.ndarray, float]:
    """Best (freq, Q, gain) over a candidate mesh, by residual RMS.

    For each (freq, Q) the gain is solved in closed form: a bell's dB response
    is very nearly proportional to its gain, so the least-squares gain against
    the unit-gain shape is one dot product. The proportionality is only
    approximate (a bell narrows slightly as it is boosted), so the shape is
    re-measured at the solved gain and the gain re-solved once - that second
    pass is what makes the recovered gain accurate to hundredths of a dB
    rather than tenths.
    """
    mesh_f, mesh_q = np.meshgrid(freq_candidates, q_candidates, indexing="ij")
    f_flat = mesh_f.ravel()
    q_flat = mesh_q.ravel()
    ones = np.ones_like(f_flat)

    shape = _peaking_response_matrix(f_flat, ones, q_flat, grid)
    for _ in range(2):
        denom = np.sum(shape * shape, axis=1)
        denom = np.where(denom > 1e-12, denom, 1e-12)
        gains = np.clip((shape @ residual) / denom, -max_gain_db, max_gain_db)
        response = _peaking_response_matrix(f_flat, gains, q_flat, grid)
        # Re-normalize the shape at the gain actually being used.
        safe = np.where(np.abs(gains) > 1e-3, gains, 1.0)[:, None]
        shape = np.where(np.abs(gains)[:, None] > 1e-3, response / safe, shape)

    errors = residual[None, :] - response
    rms = np.sqrt(np.mean(np.square(errors), axis=1))
    best = int(np.argmin(rms))
    return (
        PeakingFilter(
            freq_hz=float(f_flat[best]),
            gain_db=float(gains[best]),
            q=float(q_flat[best]),
        ),
        response[best],
        float(rms[best]),
    )


def _fit_one_filter(
    residual: np.ndarray,
    grid: np.ndarray,
    q_range: tuple[float, float],
    max_gain_db: float,
) -> tuple[PeakingFilter, np.ndarray, float]:
    """Fit one bell to the largest remaining feature: coarse mesh, then fine."""
    peak_index = int(np.argmax(np.abs(residual)))
    f_peak = float(grid[peak_index])
    f_lo, f_hi = float(grid[0]), float(grid[-1])
    q_lo, q_hi = float(min(q_range)), float(max(q_range))

    coarse_freqs = np.clip(
        f_peak
        * 2.0 ** np.linspace(-_COARSE_FREQ_SPAN_OCT, _COARSE_FREQ_SPAN_OCT, _COARSE_FREQ_STEPS),
        f_lo,
        f_hi,
    )
    coarse_qs = np.geomspace(q_lo, q_hi, _COARSE_Q_STEPS)
    candidate, response, rms = _fit_candidates(residual, grid, coarse_freqs, coarse_qs, max_gain_db)

    fine_freqs = np.clip(
        candidate.freq_hz
        * 2.0 ** np.linspace(-_FINE_FREQ_SPAN_OCT, _FINE_FREQ_SPAN_OCT, _FINE_FREQ_STEPS),
        f_lo,
        f_hi,
    )
    fine_qs = np.clip(
        np.geomspace(candidate.q / _FINE_Q_RATIO, candidate.q * _FINE_Q_RATIO, _FINE_Q_STEPS),
        q_lo,
        q_hi,
    )
    fine_candidate, fine_response, fine_rms = _fit_candidates(
        residual, grid, fine_freqs, fine_qs, max_gain_db
    )
    if fine_rms <= rms:
        return fine_candidate, fine_response, fine_rms
    return candidate, response, rms


def suggest_eq(
    delta: DeltaResult,
    *,
    max_filters: int = 8,
    max_gain_db: float = 12.0,
    min_gain_db: float = 0.5,
    q_range: tuple[float, float] = (0.5, 8.0),
    preamp: bool = True,
    smoothing_fraction: int | None = 12,
) -> EqSuggestion:
    """Greedily fit peaking filters that undo a delta.

    The correction to fit is ``-delta``: what the EQ must add. The procedure,
    repeated until ``max_filters`` filters exist or one buys less than
    :data:`EQ_MIN_IMPROVEMENT_DB` of residual RMS:

    1. Find the frequency where the remaining correction is largest.
    2. Search a mesh of centre frequencies within +/-0.4 octave of it and Qs
       across ``q_range``, solving the gain in closed form for each, then
       refine on a narrow mesh around the winner.
    3. Subtract the chosen filter's exact dB response and continue.

    Gains are clipped to ``+/-max_gain_db``; a filter whose fitted gain falls
    below ``min_gain_db`` ends the search rather than being added. Greedy
    fitting is not globally optimal - it does not revisit earlier filters -
    but it is deterministic, fast, and produces the kind of filter list a
    person would have chosen by hand.
    """
    grid = np.asarray(COMMON_GRID, dtype=np.float64)
    delta_db = np.asarray(delta.delta_db, dtype=np.float64)
    if delta_db.shape != grid.shape or not np.array_equal(
        np.asarray(delta.freqs, dtype=np.float64), grid
    ):
        delta_db = resample_to_common(delta.freqs, delta.delta_db)

    correction = -delta_db
    if smoothing_fraction is not None and int(smoothing_fraction) > 0:
        _, correction = smooth_fractional_octave(grid, correction, fraction=int(smoothing_fraction))
        correction = np.asarray(correction, dtype=np.float64)

    residual = correction.copy()
    current_rms = _rms(residual)
    filters: list[PeakingFilter] = []

    for _ in range(int(max_filters)):
        if current_rms <= 0.0:
            break
        candidate, response, new_rms = _fit_one_filter(residual, grid, q_range, float(max_gain_db))
        if abs(candidate.gain_db) < float(min_gain_db):
            break
        if current_rms - new_rms < EQ_MIN_IMPROVEMENT_DB:
            break
        filters.append(candidate)
        residual = residual - response
        current_rms = new_rms

    total = eq_response_db(filters, grid)
    residual_delta = delta_db + total
    return EqSuggestion(
        filters=filters,
        preamp_db=compute_preamp_db(filters, grid) if preamp else 0.0,
        residual_rms_db=_rms(residual_delta),
        residual_delta_db=residual_delta,
    )


def format_eq_apo(suggestion: EqSuggestion) -> str:
    """Equalizer APO / AutoEQ preset text.

    ``Preamp: -x.x dB`` followed by one ``Filter n: ON PK ...`` line each, in
    the field order and precision Equalizer APO's own parser expects.
    """
    preamp = float(suggestion.preamp_db)
    if preamp == 0.0:
        preamp = 0.0  # collapse -0.0
    lines = [f"Preamp: {preamp:.1f} dB"]
    for index, item in enumerate(suggestion.filters, start=1):
        lines.append(
            f"Filter {index}: ON PK Fc {item.freq_hz:.0f} Hz "
            f"Gain {item.gain_db:.1f} dB Q {item.q:.2f}"
        )
    return "\n".join(lines)


def format_eq_table(suggestion: EqSuggestion) -> str:
    """Human-readable table of the suggested filters."""
    lines = [
        f"Preamp {suggestion.preamp_db:+.1f} dB    "
        f"residual RMS {suggestion.residual_rms_db:.2f} dB",
        "  #   Freq (Hz)   Gain (dB)      Q",
    ]
    if not suggestion.filters:
        lines.append("  (no filters suggested)")
        return "\n".join(lines)
    for index, item in enumerate(suggestion.filters, start=1):
        lines.append(f"{index:>3}   {item.freq_hz:>9.0f}   {item.gain_db:>+9.1f}   {item.q:>4.2f}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# A/B reference layers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReferenceLayer:
    """One curve held alongside the live measurement for A/B comparison."""

    name: str
    freqs: np.ndarray
    mag_db: np.ndarray
    source_path: Path | None = None
    color_hint: str | None = None


def _normalized_at_ref(freqs: np.ndarray, mag_db: np.ndarray) -> np.ndarray:
    return mag_db - value_at(freqs, mag_db, F_REF, log_x=True)


def load_reference_from_txt(
    path: str | Path,
    *,
    name: str | None = None,
    color_hint: str | None = None,
    normalize: bool = True,
) -> ReferenceLayer:
    """Load an exported TXT curve as an A/B reference layer.

    ``normalize`` anchors the curve at 1 kHz, which is what makes two layers
    from different sessions comparable; pass ``False`` to keep whatever
    absolute levels the file carried.
    """
    file_path = Path(path)
    freqs, mag_db, _warnings = _raw_txt_curve(file_path)
    if normalize:
        mag_db = _normalized_at_ref(freqs, mag_db)
    return ReferenceLayer(
        name=name or file_path.stem,
        freqs=freqs,
        mag_db=mag_db,
        source_path=file_path,
        color_hint=color_hint,
    )


def _raw_txt_curve(path: Path) -> tuple[np.ndarray, np.ndarray, list[str]]:
    from dms.curator.parser import parse_measurement_txt

    curve = parse_measurement_txt(path)
    values = curve.median_db if curve.kind == "variation" else curve.mag_db
    freqs, mag_db = _as_sorted_pair(curve.freqs, values)
    return freqs, mag_db, list(curve.warnings)


def _coerce_curve(value: Any) -> tuple[np.ndarray, np.ndarray] | None:
    """Pull ``(freqs, mag_db)`` out of whatever a session hands back."""
    freqs: Any = None
    mags: Any = None
    if isinstance(value, dict):
        freqs, mags = value.get("freqs"), value.get("mag_db")
    elif hasattr(value, "freqs") and hasattr(value, "mag_db"):
        freqs, mags = value.freqs, value.mag_db
    elif isinstance(value, (tuple, list)) and len(value) >= 2:
        freqs, mags = value[0], value[1]
    if freqs is None or mags is None:
        return None
    try:
        pair = _as_sorted_pair(freqs, mags)
    except (ValueError, TypeError):
        return None
    return pair if pair[0].size >= 2 else None


def _session_curves_via_module(path: Path) -> list[tuple[np.ndarray, np.ndarray]]:
    """Preferred path: let ``dms.measure_session`` interpret the file.

    Imported lazily and inside a ``try`` because that module is younger than
    this one and may not yet exist in a given working tree; the JSON fallback
    below reads the same fields directly.
    """
    from dms.measure_session import MeasureSession

    with open(path, encoding="utf-8") as handle:
        session = MeasureSession.from_dict(json.load(handle))
    curves = [_coerce_curve(item) for item in session.curves()]
    return [curve for curve in curves if curve is not None]


def _session_curves_from_json(payload: dict[str, Any]) -> list[tuple[np.ndarray, np.ndarray]]:
    entries: list[Any] = []
    for key in ("sweeps", "curves"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            entries = value
            break
    curves = [_coerce_curve(item) for item in entries]
    return [curve for curve in curves if curve is not None]


def load_reference_from_measure_session(
    path: str | Path,
    *,
    name: str | None = None,
    color_hint: str | None = None,
) -> ReferenceLayer:
    """Load a saved Measure session as one averaged A/B reference layer.

    The kept sweeps are averaged in power on :data:`COMMON_GRID` and
    normalized at 1 kHz - the same average the Measure workspace plots.
    """
    file_path = Path(path)
    with open(file_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{file_path.name} is not a Measure session file.")

    try:
        curves = _session_curves_via_module(file_path)
    except Exception:
        curves = []
    if not curves:
        curves = _session_curves_from_json(payload)
    if not curves:
        raise ValueError(f"{file_path.name} holds no usable curves.")

    freqs, mag_db = compute_rms_average(curves)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    default_name = " ".join(
        str(metadata.get(key, "")).strip()
        for key in ("brand", "model")
        if str(metadata.get(key, "")).strip()
    )
    return ReferenceLayer(
        name=name or default_name or file_path.stem,
        freqs=freqs,
        mag_db=_normalized_at_ref(freqs, mag_db),
        source_path=file_path,
        color_hint=color_hint,
    )
