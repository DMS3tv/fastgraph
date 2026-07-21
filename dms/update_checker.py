from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from packaging.version import InvalidVersion, Version
from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpdateInfo:
    latest_version: str
    release_url: str
    summary: str = ""


def parse_update_feed(url: str, timeout: float = 3.5) -> UpdateInfo:
    # The empty-URL case is already screened out upstream (main_window's
    # _start_update_check bails out before ever constructing a worker when
    # update_feed_url is blank) - this is a defense-in-depth check that the
    # scheme itself is exactly https, so a misconfigured/tampered feed URL
    # (http://, file://, ftp://, ...) can never be fetched.
    if urlparse(url).scheme != "https":
        raise ValueError(f"Update feed URL must use https, got: {url!r}")

    with urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not isinstance(payload, dict):
        raise ValueError("Update feed must decode to a JSON object.")

    version = str(payload.get("version", "")).strip()
    release_url = str(payload.get("url", "")).strip()
    summary = str(payload.get("summary", "")).strip()
    if not version or not release_url:
        raise ValueError("Update feed must contain non-empty 'version' and 'url'.")
    return UpdateInfo(latest_version=version, release_url=release_url, summary=summary)


def is_remote_newer(current: str, remote: str) -> bool:
    """Compare two version strings using PEP 440 semantics (via
    `packaging.version.Version`) rather than naive digit-run tuples, so
    pre-release/build-metadata suffixes (e.g. "2.1.0-rc.1") sort correctly
    against their final release ("2.1.0") instead of comparing as greater.

    If either string fails to parse as a valid version, the comparison is
    treated conservatively as "not newer" (never prompts an update off of
    unparsable version strings) and the failure is logged.
    """
    try:
        remote_version = Version(remote)
    except InvalidVersion:
        logger.warning("Could not parse remote version %r as a version; treating as not newer.", remote)
        return False
    try:
        current_version = Version(current)
    except InvalidVersion:
        logger.warning("Could not parse current version %r as a version; treating remote as not newer.", current)
        return False
    return remote_version > current_version


class UpdateCheckWorker(QObject):
    update_available = pyqtSignal(str, str, str)
    up_to_date = pyqtSignal(str)
    check_failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, current_version: str, feed_url: str) -> None:
        super().__init__()
        self._current_version = current_version
        self._feed_url = feed_url

    def run(self) -> None:
        try:
            info = parse_update_feed(self._feed_url)
            if is_remote_newer(self._current_version, info.latest_version):
                self.update_available.emit(
                    info.latest_version, info.release_url, info.summary
                )
            else:
                self.up_to_date.emit(info.latest_version)
        except (ValueError, URLError, TimeoutError, OSError) as exc:
            self.check_failed.emit(str(exc))
        finally:
            self.finished.emit()
