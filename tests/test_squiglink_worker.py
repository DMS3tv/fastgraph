"""Tests for SquiglinkUploadWorker.run() (dms/ui/squiglink_worker.py).

This module deliberately imports only dms.ui.squiglink_worker, never
dms.ui.main_window - the worker is a plain QObject with no QtMultimedia
dependency, so (unlike most main_window-adjacent tests) it can actually run
in this environment (libpulse0 is missing here, which breaks any import that
drags in dms.ui.rnd_photo_dialogs -> PyQt6.QtMultimedia).

We call worker.run() directly (not via a QThread) - PyQt signal/slot
connections work synchronously without an event loop as long as no
cross-thread delivery is involved, which is exactly the case here.

Note on M9 (credentials-after-success): persisting
squiglink_remember_credentials / squiglink_credentials_encrypted is
main_window's responsibility (in _on_squiglink_succeeded), not the worker's.
The worker never touches SettingsManager at all, so there is nothing to
test for M9 at this layer - it's covered by reading main_window.py's
_on_squiglink_succeeded / _on_squiglink_failed, which only write credentials
in the success slot.
"""

import pytest

from dms.session import SessionData
from dms.squiglink import (
    HostKeyMismatchError,
    HostKeyUnverifiedError,
    RemotePhoneBookInvalidError,
    RemotePhoneBookMissingError,
    RemotePhoneBookReadError,
)
from dms.ui.squiglink_worker import SquiglinkUploadConfig, SquiglinkUploadWorker


class _FakeTransport:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeSFTP:
    def __init__(self) -> None:
        self.put_calls: list[tuple[str, str]] = []

    def put(self, local_path: str, remote_path: str) -> None:
        self.put_calls.append((local_path, remote_path))


def _session(brand: str = "Apple", model: str = "AirPods Pro 2") -> SessionData:
    return SessionData(rig="KB500X", brand=brand, model=model, channel_side="L")


def _config(**overrides) -> SquiglinkUploadConfig:
    base = dict(
        host="sftp.squig.link",
        port=2022,
        username="u",
        password="p",
        known_key="ssh-ed25519 AAAA",
        local_path="/tmp/fake.txt",
        remote_filename="Apple AirPods Pro 2 L.txt",
        session=_session(),
        phone_book_stem="Apple AirPods Pro 2",
        fallback_mode=None,
        skip_upload=False,
    )
    base.update(overrides)
    return SquiglinkUploadConfig(**base)


def _collect_signals(worker: SquiglinkUploadWorker) -> list[tuple]:
    events: list[tuple] = []
    worker.succeeded.connect(lambda msg: events.append(("succeeded", msg)))
    worker.failed.connect(lambda msg: events.append(("failed", msg)))
    worker.host_key_needed.connect(
        lambda key_str, fingerprint: events.append(("host_key_needed", key_str, fingerprint))
    )
    worker.phone_book_decision_needed.connect(
        lambda kind, detail: events.append(("phone_book_decision_needed", kind, detail))
    )
    worker.finished.connect(lambda: events.append(("finished",)))
    return events


def _kinds(events: list[tuple]) -> list[str]:
    return [e[0] for e in events]


# --- host-key verification (step a) ----------------------------------------


def test_host_key_unverified_emits_host_key_needed(monkeypatch) -> None:
    def _raise_unverified(**_kwargs):
        raise HostKeyUnverifiedError("ssh-ed25519 AAAA", "SHA256:abc")

    monkeypatch.setattr(
        "dms.ui.squiglink_worker.open_sftp_session", _raise_unverified
    )
    worker = SquiglinkUploadWorker(_config())
    events = _collect_signals(worker)

    worker.run()

    assert _kinds(events) == ["host_key_needed", "finished"]
    assert events[0][1:] == ("ssh-ed25519 AAAA", "SHA256:abc")


def test_host_key_mismatch_emits_failed_with_mitm_warning(monkeypatch) -> None:
    def _raise_mismatch(**_kwargs):
        raise HostKeyMismatchError("SHA256:expected", "SHA256:actual")

    monkeypatch.setattr("dms.ui.squiglink_worker.open_sftp_session", _raise_mismatch)
    worker = SquiglinkUploadWorker(_config())
    events = _collect_signals(worker)

    worker.run()

    assert _kinds(events) == ["failed", "finished"]
    message = events[0][1]
    assert "man-in-the-middle" in message.lower()
    assert "SHA256:expected" in message
    assert "SHA256:actual" in message
    assert "squiglink_host_keys" in message


# --- phone-book fallback decisions (step c) --------------------------------


