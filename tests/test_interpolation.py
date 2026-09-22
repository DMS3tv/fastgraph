"""np.interp-based helpers against the scipy interp1d code they replaced."""

import numpy as np
import pytest
from scipy.interpolate import interp1d

from dms.processing import normalize_at_1khz, value_at


def _noisy_curve(seed: int = 7, points: int = 481) -> tuple[np.ndarray, np.ndarray]:
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
    freqs, values = _noisy_curve()
    reference = interp1d(freqs, values, kind="linear")
    for f in _probe_points(freqs):
        assert value_at(freqs, values, f) == pytest.approx(float(reference(f)), abs=1e-9)
    np.testing.assert_allclose(
        normalize_at_1khz(freqs, values), values - float(reference(1000.0)), rtol=0, atol=1e-9
    )


def test_value_at_log_x_matches_the_comparison_helper() -> None:
    freqs, values = _noisy_curve(seed=3)
    for f in _probe_points(freqs):
        expected = float(np.interp(np.log10(f), np.log10(freqs), values))
        assert value_at(freqs, values, f, log_x=True) == expected


def test_value_at_matches_the_curator_offset() -> None:
    freqs, values = _noisy_curve(seed=5)
    assert value_at(freqs, values, 1000.0) == float(np.interp(1000.0, freqs, values))


def test_value_at_rejects_frequencies_outside_the_data() -> None:
    freqs, values = _noisy_curve()
    with pytest.raises(ValueError, match="out of data range"):
        value_at(freqs, values, 10.0)
    with pytest.raises(ValueError, match="out of data range"):
        value_at(freqs, values, 30000.0, log_x=True)
    with pytest.raises(ValueError, match="incomplete"):
        value_at(freqs[:1], values[:1], freqs[0])


def _edge_held(freqs: np.ndarray, values: np.ndarray):
    return interp1d(
        freqs, values, kind="linear", bounds_error=False, fill_value=(values[0], values[-1])
    )


def test_processing_resampling_matches_interp1d() -> None:
    from dms.processing import compute_rms_average, log_grid, resample_log_band_average

    curves = [_noisy_curve(seed) for seed in (1, 2, 3)]
    grid = log_grid(1200, 10.0, 24000.0)
    power = sum(10.0 ** (_edge_held(f, v)(grid) / 10.0) for f, v in curves) / len(curves)
    expected = 10.0 * np.log10(power)
    _, got = compute_rms_average(curves, f_min=10.0, f_max=24000.0, normalize_ref=False)
    np.testing.assert_allclose(got, expected, rtol=0, atol=1e-12)

    freqs, values = curves[0]
    target, sampled = resample_log_band_average(
        freqs, values, n_points=300, f_min=10.0, f_max=24000.0, min_bins=10**6, normalize_ref=False
    )
    np.testing.assert_allclose(sampled, _edge_held(freqs, values)(target), rtol=0, atol=1e-12)


def test_hrtf_evaluation_matches_interp1d(tmp_path) -> None:
    from dms.hrtf import HRTFCurve

    freqs, median = _noisy_curve(seed=9, points=300)
    rows = np.column_stack([freqs, median - 3, median - 1, median, median + 1, median + 3])
    path = tmp_path / "population.txt"
    path.write_text("\n".join(" ".join(f"{v:.9f}" for v in row) for row in rows) + "\n")
    curve = HRTFCurve(str(path))
    probe = np.geomspace(5.0, 30000.0, 999)

    np.testing.assert_allclose(
        curve.evaluate(probe), _edge_held(curve.freqs, curve.mags)(probe), rtol=0, atol=1e-12
    )
    columns = (rows[:, 1], rows[:, 2], rows[:, 3], rows[:, 4], rows[:, 5])
    for got, values in zip(curve.evaluate_variation(probe), columns, strict=True):
        expected = _edge_held(curve.freqs, np.round(values, 9))(probe)
        np.testing.assert_allclose(got, expected, rtol=0, atol=1e-12)


def test_two_channel_reference_matches_interp1d() -> None:
    from dms.two_channel import shared_normalize_pair_at_1khz

    (f1, v1), (f2, v2) = _noisy_curve(seed=21), _noisy_curve(seed=22)
    first, second = shared_normalize_pair_at_1khz(f1, v1, f2, v2)
    refs = [float(interp1d(f, v, kind="linear")(1000.0)) for f, v in ((f1, v1), (f2, v2))]
    reference_db = 10.0 * np.log10(np.mean([10.0 ** (r / 10.0) for r in refs]))
    np.testing.assert_allclose(first, v1 - reference_db, rtol=0, atol=1e-12)
    np.testing.assert_allclose(second, v2 - reference_db, rtol=0, atol=1e-12)
