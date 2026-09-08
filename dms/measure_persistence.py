"""Atomic save/load for Measure sessions.

The R&D workspace already proved the shape this module copies: write to a
sibling temporary file, parse the serialized bytes *back* into a session
before anything is swapped in, and only then ``os.replace`` the real file. A
crash, a full disk or a rejected value therefore leaves the previously saved
session completely untouched instead of truncating it.

Loading uses :func:`dms.file_io.load_json_with_backup`, so a damaged file is
renamed to ``<name>.corrupt-<timestamp>`` instead of being silently replaced,
and :class:`MeasureSessionLoadError` names that backup so the user can go and
find it.

Photos are the one thing the R&D format carries that Measure does not, so a
Measure session is a single self-contained JSON file with no sidecar
directory.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from dms.file_io import load_json_with_backup
from dms.measure_session import (
    MEASURE_SESSION_EXTENSION,
    MeasureSession,
    UnsupportedMeasureSessionVersion,
)


_LOG = logging.getLogger(__name__)

__all__ = [
    "MEASURE_SESSION_EXTENSION",
    "MeasureSessionLoadError",
    "ensure_measure_session_extension",
    "load_measure_session",
    "save_measure_session",
    "save_measure_snapshot",
    "same_session_file",
]


class MeasureSessionLoadError(ValueError):
    """A Measure session file could not be read.

    The message is written for the user and names the ``.corrupt-…`` backup
    whenever :mod:`dms.file_io` was able to move the damaged file aside.
    """


def ensure_measure_session_extension(path: Path | str) -> Path:
    """Return ``path`` with the full canonical Measure session extension."""
    path = Path(path)
    name = path.name
    lower_name = name.lower()
    if lower_name.endswith(MEASURE_SESSION_EXTENSION):
        return path.with_name(
            name[: -len(MEASURE_SESSION_EXTENSION)] + MEASURE_SESSION_EXTENSION
        )
    if lower_name.endswith(".fastgraph-measure"):
        return path.with_name(name + ".json")
    if lower_name.endswith(".json"):
        return path.with_name(name[:-5] + MEASURE_SESSION_EXTENSION)
    return path.with_name(name + MEASURE_SESSION_EXTENSION)


def same_session_file(left: str | Path | None, right: str | Path | None) -> bool:
    """Return whether two paths name the same session file on disk."""
    if not left or not right:
        return False
    left_path = Path(left).expanduser()
    right_path = Path(right).expanduser()
    try:
        if left_path.exists() and right_path.exists():
            return left_path.samefile(right_path)
    except OSError:
        pass
    try:
        return left_path.resolve(strict=False) == right_path.resolve(strict=False)
    except OSError:
        return left_path == right_path


def save_measure_session(session: MeasureSession, path: Path | str) -> Path:
    """Save ``session`` to ``path`` atomically and record it as its home.

    Returns the path actually written, which is ``path`` with the canonical
    extension applied.
    """
    target = ensure_measure_session_extension(path)
    save_measure_snapshot(session.to_dict(), target)
    session.source_path = str(target)
    return target


def save_measure_snapshot(snapshot: Mapping[str, Any], path: Path | str) -> Path:
    """Save one already-serialized session dictionary.

    Crash recovery uses this: it holds a snapshot taken on the UI thread and
    writes it from a worker, so it must not need the live session object.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_validated_json(target, json.dumps(dict(snapshot), indent=2))
    return target


def load_measure_session(path: Path | str) -> MeasureSession:
    """Load a Measure session, moving a damaged file aside instead of losing it.

    Raises :class:`MeasureSessionLoadError` when the file is missing, damaged
    or not a Measure session, and
    :class:`~dms.measure_session.UnsupportedMeasureSessionVersion` when it was
    written by a newer Fastgraph — that file is intact, so it must not be
    reported as damaged.
    """
    source = Path(path)
    if not source.exists():
        raise MeasureSessionLoadError(f"{source.name} does not exist.")
    data, error = load_json_with_backup(source)
    if error is not None:
        raise MeasureSessionLoadError(error)
    if data is None:
        raise MeasureSessionLoadError(f"{source.name} does not exist.")
    try:
        session = MeasureSession.from_dict(data)
    except UnsupportedMeasureSessionVersion:
        raise
    except Exception as exc:
        raise MeasureSessionLoadError(
            f"{source.name} is not a readable Measure session: {exc}"
        ) from exc
    session.source_path = str(source)
    return session


def _atomic_write_validated_json(path: Path, serialized: str) -> None:
    """Write, fsync, parse back, and only then replace the real file."""
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        # Parse-back validation: if the bytes on disk cannot be read as a
        # session, the previous file is left exactly as it was.
        MeasureSession.from_dict(json.loads(temp_path.read_text(encoding="utf-8")))
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:  # pragma: no cover - best-effort cleanup
            _LOG.debug("Could not remove the Measure session temp file %s", temp_path)


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
