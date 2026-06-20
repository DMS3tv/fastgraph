from pathlib import Path

import numpy as np

from dms.curator.models import CurveData, LayerState
from dms.curator.transforms import (
    apply_layer_transform,
    can_combine_layers,
    combine_variation_layers,
    normalization_offset_at_1khz,
    visible_display_layers,
)


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

    assert normalization_offset_at_1khz(curve) == -5.0


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

    assert normalization_offset_at_1khz(curve) == 3.0


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


def test_combine_variation_layers_repercentiles_sample_curves() -> None:
    first = _variation_layer(name="first")
    second = _variation_layer(name="second", median_shift=10.0)

    combined = combine_variation_layers([first, second])

    samples = np.vstack([
        first.curve.p10_db,
        first.curve.p25_db,
        first.curve.median_db,
        first.curve.p75_db,
        first.curve.p90_db,
        second.curve.p10_db,
        second.curve.p25_db,
        second.curve.median_db,
        second.curve.p75_db,
        second.curve.p90_db,
    ])
    assert np.allclose(combined.freqs, first.curve.freqs)
    assert np.allclose(combined.p10_db, np.percentile(samples, 10, axis=0))
    assert np.allclose(combined.p25_db, np.percentile(samples, 25, axis=0))
    assert np.allclose(combined.median_db, np.percentile(samples, 50, axis=0))
    assert np.allclose(combined.p75_db, np.percentile(samples, 75, axis=0))
    assert np.allclose(combined.p90_db, np.percentile(samples, 90, axis=0))


def test_combine_variation_layers_applies_hrtf_offset_and_interpolates() -> None:
    hrtf = _FakeHrtf(
        Path("hrtf.txt"),
        "hrtf",
        np.array([100.0, 1000.0]),
        np.array([1.0, 2.0]),
    )
    first = _variation_layer(name="first", offset=1.0, hrtf=hrtf)
    second = _variation_layer(
        name="second",
        freqs=np.array([100.0, 550.0, 1000.0]),
        median_shift=4.0,
        offset=-2.0,
    )
    original_first_p10 = first.curve.p10_db.copy()

    combined = combine_variation_layers([first, second])

    transformed_first = apply_layer_transform(first)
    transformed_second = apply_layer_transform(second)
    assert transformed_first.p10_db is not None
    assert transformed_second.p10_db is not None
    expected_samples = []
    for curve in (transformed_first, transformed_second):
        assert curve.p10_db is not None
        assert curve.p25_db is not None
        assert curve.median_db is not None
        assert curve.p75_db is not None
        assert curve.p90_db is not None
        for values in (curve.p10_db, curve.p25_db, curve.median_db, curve.p75_db, curve.p90_db):
            expected_samples.append(np.interp(first.curve.freqs, curve.freqs, values))
    expected = np.vstack(expected_samples)
    assert np.allclose(combined.p10_db, np.percentile(expected, 10, axis=0))
    assert np.allclose(first.curve.p10_db, original_first_p10)
