"""Small dialogs used by the R&D photo attachment workflow."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtMultimedia import QCamera, QImageCapture, QMediaCaptureSession, QMediaDevices
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QTextEdit,
    QVBoxLayout,
)

from dms.ui.modern_button import ModernButton as QPushButton


class CameraCaptureDialog(QDialog):
    """Preview a selected webcam and return an in-memory still image."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Capture R&D Photo")
        self.resize(720, 580)
        self.image: QImage | None = None
        self._camera: QCamera | None = None
        self._capture_session = QMediaCaptureSession(self)
        self._image_capture = QImageCapture(self)
        self._capture_session.setImageCapture(self._image_capture)
        self._image_capture.imageCaptured.connect(self._on_image_captured)
        self._image_capture.errorOccurred.connect(self._on_capture_error)

        layout = QVBoxLayout(self)
        camera_row = QHBoxLayout()
        camera_row.addWidget(QLabel("Camera"))
        self._camera_combo = QComboBox()
        for device in QMediaDevices.videoInputs():
            self._camera_combo.addItem(device.description(), device)
        self._camera_combo.currentIndexChanged.connect(self._start_camera)
        camera_row.addWidget(self._camera_combo, 1)
        layout.addLayout(camera_row)

        self._video = QVideoWidget()
        self._video.setMinimumHeight(360)
        layout.addWidget(self._video, 1)
        self._preview = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumHeight(360)
        self._preview.hide()
        layout.addWidget(self._preview, 1)

        self._caption = QTextEdit()
        self._caption.setPlaceholderText("Photo caption (optional)")
        self._caption.setMaximumHeight(70)
        layout.addWidget(self._caption)
        buttons = QHBoxLayout()
        self._capture_btn = QPushButton("Capture")
        self._capture_btn.clicked.connect(self._capture)
        buttons.addWidget(self._capture_btn)
        self._retake_btn = QPushButton("Retake")
        self._retake_btn.clicked.connect(self._retake)
        self._retake_btn.setVisible(False)
        buttons.addWidget(self._retake_btn)
        buttons.addStretch(1)
        self._dialog_buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self._dialog_buttons.accepted.connect(self._accept_image)
        self._dialog_buttons.rejected.connect(self.reject)
        self._dialog_buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        buttons.addWidget(self._dialog_buttons)
        layout.addLayout(buttons)

        if self._camera_combo.count():
            self._start_camera(0)
        else:
            self._capture_btn.setEnabled(False)
            self._camera_combo.setEnabled(False)
            layout.insertWidget(1, QLabel("No webcam is available."))

    @property
    def caption(self) -> str:
        return self._caption.toPlainText().strip()

    def _start_camera(self, index: int) -> None:
        if index < 0:
            return
        if self._camera is not None:
            self._camera.stop()
        self._camera = QCamera(self._camera_combo.itemData(index))
        self._capture_session.setCamera(self._camera)
        self._capture_session.setVideoOutput(self._video)
        self._camera.start()

    def _capture(self) -> None:
        if self._image_capture.isReadyForCapture():
            self._image_capture.capture()
        else:
            QMessageBox.information(
                self, "Camera Not Ready", "The camera is not ready to capture a photo yet."
            )

    def _on_image_captured(self, _request_id: int, image: QImage) -> None:
        self.image = image
        pixmap = QPixmap.fromImage(image).scaled(
            self._preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview.setPixmap(pixmap)
        self._video.hide()
        self._preview.show()
        self._capture_btn.setVisible(False)
        self._retake_btn.setVisible(True)
        self._dialog_buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)

    def _on_capture_error(self, _request_id: int, _error, message: str) -> None:
        QMessageBox.warning(
            self, "Capture Failed", message or "The webcam could not capture a photo."
        )

    def _retake(self) -> None:
        self.image = None
        self._preview.hide()
        self._video.show()
        self._capture_btn.setVisible(True)
        self._retake_btn.setVisible(False)
        self._dialog_buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def _accept_image(self) -> None:
        if self.image is not None and not self.image.isNull():
            self.accept()

    def done(self, result: int) -> None:
        if self._camera is not None:
            self._camera.stop()
        super().done(result)


class PhotoViewerDialog(QDialog):
    """Browse an item's photos and edit captions or request removal."""

    def __init__(
        self, entries: list[tuple[QImage | None, str, str]], index: int, parent=None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("R&D Photo")
        self.resize(760, 650)
        self.remove_requested = False
        self.remove_index: int | None = None
        self._entries = entries
        self._captions = [caption for _image, caption, _title in entries]
        self._index = max(0, min(index, len(entries) - 1))
        layout = QVBoxLayout(self)
        self._image_label = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumHeight(460)
        layout.addWidget(self._image_label, 1)
        self.caption_edit = QTextEdit()
        self.caption_edit.setPlaceholderText("Photo caption (optional)")
        self.caption_edit.setMaximumHeight(80)
        layout.addWidget(self.caption_edit)
        row = QHBoxLayout()
        self._previous = QPushButton("Previous")
        self._previous.clicked.connect(lambda: self._move(-1))
        row.addWidget(self._previous)
        self._next = QPushButton("Next")
        self._next.clicked.connect(lambda: self._move(1))
        row.addWidget(self._next)
        remove = QPushButton("Remove Photo")
        remove.clicked.connect(self._request_remove)
        row.addWidget(remove)
        row.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        layout.addLayout(row)
        self._render()

    @property
    def captions(self) -> list[str]:
        self._save_caption()
        return list(self._captions)

    def _save_caption(self) -> None:
        if self._entries:
            self._captions[self._index] = self.caption_edit.toPlainText().strip()

    def _move(self, delta: int) -> None:
        self._save_caption()
        self._index = max(0, min(self._index + delta, len(self._entries) - 1))
        self._render()

    def _render(self) -> None:
        image, _caption, title = self._entries[self._index]
        self.setWindowTitle(f"R&D Photo — {title} ({self._index + 1}/{len(self._entries)})")
        self.caption_edit.setPlainText(self._captions[self._index])
        self._previous.setEnabled(self._index > 0)
        self._next.setEnabled(self._index + 1 < len(self._entries))
        if image is None or image.isNull():
            self._image_label.setText("Photo file is unavailable")
            self._image_label.setPixmap(QPixmap())
        else:
            self._image_label.setText("")
            self._image_label.setPixmap(
                QPixmap.fromImage(image).scaled(
                    700,
                    460,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def _request_remove(self) -> None:
        if (
            QMessageBox.question(self, "Remove Photo", "Remove this photo attachment?")
            == QMessageBox.StandardButton.Yes
        ):
            self.remove_requested = True
            self.remove_index = self._index
            self.accept()
