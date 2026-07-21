import os
from pathlib import Path

import pytest

from dms.hrtf import HRTFCurve, get_hrtf_curve, _hrtf_curve_cache


HRTF_TEXT = (
    "* HRTF test fixture\n"
    "20 0.0\n"
    "100 -1.0\n"
    "1000 0.0\n"
    "10000 1.0\n"
    "20000 2.0\n"
)


@pytest.fixture(autouse=True)
def _clear_cache():
    _hrtf_curve_cache.clear()
    yield
    _hrtf_curve_cache.clear()


def _write_hrtf(path: Path) -> None:
    path.write_text(HRTF_TEXT)


def test_same_instance_returned_for_unchanged_file(tmp_path: Path) -> None:
    path = tmp_path / "hrtf.txt"
    _write_hrtf(path)

    first = get_hrtf_curve(str(path))
    second = get_hrtf_curve(str(path))

    assert first is second
    assert isinstance(first, HRTFCurve)


def test_touching_file_bumps_mtime_and_returns_new_instance(tmp_path: Path) -> None:
    path = tmp_path / "hrtf.txt"
    _write_hrtf(path)

    first = get_hrtf_curve(str(path))

    # Bump mtime without necessarily changing content, simulating an edit/re-save.
    stat = path.stat()
    new_mtime_ns = stat.st_mtime_ns + 1_000_000_000  # +1s, guaranteed to differ
    os.utime(path, ns=(new_mtime_ns, new_mtime_ns))

    second = get_hrtf_curve(str(path))

    assert second is not first


def test_different_paths_produce_different_entries(tmp_path: Path) -> None:
    path_a = tmp_path / "a.txt"
    path_b = tmp_path / "b.txt"
    _write_hrtf(path_a)
    _write_hrtf(path_b)

    curve_a = get_hrtf_curve(str(path_a))
    curve_b = get_hrtf_curve(str(path_b))

    assert curve_a is not curve_b
    assert len(_hrtf_curve_cache) == 2


def test_stale_entries_for_a_path_are_evicted_after_mtime_change(tmp_path: Path) -> None:
    path = tmp_path / "hrtf.txt"
    _write_hrtf(path)

    get_hrtf_curve(str(path))
    assert len(_hrtf_curve_cache) == 1

    stat = path.stat()
    new_mtime_ns = stat.st_mtime_ns + 1_000_000_000
    os.utime(path, ns=(new_mtime_ns, new_mtime_ns))
    get_hrtf_curve(str(path))

    # Old (stale) entry for this path should have been evicted, not accumulated.
    assert len(_hrtf_curve_cache) == 1


def test_repeated_edits_do_not_grow_cache_per_path(tmp_path: Path) -> None:
    path = tmp_path / "hrtf.txt"
    _write_hrtf(path)

    for i in range(5):
        stat_before = path.stat() if path.exists() else None
        _write_hrtf(path)
        if stat_before is not None:
            new_mtime_ns = stat_before.st_mtime_ns + (i + 1) * 1_000_000_000
            os.utime(path, ns=(new_mtime_ns, new_mtime_ns))
        get_hrtf_curve(str(path))

    assert len(_hrtf_curve_cache) == 1


def test_missing_file_raises_like_direct_construction(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.txt"

    with pytest.raises(Exception):
        get_hrtf_curve(str(missing))

    with pytest.raises(Exception):
        HRTFCurve(str(missing))
