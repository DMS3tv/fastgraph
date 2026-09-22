from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtGui import QImage

from dms.rnd.models import (
    RnDGroup,
    RnDMeasurement,
    RnDPhoto,
    RnDSession,
    UnsupportedSessionVersion,
    generate_measurement_name,
    group_variation,
)
from dms.rnd.photos import RnDPhotoStore, attachment_directory
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
        groups=[
            RnDGroup(
                id="g", name="Group", pinned=True, vertical_offset_db=-2.0, measurement_ids=["b"]
            )
        ],
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


def test_rnd_session_reads_older_schema_and_flags_newer_one() -> None:
    """C8: only a *newer* file is refused, and with its own exception type."""
    measurement = _measurement("a", "A")
    older = RnDSession(measurements=[measurement], ungrouped_order=["a"]).to_dict()
    older["schema_version"] = 0
    older.pop("smoothing_fraction")
    older.pop("delta_mode_enabled")

    loaded = RnDSession.from_dict(older)

    assert [item.id for item in loaded.measurements] == ["a"]
    assert loaded.smoothing_fraction == 48
    assert loaded.delta_mode_enabled is False
    # Reading an older file upgrades it in memory, so a re-save is current.
    assert loaded.schema_version == 1

    newer = RnDSession().to_dict() | {"schema_version": 2}
    with pytest.raises(UnsupportedSessionVersion, match="newer Fastgraph"):
        RnDSession.from_dict(newer)
    assert issubclass(UnsupportedSessionVersion, ValueError)


def test_rnd_photo_round_trip_and_legacy_default() -> None:
    measurement = _measurement("a", "A")
    measurement.photos.append(RnDPhoto(id="p", display_name="Pad", caption="New pads"))
    group = RnDGroup(id="g", name="Group", photos=[RnDPhoto(id="q", display_name="Fixture")])
    session = RnDSession(measurements=[measurement], groups=[group], ungrouped_order=["a"])

    loaded = RnDSession.from_dict(session.to_dict())

    assert loaded.measurements[0].photos[0].caption == "New pads"
    assert loaded.groups[0].photos[0].file_name == "q.jpg"
    legacy = session.to_dict()
    legacy["measurements"][0].pop("photos")
    legacy["groups"][0].pop("photos")
    assert RnDSession.from_dict(legacy).measurements[0].photos == []


def test_rnd_photo_store_saves_sidecar_hydrates_and_preserves_unrelated_files(
    tmp_path: Path,
) -> None:
    store = RnDPhotoStore()
    image = QImage(1800, 900, QImage.Format.Format_RGB32)
    photo = store.add_image(image, display_name="Webcam")
    measurement = _measurement("a", "A")
    measurement.photos.append(photo)
    session = RnDSession(measurements=[measurement], ungrouped_order=["a"])
    path = tmp_path / "demo.fastgraph-rnd.json"

    store.save_session(session, path)
    sidecar = attachment_directory(path)
    assert (sidecar / photo.file_name).is_file()
    assert QImage(str(sidecar / photo.file_name)).width() == 1600
    unrelated = sidecar / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")
    measurement.photos.clear()
    store.save_session(session, path)
    assert unrelated.is_file()
    assert not (sidecar / photo.file_name).exists()

    measurement.photos.append(photo)
    store.save_session(session, path)
    loaded = RnDSession.from_dict(session.to_dict())
    loaded_store = RnDPhotoStore()
    assert loaded_store.hydrate_session(loaded, path) == []
    assert Path(loaded.measurements[0].photos[0].runtime_path).is_file()


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


def _former_measurement_session_data(measurement: RnDMeasurement) -> SessionData:
    """The hand-listed reader ``rnd.models.measurement_session_data`` used."""
    metadata = dict(measurement.metadata)
    metadata.setdefault("rig", measurement.rig)
    return SessionData(
        rig=str(metadata.get("rig") or measurement.rig or "Unknown Rig"),
        brand=str(metadata.get("brand") or "Unknown"),
        model=str(metadata.get("model") or "Unknown"),
        model_number=str(metadata.get("model_number") or ""),
        asset_tag=str(metadata.get("asset_tag") or ""),
        firmware=str(metadata.get("firmware") or ""),
        eq_applied=bool(metadata.get("eq_applied", False)),
        anc_mode=bool(metadata.get("anc_mode", False)),
        transparency_mode=bool(metadata.get("transparency_mode", False)),
        form_factor=str(metadata.get("form_factor") or "over-ear"),
        in_ear_fitment=str(metadata.get("in_ear_fitment") or ""),
        open_back=bool(metadata.get("open_back", True)),
        pads_notes=str(metadata.get("pads_notes") or ""),
        connection=str(metadata.get("connection") or "wired analog"),
        channel_side=str(metadata.get("channel_side") or ""),
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {
            **SessionData(
                rig="Rig A",
                brand="DMS",
                model="Demo",
                model_number="D-1",
                asset_tag="A7",
                firmware="1.2",
                eq_applied=True,
                anc_mode=True,
                form_factor="in-ear",
                in_ear_fitment="M tips",
                open_back=False,
                pads_notes="new pads",
                connection="bluetooth",
                channel_side="L",
            ).to_dict(),
            "measure_channel": "L",
        },
        {**SessionData("Unknown Rig", "Unknown", "Unknown").to_dict(), "measure_channel": "1"},
        {"brand": "DMS", "model": "Demo"},
    ],
)
def test_session_data_from_measurement_metadata_matches_the_former_reader(metadata) -> None:
    measurement = _measurement("m1", "One")
    measurement.metadata = metadata

    rebuilt = SessionData.from_dict({"rig": measurement.rig, **measurement.metadata})

    assert rebuilt == _former_measurement_session_data(measurement)