def test_missing_phone_book_emits_decision_signal_and_closes_first(monkeypatch) -> None:
    transport = _FakeTransport()
    sftp = _FakeSFTP()
    monkeypatch.setattr(
        "dms.ui.squiglink_worker.open_sftp_session", lambda **_kw: (transport, sftp)
    )

    def _raise_missing(_sftp, _path):
        raise RemotePhoneBookMissingError("Remote phone book missing")

    monkeypatch.setattr(
        "dms.ui.squiglink_worker.read_remote_phone_book", _raise_missing
    )
    write_calls = []
    monkeypatch.setattr(
        "dms.ui.squiglink_worker.write_remote_phone_book",
        lambda *_a, **_k: write_calls.append(1),
    )

    # skip_upload=True so this exercises the phone-book phase in isolation.
    worker = SquiglinkUploadWorker(_config(skip_upload=True))
    events = _collect_signals(worker)

    worker.run()

    assert _kinds(events) == ["phone_book_decision_needed", "finished"]
    assert events[0][1] == "missing"
    assert write_calls == []
    # The connection must be closed BEFORE the decision signal reaches the
    # UI thread, which may leave a modal dialog open indefinitely.
    assert transport.closed is True


def test_invalid_phone_book_emits_decision_signal_with_invalid_kind(monkeypatch) -> None:
    transport = _FakeTransport()
    sftp = _FakeSFTP()
    monkeypatch.setattr(
        "dms.ui.squiglink_worker.open_sftp_session", lambda **_kw: (transport, sftp)
    )

    def _raise_invalid(_sftp, _path):
        raise RemotePhoneBookInvalidError("not a list")

    monkeypatch.setattr(
        "dms.ui.squiglink_worker.read_remote_phone_book", _raise_invalid
    )

    worker = SquiglinkUploadWorker(_config(skip_upload=True))
    events = _collect_signals(worker)

    worker.run()

    assert _kinds(events) == ["phone_book_decision_needed", "finished"]
    assert events[0][1] == "invalid"
    assert "structurally invalid" in events[0][2].lower()


def test_read_error_emits_failed_without_decision(monkeypatch) -> None:
    transport = _FakeTransport()
    sftp = _FakeSFTP()
    monkeypatch.setattr(
        "dms.ui.squiglink_worker.open_sftp_session", lambda **_kw: (transport, sftp)
    )

    def _raise_read_error(_sftp, _path):
        raise RemotePhoneBookReadError("dropped connection")

    monkeypatch.setattr(
        "dms.ui.squiglink_worker.read_remote_phone_book", _raise_read_error
    )

    worker = SquiglinkUploadWorker(_config(skip_upload=True))
    events = _collect_signals(worker)

    worker.run()

    # A read error must NEVER trigger the "create fresh" decision prompt -
    # it just fails the phone-book step outright.
    assert _kinds(events) == ["failed", "finished"]
    assert "could not be read" in events[0][1].lower()


def test_create_mode_writes_fresh_book(monkeypatch) -> None:
    transport = _FakeTransport()
    sftp = _FakeSFTP()
    monkeypatch.setattr(
        "dms.ui.squiglink_worker.open_sftp_session", lambda **_kw: (transport, sftp)
    )
    monkeypatch.setattr(
        "dms.ui.squiglink_worker.read_remote_phone_book",
        lambda _sftp, _path: (_ for _ in ()).throw(
            RemotePhoneBookMissingError("missing")
        ),
    )
    written = {}
    monkeypatch.setattr(
        "dms.ui.squiglink_worker.write_remote_phone_book",
        lambda _sftp, phone_book, _path: written.setdefault("book", phone_book),
    )

    worker = SquiglinkUploadWorker(_config(fallback_mode="create", skip_upload=True))
    events = _collect_signals(worker)

    worker.run()

    assert _kinds(events) == ["succeeded", "finished"]
    assert written["book"] == [
        {
            "name": "Apple",
            "phones": [
                {
                    "name": "AirPods Pro 2",
                    "file": ["Apple AirPods Pro 2"],
                    "reviewScore": "",
                    "reviewLink": "",
                    "price": "",
                    "shopLink": "",
                }
            ],
        }
    ]


