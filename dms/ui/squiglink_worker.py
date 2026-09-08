"""Off-GUI-thread Squiglink upload.

The upload used to run inside the button handler: a slow or black-holed SFTP
connection froze the whole window, and the OS drew the "application is not
responding" overlay. This worker moves the network work onto a ``QThread`` and
keeps the two decisions that need a human — trusting a new SSH host key, and
recovering from a corrupt remote phone book — on the GUI thread.

Both questions use the same pattern: the worker emits a signal and blocks on a
``threading.Event``; the window's slot runs on the GUI thread, shows its dialog
and calls the matching ``answer_*`` method, which stores the result and sets the
event. Cancel releases every pending event so the worker can never be left
waiting on a dialog the user already dismissed.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QObject, pyqtSignal

from dms.squiglink import (
    SquiglinkHostKeyMismatch,
    SquiglinkHostKeyUnknown,
    host_key_id,
    upload_export_sftp,
)


# How long the worker waits for the GUI thread to answer a prompt before
# giving up. Long enough for a user to read a fingerprint, short enough that a
# lost dialog cannot pin a thread for the life of the process.
PROMPT_TIMEOUT_S = 600.0


class SquiglinkUploadCanceled(Exception):
    """Raised inside the worker when the user cancels."""


class SquiglinkUploadWorker(QObject):
    """Runs the measurement upload and phone-book merge on a worker thread."""

    progress = pyqtSignal(str)
    # host, port, "sha256:<base64>", key type. Answer with answer_host_key().
    host_key_prompt = pyqtSignal(str, int, str, str)
    # Emitted once a previously unknown host key is accepted, so the window can
    # persist the pin: host_id ("host:port"), fingerprint.
    host_key_accepted = pyqtSignal(str, str)
    # Detail message from the phone-book failure. Answer with
    # answer_phone_book_fallback("create" | "skip" | "fail").
    phone_book_fallback_needed = pyqtSignal(str)
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(
        self,
        *,
        local_path: Path,
        host: str,
        port: int,
        username: str,
        password: str,
        remote_filename: str,
        phone_book_stem: str,
        host_keys: dict[str, str] | None = None,
        sync_phone_book: Callable[..., str],
        upload: Callable[..., None] = upload_export_sftp,
        diagnostic: Callable[[str, dict[str, Any]], None] | None = None,
        connect_timeout: float = 20.0,
        prompt_timeout: float = PROMPT_TIMEOUT_S,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._local_path = Path(local_path)
        self._host = str(host)
        self._port = int(port)
        self._username = username
        self._password = password
        self._remote_filename = remote_filename
        self._phone_book_stem = phone_book_stem
        self._host_keys = dict(host_keys or {})
        self._sync_phone_book = sync_phone_book
        self._upload = upload
        self._diagnostic = diagnostic
        self._connect_timeout = float(connect_timeout)
        self._prompt_timeout = float(prompt_timeout)

        self._abort = threading.Event()
        self._host_key_event = threading.Event()
        self._host_key_answer = False
        self._phone_book_event = threading.Event()
        self._phone_book_answer = "fail"
        self.accepted_host_key: tuple[str, str] | None = None

    # -- GUI-thread API -------------------------------------------------

    def cancel(self) -> None:
        """Ask the worker to stop. Safe to call from the GUI thread."""
        self._abort.set()
        # Release anything blocked on a prompt so the run() loop can unwind.
        self._host_key_answer = False
        self._host_key_event.set()
        self._phone_book_answer = "fail"
        self._phone_book_event.set()

    def is_canceled(self) -> bool:
        return self._abort.is_set()

    def answer_host_key(self, accepted: bool) -> None:
        """Deliver the user's trust decision to the waiting worker."""
        self._host_key_answer = bool(accepted)
        self._host_key_event.set()

    def answer_phone_book_fallback(self, mode: str) -> None:
        """Deliver "create", "skip" or "fail" to the waiting worker."""
        self._phone_book_answer = str(mode or "fail")
        self._phone_book_event.set()

    # -- worker thread --------------------------------------------------

    def _check_abort(self) -> None:
        if self._abort.is_set():
            raise SquiglinkUploadCanceled("Upload canceled.")

    def _confirm_host_key(
        self,
        host: str,
        port: int,
        fingerprint: str,
        key_type: str,
    ) -> bool:
        self._check_abort()
        self._host_key_event.clear()
        self._host_key_answer = False
        self.host_key_prompt.emit(str(host), int(port), str(fingerprint), str(key_type))
        if not self._host_key_event.wait(self._prompt_timeout):
            return False
        self._check_abort()
        if not self._host_key_answer:
            return False
        identifier = host_key_id(host, port)
        self.accepted_host_key = (identifier, fingerprint)
        self._host_keys[identifier] = fingerprint
        self.host_key_accepted.emit(identifier, fingerprint)
        return True

    def _ask_phone_book_fallback(self, detail_message: str) -> str:
        self._check_abort()
        self._phone_book_event.clear()
        self._phone_book_answer = "fail"
        self.phone_book_fallback_needed.emit(str(detail_message))
        if not self._phone_book_event.wait(self._prompt_timeout):
            return "fail"
        self._check_abort()
        return self._phone_book_answer

    def run(self) -> None:
        try:
            self._check_abort()
            self.progress.emit(f"Connecting to {self._host}…")
            self._upload(
                local_path=self._local_path,
                host=self._host,
                port=self._port,
                username=self._username,
                password=self._password,
                remote_filename=self._remote_filename,
                diagnostic=self._diagnostic,
                host_keys=self._host_keys,
                confirm_host_key=self._confirm_host_key,
                connect_timeout=self._connect_timeout,
            )
            self._check_abort()
            self.progress.emit("Updating the Squiglink phone book…")
            phone_book_status = self._sync_phone_book(
                host=self._host,
                port=self._port,
                username=self._username,
                password=self._password,
                phone_book_stem=self._phone_book_stem,
                ask_fallback=self._ask_phone_book_fallback,
                host_keys=self._host_keys,
                confirm_host_key=self._confirm_host_key,
                connect_timeout=self._connect_timeout,
            )
            self._check_abort()
            self.progress.emit("Upload complete.")
            self.finished.emit(
                {
                    "filename": self._remote_filename,
                    "phone_book_status": phone_book_status,
                    "host_key": self.accepted_host_key,
                }
            )
        except SquiglinkUploadCanceled:
            self.failed.emit("Upload canceled.")
        except SquiglinkHostKeyMismatch as exc:
            self.failed.emit(str(exc))
        except SquiglinkHostKeyUnknown as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - reported to the user verbatim
            self.failed.emit(str(exc))
