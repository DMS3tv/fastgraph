"""
94 dB SPL calibration dialog.
User plays a 94 dB pistonphone tone into the microphone while this dialog
records RMS, then stores Pa/FS sensitivity.
"""

import numpy as np
import sounddevice as sd
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QProgressBar,
    QHBoxLayout,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from dms.calibration import CalibrationStore
from dms.audio_engine import device_by_name

_REF_SPL_DB = 94.0
_REF_PASCAL = 20e-6 * (10 ** (_REF_SPL_DB / 20.0))  # ≈ 1.0 Pa


class CalibrationDialog(QDialog):
    calibration_done = pyqtSignal(str, float)  # device_name, sensitivity

    def __init__(
        self,
        device_name: str,
        channel: int,
        fs: int,
        buffer_size: int,
        cal_store: CalibrationStore,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("SPL Calibration — 94 dB")
        self.setMinimumWidth(400)
        self._device = device_name
        self._channel = channel
        self._fs = fs
        self._buf = buffer_size
        self._cal_store = cal_store
        self._stream: sd.InputStream | None = None
        self._rms_values: list[float] = []
        self._capturing = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            "<b>94 dB SPL Calibration</b><br><br>"
            "1. Apply your 94 dB pistonphone / calibrator to the microphone.<br>"
            "2. Click <b>Start Capture</b>.<br>"
            "3. Wait ~3 seconds for measurement.<br>"
            "4. Click <b>Accept</b> to save, or <b>Cancel</b>."
        ))

        self._status = QLabel("Ready. Device: " + self._device)
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        layout.addWidget(self._bar)

        self._level_label = QLabel("—  dBFS")
        self._level_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._level_label)

        btns = QHBoxLayout()
        self._start_btn = QPushButton("Start Capture")
        self._start_btn.clicked.connect(self._start_capture)
        btns.addWidget(self._start_btn)

        self._accept_btn = QPushButton("Accept && Save")
        self._accept_btn.setEnabled(False)
        self._accept_btn.clicked.connect(self._accept_cal)
        btns.addWidget(self._accept_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._cancel)
        btns.addWidget(self._cancel_btn)

        layout.addLayout(btns)

    def _start_capture(self) -> None:
        self._rms_values = []
        self._capturing = True
        self._start_btn.setEnabled(False)
        self._accept_btn.setEnabled(False)
        self._status.setText("Capturing… (3 seconds)")
        self._bar.setValue(0)

        dev = device_by_name(self._device)
        if dev is None:
            self._status.setText("Error: device not found.")
            self._start_btn.setEnabled(True)
            return

        n_ch = dev["max_input_channels"]
        try:
            self._stream = sd.InputStream(
                device=self._device,
                channels=n_ch,
                samplerate=self._fs,
                blocksize=self._buf,
                dtype="float32",
                callback=self._audio_cb,
            )
            self._stream.start()
        except Exception as e:
            self._status.setText(f"Stream error: {e}")
            self._start_btn.setEnabled(True)
            self._capturing = False
            return

        # Stop after 3 seconds
        QTimer.singleShot(3000, self._stop_capture)

    def _audio_cb(self, indata, frames, time_info, status) -> None:
        if not self._capturing:
            return
        ch = min(self._channel, indata.shape[1] - 1)
        rms = float(np.sqrt(np.mean(indata[:, ch] ** 2)))
        self._rms_values.append(rms)

    def _stop_capture(self) -> None:
        self._capturing = False
        if self._stream:
            try:
                self._stream.stop(ignore_errors=True)
                self._stream.close(ignore_errors=True)
            except Exception:
                pass
            self._stream = None

        if not self._rms_values:
            self._status.setText("No audio captured. Check device/channel.")
            self._start_btn.setEnabled(True)
            return

        avg_rms = float(np.mean(self._rms_values))
        if avg_rms <= 0:
            self._status.setText("Signal too low — check microphone input.")
            self._start_btn.setEnabled(True)
            return

        db_fs = 20.0 * np.log10(avg_rms)
        # sensitivity: Pa per FS unit
        sensitivity = _REF_PASCAL / avg_rms
        self._sensitivity = sensitivity
        self._bar.setValue(100)
        self._level_label.setText(f"{db_fs:.2f} dBFS  →  {sensitivity:.6f} Pa/FS")
        self._status.setText(
            f"Captured. Avg RMS = {avg_rms:.5f} FS ({db_fs:.2f} dBFS)\n"
            f"Sensitivity = {sensitivity:.5f} Pa/FS  (1 FS = {1/sensitivity:.2f} Pa)"
        )
        self._accept_btn.setEnabled(True)

    def _accept_cal(self) -> None:
        self._cal_store.set_sensitivity(self._device, self._sensitivity)
        self.calibration_done.emit(self._device, self._sensitivity)
        self.accept()

    def _cancel(self) -> None:
        self._capturing = False
        if self._stream:
            try:
                self._stream.stop(ignore_errors=True)
                self._stream.close(ignore_errors=True)
            except Exception:
                pass
        self.reject()

    def closeEvent(self, event) -> None:
        self._cancel()
        super().closeEvent(event)
