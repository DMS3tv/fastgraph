"""Log-grid band averaging: the replacement for point-sampled downsampling."""

import numpy as np
import pytest

from dms.processing import downsample_to_log_points, resample_log_band_average


def _dense_grid(step_hz: float = 0.5) -> np.ndarray:
    return np.arange(20.0, 20000.0 + step_hz, step_hz)


def test_band_average_matches_interpolation_for_smooth_curve() -> None:
    freqs = _dense_grid()
    mag_db = 6.0 * np.log10(freqs / 1000.0) - 2.0 * np.sin(np.log(freqs))

    _, averaged = resample_log_band_average(freqs, mag_db, normalize_ref=False)
    _, sampled = downsample_to_log_points(
        freqs, mag_db, normalize_ref=False, band_average=False
    )

    # A cell is ~1.2 % wide, so a smooth curve cannot move inside it.
    assert np.max(np.abs(averaged - sampled)) < 0.05


def test_band_average_suppresses_single_noisy_bin() -> None:
    freqs = _dense_grid()
    target, _ = resample_log_band_average(freqs, np.zeros_like(freqs))
    spike_hz = float(target[400])
    spike_bin = int(np.argmin(np.abs(freqs - spike_hz)))

    mag_db = np.zeros_like(freqs)
    mag_db[spike_bin] = 40.0

    _, averaged = resample_log_band_average(freqs, mag_db, normalize_ref=False)
    _, sampled = downsample_to_log_points(
        freqs, mag_db, normalize_ref=False, band_average=False
    )

    # Point sampling lands on the bad bin and reports it at full height.
    assert np.max(sampled) > 30.0
    # Sharing its power with the rest of the cell buries it.
    assert np.max(averaged) < 25.0
    assert np.max(np.abs(np.delete(averaged, np.argmax(averaged)))) < 1e-9


def test_band_average_falls_back_when_cell_has_one_bin() -> None:
    freqs = np.array([100.0, 1000.0, 10000.0])
    mag_db = np.array([2.0, 4.0, 6.0])

    target_avg, averaged = downsample_to_log_points(freqs, mag_db, normalize_ref=False)
    target_int, sampled = downsample_to_log_points(
        freqs, mag_db, normalize_ref=False, band_average=False
    )

    # Every cell of a three-point curve is too sparse to average, so the
    # historical interpolation is used verbatim.
    np.testing.assert_array_equal(target_avg, target_int)
    np.testing.assert_array_equal(averaged, sampled)


def test_grid_keeps_600_points_and_exact_1khz() -> None:
    freqs = _dense_grid()
    mag_db = np.linspace(-5.0, 5.0, freqs.size)

    target, out = resample_log_band_average(freqs, mag_db)

    assert target.size == out.size == 600
    assert np.count_nonzero(target == 1000.0) == 1
    assert np.all(np.diff(target) > 0.0)
    idx_ref = int(np.argmin(np.abs(target - 1000.0)))
    assert out[idx_ref] == pytest.approx(0.0, abs=1e-12)

    legacy_target, _ = downsample_to_log_points(freqs, mag_db, band_average=False)
    np.testing.assert_array_equal(target, legacy_target)


def test_band_average_is_power_mean_not_db_mean() -> None:
    # Two bins land in the middle cell: one at 0 dB and one at 20 dB.
    freqs = np.array([100.0, 500.0, 2000.0, 10000.0])
    mag_db = np.array([0.0, 0.0, 20.0, 0.0])

    target, out = resample_log_band_average(
        freqs, mag_db, n_points=3, f_min=100.0, f_max=10000.0, normalize_ref=False
    )

    assert target[1] == pytest.approx(1000.0)
    power_mean = 10.0 * np.log10((10 ** 0.0 + 10 ** 2.0) / 2.0)
    assert power_mean == pytest.approx(17.0329, abs=1e-3)
    assert out[1] == pytest.approx(power_mean, abs=1e-9)
    # The arithmetic mean of the dB values would have said 10 dB.
    assert abs(out[1] - 10.0) > 5.0
