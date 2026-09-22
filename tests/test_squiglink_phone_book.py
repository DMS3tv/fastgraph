import base64
import hashlib
import time

import pytest

from dms.session import SessionData
from dms.squiglink import (
    SquiglinkHostKeyMismatch,
    SquiglinkHostKeyUnknown,
    build_phone_book_name_stem,
    build_upload_name_stem,
    host_key_fingerprint,
    merge_phone_book_entry,
    upload_export_sftp,
)


def _session(
    brand: str = "Apple",
    model: str = "AirPods Pro 2",
    channel_side: str = "L",
) -> SessionData:
    return SessionData(rig="KB500X", brand=brand, model=model, channel_side=channel_side)


def test_build_upload_name_stem_defaults_modifier_to_channel_side() -> None:
    s = _session()
    assert build_upload_name_stem(s, "") == "Apple AirPods Pro 2 L"


def test_build_upload_name_stem_normalizes_modifier() -> None:
    s = _session()
    assert build_upload_name_stem(s, "small tips L") == "Apple AirPods Pro 2 small tips L"
    assert build_upload_name_stem(s, "  small   tips   L1  ") == "Apple AirPods Pro 2 small tips L1"
    assert build_upload_name_stem(s, "small tips R.txt") == "Apple AirPods Pro 2 small tips R"


def test_build_upload_name_stem_requires_channel_side() -> None:
    try:
        build_upload_name_stem(_session(channel_side=""), "")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "channel side" in str(exc).lower()


def test_build_phone_book_name_stem_omits_side_suffix() -> None:
    s = _session(channel_side="L")
    assert build_phone_book_name_stem(s, "") == "Apple AirPods Pro 2"
    assert build_phone_book_name_stem(s, "L") == "Apple AirPods Pro 2"
    assert build_phone_book_name_stem(s, "R1") == "Apple AirPods Pro 2"
    assert build_phone_book_name_stem(s, "small tips L") == "Apple AirPods Pro 2 small tips"
    assert build_phone_book_name_stem(s, "small tips R2.txt") == "Apple AirPods Pro 2 small tips"


def test_merge_existing_phone_with_string_file_converts_to_list() -> None:
    phone_book = [
        {
            "name": "Apple",
            "phones": [
                {
                    "name": "AirPods Pro 2",
                    "file": "Apple AirPods Pro 2",
                    "reviewScore": "★★★★★",
                    "reviewLink": "x",
                    "price": "$250",
                    "shopLink": "y",
                    "collab": "z",
                }
            ],
        }
    ]

    merge_phone_book_entry(phone_book, _session(), "Apple AirPods Pro 2 small tips")
    phone = phone_book[0]["phones"][0]
    assert phone["file"] == ["Apple AirPods Pro 2", "Apple AirPods Pro 2 small tips"]
    assert phone["prefix"] == "Apple AirPods Pro 2"
    assert phone["reviewScore"] == "★★★★★"
    assert phone["collab"] == "z"


def test_merge_existing_phone_with_list_appends_unique_only() -> None:
    phone_book = [
        {
            "name": "Apple",
            "phones": [
                {
                    "name": "AirPods Pro 2",
                    "file": ["Apple AirPods Pro 2", "Apple AirPods Pro 2 small tips"],
                    "prefix": "Existing Prefix",
                    "reviewScore": "",
                    "reviewLink": "",
                    "price": "",
                    "shopLink": "",
                    "suffix": "demo",
                }
            ],
        }
    ]

    merge_phone_book_entry(phone_book, _session(), "Apple AirPods Pro 2 small tips")
    phone = phone_book[0]["phones"][0]
    assert phone["file"] == ["Apple AirPods Pro 2", "Apple AirPods Pro 2 small tips"]
    assert phone["prefix"] == "Existing Prefix"
    assert phone["suffix"] == "demo"


def test_merge_creates_new_brand_and_phone_minimal_shape() -> None:
    phone_book: list[dict] = []
    merge_phone_book_entry(phone_book, _session("Aful", "Explorer"), "Aful Explorer")

    assert phone_book == [
        {
            "name": "Aful",
            "phones": [
                {
                    "name": "Explorer",
                    "file": ["Aful Explorer"],
                    "reviewScore": "",
                    "reviewLink": "",
                    "price": "",
                    "shopLink": "",
                }
            ],
        }
    ]


