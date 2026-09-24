from pathlib import Path

import numpy as np

from dms.curator.metadata import (
    automatic_export_values,
    canonicalize_metadata,
    shared_metadata,
)
from dms.curator.models import CurveData, GraphState, LayerState


def _layer(kind: str = "fr", **metadata) -> LayerState:
    freqs = np.array([100.0, 1000.0])
    if kind == "variation":
        curve = CurveData(
            "variation",
            freqs,
            p10_db=np.array([-2.0, -1.0]),
            p25_db=np.array([-1.0, 0.0]),
            median_db=np.array([0.0, 1.0]),
            p75_db=np.array([1.0, 2.0]),
            p90_db=np.array([2.0, 3.0]),
            metadata=metadata,
        )
    else:
        curve = CurveData("fr", freqs, mag_db=np.array([1.0, 0.0]), metadata=metadata)
    return LayerState(curve, Path("sample.txt"), "Sample")


def test_canonicalize_fastgraph_headers_preserves_original_values() -> None:
    source = {
        "Brand": "Sony",
        "Model": "WH-1000XM5",
        "Rig": "B&K 5128",
        "Asset Tag": "HP-104",
        "EQ Applied": "No",
        "ANC/Transparency": "ANC",
        "Connection": "Bluetooth",
    }

    result = canonicalize_metadata(source)

    assert result["Brand"] == "Sony"
    assert result["brand"] == "Sony"
    assert result["model"] == "WH-1000XM5"
    assert result["rig"] == "B&K 5128"
    assert result["asset_tag"] == "HP-104"
    assert result["eq_applied"] is False
    assert result["anc_mode"] is True
    assert result["transparency_mode"] is False
    assert result["connection"] == "Bluetooth"


def test_automatic_values_use_curve_type_and_headphone_metadata(fake_brand) -> None:
    layer = _layer(
        "variation",
        brand="Sony",
        model="WH-1000XM5",
        rig="B&K 5128",
        asset_tag="HP-104",
        anc_mode=True,
        eq_applied=False,
        connection="Bluetooth",
        hrtf_name="HpTF 5128",
        compensated=True,
    )
    state = GraphState(layers=[layer])

    values = automatic_export_values(state, layer)

    assert values["title"] == "FREQUENCY RESPONSE & VARIATION"
    assert values["fixture"] == "SONY WH-1000XM5 | ANC ON | STANDARD | BLUETOOTH"
    assert values["footer"] == "B&K 5128, HpTF 5128"
    assert values["footer1"] == "HP-104"
    assert values["footer2"] == fake_brand.poster_defaults["footer2"]
    assert values["legend_variation"] == "Frequency Response + HpTF Variation"


def test_shared_metadata_drops_conflicts() -> None:
    shared = shared_metadata(
        [
            {"brand": "DMS", "model": "One", "rig": "Rig"},
            {"brand": "DMS", "model": "Two", "rig": "Rig"},
        ]
    )

    assert shared["brand"] == "DMS"
    assert shared["rig"] == "Rig"
    assert "model" not in shared


def test_unknown_metadata_does_not_add_empty_segments(fake_brand) -> None:
    layer = _layer(source="Imported")
    values = automatic_export_values(GraphState(layers=[layer]), layer)

    assert values["fixture"] == ""
    assert values["footer"] == ""
    assert values["footer1"] == fake_brand.poster_defaults["footer1"]


def test_poster_defaults_are_empty_without_a_brand() -> None:
    layer = _layer(source="Imported")
    values = automatic_export_values(GraphState(layers=[layer]), layer)

    assert values["footer1"] == values["footer2"] == values["legend_bounds"] == ""
