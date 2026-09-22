"""
Squiglink upload from the Measure tab.

``SquiglinkController`` asks for credentials, writes the displayed average to a
temporary export, and hands the SFTP work (upload plus phone-book merge) to a
:class:`~dms.ui.squiglink_worker.SquiglinkUploadWorker` on its own thread. It
owns that thread and joins it in :meth:`shutdown`.
"""

from __future__ import annotations

import contextlib
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, Qt, QThread
from PyQt6.QtWidgets import QDialog, QMessageBox, QProgressDialog

from dms.console import exception_diagnostics
from dms.export import export_curve
from dms.secure_store import decrypt_credentials, encrypt_credentials
from dms.squiglink import (
    DEFAULT_CONNECT_TIMEOUT,
    PHONE_BOOK_REMOTE_PATH,
    RemotePhoneBookInvalidError,
    RemotePhoneBookMissingError,
    build_phone_book_name_stem,
    build_upload_name_stem,
    merge_phone_book_entry,
    open_sftp_connection,
    read_remote_phone_book,
    write_remote_phone_book,
)
from dms.ui.measure_dialogs import SquiglinkAuthDialog, SquiglinkUploadMetadataDialog
from dms.ui.squiglink_worker import SquiglinkUploadWorker

if TYPE_CHECKING:
    from dms.ui.main_window import MainWindow


