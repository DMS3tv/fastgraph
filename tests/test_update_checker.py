import json
from unittest.mock import patch

import pytest

from dms.update_checker import parse_update_feed


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
