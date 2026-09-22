from pathlib import Path

import numpy as np
import pytest

from dms.curator.models import CurveData, LayerState
from dms.curator.transforms import (
    apply_layer_transform,
    can_combine_layers,
    combine_variation_layers,
    layer_sweep_count,
    normalization_offset_at_1khz_with_warning,
    visible_display_layers,
)
from dms.hrtf import _Z_P75, _Z_P90, HRTFCurve, sigma_from_percentiles


class _FakeHrtf:
    def __init__(self, path, name, freqs, mags) -> None:
        self.path = path
        self.name = name
        self.freqs = freqs
        self.mags = mags

    def evaluate(self, freqs):
        return np.interp(freqs, self.freqs, self.mags, left=0.0, right=0.0)


def test_normalization_offset_at_1khz_uses_fr_magnitude() -> None:
    curve = CurveData(
        kind="fr",
        freqs=np.array([100.0, 1000.0]),
        mag_db=np.array([-2.0, 5.0]),
    )

    assert normalization_offset_at_1khz_with_warning(curve) == (-5.0, None)


def test_normalization_offset_at_1khz_uses_variation_median() -> None:
    curve = CurveData(
        kind="variation",
        freqs=np.array([100.0, 1000.0]),
        p10_db=np.array([-5.0, -6.0]),
        p25_db=np.array([-4.0, -5.0]),
        median_db=np.array([-2.0, -3.0]),
        p75_db=np.array([0.0, -1.0]),
        p90_db=np.array([1.0, 0.0]),
    )

    assert normalization_offset_at_1khz_with_warning(curve) == (3.0, None)


def test_hrtf_and_offset_apply_to_fr_in_order() -> None:
    layer = LayerState(
        curve=CurveData(kind="fr", freqs=np.array([100.0, 1000.0]), mag_db=np.array([4.0, 8.0])),
        source_path=Path("curve.txt"),
        name="curve",
        vertical_offset_db=2.0,
        hrtf=_FakeHrtf(Path("hrtf.txt"), "hrtf", np.array([100.0, 1000.0]), np.array([1.0, 3.0])),
    )

    transformed = apply_layer_transform(layer)

    assert np.allclose(transformed.mag_db, [5.0, 7.0])


def test_hrtf_and_offset_apply_to_every_variation_column() -> None:
    freqs = np.array([100.0, 1000.0])
    layer = LayerState(
        curve=CurveData(
            kind="variation",
            freqs=freqs,
            p10_db=np.array([0.0, 10.0]),
            p25_db=np.array([1.0, 11.0]),
            median_db=np.array([2.0, 12.0]),
            p75_db=np.array([3.0, 13.0]),
            p90_db=np.array([4.0, 14.0]),
        ),
        source_path=Path("variation.txt"),
        name="variation",
        vertical_offset_db=-1.0,
        hrtf=_FakeHrtf(Path("hrtf.txt"), "hrtf", freqs, np.array([2.0, 4.0])),
    )

    transformed = apply_layer_transform(layer)

    assert np.allclose(transformed.p10_db, [-3.0, 5.0])
    assert np.allclose(transformed.p25_db, [-2.0, 6.0])
    assert np.allclose(transformed.median_db, [-1.0, 7.0])
    assert np.allclose(transformed.p75_db, [0.0, 8.0])
    assert np.allclose(transformed.p90_db, [1.0, 9.0])


def test_variation_hrtf_turns_one_fr_line_into_a_variation_band(
    tmp_path: Path,
) -> None:
    hrtf_path = tmp_path / "population.txt"
    hrtf_path.write_text(
        "100 1 2 3 4 5\n1000 10 20 30 40 50\n",
        encoding="utf-8",
    )
    layer = LayerState(
        curve=CurveData(
            kind="fr",
            freqs=np.array([100.0, 1000.0]),
            mag_db=np.array([10.0, 100.0]),
        ),
        source_path=Path("curve.txt"),
        name="curve",
        hrtf=HRTFCurve(str(hrtf_path)),
    )

    transformed = apply_layer_transform(layer)

    assert transformed.kind == "variation"
    assert transformed.mag_db is None
    assert np.allclose(transformed.p10_db, [5.0, 50.0])
    assert np.allclose(transformed.p25_db, [6.0, 60.0])
    assert np.allclose(transformed.median_db, [7.0, 70.0])
    assert np.allclose(transformed.p75_db, [8.0, 80.0])
    assert np.allclose(transformed.p90_db, [9.0, 90.0])