class SquiglinkController(QObject):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self._squiglink_upload_context: dict | None = None

    def shutdown(self) -> None:
        """Cancel an upload in flight and join its thread."""
        context = self._squiglink_upload_context or {}
        upload_worker = context.get("worker")
        upload_thread = context.get("thread")
        if upload_worker is not None:
            upload_worker.cancel()
        if upload_thread is not None and upload_thread.isRunning():
            upload_thread.quit()
            upload_thread.wait(2000)

    def _log_exception(self, source: str, message: str, exc: BaseException, **details) -> None:
        details.update(exception_diagnostics(exc))
        self._window._log_event("ERROR", source, message, **details)

    def _log_sftp_diagnostic(self, stage: str, details: dict) -> None:
        severity = "WARNING" if stage.endswith("failed") else "DEBUG"
        message = stage.replace("_", " ").capitalize()
        self._window._log_event(severity, "squiglink", message, stage=stage, **details)

    def endpoint(self) -> tuple[str, int]:
        host = str(self._window._settings.get("squiglink_host") or "").strip()
        port = int(self._window._settings.get("squiglink_port") or 22)
        return host, port

    def upload(self) -> None:
        from dms.ui.measure_controller import _DISPLAY_AVG_SMOOTHING

        # Upload what is displayed, exactly as Export Average writes it.
        curve = self._window.measure.bottom_curve_for_display()
        if curve is None:
            QMessageBox.information(
                self._window,
                "Nothing to Upload",
                "No averaged curve available yet.",
            )
            return

        host, port = self.endpoint()
        if not host:
            QMessageBox.warning(
                self._window,
                "Squiglink Not Configured",
                "Squiglink SFTP host is not configured yet. Add it later in settings.json.",
            )
            return

        self._window._log_event(
            "INFO", "upload", "Squiglink upload requested", host=host, port=port
        )

        saved = decrypt_credentials(self._window._settings.get("squiglink_credentials_encrypted"))
        remember_saved = bool(self._window._settings.get("squiglink_remember_credentials"))
        auth = SquiglinkAuthDialog(
            self._window,
            initial_username=saved[0] if saved else "",
            initial_password=saved[1] if saved else "",
            remember=remember_saved,
        )
        if auth.exec() != QDialog.DialogCode.Accepted:
            return

        username = auth.username()
        password = auth.password()
        remember = auth.remember_credentials()
        self._window._settings.set("squiglink_remember_credentials", remember)
        if not remember:
            self._window._settings.set("squiglink_credentials_encrypted", None)
        # Credentials that turn out to be wrong (or that went to a server whose
        # host key was rejected) are never written to disk: the save happens in
        # _on_squiglink_upload_finished, after a successful upload.

        compensated = self._window.measure.is_hrtf_active()
        channel_label = self._window.measure.active_measure_label()
        # Squiglink requires exactly one channel side per file and has no way to
        # represent a combined L/R result. A Combined ("BOTH") upload is sent as
        # the L side on purpose; the "BOTH L" name modifier below marks it.
        required_side = (
            ("R" if channel_label == "R" else "L")
            if self._window.measure.two_channel_enabled
            else None
        )
        if not self.ensure_upload_metadata(required_side=required_side):
            return
        upload_session = (
            replace(self._window._session, channel_side=required_side)
            if required_side is not None
            else self._window._session
        )
        modifier = auth.name_modifier()
        if self._window.measure.two_channel_enabled and channel_label == "BOTH" and not modifier:
            modifier = "BOTH L"
        upload_stem = build_upload_name_stem(upload_session, modifier)
        phone_book_stem = build_phone_book_name_stem(upload_session, modifier)
        filename = f"{upload_stem}.txt"
        freqs, mag_db = curve

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".txt",
                prefix="dms_sq_",
                delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
            final_tmp = tmp_path.with_name(filename)
            tmp_path.rename(final_tmp)
            tmp_path = final_tmp
            export_curve(
                freqs=freqs,
                mag_db=mag_db,
                session=upload_session,
                output_path=tmp_path,
                compensated=compensated,
                hrtf=self._window.measure.hrtf if compensated else None,
                n_sweeps=self._window.measure.active_measure_count(),
                smoothing_fraction=_DISPLAY_AVG_SMOOTHING,
                level_mode=self._window.measure.level_mode()
                if self._window.measure.spl_offset_db() is not None
                else "ref_1khz",
            )
        except Exception as exc:
            self._window._statusbar.showMessage(f"Upload to Squiglink failed: {exc}")
            self._log_exception("upload", "Squiglink upload failed", exc)
            QMessageBox.warning(
                self._window, "Upload Failed", f"Upload to Squiglink failed.\n\n{exc}"
            )
            if tmp_path is not None:
                with contextlib.suppress(Exception):
                    tmp_path.unlink(missing_ok=True)
            return

        self.start_upload(
            local_path=tmp_path,
            host=host,
            port=port,
            username=username,
            password=password,
            filename=filename,
            phone_book_stem=phone_book_stem,
            remember=remember,
        )

    def start_upload(
        self,
        *,
        local_path: Path,
        host: str,
        port: int,
        username: str,
        password: str,
        filename: str,
        phone_book_stem: str,
        remember: bool,
    ) -> None:
        """Hand the SFTP work to a worker thread behind a cancellable dialog."""
        host_keys = self._squiglink_host_keys()
        worker = SquiglinkUploadWorker(
            local_path=local_path,
            host=host,
            port=port,
            username=username,
            password=password,
            remote_filename=filename,
            phone_book_stem=phone_book_stem,
            host_keys=host_keys,
            sync_phone_book=self.sync_remote_phone_book,
            diagnostic=self._log_sftp_diagnostic,
        )
        thread = QThread(self)
        worker.moveToThread(thread)

        progress = QProgressDialog("Uploading to Squiglink…", "Cancel", 0, 0, self._window)
        progress.setWindowTitle("Squiglink Upload")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        # Direct, not queued: the worker thread is blocked inside run(), so a
        # queued cancel would not be delivered until the upload it is meant to
        # interrupt had already finished. cancel() only sets thread-safe flags.
        progress.canceled.connect(worker.cancel, Qt.ConnectionType.DirectConnection)

        self._squiglink_upload_context = {
            "worker": worker,
            "thread": thread,
            "progress": progress,
            "local_path": local_path,
            "username": username,
            "password": password,
            "remember": remember,
            "filename": filename,
        }

        worker.progress.connect(progress.setLabelText)
        worker.host_key_prompt.connect(self._on_squiglink_host_key_prompt)
        worker.host_key_accepted.connect(self._on_squiglink_host_key_accepted)
        worker.phone_book_fallback_needed.connect(self._on_squiglink_phone_book_fallback)
        worker.finished.connect(self._on_squiglink_upload_finished)
        worker.failed.connect(self._on_squiglink_upload_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.started.connect(worker.run)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        progress.show()
        thread.start()

    def _squiglink_host_keys(self) -> dict:
        stored = self._window._settings.get("squiglink_host_keys")
        return dict(stored) if isinstance(stored, dict) else {}

    def _on_squiglink_host_key_prompt(
        self,
        host: str,
        port: int,
        fingerprint: str,
        key_type: str,
    ) -> None:
        """Ask the user to trust an unknown SSH host key (GUI thread)."""
        context = self._squiglink_upload_context
        worker = context.get("worker") if context else None
        self._window._log_event(
            "WARNING",
            "squiglink",
            "Unknown SSH host key offered",
            host=host,
            port=int(port),
            key_type=key_type,
            fingerprint=fingerprint,
        )
        accepted = (
            QMessageBox.question(
                self._window,
                "Trust This Server?",
                f"{host}:{int(port)} has not been connected to before.\n\n"
                f"Key type: {key_type}\nFingerprint: {fingerprint}\n\n"
                "Trust this server and remember its key? Your Squiglink "
                "password is only sent after you accept.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )
        if worker is not None:
            worker.answer_host_key(accepted)

    def _on_squiglink_host_key_accepted(self, identifier: str, fingerprint: str) -> None:
        """Pin a newly trusted key so a later mismatch is caught."""
        host_keys = self._squiglink_host_keys()
        host_keys[identifier] = fingerprint
        self._window._settings.set("squiglink_host_keys", host_keys)
        self._window._log_event(
            "INFO",
            "squiglink",
            "SSH host key trusted and stored",
            endpoint=identifier,
            fingerprint=fingerprint,
        )

    def _on_squiglink_phone_book_fallback(self, detail_message: str) -> None:
        context = self._squiglink_upload_context
        worker = context.get("worker") if context else None
        mode = self._ask_phone_book_fallback_mode(detail_message)
        if worker is not None:
            worker.answer_phone_book_fallback(mode)

    def _finish_squiglink_upload(self) -> None:
        context = self._squiglink_upload_context or {}
        progress = context.get("progress")
        if progress is not None:
            progress.close()
            progress.deleteLater()
        local_path = context.get("local_path")
        if local_path is not None:
            with contextlib.suppress(Exception):
                Path(local_path).unlink(missing_ok=True)
        self._squiglink_upload_context = None

    def _on_squiglink_upload_finished(self, result: dict) -> None:
        context = self._squiglink_upload_context or {}
        filename = str(result.get("filename") or context.get("filename") or "")
        phone_book_status = str(result.get("phone_book_status") or "")
        if context.get("remember"):
            # Only a credential that actually worked is written to disk.
            self._window._settings.set(
                "squiglink_credentials_encrypted",
                encrypt_credentials(context.get("username", ""), context.get("password", "")),
            )
        self._finish_squiglink_upload()
        self._window._statusbar.showMessage("Upload to Squiglink completed successfully.")
        self._window._log_event("INFO", "upload", "Squiglink upload completed", filename=filename)
        QMessageBox.information(
            self._window,
            "Upload Complete",
            f"Upload to Squiglink completed successfully.\n\n{phone_book_status}",
        )

    def _on_squiglink_upload_failed(self, message: str) -> None:
        self._finish_squiglink_upload()
        if message.strip().lower().startswith("upload canceled"):
            self._window._statusbar.showMessage("Upload to Squiglink canceled.")
            self._window._log_event("INFO", "upload", "Squiglink upload canceled")
            return
        self._window._statusbar.showMessage(f"Upload to Squiglink failed: {message}")
        self._window._log_event("ERROR", "upload", "Squiglink upload failed", error=message)
        QMessageBox.warning(
            self._window, "Upload Failed", f"Upload to Squiglink failed.\n\n{message}"
        )

    def ensure_upload_metadata(self, *, required_side: str | None = None) -> bool:
        side = (getattr(self._window._session, "channel_side", "") or "").strip().upper()
        brand = (getattr(self._window._session, "brand", "") or "").strip()
        model = (getattr(self._window._session, "model", "") or "").strip()
        if brand and model and (required_side in {"L", "R"} or side in {"L", "R"}):
            return True

        dialog = SquiglinkUploadMetadataDialog(
            self._window,
            initial_brand=brand,
            initial_model=model,
            initial_channel_side=required_side or side,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        self._window._session.brand = dialog.brand()
        self._window._session.model = dialog.model()
        if required_side is None:
            self._window._session.channel_side = dialog.channel_side()
        return True

    def _ask_phone_book_fallback_mode(self, detail_message: str) -> str:
        dialog = QMessageBox(self._window)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Phone Book Unavailable")
        dialog.setText("Couldn't load remote phone_book.json.")
        dialog.setInformativeText(f"{detail_message}\n\nChoose how to proceed with this upload:")
        create_btn = dialog.addButton(
            "Create Fresh Phone Book",
            QMessageBox.ButtonRole.AcceptRole,
        )
        dialog.addButton(
            "Fail Upload",
            QMessageBox.ButtonRole.DestructiveRole,
        )
        skip_btn = dialog.addButton(
            "Upload Measurement Only",
            QMessageBox.ButtonRole.ActionRole,
        )
        dialog.setDefaultButton(create_btn)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is create_btn:
            return "create"
        if clicked is skip_btn:
            return "skip"
        return "fail"

    def sync_remote_phone_book(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        phone_book_stem: str,
        ask_fallback: Callable[[str], str] | None = None,
        host_keys: dict | None = None,
        confirm_host_key: Callable[[str, int, str, str], bool] | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    ) -> str:
        """Merge this upload into the remote phone book.

        ``ask_fallback`` defaults to the modal dialog, so this stays callable
        straight from the GUI thread; the upload worker passes its own
        thread-safe prompt instead.
        """
        ask = ask_fallback or self._ask_phone_book_fallback_mode
        transport, sftp = open_sftp_connection(
            host=host,
            port=port,
            username=username,
            password=password,
            diagnostic=self._log_sftp_diagnostic,
            host_keys=host_keys,
            confirm_host_key=confirm_host_key,
            connect_timeout=connect_timeout,
        )
        try:
            try:
                self._window._log_event(
                    "DEBUG",
                    "squiglink",
                    "Phone book read start",
                    remote_path=PHONE_BOOK_REMOTE_PATH,
                )
                try:
                    phone_book = read_remote_phone_book(sftp, PHONE_BOOK_REMOTE_PATH)
                except (RemotePhoneBookMissingError, RemotePhoneBookInvalidError) as exc:
                    mode = ask(str(exc))
                    if mode == "fail":
                        raise RuntimeError(
                            f"Upload canceled because phone book could not be loaded: {exc}"
                        ) from exc
                    if mode == "skip":
                        return "Measurement uploaded. Phone book update was skipped."
                    phone_book = []

                merge_phone_book_entry(phone_book, self._window._session, phone_book_stem)
                write_remote_phone_book(sftp, phone_book, PHONE_BOOK_REMOTE_PATH)
                self._window._log_event(
                    "DEBUG",
                    "squiglink",
                    "Phone book update complete",
                    remote_path=PHONE_BOOK_REMOTE_PATH,
                    entries=len(phone_book),
                )
                return "Phone book updated successfully."
            finally:
                sftp.close()
        finally:
            transport.close()
