import time

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QMessageBox

from dms.secure_store import decrypt_credentials
from dms.session import SessionData
from dms.ui.squiglink_worker import SquiglinkUploadWorker


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


def _upload_session() -> SessionData:
    return SessionData(rig="KB500X", brand="Apple", model="AirPods Pro 2", channel_side="L")


def _fake_connection(**_kwargs):
    return _FakeTransport(None), _FakeSFTP()


def test_sync_remote_phone_book_missing_create_fresh(make_main_window, monkeypatch) -> None:
    monkeypatch.setattr("dms.ui.main_window.open_sftp_connection", _fake_connection)
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

    result = make_main_window(session=_upload_session())._sync_remote_phone_book(
        host="sftp.squig.link",
        port=2022,
        username="u",
        password="p",
        phone_book_stem="Apple AirPods Pro 2 small tips",
        ask_fallback=lambda _msg: "create",
    )
    assert "updated successfully" in result.lower()
    assert written["phone_book"] == [{"name": "Apple"}]


def test_sync_remote_phone_book_missing_skip(make_main_window, monkeypatch) -> None:
    monkeypatch.setattr("dms.ui.main_window.open_sftp_connection", _fake_connection)
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

    result = make_main_window(session=_upload_session())._sync_remote_phone_book(
        host="sftp.squig.link",
        port=2022,
        username="u",
        password="p",
        phone_book_stem="Apple AirPods Pro 2 small tips",
        ask_fallback=lambda _msg: "skip",
    )
    assert "skipped" in result.lower()
    assert called["write"] == 0


def test_sync_remote_phone_book_invalid_fail(make_main_window, monkeypatch) -> None:
    monkeypatch.setattr("dms.ui.main_window.open_sftp_connection", _fake_connection)
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
        make_main_window(session=_upload_session())._sync_remote_phone_book(
            host="sftp.squig.link",
            port=2022,
            username="u",
            password="p",
            phone_book_stem="Apple AirPods Pro 2 small tips",
            ask_fallback=lambda _msg: "fail",
        )
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "canceled" in str(exc).lower()


def test_ensure_upload_metadata_returns_true_when_already_complete(make_main_window) -> None:
    window = make_main_window(session=_upload_session())
    assert window._ensure_upload_metadata() is True


def test_ensure_upload_metadata_prompts_and_saves_fields(make_main_window, monkeypatch) -> None:
    window = make_main_window(
        session=SessionData(rig="KB500X", brand="", model="", channel_side="")
    )

    class _DialogAccepted:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self) -> int:
            return 1

        def brand(self) -> str:
            return "Sennheiser"

        def model(self) -> str:
            return "HD 800 S"

        def channel_side(self) -> str:
            return "R"

    monkeypatch.setattr("dms.ui.main_window.SquiglinkUploadMetadataDialog", _DialogAccepted)
    assert window._ensure_upload_metadata() is True
    assert window._session.brand == "Sennheiser"
    assert window._session.model == "HD 800 S"
    assert window._session.channel_side == "R"


# --- threaded upload wiring in the window ----------------------------------