def test_skip_mode_skips_phone_book_step_entirely(monkeypatch) -> None:
    # "skip" fallback_mode only comes into play once a missing/invalid
    # decision has actually been hit (the user chose "Upload Measurement
    # Only" in response to phone_book_decision_needed) - it does not bypass
    # reading the phone book altogether when the book is readable.
    transport = _FakeTransport()
    sftp = _FakeSFTP()
    monkeypatch.setattr(
        "dms.ui.squiglink_worker.open_sftp_session", lambda **_kw: (transport, sftp)
    )
    monkeypatch.setattr(
        "dms.ui.squiglink_worker.read_remote_phone_book",
        lambda _sftp, _path: (_ for _ in ()).throw(
            RemotePhoneBookMissingError("missing")
        ),
    )
    write_calls = []
    monkeypatch.setattr(
        "dms.ui.squiglink_worker.write_remote_phone_book",
        lambda *_a, **_k: write_calls.append(1),
    )

    worker = SquiglinkUploadWorker(
        _config(fallback_mode="skip", skip_upload=True)
    )
    events = _collect_signals(worker)

    worker.run()

    assert write_calls == []
    assert _kinds(events) == ["succeeded", "finished"]
    assert "skipped" in events[0][1].lower()


# --- upload step (step b) / skip_upload ordering ----------------------------


def test_skip_upload_true_never_calls_sftp_put(monkeypatch) -> None:
    transport = _FakeTransport()
    sftp = _FakeSFTP()
    monkeypatch.setattr(
        "dms.ui.squiglink_worker.open_sftp_session", lambda **_kw: (transport, sftp)
    )
    monkeypatch.setattr("dms.ui.squiglink_worker.read_remote_phone_book", lambda *_a, **_k: [])
    monkeypatch.setattr("dms.ui.squiglink_worker.write_remote_phone_book", lambda *_a, **_k: None)

    worker = SquiglinkUploadWorker(_config(skip_upload=True))
    events = _collect_signals(worker)

    worker.run()

    assert sftp.put_calls == []
    assert _kinds(events) == ["succeeded", "finished"]


def test_skip_upload_false_uploads_before_phone_book_sync(monkeypatch) -> None:
    transport = _FakeTransport()
    sftp = _FakeSFTP()
    monkeypatch.setattr(
        "dms.ui.squiglink_worker.open_sftp_session", lambda **_kw: (transport, sftp)
    )
    read_calls = []

    def _read(_sftp, _path):
        # By the time the phone-book is read, the file upload must already
        # have happened - proves the ordering guarantee that
        # phone_book_decision_needed can only ever fire after a successful
        # upload.
        assert sftp.put_calls, "upload must happen before phone-book read"
        read_calls.append(1)
        return []

    monkeypatch.setattr("dms.ui.squiglink_worker.read_remote_phone_book", _read)
    monkeypatch.setattr("dms.ui.squiglink_worker.write_remote_phone_book", lambda *_a, **_k: None)

    worker = SquiglinkUploadWorker(_config(skip_upload=False))
    events = _collect_signals(worker)

    worker.run()

    assert sftp.put_calls == [("/tmp/fake.txt", "data/Apple AirPods Pro 2 L.txt")]
    assert read_calls == [1]
    assert _kinds(events) == ["succeeded", "finished"]


# --- cancellation ------------------------------------------------------------


def test_cancel_before_run_skips_connecting_and_emits_failed(monkeypatch) -> None:
    open_calls = []
    monkeypatch.setattr(
        "dms.ui.squiglink_worker.open_sftp_session",
        lambda **_kw: open_calls.append(1),
    )

    worker = SquiglinkUploadWorker(_config())
    events = _collect_signals(worker)
    worker.cancel()  # a plain, direct method call - not a queued signal

    worker.run()

    assert open_calls == []
    assert _kinds(events) == ["failed", "finished"]
    assert events[0][1] == "Upload canceled."


def test_cancel_between_upload_and_phone_book_closes_connection(monkeypatch) -> None:
    transport = _FakeTransport()
    sftp = _FakeSFTP()
    monkeypatch.setattr(
        "dms.ui.squiglink_worker.open_sftp_session", lambda **_kw: (transport, sftp)
    )
    read_calls = []
    monkeypatch.setattr(
        "dms.ui.squiglink_worker.read_remote_phone_book",
        lambda *_a, **_k: read_calls.append(1),
    )

    worker = SquiglinkUploadWorker(_config(skip_upload=False))
    events = _collect_signals(worker)

    # Simulate a cancel request arriving while the upload put() is "in
    # flight" - it can't abort that call, but the next boundary check (right
    # before the phone-book step) must catch it.
    original_put = sftp.put

    def _put_then_cancel(local_path, remote_path):
        original_put(local_path, remote_path)
        worker.cancel()

    sftp.put = _put_then_cancel

    worker.run()

    assert sftp.put_calls  # the upload itself was not aborted mid-flight
    assert read_calls == []  # but the phone-book step never started
    assert _kinds(events) == ["failed", "finished"]
    assert events[0][1] == "Upload canceled."
    assert transport.closed is True
