import json
import os
from pathlib import Path

import numpy as np
import pytest
from helpers import rnd_measurement
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest

import dms.rnd.persistence as persistence
from dms.file_io import ensure_extension
from dms.recovery import rnd_recovery_manager
from dms.rnd.models import RnDGroup, RnDSession
from dms.rnd.persistence import (
    RND_SESSION_EXTENSION,
    load_rnd_session,
    save_rnd_session,
)
from dms.rnd.photos import RnDPhotoStore, attachment_directory


def _rnd_session(name: str = "One") -> RnDSession:
    measurement = rnd_measurement(name=name, notes="Pads changed")
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
    assert ensure_extension(Path(selected_name), RND_SESSION_EXTENSION).name == expected_name


def test_atomic_session_save_round_trips_state_and_photos(tmp_path: Path) -> None:
    store = RnDPhotoStore()
    session = _rnd_session()
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
    first = _rnd_session("First")
    save_rnd_session(first, store, path)
    real_replace = os.replace

    def fail_manifest_replace(source, destination):
        if Path(destination) == path:
            raise OSError("simulated interruption")
        return real_replace(source, destination)

    monkeypatch.setattr(persistence.os, "replace", fail_manifest_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        save_rnd_session(_rnd_session("Second"), store, path)

    loaded, _missing = load_rnd_session(path)
    assert loaded.measurements[0].name == "First"


def test_recovery_falls_back_to_previous_and_quarantines_invalid_current(
    tmp_path: Path,
) -> None:
    store = RnDPhotoStore()
    current_session = _rnd_session("First")
    manager = rnd_recovery_manager(
        tmp_path / "recovery",
        lambda: persistence.session_snapshot(current_session, store),
    )
    first_snapshot, first_sources = persistence.session_snapshot(current_session, store)
    manager._save_snapshot(first_snapshot, first_sources)
    second_session = _rnd_session("Second")
    second_snapshot, second_sources = persistence.session_snapshot(second_session, store)
    manager._save_snapshot(second_snapshot, second_sources)
    manager.current_path.write_text("{broken", encoding="utf-8")

    candidates = manager.candidates()

    assert len(candidates) == 1
    assert candidates[0].kind == "previous"
    loaded, _missing = manager.restore(candidates[0], store)
    assert loaded.measurements[0].name == "First"
    assert list(manager.quarantine_root.glob("*/current.fastgraph-rnd.json"))
    manager.shutdown_clean()


def test_keep_for_later_preserves_bundle_and_clears_active(tmp_path: Path) -> None:
    store = RnDPhotoStore()
    session = _rnd_session()
    manager = rnd_recovery_manager(
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
    session = _rnd_session()
    snapshots = 0

    def provider():
        nonlocal snapshots
        snapshots += 1
        return persistence.session_snapshot(session, store)

    manager = rnd_recovery_manager(
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
    session = _rnd_session()
    manager = rnd_recovery_manager(
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


def test_save_as_to_another_session_keeps_that_sessions_photos(tmp_path: Path) -> None:
    """C1: only a save back to the session's own file prunes attachments."""
    store = RnDPhotoStore()
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    existing = _rnd_session("Existing")
    existing.measurements[0].photos.append(store.add_image(image, display_name="Pads"))
    destination = tmp_path / "existing.fastgraph-rnd.json"
    save_rnd_session(existing, store, destination)
    stranger_photo = (
        attachment_directory(destination) / existing.measurements[0].photos[0].file_name
    )
    assert stranger_photo.is_file()

    incoming = _rnd_session("Incoming")
    incoming.source_path = str(tmp_path / "elsewhere.fastgraph-rnd.json")
    save_rnd_session(incoming, RnDPhotoStore(), destination)

    assert stranger_photo.is_file()
    assert incoming.source_path == str(destination)

    # Saving again, now that this file is the session's own, does prune it.
    save_rnd_session(incoming, RnDPhotoStore(), destination)
    assert not stranger_photo.exists()


def test_loading_a_session_records_its_path(tmp_path: Path) -> None:
    store = RnDPhotoStore()
    path = tmp_path / "session.fastgraph-rnd.json"
    save_rnd_session(_rnd_session(), store, path)

    loaded, _missing = load_rnd_session(path, RnDPhotoStore())

    assert loaded.source_path == str(path)


def test_photo_file_names_from_json_cannot_escape_their_directories(tmp_path: Path) -> None:
    """C7: ``file_name`` is attacker-controlled text, so it is basenamed."""
    store = RnDPhotoStore()
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    session = _rnd_session()
    photo = store.add_image(image, display_name="Pads")
    session.measurements[0].photos.append(photo)
    path = tmp_path / "session.fastgraph-rnd.json"
    save_rnd_session(session, store, path)

    managed_name = photo.file_name
    photo.file_name = f"../../{managed_name}"
    photo.runtime_path = ""

    store.save_session(session, path)
    assert (attachment_directory(path) / managed_name).is_file()
    assert not (tmp_path.parent / managed_name).exists()

    hydrating_store = RnDPhotoStore()
    hydrating_store.hydrate_session(session, path)
    root = Path(hydrating_store.root)
    assert (root / managed_name).is_file()
    assert not (root.parent.parent / managed_name).exists()


def test_failed_rotation_keeps_newest_snapshot_and_flags_degraded(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """C5: a broken current -> previous copy must not cost the new snapshot."""
    store = RnDPhotoStore()
    session = _rnd_session("First")
    manager = rnd_recovery_manager(
        tmp_path / "recovery",
        lambda: persistence.session_snapshot(session, store),
    )
    snapshot, sources = persistence.session_snapshot(session, store)
    manager._save_snapshot(snapshot, sources)
    assert manager.rotation_degraded is False

    real_copy = manager._copy

    def fail_previous(source, destination):
        if Path(destination) == manager.previous_path:
            raise OSError("simulated rotation failure")
        return real_copy(source, destination)

    monkeypatch.setattr(manager, "_copy", fail_previous)
    second = _rnd_session("Second")
    second_snapshot, second_sources = persistence.session_snapshot(second, store)
    manager._save_snapshot(second_snapshot, second_sources)

    assert manager.rotation_degraded is True
    current, _missing = load_rnd_session(manager.current_path)
    assert current.measurements[0].name == "Second"
    assert not manager.staging_path.exists()

    monkeypatch.setattr(manager, "_copy", real_copy)
    manager._save_snapshot(second_snapshot, second_sources)
    assert manager.rotation_degraded is False
    manager.shutdown_clean()


def test_newer_schema_is_reported_not_quarantined(caplog, tmp_path: Path) -> None:
    """C8: a session from a newer Fastgraph is intact; only junk is quarantined."""
    store = RnDPhotoStore()
    session = _rnd_session()
    manager = rnd_recovery_manager(
        tmp_path / "recovery",
        lambda: persistence.session_snapshot(session, store),
    )
    snapshot, sources = persistence.session_snapshot(session, store)
    manager._save_snapshot(snapshot, sources)
    data = json.loads(manager.current_path.read_text(encoding="utf-8"))
    data["schema_version"] = 99
    manager.current_path.write_text(json.dumps(data), encoding="utf-8")

    candidates = manager.candidates()

    assert len(candidates) == 1
    assert candidates[0].unsupported is True
    assert "newer Fastgraph" in candidates[0].label
    assert manager.current_path.is_file()
    assert not list(manager.quarantine_root.glob("*/*.json"))

    manager.current_path.write_text("{not json", encoding="utf-8")
    with caplog.at_level("WARNING", logger="dms.recovery"):
        assert manager.candidates() == []
    quarantined = list(manager.quarantine_root.glob("*/current.fastgraph-rnd.json"))
    assert quarantined
    assert any(str(quarantined[0]) in record.getMessage() for record in caplog.records)
    manager.shutdown_clean()


def test_quarantine_moves_the_photo_sidecar_with_the_manifest(tmp_path: Path) -> None:
    store = RnDPhotoStore()
    session = _rnd_session()
    image = QImage(16, 16, QImage.Format.Format_RGB32)
    session.measurements[0].photos.append(store.add_image(image, display_name="Pads"))
    manager = rnd_recovery_manager(
        tmp_path / "recovery",
        lambda: persistence.session_snapshot(session, store),
    )
    manager._save_snapshot(*persistence.session_snapshot(session, store))
    sidecar = attachment_directory(manager.current_path)
    assert sidecar.is_dir()
    manager.current_path.write_text("{broken", encoding="utf-8")

    assert manager.candidates() == []

    assert not sidecar.exists()
    assert list(manager.quarantine_root.glob(f"*/{sidecar.name}/*.jpg"))
    manager.shutdown_clean()
