"""
Main application window.
Orchestrates: device selectors, level meter, dual plot, queue control,
pass/fail UI, HRTF selector, settings/calibration, and export.
"""

import sys
import shlex
import tempfile
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
from PyQt6.QtCore import (
    QEvent,
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    QSize,
    QThread,
    QTimer,
    Qt,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import QDesktopServices, QFontMetrics, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton as NativePushButton,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QStyle,
    QStyleOptionButton,
    QTabWidget,
    QKeySequenceEdit,
    QToolButton,
    QTextEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
    QApplication,
)
from dms.ui.modern_button import ModernButton as QPushButton
from dms.ui.modern_spinbox import (
    ModernDoubleSpinBox as QDoubleSpinBox,
    ModernSpinBox as QSpinBox,
)

from dms.audio_engine import (
    DualLevelMonitor,
    LevelMonitor,
    SweepWorker,
    device_channel_count,
    device_label,
    device_setting,
    duplicate_device_names,
    filter_devices_by_hostapi,
    get_input_devices,
    get_output_devices,
    is_compatible_device_pair,
    is_windows_audio_host,
    preferred_windows_hostapi,
    refresh_audio_backend,
    resolve_device_selection,
)
from dms.channel_balance import ChannelBalanceEngine, frequency_limit
from dms.automation import AutomationDefinition, AutomationStep, default_automation_directory
from dms.calibration import CalibrationStore
from dms.console import ConsoleEventStore, exception_diagnostics, runtime_diagnostics
from dms.curator.metadata import shared_metadata
from dms.curator.models import CurveData
from dms.curator.parser import parse_measurement_txt
from dms.export import (
    build_filename,
    build_variation_filename,
    export_curve,
    export_variation,
)
from dms.hrtf import HRTFCurve
from dms.measurement_alignment import (
    format_diagnostics_summary,
    is_retryable_timing_failure,
)
from dms.measurement_profiles import (
    PROFILE_SNAPSHOT_SETTING,
    bluetooth_profile_updates,
    restore_standard_profile_updates,
    snapshot_measurement_profile,
)
from dms.measurement_txt import load_two_column_txt_curve
from dms.processing import (
    compute_frequency_response,
    compute_rms_average,
    downsample_to_log_points,
    generate_log_sweep,
    normalize_at_1khz,
    smooth_fractional_octave,
)
from dms.two_channel import (
    TwoChannelCurvePair,
    channel_curves,
    combined_pair_curves,
    curve_label_for_selection,
    shared_normalize_pair_at_1khz,
)
from dms.rnd.models import (
    RnDGroup,
    RnDMeasurement,
    RnDSession,
    generate_measurement_name,
    group_variation as rnd_group_variation,
    measurement_session_data,
    session_snapshot,
)
from dms.rnd.persistence import (
    RND_SESSION_EXTENSION,
    ensure_rnd_session_extension,
    load_rnd_session,
    save_rnd_session,
    session_snapshot as rnd_persistence_snapshot,
)
from dms.rnd.recovery import RecoveryCandidate, RnDRecoveryManager
from dms.secure_store import decrypt_credentials, encrypt_credentials
from dms.session import SessionData
from dms.settings_manager import SettingsManager, config_dir
from dms.shortcuts import SHORTCUT_ACTIONS, shortcut_bindings_from_settings
from dms.squiglink import (
    PHONE_BOOK_REMOTE_PATH,
    RemotePhoneBookInvalidError,
    RemotePhoneBookMissingError,
    build_phone_book_name_stem,
    build_upload_name_stem,
    merge_phone_book_entry,
    open_sftp_connection,
    read_remote_phone_book,
    upload_export_sftp,
    write_remote_phone_book,
)
from dms.theme import ThemeController
from dms.update_checker import UpdateCheckWorker
from dms.version import __version__
from dms.ui.calibration_dialog import CalibrationDialog
from dms.ui.automation_widget import AutomationWidget
from dms.ui.console_widget import ConsoleWidget
from dms.ui.curator_widget import CuratorWidget
from dms.ui.measure_workspace import MeasureWorkspace
from dms.ui.level_meter import LevelMeterWidget
from dms.ui.rnd_widget import RnDWidget
from dms.ui.session_dialog import SessionEditor
from dms.ui.settings_dialog import SettingsWidget
from dms.ui.theme_surface import DitherSurface
from dms.ui.toggle_switch import ToggleSwitch


class AppState:
    IDLE = "idle"
    SWEEPING = "sweeping"
    PASS_FAIL = "pass_fail"
    QUEUE_RUNNING = "queue_running"


_MEASUREMENT_F_MIN = 20.0
_MEASUREMENT_F_MAX = 20000.0
_DISPLAY_AVG_POINTS = 1200
_DISPLAY_AVG_SMOOTHING = 48
_METER_UPDATE_MS = 140


class RnDRecoveryDialog(QDialog):
    """Select and act on a recoverable R&D session."""

    RESTORE = "restore"
    DISCARD = "discard"
    KEEP = "keep"

    def __init__(self, candidates: list[RecoveryCandidate], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Recover R&D Session")
        self.setModal(True)
        self.action = self.KEEP
        self._candidates = candidates

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Fastgraph found R&D session data that was not cleared during a normal exit."
        ))
        self._candidate_combo = QComboBox()
        for candidate in candidates:
            self._candidate_combo.addItem(candidate.label, candidate)
        layout.addWidget(self._candidate_combo)

        row = QHBoxLayout()
        restore = QPushButton("Restore Session")
        restore.clicked.connect(lambda: self._finish(self.RESTORE))
        discard = QPushButton("Discard")
        discard.clicked.connect(lambda: self._finish(self.DISCARD))
        keep = QPushButton("Keep for Later")
        keep.clicked.connect(lambda: self._finish(self.KEEP))
        row.addWidget(restore)
        row.addWidget(discard)
        row.addStretch(1)
        row.addWidget(keep)
        layout.addLayout(row)

    def selected_candidate(self) -> RecoveryCandidate:
        return self._candidate_combo.currentData()

    def _finish(self, action: str) -> None:
        self.action = action
        self.accept()
_MAX_SWEEP_ATTEMPTS = 3
_QUEUE_AMBIENT_WARN_DBFS = -45.0
ROOT_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
HRTF_DIR = ROOT_DIR / "HRTFs"

_CONSOLE_SETTING_SPECS = {
    "sweep_duration": ("float", 0.5, 30.0),
    "sample_rate": ("choice", {44100, 48000, 88200, 96000, 192000}),
    "buffer_size": ("choice", {64, 128, 256, 512, 1024, 2048, 4096}),
    "pre_sweep_silence": ("float", 0.05, 2.0),
    "post_sweep_silence": ("float", 0.1, 3.0),
    "latency": ("choice", {"low", "high"}),
    "start_alignment_confidence_min": ("float", 2.0, 30.0),
    "end_marker_confidence_min": ("float", 2.0, 30.0),
    "timing_drift_max_ms": ("float", 5.0, 250.0),
    "bluetooth_mode": ("bool",),
    "queue_count": ("int", 1, 100),
    "output_level": ("float", -120.0, 0.0),
}

_CONSOLE_SETTING_KEYS = {
    "bluetooth_mode": "bluetooth_headphone_mode",
    "output_level": "queue_output_level_db",
}


