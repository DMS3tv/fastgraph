from PyQt6.QtWidgets import (
    QDialog, QFormLayout, QDoubleSpinBox, QSpinBox, QComboBox,
    QDialogButtonBox, QVBoxLayout, QGroupBox, QLabel,
)
from dms.settings_manager import SettingsManager


class SettingsDialog(QDialog):
    def __init__(self, settings: SettingsManager, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(360)
        self._settings = settings
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # --- Sweep ---
        sweep_group = QGroupBox("Sweep")
        sweep_form = QFormLayout(sweep_group)

        self._duration = QDoubleSpinBox()
        self._duration.setRange(0.5, 30.0)
        self._duration.setSingleStep(0.5)
        self._duration.setDecimals(1)
        self._duration.setSuffix(" s")
        self._duration.setValue(self._settings.get("sweep_duration"))

        self._fs = QComboBox()
        for rate in [44100, 48000, 88200, 96000, 192000]:
            self._fs.addItem(f"{rate} Hz", rate)
        current_fs = self._settings.get("sample_rate")
        idx = self._fs.findData(current_fs)
        if idx >= 0:
            self._fs.setCurrentIndex(idx)

        self._buf = QComboBox()
        for size in [64, 128, 256, 512, 1024, 2048, 4096]:
            self._buf.addItem(str(size), size)
        current_buf = self._settings.get("buffer_size")
        idx = self._buf.findData(current_buf)
        if idx >= 0:
            self._buf.setCurrentIndex(idx)

        self._pre_silence = QDoubleSpinBox()
        self._pre_silence.setRange(0.05, 2.0)
        self._pre_silence.setSingleStep(0.05)
        self._pre_silence.setDecimals(2)
        self._pre_silence.setSuffix(" s")
        self._pre_silence.setValue(self._settings.get("pre_sweep_silence"))

        self._post_silence = QDoubleSpinBox()
        self._post_silence.setRange(0.1, 3.0)
        self._post_silence.setSingleStep(0.1)
        self._post_silence.setDecimals(1)
        self._post_silence.setSuffix(" s")
        self._post_silence.setValue(self._settings.get("post_sweep_silence"))

        self._latency = QComboBox()
        self._latency.addItems(["low", "high"])
        lat = self._settings.get("latency")
        if lat in ["low", "high"]:
            self._latency.setCurrentText(lat)

        sweep_form.addRow("Sweep Duration", self._duration)
        sweep_form.addRow("Sample Rate", self._fs)
        sweep_form.addRow("Buffer Size", self._buf)
        sweep_form.addRow("Pre-sweep Silence", self._pre_silence)
        sweep_form.addRow("Post-sweep Silence", self._post_silence)
        sweep_form.addRow("Latency Mode", self._latency)
        layout.addWidget(sweep_group)

        layout.addWidget(QLabel(
            '<span style="color:#888; font-size:11px;">'
            "Buffer size / latency mode affect reliability on some OSes.<br>"
            "If recording has artifacts, increase buffer size or use 'high' latency."
            "</span>"
        ))

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._save_and_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _save_and_accept(self) -> None:
        self._settings.update({
            "sweep_duration": self._duration.value(),
            "sample_rate": self._fs.currentData(),
            "buffer_size": self._buf.currentData(),
            "pre_sweep_silence": self._pre_silence.value(),
            "post_sweep_silence": self._post_silence.value(),
            "latency": self._latency.currentText(),
        })
        self.accept()