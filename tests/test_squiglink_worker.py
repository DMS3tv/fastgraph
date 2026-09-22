"""The Squiglink upload must not block the GUI thread, and must ask before
trusting a new SSH host key."""

import time

from PyQt6.QtCore import QThread, QTimer

from dms.squiglink import SquiglinkHostKeyMismatch, SquiglinkHostKeyUnknown
from dms.ui.squiglink_worker import SquiglinkUploadWorker


def _pump(qapp, predicate, timeout_s: float = 5.0) -> bool:
    """Spin the GUI event loop until ``predicate`` holds or time runs out."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        QThread.msleep(5)
    qapp.processEvents()
    return predicate()


class _Harness:
    """A worker on its own thread, with a QTimer proving the GUI still runs."""

    def __init__(self, qapp, worker: SquiglinkUploadWorker) -> None:
        self.qapp = qapp
        self.worker = worker
        self.thread = QThread()
        self.ticks = 0
        self.result: dict | None = None
        self.error: str | None = None
        self.progress: list[str] = []

        worker.moveToThread(self.thread)
        self.thread.started.connect(worker.run)
        worker.progress.connect(self.progress.append)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)

        self.timer = QTimer()
        self.timer.setInterval(10)
        self.timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        self.ticks += 1

    def _on_finished(self, result: dict) -> None:
        self.result = result
        self.thread.quit()

    def _on_failed(self, message: str) -> None:
        self.error = message
        self.thread.quit()

    def run(self, timeout_s: float = 5.0) -> None:
        self.timer.start()
        self.thread.start()
        _pump(self.qapp, lambda: self.result is not None or self.error is not None, timeout_s)
        self.timer.stop()
        self.thread.quit()
        self.thread.wait(2000)


def _make_worker(tmp_path, *, upload, sync=None, host_keys=None, **kwargs):
    local = tmp_path / "Apple AirPods Pro 2 L.txt"
    local.write_text("data", encoding="utf-8")
    return SquiglinkUploadWorker(
        local_path=local,
        host="sftp.squig.link",
        port=2022,
        username="user",
        password="hunter2",
        remote_filename="Apple AirPods Pro 2 L.txt",
        phone_book_stem="Apple AirPods Pro 2",
        host_keys=host_keys or {},
        upload=upload,
        sync_phone_book=sync or (lambda **_kw: "Phone book updated successfully."),
        **kwargs,
    )


def test_slow_upload_leaves_the_gui_thread_responsive(qapp, tmp_path) -> None:
    """A 0.2 s network stall must not stop the GUI timer from firing."""

    def _slow_upload(**kwargs):
        assert kwargs["confirm_host_key"] is not None
        time.sleep(0.2)

    worker = _make_worker(
        tmp_path,
        upload=_slow_upload,
        host_keys={"sftp.squig.link:2022": "sha256:known"},
    )
    harness = _Harness(qapp, worker)
    harness.run()

    assert harness.error is None
    assert harness.result is not None
    assert harness.result["phone_book_status"] == "Phone book updated successfully."
    # 200 ms of blocking network work against a 10 ms timer.
    assert harness.ticks >= 5, f"GUI thread was blocked (only {harness.ticks} ticks)"
    assert harness.progress[0].startswith("Connecting to sftp.squig.link")


def test_unknown_host_key_is_answered_from_the_gui_thread(qapp, tmp_path) -> None:
    prompted: list[tuple] = []

    def _upload(**kwargs):
        # The transport layer would ask here; the fake asks directly.
        assert kwargs["confirm_host_key"]("sftp.squig.link", 2022, "sha256:new", "ssh-ed25519")

    worker = _make_worker(tmp_path, upload=_upload)
    pinned: list[tuple] = []
    worker.host_key_accepted.connect(lambda *args: pinned.append(args))

    def _answer(host, port, fingerprint, key_type):
        prompted.append((host, port, fingerprint, key_type))
        worker.answer_host_key(True)

    worker.host_key_prompt.connect(_answer)

    harness = _Harness(qapp, worker)
    harness.run()

    assert prompted == [("sftp.squig.link", 2022, "sha256:new", "ssh-ed25519")]
    assert pinned == [("sftp.squig.link:2022", "sha256:new")]
    assert harness.result is not None
    assert harness.result["host_key"] == ("sftp.squig.link:2022", "sha256:new")


def test_declined_host_key_stops_the_upload(qapp, tmp_path) -> None:
    def _upload(**kwargs):
        if not kwargs["confirm_host_key"]("sftp.squig.link", 2022, "sha256:new", "ssh-ed25519"):
            raise SquiglinkHostKeyUnknown("sftp.squig.link", 2022, "sha256:new", "ssh-ed25519")
        raise AssertionError("upload continued after the key was declined")

    worker = _make_worker(tmp_path, upload=_upload)
    pinned: list = []
    worker.host_key_accepted.connect(lambda *args: pinned.append(args))
    worker.host_key_prompt.connect(lambda *_a: worker.answer_host_key(False))

    harness = _Harness(qapp, worker)
    harness.run()

    assert harness.result is None
    assert pinned == []
    assert "not trusted yet" in harness.error


def test_host_key_mismatch_is_surfaced_verbatim(qapp, tmp_path) -> None:
    def _upload(**_kwargs):
        raise SquiglinkHostKeyMismatch("sftp.squig.link", 2022, "sha256:pinned", "sha256:other")

    worker = _make_worker(
        tmp_path,
        upload=_upload,
        host_keys={"sftp.squig.link:2022": "sha256:pinned"},
    )
    harness = _Harness(qapp, worker)
    harness.run()

    assert harness.result is None
    assert "does not match" in harness.error
    assert "Nothing was sent" in harness.error


def test_phone_book_fallback_is_answered_from_the_gui_thread(qapp, tmp_path) -> None:
    asked: list[str] = []

    def _sync(**kwargs):
        mode = kwargs["ask_fallback"]("phone_book.json is invalid JSON.")
        return f"mode={mode}"

    worker = _make_worker(tmp_path, upload=lambda **_kw: None, sync=_sync)

    def _answer(detail: str) -> None:
        asked.append(detail)
        worker.answer_phone_book_fallback("skip")

    worker.phone_book_fallback_needed.connect(_answer)

    harness = _Harness(qapp, worker)
    harness.run()

    assert asked == ["phone_book.json is invalid JSON."]
    assert harness.result["phone_book_status"] == "mode=skip"


def test_cancel_unblocks_a_worker_waiting_on_a_prompt(qapp, tmp_path) -> None:
    """Cancel must release the prompt wait rather than stranding the thread."""

    def _upload(**kwargs):
        kwargs["confirm_host_key"]("sftp.squig.link", 2022, "sha256:new", "ssh-ed25519")
        raise AssertionError("upload should not proceed after cancel")

    worker = _make_worker(tmp_path, upload=_upload, prompt_timeout=30.0)
    # Nobody answers the prompt; the user hits Cancel on the progress dialog.
    worker.host_key_prompt.connect(lambda *_a: worker.cancel())

    harness = _Harness(qapp, worker)
    harness.run()

    assert harness.result is None
    assert harness.error == "Upload canceled."
    assert worker.is_canceled() is True


def test_cancel_before_start_skips_the_network_entirely(qapp, tmp_path) -> None:
    called: list[int] = []
    worker = _make_worker(tmp_path, upload=lambda **_kw: called.append(1))
    worker.cancel()

    harness = _Harness(qapp, worker)
    harness.run()

    assert called == []
    assert harness.error == "Upload canceled."


def test_upload_failure_is_reported_without_the_password(qapp, tmp_path) -> None:
    def _upload(**_kwargs):
        raise OSError("Authentication failed for user")

    worker = _make_worker(
        tmp_path,
        upload=_upload,
        host_keys={"sftp.squig.link:2022": "sha256:known"},
    )
    harness = _Harness(qapp, worker)
    harness.run()

    assert harness.result is None
    assert harness.error == "Authentication failed for user"
    assert "hunter2" not in harness.error
