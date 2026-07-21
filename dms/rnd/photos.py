"""Managed staging and sidecar persistence for R&D photo attachments."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage

from dms.rnd.models import RnDPhoto, RnDSession

_MANAGED_JPEG = re.compile(r"^[0-9a-f]{32}\.jpg$", re.IGNORECASE)
_MAX_DIMENSION = 1600
_JPEG_QUALITY = 88


def attachment_directory(session_path: Path) -> Path:
    """Return the portable sidecar directory for an R&D session file."""
    return session_path.with_name(f"{session_path.stem}.attachments")


def _atomic_copy(source: Path, destination: Path) -> None:
    """Copy source into destination without ever leaving a partially-written file in place."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copy2(source, tmp_path)
        os.replace(tmp_path, destination)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def session_photos(session: RnDSession):
    for measurement in session.measurements:
        yield from measurement.photos
    for group in session.groups:
        yield from group.photos


class RnDPhotoStore:
    """Owns temporary JPEGs until they are materialized beside a session file."""

    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="fastgraph-rnd-photos-")
        self.root = Path(self._temporary.name)

    def add_image(self, image: QImage, *, display_name: str, caption: str = "") -> RnDPhoto:
        if image.isNull():
            raise ValueError("The selected image could not be read.")
        if max(image.width(), image.height()) > _MAX_DIMENSION:
            image = image.scaled(
                _MAX_DIMENSION,
                _MAX_DIMENSION,
                aspectRatioMode=Qt.AspectRatioMode.KeepAspectRatio,
                transformMode=Qt.TransformationMode.SmoothTransformation,
            )
        photo = RnDPhoto(display_name=display_name or "Photo", caption=caption)
        output = self.root / photo.file_name
        if not image.save(str(output), "JPG", _JPEG_QUALITY):
            raise OSError("Could not save the photo as JPEG.")
        photo.runtime_path = str(output)
        return photo

    def import_file(self, path: str | Path, *, caption: str = "") -> RnDPhoto:
        source = Path(path)
        return self.add_image(QImage(str(source)), display_name=source.stem, caption=caption)

    def hydrate_session(self, session: RnDSession, session_path: Path) -> list[str]:
        """Copy available sidecar files into staging and return missing filenames."""
        missing: list[str] = []
        source_dir = attachment_directory(session_path)
        for photo in session_photos(session):
            source = source_dir / Path(photo.file_name).name
            destination = self.root / photo.file_name
            # A merge can theoretically contain two photos with the same UUID.
            # Keep both by assigning the incoming photo a fresh managed name.
            if source.is_file() and destination.is_file() and source.read_bytes() != destination.read_bytes():
                photo.id = uuid4().hex
                photo.file_name = f"{photo.id}.jpg"
                destination = self.root / photo.file_name
            if source.is_file() and not QImage(str(source)).isNull():
                shutil.copy2(source, destination)
                photo.runtime_path = str(destination)
            else:
                photo.runtime_path = ""
                missing.append(photo.file_name)
        return missing

    def materialize_photos(self, session: RnDSession, session_path: Path) -> None:
        """Copy all available staged/runtime photos into the sidecar directory.

        Purely additive: never removes anything, so it is safe to call before the
        session JSON itself has been durably written.
        """
        destination_dir = attachment_directory(session_path)
        photos = list(session_photos(session))
        if photos:
            destination_dir.mkdir(parents=True, exist_ok=True)
        for photo in photos:
            source = Path(photo.runtime_path) if photo.runtime_path else self.root / photo.file_name
            if not source.is_file():
                continue
            destination = destination_dir / photo.file_name
            _atomic_copy(source, destination)

    def prune_stale_photos(self, session: RnDSession, session_path: Path) -> None:
        """Remove managed sidecar JPEGs no longer referenced by the session.

        Destructive: only call this after the session JSON referencing the current
        photo set has been durably written, so a failed write never orphans photos
        the on-disk session still points at.
        """
        destination_dir = attachment_directory(session_path)
        expected = {photo.file_name for photo in session_photos(session)}
        if destination_dir.exists():
            for child in destination_dir.iterdir():
                if child.is_file() and _MANAGED_JPEG.match(child.name) and child.name not in expected:
                    child.unlink()

    def save_session(self, session: RnDSession, session_path: Path) -> None:
        """Materialize all available staged photos and remove stale managed assets."""
        self.materialize_photos(session, session_path)
        self.prune_stale_photos(session, session_path)
