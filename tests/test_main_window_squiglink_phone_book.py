"""main_window-level Squiglink tests.

NOTE: this module cannot actually run in this environment - importing
dms.ui.main_window drags in dms.ui.rnd_widget -> dms.ui.rnd_photo_dialogs ->
PyQt6.QtMultimedia, which needs libpulse.so.0 (not installed here). It is
kept up to date by static rewrite and will run once libpulse0 is available.

The old `_sync_remote_phone_book`-based tests are gone: that method was
moved (not duplicated) into SquiglinkUploadWorker.run() /
SquiglinkUploadWorker._sync_phone_book (dms/ui/squiglink_worker.py) as part
of H2 (background worker thread). Equivalent coverage now lives in
tests/test_squiglink_worker.py, which imports only the worker module (a
plain QObject with no QtMultimedia dependency) and actually runs here.

What remains here are the still-relevant MainWindow-side unit tests: the
metadata-completion prompt (unchanged), and the new slots that glue the
worker's signals back into UI state (M9 credential persistence, host-key
trust prompt, phone-book fallback prompt, and the upload re-entrancy
guard).
"""

from types import SimpleNamespace

from PyQt6.QtWidgets import QMessageBox

from dms.secure_store import decrypt_credentials
from dms.session import SessionData
from dms.ui.main_window import MainWindow


class _FakeSettings:
    def __init__(self, initial: dict | None = None) -> None:
        self._data: dict = dict(initial or {})
        self.set_calls: list[tuple[str, object]] = []

    def get(self, key: str):
        return self._data.get(key)

    def set(self, key: str, value) -> None:
        self.set_calls.append((key, value))
        self._data[key] = value


class _FakeStatusBar:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def showMessage(self, message: str) -> None:
        self.messages.append(message)


# --- _ensure_upload_metadata (unchanged by H1/H2/M9) ------------------------


def test_ensure_upload_metadata_returns_true_when_already_complete() -> None:
    fake = SimpleNamespace(
        _session=SessionData(rig="KB500X", brand="Apple", model="AirPods Pro 2", channel_side="L")
    )
    assert MainWindow._ensure_upload_metadata(fake) is True


