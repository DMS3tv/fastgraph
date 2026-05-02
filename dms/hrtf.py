import numpy as np
from pathlib import Path
from typing import Optional
from scipy.interpolate import interp1d


class HRTFCurve:
    def __init__(self, path: str) -> None:
        self.path = path
        self.name = Path(path).stem
        freqs, mags = _load_hrtf_file(path)
        self.freqs = freqs
        self.mags = mags
        self._interp = interp1d(
            freqs, mags, kind="linear", bounds_error=False, fill_value=0.0
        )

    def evaluate(self, freqs_hz: np.ndarray) -> np.ndarray:
        """Return HRTF dB values at requested frequencies."""
        return self._interp(freqs_hz)

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


def _load_hrtf_file(path: str) -> tuple[np.ndarray, np.ndarray]:
    freqs, mags = [], []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("*"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                freqs.append(float(parts[0]))
                mags.append(float(parts[1]))
            except ValueError:
                continue  # skip header-like lines
    if len(freqs) < 2:
        raise ValueError(f"HRTF file '{path}' has fewer than 2 valid data rows.")
    return np.array(freqs), np.array(mags)