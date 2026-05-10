"""
Main application window.
Orchestrates: device selectors, level meter, dual plot, queue control,
pass/fail UI, HRTF selector, settings/calibration, and export.
"""

import tempfile
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import paramiko
import sounddevice as sd
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QThread, QTimer, Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dms.audio_engine import (
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
    resolve_device_selection,
)
from dms.calibration import CalibrationStore
from dms.export import build_filename, export_curve
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
from dms.secure_store import decrypt_credentials, encrypt_credentials
from dms.session import SessionData
from dms.settings_manager import SettingsManager
from dms.squiglink import (
    PHONE_BOOK_REMOTE_PATH,
    RemotePhoneBookInvalidError,
    RemotePhoneBookMissingError,
    build_phone_book_name_stem,
    build_upload_name_stem,
    merge_phone_book_entry,
    read_remote_phone_book,
    upload_export_sftp,
    write_remote_phone_book,
)
from dms.update_checker import UpdateCheckWorker
from dms.version import __version__
from dms.ui.calibration_dialog import CalibrationDialog
from dms.ui.dual_plot_widget import DualPlotWidget
from dms.ui.level_meter import LevelMeterWidget
from dms.ui.session_dialog import SessionDialog
from dms.ui.settings_dialog import SettingsDialog
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
_MAX_SWEEP_ATTEMPTS = 3
_QUEUE_AMBIENT_WARN_DBFS = -45.0


class _SweepThread(QThread):
    def __init__(self, worker: SweepWorker, **kwargs) -> None:
        super().__init__()
        self._worker = worker
        self._kwargs = kwargs

    def run(self) -> None:
        self._worker.run(**self._kwargs)

    def abort(self) -> None:
        self._worker.abort()


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
        self._hint_label.setStyleSheet("color: #8ea1b7;")
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
        detail.setStyleSheet("color: #888;")
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
            timing_box.setStyleSheet(
                "QFrame {"
                " border: 1px solid #3a4a5c;"
                " border-radius: 6px;"
                " background-color: rgba(90, 120, 160, 0.12);"
                "}"
            )
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
            timing.setStyleSheet("color: #9fb7d1;")
            timing_box_layout.addWidget(timing)
            if warning_message:
                warning = QLabel(f"Bluetooth timing marginal - {warning_message}")
                warning.setWordWrap(True)
                warning.setStyleSheet("color: #d9b35f;")
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
            details.setWordWrap(True)
            details.setVisible(False)
            details.setStyleSheet(
                "QLabel {"
                " color: #9fb7d1;"
                " background-color: rgba(40, 55, 75, 0.35);"
                " border: 1px solid #33475f;"
                " border-radius: 6px;"
                " padding: 8px;"
                " font-family: monospace;"
                "}"
            )
            layout.addWidget(details)
            details_toggle.toggled.connect(details.setVisible)
            details_toggle.toggled.connect(lambda _checked: self.adjustSize())

        button_row = QHBoxLayout()
        button_row.setSpacing(10)

        keep_btn = QPushButton("Keep")
        keep_btn.setDefault(True)
        keep_btn.setStyleSheet(
            "QPushButton {"
            " background-color: #1f7a3f;"
            " color: white;"
            " border: 1px solid #2ca85a;"
            " border-radius: 6px;"
            " padding: 8px 16px;"
            " font-weight: 600;"
            "}"
            "QPushButton:hover { background-color: #24914a; }"
        )
        keep_btn.clicked.connect(self._accept_keep)
        button_row.addWidget(keep_btn)

        fail_btn = QPushButton("Fail / Redo")
        fail_btn.setStyleSheet(
            "QPushButton {"
            " background-color: #8d2b2b;"
            " color: white;"
            " border: 1px solid #b63b3b;"
            " border-radius: 6px;"
            " padding: 8px 16px;"
            " font-weight: 600;"
            "}"
            "QPushButton:hover { background-color: #a73333; }"
        )
        fail_btn.clicked.connect(self._accept_fail)
        button_row.addWidget(fail_btn)

        cancel_btn = QPushButton("Cancel Queue")
        cancel_btn.setStyleSheet(
            "QPushButton { padding: 8px 16px; border-radius: 6px; }"
        )
        cancel_btn.clicked.connect(self._accept_cancel)
        button_row.addWidget(cancel_btn)

        layout.addLayout(button_row)
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
        self._status.setStyleSheet("color: #ff8888;")
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
        self._status.setStyleSheet("color: #ff8888;")
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


