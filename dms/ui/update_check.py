"""
Background check for a newer app release.

``UpdateCheck`` owns the quiet status-bar "Update" button and the worker thread
that fetches the release feed. It never interrupts the user: a failed or
up-to-date check just keeps the button hidden.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, Qt, QThread, QUrl
from PyQt6.QtGui import QDesktopServices

from dms.ui.modern_button import ModernButton as QPushButton
from dms.update_checker import UpdateCheckWorker, is_allowed_feed_url, is_allowed_release_url
from dms.version import __version__

if TYPE_CHECKING:
    from dms.ui.main_window import MainWindow


class UpdateCheck(QObject):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self._update_button = QPushButton("Update")
        self._update_button.setObjectName("btn_update")
        self._update_button.setVisible(False)
        self._update_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_button.setToolTip("A new app version is available.")
        self._update_button.clicked.connect(self._open_update_url)
        self._window._statusbar.addPermanentWidget(self._update_button)
        self._pending_update_url: str | None = None
        self._update_check_thread: QThread | None = None

    def start(self) -> None:
        enabled = bool(self._window._settings.get("update_check_enabled"))
        feed_url = str(self._window._settings.get("update_feed_url") or "").strip()
        if not enabled or not feed_url:
            return
        if not is_allowed_feed_url(feed_url):
            self._window._log_event(
                "WARNING",
                "update",
                "Update check skipped: feed URL must use https://",
                url=feed_url,
            )
            return

        worker = UpdateCheckWorker(current_version=__version__, feed_url=feed_url)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.update_available.connect(self._on_update_available)
        worker.up_to_date.connect(self._on_update_up_to_date)
        worker.check_failed.connect(self._on_update_check_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._update_check_thread = thread
        thread.start()

    def shutdown(self) -> None:
        if self._update_check_thread is not None and self._update_check_thread.isRunning():
            self._update_check_thread.quit()
            self._update_check_thread.wait(500)

    def _on_update_available(
        self,
        latest_version: str,
        release_url: str,
        summary: str,
    ) -> None:
        self._pending_update_url = release_url
        self._update_button.setVisible(True)
        summary_text = f" - {summary}" if summary else ""
        self._update_button.setToolTip(f"v{latest_version} is available{summary_text}")
        self._window._statusbar.showMessage(
            f"Update available: v{latest_version}. Click 'Update' to open release notes."
        )
        self._window._log_event("INFO", "update", "Update available", version=latest_version)

    def _on_update_up_to_date(self, _latest_version: str) -> None:
        self._pending_update_url = None
        self._update_button.setVisible(False)
        self._window._log_event(
            "DEBUG", "update", "Application is up to date", version=_latest_version
        )

    def _on_update_check_failed(self, _error: str) -> None:
        # Keep this fully non-intrusive by silently failing.
        self._pending_update_url = None
        self._update_button.setVisible(False)
        self._window._log_event("WARNING", "update", "Update check failed", error=_error)

    def _open_update_url(self) -> None:
        if not self._pending_update_url:
            return
        # Second gate: the feed was validated at parse time, but the URL is
        # about to be handed to the OS browser, so re-check it here too.
        if not is_allowed_release_url(self._pending_update_url):
            self._window._log_event(
                "WARNING",
                "update",
                "Blocked update link outside the project release org",
                url=self._pending_update_url,
            )
            self._pending_update_url = None
            self._update_button.setVisible(False)
            return
        QDesktopServices.openUrl(QUrl(self._pending_update_url))
