from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from packaging.version import InvalidVersion, Version
from PyQt6.QtCore import QObject, pyqtSignal

# The feed decides which URL the app hands to the OS browser. Both ends are
# constrained: the feed itself must be fetched over TLS, and the release link
# it advertises must point at this project's own GitHub organisation, so a
# hijacked or mistyped feed cannot steer users to an arbitrary download.
RELEASE_URL_PREFIX = "https://github.com/DMS3tv/"


@dataclass(frozen=True)
class UpdateInfo:
    latest_version: str
    release_url: str
    summary: str = ""


def is_allowed_feed_url(url: str) -> bool:
    """True when the feed URL is an absolute https:// URL with a host."""
    try:
        parsed = urlparse(str(url).strip())
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


def is_allowed_release_url(url: str) -> bool:
    """True when the release link points inside the project's GitHub org."""
    return str(url or "").strip().startswith(RELEASE_URL_PREFIX)


def parse_update_feed(url: str, timeout: float = 3.5) -> UpdateInfo:
    feed_url = str(url).strip()
    if not is_allowed_feed_url(feed_url):
        raise ValueError("Update feed URL must be an absolute https:// URL.")

    with urlopen(feed_url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not isinstance(payload, dict):
        raise ValueError("Update feed must be a JSON object.")

    version = str(payload.get("version", "")).strip()
    release_url = str(payload.get("url", "")).strip()
    summary = str(payload.get("summary", "")).strip()
    if not version or not release_url:
        raise ValueError("Update feed must contain non-empty 'version' and 'url'.")
    if not is_allowed_release_url(release_url):
        raise ValueError(f"Update feed release URL must start with {RELEASE_URL_PREFIX}.")
    return UpdateInfo(latest_version=version, release_url=release_url, summary=summary)


def is_remote_newer(current: str, remote: str) -> bool:
    return _normalize_version(remote) > _normalize_version(current)


def _normalize_version(value: str) -> Version:
    """Parse a version leniently: "v0.4.2 Beta" and "0.4.2" compare equal."""
    text = str(value or "").strip()
    if text[:1].lower() == "v":
        text = text[1:]
    try:
        return Version(text)
    except InvalidVersion:
        pass
    numbers = re.findall(r"\d+", text)
    if not numbers:
        return Version("0")
    return Version(".".join(numbers[:4]))


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
                self.update_available.emit(info.latest_version, info.release_url, info.summary)
            else:
                self.up_to_date.emit(info.latest_version)
        except (ValueError, URLError, TimeoutError, OSError) as exc:
            self.check_failed.emit(str(exc))
        finally:
            self.finished.emit()
