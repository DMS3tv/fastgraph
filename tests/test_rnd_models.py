from pathlib import Path

import numpy as np
import pytest

from dms.rnd.models import (
    RnDGroup,
    RnDMeasurement,
    RnDSession,
    generate_measurement_name,
    group_variation,
)
from dms.session import SessionData


def _measurement(mid: str, name: str, shift: float = 0.0) -> RnDMeasurement:
    return RnDMeasurement(
        id=mid,
        name=name,
        freqs=np.array([100.0, 1000.0, 10000.0]),
        mag_db=np.array([1.0 + shift, 0.0 + shift, -1.0 + shift]),
        metadata={"brand": "DMS", "model": "Demo", "rig": "Rig"},
        rig="Rig",
        input_device_label="Input",
        input_channel_index=0,
        input_channel_label="Channel 1",
        output_device_label="Output",
    )


def test_rnd_session_round_trip_and_order_repair() -> None:
    first = _measurement("a", "A")
    second = _measurement("b", "B", 1.0)
    first.vertical_offset_db = 1.5
    first.hrtf_name = "Fixture A"
    first.hrtf_path = "old/path/Fixture A.txt"
    session = RnDSession(
        measurements=[first, second],
        groups=[RnDGroup(id="g", name="Group", pinned=True, vertical_offset_db=-2.0, measurement_ids=["b"])],
        ungrouped_order=["a"],
        selected_id="b",
        hrtf_name="Fixture A",
        hrtf_path="old/path/Fixture A.txt",
        preference_bounds_enabled=True,
        target_visible=True,
        target_name="Target",
        target_path="target.txt",
        target_freqs=np.array([100.0, 1000.0]),
        target_mag_db=np.array([2.0, 3.0]),
        target_offset_db=-4.5,
        smoothing_fraction=12,
        delta_mode_enabled=True,
        saved_app_version="0.3.0",
    )

    loaded = RnDSession.from_dict(session.to_dict())

    assert loaded.schema_version == 1
    assert [item.name for item in loaded.measurements] == ["A", "B"]
    assert loaded.groups[0].measurement_ids == ["b"]
    assert loaded.groups[0].pinned is True
    assert loaded.groups[0].vertical_offset_db == -2.0
    assert loaded.measurements[0].vertical_offset_db == 1.5
    assert loaded.measurements[0].hrtf_name == "Fixture A"
    assert loaded.hrtf_name == "Fixture A"
    assert loaded.preference_bounds_enabled is True
    assert loaded.target_visible is True
    assert loaded.target_name == "Target"
    assert loaded.target_offset_db == -4.5
    assert np.allclose(loaded.target_mag_db, [2.0, 3.0])
    assert loaded.smoothing_fraction == 12
    assert loaded.delta_mode_enabled is True
    assert loaded.ungrouped_order == ["a"]
    assert np.allclose(loaded.measurements[1].mag_db, second.mag_db)


def test_rnd_session_old_offset_and_bounds_fields_default_to_zero_and_off() -> None:
    session = RnDSession(
        measurements=[_measurement("a", "A")],
        groups=[RnDGroup(id="g", name="Group", measurement_ids=["a"])],
        ungrouped_order=[],
    )
    data = session.to_dict()
    data.pop("preference_bounds_enabled")
    for key in (
        "target_visible",
        "target_name",
        "target_path",
        "target_freqs",
        "target_mag_db",
        "target_offset_db",
        "smoothing_fraction",
        "delta_mode_enabled",
    ):
        data.pop(key)
    data["measurements"][0].pop("vertical_offset_db")
    data["groups"][0].pop("vertical_offset_db")

    loaded = RnDSession.from_dict(data)

    assert loaded.measurements[0].vertical_offset_db == 0.0
    assert loaded.groups[0].vertical_offset_db == 0.0
    assert loaded.preference_bounds_enabled is False
    assert loaded.target_visible is False
    assert loaded.target_offset_db == 0.0
    assert loaded.target_freqs.size == 0
    assert loaded.smoothing_fraction == 48
    assert loaded.delta_mode_enabled is False


def test_rnd_session_rejects_unknown_schema() -> None:
    with pytest.raises(ValueError, match="Unsupported R&D session schema"):
        RnDSession.from_dict({"schema_version": 999})


def test_generate_measurement_name_uses_metadata_and_channel_with_suffix() -> None:
    session = SessionData(rig="GRAS", brand="DMS", model="Alpha")

    name = generate_measurement_name(
        session,
        "MOTU Input",
        "Channel 2",
        {"DMS Alpha - GRAS - MOTU Input - Channel 2"},
    )

    assert name == "DMS Alpha - GRAS - MOTU Input - Channel 2 (2)"


def test_group_variation_requires_two_measurements_and_returns_percentiles() -> None:
    assert group_variation([_measurement("a", "A")]) is None

    variation = group_variation([_measurement("a", "A"), _measurement("b", "B", 2.0)])

    assert variation is not None
    freqs, p10, p25, p75, p90, median = variation
    assert len(freqs) == 1200
    assert p10.shape == freqs.shape
    assert p25.shape == freqs.shape
    assert p75.shape == freqs.shape
    assert p90.shape == freqs.shape
    assert median.shape == freqs.shape
