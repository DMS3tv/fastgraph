from types import SimpleNamespace

from dms.session import SessionData
from dms.ui.main_window import MainWindow


class _FakeTransport:
    def __init__(self, _addr):
        self.connected = False

    def connect(self, username: str, password: str) -> None:
        assert username
        assert password
        self.connected = True

    def close(self) -> None:
        self.connected = False


class _FakeSFTP:
    def close(self) -> None:
        pass


def _fake_self(mode: str):
    return SimpleNamespace(
        _session=SessionData(rig="KB500X", brand="Apple", model="AirPods Pro 2", channel_side="L"),
        _ask_phone_book_fallback_mode=lambda _msg: mode,
    )


def test_sync_remote_phone_book_missing_create_fresh(monkeypatch) -> None:
    monkeypatch.setattr("dms.ui.main_window.paramiko.Transport", _FakeTransport)
    monkeypatch.setattr(
        "dms.ui.main_window.paramiko.SFTPClient.from_transport",
        lambda _transport: _FakeSFTP(),
    )
    monkeypatch.setattr(
        "dms.ui.main_window.read_remote_phone_book",
        lambda _sftp, _path: (_ for _ in ()).throw(FileNotFoundError("missing")),
    )
    written = {}
    monkeypatch.setattr(
        "dms.ui.main_window.merge_phone_book_entry",
        lambda phone_book, _session, _stem: phone_book.append({"name": "Apple"}),
    )
    monkeypatch.setattr(
        "dms.ui.main_window.write_remote_phone_book",
        lambda _sftp, phone_book, _path: written.setdefault("phone_book", phone_book),
    )
    monkeypatch.setattr(
        "dms.ui.main_window.RemotePhoneBookMissingError",
        FileNotFoundError,
    )
    monkeypatch.setattr(
        "dms.ui.main_window.RemotePhoneBookInvalidError",
        ValueError,
    )

    result = MainWindow._sync_remote_phone_book(
        _fake_self("create"),
        host="sftp.squig.link",
        port=2022,
        username="u",
        password="p",
        phone_book_stem="Apple AirPods Pro 2 small tips",
    )
    assert "updated successfully" in result.lower()
    assert written["phone_book"] == [{"name": "Apple"}]


def test_sync_remote_phone_book_missing_skip(monkeypatch) -> None:
    monkeypatch.setattr("dms.ui.main_window.paramiko.Transport", _FakeTransport)
    monkeypatch.setattr(
        "dms.ui.main_window.paramiko.SFTPClient.from_transport",
        lambda _transport: _FakeSFTP(),
    )
    monkeypatch.setattr(
        "dms.ui.main_window.read_remote_phone_book",
        lambda _sftp, _path: (_ for _ in ()).throw(FileNotFoundError("missing")),
    )
    monkeypatch.setattr(
        "dms.ui.main_window.RemotePhoneBookMissingError",
        FileNotFoundError,
    )
    monkeypatch.setattr(
        "dms.ui.main_window.RemotePhoneBookInvalidError",
        ValueError,
    )
    called = {"write": 0}
    monkeypatch.setattr(
        "dms.ui.main_window.write_remote_phone_book",
        lambda *_args, **_kwargs: called.__setitem__("write", called["write"] + 1),
    )

    result = MainWindow._sync_remote_phone_book(
        _fake_self("skip"),
        host="sftp.squig.link",
        port=2022,
        username="u",
        password="p",
        phone_book_stem="Apple AirPods Pro 2 small tips",
    )
    assert "skipped" in result.lower()
    assert called["write"] == 0


def test_sync_remote_phone_book_invalid_fail(monkeypatch) -> None:
    monkeypatch.setattr("dms.ui.main_window.paramiko.Transport", _FakeTransport)
    monkeypatch.setattr(
        "dms.ui.main_window.paramiko.SFTPClient.from_transport",
        lambda _transport: _FakeSFTP(),
    )
    monkeypatch.setattr(
        "dms.ui.main_window.read_remote_phone_book",
        lambda _sftp, _path: (_ for _ in ()).throw(ValueError("bad json")),
    )
    monkeypatch.setattr(
        "dms.ui.main_window.RemotePhoneBookMissingError",
        FileNotFoundError,
    )
    monkeypatch.setattr(
        "dms.ui.main_window.RemotePhoneBookInvalidError",
        ValueError,
    )

    try:
        MainWindow._sync_remote_phone_book(
            _fake_self("fail"),
            host="sftp.squig.link",
            port=2022,
            username="u",
            password="p",
            phone_book_stem="Apple AirPods Pro 2 small tips",
        )
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "canceled" in str(exc).lower()
