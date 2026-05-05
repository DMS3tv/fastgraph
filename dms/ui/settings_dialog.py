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

        self._start_conf_min = QDoubleSpinBox()
        self._start_conf_min.setRange(2.0, 30.0)
        self._start_conf_min.setSingleStep(0.5)
        self._start_conf_min.setDecimals(1)
        self._start_conf_min.setValue(
            float(self._settings.get("start_alignment_confidence_min"))
        )
        self._start_conf_min.setToolTip(
            "Minimum allowed confidence for sweep start alignment.\n"
            "Higher is better quality and stricter (more retries).\n"
            "Rough guide: >=12 strong, 9-12 good, 7-9 borderline, <7 weak."
        )

        self._end_conf_min = QDoubleSpinBox()
        self._end_conf_min.setRange(2.0, 30.0)
        self._end_conf_min.setSingleStep(0.5)
        self._end_conf_min.setDecimals(1)
        self._end_conf_min.setValue(
            float(self._settings.get("end_marker_confidence_min"))
        )
        self._end_conf_min.setToolTip(
            "Minimum allowed confidence for detecting the end marker.\n"
            "Higher is better quality and stricter.\n"
            "Rough guide: >=12 strong, 9-12 good, 7-9 borderline, <7 weak."
        )

        self._timing_drift_max_ms = QDoubleSpinBox()
        self._timing_drift_max_ms.setRange(5.0, 250.0)
        self._timing_drift_max_ms.setSingleStep(1.0)
        self._timing_drift_max_ms.setDecimals(1)
        self._timing_drift_max_ms.setSuffix(" ms")
        self._timing_drift_max_ms.setValue(
            float(self._settings.get("timing_drift_max_ms"))
        )
        self._timing_drift_max_ms.setToolTip(
            "Maximum allowed timing drift (ms) between expected and detected end marker.\n"
            "Lower values are stricter and improve consistency; higher values accept more jitter."
        )

        sweep_form.addRow("Sweep Duration", self._duration)
        sweep_form.addRow("Sample Rate", self._fs)
        sweep_form.addRow("Buffer Size", self._buf)
        sweep_form.addRow("Pre-sweep Silence", self._pre_silence)
        sweep_form.addRow("Post-sweep Silence", self._post_silence)
        sweep_form.addRow("Latency Mode", self._latency)
        sweep_form.addRow("Start Align Confidence Min", self._start_conf_min)
        sweep_form.addRow("End Marker Confidence Min", self._end_conf_min)
        sweep_form.addRow("Max Timing Drift", self._timing_drift_max_ms)
        layout.addWidget(sweep_group)

        layout.addWidget(QLabel(
            '<span style="color:#888; font-size:11px;">'
            "Buffer size / latency mode affect reliability on some OSes.<br>"
            "If recording has artifacts, increase buffer size or use 'high' latency."
            "</span>"
        ))
        layout.addWidget(QLabel(
            '<span style="color:#888; font-size:11px;">'
            "<b>Timing Tuning:</b> These controls tune how strict measurement acceptance is.<br>"
            "Start/End confidence are correlation-quality checks (higher is better and stricter).<br>"
            "Confidence guide: <b>&gt;=12 strong</b>, <b>9-12 good</b>, "
            "<b>7-9 borderline</b>, <b>&lt;7 weak</b>.<br>"
            "Max Timing Drift controls allowed end-marker error (lower = stricter).<br>"
            "Drift guide: <b>&lt;5 ms excellent</b>, <b>5-15 ms good</b>, "
            "<b>15-35 ms acceptable</b>, <b>&gt;35 ms may reduce repeatability</b>."
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
            "start_alignment_confidence_min": self._start_conf_min.value(),
            "end_marker_confidence_min": self._end_conf_min.value(),
            "timing_drift_max_ms": self._timing_drift_max_ms.value(),
        })
        self.accept()