class MainWindow(QMainWindow):
    def __init__(self, session: SessionData, settings: SettingsManager) -> None:
        super().__init__()
        self._session = session
        self._settings = settings
        self._cal_store = CalibrationStore()

        self._state = AppState.IDLE
        self._kept_curves: list[tuple[np.ndarray, np.ndarray]] = []
        self._average: Optional[tuple[np.ndarray, np.ndarray]] = None
        self._variation: Optional[
            tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]
        ] = None
        self._pending_curve: Optional[tuple[np.ndarray, np.ndarray]] = None

        self._queue_target = 0
        self._queue_index = 0
        self._current_sweep_attempts = 0

        self._hrtf: Optional[HRTFCurve] = None

        self._sweep_thread: Optional[_SweepThread] = None
        self._active_sweep_worker: Optional[SweepWorker] = None
        self._pass_fail_dialog: Optional[PassFailDialog] = None

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

        self._level_monitor = LevelMonitor()
        self._level_monitor.level_updated.connect(self._on_level_update)
        self._level_monitor.error_occurred.connect(self._on_level_error)

        self._refresh_window_title()
        self.setMinimumSize(1100, 700)

        self._build_ui()
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

        self._meter_ui_timer = QTimer(self)
        self._meter_ui_timer.timeout.connect(self._refresh_level_meter_display)
        self._meter_ui_timer.start(_METER_UPDATE_MS)

        self._device_check_timer = QTimer(self)
        self._device_check_timer.timeout.connect(self._check_devices)
        self._device_check_timer.start(1500)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self._plots = DualPlotWidget()
        self._plots.measurement_files_dropped.connect(self._import_dropped_measurement_files)
        root.addWidget(self._plots, 1)

        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        controls_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        controls_scroll.setMinimumWidth(340)
        controls_scroll.setMaximumWidth(380)
        controls_scroll.setWidget(self._build_control_panel())
        root.addWidget(controls_scroll, 0)

        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("Ready.")
        self._version_label = QLabel(f"v{__version__}")
        self._version_label.setStyleSheet("color: #8b95a6; font-size: 11px;")
        self._version_label.setToolTip("DMS Fastgraph version")
        self._statusbar.addPermanentWidget(self._version_label)
        self._feedback_btn = QPushButton("Report Bugs / Feedback")
        self._feedback_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._feedback_btn.setToolTip("Open feedback form")
        self._feedback_btn.setStyleSheet(
            "QPushButton {"
            " background-color: #6f1f1f;"
            " color: #ffd7d7;"
            " border: 1px solid #a63b3b;"
            " border-radius: 10px;"
            " padding: 2px 10px;"
            " font-size: 11px;"
            " font-weight: 600;"
            "}"
            "QPushButton:hover { background-color: #822727; }"
        )
        self._feedback_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl(
                    "https://docs.google.com/forms/d/e/1FAIpQLScHMtJluNWrJnYH2_gcqnrRyhtWF_FQnOB5msfU-NKTFAyElw/viewform?usp=publish-editor"
                )
            )
        )
        self._statusbar.addPermanentWidget(self._feedback_btn)
        self._build_update_indicator()

    def _build_control_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        bt_mode_box = QGroupBox("Measurement Mode")
        bt_mode_layout = QVBoxLayout(bt_mode_box)
        self._bluetooth_mode_toggle = ToggleSwitch("Bluetooth Headphone Mode")
        self._bluetooth_mode_toggle.setChecked(
            bool(self._settings.get("bluetooth_headphone_mode"))
        )
        self._bluetooth_mode_toggle.stateChanged.connect(
            self._on_bluetooth_mode_changed
        )
        bt_mode_layout.addWidget(self._bluetooth_mode_toggle)
        bt_hint = QLabel(
            "Applies safer timing settings for Bluetooth latency/jitter paths."
        )
        bt_hint.setWordWrap(True)
        bt_hint.setStyleSheet("color: #91a2ba;")
        bt_mode_layout.addWidget(bt_hint)
        layout.addWidget(bt_mode_box)

        self._clear_metadata_btn = QPushButton("Clear Metadata")
        self._clear_metadata_btn.clicked.connect(self._clear_metadata)
        layout.addWidget(self._clear_metadata_btn)

        session_box = QGroupBox("Session")
        sb_layout = QVBoxLayout(session_box)
        self._session_name_label = QLabel("")
        self._session_name_label.setTextFormat(Qt.TextFormat.RichText)
        self._session_rig_label = QLabel("")
        self._metadata_btn = QPushButton("Headphone Metadata…")
        self._metadata_btn.setObjectName("btn_metadata")
        self._metadata_btn.clicked.connect(self._open_metadata_dialog)
        sb_layout.addWidget(self._session_name_label)
        sb_layout.addWidget(self._session_rig_label)
        sb_layout.addWidget(self._metadata_btn)
        self._refresh_session_labels()
        layout.addWidget(self._make_collapsible_section("Session", session_box))

        dev_box = QGroupBox("Devices")
        dev_layout = QVBoxLayout(dev_box)

        dev_layout.addWidget(QLabel("Output Device:"))
        self._out_dev_combo = QComboBox()
        self._out_dev_combo.currentIndexChanged.connect(
            self._on_output_device_changed
        )
        dev_layout.addWidget(self._out_dev_combo)

        dev_layout.addWidget(QLabel("Input Device:"))
        self._in_dev_combo = QComboBox()
        self._in_dev_combo.currentIndexChanged.connect(self._on_input_device_changed)
        dev_layout.addWidget(self._in_dev_combo)

        dev_layout.addWidget(QLabel("Input Channel:"))
        self._ch_combo = QComboBox()
        self._ch_combo.currentIndexChanged.connect(self._on_channel_changed)
        dev_layout.addWidget(self._ch_combo)

        self._active_ch_label = QLabel("Active input channel: —")
        self._active_ch_label.setObjectName("label_channel_active")
        dev_layout.addWidget(self._active_ch_label)

        self._advanced_windows_drivers_toggle = ToggleSwitch("Advanced Windows Drivers")
        self._advanced_windows_drivers_toggle.setChecked(
            bool(self._settings.get("windows_advanced_audio_drivers"))
        )
        self._advanced_windows_drivers_toggle.setVisible(is_windows_audio_host())
        self._advanced_windows_drivers_toggle.stateChanged.connect(
            self._on_advanced_windows_drivers_changed
        )
        dev_layout.addWidget(self._advanced_windows_drivers_toggle)

        refresh_btn = QPushButton("Refresh Devices")
        refresh_btn.clicked.connect(self._refresh_devices)
        dev_layout.addWidget(refresh_btn)

        layout.addWidget(self._make_collapsible_section("Devices", dev_box))

        meter_box = QGroupBox("Input Level")
        meter_layout = QHBoxLayout(meter_box)
        meter_layout.setSpacing(12)
        self._level_meter = LevelMeterWidget(
            orientation=Qt.Orientation.Horizontal
        )
        self._level_status_label = QLabel("Live RMS monitor")
        self._level_status_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._level_status_label.setWordWrap(True)
        meter_layout.addWidget(self._level_meter, 1, Qt.AlignmentFlag.AlignVCenter)
        meter_layout.addWidget(self._level_status_label, 1)
        layout.addWidget(self._make_collapsible_section("Input Level", meter_box))

        queue_box = QGroupBox("Queue")
        queue_layout = QVBoxLayout(queue_box)

        n_layout = QHBoxLayout()
        n_label = QLabel("Number of measurements:")
        n_label.setStyleSheet("font-weight: 600; color: #9ad3f6;")
        n_layout.addWidget(n_label)
        self._queue_n_spin = QSpinBox()
        self._queue_n_spin.setObjectName("queue_count_spin")
        self._queue_n_spin.setRange(1, 100)
        self._queue_n_spin.setValue(int(self._settings.get("queue_count") or 5))
        self._queue_n_spin.setFixedWidth(110)
        n_layout.addWidget(self._queue_n_spin)
        queue_layout.addLayout(n_layout)

        level_layout = QHBoxLayout()
        level_label = QLabel("Output level:")
        level_label.setStyleSheet("font-weight: 600; color: #9ad3f6;")
        level_layout.addWidget(level_label)
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
        level_layout.addWidget(self._queue_level_spin)
        self._queue_level_persist_toggle = ToggleSwitch("")
        self._queue_level_persist_toggle.setChecked(persist_output_level)
        self._queue_level_persist_toggle.stateChanged.connect(
            self._on_queue_level_persist_changed
        )
        persist_layout = QVBoxLayout()
        persist_layout.setContentsMargins(0, 0, 0, 0)
        persist_layout.setSpacing(2)
        persist_layout.addWidget(
            self._queue_level_persist_toggle,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )
        self._queue_level_persist_label = QLabel("Remember this level")
        self._queue_level_persist_label.setStyleSheet("color: #d8e0ec;")
        self._queue_level_persist_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        persist_layout.addWidget(self._queue_level_persist_label)
        level_layout.addLayout(persist_layout)
        level_layout.addStretch(1)
        queue_layout.addLayout(level_layout)

        self._queue_progress_label = QLabel("Kept: 0")
        queue_layout.addWidget(self._queue_progress_label)

        self._queue_progress_bar = QProgressBar()
        self._queue_progress_bar.setRange(0, 1)
        self._queue_progress_bar.setValue(0)
        queue_layout.addWidget(self._queue_progress_bar)

        btn_row = QHBoxLayout()
        self._start_queue_btn = QPushButton("Start Queue")
        self._start_queue_btn.setObjectName("btn_start")
        self._start_queue_btn.clicked.connect(self._start_queue)
        btn_row.addWidget(self._start_queue_btn)

        self._cancel_queue_btn = QPushButton("Cancel Queue")
        self._cancel_queue_btn.setObjectName("btn_cancel")
        self._cancel_queue_btn.clicked.connect(self._cancel_queue)
        btn_row.addWidget(self._cancel_queue_btn)
        queue_layout.addLayout(btn_row)

        self._sweep_progress = QProgressBar()
        self._sweep_progress.setRange(0, 100)
        self._sweep_progress.setValue(0)
        queue_layout.addWidget(self._sweep_progress)

        self._queue_hint_label = QLabel(
            "After each sweep, pass/fail opens in a review popup."
        )
        self._queue_hint_label.setWordWrap(True)
        self._queue_hint_label.setStyleSheet("color: #888;")
        queue_layout.addWidget(self._queue_hint_label)

        layout.addWidget(self._make_collapsible_section("Queue", queue_box))

        bottom_box = QGroupBox("Bottom View")
        bottom_layout = QVBoxLayout(bottom_box)

        self._variation_toggle = ToggleSwitch("Variation Band")
        self._variation_toggle.stateChanged.connect(self._on_bottom_view_changed)
        bottom_layout.addWidget(self._variation_toggle)

        self._hrtf_toggle = ToggleSwitch("HRTF Compensation")
        self._hrtf_toggle.stateChanged.connect(self._update_plots)
        bottom_layout.addWidget(self._hrtf_toggle)

        bottom_hint = QLabel("Variation shows confidence-style spread of kept measurements.")
        bottom_hint.setWordWrap(True)
        bottom_hint.setStyleSheet("color: #91a2ba;")
        bottom_layout.addWidget(bottom_hint)

        hrtf_btn_row = QHBoxLayout()
        self._hrtf_load_btn = QPushButton("Load HRTF…")
        self._hrtf_load_btn.clicked.connect(self._load_hrtf)
        hrtf_btn_row.addWidget(self._hrtf_load_btn)

        self._hrtf_clear_btn = QPushButton("Clear")
        self._hrtf_clear_btn.clicked.connect(self._clear_hrtf)
        hrtf_btn_row.addWidget(self._hrtf_clear_btn)
        bottom_layout.addLayout(hrtf_btn_row)

        self._hrtf_label = QLabel("No HRTF loaded")
        self._hrtf_label.setWordWrap(True)
        bottom_layout.addWidget(self._hrtf_label)

        layout.addWidget(
            self._make_collapsible_section("Bottom View", bottom_box, collapsed=True),
            1,
        )

        misc_box = QGroupBox("Tools")
        misc_layout = QVBoxLayout(misc_box)

        undo_btn = QPushButton("↶ Undo Last Measurement")
        undo_btn.clicked.connect(self._undo_last_measurement)
        misc_layout.addWidget(undo_btn)
        self._undo_btn = undo_btn

        clear_btn = QPushButton("Clear All Measurements")
        clear_btn.clicked.connect(self._clear_all)
        misc_layout.addWidget(clear_btn)
        self._clear_btn = clear_btn

        settings_btn = QPushButton("Settings…")
        settings_btn.clicked.connect(self._open_settings)
        misc_layout.addWidget(settings_btn)
        self._settings_btn = settings_btn

        cal_btn = QPushButton("SPL Calibration…")
        cal_btn.clicked.connect(self._open_calibration)
        misc_layout.addWidget(cal_btn)
        self._cal_btn = cal_btn

        test_level_btn = QPushButton("Test Level…")
        test_level_btn.clicked.connect(self._open_test_level)
        misc_layout.addWidget(test_level_btn)
        self._test_level_btn = test_level_btn

        layout.addWidget(self._make_collapsible_section("Tools", misc_box, collapsed=True))

        export_box = QGroupBox("Export")
        export_layout = QVBoxLayout(export_box)

        export_dir_row = QHBoxLayout()
        export_dir_row.addWidget(QLabel("Directory:"))
        self._export_dir_input = QLineEdit()
        self._export_dir_input.setPlaceholderText("Default: choose at export")
        self._export_dir_input.setText(str(self._settings.get("export_directory") or ""))
        export_dir_row.addWidget(self._export_dir_input, 1)
        export_dir_btn = QPushButton("Browse…")
        export_dir_btn.clicked.connect(self._choose_export_directory)
        export_dir_row.addWidget(export_dir_btn)
        export_layout.addLayout(export_dir_row)

        export_btn = QPushButton("Export Average…")
        export_btn.clicked.connect(self._export)
        export_btn.setStyleSheet(
            "QPushButton {"
            " background-color: #b98616;"
            " color: white;"
            " border: 1px solid #d7a52d;"
            " border-radius: 6px;"
            " padding: 8px 14px;"
            " font-weight: 600;"
            "}"
            "QPushButton:hover { background-color: #cf981e; }"
            "QPushButton:disabled {"
            " background-color: #5b4a22;"
            " color: #b9b0a0;"
            " border: 1px solid #6b5a31;"
            "}"
        )
        export_layout.addWidget(export_btn)
        self._export_btn = export_btn

        self._upload_btn = QPushButton("Upload to Squiglink")
        self._upload_btn.clicked.connect(self._upload_to_squiglink)
        self._upload_btn.setStyleSheet(
            "QPushButton {"
            " background-color: #1d5e33;"
            " color: #d7ffe3;"
            " border: 1px solid #2f7f49;"
            " border-radius: 6px;"
            " padding: 8px 14px;"
            " font-weight: 600;"
            "}"
            "QPushButton:hover { background-color: #257743; }"
            "QPushButton:disabled {"
            " background-color: #244031;"
            " color: #88a091;"
            " border: 1px solid #345345;"
            "}"
        )
        export_layout.addWidget(self._upload_btn)
        layout.addWidget(export_box)

        layout.addStretch(1)
        return panel

    def _make_collapsible_section(
        self,
        title: str,
        content_widget: QWidget,
        collapsed: bool = False,
    ) -> QWidget:
        if isinstance(content_widget, QGroupBox):
            content_widget.setTitle("")
        section = QWidget()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(6)

        toggle = QToolButton()
        toggle.setText(title)
        toggle.setCheckable(True)
        toggle.setChecked(not collapsed)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(
            Qt.ArrowType.DownArrow if not collapsed else Qt.ArrowType.RightArrow
        )
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setStyleSheet(
            "QToolButton {"
            " background-color: #3a2612;"
            " border: 1px solid #a8741d;"
            " color: #ffdca1;"
            " border-radius: 10px;"
            " padding: 6px 10px;"
            " font-weight: 700;"
            " text-align: left;"
            "}"
            "QToolButton:hover {"
            " background-color: #4a3117;"
            " border-color: #d49c2a;"
            "}"
            "QToolButton:pressed {"
            " background-color: #2d1d0e;"
            "}"
        )

        container = QWidget()
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
        self._update_button.setVisible(False)
        self._update_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_button.setToolTip("A new app version is available.")
        self._update_button.setStyleSheet(
            "QPushButton {"
            " background-color: #2f4f2f;"
            " color: #d9fdd3;"
            " border: 1px solid #4e7c4e;"
            " border-radius: 10px;"
            " padding: 2px 10px;"
            " font-size: 12px;"
            " font-weight: 600;"
            "}"
            "QPushButton:hover { background-color: #386038; }"
        )
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
        path = self._settings.get("hrtf_path")

        if path:
            try:
                self._hrtf = HRTFCurve(path)
            except Exception:
                self._hrtf = None
                self._settings.set("hrtf_path", None)

        self._sync_hrtf_ui()

    def _sync_hrtf_ui(self) -> None:
        has_hrtf = self._hrtf is not None
        self._hrtf_toggle.setEnabled(has_hrtf)
        self._hrtf_clear_btn.setEnabled(has_hrtf)

        if has_hrtf:
            self._hrtf_label.setText(self._hrtf.path)
        else:
            self._hrtf_label.setText("No HRTF loaded")
            self._hrtf_toggle.setChecked(False)

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

        if self._state == AppState.SWEEPING:
            return

        input_device = self._current_input_device()
        if input_device is None:
            self._displayed_level_dbfs = -60.0
            self._level_meter.set_level(-60.0)
            self._level_status_label.setText("No input device selected")
            return

        try:
            self._level_monitor.start(
                device_index=input_device,
                device_label=self._current_input_device_label(),
                channel_index=self._current_input_channel(),
                fs=int(self._settings.get("sample_rate")),
                buffer_size=int(self._settings.get("buffer_size")),
            )
            self._level_status_label.setText("Live RMS monitor")
        except Exception as exc:
            self._statusbar.showMessage(f"Level monitor start failed: {exc}")

    def _on_output_device_changed(self) -> None:
        self._settings.set("output_device", self._current_output_device_setting())
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
        self._settings.set("input_device", self._current_input_device_setting())
        self._sync_windows_output_to_input()
        self._refresh_channels()
        self._start_level_monitor()
        self._apply_state_ui()

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
        self._start_level_monitor()

    def _on_level_update(self, dbfs: float) -> None:
        self._last_level_dbfs = float(dbfs)

    def _refresh_level_meter_display(self) -> None:
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

    def _on_update_up_to_date(self, _latest_version: str) -> None:
        self._pending_update_url = None
        self._update_button.setVisible(False)

    def _on_update_check_failed(self, _error: str) -> None:
        # Keep this fully non-intrusive by silently failing.
        self._pending_update_url = None
        self._update_button.setVisible(False)

    def _open_update_url(self) -> None:
        if not self._pending_update_url:
            return
        QDesktopServices.openUrl(QUrl(self._pending_update_url))

    def _apply_state_ui(self) -> None:
        idle = self._state == AppState.IDLE
        pass_fail = self._state == AppState.PASS_FAIL
        busy = self._state in {AppState.SWEEPING, AppState.QUEUE_RUNNING}

        device_ok = (
            self._current_output_device() is not None
            and self._current_input_device() is not None
            and self._ch_combo.count() > 0
            and self._selected_audio_pair_is_compatible()
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
            self._hrtf_toggle,
            self._hrtf_load_btn,
            self._hrtf_clear_btn,
            self._settings_btn,
            self._cal_btn,
            self._test_level_btn,
            self._undo_btn,
            self._clear_btn,
            self._metadata_btn,
            self._clear_metadata_btn,
            self._advanced_windows_drivers_toggle,
        ):
            widget.setEnabled(idle)

        self._start_queue_btn.setEnabled(idle and device_ok)
        self._cancel_queue_btn.setEnabled(busy or pass_fail)
        self._undo_btn.setEnabled(idle and len(self._kept_curves) > 0)
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
        self._settings.set("queue_count", self._queue_target)

        self._queue_progress_bar.setRange(0, max(1, self._queue_target))
        self._queue_progress_bar.setValue(0)
        kept_count = len(self._kept_curves)
        self._queue_progress_label.setText(
            f"Kept: {kept_count}"
        )

        self._state = AppState.QUEUE_RUNNING
        self._apply_state_ui()
        self._statusbar.showMessage("Queue started.")
        self._start_next_sweep()

    def _start_next_sweep(self) -> None:
        if not self._queue_active():
            self._state = AppState.IDLE
            self._apply_state_ui()
            return

        if self._queue_index >= self._queue_target:
            self._finish_queue()
            return

        self._current_sweep_attempts += 1
        self._state = AppState.SWEEPING
        self._apply_state_ui()
        self._sweep_progress.setValue(0)

        output_device = self._current_output_device()
        input_device = self._current_input_device()
        input_channel = self._current_input_channel()

        if output_device is None or input_device is None:
            self._on_sweep_error("Selected device is unavailable.")
            return

        if not self._selected_audio_pair_is_compatible():
            self._on_sweep_error(self._windows_audio_pair_message())
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

        self._statusbar.showMessage(
            f"Sweeping {self._queue_index + 1}/{self._queue_target} "
            f"(attempt {self._current_sweep_attempts})..."
        )

    def _on_sweep_progress(self, frac: float) -> None:
        self._sweep_progress.setValue(int(max(0.0, min(1.0, frac)) * 100.0))

    def _on_timing_quality(
        self, start_conf: float, end_conf: float, drift_ms: float, snr_db: float
    ) -> None:
        self._last_timing_quality = (start_conf, end_conf, drift_ms, snr_db)

    def _on_measurement_diagnostics(self, diagnostics: object) -> None:
        self._last_measurement_diagnostics = diagnostics

    def _on_sweep_finished(self, recording: np.ndarray, sweep: np.ndarray) -> None:
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
        self._cleanup_sweep_thread()
        self._close_pass_fail_dialog()
        self._pending_curve = None
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
        if (
            self._queue_active()
            and is_timing_quality_error
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
            retry_msg = (
                f"{message}\n\n"
                f"Measurement {self._queue_index + 1} did not meet timing quality.\n"
                f"Retry attempt {self._current_sweep_attempts + 1} of {_MAX_SWEEP_ATTEMPTS}?"
                f"{diagnostics_text}"
            )
            choice = QMessageBox.question(
                self,
                "Timing Quality Retry",
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
            self._statusbar.showMessage("Queue canceled by user after timing-quality retry prompt.")
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
        if self._state != AppState.PASS_FAIL:
            self._start_level_monitor()

    def _on_keep(self) -> None:
        if self._state != AppState.PASS_FAIL or self._pending_curve is None:
            return

        self._close_pass_fail_dialog()
        self._kept_curves.append(self._pending_curve)
        self._pending_curve = None
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
        self._pending_curve = None
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

    def _update_queue_progress(self) -> None:
        target = max(0, self._queue_target)
        self._queue_progress_bar.setRange(0, max(1, target))
        self._queue_progress_bar.setValue(min(self._queue_index, max(1, target)))
        kept_count = len(self._kept_curves)
        self._queue_progress_label.setText(
            f"Kept: {kept_count}"
        )

    def _show_pass_fail_dialog(self) -> None:
        if self._state != AppState.PASS_FAIL or self._pending_curve is None:
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

    def _recompute_variation(self) -> None:
        if not self._kept_curves:
            self._variation = None
            return

        base = self._bottom_curve_for_display()
        if base is None:
            self._variation = None
            return

        base_freqs, _ = base
        rows: list[np.ndarray] = []
        for freqs, mag in self._kept_curves:
            values = np.interp(base_freqs, freqs, mag)
            if self._is_hrtf_active():
                values = self._hrtf.apply(
                    base_freqs,
                    values,
                )
            _, values = smooth_fractional_octave(
                base_freqs,
                values,
                fraction=_DISPLAY_AVG_SMOOTHING,
            )
            rows.append(values)

        if not rows:
            self._variation = None
            return

        mat = np.vstack(rows)
        p10 = np.percentile(mat, 10, axis=0)
        p25 = np.percentile(mat, 25, axis=0)
        p75 = np.percentile(mat, 75, axis=0)
        p90 = np.percentile(mat, 90, axis=0)
        median = np.percentile(mat, 50, axis=0)
        self._variation = (base_freqs, p10, p25, p75, p90, median)

    def _bottom_curve_for_display_and_export(
        self,
    ) -> Optional[tuple[np.ndarray, np.ndarray]]:
        if self._average is None:
            return None

        freqs, mag_db = self._average
        if self._is_hrtf_active():
            corrected = self._hrtf.apply(
                freqs,
                mag_db,
            )
            return freqs, corrected

        return freqs, mag_db

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
        return "variation" if self._variation_toggle.isChecked() else "average"

    def _on_bottom_view_changed(self, *_args) -> None:
        self._update_plots()

    def _load_hrtf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load HRTF TXT",
            "",
            "Text Files (*.txt);;All Files (*)",
        )
        if not path:
            return

        try:
            self._hrtf = HRTFCurve(path)
            self._settings.set("hrtf_path", path)
            self._sync_hrtf_ui()
            self._hrtf_toggle.setChecked(True)
            self._update_plots()
            self._statusbar.showMessage(f"Loaded HRTF: {Path(path).name}")
        except Exception as exc:
            QMessageBox.warning(self, "HRTF Load Error", str(exc))

    def _clear_hrtf(self) -> None:
        self._hrtf = None
        self._settings.set("hrtf_path", None)
        self._sync_hrtf_ui()
        self._update_plots()
        self._statusbar.showMessage("HRTF cleared.")

    def _import_dropped_measurement_files(self, paths: list[str]) -> None:
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

        self._kept_curves.clear()
        self._average = None
        self._variation = None
        self._pending_curve = None
        self._queue_target = 0
        self._queue_index = 0
        self._current_sweep_attempts = 0
        self._plots.clear_all()
        self._update_queue_progress()
        self._sweep_progress.setValue(0)
        self._statusbar.showMessage("All measurements cleared.")

    def _undo_last_measurement(self) -> None:
        if self._state != AppState.IDLE:
            QMessageBox.information(
                self,
                "Busy",
                "Undo is only available while idle.",
            )
            return

        if not self._kept_curves:
            return

        self._kept_curves.pop()
        self._recompute_average()
        self._recompute_variation()
        self._update_queue_progress()
        self._update_plots()
        self._statusbar.showMessage("Last kept measurement removed.")

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._settings, self)
        if dlg.exec():
            self._start_level_monitor()

    def _open_metadata_dialog(self) -> None:
        dlg = SessionDialog(
            self._settings,
            self,
            initial_session=self._session,
        )
        if dlg.exec():
            self._session = dlg.session_data()
            self._refresh_session_labels()
            self._refresh_window_title()
            self._statusbar.showMessage("Headphone metadata updated.")

    def _clear_metadata(self) -> None:
        self._session = SessionData(
            rig="Unknown Rig",
            brand="Unknown",
            model="Unknown",
        )
        self._refresh_session_labels()
        self._refresh_window_title()
        self._statusbar.showMessage("Headphone metadata cleared.")

    def _on_queue_level_changed(self, value: float) -> None:
        clamped = max(-120.0, min(0.0, float(value)))
        if abs(clamped - float(value)) > 1e-9:
            self._queue_level_spin.blockSignals(True)
            self._queue_level_spin.setValue(clamped)
            self._queue_level_spin.blockSignals(False)
        if self._queue_level_persist_toggle.isChecked():
            self._settings.set("queue_output_level_db", clamped)

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
        curve = self._bottom_curve_for_display_and_export()
        if curve is None:
            QMessageBox.information(
                self,
                "Nothing to Export",
                "No averaged curve available yet.",
            )
            return

        compensated = self._is_hrtf_active()
        filename = build_filename(self._session, compensated=compensated)

        default_dir = self._export_dir_input.text().strip() or str(
            self._settings.get("export_directory") or ""
        )
        default_path = str(Path(default_dir) / filename) if default_dir else filename
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Export Average",
            default_path,
            "Text Files (*.txt);;All Files (*)",
        )
        if not path_str:
            return
        export_dir = str(Path(path_str).parent)
        self._export_dir_input.setText(export_dir)
        self._settings.set("export_directory", export_dir)

        freqs, mag_db = curve
        try:
            export_curve(
                freqs=freqs,
                mag_db=mag_db,
                session=self._session,
                output_path=Path(path_str),
                compensated=compensated,
                hrtf=self._hrtf if compensated else None,
                n_sweeps=len(self._kept_curves),
            )
            self._statusbar.showMessage(f"Exported: {path_str}")
        except Exception as exc:
            QMessageBox.warning(self, "Export Error", str(exc))

    def _sync_export_button(self) -> None:
        self._export_btn.setText("Export Average…")
        self._export_btn.setToolTip(
            "Export averaged FR as a REW-style TXT file (available in all bottom-view modes)."
        )
        enabled = self._state == AppState.IDLE and self._average is not None
        self._export_btn.setEnabled(enabled)
        self._upload_btn.setEnabled(enabled)

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
        if not self._ensure_upload_metadata():
            return
        upload_stem = build_upload_name_stem(self._session, auth.name_modifier())
        phone_book_stem = build_phone_book_name_stem(self._session, auth.name_modifier())
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
                session=self._session,
                output_path=tmp_path,
                compensated=compensated,
                hrtf=self._hrtf if compensated else None,
                n_sweeps=len(self._kept_curves),
            )
            upload_export_sftp(
                local_path=tmp_path,
                host=host,
                port=port,
                username=username,
                password=password,
                remote_filename=filename,
            )
            phone_book_status = self._sync_remote_phone_book(
                host=host,
                port=port,
                username=username,
                password=password,
                phone_book_stem=phone_book_stem,
            )
            self._statusbar.showMessage("Upload to Squiglink completed successfully.")
            QMessageBox.information(
                self,
                "Upload Complete",
                f"Upload to Squiglink completed successfully.\n\n{phone_book_status}",
            )
        except Exception as exc:
            self._statusbar.showMessage(f"Upload to Squiglink failed: {exc}")
            QMessageBox.warning(self, "Upload Failed", f"Upload to Squiglink failed.\n\n{exc}")
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _ensure_upload_metadata(self) -> bool:
        side = (getattr(self._session, "channel_side", "") or "").strip().upper()
        brand = (getattr(self._session, "brand", "") or "").strip()
        model = (getattr(self._session, "model", "") or "").strip()
        if brand and model and side in {"L", "R"}:
            return True

        dialog = SquiglinkUploadMetadataDialog(
            self,
            initial_brand=brand,
            initial_model=model,
            initial_channel_side=side,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        self._session.brand = dialog.brand()
        self._session.model = dialog.model()
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
        transport = paramiko.Transport((host, int(port)))
        try:
            transport.connect(username=username, password=password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            try:
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
                return "Phone book updated successfully."
            finally:
                sftp.close()
        finally:
            transport.close()

    def closeEvent(self, event) -> None:
        self._close_pass_fail_dialog()
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
            if self._update_check_thread is not None and self._update_check_thread.isRunning():
                self._update_check_thread.quit()
                self._update_check_thread.wait(500)
        except Exception:
            pass

        super().closeEvent(event)

    def _refresh_session_labels(self) -> None:
        self._session_name_label.setText(f"<b>{self._session.display_name()}</b>")
        self._session_rig_label.setText(f"Rig: {self._session.rig}")

    def _refresh_window_title(self) -> None:
        self.setWindowTitle(
            f"DMS fastgraph Beta — {self._session.display_name()} @ {self._session.rig}"
        )
