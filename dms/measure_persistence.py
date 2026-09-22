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
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dms.file_io import atomic_write_text, ensure_extension, load_json_with_backup
from dms.measure_session import (
    MEASURE_SESSION_EXTENSION,
    MeasureSession,
    UnsupportedMeasureSessionVersion,
)

__all__ = [
    "MEASURE_SESSION_EXTENSION",
    "MeasureSessionLoadError",
    "load_measure_session",
    "save_measure_session",
    "save_measure_snapshot",
]


class MeasureSessionLoadError(ValueError):
    """A Measure session file could not be read.

    The message is written for the user and names the ``.corrupt-…`` backup
    whenever :mod:`dms.file_io` was able to move the damaged file aside.
    """


def save_measure_session(session: MeasureSession, path: Path | str) -> Path:
    """Save ``session`` to ``path`` atomically and record it as its home.

    Returns the path actually written, which is ``path`` with the canonical
    extension applied.
    """
    target = ensure_extension(path, MEASURE_SESSION_EXTENSION)
    save_measure_snapshot(session.to_dict(), target)
    session.source_path = str(target)
    return target


def save_measure_snapshot(snapshot: Mapping[str, Any], path: Path | str) -> Path:
    """Save one already-serialized session dictionary.

    Crash recovery uses this: it holds a snapshot taken on the UI thread and
    writes it from a worker, so it must not need the live session object.
    """
    target = Path(path)
    # Parse-back validation: if the bytes on disk cannot be read as a
    # session, the previous file is left exactly as it was.
    atomic_write_text(
        target,
        json.dumps(dict(snapshot), indent=2),
        validate=lambda text: MeasureSession.from_dict(json.loads(text)),
    )
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
