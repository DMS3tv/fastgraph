from pathlib import Path

import numpy as np

from dms.curator.parser import parse_measurement_txt
from dms.processing import _Z_P75, _Z_P90, VariationBand, sigma_from_percentiles


class HRTFCurve:
    def __init__(self, path: str) -> None:
        self.path = path
        self.name = Path(path).stem
        self.freqs, columns = _load_hrtf_data(path)
        self.is_variation = len(columns) == 5
        self.mags = columns[2] if self.is_variation else columns[0]
        self._variation = columns if self.is_variation else None

    def evaluate(self, freqs_hz: np.ndarray) -> np.ndarray:
        """Return the HRTF line, or the median for a variation HRTF.

        Outside the file range the first/last value is held (``np.interp``'s
        default). Filling with 0 dB instead put a step at the edge of every
        HRTF file, which showed up as a kink in the compensated curve.
        """
        return np.interp(freqs_hz, self.freqs, self.mags)

    def evaluate_variation(
        self,
        freqs_hz: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
        """Return P10, P25, median, P75, and P90 at requested frequencies."""
        if self._variation is None:
            return None
        return tuple(np.interp(freqs_hz, self.freqs, values) for values in self._variation)

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
    ) -> VariationBand:
        """Apply a variation HRTF to one FR line and return five percentiles."""
        variation = self.evaluate_variation(freqs_hz)
        if variation is None:
            corrected = self.apply(freqs_hz, mag_db)
            return VariationBand(freqs_hz, corrected, corrected, corrected, corrected, corrected)
        p10, p25, median, p75, p90 = variation
        return VariationBand(
            freqs_hz,
            mag_db - p90,
            mag_db - p75,
            mag_db - median,
            mag_db - p25,
            mag_db - p10,
        )

    def apply_to_variation(self, band: VariationBand) -> VariationBand:
        """Apply the compensation spread to an existing variation envelope.

        The measurement spread and the population spread are treated as
        independent and their variances are added in quadrature, which is
        what two unrelated sources of variation actually do.
        """
        variation = self.evaluate_variation(band.freqs)
        if variation is None:
            # A mono HRTF has no spread of its own.
            correction = self.evaluate(band.freqs)
            return VariationBand(
                band.freqs,
                band.p10 - correction,
                band.p25 - correction,
                band.median - correction,
                band.p75 - correction,
                band.p90 - correction,
            )
        comp_p10, comp_p25, comp_median, comp_p75, comp_p90 = variation
        sigma_meas = sigma_from_percentiles(band.p10, band.p25, band.p75, band.p90)
        sigma_hrtf = sigma_from_percentiles(comp_p10, comp_p25, comp_p75, comp_p90)
        sigma = np.sqrt(np.square(sigma_meas) + np.square(sigma_hrtf))
        median_c = np.asarray(band.median, dtype=float) - np.asarray(comp_median, dtype=float)
        return VariationBand(
            band.freqs,
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
