from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import urlopen

from PyQt6.QtCore import QObject, pyqtSignal


@dataclass(frozen=True)
class UpdateInfo:
    latest_version: str
    release_url: str
    summary: str = ""


def parse_update_feed(url: str, timeout: float = 3.5) -> UpdateInfo:
    with urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    version = str(payload.get("version", "")).strip()
    release_url = str(payload.get("url", "")).strip()
    summary = str(payload.get("summary", "")).strip()
    if not version or not release_url:
        raise ValueError("Update feed must contain non-empty 'version' and 'url'.")
    return UpdateInfo(latest_version=version, release_url=release_url, summary=summary)


def is_remote_newer(current: str, remote: str) -> bool:
    return _normalize_version(remote) > _normalize_version(current)


def _normalize_version(value: str) -> tuple[int, ...]:
    numbers = [int(match) for match in re.findall(r"\d+", value)]
    if not numbers:
        return (0,)
    return tuple(numbers)


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
