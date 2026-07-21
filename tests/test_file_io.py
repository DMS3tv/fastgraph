import json
from pathlib import Path

from dms.file_io import atomic_write_json, atomic_write_text


def test_atomic_write_text_creates_target_with_content(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"

    atomic_write_text(target, "hello world")

    assert target.read_text(encoding="utf-8") == "hello world"


def test_atomic_write_text_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"

    atomic_write_text(target, "content")

    remaining = list(tmp_path.iterdir())
    assert remaining == [target]


def test_atomic_write_text_overwrites_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    target.write_text("old content", encoding="utf-8")

    atomic_write_text(target, "new content")

    assert target.read_text(encoding="utf-8") == "new content"
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_json_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    payload = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}

    atomic_write_json(target, payload)

    assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_text_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "out.txt"

    atomic_write_text(target, "hi")

    assert target.read_text(encoding="utf-8") == "hi"
