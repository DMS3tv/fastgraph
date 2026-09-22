import re
from pathlib import Path

import numpy as np
from scipy.interpolate import interp1d

from dms.measurement_txt import load_two_column_txt_curve

#: Standard-normal quantiles for the 90th and 75th percentiles. A population
#: HRTF's percentile columns are converted to a sigma through these, so the
#: measurement spread and the population spread can be added in quadrature.
_Z_P90 = 1.2815515655446004
_Z_P75 = 0.6744897501960817


def sigma_from_percentiles(
    p10: np.ndarray,
    p25: np.ndarray,
    p75: np.ndarray,
    p90: np.ndarray,
) -> np.ndarray:
    """Estimate the standard deviation behind a set of percentile columns.

    Both the 10/90 and the 25/75 pairs give an estimate of sigma for a normal
    distribution; averaging them uses all four columns and is less sensitive to
    one noisy tail than either alone.
    """
    outer = (np.asarray(p90, dtype=float) - np.asarray(p10, dtype=float)) / (2.0 * _Z_P90)
    inner = (np.asarray(p75, dtype=float) - np.asarray(p25, dtype=float)) / (2.0 * _Z_P75)
    return 0.5 * (outer + inner)


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
        combination: str = "independent",
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Apply the compensation spread to an existing variation envelope.

        ``combination="independent"`` treats the measurement spread and the
        population spread as independent and adds their variances in
        quadrature, which is what two unrelated sources of variation actually
        do. ``combination="worst_case"`` reproduces Fastgraph's historical
        pairing of opposing percentiles (p10 against comp_p90), which assumes
        the two spreads always conspire and therefore reads much wider.
        """
        variation = self.evaluate_variation(freqs_hz)
        if variation is None:
            # A mono HRTF has no spread of its own; both modes are identical.
            correction = self.evaluate(freqs_hz)
            return (
                p10_db - correction,
                p25_db - correction,
                median_db - correction,
                p75_db - correction,
                p90_db - correction,
            )
        comp_p10, comp_p25, comp_median, comp_p75, comp_p90 = variation
        if str(combination) == "worst_case":
            return (
                p10_db - comp_p90,
                p25_db - comp_p75,
                median_db - comp_median,
                p75_db - comp_p25,
                p90_db - comp_p10,
            )

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
    rows: list[list[float]] = []
    with open(path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("*"):
                continue
            parts = [part for part in re.split(r"[\s,]+", line) if part]
            try:
                values = [float(part) for part in parts]
            except ValueError:
                continue
            if len(values) >= 2 and all(np.isfinite(value) for value in values):
                rows.append(values)

    if rows and max(len(row) for row in rows) >= 6:
        data = np.asarray([row[:6] for row in rows if len(row) >= 6], dtype=float)
        if data.shape[0] < 2:
            raise ValueError(f"HRTF file '{path}' has fewer than 2 complete variation rows.")
        data = data[data[:, 0] > 0.0]
        if data.shape[0] < 2:
            raise ValueError(f"HRTF file '{path}' has fewer than 2 positive frequency rows.")
        data = data[np.argsort(data[:, 0], kind="stable")]
        return data[:, 0], tuple(data[:, index] for index in range(1, 6))

    freqs, mags = load_two_column_txt_curve(path, label="HRTF")
    return freqs, (mags,)