class _EventStatusBar(QStatusBar):
    def __init__(self, events: ConsoleEventStore, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._events = events

    def showMessage(self, message: str, timeout: int = 0) -> None:
        super().showMessage(message, timeout)
        severity = "WARNING" if any(
            word in message.lower() for word in ("failed", "error", "warning", "aborted", "canceled", "unavailable")
        ) else "INFO"
        self._events.publish(severity, "status", message)


class _SweepThread(QThread):
    def __init__(self, worker: SweepWorker, **kwargs) -> None:
        super().__init__()
        self._worker = worker
        self._kwargs = kwargs

    def run(self) -> None:
        self._worker.run(**self._kwargs)

    def abort(self) -> None:
        self._worker.abort()


class _BalanceThread(QThread):
    def __init__(self, engine: ChannelBalanceEngine, **kwargs) -> None:
        super().__init__()
        self._engine = engine
        self._kwargs = kwargs

    def run(self) -> None:
        self._engine.run(**self._kwargs)


class TestLevelDialog(QDialog):
    def __init__(
        self,
        snapshot_fn: Callable[[], tuple[float, Optional[float], str]],
        play_noise_fn: Optional[Callable[[], Optional[str]]] = None,
        calibrated: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._snapshot_fn = snapshot_fn
        self._play_noise_fn = play_noise_fn
        self._calibrated = calibrated
        self.setWindowTitle("DMS fastgraph — Test Level")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Live input test level for the currently selected input channel.\n"
            "If calibrated, SPL is shown. Otherwise, use dBFS + noise ping to verify routing."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._device_label = QLabel("Device: —")
        layout.addWidget(self._device_label)

        self._dbfs_label = QLabel("— dBFS")
        self._dbfs_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dbfs_label.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(self._dbfs_label)

        self._spl_label = QLabel("— dB SPL")
        self._spl_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spl_label.setStyleSheet("font-size: 30px; font-weight: bold;")
        layout.addWidget(self._spl_label)

        self._hint_label = QLabel("")
        self._hint_label.setWordWrap(True)
        self._hint_label.setProperty("tone", "muted")
        if self._calibrated:
            self._hint_label.setText("SPL is calibrated for this input device.")
        else:
            self._hint_label.setText(
                "This device is not SPL-calibrated yet. dB SPL is unavailable; "
                "use dBFS changes to confirm signal."
            )
        layout.addWidget(self._hint_label)

        if self._play_noise_fn is not None:
            self._noise_btn = QPushButton("Play Noise Ping")
            self._noise_btn.clicked.connect(self._play_noise_ping)
            layout.addWidget(self._noise_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_values)
        self._timer.start(100)
        self._update_values()

    def _update_values(self) -> None:
        dbfs, spl, device_name = self._snapshot_fn()
        self._device_label.setText(f"Device: {device_name or '—'}")
        self._dbfs_label.setText(f"{dbfs:.1f} dBFS")
        if spl is None:
            self._spl_label.setText("— dB SPL")
        else:
            self._spl_label.setText(f"{spl:.1f} dB SPL")

    def _play_noise_ping(self) -> None:
        if self._play_noise_fn is None:
            return
        err = self._play_noise_fn()
        if err:
            self._hint_label.setText(err)
        elif self._calibrated:
            self._hint_label.setText("Noise ping sent. Confirm input response and SPL stability.")
        else:
            self._hint_label.setText("Noise ping sent. Confirm input level responds in dBFS.")


class PassFailDialog(QDialog):
    KEEP = "keep"
    FAIL = "fail"
    CANCEL = "cancel"

    def __init__(
        self,
        index: int,
        total: int,
        timing_quality: Optional[tuple[float, float, float, float]] = None,
        diagnostics: Optional[object] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._choice = self.CANCEL
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle("Review Measurement")
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumWidth(360)
        self.setMinimumHeight(150)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        summary = QLabel(
            f"Review measurement {index} of {total} and choose whether to keep it."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        detail = QLabel(
            "The latest sweep is shown in teal in the top plot while you decide."
        )
        detail.setWordWrap(True)
        detail.setProperty("tone", "muted")
        layout.addWidget(detail)

        if timing_quality is not None:
            start_conf, end_conf, drift_ms, snr_db = timing_quality
            bluetooth_mode = bool(
                getattr(diagnostics, "bluetooth_headphone_mode", False)
            )
            warning_message = (
                getattr(diagnostics, "warning_message", None)
                if diagnostics is not None
                else None
            )
            timing_box = QFrame()
            timing_box.setObjectName("diagnostic_box")
            timing_box_layout = QVBoxLayout(timing_box)
            timing_box_layout.setContentsMargins(10, 8, 10, 8)
            timing_box_layout.setSpacing(4)
            if bluetooth_mode:
                quality_text = (
                    f"Timing Quality - start: {start_conf:.1f}, "
                    f"end: {end_conf:.1f}, drift: {drift_ms:.1f} ms, "
                    f"SNR: {snr_db:.1f} dB"
                )
            else:
                quality_text = (
                    f"Sweep Quality - alignment: {start_conf:.1f}, "
                    f"SNR: {snr_db:.1f} dB"
                )
            timing = QLabel(quality_text)
            timing.setWordWrap(True)
            timing.setProperty("tone", "muted")
            timing_box_layout.addWidget(timing)
            if warning_message:
                warning = QLabel(f"Bluetooth timing marginal - {warning_message}")
                warning.setWordWrap(True)
                warning.setProperty("tone", "warning")
                timing_box_layout.addWidget(warning)
            if bluetooth_mode:
                timing_box.setToolTip(
                    "Timing quality guide:\n"
                    "Start confidence: higher is better.\n"
                    "End confidence: higher is better.\n"
                    "Drift (ms): lower is better.\n\n"
                    "SNR (dB): higher is better.\n\n"
                    "Confidence guide (rough):\n"
                    ">= 12 strong, 9-12 good, 7-9 borderline, < 7 weak.\n\n"
                    "Drift guide:\n"
                    "< 5 ms excellent\n"
                    "5-15 ms good\n"
                    "15-35 ms acceptable\n"
                    "> 35 ms may hurt repeatability.\n\n"
                    "SNR guide:\n"
                    ">= 35 dB excellent\n"
                    "25-35 dB good\n"
                    "15-25 dB usable\n"
                    "< 15 dB noisy."
                )
            else:
                timing_box.setToolTip(
                    "Sweep quality guide:\n"
                    "Alignment confidence: higher is better.\n"
                    "SNR (dB): higher is better.\n\n"
                    "SNR guide:\n"
                    ">= 35 dB excellent\n"
                    "25-35 dB good\n"
                    "15-25 dB usable\n"
                    "< 15 dB noisy."
                )
            layout.addWidget(timing_box)

        if diagnostics is not None:
            details_toggle = QToolButton()
            details_toggle.setText("Measurement Diagnostics")
            details_toggle.setCheckable(True)
            details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            layout.addWidget(details_toggle)

            details = QLabel(format_diagnostics_summary(diagnostics))
            details.setObjectName("diagnostic_details")
            details.setWordWrap(True)
            details.setVisible(False)
            layout.addWidget(details)
            details_toggle.toggled.connect(details.setVisible)
            details_toggle.toggled.connect(lambda _checked: self.adjustSize())

        button_row = QHBoxLayout()
        button_row.setSpacing(10)

        keep_btn = QPushButton("Keep")
        keep_btn.setDefault(True)
        keep_btn.setObjectName("btn_keep")
        keep_btn.clicked.connect(self._accept_keep)
        button_row.addWidget(keep_btn)

        fail_btn = QPushButton("Fail / Redo")
        fail_btn.setObjectName("btn_fail")
        fail_btn.clicked.connect(self._accept_fail)
        button_row.addWidget(fail_btn)

        cancel_btn = QPushButton("Cancel Queue")
        cancel_btn.setRole("warning")
        cancel_btn.clicked.connect(self._accept_cancel)
        button_row.addWidget(cancel_btn)

        layout.addLayout(button_row)
        fail_shortcut = QShortcut(QKeySequence("F"), self)
        fail_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        fail_shortcut.activated.connect(self._accept_fail)
        self.adjustSize()

    def choice(self) -> str:
        return self._choice

    def _accept_keep(self) -> None:
        self._choice = self.KEEP
        self.accept()

    def _accept_fail(self) -> None:
        self._choice = self.FAIL
        self.accept()

    def _accept_cancel(self) -> None:
        self._choice = self.CANCEL
        self.accept()


class RnDReviewDialog(QDialog):
    KEEP_NO_CHANGE = "keep_no_change"
    KEEP_CHANGE = "keep_change"
    FAIL = "fail"
    CANCEL = "cancel"

    def __init__(
        self,
        previous_name: str,
        timing_quality: Optional[tuple[float, float, float, float]] = None,
        diagnostics: Optional[object] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._choice = self.CANCEL
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle("Review R&D Measurement")
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        summary = QLabel("Review the latest R&D sweep before adding it to the session.")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        previous = QLabel(
            f"Previous kept measurement: {previous_name}" if previous_name else "Previous kept measurement: none"
        )
        previous.setProperty("tone", "muted")
        previous.setWordWrap(True)
        layout.addWidget(previous)

        if timing_quality is not None:
            start_conf, end_conf, drift_ms, snr_db = timing_quality
            bluetooth_mode = bool(getattr(diagnostics, "bluetooth_headphone_mode", False))
            if bluetooth_mode:
                quality_text = (
                    f"Timing Quality - start: {start_conf:.1f}, end: {end_conf:.1f}, "
                    f"drift: {drift_ms:.1f} ms, SNR: {snr_db:.1f} dB"
                )
            else:
                quality_text = f"Sweep Quality - alignment: {start_conf:.1f}, SNR: {snr_db:.1f} dB"
            timing = QLabel(quality_text)
            timing.setProperty("tone", "muted")
            timing.setWordWrap(True)
            layout.addWidget(timing)

        if diagnostics is not None:
            details_toggle = QToolButton()
            details_toggle.setText("Measurement Diagnostics")
            details_toggle.setCheckable(True)
            details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            layout.addWidget(details_toggle)
            details = QLabel(format_diagnostics_summary(diagnostics))
            details.setObjectName("diagnostic_details")
            details.setWordWrap(True)
            details.setVisible(False)
            layout.addWidget(details)
            details_toggle.toggled.connect(details.setVisible)
            details_toggle.toggled.connect(lambda _checked: self.adjustSize())

        self._notes = QTextEdit()
        self._notes.setPlaceholderText("Change notes, design/sample details, pads, EQ, fixture notes...")
        self._notes.setMaximumHeight(96)
        layout.addWidget(self._notes)

        button_row = QHBoxLayout()
        no_change_btn = QPushButton("Keep: No Change")
        no_change_btn.setDefault(True)
        no_change_btn.setObjectName("btn_keep")
        no_change_btn.clicked.connect(self._accept_no_change)
        button_row.addWidget(no_change_btn)

        change_btn = QPushButton("Keep: Change...")
        change_btn.setObjectName("btn_keep")
        change_btn.clicked.connect(self._accept_change)
        button_row.addWidget(change_btn)

        fail_btn = QPushButton("Fail / Redo")
        fail_btn.setObjectName("btn_fail")
        fail_btn.clicked.connect(self._accept_fail)
        button_row.addWidget(fail_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self._accept_cancel)
        button_row.addWidget(cancel_btn)
        layout.addLayout(button_row)
        fail_shortcut = QShortcut(QKeySequence("F"), self)
        fail_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        fail_shortcut.activated.connect(self._accept_fail)

    def choice(self) -> str:
        return self._choice

    def notes(self) -> str:
        return self._notes.toPlainText().strip()

    def _accept_no_change(self) -> None:
        self._choice = self.KEEP_NO_CHANGE
        self.accept()

    def _accept_change(self) -> None:
        self._choice = self.KEEP_CHANGE
        self.accept()

    def _accept_fail(self) -> None:
        self._choice = self.FAIL
        self.accept()

    def _accept_cancel(self) -> None:
        self._choice = self.CANCEL
        self.accept()


class SquiglinkAuthDialog(QDialog):
    def __init__(
        self,
        parent=None,
        initial_username: str = "",
        initial_password: str = "",
        remember: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Upload to Squiglink")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Enter your Squiglink SFTP credentials."))

        layout.addWidget(QLabel("Username"))
        self._username = QLineEdit()
        self._username.setText(initial_username)
        layout.addWidget(self._username)

        layout.addWidget(QLabel("Password"))
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setText(initial_password)
        layout.addWidget(self._password)

        self._remember = QCheckBox("Remember credentials on this device")
        self._remember.setChecked(remember)
        layout.addWidget(self._remember)

        layout.addWidget(QLabel("Name Modifier"))
        layout.addWidget(QLabel("Optional. Type here if you're using different tips, pads, EQ modes, etc"))
        self._name_modifier = QLineEdit()
        self._name_modifier.setPlaceholderText("")
        layout.addWidget(self._name_modifier)

        self._status = QLabel("")
        self._status.setProperty("tone", "error")
        layout.addWidget(self._status)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        upload_btn = QPushButton("Upload")
        upload_btn.clicked.connect(self._accept_if_valid)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(upload_btn)
        layout.addLayout(btn_row)

    def _accept_if_valid(self) -> None:
        if not self.username():
            self._status.setText("Username is required.")
            return
        if not self.password():
            self._status.setText("Password is required.")
            return
        self.accept()

    def username(self) -> str:
        return self._username.text().strip()

    def password(self) -> str:
        return self._password.text()

    def remember_credentials(self) -> bool:
        return self._remember.isChecked()

    def name_modifier(self) -> str:
        return self._name_modifier.text().strip()


class SquiglinkUploadMetadataDialog(QDialog):
    def __init__(
        self,
        parent=None,
        initial_brand: str = "",
        initial_model: str = "",
        initial_channel_side: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Upload Metadata Required")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Fill required metadata to continue upload."))

        form = QFormLayout()
        self._brand = QLineEdit()
        self._brand.setText(initial_brand)
        form.addRow("Brand *", self._brand)

        self._model = QLineEdit()
        self._model.setText(initial_model)
        form.addRow("Model *", self._model)

        self._channel_side = QComboBox()
        self._channel_side.addItems(["", "L", "R"])
        self._channel_side.setCurrentText(initial_channel_side.strip().upper())
        form.addRow("Channel Side *", self._channel_side)
        layout.addLayout(form)

        self._status = QLabel("")
        self._status.setProperty("tone", "error")
        layout.addWidget(self._status)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save and Continue")
        save_btn.clicked.connect(self._accept_if_valid)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _accept_if_valid(self) -> None:
        missing = []
        if not self.brand():
            missing.append("Brand")
        if not self.model():
            missing.append("Model")
        if self.channel_side() not in {"L", "R"}:
            missing.append("Channel Side")
        if missing:
            self._status.setText(f"Required: {', '.join(missing)}")
            return
        self.accept()

    def brand(self) -> str:
        return self._brand.text().strip()

    def model(self) -> str:
        return self._model.text().strip()

    def channel_side(self) -> str:
        return self._channel_side.currentText().strip().upper()


class _ResponsiveQueueBar(QWidget):
    compact_changed = pyqtSignal(bool)
    _BASE_COMPACT_WIDTH = 1250

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._compact: bool | None = None
        self._additional_compact_width = 0

    @property
    def compact_breakpoint(self) -> int:
        return self._BASE_COMPACT_WIDTH + self._additional_compact_width

    def set_additional_compact_width(self, width: int) -> None:
        width = max(0, int(width))
        if width == self._additional_compact_width:
            return
        self._additional_compact_width = width
        self._update_compact_state(self.width())

    def _update_compact_state(self, width: int) -> None:
        compact = width < self.compact_breakpoint
        if compact != self._compact:
            self._compact = compact
            self.compact_changed.emit(compact)

    def resizeEvent(self, event) -> None:
        self._update_compact_state(event.size().width())
        super().resizeEvent(event)


class _MeasureSubmodeControl(QWidget):
    """Two joined buttons that select the Measure tab submode."""

    balance_toggled = pyqtSignal(bool)
    minimum_width_changed = pyqtSignal(int)
    _TEXT_WIDTH_HEADROOM = 4
    _WIDTH_STATES = (
        QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Off,
        QStyle.StateFlag.State_Enabled
        | QStyle.StateFlag.State_Off
        | QStyle.StateFlag.State_HasFocus,
        QStyle.StateFlag.State_Enabled
        | QStyle.StateFlag.State_Off
        | QStyle.StateFlag.State_MouseOver,
        QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_On,
        QStyle.StateFlag.State_Enabled
        | QStyle.StateFlag.State_On
        | QStyle.StateFlag.State_HasFocus,
        QStyle.StateFlag.State_Enabled
        | QStyle.StateFlag.State_On
        | QStyle.StateFlag.State_MouseOver,
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._width_refresh_pending = False
        self._minimum_width = 0
        self.setObjectName("measure_submode_control")
        self.setProperty("layoutRole", "transparent")
        self.setAccessibleName("Measure mode")
        self.setToolTip("Select Frequency Response or Channel Balance.")
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Preferred,
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.frequency_button = self._make_segment(
            "Frequency Response",
            "first",
            "Frequency Response measure mode",
            "Use Frequency Response mode.",
        )
        self.balance_button = self._make_segment(
            "Channel Balance",
            "last",
            "Channel Balance measure mode",
            "Use Channel Balance mode.",
        )

        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._button_group.addButton(self.frequency_button, 0)
        self._button_group.addButton(self.balance_button, 1)
        layout.addWidget(self.frequency_button)
        layout.addWidget(self.balance_button)

        self.frequency_button.setChecked(True)
        self.balance_button.toggled.connect(self.balance_toggled)
        self.refresh_segment_widths()

    def _make_segment(
        self,
        text: str,
        position: str,
        accessible_name: str,
        tooltip: str,
    ) -> NativePushButton:
        button = NativePushButton(text, self)
        button.setCheckable(True)
        button.setProperty("measureSegment", True)
        button.setProperty("segmentPosition", position)
        button.setAccessibleName(accessible_name)
        button.setToolTip(tooltip)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Preferred,
        )
        button.installEventFilter(self)
        return button

    @property
    def minimum_control_width(self) -> int:
        return self._minimum_width

    @classmethod
    def _required_width_for_state(
        cls,
        button: NativePushButton,
        state: QStyle.StateFlag,
    ) -> int:
        metrics = QFontMetrics(button.font())
        text_size = QSize(
            metrics.horizontalAdvance(button.text()),
            metrics.height(),
        )
        option = QStyleOptionButton()
        option.initFrom(button)
        option.text = button.text()
        option.state = state
        return button.style().sizeFromContents(
            QStyle.ContentsType.CT_PushButton,
            option,
            text_size,
            button,
        ).width()

    def refresh_segment_widths(self) -> None:
        self._width_refresh_pending = False
        widths = []
        for button in (self.frequency_button, self.balance_button):
            required_width = max(
                self._required_width_for_state(button, state)
                for state in self._WIDTH_STATES
            )
            width = required_width + self._TEXT_WIDTH_HEADROOM
            button.setFixedWidth(width)
            widths.append(width)
        minimum_width = sum(widths)
        self.setFixedWidth(minimum_width)
        if minimum_width != self._minimum_width:
            self._minimum_width = minimum_width
            self.minimum_width_changed.emit(minimum_width)

    def _schedule_width_refresh(self) -> None:
        if self._width_refresh_pending:
            return
        self._width_refresh_pending = True
        QTimer.singleShot(0, self.refresh_segment_widths)

    def eventFilter(self, watched, event) -> bool:
        if bool(watched.property("measureSegment")) and event.type() in {
            QEvent.Type.ApplicationFontChange,
            QEvent.Type.FontChange,
            QEvent.Type.Polish,
            QEvent.Type.StyleChange,
        }:
            self._schedule_width_refresh()
        return super().eventFilter(watched, event)


class MainWindow(QMainWindow):
    def __init__(
        self,
        session: SessionData,
        settings: SettingsManager,
        theme_controller: Optional[ThemeController] = None,
    ) -> None:
        super().__init__()
        self._session = session
        self._settings = settings
        if theme_controller is None:
            app = QApplication.instance()
            if app is None:
                raise RuntimeError("QApplication must exist before MainWindow")
            theme_controller = ThemeController(app, settings)
        self._theme_controller = theme_controller
        self._theme_controller.theme_changed.connect(self._on_theme_changed)
        self._theme_controller.brand_mode_changed.connect(self._on_brand_mode_changed)
        self._cal_store = CalibrationStore()

        self._state = AppState.IDLE
        self._kept_curves: list[tuple[np.ndarray, np.ndarray]] = []
        self._average: Optional[tuple[np.ndarray, np.ndarray]] = None
        self._variation: Optional[
            tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]
        ] = None
        self._pending_curve: Optional[tuple[np.ndarray, np.ndarray]] = None
        self._two_channel_pairs: list[TwoChannelCurvePair] = []
        self._pending_pair: TwoChannelCurvePair | None = None
        self._pending_pair_first_raw: tuple[np.ndarray, np.ndarray] | None = None
        self._pending_pair_first_diagnostics: object | None = None
        self._two_channel_stage = 0
        self._start_second_pair_stage = False
        self._two_channel_averages: dict[str, object] = {}
        self._two_channel_variations: dict[str, object] = {}
        self._two_channel_enabled = bool(
            self._settings.get("measure_two_channel_enabled")
        )
        bottom_mode = str(
            self._settings.get("measure_two_channel_bottom_mode") or "combined"
        )
        self._two_channel_bottom_mode = (
            "separate" if bottom_mode == "separate" else "combined"
        )
        self._two_channel_selection = "channel_1"
        self._channel_balance_active = False
        self._balance_engine: ChannelBalanceEngine | None = None
        self._balance_thread: QThread | None = None
        self._balance_waveform = "sine"
        self._balance_frequency = 500.0
        self._balance_level_db = -6.0

        self._queue_target = 0
        self._queue_index = 0
        self._current_sweep_attempts = 0

        self._hrtf: Optional[HRTFCurve] = None

        self._sweep_thread: Optional[_SweepThread] = None
        self._active_sweep_worker: Optional[SweepWorker] = None
        self._pass_fail_dialog: Optional[PassFailDialog] = None
        self._rnd_review_dialog: Optional[RnDReviewDialog] = None
        self._rnd_sweep_active = False

        self._last_level_dbfs = -120.0
        self._displayed_level_dbfs = -60.0
        self._last_input_devices: list[tuple[int, str, int]] = []
        self._last_output_devices: list[tuple[int, str, int]] = []
        self._input_devices_by_index: dict[int, dict] = {}
        self._output_devices_by_index: dict[int, dict] = {}
        self._input_device_labels_by_index: dict[int, str] = {}
        self._output_device_labels_by_index: dict[int, str] = {}
        self._last_timing_quality: Optional[tuple[float, float, float, float]] = None
        self._last_measurement_diagnostics: Optional[object] = None
        self._hrtf_options: list[tuple[str, str]] = []
        self._console_events = ConsoleEventStore(
            parent=self,
            log_path=config_dir() / "logs" / "fastgraph-console.log",
        )
        self._automation_running = False
        self._keyboard_shortcuts: list[QShortcut] = []
        self._rnd_dirty = False
        self._restored_recovery_candidate: RecoveryCandidate | None = None

        self._level_monitor = LevelMonitor()
        self._level_monitor.level_updated.connect(self._on_level_update)
        self._level_monitor.error_occurred.connect(self._on_level_error)
        self._dual_level_monitor = DualLevelMonitor()
        self._dual_level_monitor.levels_updated.connect(self._on_dual_level_update)
        self._dual_level_monitor.error_occurred.connect(self._on_level_error)
        self._last_dual_levels = (-120.0, -120.0)

        self._refresh_window_title()
        self.setMinimumSize(1280, 700)

        self._build_ui()
        self._rnd_recovery = RnDRecoveryManager(
            config_dir() / "recovery" / "rnd",
            self._rnd_recovery_snapshot,
            parent=self,
        )
        self._rnd_recovery.save_succeeded.connect(self._on_rnd_recovery_saved)
        self._rnd_recovery.save_failed.connect(self._on_rnd_recovery_failed)
        self._rnd_widget.state_changed.connect(self._on_rnd_state_changed)
        self._rnd_widget.selection_changed.connect(self._on_rnd_selection_changed)
        self._rnd_widget.view_state_changed.connect(self._on_rnd_selection_changed)
        self._configure_keyboard_shortcuts()
        self._on_theme_changed(self._theme_controller.theme, log=False)
        if bool(self._settings.get("bluetooth_headphone_mode")):
            self._apply_bluetooth_headphone_mode_settings(
                notify=False,
                preserve_standard=False,
            )
        self._restore_hrtf_state()
        self._refresh_devices()
        self._start_level_monitor()
        self._apply_state_ui()
        self._start_update_check()
        self._log_event(
            "INFO",
            "application",
            "Fastgraph ready",
            version=__version__,
            session_id=self._console_events.session_id,
            log_path=str(self._console_events.log_path),
        )
        self._log_event("DEBUG", "diagnostics", "Runtime environment", **runtime_diagnostics())
        QTimer.singleShot(0, self._initialize_rnd_recovery)

        self._meter_ui_timer = QTimer(self)
        self._meter_ui_timer.timeout.connect(self._refresh_level_meter_display)
        self._meter_ui_timer.start(_METER_UPDATE_MS)

        self._balance_ui_timer = QTimer(self)
        self._balance_ui_timer.setInterval(33)
        self._balance_ui_timer.timeout.connect(self._refresh_balance_scope)

        self._device_check_timer = QTimer(self)
        self._device_check_timer.timeout.connect(self._check_devices)
        self._device_check_timer.start(1500)

    def _build_ui(self) -> None:
        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)
        self._tabs.setCornerWidget(
            self._build_tab_header(),
            Qt.Corner.TopLeftCorner,
        )
        self._build_inputs_overlay()
        self._build_metadata_overlay()

        central = QWidget()
        self._measure_tab = central
        self._tabs.addTab(central, "Measure")

        root = QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self._plots = MeasureWorkspace()
        self._plots.measurement_files_dropped.connect(self._import_dropped_measurement_files)
        self._plots.selection_changed.connect(self._on_two_channel_selection_changed)
        self._plots.balance_start_requested.connect(self._start_channel_balance)
        self._plots.balance_stop_requested.connect(self._stop_channel_balance)
        self._plots.balance_parameters_changed.connect(self._on_balance_parameters_changed)
        self._plots.set_header_widget(self._build_measure_queue_bar())
        self._plots.set_between_plots_widget(self._build_measure_plot_controls())
        self._plots.set_footer_widget(self._build_export_controls())
        self._plots.set_two_channel_enabled(self._two_channel_enabled)
        self._plots.two.set_bottom_mode(self._two_channel_bottom_mode)
        root.addWidget(self._plots, 1)

        self._rnd_widget = RnDWidget(
            parent=self,
            notes_expanded=bool(self._settings.get("rnd_notes_expanded")),
            splitter_ratio=float(self._settings.get("rnd_splitter_ratio") or 0.5),
        )
        self._rnd_widget.measure_requested.connect(self._start_rnd_measurement)
        self._rnd_widget.cancel_requested.connect(self._cancel_rnd_measurement)
        self._rnd_widget.export_requested.connect(self._export_rnd_selected)
        self._rnd_widget.send_to_curator_requested.connect(self._send_rnd_to_curator)
        self._rnd_widget.save_requested.connect(self._save_rnd_session)
        self._rnd_widget.load_requested.connect(self._load_rnd_session)
        self._rnd_widget.input_channel_changed.connect(self._on_rnd_input_channel_changed)
        self._rnd_widget.notes_expanded_changed.connect(
            lambda expanded: self._settings.set("rnd_notes_expanded", bool(expanded))
        )
        self._rnd_widget.splitter_ratio_changed.connect(
            lambda ratio: self._settings.set("rnd_splitter_ratio", float(ratio))
        )
        self._tabs.addTab(self._rnd_widget, "R&&D")

        self._curator_widget = CuratorWidget(
            self._console_events,
            theme=self._theme_controller.theme,
            brand_mode=self._theme_controller.brand_mode,
            parent=self,
        )
        self._tabs.addTab(self._curator_widget, "Curator")

        self._console_widget = ConsoleWidget(self._console_events)
        self._console_widget.command_submitted.connect(self._run_console_command)
        self._automation_widget = AutomationWidget(
            self._console_widget,
            self._automation_default_dir,
            lambda: __version__,
            parent=self,
        )
        self._automation_widget.run_requested.connect(self._run_automation)
        self._tabs.addTab(self._automation_widget, "Automation")

        self._settings_widget = SettingsWidget(self._settings, self)
        self._settings_widget.settings_changed.connect(self._on_settings_tab_changed)
        self._settings_widget.calibration_requested.connect(self._open_calibration)
        self._settings_widget.test_level_requested.connect(self._open_test_level)
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        settings_scroll.setWidget(self._settings_widget)
        self._settings_scroll = settings_scroll
        self._tabs.addTab(settings_scroll, "Settings")
        self._tabs.currentChanged.connect(self._on_tab_changed)

        self._statusbar = _EventStatusBar(self._console_events, self)
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("Ready.")
        self._version_label = QLabel(f"v{__version__}")
        self._version_label.setProperty("tone", "muted")
        self._version_label.setToolTip("DMS Fastgraph version")
        self._statusbar.addPermanentWidget(self._version_label)
        self._feedback_btn = QPushButton("Report Bugs / Feedback")
        self._feedback_btn.setObjectName("btn_feedback")
        self._feedback_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._feedback_btn.setToolTip("Open feedback form")
        self._feedback_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl(
                    "https://docs.google.com/forms/d/e/1FAIpQLScHMtJluNWrJnYH2_gcqnrRyhtWF_FQnOB5msfU-NKTFAyElw/viewform?usp=publish-editor"
                )
            )
        )
        self._statusbar.addPermanentWidget(self._feedback_btn)
        self._build_update_indicator()

    def _on_tab_changed(self, _index: int) -> None:
        self._close_inputs_overlay()
        self._close_metadata_overlay()
        if self._tabs.currentWidget() is not self._measure_tab:
            self._stop_channel_balance()
        if self._tabs.currentWidget() is self._settings_scroll:
            self._settings_widget.refresh_from_settings()

    def _on_two_channel_toggled(self, _state: int) -> None:
        if self._state != AppState.IDLE:
            self._two_channel_toggle.blockSignals(True)
            self._two_channel_toggle.setChecked(self._two_channel_enabled)
            self._two_channel_toggle.blockSignals(False)
            return
        enabled = bool(self._two_channel_toggle.isChecked())
        if not enabled:
            self._stop_channel_balance()
            self._measure_frequency_button.setChecked(True)
        self._two_channel_enabled = enabled
        self._settings.set("measure_two_channel_enabled", enabled)
        self._plots.set_two_channel_enabled(enabled)
        self._measure_submode_control.setVisible(enabled)
        self._sync_queue_bar_submode_width()
        self._level_meter_2.setVisible(enabled)
        self._level_status_label_2.setVisible(enabled)
        self._bottom_layout_label.setVisible(enabled)
        self._bottom_layout_combo.setVisible(enabled)
        self._ch_combo.setEnabled(not enabled)
        self._update_queue_progress()
        self._update_plots()
        self._start_level_monitor()
        self._apply_state_ui()
        mode = "Two Channel" if enabled else "Single Channel"
        self._statusbar.showMessage(f"Measure mode: {mode}.")

    def _channel_balance_mode_active(self) -> bool:
        balance_button = getattr(self, "_measure_balance_button", None)
        return bool(
            getattr(self, "_two_channel_enabled", False)
            and balance_button is not None
            and balance_button.isChecked()
        )

    def _on_measure_submode_toggled(self, _checked: bool) -> None:
        balance = self._channel_balance_mode_active()
        if not balance:
            self._stop_channel_balance()
        self._plots.two.set_balance_mode(balance)
        self._bottom_layout_label.setVisible(self._two_channel_enabled and not balance)
        self._bottom_layout_combo.setVisible(self._two_channel_enabled and not balance)
        self._variation_toggle.setVisible(not balance)
        self._hrtf_toggle.setVisible(not balance)
        self._hrtf_combo.setVisible(not balance)
        self._hrtf_label.setVisible(not balance)
        self._level_meter.setVisible(not balance)
        self._level_meter_2.setVisible(self._two_channel_enabled and not balance)
        self._level_status_label.setVisible(not balance)
        self._level_status_label_2.setVisible(self._two_channel_enabled and not balance)
        self._plots.two.set_generator_level(float(self._queue_level_spin.value()))
        self._plots.two.set_frequency_limit(
            frequency_limit(int(self._settings.get("sample_rate")))
        )
        if balance:
            self._level_monitor.stop()
            self._dual_level_monitor.stop()
        else:
            self._start_level_monitor()
            self._update_plots()
        self._apply_state_ui()

    def _on_two_channel_bottom_mode_changed(self, _index: int) -> None:
        mode = str(self._bottom_layout_combo.currentData() or "combined")
        self._two_channel_bottom_mode = "separate" if mode == "separate" else "combined"
        self._settings.set(
            "measure_two_channel_bottom_mode", self._two_channel_bottom_mode
        )
        self._plots.two.set_bottom_mode(self._two_channel_bottom_mode)
        self._update_plots()

    def _on_two_channel_selection_changed(self, selection: str) -> None:
        if selection in {"channel_1", "channel_2"}:
            self._two_channel_selection = selection
        self._sync_export_button()

    def _two_channel_devices_ready(self) -> bool:
        input_info = self._current_input_device_info()
        output_info = self._current_output_device_info()
        return bool(
            input_info is not None
            and output_info is not None
            and int(input_info.get("max_input_channels", 0) or 0) >= 2
            and int(output_info.get("max_output_channels", 0) or 0) >= 2
            and self._selected_audio_pair_is_compatible()
        )

    def _start_channel_balance(self) -> None:
        if self._channel_balance_active:
            return
        if (
            self._state != AppState.IDLE
            or not self._channel_balance_mode_active()
        ):
            return
        if not self._two_channel_devices_ready():
            QMessageBox.warning(
                self,
                "Two Channels Required",
                "Channel Balance needs an input device and an output device with at least two channels.",
            )
            return
        input_device = self._current_input_device()
        output_device = self._current_output_device()
        if input_device is None or output_device is None:
            return

        self._level_monitor.stop()
        self._dual_level_monitor.stop()
        self._balance_level_db = float(self._queue_level_spin.value())
        engine = ChannelBalanceEngine()
        engine.set_parameters(
            self._balance_waveform,
            self._balance_frequency,
            self._balance_level_db,
        )
        engine.error.connect(self._on_balance_error)
        thread = _BalanceThread(
            engine,
            input_device=input_device,
            output_device=output_device,
            sample_rate=int(self._settings.get("sample_rate")),
            block_size=int(self._settings.get("buffer_size")),
            latency=self._sweep_latency_mode(),
        )
        thread.finished.connect(self._on_balance_thread_finished)
        self._balance_engine = engine
        self._balance_thread = thread
        self._channel_balance_active = True
        self._plots.two.set_balance_running(True)
        self._balance_ui_timer.start()
        thread.start()
        self._statusbar.showMessage("Channel Balance generator started.")

    def _stop_channel_balance(self, *_args) -> None:
        engine = self._balance_engine
        thread = self._balance_thread
        if hasattr(self, "_balance_ui_timer"):
            self._balance_ui_timer.stop()
        if engine is not None:
            engine.stop()
        if thread is not None and thread.isRunning():
            thread.wait(1200)
        self._channel_balance_active = False
        if hasattr(self, "_plots"):
            self._plots.two.set_balance_running(False)
        if thread is None or not thread.isRunning():
            self._balance_engine = None
            self._balance_thread = None

    def _on_balance_thread_finished(self) -> None:
        thread = self._balance_thread
        if thread is not None:
            thread.deleteLater()
        self._balance_engine = None
        self._balance_thread = None
        self._channel_balance_active = False
        self._balance_ui_timer.stop()
        self._plots.two.set_balance_running(False)

    def _on_balance_error(self, message: str) -> None:
        self._log_event("ERROR", "channel_balance", message)
        self._statusbar.showMessage(message)
        QMessageBox.warning(self, "Channel Balance Error", message)

    def _on_balance_parameters_changed(
        self, waveform: str, frequency: float, level_db: float
    ) -> None:
        self._balance_waveform = "square" if waveform == "square" else "sine"
        self._balance_frequency = max(
            20.0,
            min(
                frequency_limit(int(self._settings.get("sample_rate"))),
                float(frequency),
            ),
        )
        self._balance_level_db = max(-120.0, min(0.0, float(level_db)))
        if abs(float(self._queue_level_spin.value()) - self._balance_level_db) > 1e-9:
            self._queue_level_spin.setValue(self._balance_level_db)
        if self._balance_engine is not None:
            self._balance_engine.set_parameters(
                self._balance_waveform,
                self._balance_frequency,
                self._balance_level_db,
            )

    def _refresh_balance_scope(self) -> None:
        engine = self._balance_engine
        if engine is None:
            return
        sample_rate = int(self._settings.get("sample_rate"))
        sample_count = int(
            round(5.0 * sample_rate / max(20.0, self._balance_frequency))
        )
        sample_count = max(128, min(sample_count, int(0.25 * sample_rate)))
        left, right, left_db, right_db, delta_db = engine.snapshot(sample_count)
        self._plots.two.update_scope(
            left, right, sample_rate, left_db, right_db, delta_db
        )

    def _toggle_inputs_overlay(self) -> None:
        if self._inputs_overlay_open:
            self._close_inputs_overlay()
        else:
            self._open_inputs_overlay()

    def _open_inputs_overlay(self) -> None:
        self._close_metadata_overlay()
        self._inputs_overlay_open = True
        target = self._inputs_overlay_geometry(
            max(1, self._inputs_overlay.sizeHint().height())
        )
        start = QRect(target.x(), target.y(), target.width(), 0)
        self._inputs_overlay.setGeometry(start)
        self._inputs_overlay.show()
        self._inputs_overlay.raise_()
        self._inputs_overlay_animation.stop()
        self._inputs_overlay_animation.setStartValue(start)
        self._inputs_overlay_animation.setEndValue(target)
        self._inputs_overlay_animation.start()

    def _close_inputs_overlay(self) -> None:
        if not getattr(self, "_inputs_overlay_open", False):
            return
        self._inputs_overlay_open = False
        self._inputs_overlay_animation.stop()
        start = self._inputs_overlay.geometry()
        self._inputs_overlay_animation.setStartValue(start)
        self._inputs_overlay_animation.setEndValue(
            QRect(start.x(), start.y(), start.width(), 0)
        )
        self._inputs_overlay_animation.start()

    def _on_inputs_overlay_animation_finished(self) -> None:
        if not self._inputs_overlay_open:
            self._inputs_overlay.hide()

    def _position_inputs_overlay(self) -> None:
        if not hasattr(self, "_inputs_overlay"):
            return
        self._inputs_overlay.setGeometry(
            self._inputs_overlay_geometry(self._inputs_overlay.height())
        )
        self._inputs_overlay.raise_()

    def _inputs_overlay_geometry(self, height: int) -> QRect:
        anchor = self._inputs_btn.mapTo(
            self._tabs,
            self._inputs_btn.rect().bottomLeft(),
        )
        width = min(520, max(360, self._tabs.width() - 16))
        x = min(max(8, anchor.x()), max(8, self._tabs.width() - width - 8))
        y = anchor.y() + 6
        available_height = max(0, self._tabs.height() - y - 8)
        return QRect(x, y, width, min(max(0, int(height)), available_height))

    def _toggle_metadata_overlay(self) -> None:
        if self._metadata_overlay_open:
            self._close_metadata_overlay()
        else:
            self._open_metadata_overlay()

    def _open_metadata_overlay(self) -> None:
        self._close_inputs_overlay()
        self._metadata_editor.set_session(self._session)
        self._metadata_overlay_open = True
        target = self._metadata_overlay_geometry(
            max(1, self._metadata_overlay.sizeHint().height())
        )
        start = QRect(target.x(), target.y(), target.width(), 0)
        self._metadata_overlay.setGeometry(start)
        self._metadata_overlay.show()
        self._metadata_overlay.raise_()
        self._metadata_overlay_animation.stop()
        self._metadata_overlay_animation.setStartValue(start)
        self._metadata_overlay_animation.setEndValue(target)
        self._metadata_overlay_animation.start()

    def _close_metadata_overlay(self) -> None:
        if not getattr(self, "_metadata_overlay_open", False):
            return
        self._metadata_overlay_open = False
        self._metadata_overlay_animation.stop()
        start = self._metadata_overlay.geometry()
        self._metadata_overlay_animation.setStartValue(start)
        self._metadata_overlay_animation.setEndValue(
            QRect(start.x(), start.y(), start.width(), 0)
        )
        self._metadata_overlay_animation.start()

    def _on_metadata_overlay_animation_finished(self) -> None:
        if not self._metadata_overlay_open:
            self._metadata_overlay.hide()

    def _position_metadata_overlay(self) -> None:
        if not hasattr(self, "_metadata_overlay"):
            return
        self._metadata_overlay.setGeometry(
            self._metadata_overlay_geometry(self._metadata_overlay.height())
        )
        self._metadata_overlay.raise_()

    def _metadata_overlay_geometry(self, height: int) -> QRect:
        anchor = self._metadata_btn.mapTo(
            self._tabs,
            self._metadata_btn.rect().bottomLeft(),
        )
        width = min(580, max(440, self._tabs.width() - 16))
        x = min(max(8, anchor.x()), max(8, self._tabs.width() - width - 8))
        y = anchor.y() + 6
        available_height = max(0, self._tabs.height() - y - 8)
        return QRect(x, y, width, min(max(0, int(height)), available_height))

    @staticmethod
    def _global_point_inside(widget: QWidget, global_point) -> bool:
        return widget.rect().contains(widget.mapFromGlobal(global_point))

    def eventFilter(self, watched, event) -> bool:
        overlays_open = getattr(self, "_inputs_overlay_open", False) or getattr(
            self, "_metadata_overlay_open", False
        )
        if overlays_open:
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self._close_inputs_overlay()
                self._close_metadata_overlay()
                return True
            if event.type() == QEvent.Type.MouseButtonPress and hasattr(event, "globalPosition"):
                point = event.globalPosition().toPoint()
                if getattr(self, "_inputs_overlay_open", False) and not self._global_point_inside(
                    self._inputs_overlay, point
                ) and not self._global_point_inside(self._inputs_btn, point):
                    self._close_inputs_overlay()
                if getattr(self, "_metadata_overlay_open", False) and not self._global_point_inside(
                    self._metadata_overlay, point
                ) and not self._global_point_inside(self._metadata_btn, point):
                    self._close_metadata_overlay()
            if watched is self and event.type() == QEvent.Type.WindowDeactivate:
                self._close_inputs_overlay()
                self._close_metadata_overlay()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_inputs_overlay"):
            self._position_inputs_overlay()
        if hasattr(self, "_metadata_overlay"):
            self._position_metadata_overlay()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if hasattr(self, "_inputs_overlay"):
            self._position_inputs_overlay()
        if hasattr(self, "_metadata_overlay"):
            self._position_metadata_overlay()

    def _on_settings_tab_changed(self, key: str, _value: object) -> None:
        if key in {"sample_rate", "buffer_size", "latency"}:
            self._stop_channel_balance()
            self._plots.two.set_frequency_limit(
                frequency_limit(int(self._settings.get("sample_rate")))
            )
            self._start_level_monitor()
        if key == "shortcut_bindings":
            self._configure_keyboard_shortcuts()
        if key == "brand_mode":
            # SettingsWidget._save already persisted this value; avoid a redundant write.
            self._theme_controller.set_brand_mode(bool(_value), persist=False)
        if key == "theme":
            # SettingsWidget._save already persisted this value; avoid a redundant write.
            self._theme_controller.set_theme(str(_value), persist=False)
        self._log_event("INFO", "settings", "Setting saved", name=key)
        self._statusbar.showMessage("Setting saved.")

    def _configure_keyboard_shortcuts(self) -> None:
        for shortcut in getattr(self, "_keyboard_shortcuts", []):
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self._keyboard_shortcuts = []
        bindings = shortcut_bindings_from_settings(self._settings.get("shortcut_bindings"))
        valid_actions = {action for action, _label, _default in SHORTCUT_ACTIONS}
        for action in valid_actions:
            sequence_text = str(bindings.get(action, "")).strip()
            if not sequence_text:
                continue
            sequence = QKeySequence(sequence_text)
            if sequence.isEmpty():
                continue
            shortcut = QShortcut(sequence, self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(lambda action=action: self._handle_keyboard_shortcut(action))
            self._keyboard_shortcuts.append(shortcut)

    def _shortcut_focus_is_editing(self) -> bool:
        focus = QApplication.focusWidget()
        return isinstance(
            focus,
            (
                QLineEdit,
                QTextEdit,
                QPlainTextEdit,
                QComboBox,
                QSpinBox,
                QDoubleSpinBox,
                QKeySequenceEdit,
            ),
        )

    def _handle_keyboard_shortcut(self, action: str) -> None:
        if self._shortcut_focus_is_editing():
            return
        if action == "start_measurement":
            self._shortcut_start_measurement()
        elif action == "fail_review":
            self._shortcut_fail_review()
        elif action.startswith("tab_"):
            self._shortcut_switch_tab(action)

    def _shortcut_start_measurement(self) -> None:
        if self._tabs.currentWidget() is self._rnd_widget:
            self._start_rnd_measurement()
            return
        if self._tabs.currentIndex() == 0:
            self._start_queue()
            return
        self._statusbar.showMessage("Shortcut ignored: switch to Measure or R&D to start a measurement.")

    def _shortcut_fail_review(self) -> None:
        if self._state != AppState.PASS_FAIL:
            return
        if self._rnd_review_dialog is not None:
            self._rnd_review_dialog._accept_fail()
            return
        self._on_fail()

    def _shortcut_switch_tab(self, action: str) -> None:
        tab_map = {
            "tab_measure": 0,
            "tab_rnd": 1,
            "tab_curator": 2,
            "tab_automation": 3,
            "tab_settings": 4,
        }
        index = tab_map.get(action)
        if index is not None and 0 <= index < self._tabs.count():
            self._tabs.setCurrentIndex(index)

    def _build_tab_header(self) -> QWidget:
        header = DitherSurface()
        header.setObjectName("tab_header_controls")
        header.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        row = QHBoxLayout(header)
        row.setContentsMargins(6, 0, 8, 0)
        row.setSpacing(6)
        row.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        self._inputs_btn = QPushButton("Inputs")
        self._inputs_btn.setObjectName("btn_inputs")
        self._inputs_btn.setRole("primary")
        self._inputs_btn.setProperty("emphasized", True)
        self._inputs_btn.setProperty("darkAccentColor", "#A970FF")
        self._inputs_btn.setFixedHeight(30)
        self._inputs_btn.clicked.connect(self._toggle_inputs_overlay)
        row.addWidget(self._inputs_btn)

        self._metadata_btn = QPushButton("Headphone Metadata…")
        self._metadata_btn.setObjectName("btn_metadata")
        self._metadata_btn.setFixedHeight(30)
        self._metadata_btn.clicked.connect(self._toggle_metadata_overlay)
        row.addWidget(self._metadata_btn)

        self._clear_metadata_btn = QPushButton("Clear Metadata")
        self._clear_metadata_btn.setObjectName("btn_danger")
        self._clear_metadata_btn.setFixedHeight(30)
        self._clear_metadata_btn.clicked.connect(self._clear_metadata)
        row.addWidget(self._clear_metadata_btn)

        self._bluetooth_mode_toggle = ToggleSwitch("Bluetooth")
        self._bluetooth_mode_toggle.setFixedHeight(30)
        self._bluetooth_mode_toggle.setChecked(
            bool(self._settings.get("bluetooth_headphone_mode"))
        )
        self._bluetooth_mode_toggle.setToolTip(
            "Bluetooth Headphone Mode applies safer timing settings for "
            "Bluetooth latency and jitter paths."
        )
        self._bluetooth_mode_toggle.stateChanged.connect(
            self._on_bluetooth_mode_changed
        )
        row.addWidget(self._bluetooth_mode_toggle)
        self._refresh_session_labels()
        return header

    def _on_theme_changed(self, theme: str, log: bool = True) -> None:
        brand = self._theme_controller.brand_mode
        submode_control = getattr(self, "_measure_submode_control", None)
        if submode_control is not None:
            submode_control.refresh_segment_widths()
        plots = getattr(self, "_plots", None)
        if plots is not None:
            plots.apply_theme(theme, brand_mode=brand)
        rnd = getattr(self, "_rnd_widget", None)
        if rnd is not None:
            rnd.apply_theme(theme, brand_mode=brand)
        curator = getattr(self, "_curator_widget", None)
        if curator is not None:
            curator.apply_theme(theme, brand_mode=brand)
        meter = getattr(self, "_level_meter", None)
        if meter is not None:
            meter.update()
        meter_2 = getattr(self, "_level_meter_2", None)
        if meter_2 is not None:
            meter_2.update()
        if log and hasattr(self, "_console_events"):
            self._log_event("INFO", "theme", "Application theme changed", theme=theme)

    def _on_brand_mode_changed(self, enabled: bool) -> None:
        settings_widget = getattr(self, "_settings_widget", None)
        if settings_widget is not None:
            settings_widget.refresh_from_settings()
        self._on_theme_changed(self._theme_controller.theme, log=False)
        if hasattr(self, "_upload_btn"):
            self._sync_export_button()
        if hasattr(self, "_console_events"):
            self._log_event("INFO", "theme", "brand mode changed", brand_mode=enabled)

    def _log_event(self, severity: str, source: str, message: str, **details) -> None:
        self._console_events.publish(severity, source, message, details)
        if (
            severity.upper() == "ERROR"
            and source != "automation"
            and hasattr(self, "_automation_widget")
            and not getattr(self, "_automation_running", False)
        ):
            QTimer.singleShot(0, lambda: self._run_automation_trigger("app_error"))

    def _log_exception(self, source: str, message: str, exc: BaseException, **details) -> None:
        details.update(exception_diagnostics(exc))
        self._log_event("ERROR", source, message, **details)

    def _log_sftp_diagnostic(self, stage: str, details: dict) -> None:
        severity = "WARNING" if stage.endswith("failed") else "DEBUG"
        message = stage.replace("_", " ").capitalize()
        self._log_event(severity, "squiglink", message, stage=stage, **details)

    def _command_reply(self, message: str, error: bool = False) -> None:
        self._log_event("ERROR" if error else "INFO", "console", message)

    def _automation_default_dir(self) -> Path:
        configured = str(self._settings.get("automation_directory") or "").strip()
        if configured:
            return Path(configured).expanduser()
        return default_automation_directory()

    def _run_automation_trigger(self, trigger: str) -> None:
        widget = getattr(self, "_automation_widget", None)
        if widget is None:
            return
        for automation in widget.events.automations_for_trigger(trigger):
            self._run_automation(automation, triggered_by=trigger)

    def _run_automation(self, automation: AutomationDefinition, triggered_by: str = "manual") -> None:
        if getattr(self, "_automation_running", False):
            self._log_event("WARNING", "automation", "Automation already running", name=automation.name)
            return
        self._automation_running = True
        variables = dict(automation.variables)
        self._log_event("INFO", "automation", "Automation started", name=automation.name, trigger=triggered_by)
        try:
            for index, step in enumerate(automation.steps, start=1):
                if not self._automation_condition_matches(step, variables):
                    self._log_event("DEBUG", "automation", "Automation step skipped", step=index)
                    continue
                if self._automation_step_is_risky(step) and not step.skip_risky_confirmation:
                    choice = QMessageBox.question(
                        self,
                        "Confirm Automation Action",
                        f"Run risky automation action?\n\n{step.action}: {step.target} {step.value}",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if choice != QMessageBox.StandardButton.Yes:
                        raise RuntimeError(f"Automation canceled before step {index}.")
                self._execute_automation_step(step, variables)
                self._log_event("INFO", "automation", "Automation step complete", step=index, action=step.action)
            self._log_event("INFO", "automation", "Automation complete", name=automation.name)
        except Exception as exc:
            self._log_event("ERROR", "automation", f"Automation failed: {exc}", name=automation.name)
            if triggered_by == "manual":
                QMessageBox.warning(self, "Automation Failed", str(exc))
        finally:
            self._automation_running = False

    def _automation_condition_matches(self, step: AutomationStep, variables: dict[str, object]) -> bool:
        condition = step.condition
        value = str(condition.value)
        current = str(variables.get(condition.left, ""))
        if condition.kind == "always":
            return True
        if condition.kind == "variable_equals":
            return current == value
        if condition.kind == "variable_not_equals":
            return current != value
        if condition.kind == "variable_contains":
            return value in current
        if condition.kind == "variable_true":
            return bool(variables.get(condition.left))
        if condition.kind == "variable_false":
            return not bool(variables.get(condition.left))
        if condition.kind == "app_state":
            return self._state == value
        if condition.kind == "kept_count_at_least":
            return len(self._kept_curves) >= int(value or 0)
        if condition.kind == "rnd_count_at_least":
            return len(self._rnd_widget.session.measurements) >= int(value or 0)
        if condition.kind == "curator_layers_at_least":
            return len(self._curator_widget.graph_state.layers) >= int(value or 0)
        return False

    @staticmethod
    def _automation_step_is_risky(step: AutomationStep) -> bool:
        return step.action in {
            "measure_start",
            "measure_pass",
            "measure_fail",
            "measure_cancel",
            "rnd_start",
            "rnd_load_session",
            "rnd_export_selected",
            "rnd_send_to_curator",
            "curator_send_measure",
            "curator_export_png",
            "export_average",
            "export_variation",
        }

    def _execute_automation_step(self, step: AutomationStep, variables: dict[str, object]) -> None:
        action = step.action
        target = self._expand_automation_text(step.target, variables)
        value = self._expand_automation_text(step.value, variables)
        if action == "navigate":
            self._automation_navigate(target)
        elif action == "switch_input_device":
            self._automation_switch_input_device(target or value)
        elif action == "switch_input_channel":
            self._automation_switch_input_channel(target or value)
        elif action == "console_command":
            self._run_console_command(target or value)
        elif action == "prompt_info":
            QMessageBox.information(self, "Automation", value or target)
        elif action == "prompt_warning":
            QMessageBox.warning(self, "Automation", value or target)
        elif action == "prompt_yes_no":
            result = QMessageBox.question(
                self,
                "Automation",
                value or target,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            variables[target or "prompt_result"] = result == QMessageBox.StandardButton.Yes
        elif action == "set_variable":
            variables[target] = value
        elif action == "clear_variable":
            variables.pop(target, None)
        elif action in {"increment_variable", "decrement_variable"}:
            current = float(variables.get(target, 0) or 0)
            delta = float(value or 1)
            variables[target] = current + delta if action == "increment_variable" else current - delta
        elif action == "measure_start":
            args = ["start"]
            if target:
                args.append(target)
            if value:
                args.append(value)
            self._run_measure_command(args)
        elif action == "measure_pass":
            self._run_measure_command(["pass"])
        elif action == "measure_fail":
            self._run_measure_command(["fail"])
        elif action == "measure_cancel":
            self._run_measure_command(["cancel"])
        elif action == "rnd_start":
            self._start_rnd_measurement()
        elif action == "rnd_save_session":
            if not self._save_rnd_session():
                raise RuntimeError("R&D session save canceled.")
        elif action == "rnd_load_session":
            self._load_rnd_session()
        elif action == "rnd_export_selected":
            self._export_rnd_selected()
        elif action == "rnd_send_to_curator":
            self._send_rnd_to_curator()
        elif action == "curator_send_measure":
            self._send_to_curator()
        elif action == "curator_command":
            self._run_curator_command(shlex.split(target or value))
        elif action == "curator_export_png":
            self._curator_widget.export_png(target or value)
            self._run_automation_trigger("export_complete")
        elif action == "export_average":
            self._export_average(target or None)
            self._run_automation_trigger("export_complete")
        elif action == "export_variation":
            self._export_variation(target or None)
            self._run_automation_trigger("export_complete")
        elif action == "export_log":
            self._export_console_log(target or None)
            self._run_automation_trigger("export_complete")
        else:
            raise ValueError(f"Unsupported automation action: {action}")

    @staticmethod
    def _expand_automation_text(text: str, variables: dict[str, object]) -> str:
        result = str(text or "")
        for key, value in variables.items():
            result = result.replace("{" + str(key) + "}", str(value))
        return result

    def _automation_navigate(self, target: str) -> None:
        normalized = target.strip().lower()
        labels = {
            "measure": "Measure",
            "r&d": "R&&D",
            "rnd": "R&&D",
            "curator": "Curator",
            "automation": "Automation",
            "console": "Automation",
            "settings": "Settings",
        }
        label = labels.get(normalized, target)
        for index in range(self._tabs.count()):
            if self._tabs.tabText(index) == label:
                self._tabs.setCurrentIndex(index)
                return
        raise ValueError(f"Automation tab target not found: {target}")

    def _automation_switch_input_device(self, requested: str) -> None:
        if self._state != AppState.IDLE:
            raise ValueError("Input device can only be changed while idle.")
        text = requested.strip()
        for index in range(self._in_dev_combo.count()):
            data = self._in_dev_combo.itemData(index)
            label = self._in_dev_combo.itemText(index)
            if text == str(data) or text.casefold() in label.casefold():
                self._in_dev_combo.setCurrentIndex(index)
                return
        raise ValueError(f"Input device unavailable: {requested}")

    def _automation_switch_input_channel(self, requested: str) -> None:
        if self._state != AppState.IDLE:
            raise ValueError("Input channel can only be changed while idle.")
        raw = requested.strip().lower()
        text = raw.removeprefix("ch").strip()
        for index in range(self._ch_combo.count()):
            data = self._ch_combo.itemData(index)
            label = self._ch_combo.itemText(index).lower()
            if text == str(data) or text == str(int(data) + 1) or raw == label:
                self._ch_combo.setCurrentIndex(index)
                return
        raise ValueError(f"Input channel unavailable: {requested}")

    def _run_console_command(self, command: str) -> None:
        echo = command
        if any(word in command.lower() for word in ("password", "credential", "secret", "token")):
            echo = "<redacted command>"
        self._log_event("COMMAND", "console", f"> {echo}")
        try:
            args = shlex.split(command)
        except ValueError as exc:
            self._command_reply(f"Parse error: {exc}", error=True)
            return
        if not args:
            return
        args[0] = args[0].lower()
        try:
            if args == ["help"]:
                self._command_reply(self._console_help())
            elif args == ["clear"]:
                self._console_events.clear()
                self._command_reply("Console cleared.")
            elif args == ["status"]:
                self._command_reply(self._console_status())
            elif args == ["devices"]:
                self._command_reply(self._console_devices())
            elif args[0] == "settings":
                self._run_settings_command(args[1:])
            elif args == ["diagnostics", "system"]:
                self._log_event(
                    "INFO",
                    "diagnostics",
                    "System information",
                    session_id=self._console_events.session_id,
                    persistent_log=str(self._console_events.log_path),
                    **runtime_diagnostics(),
                )
            elif args == ["diagnostics", "last"]:
                if self._last_measurement_diagnostics is None:
                    self._command_reply("No measurement diagnostics are available yet.")
                else:
                    self._command_reply(format_diagnostics_summary(self._last_measurement_diagnostics))
            elif args[0] == "measure":
                self._run_measure_command(args[1:])
            elif args[0] == "export":
                self._run_export_command(args[1:])
            elif args[0] == "curator":
                try:
                    self._run_curator_command(args[1:])
                except Exception as exc:
                    self._log_event(
                        "ERROR",
                        "curator",
                        "Curator command failed",
                        command=" ".join(args[1:]),
                        error=str(exc),
                    )
                    raise
            else:
                self._command_reply("Unknown command. Type 'help' for available commands.", error=True)
        except Exception as exc:
            self._command_reply(f"Command failed: {exc}", error=True)

    @staticmethod
    def _console_help() -> str:
        return "\n".join((
            "Commands:",
            "  help | clear | status | devices | diagnostics system | diagnostics last",
            "  settings list | settings get <name> | settings set <name> <value>",
            "  settings save [<name>|all]",
            "  measure start [count] [level_db] | measure pass | measure fail | measure cancel",
            "  export average [path] | export variation [path] | export squiglink | export log [path]",
            "  curator help  (Curator workspace commands)",
        ))

    def _console_status(self) -> str:
        return "\n".join((
            f"State: {self._state}",
            f"Queue: {self._queue_index}/{self._queue_target or 0}",
            f"Kept curves: {len(self._kept_curves)}",
            f"Curator layers: {len(self._curator_widget.graph_state.layers)} "
            f"({sum(layer.visible for layer in self._curator_widget.graph_state.layers)} visible)",
            f"Output: {self._current_output_device_label() or 'none'}",
            f"Input: {self._current_input_device_label() or 'none'} / channel {self._current_input_channel() + 1}",
            f"Bluetooth mode: {bool(self._settings.get('bluetooth_headphone_mode'))}",
            f"Sweep: {self._settings.get('sweep_duration')} s @ {self._settings.get('sample_rate')} Hz, buffer {self._settings.get('buffer_size')}",
        ))

    def _console_devices(self) -> str:
        output_lines = ["Output devices:"]
        for index, label in self._output_device_labels_by_index.items():
            marker = "*" if index == self._current_output_device() else " "
            output_lines.append(f" {marker} [{index}] {label}")
        input_lines = ["Input devices:"]
        for index, label in self._input_device_labels_by_index.items():
            marker = "*" if index == self._current_input_device() else " "
            input_lines.append(f" {marker} [{index}] {label}")
        if len(output_lines) == 1:
            output_lines.append("   none")
        if len(input_lines) == 1:
            input_lines.append("   none")
        return "\n".join(output_lines + input_lines)

    def _run_settings_command(self, args: list[str]) -> None:
        if args == ["list"]:
            overrides = self._settings.session_overrides()
            lines = []
            for name in _CONSOLE_SETTING_SPECS:
                key = _CONSOLE_SETTING_KEYS.get(name, name)
                suffix = " (session)" if key in overrides else ""
                value = self._queue_level_spin.value() if name == "output_level" else self._settings.get(key)
                lines.append(f"{name} = {value}{suffix}")
            self._command_reply("\n".join(lines))
            return
        if len(args) == 2 and args[0] == "get":
            name = args[1].lower()
            if name not in _CONSOLE_SETTING_SPECS:
                raise ValueError(f"Unknown editable setting: {name}")
            key = _CONSOLE_SETTING_KEYS.get(name, name)
            value = self._queue_level_spin.value() if name == "output_level" else self._settings.get(key)
            session = " (session)" if key in self._settings.session_overrides() else ""
            self._command_reply(f"{name} = {value}{session}")
            return
        if len(args) == 3 and args[0] == "set":
            if self._state != AppState.IDLE:
                raise ValueError("Settings can only be changed while idle.")
            name = args[1].lower()
            value = self._parse_console_setting(name, args[2])
            self._set_console_setting(name, value)
            self._command_reply(f"Session setting applied: {name} = {value}")
            self._log_event("INFO", "settings", "Session setting changed", name=name, value=value)
            return
        if args and args[0] == "save" and len(args) <= 2:
            requested = args[1].lower() if len(args) == 2 else "all"
            if requested == "all":
                saved = self._settings.save_session()
            else:
                if requested not in _CONSOLE_SETTING_SPECS:
                    raise ValueError(f"Unknown editable setting: {requested}")
                if requested == "bluetooth_mode":
                    bluetooth_keys = [
                        "bluetooth_headphone_mode",
                        PROFILE_SNAPSHOT_SETTING,
                        *bluetooth_profile_updates().keys(),
                    ]
                    saved = []
                    for key in bluetooth_keys:
                        saved.extend(self._settings.save_session(key))
                else:
                    saved = self._settings.save_session(_CONSOLE_SETTING_KEYS.get(requested, requested))
            if not saved:
                self._command_reply("No matching session overrides to save.")
            else:
                if "queue_output_level_db" in saved:
                    self._settings.set("queue_output_level_persist", True)
                    self._queue_level_persist_toggle.blockSignals(True)
                    self._queue_level_persist_toggle.setChecked(True)
                    self._queue_level_persist_toggle.blockSignals(False)
                self._command_reply("Saved settings: " + ", ".join(saved))
                self._log_event("INFO", "settings", "Session settings persisted", keys=saved)
            return
        raise ValueError("Usage: settings list|get <name>|set <name> <value>|save [<name>|all]")

    @staticmethod
    def _parse_console_setting(name: str, raw: str):
        if name not in _CONSOLE_SETTING_SPECS:
            raise ValueError(f"Unknown editable setting: {name}")
        spec = _CONSOLE_SETTING_SPECS[name]
        kind = spec[0]
        if kind == "bool":
            lowered = raw.lower()
            if lowered not in {"true", "false", "on", "off", "1", "0"}:
                raise ValueError(f"{name} expects true or false")
            return lowered in {"true", "on", "1"}
        if kind == "choice":
            choices = spec[1]
            value = int(raw) if all(isinstance(item, int) for item in choices) else raw.lower()
            if value not in choices:
                raise ValueError(f"{name} must be one of: {', '.join(map(str, sorted(choices)))}")
            return value
        value = int(raw) if kind == "int" else float(raw)
        if value < spec[1] or value > spec[2]:
            raise ValueError(f"{name} must be between {spec[1]} and {spec[2]}")
        return value

    def _set_console_setting(self, name: str, value) -> None:
        key = _CONSOLE_SETTING_KEYS.get(name, name)
        if name == "bluetooth_mode":
            self._set_console_bluetooth_mode(bool(value))
            return
        self._settings.set_session(key, value)
        if name == "queue_count":
            self._queue_n_spin.blockSignals(True)
            self._queue_n_spin.setValue(int(value))
            self._queue_n_spin.blockSignals(False)
        elif name == "output_level":
            self._queue_level_spin.blockSignals(True)
            self._queue_level_spin.setValue(float(value))
            self._queue_level_spin.blockSignals(False)
        if name in {"sample_rate", "buffer_size"}:
            self._start_level_monitor()
        self._settings_widget.refresh_from_settings()

    def _set_console_bluetooth_mode(self, enabled: bool) -> None:
        current = bool(self._settings.get("bluetooth_headphone_mode"))
        if enabled == current:
            self._settings.set_session("bluetooth_headphone_mode", enabled)
        elif enabled:
            updates = bluetooth_profile_updates()
            self._console_bt_snapshot = {key: self._settings.get(key) for key in updates}
            self._settings.set_session("bluetooth_headphone_mode", True)
            self._settings.set_session(
                PROFILE_SNAPSHOT_SETTING,
                snapshot_measurement_profile(self._console_bt_snapshot),
            )
            for key, value in updates.items():
                self._settings.set_session(key, value)
        else:
            self._settings.set_session("bluetooth_headphone_mode", False)
            for key, value in getattr(self, "_console_bt_snapshot", {}).items():
                self._settings.set_session(key, value)
            self._settings.set_session(PROFILE_SNAPSHOT_SETTING, None)
        self._bluetooth_mode_toggle.blockSignals(True)
        self._bluetooth_mode_toggle.setChecked(enabled)
        self._bluetooth_mode_toggle.blockSignals(False)
        self._settings_widget.refresh_from_settings()

    def _run_measure_command(self, args: list[str]) -> None:
        if args and args[0] == "start" and len(args) <= 3:
            if self._state != AppState.IDLE:
                raise ValueError("A measurement can only be started while idle.")
            if len(args) >= 2:
                count = self._parse_console_setting("queue_count", args[1])
                self._settings.set_session("queue_count", count)
                self._queue_n_spin.blockSignals(True)
                self._queue_n_spin.setValue(int(count))
                self._queue_n_spin.blockSignals(False)
            if len(args) == 3:
                level = self._parse_console_setting("output_level", args[2])
                self._settings.set_session("queue_output_level_db", level)
                self._queue_level_spin.blockSignals(True)
                self._queue_level_spin.setValue(float(level))
                self._queue_level_spin.blockSignals(False)
            self._start_queue()
            return
        if args == ["pass"]:
            if self._state != AppState.PASS_FAIL or self._pending_curve is None:
                raise ValueError("There is no measurement awaiting review.")
            self._log_event("INFO", "review", "Measurement passed from console")
            self._on_keep()
            return
        if args == ["fail"]:
            if self._state != AppState.PASS_FAIL or self._pending_curve is None:
                raise ValueError("There is no measurement awaiting review.")
            self._log_event("WARNING", "review", "Measurement failed from console")
            self._on_fail()
            return
        if args == ["cancel"]:
            if self._state == AppState.IDLE and not self._queue_active():
                raise ValueError("There is no active measurement queue to cancel.")
            self._cancel_queue()
            return
        raise ValueError("Usage: measure start [count] [level_db]|pass|fail|cancel")

    def _run_export_command(self, args: list[str]) -> None:
        if not args:
            raise ValueError("Usage: export average|variation|squiglink|log [path]")
        kind = args[0].lower()
        path = args[1] if len(args) == 2 else None
        if len(args) > 2:
            raise ValueError("Export paths containing spaces must be quoted.")
        if kind == "average":
            if self._state != AppState.IDLE:
                raise ValueError("Average export is only available while idle.")
            if self._bottom_curve_for_display_and_export() is None:
                raise ValueError("No averaged curve is available yet.")
            self._export_average(path)
        elif kind == "variation":
            if self._state != AppState.IDLE:
                raise ValueError("Variation export is only available while idle.")
            if self._variation is None:
                raise ValueError("No variation band is available yet.")
            self._export_variation(path)
        elif kind == "squiglink" and path is None:
            if self._state != AppState.IDLE:
                raise ValueError("Squiglink upload is only available while idle.")
            self._upload_to_squiglink()
        elif kind == "log":
            self._export_console_log(path)
        else:
            raise ValueError("Usage: export average|variation|squiglink|log [path]")

    @staticmethod
    def _curator_help() -> str:
        return "\n".join((
            "Curator commands:",
            "  curator status | curator layers | curator send",
            "  curator import <path> [<path>...]",
            "  curator layer <n> show|hide|remove",
            "  curator layer <n> offset <db> | color <#RRGGBB> | hrtf <name|none>",
            "  curator combine <n> <n> [...] | curator clear",
            "  curator bounds on|off",
            "  curator view limits <min_db> <max_db> | aspect on|off",
            "  curator view background <#RRGGBB|theme>",
            "  curator text title|fixture|footer <text>",
            "  curator reset | curator export <path>",
        ))

    @staticmethod
    def _console_on_off(value: str) -> bool:
        lowered = value.lower()
        if lowered not in {"on", "off"}:
            raise ValueError("Expected on or off.")
        return lowered == "on"

    def _run_curator_command(self, args: list[str]) -> None:
        curator = self._curator_widget
        if args == ["help"]:
            self._command_reply(self._curator_help())
            return
        if args == ["status"]:
            layers = curator.graph_state.layers
            self._command_reply("\n".join((
                f"Layers: {len(layers)}",
                f"Visible: {sum(layer.visible for layer in layers)}",
                f"Bounds: {'on' if curator.graph_state.bounds.enabled else 'off'}",
                f"Limits: {curator.graph_state.y_min:g} to {curator.graph_state.y_max:g} dB",
                f"25 dB/decade: {'on' if curator.graph_state.aspect_locked_25db else 'off'}",
                f"Background: {curator.graph_state.background}",
            )))
            return
        if args == ["layers"]:
            self._command_reply(curator.layer_summary())
            return
        if args == ["send"]:
            self._send_to_curator()
            return
        if args and args[0] == "import" and len(args) >= 2:
            paths = [Path(raw).expanduser() for raw in args[1:]]
            for path in paths:
                if not path.exists() or not path.is_file():
                    raise ValueError(f"Import file does not exist: {path}")
            parsed = [(path, parse_measurement_txt(path)) for path in paths]
            for path, curve in parsed:
                curator.add_curve(curve, path.stem, source_path=path, normalize=True)
            self._command_reply(f"Imported {len(parsed)} Curator file(s).")
            return
        if args and args[0] == "layer" and len(args) >= 3:
            try:
                number = int(args[1])
            except ValueError as exc:
                raise ValueError("Layer number must be an integer.") from exc
            action = args[2].lower()
            if len(args) == 3 and action in {"show", "hide"}:
                curator.set_layer_number_visible(number, action == "show")
            elif len(args) == 3 and action == "remove":
                curator.remove_layer_number(number)
            elif len(args) == 4 and action == "offset":
                curator.set_layer_number_offset(number, float(args[3]))
            elif len(args) == 4 and action == "color":
                curator.set_layer_number_color(number, args[3])
            elif len(args) >= 4 and action == "hrtf":
                curator.set_layer_number_hrtf(number, " ".join(args[3:]))
            else:
                raise ValueError(
                    "Usage: curator layer <n> show|hide|remove|offset <db>|"
                    "color <#RRGGBB>|hrtf <name|none>"
                )
            self._command_reply(f"Curator layer {number} updated.")
            return
        if args and args[0] == "combine" and len(args) >= 3:
            try:
                numbers = [int(value) for value in args[1:]]
            except ValueError as exc:
                raise ValueError("Combine expects integer layer numbers.") from exc
            layer = curator.combine_layer_numbers(numbers)
            self._command_reply(f"Created Curator layer {len(curator.graph_state.layers)}: {layer.name}")
            return
        if args == ["clear"]:
            curator.clear_layers()
            self._command_reply("Curator layers cleared.")
            return
        if len(args) == 2 and args[0] == "bounds":
            curator.set_bounds_enabled(self._console_on_off(args[1]))
            self._command_reply(f"Curator bounds {args[1].lower()}.")
            return
        if len(args) == 4 and args[:2] == ["view", "limits"]:
            curator.set_y_limits(float(args[2]), float(args[3]))
            self._command_reply(f"Curator limits set to {args[2]}..{args[3]} dB.")
            return
        if len(args) == 3 and args[:2] == ["view", "aspect"]:
            curator.set_aspect_locked(self._console_on_off(args[2]))
            self._command_reply(f"Curator aspect lock {args[2].lower()}.")
            return
        if len(args) == 3 and args[:2] == ["view", "background"]:
            if args[2].lower() == "theme":
                curator.reset_background_to_theme()
            else:
                curator.set_background(args[2])
            self._command_reply(f"Curator background set to {curator.graph_state.background}.")
            return
        if len(args) >= 3 and args[0] == "text" and args[1] in {"title", "fixture", "footer"}:
            curator.set_export_text(args[1], " ".join(args[2:]))
            self._command_reply(f"Curator {args[1]} updated.")
            return
        if args == ["reset"]:
            curator.reset_view()
            self._command_reply("Curator view reset.")
            return
        if len(args) == 2 and args[0] == "export":
            path = curator.export_png(args[1])
            self._command_reply(f"Exported Curator PNG: {path}")
            return
        raise ValueError("Unknown Curator command. Type 'curator help' for available commands.")

    def _build_measure_plot_controls(self) -> QWidget:
        row_widget = QWidget()
        row_widget.setObjectName("measure_interplot_controls")
        row_widget.setProperty("layoutRole", "transparent")
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(8)

        input_label = QLabel("Input")
        input_label.setProperty("tone", "muted")
        row.addWidget(input_label)
        self._level_meter = LevelMeterWidget(orientation=Qt.Orientation.Horizontal)
        self._level_meter.setMinimumWidth(160)
        row.addWidget(self._level_meter, 1, Qt.AlignmentFlag.AlignVCenter)
        self._level_status_label = QLabel("RMS")
        self._level_status_label.setProperty("tone", "muted")
        self._level_status_label.setMinimumWidth(30)
        self._level_status_label.setToolTip("Live input RMS monitor")
        row.addWidget(self._level_status_label)

        self._level_meter_2 = LevelMeterWidget(orientation=Qt.Orientation.Horizontal)
        self._level_meter_2.setMinimumWidth(120)
        self._level_meter_2.setVisible(self._two_channel_enabled)
        row.addWidget(self._level_meter_2, 1, Qt.AlignmentFlag.AlignVCenter)
        self._level_status_label_2 = QLabel("R")
        self._level_status_label_2.setProperty("tone", "muted")
        self._level_status_label_2.setVisible(self._two_channel_enabled)
        row.addWidget(self._level_status_label_2)

        self._bottom_layout_label = QLabel("Bottom")
        self._bottom_layout_label.setProperty("tone", "muted")
        self._bottom_layout_label.setVisible(self._two_channel_enabled)
        row.addWidget(self._bottom_layout_label)
        self._bottom_layout_combo = QComboBox()
        self._bottom_layout_combo.addItem("Combined", "combined")
        self._bottom_layout_combo.addItem("Separate", "separate")
        self._bottom_layout_combo.setCurrentIndex(
            1 if self._two_channel_bottom_mode == "separate" else 0
        )
        self._bottom_layout_combo.setVisible(self._two_channel_enabled)
        self._bottom_layout_combo.currentIndexChanged.connect(
            self._on_two_channel_bottom_mode_changed
        )
        row.addWidget(self._bottom_layout_combo)

        self._variation_toggle = ToggleSwitch("Variation")
        self._variation_toggle.setToolTip(
            "Show confidence-style spread of kept measurements in the bottom viewport."
        )
        self._variation_toggle.stateChanged.connect(self._on_bottom_view_changed)
        row.addWidget(self._variation_toggle)

        self._hrtf_toggle = ToggleSwitch("HRTF")
        self._hrtf_toggle.setToolTip("Apply the selected HRTF to the bottom viewport.")
        self._hrtf_toggle.stateChanged.connect(self._update_plots)
        row.addWidget(self._hrtf_toggle)

        self._hrtf_combo = QComboBox()
        self._hrtf_combo.setMinimumWidth(120)
        self._hrtf_combo.setToolTip("Select the HRTF used for compensation.")
        self._hrtf_combo.currentIndexChanged.connect(self._on_hrtf_selected)
        row.addWidget(self._hrtf_combo)
        self._hrtf_label = QLabel("None")
        self._hrtf_label.setProperty("tone", "muted")
        self._hrtf_label.setMaximumWidth(90)
        row.addWidget(self._hrtf_label)
        self._refresh_hrtf_options()

        self._undo_btn = QPushButton("Undo")
        self._undo_btn.clicked.connect(self._undo_last_measurement)
        row.addWidget(self._undo_btn)

        self._clear_btn = QPushButton("Clear All")
        self._clear_btn.setObjectName("btn_danger")
        self._clear_btn.clicked.connect(self._clear_all)
        row.addWidget(self._clear_btn)
        return row_widget

    def _build_export_controls(self) -> QWidget:
        row_widget = QWidget()
        row_widget.setObjectName("measure_export_controls")
        row_widget.setProperty("layoutRole", "transparent")
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(8)

        row.addWidget(QLabel("Export directory:"))
        self._export_dir_input = QLineEdit()
        self._export_dir_input.setPlaceholderText("Default: choose at export")
        self._export_dir_input.setText(str(self._settings.get("export_directory") or ""))
        self._export_dir_input.setMinimumWidth(140)
        self._export_dir_input.setMaximumWidth(240)
        row.addWidget(self._export_dir_input)
        export_dir_btn = QPushButton("Browse…")
        export_dir_btn.clicked.connect(self._choose_export_directory)
        row.addWidget(export_dir_btn)

        self._send_to_rnd_btn = QPushButton("Send to R&D")
        self._send_to_rnd_btn.clicked.connect(self._send_measure_to_rnd)
        row.addWidget(self._send_to_rnd_btn)

        self._export_btn = QPushButton("Export Average…")
        self._export_btn.setObjectName("btn_export")
        self._export_btn.clicked.connect(self._export)
        row.addWidget(self._export_btn)

        self._send_to_curator_btn = QPushButton("Send to Curator")
        self._send_to_curator_btn.clicked.connect(self._send_to_curator)
        self._send_to_curator_btn.setToolTip(
            "Add the current average or variation view to Curator."
        )
        row.addWidget(self._send_to_curator_btn)

        self._upload_btn = QPushButton("Upload to Squiglink")
        self._upload_btn.setObjectName("btn_upload")
        self._upload_btn.clicked.connect(self._run_measure_upload_action)
        row.addWidget(self._upload_btn)
        return row_widget

    def _build_inputs_overlay(self) -> None:
        overlay = QFrame(self._tabs)
        overlay.setObjectName("inputs_overlay")
        overlay.setProperty("surfaceLevel", "raised")
        overlay.setMinimumWidth(430)
        overlay.hide()
        layout = QVBoxLayout(overlay)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        layout.addWidget(QLabel("Output Device"))
        self._out_dev_combo = QComboBox()
        self._out_dev_combo.currentIndexChanged.connect(
            self._on_output_device_changed
        )
        layout.addWidget(self._out_dev_combo)

        layout.addWidget(QLabel("Input Device"))
        self._in_dev_combo = QComboBox()
        self._in_dev_combo.currentIndexChanged.connect(self._on_input_device_changed)
        layout.addWidget(self._in_dev_combo)

        layout.addWidget(QLabel("Input Channel"))
        self._ch_combo = QComboBox()
        self._ch_combo.currentIndexChanged.connect(self._on_channel_changed)
        layout.addWidget(self._ch_combo)

        self._active_ch_label = QLabel("Active input channel: —")
        self._active_ch_label.setObjectName("label_channel_active")
        layout.addWidget(self._active_ch_label)

        self._advanced_windows_drivers_toggle = ToggleSwitch("Advanced Windows Drivers")
        self._advanced_windows_drivers_toggle.setChecked(
            bool(self._settings.get("windows_advanced_audio_drivers"))
        )
        self._advanced_windows_drivers_toggle.setVisible(is_windows_audio_host())
        self._advanced_windows_drivers_toggle.stateChanged.connect(
            self._on_advanced_windows_drivers_changed
        )
        layout.addWidget(self._advanced_windows_drivers_toggle)

        self._refresh_devices_btn = QPushButton("Refresh Devices")
        self._refresh_devices_btn.clicked.connect(self._manual_refresh_devices)
        layout.addWidget(self._refresh_devices_btn)

        self._inputs_overlay = overlay
        self._inputs_overlay_open = False
        self._inputs_overlay_animation = QPropertyAnimation(
            overlay,
            b"geometry",
            self,
        )
        self._inputs_overlay_animation.setDuration(180)
        self._inputs_overlay_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._inputs_overlay_animation.finished.connect(self._on_inputs_overlay_animation_finished)
        QApplication.instance().installEventFilter(self)

    def _build_metadata_overlay(self) -> None:
        overlay = QFrame(self._tabs)
        overlay.setObjectName("metadata_overlay")
        overlay.setProperty("surfaceLevel", "raised")
        overlay.setMinimumWidth(500)
        overlay.hide()
        layout = QVBoxLayout(overlay)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        self._metadata_editor = SessionEditor(overlay, initial_session=self._session)
        self._metadata_editor.setMinimumHeight(430)
        layout.addWidget(self._metadata_editor, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self._close_metadata_overlay)
        actions.addWidget(close_button)
        save_button = QPushButton("Save Metadata")
        save_button.setRole("positive")
        save_button.clicked.connect(self._save_metadata_overlay)
        actions.addWidget(save_button)
        layout.addLayout(actions)

        self._metadata_overlay = overlay
        self._metadata_overlay_open = False
        self._metadata_overlay_animation = QPropertyAnimation(
            overlay,
            b"geometry",
            self,
        )
        self._metadata_overlay_animation.setDuration(180)
        self._metadata_overlay_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._metadata_overlay_animation.finished.connect(
            self._on_metadata_overlay_animation_finished
        )

    def _build_measure_queue_bar(self) -> QWidget:
        bar = _ResponsiveQueueBar()
        self._queue_bar = bar
        bar.setObjectName("measure_queue_bar")
        bar.setProperty("surfaceLevel", "raised")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(4)

        primary_widget = QWidget()
        primary_widget.setProperty("layoutRole", "transparent")
        primary = QHBoxLayout(primary_widget)
        primary.setContentsMargins(0, 0, 0, 0)
        primary.setSpacing(8)
        progress_widget = QWidget()
        progress_widget.setProperty("layoutRole", "transparent")
        progress = QHBoxLayout(progress_widget)
        progress.setContentsMargins(0, 0, 0, 0)
        progress.setSpacing(8)
        outer.addWidget(primary_widget)
        outer.addWidget(progress_widget)

        self._start_queue_btn = QPushButton("Measure")
        self._start_queue_btn.setObjectName("btn_start")
        self._start_queue_btn.clicked.connect(self._start_queue)
        primary.addWidget(self._start_queue_btn)

        self._cancel_queue_btn = QPushButton("Cancel Queue")
        self._cancel_queue_btn.setObjectName("btn_cancel")
        self._cancel_queue_btn.clicked.connect(self._cancel_queue)
        primary.addWidget(self._cancel_queue_btn)

        self._two_channel_toggle = ToggleSwitch("Two Channel")
        self._two_channel_toggle.setChecked(self._two_channel_enabled)
        self._two_channel_toggle.setToolTip(
            "Measure output/input channel 1 as L and channel 2 as R."
        )
        self._two_channel_toggle.stateChanged.connect(self._on_two_channel_toggled)
        primary.addWidget(self._two_channel_toggle)

        self._measure_submode_control = _MeasureSubmodeControl()
        self._measure_frequency_button = (
            self._measure_submode_control.frequency_button
        )
        self._measure_balance_button = (
            self._measure_submode_control.balance_button
        )
        self._measure_submode_control.balance_toggled.connect(
            self._on_measure_submode_toggled
        )
        self._measure_submode_control.minimum_width_changed.connect(
            self._sync_queue_bar_submode_width
        )
        self._measure_submode_control.setVisible(self._two_channel_enabled)
        primary.addWidget(self._measure_submode_control)

        n_label = QLabel("Count")
        n_label.setProperty("tone", "accent")
        primary.addWidget(n_label)
        self._queue_n_spin = QSpinBox()
        self._queue_n_spin.setObjectName("queue_count_spin")
        self._queue_n_spin.setRange(1, 100)
        self._queue_n_spin.setValue(int(self._settings.get("queue_count") or 5))
        self._queue_n_spin.setFixedWidth(110)
        self._queue_n_spin.valueChanged.connect(self._on_queue_count_changed)
        primary.addWidget(self._queue_n_spin)

        level_label = QLabel("Output")
        level_label.setProperty("tone", "accent")
        primary.addWidget(level_label)
        self._queue_level_spin = QDoubleSpinBox()
        self._queue_level_spin.setRange(-120.0, 0.0)
        self._queue_level_spin.setSingleStep(0.5)
        self._queue_level_spin.setDecimals(1)
        self._queue_level_spin.setSuffix(" dB")
        self._queue_level_spin.setFixedWidth(110)
        persist_output_level = bool(self._settings.get("queue_output_level_persist"))
        initial_output_level = float(self._settings.get("queue_output_level_db") or -6.0)
        if not persist_output_level:
            initial_output_level = -6.0
        self._queue_level_spin.setValue(max(-120.0, min(0.0, initial_output_level)))
        self._queue_level_spin.valueChanged.connect(self._on_queue_level_changed)
        primary.addWidget(self._queue_level_spin)
        self._queue_level_persist_toggle = ToggleSwitch("")
        self._queue_level_persist_toggle.setChecked(persist_output_level)
        self._queue_level_persist_toggle.stateChanged.connect(
            self._on_queue_level_persist_changed
        )
        primary.addWidget(
            self._queue_level_persist_toggle,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        self._queue_level_persist_label = QLabel("Remember")
        self._queue_level_persist_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        primary.addWidget(self._queue_level_persist_label)
        primary.addStretch(1)

        self._queue_progress_label = QLabel("Kept: 0")
        progress.addWidget(self._queue_progress_label)

        self._queue_progress_bar = QProgressBar()
        self._queue_progress_bar.setRange(0, 1)
        self._queue_progress_bar.setValue(0)
        self._queue_progress_bar.setMinimumWidth(120)
        progress.addWidget(self._queue_progress_bar, 1)

        sweep_label = QLabel("Sweep")
        self._queue_sweep_label = sweep_label
        progress.addWidget(sweep_label)
        self._sweep_progress = QProgressBar()
        self._sweep_progress.setRange(0, 100)
        self._sweep_progress.setValue(0)
        self._sweep_progress.setMinimumWidth(120)
        progress.addWidget(self._sweep_progress, 1)

        self._queue_primary_widget = primary_widget
        self._queue_primary_layout = primary
        self._queue_progress_widget = progress_widget
        self._queue_progress_layout = progress
        bar.compact_changed.connect(self._set_queue_bar_compact)
        self._queue_bar_compact = True
        self._set_queue_bar_compact(True)
        self._sync_queue_bar_submode_width()
        return bar

    def _sync_queue_bar_submode_width(self, _width: int | None = None) -> None:
        bar = getattr(self, "_queue_bar", None)
        control = getattr(self, "_measure_submode_control", None)
        if bar is None or control is None:
            return
        reservation = 0 if control.isHidden() else control.minimum_control_width
        bar.set_additional_compact_width(reservation)

    def _set_queue_bar_compact(self, compact: bool) -> None:
        self._queue_bar_compact = bool(compact)
        widgets = (
            self._queue_progress_label,
            self._queue_progress_bar,
            self._queue_sweep_label,
            self._sweep_progress,
        )
        if compact:
            for widget in widgets:
                self._queue_primary_layout.removeWidget(widget)
            self._queue_progress_layout.addWidget(self._queue_progress_label)
            self._queue_progress_layout.addWidget(self._queue_progress_bar, 1)
            self._queue_progress_layout.addWidget(self._queue_sweep_label)
            self._queue_progress_layout.addWidget(self._sweep_progress, 1)
            self._queue_progress_widget.setVisible(True)
            return
        for widget in widgets:
            self._queue_progress_layout.removeWidget(widget)
        self._queue_primary_layout.addWidget(self._queue_progress_label)
        self._queue_primary_layout.addWidget(self._queue_progress_bar, 1)
        self._queue_primary_layout.addWidget(self._queue_sweep_label)
        self._queue_primary_layout.addWidget(self._sweep_progress, 1)
        self._queue_progress_widget.setVisible(False)

    def _make_collapsible_section(
        self,
        title: str,
        content_widget: QWidget,
        collapsed: bool = False,
    ) -> QWidget:
        if isinstance(content_widget, QGroupBox):
            content_widget.setTitle("")
        section = QWidget()
        section.setProperty("layoutRole", "transparent")
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(6)

        toggle = QToolButton()
        toggle.setObjectName("section_toggle")
        toggle.setText(title)
        toggle.setCheckable(True)
        toggle.setChecked(not collapsed)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(
            Qt.ArrowType.DownArrow if not collapsed else Qt.ArrowType.RightArrow
        )
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        container = QWidget()
        container.setProperty("layoutRole", "transparent")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        container_layout.addWidget(content_widget)
        full_height = max(1, content_widget.sizeHint().height())
        container.setMaximumHeight(full_height if not collapsed else 0)
        container.setVisible(not collapsed)

        anim = QPropertyAnimation(container, b"maximumHeight", section)
        anim.setDuration(160)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        def on_toggle(checked: bool) -> None:
            toggle.setArrowType(
                Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
            )
            anim.stop()
            target = max(1, content_widget.sizeHint().height())
            if checked:
                container.setVisible(True)
                anim.setStartValue(container.maximumHeight())
                anim.setEndValue(target)
            else:
                anim.setStartValue(container.maximumHeight())
                anim.setEndValue(0)
            anim.start()

        toggle.toggled.connect(on_toggle)
        def on_finished() -> None:
            if toggle.isChecked():
                container.setMaximumHeight(max(1, content_widget.sizeHint().height()))
            else:
                container.setVisible(False)
        anim.finished.connect(on_finished)

        section_layout.addWidget(toggle)
        section_layout.addWidget(container)
        return section

    def _build_update_indicator(self) -> None:
        self._update_button = QPushButton("Update")
        self._update_button.setObjectName("btn_update")
        self._update_button.setVisible(False)
        self._update_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_button.setToolTip("A new app version is available.")
        self._update_button.clicked.connect(self._open_update_url)
        self._statusbar.addPermanentWidget(self._update_button)
        self._pending_update_url: Optional[str] = None
        self._update_check_thread: Optional[QThread] = None

    def _current_output_device(self) -> Optional[int]:
        value = self._out_dev_combo.currentData()
        return int(value) if value is not None else None

    def _current_input_device(self) -> Optional[int]:
        value = self._in_dev_combo.currentData()
        return int(value) if value is not None else None

    def _current_output_device_info(self) -> Optional[dict]:
        index = self._current_output_device()
        if index is None:
            return None
        return self._output_devices_by_index.get(index)

    def _current_input_device_info(self) -> Optional[dict]:
        index = self._current_input_device()
        if index is None:
            return None
        return self._input_devices_by_index.get(index)

    def _current_output_device_label(self) -> str:
        index = self._current_output_device()
        if index is None:
            return ""
        return self._output_device_labels_by_index.get(index, str(index))

    def _current_input_device_label(self) -> str:
        index = self._current_input_device()
        if index is None:
            return ""
        return self._input_device_labels_by_index.get(index, str(index))

    def _current_output_device_setting(self) -> Optional[dict]:
        device = self._current_output_device_info()
        return device_setting(device, "output") if device is not None else None

    def _current_input_device_setting(self) -> Optional[dict]:
        device = self._current_input_device_info()
        return device_setting(device, "input") if device is not None else None

    def _use_advanced_windows_drivers(self) -> bool:
        toggle = getattr(self, "_advanced_windows_drivers_toggle", None)
        if toggle is not None:
            return bool(toggle.isChecked())
        return bool(self._settings.get("windows_advanced_audio_drivers"))

    def _selected_audio_pair_is_compatible(self) -> bool:
        return is_compatible_device_pair(
            self._current_input_device_info(),
            self._current_output_device_info(),
        )

    def _windows_audio_pair_message(self) -> str:
        in_label = self._current_input_device_label() or "selected input"
        out_label = self._current_output_device_label() or "selected output"
        return (
            "On Windows, input and output must use the same audio driver backend "
            f"for stable timing.\n\nInput: {in_label}\nOutput: {out_label}"
        )

    def _matching_output_for_input(self, input_device: Optional[dict]) -> Optional[dict]:
        if input_device is None:
            return None
        hostapi = int(input_device.get("hostapi", -1))
        for device in self._output_devices_by_index.values():
            if int(device.get("hostapi", -1)) == hostapi:
                return device
        return None

    def _sync_windows_output_to_input(self, *, show_status: bool = True) -> None:
        if not is_windows_audio_host():
            return
        input_device = self._current_input_device_info()
        output_device = self._current_output_device_info()
        if input_device is None:
            return
        if is_compatible_device_pair(input_device, output_device):
            return
        matched_output = self._matching_output_for_input(input_device)
        if matched_output is not None:
            idx = self._out_dev_combo.findData(int(matched_output["index"]))
            if idx >= 0:
                self._out_dev_combo.setCurrentIndex(idx)
                self._settings.set("output_device", self._current_output_device_setting())
                if show_status:
                    self._statusbar.showMessage(
                        "Matched Windows input/output to the same audio driver backend."
                    )
                return
        self._out_dev_combo.setCurrentIndex(-1)
        self._settings.set("output_device", None)
        if show_status:
            self._statusbar.showMessage(
                "No matching Windows output backend found for the selected input."
            )

    def _sweep_latency_mode(self) -> str:
        configured = str(self._settings.get("latency"))
        if bool(self._settings.get("bluetooth_headphone_mode")):
            return configured
        if is_windows_audio_host() and not bool(self._settings.get("latency_user_override")):
            return "high"
        return configured

    def _current_input_channel(self) -> int:
        value = self._ch_combo.currentData()
        return int(value) if value is not None else 0

    def _queue_active(self) -> bool:
        return self._queue_target > 0

    def _is_hrtf_active(self) -> bool:
        return (
            self._hrtf is not None
            and self._hrtf_toggle.isChecked()
        )

    def _restore_hrtf_state(self) -> None:
        self._refresh_hrtf_options()
        path = self._settings.get("hrtf_path")

        if path:
            built_in_paths = self._built_in_hrtf_paths()
            try:
                resolved_path = str(Path(path).resolve())
            except Exception:
                resolved_path = ""
            if resolved_path not in built_in_paths:
                self._hrtf = None
                self._settings.set("hrtf_path", None)
            else:
                try:
                    self._hrtf = HRTFCurve(path)
                except Exception:
                    self._hrtf = None
                    self._settings.set("hrtf_path", None)

        self._sync_hrtf_ui()

    def _refresh_hrtf_options(self) -> None:
        self._hrtf_options = [("None", "")]
        for path in sorted(HRTF_DIR.glob("*.txt")):
            self._hrtf_options.append((path.stem, str(path)))

        if not hasattr(self, "_hrtf_combo"):
            return

        current_path = self._hrtf.path if self._hrtf is not None else ""
        self._hrtf_combo.blockSignals(True)
        self._hrtf_combo.clear()
        for label, value in self._hrtf_options:
            self._hrtf_combo.addItem(label, value)
        index = self._hrtf_combo.findData(current_path)
        self._hrtf_combo.setCurrentIndex(index if index >= 0 else 0)
        self._hrtf_combo.blockSignals(False)

    def _built_in_hrtf_paths(self) -> set[str]:
        paths: set[str] = set()
        for _label, value in self._hrtf_options:
            if not value:
                continue
            try:
                paths.add(str(Path(value).resolve()))
            except Exception:
                continue
        return paths

    def _sync_hrtf_ui(self) -> None:
        has_hrtf = self._hrtf is not None
        self._hrtf_toggle.setEnabled(has_hrtf)

        if has_hrtf:
            self._hrtf_label.setText(Path(self._hrtf.path).stem)
            self._hrtf_label.setToolTip(self._hrtf.path)
            index = self._hrtf_combo.findData(self._hrtf.path)
        else:
            self._hrtf_label.setText("None")
            self._hrtf_label.setToolTip("")
            self._hrtf_toggle.setChecked(False)
            index = 0

        self._hrtf_combo.blockSignals(True)
        self._hrtf_combo.setCurrentIndex(index if index >= 0 else 0)
        self._hrtf_combo.blockSignals(False)

    def _refresh_devices(self) -> None:
        selected_out = (
            self._current_output_device_setting()
            if self._current_output_device() is not None
            else self._settings.get("output_device")
        )
        selected_in = (
            self._current_input_device_setting()
            if self._current_input_device() is not None
            else self._settings.get("input_device")
        )
        selected_ch = self._current_input_channel()

        all_out_devices = get_output_devices()
        all_in_devices = get_input_devices()
        preferred_hostapi = (
            preferred_windows_hostapi(all_in_devices, all_out_devices)
            if is_windows_audio_host() and not self._use_advanced_windows_drivers()
            else None
        )
        out_devices = filter_devices_by_hostapi(all_out_devices, preferred_hostapi)
        in_devices = filter_devices_by_hostapi(all_in_devices, preferred_hostapi)

        out_signature = [
            (int(d["index"]), str(d["name"]), int(d.get("hostapi", -1)))
            for d in all_out_devices
        ]
        in_signature = [
            (int(d["index"]), str(d["name"]), int(d.get("hostapi", -1)))
            for d in all_in_devices
        ]
        out_duplicates = duplicate_device_names(out_devices)
        in_duplicates = duplicate_device_names(in_devices)

        self._out_dev_combo.blockSignals(True)
        self._in_dev_combo.blockSignals(True)
        self._ch_combo.blockSignals(True)

        self._output_devices_by_index = {int(d["index"]): d for d in out_devices}
        self._input_devices_by_index = {int(d["index"]): d for d in in_devices}
        self._output_device_labels_by_index = {
            int(d["index"]): device_label(d, out_duplicates)
            for d in out_devices
        }
        self._input_device_labels_by_index = {
            int(d["index"]): device_label(d, in_duplicates)
            for d in in_devices
        }

        self._out_dev_combo.clear()
        for d in out_devices:
            self._out_dev_combo.addItem(
                self._output_device_labels_by_index[int(d["index"])],
                int(d["index"]),
            )

        self._in_dev_combo.clear()
        for d in in_devices:
            self._in_dev_combo.addItem(
                self._input_device_labels_by_index[int(d["index"])],
                int(d["index"]),
            )

        selected_out_device, out_ambiguous = resolve_device_selection(
            selected_out,
            "output",
            out_devices,
        )
        selected_in_device, in_ambiguous = resolve_device_selection(
            selected_in,
            "input",
            in_devices,
        )

        if out_devices:
            if selected_out_device is not None:
                self._out_dev_combo.setCurrentIndex(
                    self._out_dev_combo.findData(int(selected_out_device["index"]))
                )
            elif out_ambiguous:
                self._out_dev_combo.setCurrentIndex(-1)
            else:
                self._out_dev_combo.setCurrentIndex(0)

        if in_devices:
            if selected_in_device is not None:
                self._in_dev_combo.setCurrentIndex(
                    self._in_dev_combo.findData(int(selected_in_device["index"]))
                )
            elif in_ambiguous:
                self._in_dev_combo.setCurrentIndex(-1)
            else:
                self._in_dev_combo.setCurrentIndex(0)

        self._out_dev_combo.blockSignals(False)
        self._in_dev_combo.blockSignals(False)

        self._sync_windows_output_to_input(show_status=False)

        self._refresh_channels(selected_ch=selected_ch)
        self._ch_combo.blockSignals(False)

        current_out = self._current_output_device()
        current_in = self._current_input_device()

        self._settings.set("output_device", self._current_output_device_setting())
        self._settings.set("input_device", self._current_input_device_setting())

        self._last_output_devices = out_signature
        self._last_input_devices = in_signature
        if hasattr(self, "_log_event"):
            self._log_event(
                "INFO",
                "devices",
                "Audio devices refreshed",
                output_count=len(out_devices),
                input_count=len(in_devices),
                selected_output=self._current_output_device_label(),
                selected_input=self._current_input_device_label(),
                input_channel=self._current_input_channel() + 1,
            )

        if out_ambiguous or in_ambiguous:
            self._statusbar.showMessage(
                "Saved audio device name is ambiguous. Select the desired host API once."
            )
        elif is_windows_audio_host() and preferred_hostapi is not None:
            self._statusbar.showMessage(
                "Windows audio set to matched driver backend for stable timing."
            )

        self._apply_state_ui()
        self._start_level_monitor()
        if hasattr(self, "_refresh_session_labels"):
            self._refresh_session_labels()

    def _manual_refresh_devices(self) -> None:
        previous_out = self._current_output_device()
        previous_in = self._current_input_device()
        previous_ch = self._current_input_channel()

        getattr(self, "_stop_channel_balance", lambda: None)()
        self._level_monitor.stop()
        dual_monitor = getattr(self, "_dual_level_monitor", None)
        if dual_monitor is not None:
            dual_monitor.stop()
        refresh_audio_backend()
        self._refresh_devices()

        current_out = self._current_output_device()
        current_in = self._current_input_device()
        current_ch = self._current_input_channel()
        if (
            previous_out == current_out
            and previous_in == current_in
            and previous_ch == current_ch
        ):
            self._statusbar.showMessage("Audio devices refreshed.")
        else:
            self._statusbar.showMessage("Audio devices refreshed; selection changed.")

    def _refresh_channels(self, selected_ch: Optional[int] = None) -> None:
        input_device = self._current_input_device()
        count = (
            device_channel_count(input_device, "input")
            if input_device is not None
            else 0
        )

        self._ch_combo.clear()
        for idx in range(count):
            self._ch_combo.addItem(f"Ch {idx + 1}", idx)

        want_ch = (
            selected_ch
            if selected_ch is not None
            else int(self._settings.get("input_channel") or 0)
        )

        if count > 0:
            want_ch = max(0, min(want_ch, count - 1))
            self._ch_combo.setCurrentIndex(want_ch)
            self._settings.set("input_channel", want_ch)
            self._active_ch_label.setText(
                f"Active input channel: Ch {want_ch + 1}"
            )
        else:
            self._active_ch_label.setText("Active input channel: —")
        if hasattr(self, "_rnd_widget"):
            self._rnd_widget.set_input_channels(
                [
                    (self._ch_combo.itemText(index), int(self._ch_combo.itemData(index)))
                    for index in range(self._ch_combo.count())
                ],
                self._current_input_channel(),
            )

    def _check_devices(self) -> None:
        current_out = [
            (int(d["index"]), str(d["name"]), int(d.get("hostapi", -1)))
            for d in get_output_devices()
        ]
        current_in = [
            (int(d["index"]), str(d["name"]), int(d.get("hostapi", -1)))
            for d in get_input_devices()
        ]

        if current_out == self._last_output_devices and current_in == (
            self._last_input_devices
        ):
            return

        getattr(self, "_stop_channel_balance", lambda: None)()

        selected_out = self._current_output_device()
        selected_in = self._current_input_device()

        if (
            self._state == AppState.SWEEPING
            and (
                selected_out not in {idx for idx, _name, _hostapi in current_out}
                or selected_in not in {idx for idx, _name, _hostapi in current_in}
            )
        ):
            self._abort_active_sweep()
            self._state = AppState.IDLE
            self._statusbar.showMessage(
                "Audio device change detected. Active sweep aborted safely."
            )

        self._refresh_devices()

    def _abort_active_sweep(self) -> None:
        if self._sweep_thread is not None and self._sweep_thread.isRunning():
            try:
                self._sweep_thread.abort()
            except Exception:
                pass

    def _cleanup_sweep_thread(self) -> None:
        if self._sweep_thread is not None:
            self._sweep_thread.deleteLater()
            self._sweep_thread = None
        if self._active_sweep_worker is not None:
            self._active_sweep_worker.deleteLater()
            self._active_sweep_worker = None

    def _start_level_monitor(self) -> None:
        self._level_monitor.stop()
        self._dual_level_monitor.stop()

        if self._state == AppState.SWEEPING or self._channel_balance_active:
            return

        input_device = self._current_input_device()
        if input_device is None:
            self._displayed_level_dbfs = -60.0
            self._level_meter.set_level(-60.0)
            self._level_status_label.setText("No input")
            return

        try:
            if self._two_channel_enabled:
                if not self._two_channel_devices_ready():
                    self._level_status_label.setText("Two inputs needed")
                    self._level_status_label_2.setText("R")
                    return
                self._dual_level_monitor.start(
                    device_index=input_device,
                    device_label=self._current_input_device_label(),
                    fs=int(self._settings.get("sample_rate")),
                    buffer_size=int(self._settings.get("buffer_size")),
                )
                self._level_status_label.setText("L")
                self._level_status_label_2.setText("R")
                return
            self._level_monitor.start(
                device_index=input_device,
                device_label=self._current_input_device_label(),
                channel_index=self._current_input_channel(),
                fs=int(self._settings.get("sample_rate")),
                buffer_size=int(self._settings.get("buffer_size")),
            )
            self._level_status_label.setText("RMS")
        except Exception as exc:
            self._statusbar.showMessage(f"Level monitor start failed: {exc}")

    def _on_output_device_changed(self) -> None:
        self._stop_channel_balance()
        self._settings.set("output_device", self._current_output_device_setting())
        self._refresh_session_labels()
        self._apply_state_ui()
        if (
            is_windows_audio_host()
            and self._current_output_device() is not None
            and self._current_input_device() is not None
            and not self._selected_audio_pair_is_compatible()
        ):
            self._statusbar.showMessage(
                "Windows input/output driver backends do not match."
            )

    def _on_input_device_changed(self) -> None:
        self._stop_channel_balance()
        self._settings.set("input_device", self._current_input_device_setting())
        self._sync_windows_output_to_input()
        self._refresh_channels()
        self._start_level_monitor()
        self._apply_state_ui()
        self._refresh_session_labels()

    def _on_advanced_windows_drivers_changed(self) -> None:
        enabled = self._use_advanced_windows_drivers()
        self._settings.set("windows_advanced_audio_drivers", enabled)
        self._refresh_devices()
        if enabled:
            self._statusbar.showMessage(
                "Advanced Windows drivers visible. Keep input/output on the same backend."
            )
        else:
            self._statusbar.showMessage(
                "Advanced Windows drivers hidden. Using the preferred matched backend."
            )

    def _on_channel_changed(self) -> None:
        self._settings.set("input_channel", self._current_input_channel())
        self._active_ch_label.setText(
            f"Active input channel: Ch {self._current_input_channel() + 1}"
        )
        if hasattr(self, "_rnd_widget"):
            self._rnd_widget.set_input_channel(self._current_input_channel())
        self._start_level_monitor()
        self._refresh_session_labels()

    def _on_rnd_input_channel_changed(self, channel: int) -> None:
        index = self._ch_combo.findData(int(channel))
        if index >= 0 and index != self._ch_combo.currentIndex():
            self._ch_combo.setCurrentIndex(index)

    def _on_level_update(self, dbfs: float) -> None:
        self._last_level_dbfs = float(dbfs)

    def _on_dual_level_update(self, left_dbfs: float, right_dbfs: float) -> None:
        self._last_dual_levels = (float(left_dbfs), float(right_dbfs))
        self._last_level_dbfs = max(self._last_dual_levels)

    def _refresh_level_meter_display(self) -> None:
        if getattr(self, "_two_channel_enabled", False):
            left_db, right_db = self._last_dual_levels
            self._level_meter.set_level(max(-60.0, min(0.0, left_db)))
            self._level_meter_2.set_level(max(-60.0, min(0.0, right_db)))
            return
        target_db = max(-60.0, min(0.0, self._last_level_dbfs))
        self._displayed_level_dbfs = (
            self._displayed_level_dbfs * 0.5
            + target_db * 0.5
        )
        if abs(self._displayed_level_dbfs - target_db) < 0.2:
            self._displayed_level_dbfs = target_db
        self._level_meter.set_level(self._displayed_level_dbfs)

    def _on_level_error(self, message: str) -> None:
        self._statusbar.showMessage(message)

    def _start_update_check(self) -> None:
        enabled = bool(self._settings.get("update_check_enabled"))
        feed_url = str(self._settings.get("update_feed_url") or "").strip()
        if not enabled or not feed_url:
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

    def _on_update_available(
        self,
        latest_version: str,
        release_url: str,
        summary: str,
    ) -> None:
        self._pending_update_url = release_url
        self._update_button.setVisible(True)
        summary_text = f" - {summary}" if summary else ""
        self._update_button.setToolTip(
            f"v{latest_version} is available{summary_text}"
        )
        self._statusbar.showMessage(
            f"Update available: v{latest_version}. Click 'Update' to open release notes."
        )
        self._log_event("INFO", "update", "Update available", version=latest_version)

    def _on_update_up_to_date(self, _latest_version: str) -> None:
        self._pending_update_url = None
        self._update_button.setVisible(False)
        self._log_event("DEBUG", "update", "Application is up to date", version=_latest_version)

    def _on_update_check_failed(self, _error: str) -> None:
        # Keep this fully non-intrusive by silently failing.
        self._pending_update_url = None
        self._update_button.setVisible(False)
        self._log_event("WARNING", "update", "Update check failed", error=_error)

    def _open_update_url(self) -> None:
        if not self._pending_update_url:
            return
        QDesktopServices.openUrl(QUrl(self._pending_update_url))

    def _apply_state_ui(self) -> None:
        idle = self._state == AppState.IDLE
        pass_fail = self._state == AppState.PASS_FAIL
        busy = self._state in {AppState.SWEEPING, AppState.QUEUE_RUNNING}
        balance_mode = self._channel_balance_mode_active()

        single_device_ok = (
            self._current_output_device() is not None
            and self._current_input_device() is not None
            and self._ch_combo.count() > 0
            and self._selected_audio_pair_is_compatible()
        )
        device_ok = (
            self._two_channel_devices_ready()
            if self._two_channel_enabled
            else single_device_ok
        )

        for widget in (
            self._out_dev_combo,
            self._in_dev_combo,
            self._ch_combo,
            self._queue_n_spin,
            self._queue_level_spin,
            self._queue_level_persist_toggle,
            self._queue_level_persist_label,
            self._bluetooth_mode_toggle,
            self._variation_toggle,
            self._hrtf_combo,
            self._undo_btn,
            self._clear_btn,
            self._metadata_btn,
            self._clear_metadata_btn,
            self._advanced_windows_drivers_toggle,
            self._refresh_devices_btn,
        ):
            widget.setEnabled(idle)

        self._two_channel_toggle.setEnabled(idle)
        self._measure_submode_control.setEnabled(idle)
        self._bottom_layout_combo.setEnabled(idle)
        self._ch_combo.setEnabled(idle and not self._two_channel_enabled)

        self._hrtf_toggle.setEnabled(idle and self._hrtf is not None)
        self._settings_widget.set_editing_enabled(idle)
        if not idle:
            self._close_metadata_overlay()
        if hasattr(self, "_rnd_widget"):
            self._rnd_widget.set_busy(not idle)
        self._start_queue_btn.setEnabled(idle and device_ok and not balance_mode)
        self._cancel_queue_btn.setEnabled(busy or pass_fail)
        active_count = (
            len(self._two_channel_pairs)
            if self._two_channel_enabled
            else len(self._kept_curves)
        )
        self._undo_btn.setEnabled(idle and active_count > 0)
        has_measurements = (
            bool(self._two_channel_pairs) or self._pending_pair is not None
            if self._two_channel_enabled
            else bool(self._kept_curves) or self._pending_curve is not None
        )
        self._clear_btn.setEnabled(idle and has_measurements)
        self._sync_export_button()

    def _start_queue(self) -> None:
        if self._state != AppState.IDLE:
            return

        if self._current_output_device() is None:
            QMessageBox.warning(self, "No Output Device", "Select an output device.")
            return

        if self._current_input_device() is None:
            QMessageBox.warning(self, "No Input Device", "Select an input device.")
            return

        if not self._selected_audio_pair_is_compatible():
            QMessageBox.warning(
                self,
                "Windows Audio Driver Mismatch",
                self._windows_audio_pair_message(),
            )
            self._statusbar.showMessage(
                "Queue start blocked: Windows input/output driver backends do not match."
            )
            return

        if self._ch_combo.count() == 0:
            QMessageBox.warning(
                self,
                "No Input Channel",
                "Selected input device has no available input channels.",
            )
            return

        if self._two_channel_enabled and not self._two_channel_devices_ready():
            QMessageBox.warning(
                self,
                "Two Channels Required",
                "Two Channel measurement needs an input device and an output device with at least two channels.",
            )
            return

        ambient_dbfs = float(self._last_level_dbfs)
        if ambient_dbfs > _QUEUE_AMBIENT_WARN_DBFS:
            choice = QMessageBox.question(
                self,
                "Ambient Level Warning",
                "Current ambient/input RMS looks high before queue start:\n"
                f"{ambient_dbfs:.1f} dBFS (warning threshold: {_QUEUE_AMBIENT_WARN_DBFS:.1f} dBFS).\n\n"
                "This can reduce measurement SNR.\n"
                "Start queue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                self._statusbar.showMessage("Queue start canceled due to high ambient level.")
                return

        self._queue_target = int(self._queue_n_spin.value())
        self._queue_index = 0
        self._current_sweep_attempts = 0
        overrides = self._settings.session_overrides() if hasattr(self._settings, "session_overrides") else {}
        if "queue_count" not in overrides:
            self._settings.set("queue_count", self._queue_target)

        self._queue_progress_bar.setRange(0, max(1, self._queue_target))
        self._queue_progress_bar.setValue(0)
        kept_count = (
            len(self._two_channel_pairs)
            if self._two_channel_enabled
            else len(self._kept_curves)
        )
        self._queue_progress_label.setText(
            f"Kept: {kept_count}"
        )

        self._state = AppState.QUEUE_RUNNING
        self._apply_state_ui()
        self._statusbar.showMessage("Queue started.")
        self._start_next_sweep()

    def _start_next_sweep(self, *, second_stage: bool = False) -> None:
        if not self._queue_active():
            self._state = AppState.IDLE
            self._apply_state_ui()
            return

        if self._queue_index >= self._queue_target:
            self._finish_queue()
            return

        if second_stage:
            self._two_channel_stage = 2
        else:
            self._current_sweep_attempts += 1
            if self._two_channel_enabled:
                self._two_channel_stage = 1
                self._pending_pair = None
                self._pending_pair_first_raw = None
                self._pending_pair_first_diagnostics = None
        self._state = AppState.SWEEPING
        self._apply_state_ui()
        self._sweep_progress.setValue(0)

        output_device = self._current_output_device()
        input_device = self._current_input_device()
        input_channel = (
            self._two_channel_stage - 1
            if self._two_channel_enabled
            else self._current_input_channel()
        )
        output_channel = input_channel if self._two_channel_enabled else None

        if output_device is None or input_device is None:
            self._on_sweep_error("Selected device is unavailable.")
            return

        if not self._selected_audio_pair_is_compatible():
            self._on_sweep_error(self._windows_audio_pair_message())
            return

        self._level_monitor.stop()
        self._dual_level_monitor.stop()
        self._last_timing_quality = None
        self._last_measurement_diagnostics = None

        sweep = generate_log_sweep(
            duration=float(self._settings.get("sweep_duration")),
            fs=int(self._settings.get("sample_rate")),
            f_low=_MEASUREMENT_F_MIN,
            f_high=_MEASUREMENT_F_MAX,
        )
        output_level_db = float(self._queue_level_spin.value())
        output_gain = 10.0 ** (output_level_db / 20.0)
        sweep = (sweep * output_gain).astype(np.float32, copy=False)

        worker = SweepWorker()
        worker.finished.connect(self._on_sweep_finished)
        worker.error.connect(self._on_sweep_error)
        worker.progress.connect(self._on_sweep_progress)
        worker.timing_quality.connect(self._on_timing_quality)
        worker.measurement_diagnostics.connect(self._on_measurement_diagnostics)

        self._active_sweep_worker = worker
        self._sweep_thread = _SweepThread(
            worker,
            sweep=sweep,
            output_device=output_device,
            input_device=input_device,
            output_device_label=self._current_output_device_label(),
            input_device_label=self._current_input_device_label(),
            input_channel=input_channel,
            output_channel=output_channel,
            fs=int(self._settings.get("sample_rate")),
            buffer_size=int(self._settings.get("buffer_size")),
            pre_silence=float(self._settings.get("pre_sweep_silence")),
            post_silence=float(self._settings.get("post_sweep_silence")),
            latency=self._sweep_latency_mode(),
            bluetooth_headphone_mode=bool(
                self._settings.get("bluetooth_headphone_mode")
            ),
            start_alignment_confidence_min=float(
                self._settings.get("start_alignment_confidence_min")
            ),
            end_marker_confidence_min=float(
                self._settings.get("end_marker_confidence_min")
            ),
            timing_drift_max_ms=float(self._settings.get("timing_drift_max_ms")),
        )
        self._sweep_thread.finished.connect(self._on_sweep_thread_finished)
        self._sweep_thread.start()

        channel_text = (
            f", channel {self._two_channel_stage}"
            if self._two_channel_enabled
            else ""
        )
        self._statusbar.showMessage(
            f"Sweeping {self._queue_index + 1}/{self._queue_target}{channel_text} "
            f"(attempt {self._current_sweep_attempts})..."
        )
        self._log_event(
            "INFO", "measurement", "Sweep started",
            index=self._queue_index + 1,
            total=self._queue_target,
            attempt=self._current_sweep_attempts,
            sample_rate=int(self._settings.get("sample_rate")),
            buffer_size=int(self._settings.get("buffer_size")),
            output_level_db=float(self._queue_level_spin.value()),
            input_channel=input_channel + 1,
            output_channel=(output_channel + 1) if output_channel is not None else None,
        )

    def _start_second_two_channel_sweep(self) -> None:
        if self._queue_active() and self._pending_pair_first_raw is not None:
            self._start_next_sweep(second_stage=True)

    def _on_sweep_progress(self, frac: float) -> None:
        self._sweep_progress.setValue(int(max(0.0, min(1.0, frac)) * 100.0))

    def _on_timing_quality(
        self, start_conf: float, end_conf: float, drift_ms: float, snr_db: float
    ) -> None:
        self._last_timing_quality = (start_conf, end_conf, drift_ms, snr_db)

    def _on_measurement_diagnostics(self, diagnostics: object) -> None:
        self._last_measurement_diagnostics = diagnostics
        details = {
            name: getattr(diagnostics, name)
            for name in (
                "start_confidence", "marker_confidence", "timing_error_ms", "snr_db",
                "failure_reason", "warning_reason", "bluetooth_headphone_mode", "buffer_size",
            )
            if hasattr(diagnostics, name)
        }
        self._log_event("INFO", "diagnostics", "Measurement diagnostics received", **details)

    def _on_sweep_finished(self, recording: np.ndarray, sweep: np.ndarray) -> None:
        try:
            freqs, mag_db = compute_frequency_response(
                recording=recording,
                sweep=sweep,
                fs=int(self._settings.get("sample_rate")),
                f_low=_MEASUREMENT_F_MIN,
                f_high=_MEASUREMENT_F_MAX,
            )
            if self._two_channel_enabled:
                if self._two_channel_stage == 1:
                    self._pending_pair_first_raw = (freqs, mag_db)
                    self._pending_pair_first_diagnostics = self._last_measurement_diagnostics
                    self._start_second_pair_stage = True
                    self._state = AppState.QUEUE_RUNNING
                    self._apply_state_ui()
                    self._statusbar.showMessage(
                        "Channel 1/L complete. Starting channel 2/R."
                    )
                    return
                if self._two_channel_stage != 2 or self._pending_pair_first_raw is None:
                    raise ValueError("The first channel result is unavailable.")
                first_freqs, first_mag = self._pending_pair_first_raw
                first_norm, second_norm = shared_normalize_pair_at_1khz(
                    first_freqs,
                    first_mag,
                    freqs,
                    mag_db,
                    f_ref=1000.0,
                )
                first_ds = downsample_to_log_points(
                    first_freqs,
                    first_norm,
                    n_points=600,
                    f_ref=1000.0,
                    normalize_ref=False,
                )
                second_ds = downsample_to_log_points(
                    freqs,
                    second_norm,
                    n_points=600,
                    f_ref=1000.0,
                    normalize_ref=False,
                )
                self._pending_pair = TwoChannelCurvePair(
                    channel_1=first_ds,
                    channel_2=second_ds,
                    channel_1_diagnostics=self._pending_pair_first_diagnostics,
                    channel_2_diagnostics=self._last_measurement_diagnostics,
                )
                self._state = AppState.PASS_FAIL
                self._apply_state_ui()
                self._update_plots(show_pending=True)
                self._statusbar.showMessage(
                    "Two-channel pair complete. Waiting for review."
                )
                QTimer.singleShot(0, self._show_pass_fail_dialog)
                return
            mag_db = normalize_at_1khz(freqs, mag_db, f_ref=1000.0)

            freqs_ds, mag_ds = downsample_to_log_points(
                freqs,
                mag_db,
                n_points=600,
                f_ref=1000.0,
                normalize_ref=True,
            )

            self._pending_curve = (freqs_ds, mag_ds)
            self._log_event(
                "INFO", "processing", "Frequency response processed",
                input_points=len(freqs), output_points=len(freqs_ds),
            )
            self._state = AppState.PASS_FAIL
            self._apply_state_ui()
            self._update_plots(show_pending=True)
            timing_msg = ""
            if self._last_timing_quality is not None:
                start_conf, end_conf, drift_ms, snr_db = self._last_timing_quality
                bluetooth_mode = bool(
                    getattr(
                        self._last_measurement_diagnostics,
                        "bluetooth_headphone_mode",
                        False,
                    )
                )
                warning_prefix = ""
                warning_message = None
                if self._last_measurement_diagnostics is not None:
                    warning_message = getattr(
                        self._last_measurement_diagnostics,
                        "warning_message",
                        None,
                    )
                if warning_message:
                    warning_prefix = " Bluetooth timing marginal."
                if bluetooth_mode:
                    timing_msg = (
                        f" Timing Quality: start {start_conf:.1f}, "
                        f"end {end_conf:.1f}, drift {drift_ms:.1f} ms, "
                        f"SNR {snr_db:.1f} dB.{warning_prefix}"
                    )
                else:
                    timing_msg = (
                        f" Sweep Quality: alignment {start_conf:.1f}, "
                        f"SNR {snr_db:.1f} dB."
                    )
            self._statusbar.showMessage(f"Sweep complete. Waiting for review.{timing_msg}")
            QTimer.singleShot(0, self._show_pass_fail_dialog)
        except Exception as exc:
            self._on_sweep_error(f"Processing error: {exc}")

    def _on_sweep_error(self, message: str) -> None:
        self._log_event("ERROR", "measurement", message)
        self._cleanup_sweep_thread()
        self._close_pass_fail_dialog()
        self._pending_curve = None
        self._pending_pair = None
        self._pending_pair_first_raw = None
        self._pending_pair_first_diagnostics = None
        self._start_second_pair_stage = False
        self._two_channel_stage = 0
        self._last_timing_quality = None
        self._sweep_progress.setValue(0)

        failure_reason = None
        if self._last_measurement_diagnostics is not None:
            failure_reason = getattr(
                self._last_measurement_diagnostics, "failure_reason", None
            )
        is_timing_quality_error = is_retryable_timing_failure(
            message=message,
            failure_reason=failure_reason,
        )
        retry_complete_pair = bool(
            getattr(self, "_two_channel_enabled", False) and self._queue_active()
        )
        if (
            self._queue_active()
            and (is_timing_quality_error or retry_complete_pair)
            and self._current_sweep_attempts < _MAX_SWEEP_ATTEMPTS
        ):
            diagnostics_text = ""
            if (
                self._last_measurement_diagnostics is not None
                and getattr(
                    self._last_measurement_diagnostics, "failure_reason", None
                ) is not None
            ):
                diagnostics_text = (
                    "\n\n"
                    + format_diagnostics_summary(
                        self._last_measurement_diagnostics
                    )
                )
            self._state = AppState.QUEUE_RUNNING
            self._apply_state_ui()
            self._start_level_monitor()
            retry_subject = (
                "The two-channel pair failed. Both channels will be measured again."
                if retry_complete_pair
                else f"Measurement {self._queue_index + 1} did not meet timing quality."
            )
            retry_msg = (
                f"{message}\n\n{retry_subject}\n"
                f"Retry attempt {self._current_sweep_attempts + 1} of {_MAX_SWEEP_ATTEMPTS}?"
                f"{diagnostics_text}"
            )
            choice = QMessageBox.question(
                self,
                "Two-Channel Pair Retry" if retry_complete_pair else "Timing Quality Retry",
                retry_msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if choice == QMessageBox.StandardButton.Yes:
                self._statusbar.showMessage(
                    f"{message} Retrying measurement {self._queue_index + 1} "
                    f"({self._current_sweep_attempts}/{_MAX_SWEEP_ATTEMPTS})..."
                )
                QTimer.singleShot(150, self._start_next_sweep)
                return
            self._cancel_queue()
            cancel_reason = (
                "two-channel pair retry"
                if retry_complete_pair
                else "timing-quality retry"
            )
            self._statusbar.showMessage(
                f"Queue canceled by user after {cancel_reason} prompt."
            )
            return

        self._state = AppState.IDLE

        self._apply_state_ui()
        self._start_level_monitor()
        self._statusbar.showMessage(message)
        dialog_message = message
        if (
            self._last_measurement_diagnostics is not None
            and getattr(
                self._last_measurement_diagnostics, "failure_reason", None
            ) is not None
        ):
            dialog_message = (
                f"{message}\n\n"
                f"{format_diagnostics_summary(self._last_measurement_diagnostics)}"
            )
        QMessageBox.warning(self, "Sweep Error", dialog_message)

    def _on_sweep_thread_finished(self) -> None:
        self._cleanup_sweep_thread()
        if self._start_second_pair_stage:
            self._start_second_pair_stage = False
            QTimer.singleShot(0, self._start_second_two_channel_sweep)
            return
        if self._state != AppState.PASS_FAIL:
            self._start_level_monitor()

    def _on_keep(self) -> None:
        if self._state != AppState.PASS_FAIL:
            return

        if self._two_channel_enabled:
            if self._pending_pair is None:
                return
            self._close_pass_fail_dialog()
            self._two_channel_pairs.append(self._pending_pair)
            self._pending_pair = None
            self._pending_pair_first_raw = None
            self._pending_pair_first_diagnostics = None
            self._two_channel_stage = 0
            self._queue_index += 1
            self._current_sweep_attempts = 0
            self._recompute_two_channel_results()
            self._update_queue_progress()
            self._update_plots()
            self._run_automation_trigger("measurement_kept")
            if self._queue_index >= self._queue_target:
                self._finish_queue()
                return
            self._state = AppState.QUEUE_RUNNING
            self._apply_state_ui()
            self._start_next_sweep()
            return

        if self._pending_curve is None:
            return

        self._close_pass_fail_dialog()
        self._kept_curves.append(self._pending_curve)
        self._log_event(
            "INFO", "review", "Measurement kept",
            index=self._queue_index + 1,
            kept_count=len(self._kept_curves),
        )
        self._run_automation_trigger("measurement_kept")
        self._pending_curve = None
        self._pending_pair = None
        self._pending_pair_first_raw = None
        self._pending_pair_first_diagnostics = None
        self._two_channel_stage = 0
        self._queue_index += 1
        self._current_sweep_attempts = 0

        self._recompute_average()
        self._recompute_variation()
        self._update_queue_progress()
        self._update_plots()

        if self._queue_index >= self._queue_target:
            self._finish_queue()
            return

        self._state = AppState.QUEUE_RUNNING
        self._apply_state_ui()
        self._start_next_sweep()

    def _on_fail(self) -> None:
        if self._state != AppState.PASS_FAIL:
            return

        self._close_pass_fail_dialog()
        self._log_event("WARNING", "review", "Measurement rejected", index=self._queue_index + 1)
        self._pending_curve = None
        self._pending_pair = None
        self._pending_pair_first_raw = None
        self._pending_pair_first_diagnostics = None
        self._start_second_pair_stage = False
        self._two_channel_stage = 0
        self._state = AppState.QUEUE_RUNNING
        self._apply_state_ui()
        self._update_plots()
        self._statusbar.showMessage(
            f"Measurement {self._queue_index + 1} failed. Redoing same index."
        )
        self._start_next_sweep()

    def _cancel_queue(self) -> None:
        self._abort_active_sweep()
        self._close_pass_fail_dialog()
        self._pending_curve = None
        self._queue_target = 0
        self._queue_index = 0
        self._current_sweep_attempts = 0
        self._state = AppState.IDLE
        self._sweep_progress.setValue(0)
        self._update_queue_progress()
        self._update_plots()
        self._apply_state_ui()
        self._start_level_monitor()
        self._statusbar.showMessage("Queue canceled.")

    def _finish_queue(self) -> None:
        self._queue_target = 0
        self._current_sweep_attempts = 0
        self._state = AppState.IDLE
        self._sweep_progress.setValue(100)
        self._apply_state_ui()
        self._start_level_monitor()
        self._statusbar.showMessage("Queue complete.")
        self._run_automation_trigger("queue_complete")

    def _update_queue_progress(self) -> None:
        target = max(0, self._queue_target)
        self._queue_progress_bar.setRange(0, max(1, target))
        self._queue_progress_bar.setValue(min(self._queue_index, max(1, target)))
        kept_count = (
            len(self._two_channel_pairs)
            if self._two_channel_enabled
            else len(self._kept_curves)
        )
        self._queue_progress_label.setText(
            f"Kept: {kept_count}"
        )

    def _show_pass_fail_dialog(self) -> None:
        pending_available = (
            self._pending_pair is not None
            if self._two_channel_enabled
            else self._pending_curve is not None
        )
        if self._state != AppState.PASS_FAIL or not pending_available:
            return

        if self._pass_fail_dialog is not None:
            self._pass_fail_dialog.raise_()
            self._pass_fail_dialog.activateWindow()
            return

        dlg = PassFailDialog(
            index=self._queue_index + 1,
            total=max(self._queue_target, self._queue_index + 1),
            timing_quality=self._last_timing_quality,
            diagnostics=self._last_measurement_diagnostics,
            parent=self,
        )
        dlg.adjustSize()
        target_rect = self._plots.bottom_plot_global_rect()
        x = target_rect.center().x() - dlg.width() // 2
        y = target_rect.center().y() - dlg.height() // 2
        x = max(target_rect.left() + 12, min(x, target_rect.right() - dlg.width() - 12))
        y = max(target_rect.top() + 12, min(y, target_rect.bottom() - dlg.height() - 12))
        dlg.move(x, y)
        dlg.finished.connect(lambda _result: self._handle_pass_fail_choice(dlg))
        self._pass_fail_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _handle_pass_fail_choice(self, dlg: PassFailDialog) -> None:
        if self._pass_fail_dialog is dlg:
            self._pass_fail_dialog = None

        choice = dlg.choice()
        if choice == PassFailDialog.KEEP:
            self._on_keep()
        elif choice == PassFailDialog.FAIL:
            self._on_fail()
        else:
            self._cancel_queue()

    def _close_pass_fail_dialog(self) -> None:
        if self._pass_fail_dialog is None:
            return
        dlg = self._pass_fail_dialog
        self._pass_fail_dialog = None
        dlg.blockSignals(True)
        dlg.close()

    def _start_rnd_measurement(self) -> None:
        if self._state != AppState.IDLE:
            return
        if self._current_output_device() is None:
            QMessageBox.warning(self, "No Output Device", "Select an output device.")
            return
        if self._current_input_device() is None:
            QMessageBox.warning(self, "No Input Device", "Select an input device.")
            return
        if not self._selected_audio_pair_is_compatible():
            QMessageBox.warning(
                self,
                "Windows Audio Driver Mismatch",
                self._windows_audio_pair_message(),
            )
            return
        if self._ch_combo.count() == 0:
            QMessageBox.warning(
                self,
                "No Input Channel",
                "Selected input device has no available input channels.",
            )
            return
        ambient_dbfs = float(self._last_level_dbfs)
        if ambient_dbfs > _QUEUE_AMBIENT_WARN_DBFS:
            choice = QMessageBox.question(
                self,
                "Ambient Level Warning",
                "Current ambient/input RMS looks high before R&D measurement:\n"
                f"{ambient_dbfs:.1f} dBFS (warning threshold: {_QUEUE_AMBIENT_WARN_DBFS:.1f} dBFS).\n\n"
                "This can reduce measurement SNR.\n"
                "Start measurement anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                self._statusbar.showMessage("R&D measurement canceled due to high ambient level.")
                return

        self._rnd_sweep_active = True
        self._current_sweep_attempts = 0
        self._start_rnd_sweep()

    def _start_rnd_sweep(self) -> None:
        self._current_sweep_attempts += 1
        self._state = AppState.SWEEPING
        self._apply_state_ui()
        self._rnd_widget.set_status(f"Sweeping attempt {self._current_sweep_attempts}...")
        self._sweep_progress.setValue(0)

        output_device = self._current_output_device()
        input_device = self._current_input_device()
        input_channel = self._current_input_channel()
        if output_device is None or input_device is None:
            self._on_rnd_sweep_error("Selected device is unavailable.")
            return
        if not self._selected_audio_pair_is_compatible():
            self._on_rnd_sweep_error(self._windows_audio_pair_message())
            return

        self._level_monitor.stop()
        self._last_timing_quality = None
        self._last_measurement_diagnostics = None

        sweep = generate_log_sweep(
            duration=float(self._settings.get("sweep_duration")),
            fs=int(self._settings.get("sample_rate")),
            f_low=_MEASUREMENT_F_MIN,
            f_high=_MEASUREMENT_F_MAX,
        )
        output_level_db = float(self._queue_level_spin.value())
        sweep = (sweep * (10.0 ** (output_level_db / 20.0))).astype(np.float32, copy=False)

        worker = SweepWorker()
        worker.finished.connect(self._on_rnd_sweep_finished)
        worker.error.connect(self._on_rnd_sweep_error)
        worker.progress.connect(self._on_sweep_progress)
        worker.timing_quality.connect(self._on_timing_quality)
        worker.measurement_diagnostics.connect(self._on_measurement_diagnostics)
        self._active_sweep_worker = worker
        self._sweep_thread = _SweepThread(
            worker,
            sweep=sweep,
            output_device=output_device,
            input_device=input_device,
            output_device_label=self._current_output_device_label(),
            input_device_label=self._current_input_device_label(),
            input_channel=input_channel,
            fs=int(self._settings.get("sample_rate")),
            buffer_size=int(self._settings.get("buffer_size")),
            pre_silence=float(self._settings.get("pre_sweep_silence")),
            post_silence=float(self._settings.get("post_sweep_silence")),
            latency=self._sweep_latency_mode(),
            bluetooth_headphone_mode=bool(self._settings.get("bluetooth_headphone_mode")),
            start_alignment_confidence_min=float(self._settings.get("start_alignment_confidence_min")),
            end_marker_confidence_min=float(self._settings.get("end_marker_confidence_min")),
            timing_drift_max_ms=float(self._settings.get("timing_drift_max_ms")),
        )
        self._sweep_thread.finished.connect(self._on_sweep_thread_finished)
        self._sweep_thread.start()
        self._statusbar.showMessage(f"R&D sweep started (attempt {self._current_sweep_attempts}).")
        self._log_event(
            "INFO",
            "rnd",
            "R&D sweep started",
            attempt=self._current_sweep_attempts,
            sample_rate=int(self._settings.get("sample_rate")),
            buffer_size=int(self._settings.get("buffer_size")),
            output_level_db=output_level_db,
        )

    def _on_rnd_sweep_finished(self, recording: np.ndarray, sweep: np.ndarray) -> None:
        try:
            freqs, mag_db = compute_frequency_response(
                recording=recording,
                sweep=sweep,
                fs=int(self._settings.get("sample_rate")),
                f_low=_MEASUREMENT_F_MIN,
                f_high=_MEASUREMENT_F_MAX,
            )
            mag_db = normalize_at_1khz(freqs, mag_db, f_ref=1000.0)
            freqs_ds, mag_ds = downsample_to_log_points(
                freqs,
                mag_db,
                n_points=600,
                f_ref=1000.0,
                normalize_ref=True,
            )
            self._pending_curve = (freqs_ds, mag_ds)
            self._rnd_widget.set_review_curve(self._pending_curve)
            self._state = AppState.PASS_FAIL
            self._apply_state_ui()
            self._rnd_widget.set_status("Sweep complete. Waiting for review.")
            self._statusbar.showMessage("R&D sweep complete. Waiting for review.")
            QTimer.singleShot(0, self._show_rnd_review_dialog)
        except Exception as exc:
            self._on_rnd_sweep_error(f"Processing error: {exc}")

    def _on_rnd_sweep_error(self, message: str) -> None:
        self._log_event("ERROR", "rnd", message)
        self._cleanup_sweep_thread()
        self._close_rnd_review_dialog()
        self._rnd_widget.set_review_curve(None)
        self._pending_curve = None
        failure_reason = None
        if self._last_measurement_diagnostics is not None:
            failure_reason = getattr(self._last_measurement_diagnostics, "failure_reason", None)
        is_timing_quality_error = is_retryable_timing_failure(
            message=message,
            failure_reason=failure_reason,
        )
        if is_timing_quality_error and self._current_sweep_attempts < _MAX_SWEEP_ATTEMPTS:
            choice = QMessageBox.question(
                self,
                "Timing Quality Retry",
                f"{message}\n\nRetry R&D measurement attempt {self._current_sweep_attempts + 1} of {_MAX_SWEEP_ATTEMPTS}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if choice == QMessageBox.StandardButton.Yes:
                self._state = AppState.IDLE
                self._apply_state_ui()
                QTimer.singleShot(150, self._start_rnd_sweep)
                return
        self._rnd_sweep_active = False
        self._current_sweep_attempts = 0
        self._state = AppState.IDLE
        self._sweep_progress.setValue(0)
        self._apply_state_ui()
        self._start_level_monitor()
        self._rnd_widget.set_status("Ready")
        self._statusbar.showMessage(message)
        QMessageBox.warning(self, "R&D Sweep Error", message)

    def _show_rnd_review_dialog(self) -> None:
        if self._state != AppState.PASS_FAIL or self._pending_curve is None:
            return
        if self._rnd_review_dialog is not None:
            self._rnd_review_dialog.raise_()
            self._rnd_review_dialog.activateWindow()
            return
        previous = self._rnd_widget.session.measurements[-1].name if self._rnd_widget.session.measurements else ""
        dlg = RnDReviewDialog(
            previous,
            timing_quality=self._last_timing_quality,
            diagnostics=self._last_measurement_diagnostics,
            parent=self,
        )
        dlg.adjustSize()
        dlg.finished.connect(lambda _result: self._handle_rnd_review_choice(dlg))
        self._rnd_review_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _handle_rnd_review_choice(self, dlg: RnDReviewDialog) -> None:
        if self._rnd_review_dialog is dlg:
            self._rnd_review_dialog = None
        choice = dlg.choice()
        if choice == RnDReviewDialog.FAIL:
            self._rnd_widget.set_review_curve(None)
            self._pending_curve = None
            self._state = AppState.IDLE
            self._apply_state_ui()
            self._statusbar.showMessage("R&D measurement rejected. Redoing...")
            QTimer.singleShot(100, self._start_rnd_measurement)
            return
        if choice in {RnDReviewDialog.KEEP_NO_CHANGE, RnDReviewDialog.KEEP_CHANGE}:
            self._keep_rnd_measurement(
                change_status="changed" if choice == RnDReviewDialog.KEEP_CHANGE else "no_change",
                notes=dlg.notes() if choice == RnDReviewDialog.KEEP_CHANGE else dlg.notes(),
            )
            return
        self._cancel_rnd_measurement()

    def _keep_rnd_measurement(self, *, change_status: str, notes: str) -> None:
        if self._pending_curve is None:
            return
        freqs, mag_db = self._pending_curve
        channel_label = self._ch_combo.currentText().strip() or f"Channel {self._current_input_channel() + 1}"
        existing_names = {item.name for item in self._rnd_widget.session.measurements}
        measurement = RnDMeasurement(
            name=generate_measurement_name(
                self._session,
                self._current_input_device_label(),
                channel_label,
                existing_names,
            ),
            freqs=np.array(freqs, dtype=float, copy=True),
            mag_db=np.array(mag_db, dtype=float, copy=True),
            metadata=session_snapshot(self._session),
            rig=self._session.rig,
            input_device_label=self._current_input_device_label(),
            input_channel_index=self._current_input_channel(),
            input_channel_label=channel_label,
            output_device_label=self._current_output_device_label(),
            notes=notes,
            change_status=change_status,
            top_visible=True,
            pinned=False,
        )
        self._rnd_widget.set_review_curve(None)
        self._rnd_widget.add_measurement(measurement)
        self._pending_curve = None
        self._rnd_sweep_active = False
        self._current_sweep_attempts = 0
        self._state = AppState.IDLE
        self._sweep_progress.setValue(100)
        self._apply_state_ui()
        self._start_level_monitor()
        self._rnd_widget.set_status("Ready")
        self._statusbar.showMessage(f"R&D measurement kept: {measurement.name}")
        self._log_event("INFO", "rnd", "R&D measurement kept", name=measurement.name, status=change_status)
        self._run_automation_trigger("rnd_measurement_kept")

    def _cancel_rnd_measurement(self) -> None:
        self._abort_active_sweep()
        self._close_rnd_review_dialog()
        self._rnd_widget.set_review_curve(None)
        self._pending_curve = None
        self._rnd_sweep_active = False
        self._current_sweep_attempts = 0
        self._state = AppState.IDLE
        self._sweep_progress.setValue(0)
        self._apply_state_ui()
        self._start_level_monitor()
        self._rnd_widget.set_status("Ready")
        self._statusbar.showMessage("R&D measurement canceled.")

    def _close_rnd_review_dialog(self) -> None:
        if self._rnd_review_dialog is None:
            return
        dlg = self._rnd_review_dialog
        self._rnd_review_dialog = None
        dlg.blockSignals(True)
        dlg.close()

    def _recompute_average(self) -> None:
        if not self._kept_curves:
            self._average = None
            return

        freqs, mag_db = compute_rms_average(
            self._kept_curves,
            n_points=_DISPLAY_AVG_POINTS,
            f_ref=1000.0,
            f_min=_MEASUREMENT_F_MIN,
            f_max=_MEASUREMENT_F_MAX,
            normalize_ref=True,
        )
        self._average = (freqs, mag_db)

    def _recompute_two_channel_results(self) -> None:
        curves_by_key = {
            "channel_1": channel_curves(self._two_channel_pairs, 1),
            "channel_2": channel_curves(self._two_channel_pairs, 2),
            "combined": combined_pair_curves(
                self._two_channel_pairs,
                n_points=_DISPLAY_AVG_POINTS,
            ),
        }
        averages: dict[str, object] = {}
        for key, curves in curves_by_key.items():
            if not curves:
                averages[key] = None
                continue
            averages[key] = compute_rms_average(
                curves,
                n_points=_DISPLAY_AVG_POINTS,
                f_ref=1000.0,
                f_min=_MEASUREMENT_F_MIN,
                f_max=_MEASUREMENT_F_MAX,
                normalize_ref=False,
            )
        self._two_channel_averages = averages

    def _active_two_channel_key(self) -> str:
        if self._two_channel_bottom_mode == "combined":
            return "combined"
        return self._two_channel_selection

    def _active_two_channel_average(
        self,
    ) -> Optional[tuple[np.ndarray, np.ndarray]]:
        value = self._two_channel_averages.get(self._active_two_channel_key())
        return value if isinstance(value, tuple) else None

    def _active_measure_curves(self) -> list[tuple[np.ndarray, np.ndarray]]:
        if not getattr(self, "_two_channel_enabled", False):
            return list(self._kept_curves)
        key = self._active_two_channel_key()
        if key == "channel_1":
            return channel_curves(self._two_channel_pairs, 1)
        if key == "channel_2":
            return channel_curves(self._two_channel_pairs, 2)
        return combined_pair_curves(
            self._two_channel_pairs,
            n_points=_DISPLAY_AVG_POINTS,
        )

    def _active_measure_count(self) -> int:
        return (
            len(self._two_channel_pairs)
            if getattr(self, "_two_channel_enabled", False)
            else len(self._kept_curves)
        )

    def _active_measure_label(self) -> str:
        if not getattr(self, "_two_channel_enabled", False):
            return ""
        return curve_label_for_selection(self._active_two_channel_key())

    def _active_measure_session(self) -> SessionData:
        label = self._active_measure_label()
        if label in {"L", "R"}:
            return replace(self._session, channel_side=label)
        if label == "BOTH":
            return replace(self._session, channel_side="")
        return self._session

    def _active_measure_variation(self):
        if getattr(self, "_two_channel_enabled", False):
            return self._two_channel_variations.get(self._active_two_channel_key())
        return self._variation

    def _recompute_variation(self) -> None:
        active_hrtf = self._hrtf if self._is_hrtf_active() else None
        self._variation = self._variation_from_kept_curves(hrtf=active_hrtf)

    def _variation_from_kept_curves(
        self,
        *,
        hrtf: HRTFCurve | None,
    ) -> Optional[
        tuple[
            np.ndarray,
            np.ndarray,
            np.ndarray,
            np.ndarray,
            np.ndarray,
            np.ndarray,
        ]
    ]:
        return MainWindow._variation_from_curves(
            self,
            self._kept_curves,
            self._average,
            hrtf=hrtf,
        )

    def _variation_from_curves(
        self,
        curves: list[tuple[np.ndarray, np.ndarray]],
        average: Optional[tuple[np.ndarray, np.ndarray]],
        *,
        hrtf: HRTFCurve | None,
    ) -> Optional[
        tuple[
            np.ndarray,
            np.ndarray,
            np.ndarray,
            np.ndarray,
            np.ndarray,
            np.ndarray,
        ]
    ]:
        if not curves or average is None:
            return None

        base_freqs = average[0]
        rows: list[np.ndarray] = []
        for freqs, mag in curves:
            values = np.interp(base_freqs, freqs, mag)
            if hrtf is not None and not getattr(hrtf, "is_variation", False):
                values = hrtf.apply(base_freqs, values)
            _, values = smooth_fractional_octave(
                base_freqs,
                values,
                fraction=_DISPLAY_AVG_SMOOTHING,
            )
            rows.append(values)

        if not rows:
            return None

        mat = np.vstack(rows)
        p10 = np.percentile(mat, 10, axis=0)
        p25 = np.percentile(mat, 25, axis=0)
        p75 = np.percentile(mat, 75, axis=0)
        p90 = np.percentile(mat, 90, axis=0)
        median = np.percentile(mat, 50, axis=0)
        if hrtf is not None and getattr(hrtf, "is_variation", False):
            p10, p25, median, p75, p90 = hrtf.apply_to_variation(
                base_freqs,
                p10,
                p25,
                median,
                p75,
                p90,
            )
        return (base_freqs, p10, p25, p75, p90, median)

    def _average_curve_with_hrtf(
        self,
        hrtf: HRTFCurve | None,
    ) -> Optional[tuple[np.ndarray, np.ndarray]]:
        source = (
            self._active_two_channel_average()
            if getattr(self, "_two_channel_enabled", False)
            else self._average
        )
        if source is None:
            return None
        freqs, mag_db = source
        if hrtf is None:
            return freqs, mag_db
        return freqs, hrtf.apply(freqs, mag_db)

    def _bottom_curve_for_display_and_export(
        self,
    ) -> Optional[tuple[np.ndarray, np.ndarray]]:
        active_hrtf = self._hrtf if self._is_hrtf_active() else None
        return self._average_curve_with_hrtf(active_hrtf)

    def _bottom_curve_for_display(self) -> Optional[tuple[np.ndarray, np.ndarray]]:
        curve = self._bottom_curve_for_display_and_export()
        if curve is None:
            return None

        freqs, mag_db = curve
        return smooth_fractional_octave(
            freqs,
            mag_db,
            fraction=_DISPLAY_AVG_SMOOTHING,
        )

    def _update_plots(self, *_args, show_pending: bool = False) -> None:
        if self._two_channel_enabled:
            active_hrtf = self._hrtf if self._is_hrtf_active() else None
            pairs = list(self._two_channel_pairs)
            if show_pending and self._pending_pair is not None:
                pairs.append(self._pending_pair)
            curves_by_key = {
                "channel_1": channel_curves(self._two_channel_pairs, 1),
                "channel_2": channel_curves(self._two_channel_pairs, 2),
                "combined": combined_pair_curves(
                    self._two_channel_pairs,
                    n_points=_DISPLAY_AVG_POINTS,
                ),
            }
            averages: dict[str, object] = {}
            variations: dict[str, object] = {}
            for key in ("channel_1", "channel_2", "combined"):
                raw_average = self._two_channel_averages.get(key)
                if isinstance(raw_average, tuple):
                    freqs, values = raw_average
                    if active_hrtf is not None:
                        values = active_hrtf.apply(freqs, values)
                    averages[key] = smooth_fractional_octave(
                        freqs,
                        values,
                        fraction=_DISPLAY_AVG_SMOOTHING,
                    )
                else:
                    averages[key] = None
                variations[key] = self._variation_from_curves(
                    curves_by_key[key],
                    raw_average if isinstance(raw_average, tuple) else None,
                    hrtf=active_hrtf,
                )
            self._two_channel_variations = variations
            self._plots.two.update_frequency_response(
                top_channel_1=channel_curves(pairs, 1),
                top_channel_2=channel_curves(pairs, 2),
                averages=averages,
                variations=variations,
                show_variation=self._bottom_view_mode() == "variation",
            )
            self._sync_export_button()
            return

        avg = self._bottom_curve_for_display()
        self._recompute_variation()

        kept = list(self._kept_curves)
        if show_pending and self._pending_curve is not None:
            kept = kept + [self._pending_curve]

        self._plots.update_curves(
            kept=kept,
            average=avg,
            variation=self._variation,
            bottom_mode=self._bottom_view_mode(),
            animate_last=show_pending and self._pending_curve is not None,
        )
        self._sync_export_button()

    def _bottom_view_mode(self) -> str:
        if (
            getattr(self, "_is_hrtf_active", lambda: False)()
            and getattr(getattr(self, "_hrtf", None), "is_variation", False)
        ):
            return "variation"
        return "variation" if self._variation_toggle.isChecked() else "average"

    def _on_bottom_view_changed(self, *_args) -> None:
        self._update_plots()

    def _on_hrtf_selected(self) -> None:
        path = self._hrtf_combo.currentData()
        if not path:
            self._hrtf = None
            self._settings.set("hrtf_path", None)
            self._sync_hrtf_ui()
            self._update_plots()
            self._statusbar.showMessage("HRTF cleared.")
            return

        try:
            self._hrtf = HRTFCurve(path)
            self._settings.set("hrtf_path", path)
            self._sync_hrtf_ui()
            self._hrtf_toggle.setChecked(True)
            if self._hrtf.is_variation and hasattr(self, "_variation_toggle"):
                self._variation_toggle.setChecked(True)
            self._update_plots()
            kind = "population variation compensation" if self._hrtf.is_variation else "HRTF"
            self._statusbar.showMessage(f"Loaded {kind}: {Path(path).name}")
        except Exception as exc:
            QMessageBox.warning(self, "HRTF Load Error", str(exc))
            self._hrtf = None
            self._settings.set("hrtf_path", None)
            self._sync_hrtf_ui()
            self._update_plots()

    def _import_dropped_measurement_files(self, paths: list[str]) -> None:
        if getattr(self, "_two_channel_enabled", False):
            QMessageBox.information(
                self,
                "Single Channel Only",
                "TXT drag-and-drop import is available only in Single Channel mode.",
            )
            self._statusbar.showMessage(
                "Measurement import blocked: Two Channel mode is active."
            )
            return
        if self._state != AppState.IDLE:
            QMessageBox.information(
                self,
                "Busy",
                "Measurement import is only available while idle.",
            )
            self._statusbar.showMessage("Measurement import blocked: queue is active.")
            return

        loaded = 0
        failed: list[str] = []
        for path in paths:
            try:
                curve = load_two_column_txt_curve(path, label="Measurement")
            except Exception as exc:
                failed.append(f"{Path(path).name}: {exc}")
                continue
            self._kept_curves.append(curve)
            loaded += 1

        if loaded > 0:
            self._recompute_average()
            self._recompute_variation()
            self._update_queue_progress()
            self._update_plots()

        if loaded == 0 and failed:
            QMessageBox.warning(
                self,
                "Import Failed",
                "No files were imported.\n\n" + "\n".join(failed[:8]),
            )
            self._statusbar.showMessage("Measurement import failed.")
            return

        if failed:
            QMessageBox.warning(
                self,
                "Import Completed With Warnings",
                f"Loaded {loaded} file(s), failed {len(failed)} file(s).\n\n"
                + "\n".join(failed[:8]),
            )

        self._statusbar.showMessage(
            f"Measurement import complete: loaded {loaded}, failed {len(failed)}."
        )

    def _clear_all(self) -> None:
        if self._state != AppState.IDLE:
            QMessageBox.information(
                self,
                "Busy",
                "Cannot clear measurements while queue is active.",
            )
            return

        active_has_data = (
            bool(self._two_channel_pairs) or self._pending_pair is not None
            if self._two_channel_enabled
            else bool(self._kept_curves) or self._pending_curve is not None
        )
        if not active_has_data:
            return

        if bool(self._settings.get("confirm_clear_measurements")):
            confirmed, dont_show_again = self._confirm_clear_all()
            if not confirmed:
                return
            if dont_show_again:
                self._settings.set("confirm_clear_measurements", False)
                self._settings_widget.refresh_from_settings()

        if self._two_channel_enabled:
            self._two_channel_pairs.clear()
            self._two_channel_averages.clear()
            self._two_channel_variations.clear()
            self._pending_pair = None
            self._pending_pair_first_raw = None
            self._pending_pair_first_diagnostics = None
            self._update_plots()
        else:
            self._kept_curves.clear()
            self._average = None
            self._variation = None
            self._pending_curve = None
            self._plots.clear_all()
        self._queue_target = 0
        self._queue_index = 0
        self._current_sweep_attempts = 0
        self._update_queue_progress()
        self._sweep_progress.setValue(0)
        self._sync_export_button()
        self._apply_state_ui()
        self._statusbar.showMessage("All measurements cleared.")

    def _confirm_clear_all(self) -> tuple[bool, bool]:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Clear All Measurements")
        dialog.setText(
            "Are you sure you want to clear all measurements from this tab?"
        )
        clear_button = dialog.addButton(
            "Clear All", QMessageBox.ButtonRole.DestructiveRole
        )
        clear_button.setObjectName("btn_danger")
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dont_show = QCheckBox("Don’t show this warning again")
        dialog.setCheckBox(dont_show)
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        return dialog.clickedButton() is clear_button, dont_show.isChecked()

    def _undo_last_measurement(self) -> None:
        if self._state != AppState.IDLE:
            QMessageBox.information(
                self,
                "Busy",
                "Undo is only available while idle.",
            )
            return

        if self._two_channel_enabled:
            if not self._two_channel_pairs:
                return
            self._two_channel_pairs.pop()
            self._recompute_two_channel_results()
        else:
            if not self._kept_curves:
                return
            self._kept_curves.pop()
            self._recompute_average()
            self._recompute_variation()
        self._update_queue_progress()
        self._update_plots()
        self._apply_state_ui()
        self._statusbar.showMessage("Last kept measurement removed.")

    def _save_metadata_overlay(self) -> None:
        if not self._metadata_editor.validate():
            return
        self._session = self._metadata_editor.session_data()
        self._refresh_session_labels()
        self._refresh_window_title()
        self._close_metadata_overlay()
        self._statusbar.showMessage("Headphone metadata updated.")

    def _clear_metadata(self) -> None:
        if bool(self._settings.get("confirm_clear_metadata")):
            confirmed, dont_show_again = self._confirm_clear_metadata()
            if not confirmed:
                return
            if dont_show_again:
                self._settings.set("confirm_clear_metadata", False)
                self._settings_widget.refresh_from_settings()

        self._session = SessionData(
            rig="Unknown Rig",
            brand="Unknown",
            model="Unknown",
        )
        self._refresh_session_labels()
        self._refresh_window_title()
        self._statusbar.showMessage("Headphone metadata cleared.")

    def _confirm_clear_metadata(self) -> tuple[bool, bool]:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Clear Headphone Metadata")
        dialog.setText("Are you sure you want to clear all headphone metadata?")
        clear_button = dialog.addButton(
            "Clear Metadata", QMessageBox.ButtonRole.DestructiveRole
        )
        clear_button.setObjectName("btn_danger")
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dont_show = QCheckBox("Don’t show this warning again")
        dialog.setCheckBox(dont_show)
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        return dialog.clickedButton() is clear_button, dont_show.isChecked()

    def _on_queue_level_changed(self, value: float) -> None:
        if hasattr(self._settings, "clear_session"):
            self._settings.clear_session("queue_output_level_db")
        clamped = max(-120.0, min(0.0, float(value)))
        if abs(clamped - float(value)) > 1e-9:
            self._queue_level_spin.blockSignals(True)
            self._queue_level_spin.setValue(clamped)
            self._queue_level_spin.blockSignals(False)
        if self._queue_level_persist_toggle.isChecked():
            self._settings.set("queue_output_level_db", clamped)
        if hasattr(self, "_plots"):
            self._plots.two.set_generator_level(clamped)
        self._balance_level_db = clamped
        if self._balance_engine is not None:
            self._balance_engine.set_parameters(
                self._balance_waveform,
                self._balance_frequency,
                self._balance_level_db,
            )

    def _on_queue_count_changed(self, _value: int) -> None:
        if hasattr(self._settings, "clear_session"):
            self._settings.clear_session("queue_count")

    def _on_queue_level_persist_changed(self, _state: int) -> None:
        persist = self._queue_level_persist_toggle.isChecked()
        self._settings.set("queue_output_level_persist", persist)
        if persist:
            self._settings.set("queue_output_level_db", float(self._queue_level_spin.value()))

    def _on_bluetooth_mode_changed(self, _state: int) -> None:
        enabled = self._bluetooth_mode_toggle.isChecked()
        self._settings.set("bluetooth_headphone_mode", enabled)
        if enabled:
            self._apply_bluetooth_headphone_mode_settings(
                notify=True,
                preserve_standard=True,
            )
            return
        used_fallback = self._apply_standard_measurement_mode_settings()
        if used_fallback:
            self._statusbar.showMessage(
                "Bluetooth headphone mode disabled. Restored standard measurement defaults."
            )
        else:
            self._statusbar.showMessage(
                "Bluetooth headphone mode disabled. Restored standard measurement settings."
            )

    def _apply_bluetooth_headphone_mode_settings(
        self,
        notify: bool,
        preserve_standard: bool,
    ) -> None:
        updates = bluetooth_profile_updates()
        if preserve_standard:
            current_profile = {
                key: self._settings.get(key)
                for key in updates
            }
            updates[PROFILE_SNAPSHOT_SETTING] = snapshot_measurement_profile(
                current_profile
            )
        self._settings.update(updates)
        self._settings_widget.refresh_from_settings()
        if notify:
            self._statusbar.showMessage(
                "Bluetooth mode applied: high latency, 512 buffer, and Bluetooth-safe timing thresholds."
            )

    def _apply_standard_measurement_mode_settings(self) -> bool:
        updates, used_fallback = restore_standard_profile_updates(
            self._settings.get(PROFILE_SNAPSHOT_SETTING)
        )
        updates[PROFILE_SNAPSHOT_SETTING] = None
        self._settings.update(updates)
        self._settings_widget.refresh_from_settings()
        return used_fallback

    def _choose_export_directory(self) -> None:
        current = self._export_dir_input.text().strip()
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Choose Export Directory",
            current or "",
        )
        if not chosen:
            return
        self._export_dir_input.setText(chosen)
        self._settings.set("export_directory", chosen)

    def _open_calibration(self) -> None:
        input_device = self._current_input_device()
        input_info = self._current_input_device_info()
        if input_device is None or input_info is None:
            QMessageBox.warning(
                self,
                "No Input Device",
                "Select an input device first.",
            )
            return

        dlg = CalibrationDialog(
            device_index=input_device,
            device_name=str(input_info["name"]),
            device_label=self._current_input_device_label(),
            channel=self._current_input_channel(),
            fs=int(self._settings.get("sample_rate")),
            buffer_size=int(self._settings.get("buffer_size")),
            cal_store=self._cal_store,
            parent=self,
        )
        dlg.calibration_done.connect(self._on_calibration_done)
        dlg.exec()

    def _on_calibration_done(self, device_name: str, sensitivity: float) -> None:
        label = self._current_input_device_label() or device_name
        self._statusbar.showMessage(
            f"Calibration saved for {label}: {sensitivity:.6f} Pa/FS"
        )

    def _open_test_level(self) -> None:
        input_device = self._current_input_device()
        input_info = self._current_input_device_info()
        if input_device is None or input_info is None:
            QMessageBox.information(
                self,
                "No Input Device",
                "Select an input device first.",
            )
            return

        calibrated = self._cal_store.is_calibrated(str(input_info["name"]))
        dlg = TestLevelDialog(
            self._level_snapshot,
            play_noise_fn=self._play_test_noise,
            calibrated=calibrated,
            parent=self,
        )
        dlg.exec()

    def _play_test_noise(self) -> Optional[str]:
        output_device = self._current_output_device()
        if output_device is None:
            return "No output device selected."
        fs = int(self._settings.get("sample_rate"))
        dur_s = 1.8
        n = int(round(fs * dur_s))
        if n <= 0:
            return "Invalid sample rate for noise ping."
        noise = (np.random.randn(n).astype(np.float32) * 0.04)
        fade_n = min(max(8, int(0.01 * fs)), n // 2)
        if fade_n > 0:
            fade = np.linspace(0.0, 1.0, fade_n, dtype=np.float32)
            noise[:fade_n] *= fade
            noise[-fade_n:] *= fade[::-1]
        try:
            sd.stop()
            sd.play(noise, samplerate=fs, device=output_device, blocking=False)
        except Exception as exc:
            return f"Noise ping failed: {exc}"
        self._statusbar.showMessage("Played test noise ping on selected output device.")
        return None

    def _level_snapshot(self) -> tuple[float, Optional[float], str]:
        input_info = self._current_input_device_info()
        input_device_name = str(input_info["name"]) if input_info is not None else ""
        input_label = self._current_input_device_label()
        dbfs = self._last_level_dbfs
        spl = None
        if input_device_name and self._cal_store.is_calibrated(input_device_name):
            rms_fs = 10.0 ** (dbfs / 20.0) if dbfs > -120.0 else 0.0
            spl = self._cal_store.rms_to_dbspl(input_device_name, rms_fs)
        return dbfs, spl, input_label

    def _export(self) -> None:
        if self._bottom_view_mode() == "variation":
            self._export_variation()
            return
        self._export_average()

    def _send_to_curator(self) -> None:
        if self._state != AppState.IDLE:
            raise ValueError("Measurements can only be sent to Curator while idle.")

        mode = self._bottom_view_mode()
        active_hrtf = self._hrtf if self._is_hrtf_active() else None
        correction = None
        curve: CurveData
        curator_metadata = session_snapshot(self._session)
        curator_metadata.update(
            {
                "hrtf_name": active_hrtf.name if active_hrtf is not None else "",
                "compensated": active_hrtf is not None,
                "measure_channel": self._active_measure_label(),
            }
        )
        if mode == "variation":
            active_variation = self._active_measure_variation()
            if active_variation is None:
                raise ValueError("No variation band is available to send.")
            source_variation = None
            if active_hrtf is not None and getattr(active_hrtf, "is_variation", False):
                source_variation = self._variation_from_curves(
                    self._active_measure_curves(),
                    self._active_two_channel_average()
                    if self._two_channel_enabled
                    else self._average,
                    hrtf=None,
                )
            freqs, p10, p25, p75, p90, median = (
                source_variation if source_variation is not None else active_variation
            )
            if active_hrtf is not None and not getattr(active_hrtf, "is_variation", False):
                correction = active_hrtf.evaluate(freqs)
            curve = CurveData(
                kind="variation",
                freqs=np.array(freqs, dtype=float, copy=True),
                p10_db=np.array(p10, dtype=float, copy=True) + (correction if correction is not None else 0.0),
                p25_db=np.array(p25, dtype=float, copy=True) + (correction if correction is not None else 0.0),
                median_db=np.array(median, dtype=float, copy=True) + (correction if correction is not None else 0.0),
                p75_db=np.array(p75, dtype=float, copy=True) + (correction if correction is not None else 0.0),
                p90_db=np.array(p90, dtype=float, copy=True) + (correction if correction is not None else 0.0),
                metadata={
                    **curator_metadata,
                    "Source": "Fastgraph current variation",
                    "curve_type": "variation",
                },
            )
            kind_label = "VAR"
        else:
            displayed = self._bottom_curve_for_display()
            if displayed is None:
                raise ValueError("No averaged curve is available to send.")
            freqs, magnitude = displayed
            if active_hrtf is not None:
                correction = active_hrtf.evaluate(freqs)
            baseline = np.array(magnitude, dtype=float, copy=True)
            if correction is not None:
                baseline = baseline + correction
            curve = CurveData(
                kind="fr",
                freqs=np.array(freqs, dtype=float, copy=True),
                mag_db=baseline,
                metadata={
                    **curator_metadata,
                    "Source": "Fastgraph current average",
                    "curve_type": "frequency_response",
                },
            )
            kind_label = "AVG"

        identity = self._session.asset_tag.strip() or " ".join(
            part for part in (self._session.brand.strip(), self._session.model.strip()) if part
        )
        if not identity:
            identity = "Fastgraph"
        comp_label = "COMP" if active_hrtf is not None else "RAW"
        channel_label = self._active_measure_label()
        channel_part = f" {channel_label}" if channel_label else ""
        name = f"{identity}{channel_part} {comp_label} {kind_label}"
        # Make Curator visible before its reveal animation starts. Some Qt
        # platforms defer animation paints for hidden tab pages.
        self._tabs.setCurrentWidget(self._curator_widget)
        layer = self._curator_widget.add_curve(
            curve,
            name,
            source_path="<fastgraph>",
            hrtf=active_hrtf,
            normalize=False,
        )
        self._curator_widget.offset_layer_to_zero_at_1khz(layer)
        self._statusbar.showMessage(f"Sent to Curator: {layer.name}")
        self._log_event(
            "INFO",
            "curator",
            "Measure view sent to Curator",
            name=layer.name,
            kind=curve.kind,
            hrtf=active_hrtf.name if active_hrtf else None,
        )

    @staticmethod
    def _unique_rnd_transfer_name(base: str, existing: set[str]) -> str:
        if base not in existing:
            return base
        suffix = 2
        while f"{base} ({suffix})" in existing:
            suffix += 1
        return f"{base} ({suffix})"

    def _measure_to_rnd_unavailable_reason(self) -> str:
        if self._state != AppState.IDLE:
            return "Measurements can only be sent to R&D while Measure is idle."
        if self._channel_balance_mode_active():
            return "Switch to Frequency Response before sending data to R&D."
        if self._bottom_view_mode() == "variation":
            if not self._active_measure_curves():
                return "Keep at least one measurement before sending Var to R&D."
        elif (
            self._active_two_channel_average()
            if self._two_channel_enabled
            else self._average
        ) is None:
            return "Create an average before sending it to R&D."
        return ""

    def _send_measure_to_rnd(self) -> None:
        unavailable = self._measure_to_rnd_unavailable_reason()
        if unavailable:
            QMessageBox.information(self, "Send to R&D Unavailable", unavailable)
            return

        active_hrtf = self._hrtf if self._is_hrtf_active() else None
        hrtf_path = str(active_hrtf.path) if active_hrtf is not None else ""
        hrtf_name = active_hrtf.name if active_hrtf is not None else ""
        metadata = session_snapshot(self._session)
        input_label = self._current_input_device_label()
        output_label = self._current_output_device_label()
        active_label = self._active_measure_label()
        input_channel_index = (
            1 if active_label == "R" else 0
        ) if self._two_channel_enabled else self._current_input_channel()
        channel_label = active_label or (
            self._ch_combo.currentText().strip()
            or f"Channel {input_channel_index + 1}"
        )
        metadata["measure_channel"] = channel_label
        identity = self._session.asset_tag.strip() or " ".join(
            part
            for part in (self._session.brand.strip(), self._session.model.strip())
            if part
        )
        identity = identity or "Fastgraph"

        if self._bottom_view_mode() == "variation":
            group_name = self._unique_rnd_transfer_name(
                f"{identity} VAR",
                {group.name for group in self._rnd_widget.session.groups},
            )
            existing_names = {
                measurement.name for measurement in self._rnd_widget.session.measurements
            }
            measurements: list[RnDMeasurement] = []
            for index, (freqs, mag_db) in enumerate(
                self._active_measure_curves(), start=1
            ):
                name = self._unique_rnd_transfer_name(
                    f"{group_name} Sweep {index}",
                    existing_names,
                )
                existing_names.add(name)
                measurements.append(
                    RnDMeasurement(
                        name=name,
                        freqs=np.array(freqs, dtype=float, copy=True),
                        mag_db=np.array(mag_db, dtype=float, copy=True),
                        metadata=dict(metadata),
                        rig=self._session.rig,
                        input_device_label=input_label,
                        input_channel_index=input_channel_index,
                        input_channel_label=channel_label,
                        output_device_label=output_label,
                        top_visible=True,
                        pinned=False,
                        hrtf_path=hrtf_path,
                        hrtf_name=hrtf_name,
                    )
                )
            group = RnDGroup(
                name=group_name,
                expanded=True,
                visible=True,
                pinned=False,
                variation_enabled=True,
            )
            self._rnd_widget.add_measurement_batch(
                measurements,
                group=group,
                inherit_default_hrtf=False,
            )
            transferred_name = group_name
            transferred_count = len(measurements)
            transfer_mode = "variation"
        else:
            active_average = (
                self._active_two_channel_average()
                if self._two_channel_enabled
                else self._average
            )
            assert active_average is not None
            freqs, mag_db = active_average
            name = self._unique_rnd_transfer_name(
                f"{identity} AVG",
                {
                    measurement.name
                    for measurement in self._rnd_widget.session.measurements
                },
            )
            measurement = RnDMeasurement(
                name=name,
                freqs=np.array(freqs, dtype=float, copy=True),
                mag_db=np.array(mag_db, dtype=float, copy=True),
                metadata=dict(metadata),
                rig=self._session.rig,
                input_device_label=input_label,
                input_channel_index=input_channel_index,
                input_channel_label=channel_label,
                output_device_label=output_label,
                top_visible=True,
                pinned=False,
                hrtf_path=hrtf_path,
                hrtf_name=hrtf_name,
            )
            self._rnd_widget.add_measurement_batch(
                [measurement],
                inherit_default_hrtf=False,
            )
            transferred_name = name
            transferred_count = 1
            transfer_mode = "average"

        self._tabs.setCurrentWidget(self._rnd_widget)
        self._statusbar.showMessage(
            f"Sent to R&D: {transferred_name} ({transferred_count} measurement"
            f"{'s' if transferred_count != 1 else ''})"
        )
        self._log_event(
            "INFO",
            "rnd",
            "Measure view sent to R&D",
            name=transferred_name,
            mode=transfer_mode,
            measurement_count=transferred_count,
            hrtf=hrtf_name or None,
        )

    def _send_rnd_to_curator(self) -> None:
        if self._state != AppState.IDLE:
            return
        measurement = self._rnd_widget.selected_measurement()
        group = self._rnd_widget.selected_group()
        if measurement is not None:
            if not self._ensure_rnd_hrtfs_available([measurement]):
                return
            freqs, mag = self._rnd_widget.displayed_measurement_curve(measurement)
            freqs = np.array(freqs, dtype=float, copy=True)
            mag = np.array(mag, dtype=float, copy=True)
            curve = CurveData(
                kind="fr",
                freqs=freqs,
                mag_db=mag,
                metadata={
                    **dict(measurement.metadata),
                    "rig": measurement.rig,
                    "hrtf_name": measurement.hrtf_name,
                    "compensated": bool(measurement.hrtf_path or measurement.hrtf_name),
                    "Source": "Fastgraph R&D measurement",
                    "curve_type": "frequency_response",
                },
            )
            name = measurement.name
        elif group is not None:
            if not group.variation_enabled:
                QMessageBox.information(
                    self,
                    "Variation Disabled",
                    "Enable variation for the selected group before sending it to Curator.",
                )
                return
            measurements = self._rnd_widget.displayed_group_measurements(group)
            if not self._ensure_rnd_hrtfs_available(measurements):
                return
            variation = rnd_group_variation(
                measurements,
                smoothing_fraction=int(self._rnd_widget.session.smoothing_fraction or 48),
            )
            if variation is None:
                QMessageBox.information(
                    self,
                    "Nothing to Send",
                    "Selected group needs at least two measurements for a variation layer.",
                )
                return
            freqs, p10, p25, p75, p90, median = variation
            group_metadata = shared_metadata(
                measurement.metadata for measurement in measurements
            )
            rigs = {measurement.rig.strip() for measurement in measurements}
            if len(rigs) == 1 and next(iter(rigs)):
                group_metadata["rig"] = next(iter(rigs))
            hrtf_names = {
                measurement.hrtf_name.strip()
                for measurement in measurements
            }
            if len(hrtf_names) == 1 and next(iter(hrtf_names)):
                group_metadata["hrtf_name"] = next(iter(hrtf_names))
            compensation_states = {
                bool(measurement.hrtf_path or measurement.hrtf_name)
                for measurement in measurements
            }
            if len(compensation_states) == 1:
                group_metadata["compensated"] = next(iter(compensation_states))
            group_metadata.update(
                {
                    "Source": "Fastgraph R&D group variation",
                    "curve_type": "variation",
                }
            )
            curve = CurveData(
                kind="variation",
                freqs=np.array(freqs, dtype=float, copy=True),
                p10_db=np.array(p10, dtype=float, copy=True),
                p25_db=np.array(p25, dtype=float, copy=True),
                median_db=np.array(median, dtype=float, copy=True),
                p75_db=np.array(p75, dtype=float, copy=True),
                p90_db=np.array(p90, dtype=float, copy=True),
                metadata=group_metadata,
            )
            name = f"{group.name} VAR"
        else:
            QMessageBox.information(self, "Nothing Selected", "Select an R&D measurement or group first.")
            return

        self._tabs.setCurrentWidget(self._curator_widget)
        layer = self._curator_widget.add_curve(
            curve,
            name,
            source_path="<fastgraph-rnd>",
            hrtf=None,
            normalize=False,
        )
        self._curator_widget.offset_layer_to_zero_at_1khz(layer)
        self._statusbar.showMessage(f"Sent R&D item to Curator: {layer.name}")
        self._log_event("INFO", "rnd", "R&D item sent to Curator", name=layer.name, kind=curve.kind)

    def _export_rnd_selected(self) -> None:
        if self._state != AppState.IDLE:
            return
        measurement = self._rnd_widget.selected_measurement()
        group = self._rnd_widget.selected_group()
        if measurement is not None:
            self._export_rnd_measurement(measurement)
            return
        if group is not None:
            if group.variation_enabled:
                self._export_rnd_group_variation(group)
            else:
                self._export_rnd_group_measurements(group)
            return
        QMessageBox.information(self, "Nothing Selected", "Select an R&D measurement or group first.")

    def _export_rnd_measurement(self, measurement: RnDMeasurement, requested_path: Optional[str] = None) -> None:
        session = measurement_session_data(measurement)
        hrtf_path = self._rnd_widget.resolve_hrtf_path(measurement.hrtf_path, measurement.hrtf_name)
        if not self._ensure_rnd_hrtfs_available([measurement]):
            return
        compensated = bool(hrtf_path)
        filename = f"{self._safe_filename(measurement.name)} {'COMP' if compensated else 'RAW'}.txt"
        path = self._resolve_export_path(requested_path, filename, "Export R&D Measurement")
        if path is None:
            return
        freqs, mag = self._rnd_widget.displayed_measurement_curve(measurement)
        hrtf = HRTFCurve(hrtf_path) if compensated else None
        export_curve(
            freqs=freqs,
            mag_db=mag,
            session=session,
            output_path=path,
            compensated=compensated,
            hrtf=hrtf,
            n_sweeps=1,
        )
        self._settings.set("export_directory", str(path.parent))
        self._export_dir_input.setText(str(path.parent))
        self._statusbar.showMessage(f"Exported R&D measurement: {path}")
        self._log_event("INFO", "rnd", "R&D measurement exported", path=str(path))
        self._run_automation_trigger("export_complete")

    def _export_rnd_group_variation(self, group) -> None:
        measurements = self._rnd_widget.displayed_group_measurements(group)
        if not self._ensure_rnd_hrtfs_available(measurements):
            return
        variation = rnd_group_variation(
            measurements,
            smoothing_fraction=int(self._rnd_widget.session.smoothing_fraction or 48),
        )
        if variation is None:
            QMessageBox.information(
                self,
                "Nothing to Export",
                "Selected group needs at least two measurements for variation export.",
            )
            return
        compensated = any(
            bool(self._rnd_widget.resolve_hrtf_path(measurement.hrtf_path, measurement.hrtf_name))
            for measurement in measurements
        )
        filename = f"{self._safe_filename(group.name)} {'COMP' if compensated else 'RAW'} VAR.txt"
        path = self._resolve_export_path(None, filename, "Export R&D Group Variation")
        if path is None:
            return
        freqs, p10, p25, p75, p90, median = variation
        hrtf = None
        session = measurement_session_data(measurements[0])
        export_variation(
            freqs=freqs,
            p10_db=p10,
            p25_db=p25,
            median_db=median,
            p75_db=p75,
            p90_db=p90,
            session=session,
            output_path=path,
            compensated=compensated,
            hrtf=hrtf,
            n_sweeps=len(measurements),
            smoothing_fraction=int(self._rnd_widget.session.smoothing_fraction or 48),
        )
        self._settings.set("export_directory", str(path.parent))
        self._export_dir_input.setText(str(path.parent))
        self._statusbar.showMessage(f"Exported R&D variation: {path}")
        self._log_event("INFO", "rnd", "R&D variation exported", path=str(path))
        self._run_automation_trigger("export_complete")

    def _export_rnd_group_measurements(self, group) -> None:
        measurements = [
            measurement
            for measurement_id in group.measurement_ids
            if (measurement := self._rnd_widget.session.measurement_by_id(measurement_id)) is not None
        ]
        if not measurements:
            QMessageBox.information(self, "Nothing to Export", "Selected group has no measurements.")
            return
        if not self._ensure_rnd_hrtfs_available(measurements):
            return
        directory = QFileDialog.getExistingDirectory(
            self,
            "Export R&D Group Measurements",
            str(self._settings.get("export_directory") or ""),
        )
        if not directory:
            return
        for measurement in measurements:
            path = Path(directory) / f"{self._safe_filename(measurement.name)}.txt"
            self._export_rnd_measurement(measurement, str(path))
        self._statusbar.showMessage(f"Exported {len(measurements)} R&D measurements.")

    def _ensure_rnd_hrtfs_available(self, measurements: list[RnDMeasurement]) -> bool:
        missing = []
        for measurement in measurements:
            if not (measurement.hrtf_path or measurement.hrtf_name):
                continue
            if self._rnd_widget.resolve_hrtf_path(measurement.hrtf_path, measurement.hrtf_name):
                continue
            label = measurement.hrtf_name or Path(measurement.hrtf_path).stem or measurement.hrtf_path
            missing.append(f"{measurement.name}: {label}")
        if not missing:
            return True
        QMessageBox.warning(
            self,
            "Missing R&D HRTF",
            "One or more R&D measurements reference an HRTF that is not available on this machine.\n\n"
            + "\n".join(missing[:6])
            + ("\n..." if len(missing) > 6 else "")
            + "\n\nChoose an available HRTF or set the row to None before exporting or sending to Curator.",
        )
        self._rnd_widget.set_status("Ready - missing R&D HRTF")
        return False

    def _rnd_default_dir(self) -> Path:
        configured = str(self._settings.get("rnd_session_directory") or "").strip()
        if configured:
            return Path(configured).expanduser()
        documents = Path.home() / "Documents"
        return documents if documents.exists() else Path.home()

    def _initialize_rnd_recovery(self) -> None:
        try:
            candidates = self._rnd_recovery.candidates()
            if candidates:
                dialog = RnDRecoveryDialog(candidates, self)
                dialog.exec()
                candidate = dialog.selected_candidate()
                if dialog.action == RnDRecoveryDialog.RESTORE:
                    session, missing_photos = self._rnd_recovery.load_candidate(
                        candidate,
                        self._rnd_widget.photo_store,
                    )
                    self._rnd_widget.replace_session(session)
                    self._tabs.setCurrentWidget(self._rnd_widget)
                    self._rnd_dirty = not session.is_empty()
                    self._restored_recovery_candidate = (
                        candidate if candidate.kind == "deferred" else None
                    )
                    if missing_photos:
                        QMessageBox.warning(
                            self,
                            "Missing R&D Photos",
                            f"{len(missing_photos)} recovered photo attachment(s) could not be found.",
                        )
                elif dialog.action == RnDRecoveryDialog.DISCARD:
                    self._rnd_recovery.discard(candidate)
                else:
                    self._rnd_recovery.keep_for_later(candidate)
        except Exception as exc:
            self._on_rnd_recovery_failed(str(exc))
        finally:
            self._rnd_recovery.enable()
            if self._rnd_dirty:
                self._rnd_recovery.schedule()
            self._run_automation_trigger("app_start")

    def _rnd_recovery_snapshot(self):
        self._rnd_widget.session.saved_app_version = __version__
        return rnd_persistence_snapshot(
            self._rnd_widget.session,
            self._rnd_widget.photo_store,
        )

    def _on_rnd_state_changed(self) -> None:
        if self._rnd_widget.session.is_empty():
            self._rnd_dirty = False
        else:
            self._rnd_dirty = True
        self._rnd_recovery.schedule()

    def _on_rnd_selection_changed(self) -> None:
        self._rnd_recovery.schedule()

    def _on_rnd_recovery_saved(self) -> None:
        self._rnd_widget.set_recovery_warning("")
        if self._restored_recovery_candidate is not None:
            self._rnd_recovery.discard(self._restored_recovery_candidate)
            self._restored_recovery_candidate = None

    def _on_rnd_recovery_failed(self, error: str) -> None:
        self._rnd_widget.set_recovery_warning("R&D recovery save failed")
        self._log_event("ERROR", "rnd", "R&D recovery save failed", error=error)

    def _save_rnd_session(self) -> bool:
        default_dir = self._rnd_default_dir()
        default_path = default_dir / f"fastgraph-rnd-session{RND_SESSION_EXTENSION}"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save R&D Session",
            str(default_path),
            f"Fastgraph R&D Session (*{RND_SESSION_EXTENSION});;JSON Files (*.json);;All Files (*)",
        )
        if not path_str:
            return False
        path = ensure_rnd_session_extension(Path(path_str))
        self._rnd_widget.session.saved_app_version = __version__
        try:
            save_rnd_session(
                self._rnd_widget.session,
                self._rnd_widget.photo_store,
                path,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Save Failed", f"Could not save the R&D session.\n\n{exc}")
            return False
        self._settings.set("rnd_session_directory", str(path.parent))
        self._settings_widget.refresh_from_settings()
        self._statusbar.showMessage(f"Saved R&D session: {path}")
        self._log_event("INFO", "rnd", "R&D session saved", path=str(path))
        self._rnd_dirty = False
        return True

    def _load_rnd_session(self) -> None:
        default_dir = self._rnd_default_dir()
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Load R&D Session",
            str(default_dir),
            "Fastgraph R&D Session (*.fastgraph-rnd.json *.json);;All Files (*)",
        )
        if not path_str:
            return
        try:
            session_path = Path(path_str)
            incoming, missing_photos = load_rnd_session(
                session_path,
                self._rnd_widget.photo_store,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Load Failed", f"Could not load R&D session.\n\n{exc}")
            return

        mode = self._choose_rnd_load_mode()
        if mode == "cancel":
            return
        if mode == "clear":
            if not self._rnd_widget.session.is_empty():
                save_choice = QMessageBox.question(
                    self,
                    "Save Current R&D Session?",
                    "Save the current R&D session before clearing it?",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Yes,
                )
                if save_choice == QMessageBox.StandardButton.Cancel:
                    return
                if save_choice == QMessageBox.StandardButton.Yes and not self._save_rnd_session():
                    return
            self._rnd_widget.replace_session(incoming)
            self._rnd_dirty = False
        else:
            self._rnd_widget.merge_session(incoming)
            self._rnd_dirty = not self._rnd_widget.session.is_empty()
        self._settings.set("rnd_session_directory", str(Path(path_str).parent))
        self._settings_widget.refresh_from_settings()
        self._statusbar.showMessage(f"Loaded R&D session: {path_str}")
        self._log_event("INFO", "rnd", "R&D session loaded", path=path_str, mode=mode)
        if missing_photos:
            QMessageBox.warning(
                self,
                "Missing R&D Photos",
                f"{len(missing_photos)} photo attachment(s) could not be found beside this session. "
                "They will remain listed as unavailable.",
            )

    def _choose_rnd_load_mode(self) -> str:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle("Load R&D Session")
        dialog.setText("How should this R&D session be loaded?")
        clear_btn = dialog.addButton("Clear current session and load", QMessageBox.ButtonRole.AcceptRole)
        add_btn = dialog.addButton("Add to current session", QMessageBox.ButtonRole.ActionRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is clear_btn:
            return "clear"
        if clicked is add_btn:
            return "add"
        return "cancel"

    @staticmethod
    def _safe_filename(value: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in " ._-()" else "_" for ch in value).strip()
        return safe or "R&D Measurement"

    def _resolve_export_path(
        self,
        requested_path: Optional[str],
        filename: str,
        title: str,
        file_filter: str = "Text Files (*.txt);;All Files (*)",
    ) -> Optional[Path]:
        if requested_path:
            path = Path(requested_path).expanduser()
            if path.exists() and path.is_dir():
                path = path / filename
            if not path.parent.exists():
                raise ValueError(f"Export directory does not exist: {path.parent}")
            if path.exists():
                choice = QMessageBox.question(
                    self,
                    "Confirm Overwrite",
                    f"Overwrite existing file?\n\n{path}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if choice != QMessageBox.StandardButton.Yes:
                    return None
            return path
        default_dir = self._export_dir_input.text().strip() or str(
            self._settings.get("export_directory") or ""
        )
        default_path = str(Path(default_dir) / filename) if default_dir else filename
        path_str, _ = QFileDialog.getSaveFileName(
            self, title, default_path, file_filter
        )
        return Path(path_str) if path_str else None

    def _export_average(self, requested_path: Optional[str] = None) -> None:
        curve = self._bottom_curve_for_display_and_export()
        if curve is None:
            QMessageBox.information(self, "Nothing to Export", "No averaged curve available yet.")
            return

        compensated = self._is_hrtf_active()
        two_channel = bool(getattr(self, "_two_channel_enabled", False))
        channel_label = self._active_measure_label() if two_channel else ""
        export_session = self._active_measure_session() if two_channel else self._session
        active_count = (
            self._active_measure_count() if two_channel else len(self._kept_curves)
        )
        filename = build_filename(
            self._session,
            compensated=compensated,
            channel_label=channel_label,
        )
        path = MainWindow._resolve_export_path(self, requested_path, filename, "Export Average")
        if path is None:
            return
        export_dir = str(path.parent)
        self._export_dir_input.setText(export_dir)
        self._settings.set("export_directory", export_dir)

        freqs, mag_db = curve
        try:
            export_curve(
                freqs=freqs,
                mag_db=mag_db,
                session=export_session,
                output_path=path,
                compensated=compensated,
                hrtf=self._hrtf if compensated else None,
                n_sweeps=active_count,
            )
            self._statusbar.showMessage(f"Exported average: {path}")
            if hasattr(self, "_log_event"):
                self._log_event("INFO", "export", "Average exported", path=str(path), compensated=compensated)
            if hasattr(self, "_run_automation_trigger"):
                self._run_automation_trigger("export_complete")
        except Exception as exc:
            if hasattr(self, "_log_event"):
                self._log_event("ERROR", "export", f"Average export failed: {exc}")
            QMessageBox.warning(self, "Export Error", str(exc))

    def _export_variation(self, requested_path: Optional[str] = None) -> None:
        two_channel = bool(getattr(self, "_two_channel_enabled", False))
        active_variation = (
            self._active_measure_variation() if two_channel else self._variation
        )
        if active_variation is None:
            QMessageBox.information(self, "Nothing to Export", "No variation band available yet.")
            return

        compensated = self._is_hrtf_active()
        channel_label = self._active_measure_label() if two_channel else ""
        export_session = self._active_measure_session() if two_channel else self._session
        active_count = (
            self._active_measure_count() if two_channel else len(self._kept_curves)
        )
        filename = build_variation_filename(
            self._session,
            compensated=compensated,
            channel_label=channel_label,
        )
        path = MainWindow._resolve_export_path(self, requested_path, filename, "Export Variation")
        if path is None:
            return
        export_dir = str(path.parent)
        self._export_dir_input.setText(export_dir)
        self._settings.set("export_directory", export_dir)

        freqs, p10, p25, p75, p90, median = active_variation
        try:
            export_variation(
                freqs=freqs,
                p10_db=p10,
                p25_db=p25,
                median_db=median,
                p75_db=p75,
                p90_db=p90,
                session=export_session,
                output_path=path,
                compensated=compensated,
                hrtf=self._hrtf if compensated else None,
                n_sweeps=active_count,
                smoothing_fraction=_DISPLAY_AVG_SMOOTHING,
            )
            self._statusbar.showMessage(f"Exported variation: {path}")
            if hasattr(self, "_log_event"):
                self._log_event("INFO", "export", "Variation exported", path=str(path), compensated=compensated)
            if hasattr(self, "_run_automation_trigger"):
                self._run_automation_trigger("export_complete")
        except Exception as exc:
            if hasattr(self, "_log_event"):
                self._log_event("ERROR", "export", f"Variation export failed: {exc}")
            QMessageBox.warning(self, "Export Error", str(exc))

    def _run_measure_upload_action(self) -> None:
        if self._brand_mode_active():
            self._export_all_measure_outputs()
            return
        self._upload_to_squiglink()

    def _brand_mode_active(self) -> bool:
        controller = getattr(self, "_theme_controller", None)
        return bool(controller is not None and controller.brand_mode)

    def _export_all_unavailable_reason(self) -> str:
        if self._state != AppState.IDLE:
            return "Export All is available while Measure is idle."
        active_average = (
            self._active_two_channel_average()
            if getattr(self, "_two_channel_enabled", False)
            else self._average
        )
        if active_average is None:
            return "Keep at least one measurement to create the average."
        active_count = (
            self._active_measure_count()
            if getattr(self, "_two_channel_enabled", False)
            else len(self._kept_curves)
        )
        if active_count < 2:
            return "Keep at least two measurements to create variation files."
        if self._hrtf is None:
            return "Select an HRTF to create the COMP files."
        return ""

    def _measure_export_directory(self) -> Path | None:
        configured = self._export_dir_input.text().strip()
        if configured:
            path = Path(configured).expanduser()
            if path.is_dir():
                return path
        saved = str(self._settings.get("export_directory") or "").strip()
        start = Path(saved).expanduser() if saved else Path.home()
        if not start.is_dir():
            start = start.parent if start.parent.is_dir() else Path.home()
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose Export All Directory",
            str(start),
        )
        if not selected:
            return None
        directory = Path(selected)
        self._export_dir_input.setText(str(directory))
        self._settings.set("export_directory", str(directory))
        return directory

    def _confirm_export_all_overwrite(self, conflicts: list[Path]) -> bool:
        if not conflicts:
            return True
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Overwrite Export Files?")
        dialog.setText(
            f"{len(conflicts)} Export All file(s) already exist in the selected directory."
        )
        dialog.setInformativeText(
            "\n".join(path.name for path in conflicts)
        )
        overwrite = dialog.addButton(
            "Overwrite All",
            QMessageBox.ButtonRole.AcceptRole,
        )
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        return dialog.clickedButton() is overwrite

    def _export_all_measure_outputs(self) -> None:
        reason = self._export_all_unavailable_reason()
        if reason:
            QMessageBox.information(self, "Export All Unavailable", reason)
            return
        directory = self._measure_export_directory()
        if directory is None:
            return

        hrtf = self._hrtf
        raw_average = self._average_curve_with_hrtf(None)
        comp_average = self._average_curve_with_hrtf(hrtf)
        active_average_raw = (
            self._active_two_channel_average()
            if getattr(self, "_two_channel_enabled", False)
            else self._average
        )
        active_curves = (
            self._active_measure_curves()
            if getattr(self, "_two_channel_enabled", False)
            else list(self._kept_curves)
        )
        if getattr(self, "_two_channel_enabled", False):
            raw_variation = self._variation_from_curves(
                active_curves, active_average_raw, hrtf=None
            )
            comp_variation = self._variation_from_curves(
                active_curves, active_average_raw, hrtf=hrtf
            )
        else:
            raw_variation = self._variation_from_kept_curves(hrtf=None)
            comp_variation = self._variation_from_kept_curves(hrtf=hrtf)
        if (
            hrtf is None
            or raw_average is None
            or comp_average is None
            or raw_variation is None
            or comp_variation is None
        ):
            QMessageBox.warning(
                self,
                "Export All Failed",
                "Fastgraph could not prepare all four Measure exports.",
            )
            return

        channel_label = (
            self._active_measure_label()
            if getattr(self, "_two_channel_enabled", False)
            else ""
        )
        active_count = (
            self._active_measure_count()
            if getattr(self, "_two_channel_enabled", False)
            else len(self._kept_curves)
        )
        export_session = (
            self._active_measure_session()
            if getattr(self, "_two_channel_enabled", False)
            else self._session
        )
        filenames = [
            build_filename(self._session, compensated=False, channel_label=channel_label),
            build_filename(self._session, compensated=True, channel_label=channel_label),
            build_variation_filename(self._session, compensated=False, channel_label=channel_label),
            build_variation_filename(self._session, compensated=True, channel_label=channel_label),
        ]
        destinations = [directory / name for name in filenames]
        conflicts = [path for path in destinations if path.exists()]
        if not self._confirm_export_all_overwrite(conflicts):
            return

        try:
            with tempfile.TemporaryDirectory(
                prefix=".fastgraph-export-",
                dir=directory,
            ) as temp_name:
                temp_dir = Path(temp_name)
                raw_freqs, raw_mag = raw_average
                comp_freqs, comp_mag = comp_average
                export_curve(
                    freqs=raw_freqs,
                    mag_db=raw_mag,
                    session=export_session,
                    output_path=temp_dir / filenames[0],
                    compensated=False,
                    hrtf=None,
                    n_sweeps=active_count,
                )
                export_curve(
                    freqs=comp_freqs,
                    mag_db=comp_mag,
                    session=export_session,
                    output_path=temp_dir / filenames[1],
                    compensated=True,
                    hrtf=hrtf,
                    n_sweeps=active_count,
                )
                for index, variation, compensated in (
                    (2, raw_variation, False),
                    (3, comp_variation, True),
                ):
                    freqs, p10, p25, p75, p90, median = variation
                    export_variation(
                        freqs=freqs,
                        p10_db=p10,
                        p25_db=p25,
                        median_db=median,
                        p75_db=p75,
                        p90_db=p90,
                        session=export_session,
                        output_path=temp_dir / filenames[index],
                        compensated=compensated,
                        hrtf=hrtf if compensated else None,
                        n_sweeps=active_count,
                        smoothing_fraction=_DISPLAY_AVG_SMOOTHING,
                    )
                for name, destination in zip(filenames, destinations):
                    os.replace(temp_dir / name, destination)
        except Exception as exc:
            self._log_event("ERROR", "export", "Export All failed", error=str(exc))
            QMessageBox.warning(self, "Export All Failed", str(exc))
            return

        self._export_dir_input.setText(str(directory))
        self._settings.set("export_directory", str(directory))
        self._statusbar.showMessage(f"Exported all Measure files: {directory}")
        self._log_event(
            "INFO",
            "export",
            "Export All completed",
            directory=str(directory),
            files=filenames,
        )
        self._run_automation_trigger("export_complete")
        QMessageBox.information(
            self,
            "Export All Complete",
            f"Exported to:\n{directory}\n\n" + "\n".join(filenames),
        )

    def _export_console_log(self, requested_path: Optional[str] = None) -> None:
        path = self._resolve_export_path(
            requested_path,
            "fastgraph-console.log",
            "Export Console Log",
            "Log Files (*.log *.txt);;All Files (*)",
        )
        if path is None:
            return
        self._console_events.export(path)
        self._log_event("INFO", "export", "Console log exported", path=str(path))
        if hasattr(self, "_run_automation_trigger"):
            self._run_automation_trigger("export_complete")

    def _sync_export_button(self) -> None:
        idle = self._state == AppState.IDLE
        two_channel = bool(getattr(self, "_two_channel_enabled", False))
        frequency_mode = not MainWindow._channel_balance_mode_active(self)
        active_average = (
            self._active_two_channel_average()
            if two_channel
            else self._average
        )
        active_variation = (
            self._active_measure_variation() if two_channel else self._variation
        )
        if self._bottom_view_mode() == "variation":
            self._export_btn.setText("Export Variation…")
            self._export_btn.setToolTip(
                "Export the displayed variation band as percentile columns in a tab-delimited TXT file."
            )
            export_enabled = idle and frequency_mode and active_variation is not None
        else:
            self._export_btn.setText("Export Average…")
            self._export_btn.setToolTip(
                "Export averaged FR as a REW-style TXT file."
            )
            export_enabled = idle and frequency_mode and active_average is not None
        self._export_btn.setEnabled(export_enabled)
        if hasattr(self, "_send_to_curator_btn"):
            self._send_to_curator_btn.setEnabled(export_enabled)
        if hasattr(self, "_send_to_rnd_btn"):
            unavailable = self._measure_to_rnd_unavailable_reason()
            self._send_to_rnd_btn.setEnabled(not unavailable)
            self._send_to_rnd_btn.setToolTip(
                unavailable
                or "Send the current average or all kept Var measurements to R&D."
            )
        if MainWindow._brand_mode_active(self):
            self._upload_btn.setText("Export All…")
            if hasattr(self._upload_btn, "setObjectName"):
                self._upload_btn.setObjectName("btn_export")
            if hasattr(self._upload_btn, "setRole"):
                self._upload_btn.setRole("primary")
            unavailable = self._export_all_unavailable_reason()
            self._upload_btn.setEnabled(not unavailable)
            self._upload_btn.setToolTip(
                unavailable
                or "Export RAW AVG, COMP AVG, RAW VAR, and COMP VAR to one directory."
            )
        else:
            self._upload_btn.setText("Upload to Squiglink")
            if hasattr(self._upload_btn, "setObjectName"):
                self._upload_btn.setObjectName("btn_upload")
            if hasattr(self._upload_btn, "setRole"):
                self._upload_btn.setRole("positive")
            self._upload_btn.setEnabled(idle and frequency_mode and active_average is not None)
            self._upload_btn.setToolTip("Upload the current average to Squiglink.")
        if hasattr(self, "_undo_btn"):
            self._undo_btn.setEnabled(idle and self._active_measure_count() > 0)
        if hasattr(self, "_clear_btn"):
            self._clear_btn.setEnabled(
                idle
                and (
                    bool(self._two_channel_pairs) or self._pending_pair is not None
                    if self._two_channel_enabled
                    else bool(self._kept_curves) or self._pending_curve is not None
                )
            )

    def _squiglink_endpoint(self) -> tuple[str, int]:
        host = str(self._settings.get("squiglink_host") or "").strip()
        port = int(self._settings.get("squiglink_port") or 22)
        return host, port

    def _upload_to_squiglink(self) -> None:
        curve = self._bottom_curve_for_display_and_export()
        if curve is None:
            QMessageBox.information(
                self,
                "Nothing to Upload",
                "No averaged curve available yet.",
            )
            return

        host, port = self._squiglink_endpoint()
        if not host:
            QMessageBox.warning(
                self,
                "Squiglink Not Configured",
                "Squiglink SFTP host is not configured yet. Add it later in settings.json.",
            )
            return

        self._log_event("INFO", "upload", "Squiglink upload requested", host=host, port=port)

        saved = decrypt_credentials(self._settings.get("squiglink_credentials_encrypted"))
        remember_saved = bool(self._settings.get("squiglink_remember_credentials"))
        auth = SquiglinkAuthDialog(
            self,
            initial_username=saved[0] if saved else "",
            initial_password=saved[1] if saved else "",
            remember=remember_saved,
        )
        if auth.exec() != QDialog.DialogCode.Accepted:
            return

        username = auth.username()
        password = auth.password()
        remember = auth.remember_credentials()
        self._settings.set("squiglink_remember_credentials", remember)
        if remember:
            self._settings.set(
                "squiglink_credentials_encrypted",
                encrypt_credentials(username, password),
            )
        else:
            self._settings.set("squiglink_credentials_encrypted", None)

        compensated = self._is_hrtf_active()
        channel_label = self._active_measure_label()
        required_side = (
            "R" if channel_label == "R" else "L"
        ) if self._two_channel_enabled else None
        if not self._ensure_upload_metadata(required_side=required_side):
            return
        upload_session = (
            replace(self._session, channel_side=required_side)
            if required_side is not None
            else self._session
        )
        modifier = auth.name_modifier()
        if self._two_channel_enabled and channel_label == "BOTH" and not modifier:
            modifier = "BOTH L"
        upload_stem = build_upload_name_stem(upload_session, modifier)
        phone_book_stem = build_phone_book_name_stem(upload_session, modifier)
        filename = f"{upload_stem}.txt"
        freqs, mag_db = curve

        tmp_path: Optional[Path] = None
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
                hrtf=self._hrtf if compensated else None,
                n_sweeps=self._active_measure_count(),
            )
            upload_export_sftp(
                local_path=tmp_path,
                host=host,
                port=port,
                username=username,
                password=password,
                remote_filename=filename,
                diagnostic=self._log_sftp_diagnostic,
            )
            phone_book_status = self._sync_remote_phone_book(
                host=host,
                port=port,
                username=username,
                password=password,
                phone_book_stem=phone_book_stem,
            )
            self._statusbar.showMessage("Upload to Squiglink completed successfully.")
            self._log_event("INFO", "upload", "Squiglink upload completed", filename=filename)
            QMessageBox.information(
                self,
                "Upload Complete",
                f"Upload to Squiglink completed successfully.\n\n{phone_book_status}",
            )
        except Exception as exc:
            self._statusbar.showMessage(f"Upload to Squiglink failed: {exc}")
            self._log_exception("upload", "Squiglink upload failed", exc)
            QMessageBox.warning(self, "Upload Failed", f"Upload to Squiglink failed.\n\n{exc}")
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _ensure_upload_metadata(self, *, required_side: str | None = None) -> bool:
        side = (getattr(self._session, "channel_side", "") or "").strip().upper()
        brand = (getattr(self._session, "brand", "") or "").strip()
        model = (getattr(self._session, "model", "") or "").strip()
        if brand and model and (
            required_side in {"L", "R"} or side in {"L", "R"}
        ):
            return True

        dialog = SquiglinkUploadMetadataDialog(
            self,
            initial_brand=brand,
            initial_model=model,
            initial_channel_side=required_side or side,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        self._session.brand = dialog.brand()
        self._session.model = dialog.model()
        if required_side is None:
            self._session.channel_side = dialog.channel_side()
        return True

    def _ask_phone_book_fallback_mode(self, detail_message: str) -> str:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Phone Book Unavailable")
        dialog.setText("Couldn't load remote phone_book.json.")
        dialog.setInformativeText(
            f"{detail_message}\n\nChoose how to proceed with this upload:"
        )
        create_btn = dialog.addButton(
            "Create Fresh Phone Book",
            QMessageBox.ButtonRole.AcceptRole,
        )
        fail_btn = dialog.addButton(
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

    def _sync_remote_phone_book(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        phone_book_stem: str,
    ) -> str:
        transport, sftp = open_sftp_connection(
            host=host,
            port=port,
            username=username,
            password=password,
            diagnostic=self._log_sftp_diagnostic,
        )
        try:
            try:
                self._log_event(
                    "DEBUG",
                    "squiglink",
                    "Phone book read start",
                    remote_path=PHONE_BOOK_REMOTE_PATH,
                )
                try:
                    phone_book = read_remote_phone_book(sftp, PHONE_BOOK_REMOTE_PATH)
                except (RemotePhoneBookMissingError, RemotePhoneBookInvalidError) as exc:
                    mode = self._ask_phone_book_fallback_mode(str(exc))
                    if mode == "fail":
                        raise RuntimeError(
                            f"Upload canceled because phone book could not be loaded: {exc}"
                        ) from exc
                    if mode == "skip":
                        return "Measurement uploaded. Phone book update was skipped."
                    phone_book = []

                merge_phone_book_entry(phone_book, self._session, phone_book_stem)
                write_remote_phone_book(sftp, phone_book, PHONE_BOOK_REMOTE_PATH)
                self._log_event(
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

    def closeEvent(self, event) -> None:
        if not self._confirm_rnd_close():
            event.ignore()
            return

        self._close_pass_fail_dialog()
        self._close_rnd_review_dialog()
        try:
            self._device_check_timer.stop()
        except Exception:
            pass

        try:
            self._abort_active_sweep()
        except Exception:
            pass

        try:
            self._level_monitor.stop()
        except Exception:
            pass

        try:
            self._dual_level_monitor.stop()
        except Exception:
            pass

        try:
            self._stop_channel_balance()
        except Exception:
            pass

        try:
            if self._update_check_thread is not None and self._update_check_thread.isRunning():
                self._update_check_thread.quit()
                self._update_check_thread.wait(500)
        except Exception:
            pass

        try:
            self._rnd_recovery.shutdown_clean()
        except Exception as exc:
            self._log_event("ERROR", "rnd", "R&D recovery cleanup failed", error=str(exc))

        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().closeEvent(event)

    def _confirm_rnd_close(self) -> bool:
        if self._rnd_widget.session.is_empty() or not self._rnd_dirty:
            return True
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle("Save R&D Session?")
        dialog.setText("The current R&D session has unsaved changes.")
        dialog.setInformativeText("Save the R&D session before Fastgraph closes?")
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.Save)
        result = dialog.exec()
        if result == QMessageBox.StandardButton.Cancel:
            return False
        if result == QMessageBox.StandardButton.Save:
            return self._save_rnd_session()
        return result == QMessageBox.StandardButton.Discard

    def _refresh_session_labels(self) -> None:
        if not hasattr(self, "_inputs_btn"):
            return
        output = self._current_output_device_label() if hasattr(self, "_out_dev_combo") else "Not selected"
        input_name = self._current_input_device_label() if hasattr(self, "_in_dev_combo") else "Not selected"
        channel = self._current_input_channel() + 1 if hasattr(self, "_ch_combo") else 1
        self._inputs_btn.setToolTip(
            f"Output: {output or 'Not selected'}\n"
            f"Input: {input_name or 'Not selected'}\n"
            f"Channel: {channel}"
        )

    def _refresh_window_title(self) -> None:
        self.setWindowTitle(
            f"DMS fastgraph Beta — {self._session.display_name()} @ {self._session.rig}"
        )
