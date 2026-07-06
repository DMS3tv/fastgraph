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
    QLineEdit,
    QFileDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QKeySequenceEdit,
)

from dms.settings_manager import SettingsManager
from dms.shortcuts import (
    DEFAULT_SHORTCUT_BINDINGS,
    SHORTCUT_ACTIONS,
    shortcut_bindings_from_settings,
)


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

        self._rnd_group = QGroupBox("R&D Sessions")
        rnd_layout = QVBoxLayout(self._rnd_group)
        rnd_row = QHBoxLayout()
        self._rnd_session_dir = QLineEdit()
        self._rnd_session_dir.setPlaceholderText("Default: Documents")
        rnd_row.addWidget(self._rnd_session_dir, 1)
        self._rnd_session_browse = QPushButton("Browse...")
        rnd_row.addWidget(self._rnd_session_browse)
        rnd_layout.addWidget(QLabel("Default save/load folder"))
        rnd_layout.addLayout(rnd_row)
        layout.addWidget(self._rnd_group)

        self._automation_group = QGroupBox("Automations")
        automation_layout = QVBoxLayout(self._automation_group)
        automation_row = QHBoxLayout()
        self._automation_dir = QLineEdit()
        self._automation_dir.setPlaceholderText("Default: Documents/Fastgraph Automations")
        automation_row.addWidget(self._automation_dir, 1)
        self._automation_browse = QPushButton("Browse...")
        automation_row.addWidget(self._automation_browse)
        automation_layout.addWidget(QLabel("Default automation folder"))
        automation_layout.addLayout(automation_row)
        layout.addWidget(self._automation_group)
        layout.addStretch(1)
        root_layout.addWidget(self._settings_column)
        self._shortcuts_column = QWidget()
        self._shortcuts_column.setObjectName("settings_shortcuts_column")
        self._shortcuts_column.setFixedWidth(420)
        shortcuts_layout = QVBoxLayout(self._shortcuts_column)
        shortcuts_layout.setContentsMargins(16, 16, 16, 16)
        shortcuts_layout.setSpacing(12)

        self._shortcuts_group = QGroupBox("Keyboard Shortcuts")
        shortcut_form = QFormLayout(self._shortcuts_group)
        self._shortcut_edits: dict[str, QKeySequenceEdit] = {}
        for action, label, _default in SHORTCUT_ACTIONS:
            edit = QKeySequenceEdit()
            edit.setClearButtonEnabled(True)
            edit.setToolTip("Click and press the desired shortcut. Clear to disable this shortcut.")
            self._shortcut_edits[action] = edit
            shortcut_form.addRow(label, edit)
        shortcuts_layout.addWidget(self._shortcuts_group)

        shortcut_hint = QLabel(
            "Shortcuts save immediately. Empty bindings are disabled. "
            "Enter starts the active measurement workspace unless you are typing in an editor."
        )
        shortcut_hint.setWordWrap(True)
        shortcut_hint.setProperty("tone", "muted")
        shortcuts_layout.addWidget(shortcut_hint)
        reset_btn = QPushButton("Reset Shortcuts")
        reset_btn.clicked.connect(self._reset_shortcuts)
        shortcuts_layout.addWidget(reset_btn)
        shortcuts_layout.addStretch(1)
        root_layout.addWidget(self._shortcuts_column)
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
        self._rnd_session_dir.editingFinished.connect(
            lambda: self._save("rnd_session_directory", self._rnd_session_dir.text().strip())
        )
        self._rnd_session_browse.clicked.connect(self._choose_rnd_session_dir)
        self._automation_dir.editingFinished.connect(
            lambda: self._save("automation_directory", self._automation_dir.text().strip())
        )
        self._automation_browse.clicked.connect(self._choose_automation_dir)
        for action, edit in self._shortcut_edits.items():
            edit.editingFinished.connect(
                lambda action=action, edit=edit: self._save_shortcut(action, edit.keySequence().toString())
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

    def _choose_rnd_session_dir(self) -> None:
        current = self._rnd_session_dir.text().strip()
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Choose R&D Session Folder",
            current,
        )
        if not chosen:
            return
        self._rnd_session_dir.setText(chosen)
        self._save("rnd_session_directory", chosen)

    def _choose_automation_dir(self) -> None:
        current = self._automation_dir.text().strip()
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Choose Automation Folder",
            current,
        )
        if not chosen:
            return
        self._automation_dir.setText(chosen)
        self._save("automation_directory", chosen)

    def _save_shortcut(self, action: str, sequence: str) -> None:
        bindings = shortcut_bindings_from_settings(self._settings.get("shortcut_bindings"))
        bindings[action] = sequence.strip()
        self._save("shortcut_bindings", bindings)

    def _reset_shortcuts(self) -> None:
        self._save("shortcut_bindings", dict(DEFAULT_SHORTCUT_BINDINGS))
        self.refresh_from_settings()

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
            self._rnd_session_dir,
            self._automation_dir,
            *self._shortcut_edits.values(),
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
            self._rnd_session_dir.setText(str(self._settings.get("rnd_session_directory") or ""))
            self._automation_dir.setText(str(self._settings.get("automation_directory") or ""))
            bindings = shortcut_bindings_from_settings(self._settings.get("shortcut_bindings"))
            for action, edit in self._shortcut_edits.items():
                edit.setKeySequence(bindings.get(action, ""))
        finally:
            for control in controls:
                control.blockSignals(False)

    def set_editing_enabled(self, enabled: bool) -> None:
        self._sweep_group.setEnabled(enabled)
        self._audio_tools_group.setEnabled(enabled)
        self._safety_group.setEnabled(enabled)
        self._rnd_group.setEnabled(enabled)
        self._automation_group.setEnabled(enabled)
        self._shortcuts_group.setEnabled(enabled)
