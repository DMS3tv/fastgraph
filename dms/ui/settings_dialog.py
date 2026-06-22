from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dms.settings_manager import SettingsManager


class SettingsWidget(QWidget):
    """Persistent, immediately saved application settings."""

    settings_changed = pyqtSignal(str, object)
    calibration_requested = pyqtSignal()
    test_level_requested = pyqtSignal()

    def __init__(self, settings: SettingsManager, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._build_ui()
        self._connect_signals()
        self.refresh_from_settings()

    def _build_ui(self) -> None:
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._settings_column = QWidget()
        self._settings_column.setObjectName("settings_left_column")
        self._settings_column.setFixedWidth(560)
        layout = QVBoxLayout(self._settings_column)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self._sweep_group = QGroupBox("Sweep and Timing")
        sweep_form = QFormLayout(self._sweep_group)

        self._duration = QDoubleSpinBox()
        self._duration.setRange(0.5, 30.0)
        self._duration.setSingleStep(0.5)
        self._duration.setDecimals(1)
        self._duration.setSuffix(" s")

        self._fs = QComboBox()
        for rate in [44100, 48000, 88200, 96000, 192000]:
            self._fs.addItem(f"{rate} Hz", rate)

        self._buf = QComboBox()
        for size in [64, 128, 256, 512, 1024, 2048, 4096]:
            self._buf.addItem(str(size), size)

        self._pre_silence = QDoubleSpinBox()
        self._pre_silence.setRange(0.05, 2.0)
        self._pre_silence.setSingleStep(0.05)
        self._pre_silence.setDecimals(2)
        self._pre_silence.setSuffix(" s")

        self._post_silence = QDoubleSpinBox()
        self._post_silence.setRange(0.1, 3.0)
        self._post_silence.setSingleStep(0.1)
        self._post_silence.setDecimals(1)
        self._post_silence.setSuffix(" s")

        self._latency = QComboBox()
        self._latency.addItems(["low", "high"])

        self._start_conf_min = QDoubleSpinBox()
        self._start_conf_min.setRange(2.0, 30.0)
        self._start_conf_min.setSingleStep(0.5)
        self._start_conf_min.setDecimals(1)
        self._start_conf_min.setToolTip(
            "Minimum sweep-start alignment confidence. Higher values are stricter."
        )

        self._end_conf_min = QDoubleSpinBox()
        self._end_conf_min.setRange(2.0, 30.0)
        self._end_conf_min.setSingleStep(0.5)
        self._end_conf_min.setDecimals(1)
        self._end_conf_min.setToolTip(
            "Bluetooth mode only: minimum end-marker detection confidence."
        )

        self._timing_drift_max_ms = QDoubleSpinBox()
        self._timing_drift_max_ms.setRange(5.0, 250.0)
        self._timing_drift_max_ms.setSingleStep(1.0)
        self._timing_drift_max_ms.setDecimals(1)
        self._timing_drift_max_ms.setSuffix(" ms")
        self._timing_drift_max_ms.setToolTip(
            "Bluetooth mode only: maximum accepted timing drift. Lower is stricter."
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
        layout.addWidget(self._sweep_group)

        tuning_hint = QLabel(
            "Changes save immediately. Buffer size and latency affect reliability on some "
            "systems; end-marker confidence and timing drift apply to Bluetooth measurements."
        )
        tuning_hint.setWordWrap(True)
        tuning_hint.setProperty("tone", "muted")
        layout.addWidget(tuning_hint)

        self._audio_tools_group = QGroupBox("Audio Tools")
        audio_tools_layout = QHBoxLayout(self._audio_tools_group)
        self._calibration_btn = QPushButton("SPL Calibration…")
        self._test_level_btn = QPushButton("Test Level…")
        audio_tools_layout.addWidget(self._calibration_btn)
        audio_tools_layout.addWidget(self._test_level_btn)
        audio_tools_layout.addStretch(1)
        layout.addWidget(self._audio_tools_group)

        self._safety_group = QGroupBox("Safety")
        safety_layout = QVBoxLayout(self._safety_group)
        self._confirm_clear = QCheckBox("Confirm before clearing measurements")
        safety_layout.addWidget(self._confirm_clear)
        self._confirm_clear_metadata = QCheckBox(
            "Confirm before clearing headphone metadata"
        )
        safety_layout.addWidget(self._confirm_clear_metadata)
        layout.addWidget(self._safety_group)
        layout.addStretch(1)
        root_layout.addWidget(self._settings_column)
        root_layout.addStretch(1)

    def _connect_signals(self) -> None:
        self._duration.editingFinished.connect(
            lambda: self._save("sweep_duration", self._duration.value())
        )
        self._fs.currentIndexChanged.connect(
            lambda: self._save("sample_rate", self._fs.currentData())
        )
        self._buf.currentIndexChanged.connect(
            lambda: self._save("buffer_size", self._buf.currentData())
        )
        self._pre_silence.editingFinished.connect(
            lambda: self._save("pre_sweep_silence", self._pre_silence.value())
        )
        self._post_silence.editingFinished.connect(
            lambda: self._save("post_sweep_silence", self._post_silence.value())
        )
        self._latency.currentTextChanged.connect(self._save_latency)
        self._start_conf_min.editingFinished.connect(
            lambda: self._save(
                "start_alignment_confidence_min", self._start_conf_min.value()
            )
        )
        self._end_conf_min.editingFinished.connect(
            lambda: self._save(
                "end_marker_confidence_min", self._end_conf_min.value()
            )
        )
        self._timing_drift_max_ms.editingFinished.connect(
            lambda: self._save(
                "timing_drift_max_ms", self._timing_drift_max_ms.value()
            )
        )
        self._confirm_clear.toggled.connect(
            lambda checked: self._save("confirm_clear_measurements", checked)
        )
        self._confirm_clear_metadata.toggled.connect(
            lambda checked: self._save("confirm_clear_metadata", checked)
        )
        self._calibration_btn.clicked.connect(self.calibration_requested)
        self._test_level_btn.clicked.connect(self.test_level_requested)

    def _save(self, key: str, value: object) -> None:
        self._settings.set(key, value)
        self.settings_changed.emit(key, value)

    def _save_latency(self, value: str) -> None:
        if not value:
            return
        self._settings.update({"latency": value, "latency_user_override": True})
        self.settings_changed.emit("latency", value)

    def refresh_from_settings(self) -> None:
        controls = (
            self._duration,
            self._fs,
            self._buf,
            self._pre_silence,
            self._post_silence,
            self._latency,
            self._start_conf_min,
            self._end_conf_min,
            self._timing_drift_max_ms,
            self._confirm_clear,
            self._confirm_clear_metadata,
        )
        for control in controls:
            control.blockSignals(True)
        try:
            self._duration.setValue(float(self._settings.get("sweep_duration")))
            self._fs.setCurrentIndex(self._fs.findData(self._settings.get("sample_rate")))
            self._buf.setCurrentIndex(self._buf.findData(self._settings.get("buffer_size")))
            self._pre_silence.setValue(float(self._settings.get("pre_sweep_silence")))
            self._post_silence.setValue(float(self._settings.get("post_sweep_silence")))
            self._latency.setCurrentText(str(self._settings.get("latency")))
            self._start_conf_min.setValue(
                float(self._settings.get("start_alignment_confidence_min"))
            )
            self._end_conf_min.setValue(
                float(self._settings.get("end_marker_confidence_min"))
            )
            self._timing_drift_max_ms.setValue(
                float(self._settings.get("timing_drift_max_ms"))
            )
            self._confirm_clear.setChecked(
                bool(self._settings.get("confirm_clear_measurements"))
            )
            self._confirm_clear_metadata.setChecked(
                bool(self._settings.get("confirm_clear_metadata"))
            )
        finally:
            for control in controls:
                control.blockSignals(False)

    def set_editing_enabled(self, enabled: bool) -> None:
        self._sweep_group.setEnabled(enabled)
        self._audio_tools_group.setEnabled(enabled)
        self._safety_group.setEnabled(enabled)
