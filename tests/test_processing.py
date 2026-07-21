import numpy as np
import pytest

from dms.processing import compute_rms_average


def test_compute_rms_average_raises_value_error_on_degenerate_curve() -> None:
    curves = [(np.array([]), np.array([]))]
    with pytest.raises(ValueError):
        compute_rms_average(curves)


def test_compute_rms_average_raises_value_error_on_single_point_curve() -> None:
    curves = [(np.array([1000.0]), np.array([1.0]))]
    with pytest.raises(ValueError):
        compute_rms_average(curves)


def test_compute_rms_average_works_normally_with_two_good_curves() -> None:
    curves = [
        (np.array([100.0, 1000.0, 10000.0]), np.array([1.0, 0.0, -1.0])),
        (np.array([100.0, 1000.0, 10000.0]), np.array([-1.0, 0.0, 1.0])),
    ]

    freqs, mag_db = compute_rms_average(curves, n_points=200)

    assert freqs.shape == (200,)
    assert mag_db.shape == (200,)
    assert np.isfinite(mag_db).all()