def test_variation_hrtf_expands_an_existing_variation_band(
    tmp_path: Path,
) -> None:
    hrtf_path = tmp_path / "population.txt"
    hrtf_path.write_text(
        "100 1 2 3 4 5\n1000 10 20 30 40 50\n",
        encoding="utf-8",
    )
    layer = LayerState(
        curve=CurveData(
            kind="variation",
            freqs=np.array([100.0, 1000.0]),
            p10_db=np.array([0.0, 0.0]),
            p25_db=np.array([2.0, 20.0]),
            median_db=np.array([4.0, 40.0]),
            p75_db=np.array([6.0, 60.0]),
            p90_db=np.array([8.0, 80.0]),
        ),
        source_path=Path("variation.txt"),
        name="variation",
        hrtf=HRTFCurve(str(hrtf_path)),
    )

    transformed = apply_layer_transform(layer)

    # The two spreads add in quadrature
    # around the compensated median.
    comp = np.array([[1.0, 2.0, 3.0, 4.0, 5.0], [10.0, 20.0, 30.0, 40.0, 50.0]])
    sigma_hrtf = sigma_from_percentiles(comp[:, 0], comp[:, 1], comp[:, 3], comp[:, 4])
    sigma_meas = sigma_from_percentiles(
        np.array([0.0, 0.0]),
        np.array([2.0, 20.0]),
        np.array([6.0, 60.0]),
        np.array([8.0, 80.0]),
    )
    sigma = np.sqrt(sigma_meas**2 + sigma_hrtf**2)
    expected_median = np.array([4.0, 40.0]) - comp[:, 2]

    assert transformed.kind == "variation"
    assert np.allclose(transformed.median_db, expected_median)
    assert np.allclose(transformed.p10_db, expected_median - _Z_P90 * sigma)
    assert np.allclose(transformed.p25_db, expected_median - _Z_P75 * sigma)
    assert np.allclose(transformed.p75_db, expected_median + _Z_P75 * sigma)
    assert np.allclose(transformed.p90_db, expected_median + _Z_P90 * sigma)


def test_hidden_layers_are_excluded_from_visible_display_layers() -> None:
    visible = LayerState(
        curve=CurveData(kind="fr", freqs=np.array([100.0, 1000.0]), mag_db=np.array([1.0, 2.0])),
        source_path=Path("visible.txt"),
        name="visible",
    )
    hidden = LayerState(
        curve=CurveData(kind="fr", freqs=np.array([100.0, 1000.0]), mag_db=np.array([3.0, 4.0])),
        source_path=Path("hidden.txt"),
        name="hidden",
        visible=False,
    )

    result = visible_display_layers([visible, hidden])

    assert [layer.name for layer, _curve in result] == ["visible"]


def test_visible_layers_apply_selected_smoothing_without_mutating_source() -> None:
    freqs = np.logspace(np.log10(20.0), np.log10(20000.0), 200)
    values = np.zeros(200)
    values[100] = 12.0
    layer = LayerState(
        curve=CurveData(kind="fr", freqs=freqs, mag_db=values.copy()),
        source_path=Path("spike.txt"),
        name="spike",
    )

    displayed = visible_display_layers([layer], smoothing_fraction=3)[0][1]

    assert displayed.mag_db is not None
    assert displayed.mag_db[100] < 12.0
    assert np.array_equal(layer.curve.mag_db, values)


