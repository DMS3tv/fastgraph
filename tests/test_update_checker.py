"""The update feed is the one place the app takes a URL from the network."""

import io
import json

import pytest

from dms.update_checker import (
    RELEASE_URL_PREFIX,
    UpdateCheckWorker,
    is_allowed_feed_url,
    is_allowed_release_url,
    is_remote_newer,
    parse_update_feed,
)


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def _feed(monkeypatch, payload, seen: list | None = None):
    def _urlopen(url, timeout=None):
        if seen is not None:
            seen.append(url)
        body = payload if isinstance(payload, str) else json.dumps(payload)
        return _FakeResponse(body.encode("utf-8"))

    monkeypatch.setattr("dms.update_checker.urlopen", _urlopen)


GOOD_URL = RELEASE_URL_PREFIX + "fastgraph/releases/tag/v0.5.0"


# --- URL policy ------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/feed.json",
        "file:///etc/passwd",
        "ftp://example.com/feed.json",
        "javascript:alert(1)",
        "//example.com/feed.json",
        "example.com/feed.json",
        "https://",
        "",
    ],
)
def test_non_https_feed_urls_are_rejected(url: str) -> None:
    assert is_allowed_feed_url(url) is False


def test_https_feed_url_is_accepted() -> None:
    assert is_allowed_feed_url("https://example.com/feed.json") is True
    assert is_allowed_feed_url("  https://example.com/feed.json  ") is True


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/DMS3tv/fastgraph/releases",
        "https://github.com/someone-else/fastgraph/releases",
        "https://github.com.evil.test/DMS3tv/fastgraph",
        "https://evil.test/?u=https://github.com/DMS3tv/",
        "",
    ],
)
def test_release_urls_outside_the_project_org_are_rejected(url: str) -> None:
    assert is_allowed_release_url(url) is False


def test_project_release_url_is_accepted() -> None:
    assert is_allowed_release_url(GOOD_URL) is True


# --- feed parsing ----------------------------------------------------------


def test_parse_update_feed_rejects_a_non_https_feed_without_fetching(monkeypatch) -> None:
    fetched: list = []
    _feed(monkeypatch, {"version": "9.9.9", "url": GOOD_URL}, seen=fetched)
    with pytest.raises(ValueError, match="https"):
        parse_update_feed("http://example.com/feed.json")
    assert fetched == []


def test_parse_update_feed_accepts_a_valid_payload(monkeypatch) -> None:
    _feed(monkeypatch, {"version": "0.5.0", "url": GOOD_URL, "summary": "Faster"})
    info = parse_update_feed("https://example.com/feed.json")
    assert info.latest_version == "0.5.0"
    assert info.release_url == GOOD_URL
    assert info.summary == "Faster"


def test_parse_update_feed_drops_a_foreign_release_url(monkeypatch) -> None:
    _feed(monkeypatch, {"version": "0.5.0", "url": "https://evil.test/payload.dmg"})
    with pytest.raises(ValueError, match=RELEASE_URL_PREFIX):
        parse_update_feed("https://example.com/feed.json")


def test_parse_update_feed_rejects_a_non_object_payload(monkeypatch) -> None:
    _feed(monkeypatch, "[1, 2, 3]")
    with pytest.raises(ValueError):
        parse_update_feed("https://example.com/feed.json")


# --- version comparison ----------------------------------------------------


@pytest.mark.parametrize(
    "current, remote, newer",
    [
        ("0.4.2", "0.4.10", True),  # the old digit-tuple compare got this right
        ("0.4.2", "0.10.0", True),
        ("0.9.0", "0.10.0", True),
        ("0.4.2", "0.4.2", False),
        ("0.4.2", "0.4.1", False),
        ("0.4.2", "v0.4.3", True),
        ("v0.4.2", "0.4.2", False),
        ("0.5.0", "0.5.0rc1", False),  # a pre-release is not newer than the release
        ("0.5.0rc1", "0.5.0", True),
        ("0.4.2", "not a version", False),
    ],
)
def test_version_comparison(current: str, remote: str, newer: bool) -> None:
    assert is_remote_newer(current, remote) is newer


# --- worker ----------------------------------------------------------------


def test_worker_reports_an_available_update(qapp, monkeypatch) -> None:
    _feed(monkeypatch, {"version": "9.9.9", "url": GOOD_URL, "summary": "New"})
    worker = UpdateCheckWorker(current_version="0.4.2", feed_url="https://example.com/f.json")
    seen: list = []
    worker.update_available.connect(lambda *args: seen.append(args))
    worker.run()
    assert seen == [("9.9.9", GOOD_URL, "New")]


def test_worker_fails_closed_on_an_http_feed(qapp, monkeypatch) -> None:
    _feed(monkeypatch, {"version": "9.9.9", "url": GOOD_URL})
    worker = UpdateCheckWorker(current_version="0.4.2", feed_url="http://example.com/f.json")
    failures: list = []
    updates: list = []
    worker.check_failed.connect(failures.append)
    worker.update_available.connect(lambda *args: updates.append(args))
    worker.run()
    assert updates == []
    assert failures and "https" in failures[0]
