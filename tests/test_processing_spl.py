"""Absolute SPL calibration offset."""

import numpy as np
import pytest

from dms.processing import absolute_spl_offset_db

#: Pascals per unit of full-scale amplitude that put a full-scale sine at
#: 1 Pa RMS, i.e. at the 94 dB SPL calibration reference.
SENSITIVITY_1PA = np.sqrt(2.0)


def test_absolute_spl_offset_matches_calibration_reference() -> None:
    offset = absolute_spl_offset_db(
        sensitivity_pa_per_fs=SENSITIVITY_1PA,
        output_level_db=0.0,
        sweep_peak_fs=1.0,
    )

    # 1 Pa RMS against the 20 uPa reference is 94 dB SPL.
    assert offset == pytest.approx(20.0 * np.log10(1.0 / 20e-6))
    assert offset == pytest.approx(93.9794, abs=1e-3)

    # Halving the playback level takes exactly 6.02 dB off the result.
    quieter = absolute_spl_offset_db(
        sensitivity_pa_per_fs=SENSITIVITY_1PA,
        output_level_db=-6.0206,
        sweep_peak_fs=1.0,
    )
    assert offset - quieter == pytest.approx(6.0206, abs=1e-3)

    # A half-scale sweep is the same 6 dB down.
    half_scale = absolute_spl_offset_db(
        sensitivity_pa_per_fs=SENSITIVITY_1PA,
        output_level_db=0.0,
        sweep_peak_fs=0.5,
    )
    assert offset - half_scale == pytest.approx(6.0206, abs=1e-3)

    for bad in ({"sensitivity_pa_per_fs": 0.0}, {"sweep_peak_fs": 0.0}):
        kwargs = {
            "sensitivity_pa_per_fs": SENSITIVITY_1PA,
            "output_level_db": 0.0,
            "sweep_peak_fs": 1.0,
        }
        kwargs.update(bad)
        with pytest.raises(ValueError):
            absolute_spl_offset_db(**kwargs)


def test_spl_offset_is_pure_additive() -> None:
    freqs = np.logspace(np.log10(20.0), np.log10(20000.0), 600)
    curve_db = 6.0 * np.log10(freqs / 1000.0) - 2.0 * np.sin(np.log(freqs))
    curve_db -= curve_db[int(np.argmin(np.abs(freqs - 1000.0)))]

    offset = absolute_spl_offset_db(
        sensitivity_pa_per_fs=SENSITIVITY_1PA,
        output_level_db=-12.0,
        sweep_peak_fs=1.0,
    )
    spl = curve_db + offset

    # Only the anchor moves; every shape metric is untouched.
    np.testing.assert_allclose(np.diff(spl), np.diff(curve_db), atol=1e-12)
    np.testing.assert_allclose(spl - np.mean(spl), curve_db - np.mean(curve_db),
                               atol=1e-12)
    assert spl[int(np.argmin(np.abs(freqs - 1000.0)))] == pytest.approx(offset)
    assert np.ptp(spl) == pytest.approx(np.ptp(curve_db))
