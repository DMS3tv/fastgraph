"""Atomic persistence helpers for R&D sessions and photo sidecars."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from dms.rnd.models import RnDSession
from dms.rnd.photos import RnDPhotoStore, attachment_directory, session_photos

_MANAGED_JPEG = re.compile(r"^[0-9a-f]{32}\.jpg$", re.IGNORECASE)


def session_snapshot(
    session: RnDSession,
    photo_store: RnDPhotoStore,
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Return an immutable session dictionary and available photo sources."""
    sources: dict[str, Path] = {}
    for photo in session_photos(session):
        source = Path(photo.runtime_path) if photo.runtime_path else photo_store.root / photo.file_name
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
    snapshot, photo_sources = session_snapshot(session, photo_store)
    save_rnd_snapshot(
        snapshot,
        photo_sources,
        path,
        cleanup_stale_photos=cleanup_stale_photos,
    )


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
    expected = {
        Path(str(item.get("file_name") or "")).name
        for collection in ("measurements", "groups")
        for owner in snapshot.get(collection, []) or []
        for item in owner.get("photos", []) or []
        if item.get("file_name")
    }

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

    serialized = json.dumps(dict(snapshot), indent=2)
    _atomic_write_validated_json(path, serialized)


def load_rnd_session(
    path: Path,
    photo_store: RnDPhotoStore | None = None,
) -> tuple[RnDSession, list[str]]:
    path = Path(path)
    session = RnDSession.from_dict(json.loads(path.read_text(encoding="utf-8")))
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
    try:
        path.unlink()
    except FileNotFoundError:
        pass
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
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _atomic_write_validated_json(path: Path, serialized: str) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        RnDSession.from_dict(json.loads(temp_path.read_text(encoding="utf-8")))
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


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
