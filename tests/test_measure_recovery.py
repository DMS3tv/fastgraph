"""Crash recovery generations for the Measure workspace."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtTest import QTest

import dms.measure_recovery as recovery_module
from dms.measure_recovery import MeasureRecoveryManager
from dms.measure_session import (
    MEASURE_SESSION_SCHEMA_VERSION,
    MeasureSession,
)
from dms.session import SessionData
from dms.two_channel import TwoChannelCurvePair


def _curve(offset: float = 0.0, points: int = 32):
    freqs = np.geomspace(20.0, 20000.0, points)
    return freqs, np.linspace(-2.0, 2.0, points) + offset


def _session(model: str = "First", two_channel: bool = False) -> MeasureSession:
    session = MeasureSession(
        metadata=SessionData(rig="Rig", brand="DMS", model=model),
        two_channel=two_channel,
    )
    session.add_sweep(*_curve(), note=model)
    if two_channel:
        session.add_pair(TwoChannelCurvePair(channel_1=_curve(1.0), channel_2=_curve(-1.0)))
    return session


def _manager(tmp_path: Path, **kwargs) -> MeasureRecoveryManager:
    return MeasureRecoveryManager(tmp_path / "recovery", **kwargs)


def test_snapshot_write_creates_the_current_generation(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = _session()

    manager._save_snapshot(session.to_dict())

    assert manager.current_path.is_file()
    assert manager.current_path.parent == tmp_path / "recovery" / "measure"
    assert not manager.previous_path.exists()
    assert not manager.staging_path.exists()
    candidates = manager.candidates()
    assert [item.kind for item in candidates] == ["current"]
    assert candidates[0].sweep_count == 1
    assert "1 sweeps" in candidates[0].label
    manager.shutdown_clean()


def test_second_snapshot_rotates_current_into_previous(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager._save_snapshot(_session("First").to_dict())
    manager._save_snapshot(_session("Second").to_dict())

    assert manager.restore(manager.candidates()[0]).metadata.model == "Second"
    previous = MeasureSession.from_dict(
        json.loads(manager.previous_path.read_text(encoding="utf-8"))
    )
    assert previous.metadata.model == "First"
    manager.shutdown_clean()


def test_candidates_fall_back_to_previous_and_quarantine_broken_current(
    caplog,
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    manager._save_snapshot(_session("First").to_dict())
    manager._save_snapshot(_session("Second").to_dict())
    manager.current_path.write_text("{broken", encoding="utf-8")

    with caplog.at_level("WARNING", logger="dms.measure_recovery"):
        candidates = manager.candidates()

    assert [item.kind for item in candidates] == ["previous"]
    assert manager.restore(candidates[0]).metadata.model == "First"
    quarantined = list(manager.quarantine_root.glob(f"*/{manager.current_path.name}"))
    assert quarantined
    assert any(str(quarantined[0]) in record.getMessage() for record in caplog.records)
    manager.shutdown_clean()


def test_candidates_are_newest_first_with_deferred_bundles(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager._save_snapshot(_session("Deferred", two_channel=True).to_dict())
    kept = manager.defer(manager.candidates()[0])
    manager._save_snapshot(_session("Active").to_dict())

    candidates = manager.candidates()

    assert [item.kind for item in candidates] == ["current", "deferred"]
    assert candidates[0].preserved_at >= candidates[1].preserved_at
    assert candidates[1].path == kept.path
    assert candidates[1].pair_count == 1
    assert "Kept for later" in candidates[1].label
    assert "1 pairs" in candidates[1].label
    manager.shutdown_clean()


def test_defer_moves_the_bundle_and_survives_a_clean_exit(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager._save_snapshot(_session().to_dict())

    kept = manager.defer(manager.candidates()[0])

    assert kept.kind == "deferred"
    assert kept.path.is_file()
    assert not manager.current_path.exists()
    assert manager.defer(kept) is kept
    manager.shutdown_clean()
    assert kept.path.is_file()
    assert MeasureRecoveryManager(tmp_path / "recovery").candidates()[0].kind == "deferred"


def test_discard_removes_both_active_generations(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager._save_snapshot(_session("First").to_dict())
    manager._save_snapshot(_session("Second").to_dict())

    manager.discard(manager.candidates()[0])

    assert not manager.current_path.exists()
    assert not manager.previous_path.exists()
    assert manager.candidates() == []
    manager.shutdown_clean()


def test_a_newer_schema_is_reported_not_quarantined(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager._save_snapshot(_session().to_dict())
    payload = json.loads(manager.current_path.read_text(encoding="utf-8"))
    payload["schema_version"] = MEASURE_SESSION_SCHEMA_VERSION + 1
    manager.current_path.write_text(json.dumps(payload), encoding="utf-8")

    candidates = manager.candidates()

    assert len(candidates) == 1
    assert candidates[0].unsupported is True
    assert "newer Fastgraph" in candidates[0].label
    assert manager.current_path.is_file()
    assert not list(manager.quarantine_root.glob("*/*.json"))
    manager.shutdown_clean()


def test_clean_exit_marker_suppresses_recovery_on_the_next_start(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    manager._save_snapshot(_session().to_dict())
    leftover = manager.current_path.read_text(encoding="utf-8")

    manager.shutdown_clean()
    # A leftover generation without the marker must still be offered, so put
    # one back and check the marker alone is what suppresses it.
    manager.current_path.write_text(leftover, encoding="utf-8")

    assert MeasureRecoveryManager(tmp_path / "recovery").candidates() == []

    manager.clean_exit_path.unlink()
    reopened = MeasureRecoveryManager(tmp_path / "recovery")
    assert [item.kind for item in reopened.candidates()] == ["current"]
    reopened.shutdown_clean()


def test_a_new_snapshot_clears_the_clean_exit_marker(qapp, tmp_path: Path) -> None:
    manager = _manager(tmp_path, debounce_ms=10, maximum_ms=40)
    manager.shutdown_clean()
    assert manager.clean_exit_path.is_file()

    resumed = _manager(tmp_path, debounce_ms=10, maximum_ms=40)
    saves: list[bool] = []
    resumed.save_succeeded.connect(lambda: saves.append(True))
    resumed.enable()
    resumed.schedule(_session().to_dict())
    for _ in range(20):
        QTest.qWait(10)
        if saves:
            break

    assert saves == [True]
    assert not resumed.clean_exit_path.exists()
    assert [item.kind for item in resumed.candidates()] == ["current"]
    resumed.shutdown_clean()


def test_schedule_debounces_a_burst_into_one_save(qapp, tmp_path: Path) -> None:
    manager = _manager(tmp_path, debounce_ms=20, maximum_ms=80)
    saves: list[bool] = []
    manager.save_succeeded.connect(lambda: saves.append(True))
    manager.enable()

    manager.schedule(_session("First").to_dict())
    manager.schedule(_session("Second").to_dict())
    manager.schedule(_session("Third").to_dict())

    for _ in range(20):
        QTest.qWait(10)
        if saves:
            break

    assert saves == [True]
    assert manager.restore(manager.candidates()[0]).metadata.model == "Third"
    assert not manager.previous_path.exists()
    manager.shutdown_clean()


def test_maximum_interval_saves_during_continuous_changes(
    qapp,
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path, debounce_ms=1000, maximum_ms=30)
    saves: list[bool] = []
    manager.save_succeeded.connect(lambda: saves.append(True))
    manager.enable()

    for _ in range(6):
        manager.schedule(_session().to_dict())
        QTest.qWait(10)
    for _ in range(20):
        QTest.qWait(10)
        if saves:
            break

    assert saves
    assert manager.current_path.is_file()
    manager.shutdown_clean()


def test_an_empty_snapshot_clears_the_active_generations(qapp, tmp_path: Path) -> None:
    manager = _manager(tmp_path, debounce_ms=10, maximum_ms=40)
    manager._save_snapshot(_session().to_dict())
    manager.enable()

    manager.schedule(MeasureSession(metadata=SessionData("Rig", "DMS", "Empty")).to_dict())
    for _ in range(20):
        QTest.qWait(10)
        if not manager.current_path.exists():
            break

    assert not manager.current_path.exists()
    assert manager.candidates() == []
    manager.shutdown_clean()


def test_failed_rotation_keeps_the_newest_snapshot_and_flags_degraded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    manager._save_snapshot(_session("First").to_dict())
    assert manager.rotation_degraded is False
    real_copy = recovery_module.copy_session_file

    def fail_previous(source, destination):
        if Path(destination) == manager.previous_path:
            raise OSError("simulated rotation failure")
        return real_copy(source, destination)

    monkeypatch.setattr(recovery_module, "copy_session_file", fail_previous)
    manager._save_snapshot(_session("Second").to_dict())

    assert manager.rotation_degraded is True
    assert manager.restore(manager.candidates()[0]).metadata.model == "Second"
    assert not manager.staging_path.exists()

    monkeypatch.setattr(recovery_module, "copy_session_file", real_copy)
    manager._save_snapshot(_session("Third").to_dict())
    assert manager.rotation_degraded is False
    manager.shutdown_clean()


def test_a_failing_save_reports_instead_of_raising(qapp, tmp_path: Path) -> None:
    manager = _manager(tmp_path, debounce_ms=10, maximum_ms=40)
    failures: list[str] = []
    manager.save_failed.connect(failures.append)
    manager.enable()

    def explode(snapshot, path):
        raise OSError("simulated disk failure")

    original = recovery_module.save_measure_snapshot
    recovery_module.save_measure_snapshot = explode
    try:
        manager.schedule(_session().to_dict())
        for _ in range(20):
            QTest.qWait(10)
            if failures:
                break
    finally:
        recovery_module.save_measure_snapshot = original

    assert failures and "simulated disk failure" in failures[0]
    assert not manager.current_path.exists()
    manager.shutdown_clean()
