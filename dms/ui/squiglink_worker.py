"""Background worker for Squiglink SFTP uploads.

Mirrors the UpdateCheckWorker pattern (dms/update_checker.py): a plain
QObject meant to be moved to a QThread, driven by `run()`, communicating
back to the UI thread exclusively through signals. It never touches Qt
widgets or `SettingsManager` directly - all persistence (including saving
credentials, which must only happen after a *successful* upload - see M9)
is the UI thread's responsibility, done in the slots connected to this
worker's signals.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from dms.session import SessionData
from dms.squiglink import (
    DATA_UPLOAD_DIR,
    PHONE_BOOK_REMOTE_PATH,
    HostKeyMismatchError,
    HostKeyUnverifiedError,
    RemotePhoneBookInvalidError,
    RemotePhoneBookMissingError,
    RemotePhoneBookReadError,
    merge_phone_book_entry,
    open_sftp_session,
    read_remote_phone_book,
    write_remote_phone_book,
)


@dataclass
class SquiglinkUploadConfig:
    """Everything a single upload attempt needs. Mutable so a decision slot
    in the UI can tweak `known_key`/`fallback_mode`/`skip_upload` in place
    and hand the same config to a relaunched worker."""

    host: str
    port: int
    username: str
    password: str
    known_key: Optional[str]
    local_path: str
    remote_filename: str
    session: SessionData
    phone_book_stem: str
    fallback_mode: Optional[str] = None  # None = ask if needed; "create"; "skip"
    skip_upload: bool = False
    timeout: float = 10.0


class SquiglinkUploadWorker(QObject):
    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str)
    host_key_needed = pyqtSignal(str, str)  # key_str, fingerprint
    phone_book_decision_needed = pyqtSignal(str, str)  # kind, detail
    finished = pyqtSignal()

    def __init__(self, config: SquiglinkUploadConfig) -> None:
        super().__init__()
        self._config = config
        # A threading.Event (mirroring dms.audio_engine.SweepWorker.abort()),
        # not a Qt signal: the UI thread must be able to call `cancel()` as a
        # plain, immediate method call and have it take effect right away.
        # A Qt signal/slot connection to a method on this worker would
        # auto-resolve to a *queued* cross-thread connection (this worker
        # lives on the upload QThread) and would not be delivered until
        # that thread's event loop starts - which only happens after run()
        # already returns, making cancellation useless. Callers must invoke
        # `cancel()` directly, not via `.emit()`.
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Checked between phases (connect / upload / phone-book sync). It
        cannot abort a blocking network call already in flight - the call
        will still return (or hit its own timeout) before the next check
        point."""
        self._cancel_event.set()

    @property
    def _canceled(self) -> bool:
        return self._cancel_event.is_set()

    def run(self) -> None:
        cfg = self._config
        transport = None
        try:
            if self._canceled:
                self.failed.emit("Upload canceled.")
                return

            try:
                transport, sftp = open_sftp_session(
                    host=cfg.host,
                    port=cfg.port,
                    username=cfg.username,
                    password=cfg.password,
                    known_key=cfg.known_key,
                    timeout=cfg.timeout,
                )
            except HostKeyUnverifiedError as exc:
                self.host_key_needed.emit(exc.key_str, exc.fingerprint)
                return
            except HostKeyMismatchError as exc:
                self.failed.emit(
                    "SECURITY WARNING: the SFTP server's host key does not "
                    "match the key previously trusted for this host. This "
                    "could mean someone is intercepting the connection (a "
                    "man-in-the-middle attack), or it could mean the server "
                    "was legitimately rebuilt or migrated.\n\n"
                    f"Expected fingerprint: {exc.expected_fingerprint}\n"
                    f"Received fingerprint: {exc.actual_fingerprint}\n\n"
                    "If you did NOT expect this server to change, do not "
                    "proceed - investigate before retrying.\n\n"
                    "If this change is expected, remove the stored entry for "
                    "this host from squiglink_host_keys in settings.json, "
                    "then retry the upload to trust the new key."
                )
                return
            except Exception as exc:
                self.failed.emit(f"Could not connect to Squiglink: {exc}")
                return

            if self._canceled:
                self._close_quietly(transport)
                transport = None
                self.failed.emit("Upload canceled.")
                return

            if not cfg.skip_upload:
                try:
                    filename = (
                        (cfg.remote_filename or Path(cfg.local_path).name)
                        .strip()
                        .split("/")[-1]
                    )
                    sftp.put(cfg.local_path, f"{DATA_UPLOAD_DIR}/{filename}")
                except Exception as exc:
                    self.failed.emit(f"Upload to Squiglink failed: {exc}")
                    return

            if self._canceled:
                self._close_quietly(transport)
                transport = None
                self.failed.emit("Upload canceled.")
                return

            try:
                self._sync_phone_book(sftp, cfg, transport)
            except Exception as exc:
                # A raise escaping run() would leave the Qt slot uncaught
                # (the same crash class as the update-feed H3 bug).
                self.failed.emit(f"Phone book update failed: {exc}")
        finally:
            self._close_quietly(transport)
            self.finished.emit()

    def _sync_phone_book(self, sftp, cfg: SquiglinkUploadConfig, transport) -> None:
        """Reads, (maybe) falls back on, merges, and writes the shared remote
        phone book. Emits `succeeded` or `failed`, or - when a missing/invalid
        book is hit for the first time (fallback_mode is None) - closes the
        connection and emits `phone_book_decision_needed` so the UI can ask
        the user and relaunch us with `fallback_mode` set and
        `skip_upload=True` (the measurement file upload above already
        succeeded; it must not be repeated)."""
        try:
            phone_book = read_remote_phone_book(sftp, PHONE_BOOK_REMOTE_PATH)
        except RemotePhoneBookReadError as exc:
            # A transient/read failure is not an invitation to overwrite the
            # shared phone book with a fresh one - never offer "create
            # fresh" here, just fail the phone-book step.
            self.failed.emit(
                "Phone book update aborted because the remote phone book "
                f"could not be read: {exc}"
            )
            return
        except (RemotePhoneBookMissingError, RemotePhoneBookInvalidError) as exc:
            is_invalid = isinstance(exc, RemotePhoneBookInvalidError)
            kind = "invalid" if is_invalid else "missing"
            if cfg.fallback_mode is None:
                # Close the connection *before* handing control back to the
                # UI thread, which may leave a modal dialog open for an
                # indeterminate amount of time while the user decides.
                self._close_quietly(transport)
                detail = (
                    f"The remote phone book exists but is structurally invalid: {exc}"
                    if is_invalid
                    else str(exc)
                )
                self.phone_book_decision_needed.emit(kind, detail)
                return
            if cfg.fallback_mode == "skip":
                self.succeeded.emit(
                    "Measurement uploaded. Phone book update was skipped."
                )
                return
            if cfg.fallback_mode == "fail":
                # Not normally reached - main_window handles "fail" directly
                # without relaunching the worker - but handled defensively.
                self.failed.emit(
                    "Upload canceled because phone book could not be loaded: "
                    f"{exc}"
                )
                return
            phone_book = []  # fallback_mode == "create"

        merge_phone_book_entry(phone_book, cfg.session, cfg.phone_book_stem)
        write_remote_phone_book(sftp, phone_book, PHONE_BOOK_REMOTE_PATH)
        self.succeeded.emit("Phone book updated successfully.")

    @staticmethod
    def _close_quietly(closeable) -> None:
        if closeable is None:
            return
        try:
            closeable.close()
        except Exception:
            pass
