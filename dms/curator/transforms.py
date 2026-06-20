from __future__ import annotations

from dataclasses import replace

import numpy as np

from dms.curator.models import CurveData, LayerState


def normalization_offset_at_1khz(curve: CurveData) -> float:
    if curve.kind == "fr" and curve.mag_db is not None:
        value = np.interp(1000.0, curve.freqs, curve.mag_db)
        return -float(value)
    if curve.kind == "variation" and curve.median_db is not None:
        value = np.interp(1000.0, curve.freqs, curve.median_db)
        return -float(value)
    return 0.0


def apply_layer_transform(layer: LayerState) -> CurveData:
    curve = layer.curve
    if layer.hrtf is not None:
        correction = layer.hrtf.evaluate(curve.freqs)
        curve = replace(
            curve,
            mag_db=_correct_optional(curve.mag_db, correction),
            p10_db=_correct_optional(curve.p10_db, correction),
            p25_db=_correct_optional(curve.p25_db, correction),
            median_db=_correct_optional(curve.median_db, correction),
            p75_db=_correct_optional(curve.p75_db, correction),
            p90_db=_correct_optional(curve.p90_db, correction),
        )
    return curve.shifted(layer.vertical_offset_db)


def visible_display_layers(layers: list[LayerState]) -> list[tuple[LayerState, CurveData]]:
    return [(layer, apply_layer_transform(layer)) for layer in layers if layer.visible]


def can_combine_layers(layers: list[LayerState]) -> bool:
    return len(layers) >= 2 and all(_is_complete_variation(layer.curve) for layer in layers)


def combine_variation_layers(layers: list[LayerState]) -> CurveData:
    if not can_combine_layers(layers):
        raise ValueError("Select at least two complete variation layers to combine.")

    base_freqs = layers[0].curve.freqs
    sample_rows: list[np.ndarray] = []
    for layer in layers:
        curve = apply_layer_transform(layer)
        if not _is_complete_variation(curve):
            raise ValueError("Only complete variation layers can be combined.")
        assert curve.p10_db is not None
        assert curve.p25_db is not None
        assert curve.median_db is not None
        assert curve.p75_db is not None
        assert curve.p90_db is not None
        for values in (
            curve.p10_db,
            curve.p25_db,
            curve.median_db,
            curve.p75_db,
            curve.p90_db,
        ):
            sample_rows.append(np.interp(base_freqs, curve.freqs, values))

    samples = np.vstack(sample_rows)
    return CurveData(
        kind="variation",
        freqs=base_freqs.copy(),
        p10_db=np.percentile(samples, 10, axis=0),
        p25_db=np.percentile(samples, 25, axis=0),
        median_db=np.percentile(samples, 50, axis=0),
        p75_db=np.percentile(samples, 75, axis=0),
        p90_db=np.percentile(samples, 90, axis=0),
        metadata={"Derived": "Combined variation"},
    )


def _correct_optional(values: np.ndarray | None, correction: np.ndarray) -> np.ndarray | None:
    if values is None:
        return None
    return values - correction


def _is_complete_variation(curve: CurveData) -> bool:
    return curve.kind == "variation" and all(
        values is not None
        for values in (
            curve.p10_db,
            curve.p25_db,
            curve.median_db,
            curve.p75_db,
            curve.p90_db,
        )
    )
