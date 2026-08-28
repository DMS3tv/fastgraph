"""Pure data and processing helpers for paired Measure captures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.interpolate import interp1d

from dms.processing import compute_rms_average


Curve = tuple[np.ndarray, np.ndarray]
Variation = tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
]


@dataclass(frozen=True)
class TwoChannelCurvePair:
    """One kept L/R measurement pair and its per-channel diagnostics."""

    channel_1: Curve
    channel_2: Curve
    channel_1_diagnostics: Any = None
    channel_2_diagnostics: Any = None


def shared_normalize_pair_at_1khz(
    first_freqs: np.ndarray,
    first_mag_db: np.ndarray,
    second_freqs: np.ndarray,
    second_mag_db: np.ndarray,
    *,
    f_ref: float = 1000.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Apply one power-mean reference offset to both channel magnitudes."""

    first_ref = _value_at(first_freqs, first_mag_db, f_ref)
    second_ref = _value_at(second_freqs, second_mag_db, f_ref)
    reference_power = (10.0 ** (first_ref / 10.0) + 10.0 ** (second_ref / 10.0)) / 2.0
    reference_db = 10.0 * np.log10(max(reference_power, 1e-30))
    return (
        np.asarray(first_mag_db, dtype=float) - reference_db,
        np.asarray(second_mag_db, dtype=float) - reference_db,
    )


def combine_curves_power(
    curves: list[Curve],
    *,
    n_points: int = 1200,
) -> Curve | None:
    """Return the power mean of the supplied curves without a new offset."""

    if not curves:
        return None
    return compute_rms_average(
        curves,
        n_points=n_points,
        normalize_ref=False,
    )


def channel_curves(
    pairs: list[TwoChannelCurvePair],
    channel: int,
) -> list[Curve]:
    if channel == 1:
        return [pair.channel_1 for pair in pairs]
    if channel == 2:
        return [pair.channel_2 for pair in pairs]
    raise ValueError("Channel must be 1 or 2.")


def combined_pair_curves(
    pairs: list[TwoChannelCurvePair],
    *,
    n_points: int = 1200,
) -> list[Curve]:
    combined: list[Curve] = []
    for pair in pairs:
        curve = combine_curves_power(
            [pair.channel_1, pair.channel_2],
            n_points=n_points,
        )
        if curve is not None:
            combined.append(curve)
    return combined


def curve_label_for_selection(selection: str) -> str:
    normalized = str(selection or "").strip().lower()
    if normalized == "channel_1":
        return "L"
    if normalized == "channel_2":
        return "R"
    return "BOTH"


def _value_at(freqs: np.ndarray, values: np.ndarray, frequency: float) -> float:
    source_freqs = np.asarray(freqs, dtype=float)
    source_values = np.asarray(values, dtype=float)
    if len(source_freqs) < 2 or len(source_freqs) != len(source_values):
        raise ValueError("Channel response data is incomplete.")
    if frequency < source_freqs[0] or frequency > source_freqs[-1]:
        raise ValueError(f"Reference frequency {frequency:g} Hz is outside the response data.")
    return float(interp1d(source_freqs, source_values, kind="linear")(frequency))