def _pump(qapp, predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        QThread.msleep(5)
    qapp.processEvents()
    return predicate()


def _install_worker(monkeypatch, *, upload, sync=None):
    """Force every worker the window builds to use the injected fakes."""

    def _factory(**kwargs):
        kwargs["upload"] = upload
        kwargs["sync_phone_book"] = sync or (lambda **_kw: "Phone book updated successfully.")
        return SquiglinkUploadWorker(**kwargs)

    monkeypatch.setattr("dms.ui.main_window.SquiglinkUploadWorker", _factory)


def _silence_dialogs(monkeypatch) -> dict:
    shown: dict[str, str] = {}
    monkeypatch.setattr(
        "dms.ui.main_window.QMessageBox.information",
        lambda *args, **_kw: shown.setdefault("information", args[2]),
    )
    monkeypatch.setattr(
        "dms.ui.main_window.QMessageBox.warning",
        lambda *args, **_kw: shown.setdefault("warning", args[2]),
    )
    return shown


def test_successful_upload_saves_credentials_and_pins_the_host_key(
    qapp, make_main_window, monkeypatch, tmp_path
) -> None:
    window = make_main_window()
    settings = window._settings
    assert settings.get("squiglink_credentials_encrypted") is None

    def _upload(**kwargs):
        assert kwargs["confirm_host_key"]("sftp.squig.link", 2022, "sha256:new", "ssh-ed25519")

    _install_worker(monkeypatch, upload=_upload)
    shown = _silence_dialogs(monkeypatch)
    monkeypatch.setattr(
        "dms.ui.main_window.QMessageBox.question",
        lambda *_args, **_kw: QMessageBox.StandardButton.Yes,
    )

    local = tmp_path / "Apple AirPods Pro 2 L.txt"
    local.write_text("curve", encoding="utf-8")
    window._start_squiglink_upload(
        local_path=local,
        host="sftp.squig.link",
        port=2022,
        username="user",
        password="hunter2",
        filename="Apple AirPods Pro 2 L.txt",
        phone_book_stem="Apple AirPods Pro 2",
        remember=True,
    )
    assert _pump(qapp, lambda: window._squiglink_upload_context is None)

    assert settings.get("squiglink_host_keys") == {"sftp.squig.link:2022": "sha256:new"}
    assert decrypt_credentials(settings.get("squiglink_credentials_encrypted")) == (
        "user",
        "hunter2",
    )
    assert "completed successfully" in shown["information"]
    # The temporary export is removed once the upload is done.
    assert not local.exists()


def test_failed_upload_never_persists_the_credentials(
    qapp, make_main_window, monkeypatch, tmp_path
) -> None:
    window = make_main_window()
    settings = window._settings

    def _upload(**_kwargs):
        raise OSError("Authentication failed.")

    _install_worker(monkeypatch, upload=_upload)
    shown = _silence_dialogs(monkeypatch)

    local = tmp_path / "export.txt"
    local.write_text("curve", encoding="utf-8")
    window._start_squiglink_upload(
        local_path=local,
        host="sftp.squig.link",
        port=2022,
        username="user",
        password="hunter2",
        filename="export.txt",
        phone_book_stem="Apple AirPods Pro 2",
        remember=True,
    )
    assert _pump(qapp, lambda: window._squiglink_upload_context is None)

    assert settings.get("squiglink_credentials_encrypted") is None
    assert settings.get("squiglink_host_keys") == {}
    assert "Authentication failed." in shown["warning"]


def test_declined_host_key_prompt_aborts_the_upload(
    qapp, make_main_window, monkeypatch, tmp_path
) -> None:
    window = make_main_window()

    def _upload(**kwargs):
        from dms.squiglink import SquiglinkHostKeyUnknown

        if not kwargs["confirm_host_key"]("sftp.squig.link", 2022, "sha256:new", "ssh-ed25519"):
            raise SquiglinkHostKeyUnknown("sftp.squig.link", 2022, "sha256:new", "ssh-ed25519")
        raise AssertionError("upload continued after the prompt was declined")

    _install_worker(monkeypatch, upload=_upload)
    shown = _silence_dialogs(monkeypatch)
    monkeypatch.setattr(
        "dms.ui.main_window.QMessageBox.question",
        lambda *_args, **_kw: QMessageBox.StandardButton.No,
    )

    local = tmp_path / "export.txt"
    local.write_text("curve", encoding="utf-8")
    window._start_squiglink_upload(
        local_path=local,
        host="sftp.squig.link",
        port=2022,
        username="user",
        password="hunter2",
        filename="export.txt",
        phone_book_stem="Apple AirPods Pro 2",
        remember=True,
    )
    assert _pump(qapp, lambda: window._squiglink_upload_context is None)

    assert window._settings.get("squiglink_host_keys") == {}
    assert window._settings.get("squiglink_credentials_encrypted") is None
    assert "not trusted yet" in shown["warning"]
