import os
import numpy as np
from pathlib import Path
from typing import Optional
from scipy.interpolate import interp1d
from dms.measurement_txt import load_two_column_txt_curve


class HRTFCurve:
    def __init__(self, path: str) -> None:
        self.path = path
        self.name = Path(path).stem
        freqs, mags = _load_hrtf_file(path)
        self.freqs = freqs
        self.mags = mags
        # freqs/mags are sorted ascending by load_two_column_txt_curve, so
        # freqs[0]/freqs[-1] and mags[0]/mags[-1] are the low/high edges of the
        # file's coverage. Edge-hold out-of-range frequencies (matching
        # processing.py's compute_rms_average) instead of snapping to 0 dB,
        # which would otherwise create a step discontinuity at the file's
        # coverage boundary.
        self._interp = interp1d(
            freqs, mags, kind="linear", bounds_error=False,
            fill_value=(mags[0], mags[-1]),
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
    return load_two_column_txt_curve(path, label="HRTF")


# Module-level cache of parsed HRTFCurve instances, keyed by (resolved path, mtime_ns).
# Avoids re-reading/re-parsing the HRTF file and rebuilding its interp1d on every
# HRTFCurve(...) construction, which matters for hot paths (per-redraw plotting)
# where the same HRTF file is requested many times in quick succession.
_hrtf_curve_cache: dict[tuple[str, int], "HRTFCurve"] = {}


def get_hrtf_curve(path) -> "HRTFCurve":
    """Return a cached HRTFCurve for ``path``, constructing/parsing only when needed.

    The cache key includes the file's mtime, so editing the file on disk (which
    bumps mtime) transparently invalidates the stale entry rather than serving
    out-of-date data. Missing files / stat failures are not cached and simply
    propagate whatever exception the underlying construction raises today.
    """
    resolved = str(Path(path).resolve())
    mtime_ns = os.stat(resolved).st_mtime_ns
    key = (resolved, mtime_ns)
    cached = _hrtf_curve_cache.get(key)
    if cached is not None:
        return cached

    curve = HRTFCurve(str(path))

    # Evict any stale entries for this same resolved path (older mtimes) so the
    # cache doesn't grow unbounded as a file is edited repeatedly over time.
    for stale_key in [k for k in _hrtf_curve_cache if k[0] == resolved]:
        del _hrtf_curve_cache[stale_key]

    _hrtf_curve_cache[key] = curve
    return curve
