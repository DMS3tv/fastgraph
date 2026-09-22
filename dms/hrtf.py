from pathlib import Path

import numpy as np
from scipy.interpolate import interp1d

from dms.curator.parser import parse_measurement_txt
from dms.processing import _Z_P75, _Z_P90, sigma_from_percentiles


def _edge_held_interp(freqs: np.ndarray, values: np.ndarray) -> interp1d:
    """Linear interpolator that holds the first/last value outside the file range.

    Filling with 0 dB instead put a step at the edge of every HRTF file, which
    showed up as a kink in the compensated curve.
    """
    return interp1d(
        freqs,
        values,
        kind="linear",
        bounds_error=False,
        fill_value=(float(values[0]), float(values[-1])),
    )


class HRTFCurve:
    def __init__(self, path: str) -> None:
        self.path = path
        self.name = Path(path).stem
        self.freqs, columns = _load_hrtf_data(path)
        self.is_variation = len(columns) == 5
        self.mags = columns[2] if self.is_variation else columns[0]
        self._interp = _edge_held_interp(self.freqs, self.mags)
        self._variation_interps: tuple[interp1d, interp1d, interp1d, interp1d, interp1d] | None = (
            None
        )
        if self.is_variation:
            self._variation_interps = tuple(
                _edge_held_interp(self.freqs, values) for values in columns
            )

    def evaluate(self, freqs_hz: np.ndarray) -> np.ndarray:
        """Return the HRTF line, or the median for a variation HRTF."""
        return self._interp(freqs_hz)

    def evaluate_variation(
        self,
        freqs_hz: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
        """Return P10, P25, median, P75, and P90 at requested frequencies."""
        if self._variation_interps is None:
            return None
        return tuple(interp(freqs_hz) for interp in self._variation_interps)

    def apply(self, freqs_hz: np.ndarray, mag_db: np.ndarray, invert: bool = False) -> np.ndarray:
        """
        Default: corrected = raw - hrtf  (invert=False)
        Inverted: corrected = raw + hrtf  (invert=True)
        """
        hrtf_vals = self.evaluate(freqs_hz)
        if invert:
            return mag_db + hrtf_vals
        return mag_db - hrtf_vals

    def apply_to_magnitude_as_variation(
        self,
        freqs_hz: np.ndarray,
        mag_db: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Apply a variation HRTF to one FR line and return five percentiles."""
        variation = self.evaluate_variation(freqs_hz)
        if variation is None:
            corrected = self.apply(freqs_hz, mag_db)
            return (corrected, corrected, corrected, corrected, corrected)
        p10, p25, median, p75, p90 = variation
        return (
            mag_db - p90,
            mag_db - p75,
            mag_db - median,
            mag_db - p25,
            mag_db - p10,
        )

    def apply_to_variation(
        self,
        freqs_hz: np.ndarray,
        p10_db: np.ndarray,
        p25_db: np.ndarray,
        median_db: np.ndarray,
        p75_db: np.ndarray,
        p90_db: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Apply the compensation spread to an existing variation envelope.

        The measurement spread and the population spread are treated as
        independent and their variances are added in quadrature, which is
        what two unrelated sources of variation actually do.
        """
        variation = self.evaluate_variation(freqs_hz)
        if variation is None:
            # A mono HRTF has no spread of its own.
            correction = self.evaluate(freqs_hz)
            return (
                p10_db - correction,
                p25_db - correction,
                median_db - correction,
                p75_db - correction,
                p90_db - correction,
            )
        comp_p10, comp_p25, comp_median, comp_p75, comp_p90 = variation
        sigma_meas = sigma_from_percentiles(p10_db, p25_db, p75_db, p90_db)
        sigma_hrtf = sigma_from_percentiles(comp_p10, comp_p25, comp_p75, comp_p90)
        sigma = np.sqrt(np.square(sigma_meas) + np.square(sigma_hrtf))
        median_c = np.asarray(median_db, dtype=float) - np.asarray(comp_median, dtype=float)
        return (
            median_c - _Z_P90 * sigma,
            median_c - _Z_P75 * sigma,
            median_c,
            median_c + _Z_P75 * sigma,
            median_c + _Z_P90 * sigma,
        )


def _load_hrtf_data(path: str) -> tuple[np.ndarray, tuple[np.ndarray, ...]]:
    curve = parse_measurement_txt(path)
    if curve.kind == "variation":
        return curve.freqs, (
            curve.p10_db,
            curve.p25_db,
            curve.median_db,
            curve.p75_db,
            curve.p90_db,
        )
    return curve.freqs, (curve.mag_db,)