def test_ensure_upload_metadata_prompts_and_saves_fields(monkeypatch) -> None:
    fake = SimpleNamespace(
        _session=SessionData(rig="KB500X", brand="", model="", channel_side="")
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
    assert MainWindow._ensure_upload_metadata(fake) is True
    assert fake._session.brand == "Sennheiser"
    assert fake._session.model == "HD 800 S"
    assert fake._session.channel_side == "R"


# --- _upload_to_squiglink re-entrancy guard ----------------------------------


def test_upload_to_squiglink_reentry_guard_when_already_running() -> None:
    fake = SimpleNamespace(
        _squiglink_thread=object(),  # any non-None sentinel means "in flight"
        _statusbar=_FakeStatusBar(),
    )
    MainWindow._upload_to_squiglink(fake)
    assert fake._statusbar.messages == ["A Squiglink upload is already in progress."]


# --- _on_squiglink_succeeded / _on_squiglink_failed (M9) --------------------


def _fake_self_for_succeeded(remember: bool):
    calls = {"finish": 0}
    fake = SimpleNamespace(
        _settings=_FakeSettings(),
        _statusbar=_FakeStatusBar(),
        _log_event=lambda *_a, **_k: None,
        _squiglink_pending_remember=remember,
        _squiglink_pending_username="user1",
        _squiglink_pending_password="pass1",
        _squiglink_config=SimpleNamespace(remote_filename="Apple AirPods Pro 2 L.txt"),
        _finish_squiglink_operation=lambda: calls.__setitem__("finish", calls["finish"] + 1),
    )
    return fake, calls


def test_on_squiglink_succeeded_persists_credentials_when_remember_true(monkeypatch) -> None:
    info_calls = []
    monkeypatch.setattr(
        "dms.ui.main_window.QMessageBox.information",
        lambda *args, **kwargs: info_calls.append((args, kwargs)),
    )
    fake, calls = _fake_self_for_succeeded(remember=True)

    MainWindow._on_squiglink_succeeded(fake, "Phone book updated successfully.")

    saved = dict(fake._settings.set_calls)
    assert saved["squiglink_remember_credentials"] is True
    assert saved["squiglink_credentials_encrypted"] is not None
    assert decrypt_credentials(saved["squiglink_credentials_encrypted"]) == ("user1", "pass1")
    assert calls["finish"] == 1
    assert info_calls, "expected an Upload Complete message box"


def test_on_squiglink_succeeded_clears_credentials_when_remember_false(monkeypatch) -> None:
    monkeypatch.setattr("dms.ui.main_window.QMessageBox.information", lambda *a, **k: None)
    fake, calls = _fake_self_for_succeeded(remember=False)

    MainWindow._on_squiglink_succeeded(fake, "Phone book updated successfully.")

    saved = dict(fake._settings.set_calls)
    assert saved["squiglink_remember_credentials"] is False
    assert saved["squiglink_credentials_encrypted"] is None
    assert calls["finish"] == 1


def test_on_squiglink_failed_never_touches_saved_credentials(monkeypatch) -> None:
    # M9: a failed attempt (bad password, host-key rejection, network error,
    # cancellation, ...) must never write squiglink_remember_credentials /
    # squiglink_credentials_encrypted - any previously-saved credentials
    # must be left exactly as they were.
    warn_calls = []
    monkeypatch.setattr(
        "dms.ui.main_window.QMessageBox.warning",
        lambda *args, **kwargs: warn_calls.append((args, kwargs)),
    )
    calls = {"finish": 0}
    fake = SimpleNamespace(
        _settings=_FakeSettings(),
        _statusbar=_FakeStatusBar(),
        _log_event=lambda *_a, **_k: None,
        _finish_squiglink_operation=lambda: calls.__setitem__("finish", calls["finish"] + 1),
    )

    MainWindow._on_squiglink_failed(fake, "Incorrect username or password.")

    assert fake._settings.set_calls == []
    assert calls["finish"] == 1
    assert warn_calls, "expected an Upload Failed message box"


# --- _on_squiglink_host_key_needed (H1 trust-on-first-use prompt) -----------


def test_on_squiglink_host_key_needed_trusts_and_relaunches(monkeypatch) -> None:
    monkeypatch.setattr(
        "dms.ui.main_window.QMessageBox.question",
        lambda *_a, **_k: QMessageBox.StandardButton.Yes,
    )
    relaunch_calls = []
    close_calls = {"n": 0}
    config = SimpleNamespace(host="sftp.squig.link", port=2022, known_key=None)
    fake = SimpleNamespace(
        _settings=_FakeSettings(),
        _squiglink_config=config,
        _close_squiglink_progress=lambda: close_calls.__setitem__("n", close_calls["n"] + 1),
        _start_squiglink_worker=lambda cfg: relaunch_calls.append(cfg),
        _on_squiglink_failed=lambda _msg: (_ for _ in ()).throw(
            AssertionError("must not fail when the user trusts the key")
        ),
    )

    MainWindow._on_squiglink_host_key_needed(fake, "ssh-ed25519 AAAA", "SHA256:abc")

    assert close_calls["n"] == 1
    assert fake._settings.get("squiglink_host_keys") == {
        "sftp.squig.link:2022": "ssh-ed25519 AAAA"
    }
    assert relaunch_calls == [config]
    assert config.known_key == "ssh-ed25519 AAAA"


def test_on_squiglink_host_key_needed_declines_fails_and_stores_nothing(monkeypatch) -> None:
    monkeypatch.setattr(
        "dms.ui.main_window.QMessageBox.question",
        lambda *_a, **_k: QMessageBox.StandardButton.No,
    )
    failed_calls = []
    config = SimpleNamespace(host="sftp.squig.link", port=2022, known_key=None)
    fake = SimpleNamespace(
        _settings=_FakeSettings(),
        _squiglink_config=config,
        _close_squiglink_progress=lambda: None,
        _start_squiglink_worker=lambda _cfg: (_ for _ in ()).throw(
            AssertionError("must not relaunch when the user declines to trust the key")
        ),
        _on_squiglink_failed=lambda msg: failed_calls.append(msg),
    )

    MainWindow._on_squiglink_host_key_needed(fake, "ssh-ed25519 AAAA", "SHA256:abc")

    assert failed_calls and "not trusted" in failed_calls[0].lower()
    # Declining must not persist a host key that was never actually trusted.
    assert fake._settings.set_calls == []
    assert config.known_key is None


# --- _on_squiglink_phone_book_decision ---------------------------------------


def test_on_squiglink_phone_book_decision_relaunches_with_skip_upload() -> None:
    relaunch_calls = []
    config = SimpleNamespace(fallback_mode=None, skip_upload=False)
    fake = SimpleNamespace(
        _squiglink_config=config,
        _close_squiglink_progress=lambda: None,
        _ask_phone_book_fallback_mode=lambda _detail: "create",
        _start_squiglink_worker=lambda cfg: relaunch_calls.append(cfg),
        _on_squiglink_failed=lambda _msg: (_ for _ in ()).throw(
            AssertionError("must not fail on a create/skip decision")
        ),
    )

    MainWindow._on_squiglink_phone_book_decision(fake, "missing", "detail text")

    # The measurement upload already succeeded in phase 1 (the worker never
    # reaches phone_book_decision_needed without it - see
    # test_squiglink_worker.py::test_skip_upload_false_uploads_before_phone_book_sync)
    # so the relaunch must not repeat it.
    assert config.fallback_mode == "create"
    assert config.skip_upload is True
    assert relaunch_calls == [config]


def test_on_squiglink_phone_book_decision_fail_mode_fails_upload() -> None:
    failed_calls = []
    config = SimpleNamespace(fallback_mode=None, skip_upload=False)
    fake = SimpleNamespace(
        _squiglink_config=config,
        _close_squiglink_progress=lambda: None,
        _ask_phone_book_fallback_mode=lambda _detail: "fail",
        _start_squiglink_worker=lambda _cfg: (_ for _ in ()).throw(
            AssertionError("must not relaunch on a fail decision")
        ),
        _on_squiglink_failed=lambda msg: failed_calls.append(msg),
    )

    MainWindow._on_squiglink_phone_book_decision(fake, "missing", "detail text")

    assert failed_calls and "phone book" in failed_calls[0].lower()
    assert config.fallback_mode is None
    assert config.skip_upload is False