def _variation_layer(
    *,
    name: str,
    freqs: np.ndarray | None = None,
    median_shift: float = 0.0,
    offset: float = 0.0,
    hrtf: object | None = None,
) -> LayerState:
    if freqs is None:
        freqs = np.array([100.0, 1000.0])
    base = np.linspace(0.0, 10.0, len(freqs)) + median_shift
    return LayerState(
        curve=CurveData(
            kind="variation",
            freqs=freqs,
            p10_db=base,
            p25_db=base + 1.0,
            median_db=base + 2.0,
            p75_db=base + 3.0,
            p90_db=base + 4.0,
        ),
        source_path=Path(f"{name}.txt"),
        name=name,
        vertical_offset_db=offset,
        hrtf=hrtf,
    )


def test_can_combine_layers_requires_two_complete_variations() -> None:
    fr = LayerState(
        curve=CurveData(kind="fr", freqs=np.array([100.0, 1000.0]), mag_db=np.array([1.0, 2.0])),
        source_path=Path("fr.txt"),
        name="fr",
    )

    assert can_combine_layers([_variation_layer(name="a"), _variation_layer(name="b")])
    assert not can_combine_layers([_variation_layer(name="a")])
    assert not can_combine_layers([_variation_layer(name="a"), fr])


def _normal_band_layer(
    *,
    name: str,
    half_width_db: float,
    median_db: float = 0.0,
    freqs: np.ndarray | None = None,
    sweeps: int | None = None,
) -> LayerState:
    """A variation layer whose percentiles come from an exact normal band."""
    if freqs is None:
        freqs = np.array([20.0, 1000.0, 20000.0])
    sigma = half_width_db / _Z_P90
    ones = np.ones(len(freqs))
    metadata: dict[str, object] = {}
    if sweeps is not None:
        metadata = {"Variation Sweeps": str(sweeps), "variation_sweeps": str(sweeps)}
    return LayerState(
        curve=CurveData(
            kind="variation",
            freqs=freqs,
            p10_db=ones * (median_db - _Z_P90 * sigma),
            p25_db=ones * (median_db - _Z_P75 * sigma),
            median_db=ones * median_db,
            p75_db=ones * (median_db + _Z_P75 * sigma),
            p90_db=ones * (median_db + _Z_P90 * sigma),
            metadata=metadata,
        ),
        source_path=Path(f"{name}.txt"),
        name=name,
    )


def _at_1khz(curve: CurveData) -> tuple[float, float, float, float, float]:
    index = int(np.argmin(np.abs(curve.freqs - 1000.0)))
    return (
        float(curve.p10_db[index]),
        float(curve.p25_db[index]),
        float(curve.median_db[index]),
        float(curve.p75_db[index]),
        float(curve.p90_db[index]),
    )


def test_combine_uses_the_shared_1200_point_log_grid_with_an_exact_1khz_point() -> None:
    combined = combine_variation_layers(
        [
            _normal_band_layer(name="a", half_width_db=1.0),
            _normal_band_layer(name="b", half_width_db=2.0),
        ]
    )

    assert combined.kind == "variation"
    assert combined.freqs.size == 1200
    assert combined.freqs[0] == pytest.approx(20.0)
    assert combined.freqs[-1] == pytest.approx(20000.0)
    assert 1000.0 in combined.freqs


def test_combining_a_narrow_and_a_wide_band_keeps_the_wide_tails() -> None:
    """A +/-1 dB band pooled with a +/-10 dB band must not average to +/-5.5 dB.

    The equal-weight mixture of N(0, 1/1.2816) and N(0, 10/1.2816) has its 10th
    and 90th percentiles at +/-6.567 dB, and its quartiles at +/-1.205 dB.
    """
    combined = combine_variation_layers(
        [
            _normal_band_layer(name="narrow", half_width_db=1.0),
            _normal_band_layer(name="wide", half_width_db=10.0),
        ]
    )

    p10, p25, median, p75, p90 = _at_1khz(combined)
    assert median == pytest.approx(0.0, abs=1e-6)
    assert p90 == pytest.approx(6.567, abs=0.02)
    assert p10 == pytest.approx(-6.567, abs=0.02)
    assert p75 == pytest.approx(1.205, abs=0.02)
    assert p25 == pytest.approx(-1.205, abs=0.02)
    # The old percentile-of-percentiles answer was +/-5.5 dB.
    assert abs(p90) > 6.0


