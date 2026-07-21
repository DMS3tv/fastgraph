import json
from unittest.mock import patch

import pytest

from dms.update_checker import is_remote_newer, parse_update_feed


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info) -> None:
        return None


def _urlopen_returning(body) -> object:
    encoded = json.dumps(body).encode("utf-8")
    return _FakeResponse(encoded)


@pytest.mark.parametrize("payload", [[], "ok", None, 42])
def test_parse_update_feed_rejects_non_object_payloads(payload) -> None:
    with patch(
        "dms.update_checker.urlopen", return_value=_urlopen_returning(payload)
    ):
        with pytest.raises(ValueError):
            parse_update_feed("https://example.com/feed.json")


def test_parse_update_feed_parses_minimal_valid_dict() -> None:
    body = {"version": "1.2.3", "url": "https://example.com/release", "summary": "Notes"}
    with patch("dms.update_checker.urlopen", return_value=_urlopen_returning(body)):
        info = parse_update_feed("https://example.com/feed.json")
    assert info.latest_version == "1.2.3"
    assert info.release_url == "https://example.com/release"
    assert info.summary == "Notes"


# --- M12(a): feed URL scheme must be https --------------------------------


@pytest.mark.parametrize("url", ["http://example.com/feed.json", "file:///etc/passwd"])
def test_parse_update_feed_rejects_non_https_scheme(url) -> None:
    with patch("dms.update_checker.urlopen") as mock_urlopen:
        with pytest.raises(ValueError):
            parse_update_feed(url)
        # Must fail closed *before* ever attempting the network call.
        mock_urlopen.assert_not_called()


# --- M13: version comparison via packaging.version, not digit-run tuples --


@pytest.mark.parametrize(
    "current, remote, expected",
    [
        ("2.1.0", "2.1.0-rc.1", False),   # a pre-release never beats its final release
        ("2.1.0-rc.1", "2.1.0", True),    # ...but the final release beats the pre-release
        ("2.1.0", "2.1.0", False),        # equal versions are never "newer"
        ("2.1.0", "2.2.0b1", True),       # a beta of a later series still beats an earlier final
        ("2.1.0", "2.2.0", True),
        ("1.9.0", "1.10.0", True),        # digit-run comparison would get this backwards
    ],
)
def test_is_remote_newer_table(current, remote, expected) -> None:
    assert is_remote_newer(current, remote) is expected


def test_is_remote_newer_malformed_remote_treated_as_not_newer() -> None:
    assert is_remote_newer("2.1.0", "not-a-version!!") is False


def test_is_remote_newer_malformed_local_treated_as_not_newer() -> None:
    assert is_remote_newer("not-a-version!!", "2.1.0") is False
