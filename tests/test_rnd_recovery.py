import json
import os
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest

import dms.rnd.persistence as persistence
from dms.rnd.models import RnDGroup, RnDMeasurement, RnDSession
from dms.rnd.persistence import (
    ensure_rnd_session_extension,
    load_rnd_session,
    save_rnd_session,
)
from dms.rnd.photos import RnDPhotoStore, attachment_directory
from dms.rnd.recovery import RnDRecoveryManager


def _measurement(mid: str = "m1", name: str = "One") -> RnDMeasurement:
    return RnDMeasurement(
        id=mid,
        name=name,
        freqs=np.array([100.0, 1000.0]),
        mag_db=np.array([1.0, 0.0]),
        metadata={"brand": "DMS"},
        rig="Rig",
        input_device_label="Input",
        input_channel_index=0,
        input_channel_label="Channel 1",
        output_device_label="Output",
        notes="Pads changed",
    )


def _session(name: str = "One") -> RnDSession:
    measurement = _measurement(name=name)
    group = RnDGroup(id="g1", name="Prototype", measurement_ids=["m1"])
    return RnDSession(
        measurements=[measurement],
        groups=[group],
        selected_id="m1",
        target_visible=True,
        target_name="Target",
        target_freqs=np.array([100.0, 1000.0]),
        target_mag_db=np.array([0.0, -1.0]),
        hrtf_path="/tmp/hrtf.txt",
        hrtf_name="Fixture",
        smoothing_fraction=24,
        delta_mode_enabled=True,
    )


@pytest.mark.parametrize(
    ("selected_name", "expected_name"),
    [
        ("prototype", "prototype.fastgraph-rnd.json"),
        ("prototype.fastgraph-rnd", "prototype.fastgraph-rnd.json"),
        ("prototype.json", "prototype.fastgraph-rnd.json"),
        ("prototype.txt", "prototype.txt.fastgraph-rnd.json"),
        ("prototype.FASTGRAPH-RND.JSON", "prototype.fastgraph-rnd.json"),
    ],
)
def test_rnd_session_extension_is_complete(
    selected_name: str,
    expected_name: str,
) -> None:
    assert ensure_rnd_session_extension(Path(selected_name)).name == expected_name


def test_atomic_session_save_round_trips_state_and_photos(tmp_path: Path) -> None:
    store = RnDPhotoStore()
    session = _session()
    image = QImage(64, 64, QImage.Format.Format_RGB32)
    session.measurements[0].photos.append(store.add_image(image, display_name="Pads"))
    path = tmp_path / "session.fastgraph-rnd.json"

    save_rnd_session(session, store, path)
    loaded_store = RnDPhotoStore()
    loaded, missing = load_rnd_session(path, loaded_store)

    assert missing == []
    assert loaded.to_dict() == session.to_dict()
    photo_path = attachment_directory(path) / session.measurements[0].photos[0].file_name
    assert photo_path.is_file()


def test_atomic_replace_failure_keeps_last_valid_manifest(
    monkeypatch,
    tmp_path: Path,
) -> None:
    store = RnDPhotoStore()
    path = tmp_path / "session.fastgraph-rnd.json"
    first = _session("First")
    save_rnd_session(first, store, path)
    real_replace = os.replace

    def fail_manifest_replace(source, destination):
        if Path(destination) == path:
            raise OSError("simulated interruption")
        return real_replace(source, destination)

    monkeypatch.setattr(persistence.os, "replace", fail_manifest_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        save_rnd_session(_session("Second"), store, path)

    loaded, _missing = load_rnd_session(path)
    assert loaded.measurements[0].name == "First"


def test_recovery_falls_back_to_previous_and_quarantines_invalid_current(
    tmp_path: Path,
) -> None:
    store = RnDPhotoStore()
    current_session = _session("First")
    manager = RnDRecoveryManager(
        tmp_path / "recovery",
        lambda: persistence.session_snapshot(current_session, store),
    )
    first_snapshot, first_sources = persistence.session_snapshot(current_session, store)
    manager._save_snapshot(first_snapshot, first_sources)
    second_session = _session("Second")
    second_snapshot, second_sources = persistence.session_snapshot(second_session, store)
    manager._save_snapshot(second_snapshot, second_sources)
    manager.current_path.write_text("{broken", encoding="utf-8")

    candidates = manager.candidates()

    assert len(candidates) == 1
    assert candidates[0].kind == "previous"
    loaded, _missing = manager.load_candidate(candidates[0], store)
    assert loaded.measurements[0].name == "First"
    assert list(manager.quarantine_root.glob("*/current.fastgraph-rnd.json"))
    manager.shutdown_clean()


def test_keep_for_later_preserves_bundle_and_clears_active(tmp_path: Path) -> None:
    store = RnDPhotoStore()
    session = _session()
    manager = RnDRecoveryManager(
        tmp_path / "recovery",
        lambda: persistence.session_snapshot(session, store),
    )
    snapshot, sources = persistence.session_snapshot(session, store)
    manager._save_snapshot(snapshot, sources)
    candidate = manager.candidates()[0]

    kept = manager.keep_for_later(candidate)

    assert kept.kind == "deferred"
    assert kept.path.is_file()
    assert not manager.current_path.exists()
    assert manager.candidates()[0].kind == "deferred"
    manager.shutdown_clean()
    assert kept.path.is_file()


def test_recovery_debounces_rapid_changes_and_clears_error(qapp, tmp_path: Path) -> None:
    store = RnDPhotoStore()
    session = _session()
    snapshots = 0

    def provider():
        nonlocal snapshots
        snapshots += 1
        return persistence.session_snapshot(session, store)

    manager = RnDRecoveryManager(
        tmp_path / "recovery",
        provider,
        debounce_ms=20,
        maximum_ms=80,
    )
    successes: list[bool] = []
    manager.save_succeeded.connect(lambda: successes.append(True))
    manager.enable()
    manager.schedule()
    manager.schedule()
    manager.schedule()

    for _ in range(30):
        QTest.qWait(10)
        if successes:
            break

    assert successes == [True]
    assert snapshots == 1
    assert manager.current_path.is_file()
    manager.shutdown_clean()


def test_recovery_maximum_timer_saves_during_continuous_changes(
    qapp,
    tmp_path: Path,
) -> None:
    store = RnDPhotoStore()
    session = _session()
    manager = RnDRecoveryManager(
        tmp_path / "recovery",
        lambda: persistence.session_snapshot(session, store),
        debounce_ms=1000,
        maximum_ms=30,
    )
    successes: list[bool] = []
    manager.save_succeeded.connect(lambda: successes.append(True))
    manager.enable()

    for _ in range(6):
        manager.schedule()
        QTest.qWait(10)

    for _ in range(30):
        QTest.qWait(10)
        if successes:
            break

    assert successes
    assert manager.current_path.is_file()
    manager.shutdown_clean()
