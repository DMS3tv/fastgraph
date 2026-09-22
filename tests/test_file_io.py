"""Crash safety and corrupt-file recovery for the shared persistence helpers."""

import json
import os
import stat
from pathlib import Path

import pytest
from helpers import corrupt_backups

from dms.file_io import (
    atomic_write_json,
    atomic_write_text,
    backup_corrupt_file,
    ensure_extension,
    load_json_with_backup,
)


def test_atomic_write_json_creates_parents_and_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper" / "data.json"
    atomic_write_json(target, {"a": 1, "b": [2, 3]})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "b": [2, 3]}


def test_atomic_write_json_applies_owner_only_mode(tmp_path: Path) -> None:
    target = tmp_path / "secret.json"
    atomic_write_json(target, {"token": "x"}, mode=0o600)
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode & 0o077 == 0, f"group/other bits set: {oct(mode)}"


def test_atomic_write_json_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    atomic_write_json(target, {"a": 1})
    assert [p.name for p in tmp_path.iterdir()] == ["data.json"]


def test_crash_mid_write_leaves_the_original_intact(tmp_path: Path, monkeypatch) -> None:
    """A failure after the temp file is written must not touch the real file."""
    target = tmp_path / "settings.json"
    atomic_write_json(target, {"generation": 1})
    original = target.read_text(encoding="utf-8")

    def _boom(_src, _dst):
        raise OSError("simulated crash between fsync and rename")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        atomic_write_json(target, {"generation": 2})

    assert target.read_text(encoding="utf-8") == original
    # And the half-written temp file is cleaned up rather than left as litter.
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_unserializable_value_never_touches_the_target(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    atomic_write_json(target, {"ok": True})
    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": object()})
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_atomic_write_text_replaces_content(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    atomic_write_text(target, "first", mode=None)
    atomic_write_text(target, "second", mode=None)
    assert target.read_text(encoding="utf-8") == "second"


def test_load_json_with_backup_returns_none_for_a_missing_file(tmp_path: Path) -> None:
    assert load_json_with_backup(tmp_path / "nope.json") == (None, None)


def test_load_json_with_backup_reads_an_object(tmp_path: Path) -> None:
    path = tmp_path / "good.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    data, error = load_json_with_backup(path)
    assert data == {"a": 1}
    assert error is None


def test_invalid_json_is_moved_aside_and_reported(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{not json at all", encoding="utf-8")

    data, error = load_json_with_backup(path)

    assert data is None
    assert not path.exists()
    backups = corrupt_backups(tmp_path, "settings.json")
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{not json at all"
    assert backups[0].name in error


def test_non_object_json_is_treated_as_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    data, error = load_json_with_backup(path)
    assert data is None
    assert "list" in error
    assert len(corrupt_backups(tmp_path, "settings.json")) == 1


def test_undecodable_bytes_are_treated_as_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_bytes(b"\xff\xfe\x00\x00 not utf-8")
    data, error = load_json_with_backup(path)
    assert data is None
    assert "UTF-8" in error
    assert len(corrupt_backups(tmp_path, "settings.json")) == 1


def test_repeated_corruption_never_overwrites_an_earlier_backup(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    for content in ("{bad one", "{bad two"):
        path.write_text(content, encoding="utf-8")
        load_json_with_backup(path)
    assert len(corrupt_backups(tmp_path, "settings.json")) == 2


def test_backup_corrupt_file_returns_none_when_the_file_is_gone(tmp_path: Path) -> None:
    assert backup_corrupt_file(tmp_path / "absent.json") is None


def test_atomic_write_text_validate_rejection_keeps_the_original(tmp_path: Path) -> None:
    target = tmp_path / "session.json"
    atomic_write_text(target, "good", mode=None)
    seen: list[str] = []

    def reject(text: str) -> None:
        seen.append(text)
        raise ValueError("bad bytes")

    with pytest.raises(ValueError, match="bad bytes"):
        atomic_write_text(target, "bad", mode=None, validate=reject)

    assert seen == ["bad"]
    assert target.read_text(encoding="utf-8") == "good"
    assert [p.name for p in tmp_path.iterdir()] == ["session.json"]


@pytest.mark.parametrize("suffix", [".fastgraph-measure.json", ".fastgraph-rnd.json"])
@pytest.mark.parametrize(
    ("selected", "expected"),
    [
        ("unit", "unit{s}"),
        ("unit{head}", "unit{s}"),
        ("unit{HEAD}", "unit{HEAD}.json"),
        ("unit.json", "unit{s}"),
        ("unit.JSON", "unit{s}"),
        ("unit.txt", "unit.txt{s}"),
        ("unit{S}", "unit{s}"),
        ("unit{s}.json", "unit{s}{s}"),
    ],
)
def test_ensure_extension_matches_the_former_per_store_helpers(
    suffix: str, selected: str, expected: str
) -> None:
    """Pins the behaviour of the removed ``ensure_*_session_extension`` pair."""
    head = suffix[: -len(".json")]
    fields = {"s": suffix, "S": suffix.upper(), "head": head, "HEAD": head.upper()}
    result = ensure_extension(Path("dir") / selected.format(**fields), suffix)
    assert result == Path("dir") / expected.format(**fields)
