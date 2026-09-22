"""np.interp-based helpers against the scipy interp1d code they replaced."""

import numpy as np
import pytest
from scipy.interpolate import interp1d

from dms.processing import normalize_at_1khz, value_at


def _curve(seed: int = 7, points: int = 481) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    freqs = np.geomspace(20.0, 20000.0, points) * (1.0 + rng.uniform(-1e-3, 1e-3, points))
    freqs.sort()
    values = np.cumsum(rng.normal(0.0, 0.4, points)) + 6.0 * np.sin(np.log(freqs))
    return freqs, values


def _probe_points(freqs: np.ndarray) -> np.ndarray:
    rng = np.random.default_rng(11)
    inside = np.exp(rng.uniform(np.log(freqs[0]), np.log(freqs[-1]), 200))
    return np.concatenate(([1000.0, freqs[0], freqs[-1], freqs[17]], inside))


def test_value_at_matches_interp1d() -> None:
    """Replaces ``two_channel._value_at`` and the interp in ``normalize_at_1khz``."""
    freqs, values = _curve()
    reference = interp1d(freqs, values, kind="linear")
    for f in _probe_points(freqs):
        assert value_at(freqs, values, f) == pytest.approx(float(reference(f)), abs=1e-9)
    np.testing.assert_allclose(
        normalize_at_1khz(freqs, values), values - float(reference(1000.0)), rtol=0, atol=1e-9
    )


def test_value_at_log_x_matches_the_comparison_helper() -> None:
    freqs, values = _curve(seed=3)
    for f in _probe_points(freqs):
        expected = float(np.interp(np.log10(f), np.log10(freqs), values))
        assert value_at(freqs, values, f, log_x=True) == expected


def test_value_at_matches_the_curator_offset() -> None:
    freqs, values = _curve(seed=5)
    assert value_at(freqs, values, 1000.0) == float(np.interp(1000.0, freqs, values))


def test_value_at_rejects_frequencies_outside_the_data() -> None:
    freqs, values = _curve()
    with pytest.raises(ValueError, match="out of data range"):
        value_at(freqs, values, 10.0)
    with pytest.raises(ValueError, match="out of data range"):
        value_at(freqs, values, 30000.0, log_x=True)
    with pytest.raises(ValueError, match="incomplete"):
        value_at(freqs[:1], values[:1], freqs[0])
