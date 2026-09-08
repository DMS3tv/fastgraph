"""Atomic save/load for Measure session files."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

import dms.measure_persistence as persistence
from dms.measure_persistence import (
    MeasureSessionLoadError,
    ensure_measure_session_extension,
    load_measure_session,
    same_session_file,
    save_measure_session,
)
from dms.measure_session import (
    MEASURE_SESSION_SCHEMA_VERSION,
    MeasureSession,
    UnsupportedMeasureSessionVersion,
)
from dms.session import SessionData
from dms.two_channel import TwoChannelCurvePair


def _curve(offset: float = 0.0, points: int = 64):
    freqs = np.geomspace(20.0, 20000.0, points)
    return freqs, np.linspace(-3.0, 3.0, points) + offset


def _session(model: str = "Demo") -> MeasureSession:
    session = MeasureSession(
        metadata=SessionData(rig="Rig", brand="DMS", model=model),
        two_channel=True,
        level_mode="dbspl",
        hrtf_path="/tmp/hrtf.txt",
        hrtf_name="Fixture",
        hrtf_enabled=True,
    )
    session.add_sweep(*_curve(), note=model)
    session.add_pair(TwoChannelCurvePair(channel_1=_curve(1.0), channel_2=_curve(-1.0)))
    return session


@pytest.mark.parametrize(
    ("selected_name", "expected_name"),
    [
        ("unit", "unit.fastgraph-measure.json"),
        ("unit.fastgraph-measure", "unit.fastgraph-measure.json"),
        ("unit.json", "unit.fastgraph-measure.json"),
        ("unit.txt", "unit.txt.fastgraph-measure.json"),
        ("unit.FASTGRAPH-MEASURE.JSON", "unit.fastgraph-measure.json"),
    ],
)
def test_measure_session_extension_is_complete(
    selected_name: str,
    expected_name: str,
) -> None:
    assert ensure_measure_session_extension(Path(selected_name)).name == expected_name


def test_save_normalizes_the_extension_and_records_the_path(tmp_path: Path) -> None:
    session = _session()

    written = save_measure_session(session, tmp_path / "unit")

    assert written == tmp_path / "unit.fastgraph-measure.json"
    assert written.is_file()
    assert session.source_path == str(written)


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    session = _session()
    path = save_measure_session(session, tmp_path / "unit")

    loaded = load_measure_session(path)

    assert loaded.to_dict() == session.to_dict()
    assert loaded.source_path == str(path)
    assert loaded.metadata.model == "Demo"
    assert loaded.two_channel is True
    assert loaded.level_mode == "dbspl"
    assert np.allclose(loaded.sweeps[0].freqs, session.sweeps[0].freqs, atol=1e-6)
    assert np.allclose(
        loaded.pairs[0].channel_2.mag_db,
        session.pairs[0].channel_2.mag_db,
        atol=1e-6,
    )


def test_atomic_replace_failure_keeps_the_previous_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "unit.fastgraph-measure.json"
    save_measure_session(_session("First"), path)
    real_replace = os.replace

    def fail_target_replace(source, destination):
        if Path(destination) == path:
            raise OSError("simulated interruption")
        return real_replace(source, destination)

    monkeypatch.setattr(persistence.os, "replace", fail_target_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        save_measure_session(_session("Second"), path)

    monkeypatch.setattr(persistence.os, "replace", real_replace)
    assert load_measure_session(path).metadata.model == "First"
    assert not list(tmp_path.glob("*.tmp"))


def test_unserializable_state_never_replaces_a_good_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Parse-back validation: bad bytes are caught before the swap."""
    path = tmp_path / "unit.fastgraph-measure.json"
    save_measure_session(_session("First"), path)

    monkeypatch.setattr(
        persistence.MeasureSession,
        "from_dict",
        staticmethod(lambda data: (_ for _ in ()).throw(ValueError("bad snapshot"))),
    )
    with pytest.raises(ValueError, match="bad snapshot"):
        save_measure_session(_session("Second"), path)

    monkeypatch.undo()
    assert load_measure_session(path).metadata.model == "First"


def test_corrupt_file_raises_and_names_the_backup(tmp_path: Path) -> None:
    path = tmp_path / "unit.fastgraph-measure.json"
    save_measure_session(_session(), path)
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(MeasureSessionLoadError) as excinfo:
        load_measure_session(path)

    backups = list(tmp_path.glob("*.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].name in str(excinfo.value)
    assert not path.exists()


def test_missing_file_raises_a_load_error(tmp_path: Path) -> None:
    with pytest.raises(MeasureSessionLoadError):
        load_measure_session(tmp_path / "absent.fastgraph-measure.json")


def test_a_newer_session_is_reported_as_unsupported_not_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "unit.fastgraph-measure.json"
    save_measure_session(_session(), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = MEASURE_SESSION_SCHEMA_VERSION + 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(UnsupportedMeasureSessionVersion):
        load_measure_session(path)

    # Intact, so it must still be there afterwards.
    assert path.is_file()
    assert not list(tmp_path.glob("*.corrupt-*"))


def test_same_session_file_compares_real_locations(tmp_path: Path) -> None:
    path = tmp_path / "unit.fastgraph-measure.json"
    save_measure_session(_session(), path)

    assert same_session_file(path, tmp_path / "." / "unit.fastgraph-measure.json")
    assert same_session_file(str(path), path)
    assert not same_session_file(path, tmp_path / "other.fastgraph-measure.json")
    assert not same_session_file(path, None)
    assert not same_session_file(None, None)
