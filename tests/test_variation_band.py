"""``percentile_band`` must reproduce the per-percentile computation it replaced."""

from __future__ import annotations

import numpy as np

from dms.processing import (
    VariationBand,
    compute_rms_average,
    log_grid,
    percentile_band,
    smooth_fractional_octave,
)


class _ShiftHRTF:
    def apply(self, freqs: np.ndarray, mag_db: np.ndarray) -> np.ndarray:
        return mag_db - 0.5 * np.log10(freqs)


def _curves() -> list[tuple[np.ndarray, np.ndarray]]:
    rng = np.random.default_rng(7)
    curves = []
    for index in range(5):
        freqs = np.geomspace(15.0, 22000.0, 300 + 40 * index)
        mag = 90.0 + 3.0 * np.sin(np.log10(freqs) * (2 + index)) + rng.normal(0, 0.8, len(freqs))
        curves.append((freqs, mag))
    return curves


def _old_band(curves, grid, smoothing, hrtf) -> tuple[np.ndarray, ...]:
    rows = []
    for freqs, mag in curves:
        values = np.interp(grid, freqs, mag)
        if hrtf is not None:
            values = hrtf.apply(grid, values)
        if smoothing is not None:
            _, values = smooth_fractional_octave(grid, values, fraction=smoothing)
        rows.append(values)
    mat = np.vstack(rows)
    return (
        grid,
        np.percentile(mat, 10, axis=0),
        np.percentile(mat, 25, axis=0),
        np.percentile(mat, 50, axis=0),
        np.percentile(mat, 75, axis=0),
        np.percentile(mat, 90, axis=0),
    )


def test_percentile_band_matches_the_old_computation() -> None:
    curves = _curves()
    measure_grid = compute_rms_average(curves, n_points=600)[0]
    for grid, smoothing, hrtf in (
        (None, 48, None),
        (measure_grid, 12, _ShiftHRTF()),
        (measure_grid, None, None),
    ):
        band = percentile_band(curves, grid=grid, smoothing=smoothing, hrtf=hrtf)
        expected = _old_band(curves, log_grid() if grid is None else grid, smoothing, hrtf)
        assert isinstance(band, VariationBand)
        for field, reference in zip(VariationBand._fields, expected, strict=True):
            assert np.allclose(getattr(band, field), reference, rtol=0, atol=1e-9), field
