from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from dms.brand_access import verify_brand_password
from dms.settings_manager import SettingsManager
from dms.shortcuts import (
    DEFAULT_SHORTCUT_BINDINGS,
    SHORTCUT_ACTIONS,
    shortcut_bindings_from_settings,
)
from dms.style_tokens import THEME_DEFINITIONS, theme_definition
from dms.ui.modern_button import ModernButton as QPushButton
from dms.ui.modern_spinbox import ModernDoubleSpinBox as QDoubleSpinBox
from dms.ui.theme_surface import ThemePreview


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

        self._themes_group = QGroupBox("Themes")
        themes_layout = QVBoxLayout(self._themes_group)
        self._theme_button_group = QButtonGroup(self)
        self._theme_buttons: dict[str, QRadioButton] = {}
        self._theme_choice_rows = QWidget()
        theme_rows_layout = QVBoxLayout(self._theme_choice_rows)
        theme_rows_layout.setContentsMargins(0, 0, 0, 0)
        theme_rows_layout.setSpacing(6)
        for definition in THEME_DEFINITIONS:
            row = QHBoxLayout()
            radio = QRadioButton(definition.label)
            radio.setToolTip(f"Use the {definition.label} interface theme.")
            self._theme_button_group.addButton(radio)
            self._theme_buttons[definition.key] = radio
            row.addWidget(radio, 1)
            row.addWidget(ThemePreview(definition.key))
            theme_rows_layout.addLayout(row)
        themes_layout.addWidget(self._theme_choice_rows)
        theme_hint = QLabel(
            "Theme changes apply immediately. The 95 themes use square controls, "
            "beveled edges, classic tabs, and no button glow."
        )
        theme_hint.setWordWrap(True)
        theme_hint.setProperty("tone", "muted")
        themes_layout.addWidget(theme_hint)
        layout.addWidget(self._themes_group)

        self._appearance_group = QGroupBox("brand")
        appearance_layout = QVBoxLayout(self._appearance_group)
        self._brand_mode = QCheckBox("brand mode")
        self._brand_mode.setToolTip(
            "Re-themes the app to the brand brand. Curator exports switch to the "
            "4K branded poster layout with brand trace colors."
        )
        appearance_layout.addWidget(self._brand_mode)
        brand_mode_hint = QLabel(
            "Curator export becomes a 3840×2160 branded poster. First use on each "
            "computer requires the BRAND access password. This local check does not "
            "certify export authenticity."
        )
        brand_mode_hint.setWordWrap(True)
        brand_mode_hint.setProperty("tone", "muted")
        appearance_layout.addWidget(brand_mode_hint)
        layout.addWidget(self._appearance_group)

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
        self._start_conf_min.setRange(0.0, 30.0)
        self._start_conf_min.setSingleStep(0.5)
        self._start_conf_min.setDecimals(1)
        self._start_conf_min.setSpecialValueText("Off")
        self._start_conf_min.setToolTip(
            "Minimum sweep alignment confidence (peak-to-background of the sweep "
            "correlation) in every mode. A recording is rejected only when this "
            "AND the noise margin both fall below their minimums, so rolled-off "
            "band edges and echoes do not cause false rejections. 0 turns the "
            "check off."
        )

        self._noise_margin_min = QDoubleSpinBox()
        self._noise_margin_min.setRange(0.0, 60.0)
        self._noise_margin_min.setSingleStep(0.5)
        self._noise_margin_min.setDecimals(1)
        self._noise_margin_min.setSuffix(" dB")
        self._noise_margin_min.setSpecialValueText("Off")
        self._noise_margin_min.setToolTip(
            "Minimum level of the middle of the sweep above the measured noise "
            "floor. Used together with alignment confidence: both must fail for "
            "a rejection. 0 turns the check off."
        )

        self._snr_warn = QDoubleSpinBox()
        self._snr_warn.setRange(0.0, 60.0)
        self._snr_warn.setSingleStep(1.0)
        self._snr_warn.setDecimals(1)
        self._snr_warn.setSuffix(" dB")
        self._snr_warn.setSpecialValueText("Off")
        self._snr_warn.setToolTip(
            "Show a warning in the review dialog when the sweep SNR is below "
            "this value. Never rejects a measurement. 0 turns the warning off."
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
        sweep_form.addRow("Alignment Confidence Min", self._start_conf_min)
        sweep_form.addRow("Sweep Noise Margin Min", self._noise_margin_min)
        sweep_form.addRow("SNR Warning Below", self._snr_warn)
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
        self._confirm_clear_metadata = QCheckBox("Confirm before clearing headphone metadata")
        safety_layout.addWidget(self._confirm_clear_metadata)
        self._save_failed_recordings = QCheckBox("Save failed recordings for diagnosis")
        self._save_failed_recordings.setToolTip(
            "When a sweep is rejected, write the raw recording and a JSON sidecar "
            "to the application data folder (failed_recordings). Replay them with "
            "tools/replay_failed_recording.py. Off by default."
        )
        safety_layout.addWidget(self._save_failed_recordings)
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

        self._measure_group = QGroupBox("Measure Sessions")
        measure_layout = QVBoxLayout(self._measure_group)
        measure_row = QHBoxLayout()
        self._measure_session_dir = QLineEdit()
        self._measure_session_dir.setPlaceholderText("Default: Documents")
        measure_row.addWidget(self._measure_session_dir, 1)
        self._measure_session_browse = QPushButton("Browse...")
        measure_row.addWidget(self._measure_session_browse)
        measure_layout.addWidget(QLabel("Default save/load folder"))
        measure_layout.addLayout(measure_row)
        layout.addWidget(self._measure_group)

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
            lambda: self._save("start_alignment_confidence_min", self._start_conf_min.value())
        )
        self._noise_margin_min.editingFinished.connect(
            lambda: self._save("sweep_noise_margin_min_db", self._noise_margin_min.value())
        )
        self._snr_warn.editingFinished.connect(
            lambda: self._save("snr_warn_db", self._snr_warn.value())
        )
        self._end_conf_min.editingFinished.connect(
            lambda: self._save("end_marker_confidence_min", self._end_conf_min.value())
        )
        self._timing_drift_max_ms.editingFinished.connect(
            lambda: self._save("timing_drift_max_ms", self._timing_drift_max_ms.value())
        )
        self._confirm_clear.toggled.connect(
            lambda checked: self._save("confirm_clear_measurements", checked)
        )
        self._confirm_clear_metadata.toggled.connect(
            lambda checked: self._save("confirm_clear_metadata", checked)
        )
        self._save_failed_recordings.toggled.connect(
            lambda checked: self._save("save_failed_recordings", checked)
        )
        self._rnd_session_dir.editingFinished.connect(
            lambda: self._save("rnd_session_directory", self._rnd_session_dir.text().strip())
        )
        self._rnd_session_browse.clicked.connect(self._choose_rnd_session_dir)
        self._measure_session_dir.editingFinished.connect(
            lambda: self._save(
                "measure_session_directory", self._measure_session_dir.text().strip()
            )
        )
        self._measure_session_browse.clicked.connect(self._choose_measure_session_dir)
        self._automation_dir.editingFinished.connect(
            lambda: self._save("automation_directory", self._automation_dir.text().strip())
        )
        self._automation_browse.clicked.connect(self._choose_automation_dir)
        self._brand_mode.toggled.connect(self._on_brand_mode_toggled)
        for theme_key, button in self._theme_buttons.items():
            button.toggled.connect(
                lambda checked, theme_key=theme_key: checked and self._save("theme", theme_key)
            )
        for action, edit in self._shortcut_edits.items():
            edit.editingFinished.connect(
                lambda action=action, edit=edit: self._save_shortcut(
                    action, edit.keySequence().toString()
                )
            )
        self._calibration_btn.clicked.connect(self.calibration_requested)
        self._test_level_btn.clicked.connect(self.test_level_requested)

    def _save(self, key: str, value: object) -> None:
        self._settings.set(key, value)
        self.settings_changed.emit(key, value)

    def _on_brand_mode_toggled(self, checked: bool) -> None:
        if checked and not bool(self._settings.get("brand_mode_unlocked")):
            password, accepted = QInputDialog.getText(
                self,
                "Unlock brand Mode",
                "Enter the BRAND access password:",
                QLineEdit.EchoMode.Password,
            )
            if not accepted or not verify_brand_password(password):
                self._brand_mode.blockSignals(True)
                self._brand_mode.setChecked(False)
                self._brand_mode.blockSignals(False)
                if accepted:
                    QMessageBox.warning(
                        self,
                        "brand Mode",
                        "The password was not accepted.",
                    )
                return
            self._settings.update(
                {
                    "brand_mode_unlocked": True,
                    "brand_mode": True,
                }
            )
            self.settings_changed.emit("brand_mode", True)
            self._theme_choice_rows.setEnabled(False)
            return
        self._save("brand_mode", checked)
        self._theme_choice_rows.setEnabled(not checked)

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

    def _choose_measure_session_dir(self) -> None:
        current = self._measure_session_dir.text().strip()
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Choose Measure Session Folder",
            current,
        )
        if not chosen:
            return
        self._measure_session_dir.setText(chosen)
        self._save("measure_session_directory", chosen)

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
            self._brand_mode,
            *self._theme_buttons.values(),
            self._duration,
            self._fs,
            self._buf,
            self._pre_silence,
            self._post_silence,
            self._latency,
            self._start_conf_min,
            self._noise_margin_min,
            self._snr_warn,
            self._end_conf_min,
            self._timing_drift_max_ms,
            self._confirm_clear,
            self._confirm_clear_metadata,
            self._save_failed_recordings,
            self._rnd_session_dir,
            self._measure_session_dir,
            self._automation_dir,
            *self._shortcut_edits.values(),
        )
        for control in controls:
            control.blockSignals(True)
        try:
            brand_mode = bool(self._settings.get("brand_mode"))
            self._brand_mode.setChecked(brand_mode)
            active_theme = theme_definition(self._settings.get("theme")).key
            self._theme_buttons[active_theme].setChecked(True)
            self._theme_choice_rows.setEnabled(not brand_mode)
            self._duration.setValue(float(self._settings.get("sweep_duration")))
            self._fs.setCurrentIndex(self._fs.findData(self._settings.get("sample_rate")))
            self._buf.setCurrentIndex(self._buf.findData(self._settings.get("buffer_size")))
            self._pre_silence.setValue(float(self._settings.get("pre_sweep_silence")))
            self._post_silence.setValue(float(self._settings.get("post_sweep_silence")))
            self._latency.setCurrentText(str(self._settings.get("latency")))
            self._start_conf_min.setValue(
                float(self._settings.get("start_alignment_confidence_min"))
            )
            self._noise_margin_min.setValue(float(self._settings.get("sweep_noise_margin_min_db")))
            self._snr_warn.setValue(float(self._settings.get("snr_warn_db")))
            self._end_conf_min.setValue(float(self._settings.get("end_marker_confidence_min")))
            self._timing_drift_max_ms.setValue(float(self._settings.get("timing_drift_max_ms")))
            self._confirm_clear.setChecked(bool(self._settings.get("confirm_clear_measurements")))
            self._confirm_clear_metadata.setChecked(
                bool(self._settings.get("confirm_clear_metadata"))
            )
            self._save_failed_recordings.setChecked(
                bool(self._settings.get("save_failed_recordings"))
            )
            self._rnd_session_dir.setText(str(self._settings.get("rnd_session_directory") or ""))
            self._measure_session_dir.setText(
                str(self._settings.get("measure_session_directory") or "")
            )
            self._automation_dir.setText(str(self._settings.get("automation_directory") or ""))
            bindings = shortcut_bindings_from_settings(self._settings.get("shortcut_bindings"))
            for action, edit in self._shortcut_edits.items():
                edit.setKeySequence(bindings.get(action, ""))
        finally:
            for control in controls:
                control.blockSignals(False)

    def set_editing_enabled(self, enabled: bool) -> None:
        self._themes_group.setEnabled(enabled)
        self._appearance_group.setEnabled(enabled)
        self._sweep_group.setEnabled(enabled)
        self._audio_tools_group.setEnabled(enabled)
        self._safety_group.setEnabled(enabled)
        self._rnd_group.setEnabled(enabled)
        self._measure_group.setEnabled(enabled)
        self._automation_group.setEnabled(enabled)
        self._shortcuts_group.setEnabled(enabled)
