import numpy as np
import re
from pathlib import Path
from scipy.interpolate import interp1d
from dms.measurement_txt import load_two_column_txt_curve


class HRTFCurve:
    def __init__(self, path: str) -> None:
        self.path = path
        self.name = Path(path).stem
        self.freqs, columns = _load_hrtf_data(path)
        self.is_variation = len(columns) == 5
        self.mags = columns[2] if self.is_variation else columns[0]
        self._interp = interp1d(
            self.freqs, self.mags, kind="linear", bounds_error=False, fill_value=0.0
        )
        self._variation_interps: tuple[interp1d, interp1d, interp1d, interp1d, interp1d] | None = None
        if self.is_variation:
            self._variation_interps = tuple(
                interp1d(
                    self.freqs,
                    values,
                    kind="linear",
                    bounds_error=False,
                    fill_value=0.0,
                )
                for values in columns
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

    def apply(
        self, freqs_hz: np.ndarray, mag_db: np.ndarray, invert: bool = False
    ) -> np.ndarray:
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
        """Apply the compensation spread to an existing variation envelope."""
        variation = self.evaluate_variation(freqs_hz)
        if variation is None:
            correction = self.evaluate(freqs_hz)
            return (
                p10_db - correction,
                p25_db - correction,
                median_db - correction,
                p75_db - correction,
                p90_db - correction,
            )
        comp_p10, comp_p25, comp_median, comp_p75, comp_p90 = variation
        return (
            p10_db - comp_p90,
            p25_db - comp_p75,
            median_db - comp_median,
            p75_db - comp_p25,
            p90_db - comp_p10,
        )


def _load_hrtf_data(path: str) -> tuple[np.ndarray, tuple[np.ndarray, ...]]:
    rows: list[list[float]] = []
    with open(path, "r", encoding="utf-8") as handle:
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