def test_combine_weights_layers_by_their_variation_sweep_count() -> None:
    weighted = combine_variation_layers(
        [
            _normal_band_layer(name="narrow", half_width_db=1.0, sweeps=90),
            _normal_band_layer(name="wide", half_width_db=10.0, sweeps=10),
        ]
    )
    equal = combine_variation_layers(
        [
            _normal_band_layer(name="narrow", half_width_db=1.0),
            _normal_band_layer(name="wide", half_width_db=10.0),
        ]
    )

    assert layer_sweep_count(_normal_band_layer(name="x", half_width_db=1.0, sweeps=7)) == 7
    assert layer_sweep_count(_normal_band_layer(name="x", half_width_db=1.0)) is None
    weighted_p90 = _at_1khz(weighted)[4]
    equal_p90 = _at_1khz(equal)[4]
    assert weighted_p90 < equal_p90
    assert weighted_p90 == pytest.approx(1.199, abs=0.02)


def test_combine_applies_offsets_and_hrtf_before_pooling() -> None:
    first = _normal_band_layer(name="first", half_width_db=2.0)
    first.vertical_offset_db = 3.0
    second = _normal_band_layer(name="second", half_width_db=2.0)
    second.vertical_offset_db = -3.0
    original = first.curve.median_db.copy()

    combined = combine_variation_layers([first, second])

    median = _at_1khz(combined)[2]
    assert median == pytest.approx(0.0, abs=0.02)
    # A +/-3 dB separation between two +/-2 dB bands must widen the pooled band.
    assert _at_1khz(combined)[4] > 3.0
    assert np.array_equal(first.curve.median_db, original)


def test_combine_ignores_a_layer_outside_its_own_frequency_range() -> None:
    """A layer that stops at 500 Hz must not be extrapolated flat to 20 kHz."""
    full = _normal_band_layer(name="full", half_width_db=1.0)
    partial = _normal_band_layer(
        name="partial",
        half_width_db=10.0,
        freqs=np.array([20.0, 500.0]),
    )

    combined = combine_variation_layers([full, partial])

    high = int(np.argmin(np.abs(combined.freqs - 10000.0)))
    low = int(np.argmin(np.abs(combined.freqs - 100.0)))
    assert float(combined.p90_db[high]) == pytest.approx(1.0, abs=0.02)
    assert float(combined.p90_db[low]) > 5.0


def test_normalization_offset_warns_instead_of_clamping_outside_the_range() -> None:
    partial = CurveData(
        kind="fr",
        freqs=np.array([20.0, 500.0]),
        mag_db=np.array([4.0, 9.0]),
    )

    offset, warning = normalization_offset_at_1khz_with_warning(partial)

    assert offset == 0.0
    assert warning is not None
    assert "1 kHz" in warning


def test_normalization_offset_still_anchors_a_curve_that_covers_1khz() -> None:
    curve = CurveData(
        kind="fr",
        freqs=np.array([100.0, 1000.0, 5000.0]),
        mag_db=np.array([-2.0, 5.0, 1.0]),
    )

    offset, warning = normalization_offset_at_1khz_with_warning(curve)

    assert offset == -5.0
    assert warning is None


def test_combine_sigma_matches_the_former_band_sigma_up_to_z_precision() -> None:
    """``_band_sigma`` used 4-digit Z constants; the exact ones move it < 4e-5."""
    rng = np.random.default_rng(2)
    median = rng.normal(0.0, 3.0, 500)
    spread = rng.uniform(0.2, 6.0, 500)
    p10, p25, p75, p90 = (median + k * spread for k in (-1.3, -0.7, 0.66, 1.25))
    former = np.maximum(
        (np.abs(p90 - p10) / (2.0 * 1.2816) + np.abs(p75 - p25) / (2.0 * 0.6745)) / 2.0, 1e-9
    )
    np.testing.assert_allclose(sigma_from_percentiles(p10, p25, p75, p90), former, rtol=4e-5)