def test_merge_matches_brand_and_model_case_and_whitespace_insensitive() -> None:
    phone_book = [
        {
            "name": "  APPLE  ",
            "phones": [
                {
                    "name": " airpods pro 2 ",
                    "file": "Apple AirPods Pro 2",
                    "reviewScore": "",
                    "reviewLink": "",
                    "price": "",
                    "shopLink": "",
                }
            ],
        }
    ]
    merge_phone_book_entry(phone_book, _session(), "Apple AirPods Pro 2 sample 2")
    phone = phone_book[0]["phones"][0]
    assert phone["file"] == ["Apple AirPods Pro 2", "Apple AirPods Pro 2 sample 2"]


def test_upload_export_sftp_targets_data_directory(monkeypatch, tmp_path) -> None:
    local = tmp_path / "local.txt"
    local.write_text("x", encoding="utf-8")
    calls = {}
    transport = install_fake_transport(monkeypatch, calls)

    diagnostics = []
    upload_export_sftp(
        local_path=local,
        host="h",
        port=2022,
        username="u",
        password="p",
        remote_filename="Apple AirPods Pro 2 L0.txt",
        diagnostic=lambda stage, details: diagnostics.append((stage, details)),
        host_keys={"h:2022": FAKE_FINGERPRINT},
    )
    assert calls["local"] == str(local)
    assert calls["remote"] == "data/Apple AirPods Pro 2 L0.txt"
    assert [stage for stage, _details in diagnostics] == [
        "connection_start",
        "transport_created",
        "host_key_verified",
        "authentication_succeeded",
        "sftp_subsystem_opened",
        "measurement_upload_start",
        "measurement_upload_complete",
    ]
    assert all("password" not in details for _stage, details in diagnostics)
    assert transport.auth_calls == [("u", "p")]


# --- Host-key trust on first use -------------------------------------------


FAKE_KEY_BYTES = b"ssh-ed25519 fake key bytes"
FAKE_FINGERPRINT = "sha256:" + base64.b64encode(
    hashlib.sha256(FAKE_KEY_BYTES).digest()
).decode().rstrip("=")


class _FakeKey:
    def __init__(self, blob: bytes = FAKE_KEY_BYTES) -> None:
        self._blob = blob

    def asbytes(self) -> bytes:
        return self._blob

    def get_name(self) -> str:
        return "ssh-ed25519"

    def get_bits(self) -> int:
        return 256


class _FakeTransport:
    """Stands in for paramiko.Transport with the key check split from auth."""

    def __init__(self, sock, key_blob: bytes = FAKE_KEY_BYTES, delay: float = 0.0) -> None:
        self.sock = sock
        self.auth_calls: list[tuple[str, str]] = []
        self.started = False
        self.closed = False
        self._key = _FakeKey(key_blob)
        self._delay = delay

    def start_client(self, timeout: float | None = None) -> None:
        if self._delay:
            time.sleep(self._delay)
        self.started = True

    def get_remote_server_key(self):
        return self._key

    def auth_password(self, username: str, password: str) -> None:
        self.auth_calls.append((username, password))

    def is_authenticated(self) -> bool:
        return bool(self.auth_calls)

    def close(self) -> None:
        self.closed = True


class _FakeSFTP:
    def __init__(self, calls: dict) -> None:
        self._calls = calls

    def put(self, local_path: str, remote_path: str) -> None:
        self._calls["local"] = local_path
        self._calls["remote"] = remote_path

    def close(self) -> None:
        pass


def install_fake_transport(
    monkeypatch,
    calls: dict | None = None,
    key_blob: bytes = FAKE_KEY_BYTES,
    delay: float = 0.0,
) -> _FakeTransport:
    """Patch the socket and paramiko entry points; return the fake transport."""
    holder: dict[str, _FakeTransport] = {}

    def _make_transport(sock):
        holder["transport"] = _FakeTransport(sock, key_blob=key_blob, delay=delay)
        return holder["transport"]

    monkeypatch.setattr(
        "dms.squiglink.socket.create_connection",
        lambda _addr, timeout=None: object(),
    )
    monkeypatch.setattr("dms.squiglink.paramiko.Transport", _make_transport)
    monkeypatch.setattr(
        "dms.squiglink.paramiko.SFTPClient.from_transport",
        lambda _transport: _FakeSFTP(calls if calls is not None else {}),
    )

    class _Proxy:
        def __getattr__(self, name):
            return getattr(holder["transport"], name)

    return _Proxy()


