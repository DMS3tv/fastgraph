"""Atomic persistence helpers for R&D sessions and photo sidecars."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dms.file_io import atomic_write_text, same_session_file
from dms.rnd.models import RnDSession
from dms.rnd.photos import RnDPhotoStore, attachment_directory, session_photos

_MANAGED_JPEG = re.compile(r"^[0-9a-f]{32}\.jpg$", re.IGNORECASE)
RND_SESSION_EXTENSION = ".fastgraph-rnd.json"

_LOG = logging.getLogger(__name__)


def session_snapshot(
    session: RnDSession,
    photo_store: RnDPhotoStore,
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Return an immutable session dictionary and available photo sources."""
    sources: dict[str, Path] = {}
    for photo in session_photos(session):
        source = (
            Path(photo.runtime_path) if photo.runtime_path else photo_store.root / photo.file_name
        )
        if source.is_file():
            sources[Path(photo.file_name).name] = source
    return session.to_dict(), sources


def save_rnd_session(
    session: RnDSession,
    photo_store: RnDPhotoStore,
    path: Path,
    *,
    cleanup_stale_photos: bool = True,
) -> None:
    """Save ``session`` to ``path``.

    Stale managed JPEGs are only removed when ``path`` is the file this session
    already lives in. A Save As to another location leaves that location's
    attachments alone: they belong to whatever session was there before, and
    deleting them would destroy another session's photos.
    """
    path = Path(path)
    snapshot, photo_sources = session_snapshot(session, photo_store)
    same_destination = same_session_file(getattr(session, "source_path", ""), path)
    cleanup = bool(cleanup_stale_photos) and same_destination
    if cleanup_stale_photos and not cleanup:
        stale = _stale_attachments(path, snapshot)
        if stale:
            _LOG.warning(
                "Left %d unreferenced photo file(s) in %s: this save went to a "
                "different location than the loaded session, so they were kept.",
                len(stale),
                attachment_directory(path),
            )
    save_rnd_snapshot(
        snapshot,
        photo_sources,
        path,
        cleanup_stale_photos=cleanup,
    )
    session.source_path = str(path)


def _stale_attachments(path: Path, snapshot: Mapping[str, Any]) -> list[str]:
    """Managed JPEGs in ``path``'s sidecar that ``snapshot`` does not reference."""
    directory = attachment_directory(Path(path))
    if not directory.exists():
        return []
    expected = _expected_photo_names(snapshot)
    return [
        child.name
        for child in directory.iterdir()
        if child.is_file() and child.name not in expected and _MANAGED_JPEG.match(child.name)
    ]


def _expected_photo_names(snapshot: Mapping[str, Any]) -> set[str]:
    return {
        Path(str(item.get("file_name") or "")).name
        for collection in ("measurements", "groups")
        for owner in snapshot.get(collection, []) or []
        for item in owner.get("photos", []) or []
        if item.get("file_name")
    }


def save_rnd_snapshot(
    snapshot: Mapping[str, Any],
    photo_sources: Mapping[str, Path],
    path: Path,
    *,
    cleanup_stale_photos: bool = False,
) -> None:
    """Save one validated snapshot. Replace the JSON manifest atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    destination_dir = attachment_directory(path)
    expected = _expected_photo_names(snapshot)

    if expected:
        destination_dir.mkdir(parents=True, exist_ok=True)
    for file_name in expected:
        source = photo_sources.get(file_name)
        if source is None or not Path(source).is_file():
            continue
        destination = destination_dir / file_name
        if destination.is_file():
            continue
        _atomic_copy(Path(source), destination)

    if cleanup_stale_photos and destination_dir.exists():
        for child in destination_dir.iterdir():
            if child.is_file() and child.name not in expected and _MANAGED_JPEG.match(child.name):
                child.unlink()

    atomic_write_text(
        path,
        json.dumps(dict(snapshot), indent=2),
        validate=lambda text: RnDSession.from_dict(json.loads(text)),
    )


def load_rnd_session(
    path: Path,
    photo_store: RnDPhotoStore | None = None,
) -> tuple[RnDSession, list[str]]:
    path = Path(path)
    session = RnDSession.from_dict(json.loads(path.read_text(encoding="utf-8")))
    session.source_path = str(path)
    missing = photo_store.hydrate_session(session, path) if photo_store is not None else []
    return session, missing


def copy_session_bundle(source: Path, destination: Path) -> None:
    """Copy one valid session and its referenced photos to a new bundle."""
    source = Path(source)
    destination = Path(destination)
    session, _missing = load_rnd_session(source)
    source_dir = attachment_directory(source)
    sources = {
        Path(photo.file_name).name: source_dir / Path(photo.file_name).name
        for photo in session_photos(session)
        if (source_dir / Path(photo.file_name).name).is_file()
    }
    save_rnd_snapshot(
        session.to_dict(),
        sources,
        destination,
        cleanup_stale_photos=True,
    )


def remove_session_bundle(path: Path) -> None:
    path = Path(path)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    sidecar = attachment_directory(path)
    if sidecar.exists():
        shutil.rmtree(sidecar)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        shutil.copy2(source, temp_path)
        with temp_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()