def test_host_key_fingerprint_matches_openssh_digest() -> None:
    assert host_key_fingerprint(_FakeKey()) == FAKE_FINGERPRINT
    assert "=" not in host_key_fingerprint(_FakeKey())


def test_unknown_host_key_is_offered_to_the_callback(monkeypatch, tmp_path) -> None:
    local = tmp_path / "local.txt"
    local.write_text("x", encoding="utf-8")
    transport = install_fake_transport(monkeypatch, {})
    seen: list[tuple] = []

    upload_export_sftp(
        local_path=local,
        host="sftp.squig.link",
        port=2022,
        username="u",
        password="p",
        host_keys={},
        confirm_host_key=lambda *args: seen.append(args) or True,
    )
    assert seen == [("sftp.squig.link", 2022, FAKE_FINGERPRINT, "ssh-ed25519")]
    assert transport.auth_calls == [("u", "p")]


def test_unknown_host_key_without_callback_is_rejected(monkeypatch, tmp_path) -> None:
    local = tmp_path / "local.txt"
    local.write_text("x", encoding="utf-8")
    transport = install_fake_transport(monkeypatch, {})

    with pytest.raises(SquiglinkHostKeyUnknown):
        upload_export_sftp(
            local_path=local,
            host="sftp.squig.link",
            port=2022,
            username="u",
            password="p",
            host_keys={},
        )
    assert transport.auth_calls == []
    assert transport.closed is True


def test_declined_host_key_never_sends_credentials(monkeypatch, tmp_path) -> None:
    local = tmp_path / "local.txt"
    local.write_text("x", encoding="utf-8")
    transport = install_fake_transport(monkeypatch, {})

    with pytest.raises(SquiglinkHostKeyUnknown):
        upload_export_sftp(
            local_path=local,
            host="sftp.squig.link",
            port=2022,
            username="u",
            password="p",
            host_keys={},
            confirm_host_key=lambda *_args: False,
        )
    assert transport.auth_calls == []


def test_changed_host_key_raises_before_authentication(monkeypatch, tmp_path) -> None:
    local = tmp_path / "local.txt"
    local.write_text("x", encoding="utf-8")
    transport = install_fake_transport(monkeypatch, {}, key_blob=b"a different server key")
    stages: list[str] = []

    with pytest.raises(SquiglinkHostKeyMismatch) as excinfo:
        upload_export_sftp(
            local_path=local,
            host="sftp.squig.link",
            port=2022,
            username="u",
            password="hunter2",
            host_keys={"sftp.squig.link:2022": FAKE_FINGERPRINT},
            confirm_host_key=lambda *_args: True,
            diagnostic=lambda stage, _details: stages.append(stage),
        )

    assert transport.auth_calls == []
    assert transport.closed is True
    assert excinfo.value.expected == FAKE_FINGERPRINT
    assert excinfo.value.actual != FAKE_FINGERPRINT
    assert "host_key_mismatch" in stages
    # The confirmation callback must not be consulted for a pinned host.
    assert "host_key_verified" not in stages


def test_connection_failure_scrubs_the_password_from_diagnostics(monkeypatch, tmp_path) -> None:
    local = tmp_path / "local.txt"
    local.write_text("x", encoding="utf-8")
    install_fake_transport(monkeypatch, {})
    monkeypatch.setattr(
        "dms.squiglink.paramiko.SFTPClient.from_transport",
        lambda _transport: (_ for _ in ()).throw(RuntimeError("server said: bad password hunter2")),
    )
    details: list[dict] = []

    with pytest.raises(RuntimeError):
        upload_export_sftp(
            local_path=local,
            host="h",
            port=2022,
            username="u",
            password="hunter2",
            host_keys={"h:2022": FAKE_FINGERPRINT},
            diagnostic=lambda stage, payload: (
                details.append(payload) if stage == "connection_failed" else None
            ),
        )

    assert details
    assert "hunter2" not in details[0]["exception_message"]
    assert "***" in details[0]["exception_message"]
