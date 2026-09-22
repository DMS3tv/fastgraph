"""
Main application window.
Orchestrates: device selectors, level meter, dual plot, queue control,
pass/fail UI, HRTF selector, settings/calibration, and export.
"""

import contextlib
import os
import re
import shlex
import sys
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import numpy as np
import sounddevice as sd
from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import QDesktopServices, QFontMetrics, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QStyle,
    QStyleOptionButton,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtWidgets import (
    QPushButton as NativePushButton,
)

from dms.audio_engine import (
    DevicePoller,
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
from dms.automation import AutomationDefinition, AutomationStep, default_automation_directory
from dms.calibration import CalibrationStore
from dms.channel_balance import ChannelBalanceEngine, frequency_limit
from dms.comparison import (
    OFFSET_MODES,
    ReferenceLayer,
    delta_curve,
    deviation_score,
    format_deviation_summary,
    load_reference_from_measure_session,
    load_reference_from_txt,
    load_target_curve,
)
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
from dms.measure_persistence import (
    MEASURE_SESSION_EXTENSION,
    MeasureSessionLoadError,
    ensure_measure_session_extension,
    load_measure_session,
    save_measure_session,
)
from dms.measure_persistence import (
    same_session_file as same_measure_session_file,
)
from dms.measure_queue import MeasurementQueue, QueueState
from dms.measure_recovery import MeasureRecoveryCandidate, MeasureRecoveryManager
from dms.measure_session import MeasureSession, UnsupportedMeasureSessionVersion
from dms.measurement_alignment import (
    MeasurementWarningReason,
    format_diagnostics_summary,
    is_device_failure,
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
    HarmonicAnalysis,
    absolute_spl_offset_db,
    compute_frequency_response,
    compute_rms_average,
    deconvolve_sweep,
    downsample_to_log_points,
    generate_log_sweep,
    harmonic_responses,
    normalize_at_1khz,
    smooth_fractional_octave,
)
from dms.rnd.models import (
    RnDGroup,
    RnDMeasurement,
    generate_measurement_name,
    measurement_session_data,
    session_snapshot,
)
from dms.rnd.models import (
    group_variation as rnd_group_variation,
)
from dms.rnd.persistence import (
    RND_SESSION_EXTENSION,
    ensure_rnd_session_extension,
    load_rnd_session,
    same_session_file,
    save_rnd_session,
)
from dms.rnd.persistence import (
    session_snapshot as rnd_persistence_snapshot,
)
from dms.rnd.recovery import RecoveryCandidate, RnDRecoveryManager
from dms.secure_store import decrypt_credentials, encrypt_credentials
from dms.session import SessionData
from dms.settings_manager import SettingsManager, config_dir
from dms.shortcuts import SHORTCUT_ACTIONS, shortcut_bindings_from_settings
from dms.squiglink import (
    DEFAULT_CONNECT_TIMEOUT,
    PHONE_BOOK_REMOTE_PATH,
    RemotePhoneBookInvalidError,
    RemotePhoneBookMissingError,
    build_phone_book_name_stem,
    build_upload_name_stem,
    merge_phone_book_entry,
    open_sftp_connection,
    read_remote_phone_book,
    write_remote_phone_book,
)
from dms.theme import ThemeController, theme_trace_palette
from dms.two_channel import (
    TwoChannelCurvePair,
    channel_curves,
    combined_pair_curves,
    curve_label_for_selection,
    shared_normalize_pair_at_1khz,
)
from dms.ui.automation_widget import AutomationWidget
from dms.ui.calibration_dialog import CalibrationDialog
from dms.ui.console_widget import ConsoleWidget
from dms.ui.curator_widget import CuratorWidget
from dms.ui.eq_suggestion_dialog import EqSuggestionDialog
from dms.ui.level_meter import LevelMeterWidget
from dms.ui.measure_workspace import MeasureWorkspace
from dms.ui.modern_button import ModernButton as QPushButton
from dms.ui.modern_spinbox import (
    ModernDoubleSpinBox as QDoubleSpinBox,
)
from dms.ui.modern_spinbox import (
    ModernSpinBox as QSpinBox,
)
from dms.ui.rnd_widget import RnDWidget
from dms.ui.session_dialog import SessionEditor
from dms.ui.settings_dialog import SettingsWidget
from dms.ui.squiglink_worker import SquiglinkUploadWorker
from dms.ui.sweep_runner import SweepRunner
from dms.ui.theme_surface import DitherSurface
from dms.ui.toggle_switch import ToggleSwitch
from dms.update_checker import UpdateCheckWorker, is_allowed_feed_url, is_allowed_release_url
from dms.version import __version__


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
#: Harmonic analysis needs a clean recording; below this SNR the distortion
#: packets are indistinguishable from the noise floor, so nothing is computed.
_DISTORTION_MIN_SNR_DB = 20.0
#: Band the pass/fail summary reports THD over.
_DISTORTION_SUMMARY_F_MIN = 100.0
_DISTORTION_SUMMARY_F_MAX = 10000.0
#: A/B reference layers held alongside the measurement. Three is as many as
#: the bottom viewport can carry before the average stops being the subject.
_MAX_REFERENCE_LAYERS = 3


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
        layout.addWidget(
            QLabel("Fastgraph found R&D session data that was not cleared during a normal exit.")
        )
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


class MeasureRecoveryDialog(QDialog):
    """Select and act on a recoverable Measure session.

    Deliberately the R&D dialog's twin: the two workspaces recover the same
    way, so anything learned in one reads the same in the other.
    """

    RESTORE = "restore"
    DISCARD = "discard"
    KEEP = "keep"

    def __init__(
        self,
        candidates: list[MeasureRecoveryCandidate],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Recover Measure Session")
        self.setModal(True)
        self.action = self.KEEP
        self._candidates = candidates

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Fastgraph found Measure session data that was not cleared during a normal exit."
            )
        )
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

    def selected_candidate(self) -> MeasureRecoveryCandidate:
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
    "start_alignment_confidence_min": ("float", 0.0, 30.0),
    "sweep_noise_margin_min_db": ("float", 0.0, 60.0),
    "snr_warn_db": ("float", 0.0, 60.0),
    "end_marker_confidence_min": ("float", 2.0, 30.0),
    "timing_drift_max_ms": ("float", 5.0, 250.0),
    "bluetooth_mode": ("bool",),
    "queue_count": ("int", 1, 100),
    "output_level": ("float", -120.0, 0.0),
}

#: Triggers an automation step can raise itself. Queueing these would let an
#: automation re-trigger itself without end, so the re-entrancy guard stays.
_AUTOMATION_REENTRANT_TRIGGERS = {"export_complete", "app_error"}
_AUTOMATION_QUEUE_LIMIT = 32

#: Console commands that do what a risky action does, spelled as text.
_RISKY_CONSOLE_PREFIXES = (
    "measure",
    "export",
    "settings set",
    "curator export",
    "rnd",
)

#: ``{name}`` placeholders, substituted in one pass so a value that contains
#: another variable's placeholder is never expanded a second time.
_AUTOMATION_VARIABLE_PATTERN = re.compile(r"\{([^{}]+)\}")

_CONSOLE_SETTING_KEYS = {
    "bluetooth_mode": "bluetooth_headphone_mode",
    "output_level": "queue_output_level_db",
}


class _EventStatusBar(QStatusBar):
    def __init__(self, events: ConsoleEventStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._events = events

    def showMessage(self, message: str, timeout: int = 0) -> None:
        super().showMessage(message, timeout)
        severity = (
            "WARNING"
            if any(
                word in message.lower()
                for word in ("failed", "error", "warning", "aborted", "canceled", "unavailable")
            )
            else "INFO"
        )
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
        snapshot_fn: Callable[[], tuple[float, float | None, str]],
        play_noise_fn: Callable[[], str | None] | None = None,
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


def thd_band_summary(
    analysis: HarmonicAnalysis | None,
) -> tuple[float, float, float] | None:
    """(median THD %, max THD %, frequency of the max) over 100 Hz - 10 kHz.

    Returns None when there is nothing measurable in the band — the summary
    line is then simply omitted rather than showing NaNs.
    """
    if analysis is None:
        return None
    freqs = np.asarray(getattr(analysis, "freqs", []), dtype=float)
    percent = np.asarray(getattr(analysis, "thd_percent", []), dtype=float)
    if freqs.size == 0 or percent.size != freqs.size:
        return None
    band = (
        (freqs >= _DISTORTION_SUMMARY_F_MIN)
        & (freqs <= _DISTORTION_SUMMARY_F_MAX)
        & np.isfinite(percent)
    )
    if not np.any(band):
        return None
    values = percent[band]
    band_freqs = freqs[band]
    peak = int(np.argmax(values))
    return float(np.median(values)), float(values[peak]), float(band_freqs[peak])


class PassFailDialog(QDialog):
    KEEP = "keep"
    FAIL = "fail"
    CANCEL = "cancel"

    def __init__(
        self,
        index: int,
        total: int,
        timing_quality: tuple[float, float, float, float] | None = None,
        diagnostics: object | None = None,
        distortion: HarmonicAnalysis | None = None,
        deviation_summary: str | None = None,
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

        summary = QLabel(f"Review measurement {index} of {total} and choose whether to keep it.")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        detail = QLabel("The latest sweep is shown in teal in the top plot while you decide.")
        detail.setWordWrap(True)
        detail.setProperty("tone", "muted")
        layout.addWidget(detail)

        if timing_quality is not None:
            start_conf, end_conf, drift_ms, snr_db = timing_quality
            bluetooth_mode = bool(getattr(diagnostics, "bluetooth_headphone_mode", False))
            warning_message = (
                getattr(diagnostics, "warning_message", None) if diagnostics is not None else None
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
                quality_text = f"Sweep Quality - alignment: {start_conf:.1f}, SNR: {snr_db:.1f} dB"
            timing = QLabel(quality_text)
            timing.setWordWrap(True)
            timing.setProperty("tone", "muted")
            timing_box_layout.addWidget(timing)
            if warning_message:
                warning_reason = getattr(diagnostics, "warning_reason", None)
                warning_title = (
                    "Signal warning"
                    if warning_reason == MeasurementWarningReason.LOW_SNR
                    else "Bluetooth timing marginal"
                )
                warning = QLabel(f"{warning_title} - {warning_message}")
                warning.setWordWrap(True)
                warning.setProperty("tone", "warning")
                timing_box_layout.addWidget(warning)
            summary = thd_band_summary(distortion)
            if summary is not None:
                median_pct, max_pct, max_freq = summary
                thd_label = QLabel(
                    f"THD 100 Hz-10 kHz: {median_pct:.2f} % "
                    f"(max {max_pct:.2f} % @ {max_freq:.0f} Hz)"
                )
                thd_label.setWordWrap(True)
                thd_label.setProperty("tone", "muted")
                timing_box_layout.addWidget(thd_label)
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

        if deviation_summary:
            # How far this one sweep sits from the loaded target. Muted and
            # monospaced: it is context for the decision, not the decision.
            deviation_box = QFrame()
            deviation_box.setObjectName("diagnostic_box")
            deviation_layout = QVBoxLayout(deviation_box)
            deviation_layout.setContentsMargins(10, 8, 10, 8)
            deviation_layout.setSpacing(4)
            heading = QLabel("Deviation from target")
            heading.setProperty("tone", "muted")
            deviation_layout.addWidget(heading)
            deviation_label = QLabel(deviation_summary)
            deviation_label.setObjectName("diagnostic_details")
            deviation_label.setProperty("tone", "muted")
            deviation_label.setTextFormat(Qt.TextFormat.PlainText)
            deviation_layout.addWidget(deviation_label)
            layout.addWidget(deviation_box)

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
        timing_quality: tuple[float, float, float, float] | None = None,
        diagnostics: object | None = None,
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
            f"Previous kept measurement: {previous_name}"
            if previous_name
            else "Previous kept measurement: none"
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
        self._notes.setPlaceholderText(
            "Change notes, design/sample details, pads, EQ, fixture notes..."
        )
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
        layout.addWidget(
            QLabel("Optional. Type here if you're using different tips, pads, EQ modes, etc")
        )
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
        return (
            button.style()
            .sizeFromContents(
                QStyle.ContentsType.CT_PushButton,
                option,
                text_size,
                button,
            )
            .width()
        )

    def refresh_segment_widths(self) -> None:
        self._width_refresh_pending = False
        widths = []
        for button in (self.frequency_button, self.balance_button):
            required_width = max(
                self._required_width_for_state(button, state) for state in self._WIDTH_STATES
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
    # ------------------------------------------------------------------
    # Queue state shims
    #
    # The measurement queue state lives in ``self._queue`` (a pure
    # ``MeasurementQueue``). These properties keep the historical field names
    # so existing code and tests that read or assign them keep working; each
    # forwards to the queue object.
    # ------------------------------------------------------------------

    @property
    def _state(self) -> str:
        return self._queue.state.value

    @_state.setter
    def _state(self, value: object) -> None:
        raw = value.value if isinstance(value, QueueState) else str(value)
        self._queue.state = QueueState(raw)

    @property
    def _queue_target(self) -> int:
        return self._queue.target

    @_queue_target.setter
    def _queue_target(self, value: int) -> None:
        self._queue.target = int(value)

    @property
    def _queue_index(self) -> int:
        return self._queue.index

    @_queue_index.setter
    def _queue_index(self, value: int) -> None:
        self._queue.index = int(value)

    @property
    def _current_sweep_attempts(self) -> int:
        return self._queue.attempts

    @_current_sweep_attempts.setter
    def _current_sweep_attempts(self, value: int) -> None:
        self._queue.attempts = int(value)

    @property
    def _two_channel_stage(self) -> int:
        return self._queue.stage

    @_two_channel_stage.setter
    def _two_channel_stage(self, value: int) -> None:
        self._queue.stage = int(value)

    @property
    def _start_second_pair_stage(self) -> bool:
        return self._queue.start_second_stage

    @_start_second_pair_stage.setter
    def _start_second_pair_stage(self, value: bool) -> None:
        self._queue.start_second_stage = bool(value)

    @property
    def _pending_curve(self):
        return self._queue.pending_curve

    @_pending_curve.setter
    def _pending_curve(self, value) -> None:
        self._queue.pending_curve = value

    @property
    def _pending_pair(self):
        return self._queue.pending_pair

    @_pending_pair.setter
    def _pending_pair(self, value) -> None:
        self._queue.pending_pair = value

    @property
    def _pending_pair_first_raw(self):
        return self._queue.pending_pair_first_raw

    @_pending_pair_first_raw.setter
    def _pending_pair_first_raw(self, value) -> None:
        self._queue.pending_pair_first_raw = value

    @property
    def _pending_pair_first_diagnostics(self):
        return self._queue.pending_pair_first_diagnostics

    @_pending_pair_first_diagnostics.setter
    def _pending_pair_first_diagnostics(self, value) -> None:
        self._queue.pending_pair_first_diagnostics = value

    @property
    def _last_timing_quality(self):
        return self._queue.last_timing_quality

    @_last_timing_quality.setter
    def _last_timing_quality(self, value) -> None:
        self._queue.last_timing_quality = value

    @property
    def _last_measurement_diagnostics(self):
        return self._queue.last_diagnostics

    @_last_measurement_diagnostics.setter
    def _last_measurement_diagnostics(self, value) -> None:
        self._queue.last_diagnostics = value

    def __init__(
        self,
        session: SessionData,
        settings: SettingsManager,
        theme_controller: ThemeController | None = None,
    ) -> None:
        super().__init__()
        self._queue = MeasurementQueue(max_attempts=_MAX_SWEEP_ATTEMPTS)
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
        # dB SPL without a calibration falls back to reference mode; the note
        # is shown once rather than after every sweep.
        self._spl_uncalibrated_warned = False

        self._state = AppState.IDLE
        self._kept_curves: list[tuple[np.ndarray, np.ndarray]] = []
        self._average: tuple[np.ndarray, np.ndarray] | None = None
        self._variation: (
            tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None
        ) = None
        self._pending_curve: tuple[np.ndarray, np.ndarray] | None = None
        # Per-capture metadata kept positionally beside the curves, so a saved
        # session carries the diagnostics, timing and distortion the review
        # dialog showed. ``_kept_pair_meta`` is bookkeeping only: a kept pair
        # already carries its own per-channel diagnostics.
        self._kept_sweep_meta: list[dict] = []
        self._kept_pair_meta: list[dict] = []
        self._two_channel_pairs: list[TwoChannelCurvePair] = []
        self._pending_pair: TwoChannelCurvePair | None = None
        self._pending_pair_first_raw: tuple[np.ndarray, np.ndarray] | None = None
        self._pending_pair_first_diagnostics: object | None = None
        self._two_channel_stage = 0
        self._start_second_pair_stage = False
        self._two_channel_averages: dict[str, object] = {}
        self._two_channel_variations: dict[str, object] = {}
        self._two_channel_enabled = bool(self._settings.get("measure_two_channel_enabled"))
        bottom_mode = str(self._settings.get("measure_two_channel_bottom_mode") or "combined")
        self._two_channel_bottom_mode = "separate" if bottom_mode == "separate" else "combined"
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

        self._hrtf: HRTFCurve | None = None

        # Kept for compatibility with code that inspects them; the runner owns
        # the real thread and worker.
        self._sweep_thread: _SweepThread | None = None
        self._active_sweep_worker: SweepWorker | None = None
        self._sweep_runner = SweepRunner(self)
        self._sweep_runner.idle.connect(self._on_sweep_thread_finished)
        self._devices_dirty = False
        # Harmonic analysis of the most recently kept sweep. The queue's own
        # last_distortion is cleared by the reset that follows Keep, so the
        # overlay would otherwise vanish the moment a sweep is accepted.
        self._kept_distortion: HarmonicAnalysis | None = None
        self._pass_fail_dialog: PassFailDialog | None = None
        self._rnd_review_dialog: RnDReviewDialog | None = None
        self._rnd_sweep_active = False

        self._last_level_dbfs = -120.0
        self._displayed_level_dbfs = -60.0
        self._last_input_devices: list[tuple[int, str, int]] = []
        self._last_output_devices: list[tuple[int, str, int]] = []
        self._input_devices_by_index: dict[int, dict] = {}
        self._output_devices_by_index: dict[int, dict] = {}
        self._input_device_labels_by_index: dict[int, str] = {}
        self._output_device_labels_by_index: dict[int, str] = {}
        self._last_timing_quality: tuple[float, float, float, float] | None = None
        self._last_measurement_diagnostics: object | None = None
        self._hrtf_options: list[tuple[str, str]] = []
        self._console_events = ConsoleEventStore(
            parent=self,
            log_path=config_dir() / "logs" / "fastgraph-console.log",
        )
        self._report_settings_load_problems()
        self._squiglink_upload_context: dict | None = None
        self._automation_running = False
        self._keyboard_shortcuts: list[QShortcut] = []
        self._rnd_dirty = False
        self._restored_recovery_candidate: RecoveryCandidate | None = None
        # Measure session file the workspace currently belongs to, and whether
        # it holds changes that file does not.
        self._measure_session_path: Path | None = None
        self._measure_dirty = False
        self._restored_measure_candidate: MeasureRecoveryCandidate | None = None
        # Target comparison. The target survives restarts through settings;
        # reference layers are deliberately session-only.
        self._measure_target: tuple[np.ndarray, np.ndarray] | None = None
        self._measure_target_path: Path | None = None
        self._measure_reference_layers: list[ReferenceLayer] = []

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
        # The manager appends its own ``measure/`` segment, so both workspaces
        # share one recovery root without colliding.
        self._measure_recovery = MeasureRecoveryManager(
            config_dir() / "recovery",
            parent=self,
        )
        self._measure_recovery.save_failed.connect(self._on_measure_recovery_failed)
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
        self._restore_measure_comparison_state()
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
        QTimer.singleShot(0, self._initialize_measure_recovery)

        self._meter_ui_timer = QTimer(self)
        self._meter_ui_timer.timeout.connect(self._refresh_level_meter_display)
        self._meter_ui_timer.start(_METER_UPDATE_MS)

        self._balance_ui_timer = QTimer(self)
        self._balance_ui_timer.setInterval(33)
        self._balance_ui_timer.timeout.connect(self._refresh_balance_scope)

        # Device enumeration is four PortAudio calls; it runs on the poller's
        # own thread and only reports back when the device set changed.
        self._device_poller = DevicePoller(parent=self)
        self._device_poller.devices_changed.connect(self._check_devices)
        self._sync_device_poller()
        self._device_poller.start()

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
        self._queue.two_channel = enabled
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
        self._distortion_toggle.setVisible(not balance)
        self._hrtf_toggle.setVisible(not balance)
        self._hrtf_combo.setVisible(not balance)
        self._hrtf_label.setVisible(not balance)
        self._level_mode_label.setVisible(not balance)
        self._level_mode_combo.setVisible(not balance)
        self._level_meter.setVisible(not balance)
        self._level_meter_2.setVisible(self._two_channel_enabled and not balance)
        self._level_status_label.setVisible(not balance)
        self._level_status_label_2.setVisible(self._two_channel_enabled and not balance)
        self._plots.two.set_generator_level(float(self._queue_level_spin.value()))
        self._plots.two.set_frequency_limit(frequency_limit(int(self._settings.get("sample_rate"))))
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
        self._settings.set("measure_two_channel_bottom_mode", self._two_channel_bottom_mode)
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
        if self._state != AppState.IDLE or not self._channel_balance_mode_active():
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
        sample_count = int(round(5.0 * sample_rate / max(20.0, self._balance_frequency)))
        sample_count = max(128, min(sample_count, int(0.25 * sample_rate)))
        left, right, left_db, right_db, delta_db = engine.snapshot(sample_count)
        self._plots.two.update_scope(left, right, sample_rate, left_db, right_db, delta_db)

    def _toggle_inputs_overlay(self) -> None:
        if self._inputs_overlay_open:
            self._close_inputs_overlay()
        else:
            self._open_inputs_overlay()

    def _open_inputs_overlay(self) -> None:
        self._close_metadata_overlay()
        self._inputs_overlay_open = True
        target = self._inputs_overlay_geometry(max(1, self._inputs_overlay.sizeHint().height()))
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
        self._inputs_overlay_animation.setEndValue(QRect(start.x(), start.y(), start.width(), 0))
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
        target = self._metadata_overlay_geometry(max(1, self._metadata_overlay.sizeHint().height()))
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
        self._metadata_overlay_animation.setEndValue(QRect(start.x(), start.y(), start.width(), 0))
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
                if (
                    getattr(self, "_inputs_overlay_open", False)
                    and not self._global_point_inside(self._inputs_overlay, point)
                    and not self._global_point_inside(self._inputs_btn, point)
                ):
                    self._close_inputs_overlay()
                if (
                    getattr(self, "_metadata_overlay_open", False)
                    and not self._global_point_inside(self._metadata_overlay, point)
                    and not self._global_point_inside(self._metadata_btn, point)
                ):
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
        self._statusbar.showMessage(
            "Shortcut ignored: switch to Measure or R&D to start a measurement."
        )

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
        self._bluetooth_mode_toggle.setChecked(bool(self._settings.get("bluetooth_headphone_mode")))
        self._bluetooth_mode_toggle.setToolTip(
            "Bluetooth Headphone Mode applies safer timing settings for "
            "Bluetooth latency and jitter paths."
        )
        self._bluetooth_mode_toggle.stateChanged.connect(self._on_bluetooth_mode_changed)
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

    def _report_settings_load_problems(self) -> None:
        """Tell the user when a store was damaged instead of silently defaulting.

        A corrupt settings.json used to be swallowed: folders, shortcuts and
        saved credentials came back as defaults with no explanation. The store
        now moves the damaged file aside and reports where it went; this shows
        that once, and logs the same text to the console.
        """
        problems: list[str] = []

        load_error = getattr(self._settings, "load_error", None)
        if load_error:
            problems.append(str(load_error))
            self._log_event(
                "ERROR", "settings", "Settings file was damaged", detail=str(load_error)
            )

        corrected = list(getattr(self._settings, "corrected_keys", []) or [])
        if corrected:
            problems.append(
                "These settings had unusable values and were reset to their "
                f"defaults: {', '.join(corrected)}."
            )
            self._log_event(
                "WARNING",
                "settings",
                "Settings values reset to defaults",
                keys=corrected,
            )

        cal_error = getattr(getattr(self, "_cal_store", None), "load_error", None)
        if cal_error:
            problems.append(str(cal_error))
            self._log_event(
                "ERROR", "calibration", "Calibration file was damaged", detail=str(cal_error)
            )

        if not problems:
            return

        text = "\n\n".join(problems)
        # Deferred so the warning lands on top of a drawn window rather than
        # blocking construction.
        QTimer.singleShot(
            0,
            lambda: QMessageBox.warning(self, "Saved Settings Recovered", text),
        )

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
        """Queue every automation for ``trigger`` and run them in order.

        Two automations on the same trigger used to mean the second one was
        dropped with a warning. They are queued instead and run one after the
        other. ``export_complete`` and ``app_error`` keep the old guard: those
        two are raised by automation steps themselves, so queueing them would
        let an automation re-trigger itself forever.
        """
        widget = getattr(self, "_automation_widget", None)
        if widget is None:
            return
        running = getattr(self, "_automation_running", False)
        if running and trigger in _AUTOMATION_REENTRANT_TRIGGERS:
            self._log_event(
                "DEBUG",
                "automation",
                "Automation trigger ignored while an automation is running",
                trigger=trigger,
            )
            return
        queue = self._automation_pending()
        for automation in widget.events.automations_for_trigger(trigger):
            if len(queue) >= _AUTOMATION_QUEUE_LIMIT:
                self._log_event(
                    "WARNING",
                    "automation",
                    "Automation queue is full; dropped an automation",
                    name=automation.name,
                    trigger=trigger,
                )
                break
            queue.append((automation, trigger))
        if not running:
            self._drain_automation_queue()

    def _automation_pending(self) -> list[tuple[AutomationDefinition, str]]:
        """The trigger queue, created on first use."""
        queue = getattr(self, "_automation_queue", None)
        if queue is None:
            queue = []
            self._automation_queue = queue
        return queue

    def _drain_automation_queue(self) -> None:
        """Run queued automations sequentially, never re-entering a run."""
        if getattr(self, "_automation_running", False) or getattr(
            self, "_automation_draining", False
        ):
            return
        queue = self._automation_pending()
        self._automation_draining = True
        try:
            while queue:
                automation, trigger = queue.pop(0)
                self._run_automation(automation, triggered_by=trigger)
        finally:
            self._automation_draining = False

    def _run_automation(
        self, automation: AutomationDefinition, triggered_by: str = "manual"
    ) -> None:
        if getattr(self, "_automation_running", False):
            self._log_event(
                "WARNING", "automation", "Automation already running", name=automation.name
            )
            return
        self._automation_running = True
        variables = dict(automation.variables)
        self._log_event(
            "INFO", "automation", "Automation started", name=automation.name, trigger=triggered_by
        )
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
                self._log_event(
                    "INFO", "automation", "Automation step complete", step=index, action=step.action
                )
            self._log_event("INFO", "automation", "Automation complete", name=automation.name)
        except Exception as exc:
            self._log_event(
                "ERROR", "automation", f"Automation failed: {exc}", name=automation.name
            )
            if triggered_by == "manual":
                QMessageBox.warning(self, "Automation Failed", str(exc))
        finally:
            self._automation_running = False
            self._drain_automation_queue()

    def _automation_condition_matches(
        self, step: AutomationStep, variables: dict[str, object]
    ) -> bool:
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
        """Whether this step needs the risky-action confirmation.

        ``console_command`` is judged by what it would run: the console can
        start a queue, export, or change a setting, and those are exactly the
        actions that ask first when spelled as their own action name.
        """
        if step.action == "console_command":
            command = " ".join(f"{step.target} {step.value}".split()).casefold()
            return command.startswith(_RISKY_CONSOLE_PREFIXES)
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
            raw = variables.get(target, 0)
            current = float(raw or 0)
            delta = float(value or 1)
            result = current + delta if action == "increment_variable" else current - delta
            # A counter that started as an int stays an int: "3" reads better
            # than "3.0" in a prompt, an export name, or a comparison.
            keeps_int = isinstance(raw, int) and not isinstance(raw, bool)
            if keeps_int and float(delta).is_integer():
                variables[target] = int(result)
            else:
                variables[target] = result
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
        """Replace every ``{name}`` placeholder in one pass.

        Substituting one variable at a time meant a value that itself contained
        ``{other}`` was expanded again by a later variable, so the result
        depended on dictionary order. Unknown names are left as written.
        """
        lookup = {str(key): value for key, value in variables.items()}

        def replace(match: "re.Match[str]") -> str:
            key = match.group(1)
            if key in lookup:
                return str(lookup[key])
            return match.group(0)

        return _AUTOMATION_VARIABLE_PATTERN.sub(replace, str(text or ""))

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
                    self._command_reply(
                        format_diagnostics_summary(self._last_measurement_diagnostics)
                    )
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
                self._command_reply(
                    "Unknown command. Type 'help' for available commands.", error=True
                )
        except Exception as exc:
            self._command_reply(f"Command failed: {exc}", error=True)

    @staticmethod
    def _console_help() -> str:
        return "\n".join(
            (
                "Commands:",
                "  help | clear | status | devices | diagnostics system | diagnostics last",
                "  settings list | settings get <name> | settings set <name> <value>",
                "  settings save [<name>|all]",
                "  measure start [count] [level_db] | measure pass | measure fail | measure cancel",
                "  measure session save|load <path> | measure target <path>|clear | measure eq [max_filters]",
                "  export average [path] | export variation [path] | export squiglink | export log [path]",
                "  curator help  (Curator workspace commands)",
            )
        )

    def _console_status(self) -> str:
        return "\n".join(
            (
                f"State: {self._state}",
                f"Queue: {self._queue_index}/{self._queue_target or 0}",
                f"Kept curves: {len(self._kept_curves)}",
                f"Curator layers: {len(self._curator_widget.graph_state.layers)} "
                f"({sum(layer.visible for layer in self._curator_widget.graph_state.layers)} visible)",
                f"Output: {self._current_output_device_label() or 'none'}",
                f"Input: {self._current_input_device_label() or 'none'} / channel {self._current_input_channel() + 1}",
                f"Bluetooth mode: {bool(self._settings.get('bluetooth_headphone_mode'))}",
                f"Sweep: {self._settings.get('sweep_duration')} s @ {self._settings.get('sample_rate')} Hz, buffer {self._settings.get('buffer_size')}",
            )
        )

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
                value = (
                    self._queue_level_spin.value()
                    if name == "output_level"
                    else self._settings.get(key)
                )
                lines.append(f"{name} = {value}{suffix}")
            self._command_reply("\n".join(lines))
            return
        if len(args) == 2 and args[0] == "get":
            name = args[1].lower()
            if name not in _CONSOLE_SETTING_SPECS:
                raise ValueError(f"Unknown editable setting: {name}")
            key = _CONSOLE_SETTING_KEYS.get(name, name)
            value = (
                self._queue_level_spin.value()
                if name == "output_level"
                else self._settings.get(key)
            )
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
                    saved = self._settings.save_session(
                        _CONSOLE_SETTING_KEYS.get(requested, requested)
                    )
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
            if self._channel_balance_mode_active() or self._channel_balance_active:
                raise ValueError(
                    "Switch to Frequency Response and stop Channel Balance before "
                    "starting a measurement."
                )
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
        if args[:1] == ["session"] and len(args) == 3:
            action = args[1].lower()
            if action == "save":
                path = ensure_measure_session_extension(Path(args[2]).expanduser())
                save_measure_session(self._current_measure_session(), path)
                self._measure_session_path = path
                self._clear_measure_dirty()
                self._command_reply(f"Saved Measure session: {path}")
                return
            if action == "load":
                if not self._load_measure_session(str(Path(args[2]).expanduser())):
                    raise ValueError("The Measure session was not loaded.")
                return
            raise ValueError("Usage: measure session save|load <path>")
        if args[:1] == ["target"] and len(args) == 2:
            if args[1].lower() == "clear":
                self._clear_measure_target()
                self._command_reply("Target cleared.")
                return
            if not self._load_measure_target(str(Path(args[1]).expanduser())):
                raise ValueError("The target curve was not loaded.")
            self._command_reply(f"Target loaded: {args[1]}")
            return
        if args[:1] == ["eq"] and len(args) <= 2:
            average = self._bottom_curve_for_display_and_export()
            if self._measure_target is None:
                raise ValueError("Load a target curve first: measure target <path>")
            if average is None:
                raise ValueError("No averaged measurement is available yet.")
            max_filters = int(args[1]) if len(args) == 2 else 8
            if not 1 <= max_filters <= 10:
                raise ValueError("measure eq accepts 1 to 10 filters.")
            from dms.comparison import format_eq_apo, suggest_eq

            delta = self._measure_delta_result(average)
            self._command_reply(format_eq_apo(suggest_eq(delta, max_filters=max_filters)))
            return
        raise ValueError(
            "Usage: measure start [count] [level_db]|pass|fail|cancel"
            " | measure session save|load <path> | measure target <path>|clear"
            " | measure eq [max_filters]"
        )

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
        return "\n".join(
            (
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
            )
        )

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
            self._command_reply(
                "\n".join(
                    (
                        f"Layers: {len(layers)}",
                        f"Visible: {sum(layer.visible for layer in layers)}",
                        f"Bounds: {'on' if curator.graph_state.bounds.enabled else 'off'}",
                        f"Limits: {curator.graph_state.y_min:g} to {curator.graph_state.y_max:g} dB",
                        f"25 dB/decade: {'on' if curator.graph_state.aspect_locked_25db else 'off'}",
                        f"Background: {curator.graph_state.background}",
                    )
                )
            )
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
            self._command_reply(
                f"Created Curator layer {len(curator.graph_state.layers)}: {layer.name}"
            )
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

        self._distortion_toggle = ToggleSwitch("Distortion")
        self._distortion_toggle.setToolTip(
            "Overlay THD and the 2nd/3rd harmonics of the last sweep on a "
            "secondary axis in the bottom viewport. Needs at least "
            f"{_DISTORTION_MIN_SNR_DB:.0f} dB SNR."
        )
        self._distortion_toggle.setChecked(bool(self._settings.get("measure_distortion_overlay")))
        self._distortion_toggle.stateChanged.connect(self._on_distortion_overlay_changed)
        row.addWidget(self._distortion_toggle)

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

        self._level_mode_label = QLabel("Level")
        self._level_mode_label.setProperty("tone", "muted")
        row.addWidget(self._level_mode_label)
        self._level_mode_combo = QComboBox()
        self._level_mode_combo.addItem("1 kHz ref", "ref_1khz")
        self._level_mode_combo.addItem("dB SPL", "dbspl")
        self._level_mode_combo.setCurrentIndex(1 if self._level_mode() == "dbspl" else 0)
        self._level_mode_combo.setToolTip(
            "1 kHz ref normalizes every curve to 0 dB at 1 kHz. dB SPL keeps "
            "the absolute level and needs a calibrated input device."
        )
        self._level_mode_combo.currentIndexChanged.connect(self._on_level_mode_changed)
        row.addWidget(self._level_mode_combo)

        self._compare_menu_btn = QToolButton()
        self._compare_menu_btn.setText("Compare ▾")
        self._compare_menu_btn.setProperty("menuButton", True)
        self._compare_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._compare_menu_btn.setToolTip(
            "Compare the average against a target curve or other measurements."
        )
        self._compare_menu = QMenu(self._compare_menu_btn)
        self._load_target_action = self._compare_menu.addAction("Load Target…")
        self._load_target_action.triggered.connect(lambda: self._load_measure_target())
        self._clear_target_action = self._compare_menu.addAction("Clear Target")
        self._clear_target_action.triggered.connect(self._clear_measure_target)
        self._delta_view_action = self._compare_menu.addAction("Delta View (measurement − target)")
        self._delta_view_action.setCheckable(True)
        self._delta_view_action.setChecked(bool(self._settings.get("measure_delta_view")))
        self._delta_view_action.toggled.connect(self._on_delta_view_toggled)
        self._compare_menu.addSeparator()
        self._load_reference_action = self._compare_menu.addAction("Load Reference…")
        self._load_reference_action.triggered.connect(lambda: self._load_measure_reference())
        self._clear_references_action = self._compare_menu.addAction("Clear References")
        self._clear_references_action.triggered.connect(self._clear_measure_references)
        self._compare_menu.addSeparator()
        self._eq_suggestion_action = self._compare_menu.addAction("EQ Suggestion…")
        self._eq_suggestion_action.triggered.connect(self._open_eq_suggestion)
        self._compare_menu_btn.setMenu(self._compare_menu)
        row.addWidget(self._compare_menu_btn)

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

        self._session_menu_btn = QToolButton()
        self._session_menu_btn.setText("Session ▾")
        self._session_menu_btn.setProperty("menuButton", True)
        self._session_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._session_menu_btn.setToolTip("Save, reopen or start over on a Measure session file.")
        self._session_menu = QMenu(self._session_menu_btn)
        self._new_session_action = self._session_menu.addAction("New Session")
        self._new_session_action.triggered.connect(self._new_measure_session)
        self._save_session_action = self._session_menu.addAction("Save Session")
        self._save_session_action.triggered.connect(lambda: self._save_measure_session())
        self._save_session_as_action = self._session_menu.addAction("Save Session As…")
        self._save_session_as_action.triggered.connect(
            lambda: self._save_measure_session(save_as=True)
        )
        self._load_session_action = self._session_menu.addAction("Load Session…")
        self._load_session_action.triggered.connect(lambda: self._load_measure_session())
        self._session_menu_btn.setMenu(self._session_menu)
        row.addWidget(self._session_menu_btn)

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
        self._out_dev_combo.currentIndexChanged.connect(self._on_output_device_changed)
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
        self._measure_frequency_button = self._measure_submode_control.frequency_button
        self._measure_balance_button = self._measure_submode_control.balance_button
        self._measure_submode_control.balance_toggled.connect(self._on_measure_submode_toggled)
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
        self._queue_level_persist_toggle.stateChanged.connect(self._on_queue_level_persist_changed)
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
        toggle.setArrowType(Qt.ArrowType.DownArrow if not collapsed else Qt.ArrowType.RightArrow)
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
            toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
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
        self._pending_update_url: str | None = None
        self._update_check_thread: QThread | None = None

    def _current_output_device(self) -> int | None:
        value = self._out_dev_combo.currentData()
        return int(value) if value is not None else None

    def _current_input_device(self) -> int | None:
        value = self._in_dev_combo.currentData()
        return int(value) if value is not None else None

    def _current_output_device_info(self) -> dict | None:
        index = self._current_output_device()
        if index is None:
            return None
        return self._output_devices_by_index.get(index)

    def _current_input_device_info(self) -> dict | None:
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

    def _current_output_device_setting(self) -> dict | None:
        device = self._current_output_device_info()
        return device_setting(device, "output") if device is not None else None

    def _current_input_device_setting(self) -> dict | None:
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

    def _matching_output_for_input(self, input_device: dict | None) -> dict | None:
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
        return self._hrtf is not None and self._hrtf_toggle.isChecked()

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
            (int(d["index"]), str(d["name"]), int(d.get("hostapi", -1))) for d in all_out_devices
        ]
        in_signature = [
            (int(d["index"]), str(d["name"]), int(d.get("hostapi", -1))) for d in all_in_devices
        ]
        out_duplicates = duplicate_device_names(out_devices)
        in_duplicates = duplicate_device_names(in_devices)

        self._out_dev_combo.blockSignals(True)
        self._in_dev_combo.blockSignals(True)
        self._ch_combo.blockSignals(True)

        self._output_devices_by_index = {int(d["index"]): d for d in out_devices}
        self._input_devices_by_index = {int(d["index"]): d for d in in_devices}
        self._output_device_labels_by_index = {
            int(d["index"]): device_label(d, out_duplicates) for d in out_devices
        }
        self._input_device_labels_by_index = {
            int(d["index"]): device_label(d, in_duplicates) for d in in_devices
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
        if previous_out == current_out and previous_in == current_in and previous_ch == current_ch:
            self._statusbar.showMessage("Audio devices refreshed.")
        else:
            self._statusbar.showMessage("Audio devices refreshed; selection changed.")

    def _refresh_channels(self, selected_ch: int | None = None) -> None:
        input_device = self._current_input_device()
        count = device_channel_count(input_device, "input") if input_device is not None else 0

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
            self._active_ch_label.setText(f"Active input channel: Ch {want_ch + 1}")
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

    def _sync_device_poller(self) -> None:
        """Pause polling whenever PortAudio must not be re-enumerated."""
        poller = getattr(self, "_device_poller", None)
        if poller is None:
            return
        busy = not self._queue.allows_device_reselect() or getattr(self, "_rnd_sweep_active", False)
        poller.pause(busy)

    def _check_devices(
        self,
        output_devices: list[dict] | None = None,
        input_devices: list[dict] | None = None,
    ) -> None:
        """React to a device-set change reported by :class:`DevicePoller`.

        The lists arrive from the poller thread; they are only enumerated here
        when a caller (a test, or a manual check) passes nothing.
        """
        if output_devices is None:
            output_devices = get_output_devices()
        if input_devices is None:
            input_devices = get_input_devices()
        current_out = [
            (int(d["index"]), str(d["name"]), int(d.get("hostapi", -1))) for d in output_devices
        ]
        current_in = [
            (int(d["index"]), str(d["name"]), int(d.get("hostapi", -1))) for d in input_devices
        ]

        if current_out == self._last_output_devices and current_in == (self._last_input_devices):
            return

        getattr(self, "_stop_channel_balance", lambda: None)()

        selected_out = self._current_output_device()
        selected_in = self._current_input_device()
        selected_vanished = selected_out not in {
            idx for idx, _name, _hostapi in current_out
        } or selected_in not in {idx for idx, _name, _hostapi in current_in}

        if selected_vanished and self._state != AppState.IDLE:
            # Whether a sweep is running, a pair is between channels, or a
            # review is open: the device is gone, so the queue is over.
            self._abort_active_sweep()
            self._close_pass_fail_dialog()
            self._queue.reset()
            self._state = AppState.IDLE
            self._update_queue_progress()
            self._apply_state_ui()
            self._statusbar.showMessage(
                "Audio device change detected. Active measurement aborted safely."
            )

        if self._queue.allows_device_reselect() and not self._rnd_sweep_active:
            self._refresh_devices()
        else:
            # Never re-select devices under a running queue or an open review;
            # the refresh happens as soon as the window returns to idle.
            self._devices_dirty = True

    def _abort_active_sweep(self) -> None:
        """Stop the running sweep and wait for its thread to end.

        Waiting matters: sounddevice's play/record state is process-global, so
        a stale thread that stops later would truncate the next sweep.
        """
        with contextlib.suppress(Exception):
            self._sweep_runner.abort()

    def _cleanup_sweep_thread(self) -> None:
        """Compatibility hook; the runner releases its thread and worker itself."""
        self._sweep_thread = None
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
            self._statusbar.showMessage("Windows input/output driver backends do not match.")

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
        self._displayed_level_dbfs = self._displayed_level_dbfs * 0.5 + target_db * 0.5
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
        if not is_allowed_feed_url(feed_url):
            self._log_event(
                "WARNING",
                "update",
                "Update check skipped: feed URL must use https://",
                url=feed_url,
            )
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
        self._update_button.setToolTip(f"v{latest_version} is available{summary_text}")
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
        # Second gate: the feed was validated at parse time, but the URL is
        # about to be handed to the OS browser, so re-check it here too.
        if not is_allowed_release_url(self._pending_update_url):
            self._log_event(
                "WARNING",
                "update",
                "Blocked update link outside the project release org",
                url=self._pending_update_url,
            )
            self._pending_update_url = None
            self._update_button.setVisible(False)
            return
        QDesktopServices.openUrl(QUrl(self._pending_update_url))

    def _apply_state_ui(self) -> None:
        if (
            getattr(self, "_devices_dirty", False)
            and self._queue.allows_device_reselect()
            and not self._rnd_sweep_active
        ):
            self._devices_dirty = False
            self._refresh_devices()
        self._sync_device_poller()
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
            self._two_channel_devices_ready() if self._two_channel_enabled else single_device_ok
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
            self._distortion_toggle,
            self._level_mode_combo,
            self._hrtf_combo,
            self._undo_btn,
            self._clear_btn,
            self._metadata_btn,
            self._clear_metadata_btn,
            self._advanced_windows_drivers_toggle,
            self._refresh_devices_btn,
        ):
            widget.setEnabled(idle)

        for name in ("_session_menu_btn", "_compare_menu_btn"):
            widget = getattr(self, name, None)
            if widget is not None:
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
            len(self._two_channel_pairs) if self._two_channel_enabled else len(self._kept_curves)
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

        # The Measure button is disabled in Channel Balance mode, but the
        # keyboard shortcut, the console and automations reach this method
        # directly; the guard must live here.
        if MainWindow._channel_balance_mode_active(self) or getattr(
            self, "_channel_balance_active", False
        ):
            self._statusbar.showMessage(
                "Start blocked: switch to Frequency Response and stop Channel Balance first."
            )
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
        overrides = (
            self._settings.session_overrides()
            if hasattr(self._settings, "session_overrides")
            else {}
        )
        if "queue_count" not in overrides:
            self._settings.set("queue_count", self._queue_target)

        self._queue_progress_bar.setRange(0, max(1, self._queue_target))
        self._queue_progress_bar.setValue(0)
        kept_count = (
            len(self._two_channel_pairs) if self._two_channel_enabled else len(self._kept_curves)
        )
        self._queue_progress_label.setText(f"Kept: {kept_count}")

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
        self._stop_channel_balance()
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

        started = self._sweep_runner.start(
            lambda: worker,
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
            bluetooth_headphone_mode=bool(self._settings.get("bluetooth_headphone_mode")),
            start_alignment_confidence_min=float(
                self._settings.get("start_alignment_confidence_min")
            ),
            end_marker_confidence_min=float(self._settings.get("end_marker_confidence_min")),
            timing_drift_max_ms=float(self._settings.get("timing_drift_max_ms")),
            sweep_noise_margin_min_db=float(self._settings.get("sweep_noise_margin_min_db")),
            snr_warn_db=float(self._settings.get("snr_warn_db")),
            failed_recording_dir=self._failed_recording_dir(),
            sweep_f_low=_MEASUREMENT_F_MIN,
            sweep_f_high=_MEASUREMENT_F_MAX,
        )
        if not started:
            self._on_sweep_error(
                "The previous sweep is still stopping. Wait a moment and try again."
            )
            return

        channel_text = f", channel {self._two_channel_stage}" if self._two_channel_enabled else ""
        self._statusbar.showMessage(
            f"Sweeping {self._queue_index + 1}/{self._queue_target}{channel_text} "
            f"(attempt {self._current_sweep_attempts})..."
        )
        self._log_event(
            "INFO",
            "measurement",
            "Sweep started",
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
                "start_confidence",
                "marker_confidence",
                "timing_error_ms",
                "snr_db",
                "failure_reason",
                "warning_reason",
                "bluetooth_headphone_mode",
                "buffer_size",
            )
            if hasattr(diagnostics, name)
        }
        self._log_event("INFO", "diagnostics", "Measurement diagnostics received", **details)

    def _on_sweep_finished(self, recording: np.ndarray, sweep: np.ndarray) -> None:
        try:
            (freqs, mag_db), _distortion = self._analyze_sweep(recording, sweep)
            spl_offset = self._spl_offset_db()
            if spl_offset is not None:
                mag_db = mag_db + spl_offset
            if self._two_channel_enabled:
                if self._two_channel_stage == 1:
                    self._pending_pair_first_raw = (freqs, mag_db)
                    self._pending_pair_first_diagnostics = self._last_measurement_diagnostics
                    self._start_second_pair_stage = True
                    self._state = AppState.QUEUE_RUNNING
                    self._apply_state_ui()
                    self._statusbar.showMessage("Channel 1/L complete. Starting channel 2/R.")
                    return
                if self._two_channel_stage != 2 or self._pending_pair_first_raw is None:
                    raise ValueError("The first channel result is unavailable.")
                first_freqs, first_mag = self._pending_pair_first_raw
                if spl_offset is None:
                    first_norm, second_norm = shared_normalize_pair_at_1khz(
                        first_freqs,
                        first_mag,
                        freqs,
                        mag_db,
                        f_ref=1000.0,
                    )
                else:
                    # Absolute levels: the shared 1 kHz anchor would throw the
                    # calibrated offset away, and the two channels must keep
                    # their real level difference.
                    first_norm, second_norm = first_mag, mag_db
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
                self._statusbar.showMessage("Two-channel pair complete. Waiting for review.")
                QTimer.singleShot(0, self._show_pass_fail_dialog)
                return
            if spl_offset is None:
                mag_db = normalize_at_1khz(freqs, mag_db, f_ref=1000.0)

            freqs_ds, mag_ds = downsample_to_log_points(
                freqs,
                mag_db,
                n_points=600,
                f_ref=1000.0,
                normalize_ref=spl_offset is None,
            )

            self._pending_curve = (freqs_ds, mag_ds)
            self._log_event(
                "INFO",
                "processing",
                "Frequency response processed",
                input_points=len(freqs),
                output_points=len(freqs_ds),
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
                    warning_reason = getattr(
                        self._last_measurement_diagnostics,
                        "warning_reason",
                        None,
                    )
                    warning_prefix = (
                        " Low SNR."
                        if warning_reason == MeasurementWarningReason.LOW_SNR
                        else " Bluetooth timing marginal."
                    )
                if bluetooth_mode:
                    timing_msg = (
                        f" Timing Quality: start {start_conf:.1f}, "
                        f"end {end_conf:.1f}, drift {drift_ms:.1f} ms, "
                        f"SNR {snr_db:.1f} dB.{warning_prefix}"
                    )
                else:
                    timing_msg = f" Sweep Quality: alignment {start_conf:.1f}, SNR {snr_db:.1f} dB."
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
            failure_reason = getattr(self._last_measurement_diagnostics, "failure_reason", None)
        is_timing_quality_error = is_retryable_timing_failure(
            message=message,
            failure_reason=failure_reason,
        )
        # A device or stream failure is terminal: retrying only repeats it, so
        # two-channel mode must not offer a pair retry for it.
        retry_complete_pair = bool(
            getattr(self, "_two_channel_enabled", False)
            and self._queue_active()
            and not is_device_failure(message, failure_reason)
        )
        if (
            self._queue_active()
            and (is_timing_quality_error or retry_complete_pair)
            and self._current_sweep_attempts < _MAX_SWEEP_ATTEMPTS
        ):
            diagnostics_text = ""
            if (
                self._last_measurement_diagnostics is not None
                and getattr(self._last_measurement_diagnostics, "failure_reason", None) is not None
            ):
                diagnostics_text = "\n\n" + format_diagnostics_summary(
                    self._last_measurement_diagnostics
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
                "two-channel pair retry" if retry_complete_pair else "timing-quality retry"
            )
            self._statusbar.showMessage(f"Queue canceled by user after {cancel_reason} prompt.")
            return

        dialog_message = message
        if (
            self._last_measurement_diagnostics is not None
            and getattr(self._last_measurement_diagnostics, "failure_reason", None) is not None
        ):
            dialog_message = (
                f"{message}\n\n{format_diagnostics_summary(self._last_measurement_diagnostics)}"
            )
        # Terminal: the queue is over. A full reset clears the counters too, so
        # no phantom queue survives in the progress bar or the console.
        self._queue.reset()
        self._state = AppState.IDLE
        self._update_queue_progress()
        self._apply_state_ui()
        self._start_level_monitor()
        self._statusbar.showMessage(message)
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
            self._kept_pair_meta.append({"timing_quality": self._last_timing_quality})
            self._kept_distortion = self._queue.last_distortion
            self._pending_pair = None
            self._pending_pair_first_raw = None
            self._pending_pair_first_diagnostics = None
            self._two_channel_stage = 0
            self._queue_index += 1
            self._current_sweep_attempts = 0
            self._recompute_two_channel_results()
            self._update_queue_progress()
            self._update_plots()
            self._mark_measure_dirty()
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
        self._kept_sweep_meta.append(
            {
                "diagnostics": self._last_measurement_diagnostics,
                "timing_quality": self._last_timing_quality,
                "distortion": self._queue.last_distortion,
            }
        )
        self._kept_distortion = self._queue.last_distortion
        self._log_event(
            "INFO",
            "review",
            "Measurement kept",
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
        self._mark_measure_dirty()
        match = self._target_match_message()
        if match:
            self._statusbar.showMessage(f"Kept {len(self._kept_curves)} measurement(s). {match}")

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
        # A manual Fail repeats the same index with a fresh retry budget; the
        # attempts that produced the rejected sweep were not timing failures.
        self._queue.reset(keep_counters=True)
        self._current_sweep_attempts = 0
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
        # One reset clears the counters and every pending curve or pair.
        self._queue.reset()
        self._state = AppState.IDLE
        self._sweep_progress.setValue(0)
        self._update_queue_progress()
        self._update_plots()
        self._apply_state_ui()
        self._start_level_monitor()
        self._statusbar.showMessage("Queue canceled.")

    def _finish_queue(self) -> None:
        self._queue.reset()
        self._state = AppState.IDLE
        self._sweep_progress.setValue(100)
        self._apply_state_ui()
        self._start_level_monitor()
        match = self._target_match_message()
        self._statusbar.showMessage(f"Queue complete. {match}" if match else "Queue complete.")
        self._run_automation_trigger("queue_complete")

    def _update_queue_progress(self) -> None:
        target = max(0, self._queue_target)
        self._queue_progress_bar.setRange(0, max(1, target))
        self._queue_progress_bar.setValue(min(self._queue_index, max(1, target)))
        kept_count = (
            len(self._two_channel_pairs) if self._two_channel_enabled else len(self._kept_curves)
        )
        self._queue_progress_label.setText(f"Kept: {kept_count}")

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
            distortion=self._queue.last_distortion,
            deviation_summary=self._pending_deviation_summary(),
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
        self._stop_channel_balance()
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
        started = self._sweep_runner.start(
            lambda: worker,
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
            start_alignment_confidence_min=float(
                self._settings.get("start_alignment_confidence_min")
            ),
            end_marker_confidence_min=float(self._settings.get("end_marker_confidence_min")),
            timing_drift_max_ms=float(self._settings.get("timing_drift_max_ms")),
            sweep_noise_margin_min_db=float(self._settings.get("sweep_noise_margin_min_db")),
            snr_warn_db=float(self._settings.get("snr_warn_db")),
            failed_recording_dir=self._failed_recording_dir(),
            sweep_f_low=_MEASUREMENT_F_MIN,
            sweep_f_high=_MEASUREMENT_F_MAX,
        )
        if not started:
            self._on_rnd_sweep_error(
                "The previous sweep is still stopping. Wait a moment and try again."
            )
            return
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
            (freqs, mag_db), _distortion = self._analyze_sweep(recording, sweep)
            spl_offset = self._spl_offset_db()
            if spl_offset is None:
                mag_db = normalize_at_1khz(freqs, mag_db, f_ref=1000.0)
            else:
                mag_db = mag_db + spl_offset
            freqs_ds, mag_ds = downsample_to_log_points(
                freqs,
                mag_db,
                n_points=600,
                f_ref=1000.0,
                normalize_ref=spl_offset is None,
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
        previous = (
            self._rnd_widget.session.measurements[-1].name
            if self._rnd_widget.session.measurements
            else ""
        )
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
        channel_label = (
            self._ch_combo.currentText().strip() or f"Channel {self._current_input_channel() + 1}"
        )
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
        self._log_event(
            "INFO", "rnd", "R&D measurement kept", name=measurement.name, status=change_status
        )
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
            # In dB SPL the average must keep the absolute level.
            normalize_ref=self._spl_offset_db() is None,
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
    ) -> tuple[np.ndarray, np.ndarray] | None:
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
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
        return MainWindow._variation_from_curves(
            self,
            self._kept_curves,
            self._average,
            hrtf=hrtf,
        )

    def _variation_from_curves(
        self,
        curves: list[tuple[np.ndarray, np.ndarray]],
        average: tuple[np.ndarray, np.ndarray] | None,
        *,
        hrtf: HRTFCurve | None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
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
    ) -> tuple[np.ndarray, np.ndarray] | None:
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
    ) -> tuple[np.ndarray, np.ndarray] | None:
        active_hrtf = self._hrtf if self._is_hrtf_active() else None
        return self._average_curve_with_hrtf(active_hrtf)

    def _bottom_curve_for_display(self) -> tuple[np.ndarray, np.ndarray] | None:
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
        overlay_freqs, overlay_series = self._distortion_overlay_series()
        self._plots.set_distortion_overlay(overlay_freqs, overlay_series)
        self._sync_compare_layers()
        delta_on = self._delta_view_enabled()
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
            show_variation = self._bottom_view_mode() == "variation"
            if delta_on:
                # Limitation: two-channel delta view replaces only the *active*
                # bottom viewport's curve. The other viewports keep showing
                # their own averages, the pane titles still read "Average", and
                # the target line and reference layers are single-channel only.
                active_key = self._active_two_channel_key()
                delta = self._measure_delta_result(
                    averages.get(active_key)
                    if isinstance(averages.get(active_key), tuple)
                    else None
                )
                if delta is not None:
                    averages[active_key] = (delta.freqs, delta.delta_db)
                    show_variation = False
            self._plots.two.update_frequency_response(
                top_channel_1=channel_curves(pairs, 1),
                top_channel_2=channel_curves(pairs, 2),
                averages=averages,
                variations=variations,
                show_variation=show_variation,
            )
            self._sync_export_button()
            return

        avg = self._bottom_curve_for_display()
        self._recompute_variation()

        kept = list(self._kept_curves)
        if show_pending and self._pending_curve is not None:
            kept = kept + [self._pending_curve]

        bottom_mode = self._bottom_view_mode()
        if delta_on:
            # The bottom viewport shows measurement - target instead of the
            # average, so the variation band has nothing to describe.
            delta = self._measure_delta_result(self._bottom_curve_for_display_and_export())
            if delta is not None:
                avg = (delta.freqs, delta.delta_db)
                bottom_mode = "average"

        self._plots.update_curves(
            kept=kept,
            average=avg,
            variation=self._variation,
            bottom_mode=bottom_mode,
            animate_last=show_pending and self._pending_curve is not None,
        )
        self._sync_export_button()

    def _bottom_view_mode(self) -> str:
        if getattr(self, "_is_hrtf_active", lambda: False)() and getattr(
            getattr(self, "_hrtf", None), "is_variation", False
        ):
            return "variation"
        return "variation" if self._variation_toggle.isChecked() else "average"

    def _on_bottom_view_changed(self, *_args) -> None:
        self._update_plots()

    # ------------------------------------------------------------------
    # Distortion overlay
    # ------------------------------------------------------------------

    def _distortion_overlay_enabled(self) -> bool:
        toggle = getattr(self, "_distortion_toggle", None)
        return toggle is not None and bool(toggle.isChecked())

    def _distortion_analysis_allowed(self) -> bool:
        """Whether the last sweep is clean enough to measure harmonics on.

        Below ``_DISTORTION_MIN_SNR_DB`` the Farina packets sit inside the
        noise floor and the numbers would be meaningless.
        """
        if not self._distortion_overlay_enabled():
            return False
        timing = self._last_timing_quality
        if timing is None:
            return False
        try:
            return float(timing[3]) >= _DISTORTION_MIN_SNR_DB
        except (TypeError, ValueError, IndexError):
            return False

    def _distortion_overlay_series(self):
        # During review show the pending sweep's analysis; afterwards keep
        # showing the most recently kept sweep's.
        analysis = self._queue.last_distortion or self._kept_distortion
        if analysis is None or not self._distortion_overlay_enabled():
            return None, None
        series: dict[str, np.ndarray] = {"THD": np.asarray(analysis.thd_db)}
        for order, name in ((2, "H2"), (3, "H3")):
            values = analysis.orders.get(order)
            if values is not None:
                series[name] = np.asarray(values)
        return np.asarray(analysis.freqs), series

    def _on_distortion_overlay_changed(self, *_args) -> None:
        self._settings.set("measure_distortion_overlay", self._distortion_overlay_enabled())
        self._update_plots()

    def _analyze_sweep(
        self,
        recording: np.ndarray,
        sweep: np.ndarray,
    ) -> tuple[tuple[np.ndarray, np.ndarray], HarmonicAnalysis | None]:
        """Frequency response plus, when asked for, harmonic distortion.

        The distortion pass is strictly optional: any failure inside it is
        logged and dropped, because a distortion overlay must never cost the
        operator a measurement.
        """
        fs = int(self._settings.get("sample_rate"))
        freqs, mag_db = compute_frequency_response(
            recording=recording,
            sweep=sweep,
            fs=fs,
            f_low=_MEASUREMENT_F_MIN,
            f_high=_MEASUREMENT_F_MAX,
        )
        distortion: HarmonicAnalysis | None = None
        if self._distortion_analysis_allowed():
            try:
                distortion = harmonic_responses(
                    deconvolve_sweep(recording, sweep, fs),
                    f_low=_MEASUREMENT_F_MIN,
                    f_high=_MEASUREMENT_F_MAX,
                )
            except Exception as exc:  # never fail a sweep over the overlay
                self._log_event(
                    "WARNING",
                    "processing",
                    f"Distortion analysis skipped: {exc}",
                )
                distortion = None
        self._queue.last_distortion = distortion
        return (freqs, mag_db), distortion

    # ------------------------------------------------------------------
    # Level mode (1 kHz reference vs absolute dB SPL)
    # ------------------------------------------------------------------

    def _level_mode(self) -> str:
        mode = str(self._settings.get("measure_level_mode") or "ref_1khz")
        return "dbspl" if mode == "dbspl" else "ref_1khz"

    def _calibrated_sensitivity(self) -> float | None:
        """Pa/FS for the selected input device, or None when uncalibrated.

        ``CalibrationStore`` is keyed by the device's raw name — the same key
        ``CalibrationDialog`` writes — not by the disambiguated UI label.
        """
        info = self._current_input_device_info()
        if info is None:
            return None
        name = str(info.get("name") or "")
        if not name or not self._cal_store.is_calibrated(name):
            return None
        return self._cal_store.get_sensitivity(name)

    def _spl_offset_db(self) -> float | None:
        """dB offset to absolute SPL, or None to stay in 1 kHz reference mode.

        Falls back to reference mode — with a single status-bar note — when
        dB SPL is selected but the input device has no calibration.
        """
        if self._level_mode() != "dbspl":
            return None
        sensitivity = self._calibrated_sensitivity()
        if sensitivity is None:
            if not getattr(self, "_spl_uncalibrated_warned", False):
                self._spl_uncalibrated_warned = True
                self._statusbar.showMessage(
                    "dB SPL needs a calibrated input device; showing 1 kHz "
                    "reference levels instead."
                )
            return None
        try:
            return absolute_spl_offset_db(
                sensitivity_pa_per_fs=float(sensitivity),
                output_level_db=float(self._queue_level_spin.value()),
            )
        except (TypeError, ValueError):
            return None

    def _has_kept_measurements(self) -> bool:
        if self._two_channel_enabled:
            return bool(self._two_channel_pairs) or self._pending_pair is not None
        return bool(self._kept_curves) or self._pending_curve is not None

    def _sync_level_mode_combo(self) -> None:
        combo = getattr(self, "_level_mode_combo", None)
        if combo is None:
            return
        combo.blockSignals(True)
        combo.setCurrentIndex(1 if self._level_mode() == "dbspl" else 0)
        combo.blockSignals(False)

    def _on_level_mode_changed(self, *_args) -> None:
        """Switch level modes, refusing to mix modes inside one kept set."""
        combo = self._level_mode_combo
        chosen = str(combo.currentData() or "ref_1khz")
        chosen = "dbspl" if chosen == "dbspl" else "ref_1khz"
        if chosen == self._level_mode():
            return
        if self._state != AppState.IDLE:
            QMessageBox.information(
                self,
                "Busy",
                "Cannot change the level mode while a measurement is running.",
            )
            self._sync_level_mode_combo()
            return
        if self._has_kept_measurements():
            label = "dB SPL" if chosen == "dbspl" else "1 kHz reference"
            choice = QMessageBox.question(
                self,
                "Clear Measurements?",
                "Kept measurements use the current level mode and cannot be "
                f"mixed with {label}.\n\nClear all measurements and switch?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                self._sync_level_mode_combo()
                self._statusbar.showMessage("Level mode unchanged.")
                return
            self._discard_all_measurements()
        self._settings.set("measure_level_mode", chosen)
        self._spl_uncalibrated_warned = False
        self._sync_level_mode_combo()
        if chosen == "dbspl" and self._calibrated_sensitivity() is None:
            self._statusbar.showMessage(
                "dB SPL selected, but the input device is not calibrated; "
                "curves stay at 1 kHz reference until it is."
            )
        else:
            self._statusbar.showMessage(
                "Level mode: dB SPL." if chosen == "dbspl" else "Level mode: 1 kHz reference."
            )
        self._update_plots()

    def _on_hrtf_selected(self) -> None:
        path = self._hrtf_combo.currentData()
        if not path:
            self._hrtf = None
            self._settings.set("hrtf_path", None)
            self._sync_hrtf_ui()
            self._update_plots()
            self._mark_measure_dirty()
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
            self._mark_measure_dirty()
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
            self._statusbar.showMessage("Measurement import blocked: Two Channel mode is active.")
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
            # An imported curve carries no sweep of its own, so its metadata
            # slot stays empty; the lists must still line up one for one.
            self._kept_sweep_meta.append({})
            loaded += 1

        if loaded > 0:
            self._recompute_average()
            self._recompute_variation()
            self._update_queue_progress()
            self._update_plots()
            self._mark_measure_dirty()

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

        self._discard_all_measurements()
        self._statusbar.showMessage("All measurements cleared.")

    def _discard_all_measurements(self) -> None:
        """Drop every kept curve and reset the queue. No prompts, no guards.

        Split out of :meth:`_clear_all` so the level-mode switch can clear
        without asking the user a second time.
        """
        if self._two_channel_enabled:
            self._two_channel_pairs.clear()
            self._kept_pair_meta.clear()
            self._two_channel_averages.clear()
            self._two_channel_variations.clear()
            self._pending_pair = None
            self._pending_pair_first_raw = None
            self._pending_pair_first_diagnostics = None
            self._update_plots()
        else:
            self._kept_curves.clear()
            self._kept_sweep_meta.clear()
            self._average = None
            self._variation = None
            self._pending_curve = None
            # ``clear_all`` resets the comparison layers too, so the loaded
            # target and references are pushed straight back onto the plot.
            self._plots.clear_all()
            self._sync_compare_layers()
        self._queue.reset()
        self._kept_distortion = None
        self._update_queue_progress()
        self._sweep_progress.setValue(0)
        self._sync_export_button()
        self._apply_state_ui()
        self._mark_measure_dirty()

    def _confirm_clear_all(self) -> tuple[bool, bool]:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Clear All Measurements")
        dialog.setText("Are you sure you want to clear all measurements from this tab?")
        clear_button = dialog.addButton("Clear All", QMessageBox.ButtonRole.DestructiveRole)
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

        # The overlay described the sweep being undone.
        self._kept_distortion = None

        if self._two_channel_enabled:
            if not self._two_channel_pairs:
                return
            self._two_channel_pairs.pop()
            if self._kept_pair_meta:
                self._kept_pair_meta.pop()
            self._recompute_two_channel_results()
        else:
            if not self._kept_curves:
                return
            self._kept_curves.pop()
            if self._kept_sweep_meta:
                self._kept_sweep_meta.pop()
            self._recompute_average()
            self._recompute_variation()
        self._update_queue_progress()
        self._update_plots()
        self._apply_state_ui()
        self._mark_measure_dirty()
        self._statusbar.showMessage("Last kept measurement removed.")

    def _save_metadata_overlay(self) -> None:
        if not self._metadata_editor.validate():
            return
        self._session = self._metadata_editor.session_data()
        self._refresh_session_labels()
        self._refresh_window_title()
        self._close_metadata_overlay()
        self._mark_measure_dirty()
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
        self._mark_measure_dirty()
        self._statusbar.showMessage("Headphone metadata cleared.")

    def _confirm_clear_metadata(self) -> tuple[bool, bool]:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Clear Headphone Metadata")
        dialog.setText("Are you sure you want to clear all headphone metadata?")
        clear_button = dialog.addButton("Clear Metadata", QMessageBox.ButtonRole.DestructiveRole)
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
            current_profile = {key: self._settings.get(key) for key in updates}
            updates[PROFILE_SNAPSHOT_SETTING] = snapshot_measurement_profile(current_profile)
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
        self._statusbar.showMessage(f"Calibration saved for {label}: {sensitivity:.6f} Pa/FS")

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

    def _play_test_noise(self) -> str | None:
        output_device = self._current_output_device()
        if output_device is None:
            return "No output device selected."
        fs = int(self._settings.get("sample_rate"))
        dur_s = 1.8
        n = int(round(fs * dur_s))
        if n <= 0:
            return "Invalid sample rate for noise ping."
        noise = np.random.randn(n).astype(np.float32) * 0.04
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

    def _level_snapshot(self) -> tuple[float, float | None, str]:
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
                p10_db=np.array(p10, dtype=float, copy=True)
                + (correction if correction is not None else 0.0),
                p25_db=np.array(p25, dtype=float, copy=True)
                + (correction if correction is not None else 0.0),
                median_db=np.array(median, dtype=float, copy=True)
                + (correction if correction is not None else 0.0),
                p75_db=np.array(p75, dtype=float, copy=True)
                + (correction if correction is not None else 0.0),
                p90_db=np.array(p90, dtype=float, copy=True)
                + (correction if correction is not None else 0.0),
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
            self._active_two_channel_average() if self._two_channel_enabled else self._average
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
            (1 if active_label == "R" else 0)
            if self._two_channel_enabled
            else self._current_input_channel()
        )
        channel_label = active_label or (
            self._ch_combo.currentText().strip() or f"Channel {input_channel_index + 1}"
        )
        metadata["measure_channel"] = channel_label
        identity = self._session.asset_tag.strip() or " ".join(
            part for part in (self._session.brand.strip(), self._session.model.strip()) if part
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
            for index, (freqs, mag_db) in enumerate(self._active_measure_curves(), start=1):
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
                self._active_two_channel_average() if self._two_channel_enabled else self._average
            )
            assert active_average is not None
            freqs, mag_db = active_average
            name = self._unique_rnd_transfer_name(
                f"{identity} AVG",
                {measurement.name for measurement in self._rnd_widget.session.measurements},
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
            group_metadata = shared_metadata(measurement.metadata for measurement in measurements)
            rigs = {measurement.rig.strip() for measurement in measurements}
            if len(rigs) == 1 and next(iter(rigs)):
                group_metadata["rig"] = next(iter(rigs))
            hrtf_names = {measurement.hrtf_name.strip() for measurement in measurements}
            if len(hrtf_names) == 1 and next(iter(hrtf_names)):
                group_metadata["hrtf_name"] = next(iter(hrtf_names))
            compensation_states = {
                bool(measurement.hrtf_path or measurement.hrtf_name) for measurement in measurements
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
            QMessageBox.information(
                self, "Nothing Selected", "Select an R&D measurement or group first."
            )
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
        QMessageBox.information(
            self, "Nothing Selected", "Select an R&D measurement or group first."
        )

    def _export_rnd_measurement(
        self, measurement: RnDMeasurement, requested_path: str | None = None
    ) -> None:
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
            smoothing_fraction=int(self._rnd_widget.session.smoothing_fraction or 48),
            offset_db=self._rnd_widget.displayed_offset_db(measurement),
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
            if (measurement := self._rnd_widget.session.measurement_by_id(measurement_id))
            is not None
        ]
        if not measurements:
            QMessageBox.information(
                self, "Nothing to Export", "Selected group has no measurements."
            )
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
            label = (
                measurement.hrtf_name or Path(measurement.hrtf_path).stem or measurement.hrtf_path
            )
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
                if dialog.action == RnDRecoveryDialog.RESTORE and getattr(
                    candidate, "unsupported", False
                ):
                    # Intact, but written by a newer build. It is left in place
                    # rather than quarantined so a Fastgraph update can read it.
                    QMessageBox.warning(
                        self,
                        "Newer R&D Session",
                        "That recovered session was saved by a newer Fastgraph "
                        "and cannot be opened by this version. It was left in "
                        "place so a newer Fastgraph can recover it.",
                    )
                elif dialog.action == RnDRecoveryDialog.RESTORE:
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
        # The newest snapshot is safe either way; "degraded" means only the
        # previous generation could not be kept, and it clears itself once a
        # rotation succeeds.
        self._rnd_widget.set_recovery_warning(
            "R&D recovery degraded"
            if getattr(self._rnd_recovery, "rotation_degraded", False)
            else ""
        )
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
        # The file dialog checked the name the user typed; the canonical
        # extension is added afterwards, so "prototype" can still land on an
        # existing "prototype.fastgraph-rnd.json" without a warning.
        if path.exists() and not same_session_file(
            getattr(self._rnd_widget.session, "source_path", ""), path
        ):
            choice = QMessageBox.question(
                self,
                "Replace R&D Session?",
                f"Replace {path.name}?\n\n{path.parent}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return False
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
        clear_btn = dialog.addButton(
            "Clear current session and load", QMessageBox.ButtonRole.AcceptRole
        )
        add_btn = dialog.addButton("Add to current session", QMessageBox.ButtonRole.ActionRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is clear_btn:
            return "clear"
        if clicked is add_btn:
            return "add"
        return "cancel"

    # ------------------------------------------------------------------
    # Measure sessions
    # ------------------------------------------------------------------

    def _measure_default_dir(self) -> Path:
        configured = str(self._settings.get("measure_session_directory") or "").strip()
        if configured:
            return Path(configured).expanduser()
        documents = Path.home() / "Documents"
        return documents if documents.exists() else Path.home()

    def _current_measure_session(self) -> MeasureSession:
        """The Measure workspace's live state as a serializable session."""
        hrtf_path = self._hrtf.path if self._hrtf is not None else None
        return MeasureSession.from_window_state(
            session_data=self._session,
            kept_curves=self._kept_curves,
            pairs=self._two_channel_pairs,
            two_channel=self._two_channel_enabled,
            bottom_mode=self._two_channel_bottom_mode,
            level_mode=self._level_mode(),
            hrtf_path=hrtf_path,
            hrtf_name=Path(hrtf_path).stem if hrtf_path else None,
            hrtf_enabled=self._is_hrtf_active(),
            sweep_diagnostics=[meta.get("diagnostics") for meta in self._kept_sweep_meta],
            sweep_timing_quality=[meta.get("timing_quality") for meta in self._kept_sweep_meta],
            sweep_distortion=[meta.get("distortion") for meta in self._kept_sweep_meta],
            source_path=(
                str(self._measure_session_path) if self._measure_session_path is not None else None
            ),
        )

    def _mark_measure_dirty(self) -> None:
        """Record an unsaved change and queue a crash-recovery snapshot."""
        self._measure_dirty = True
        self._refresh_window_title()
        recovery = getattr(self, "_measure_recovery", None)
        if recovery is None:
            return
        session = self._current_measure_session()
        if session.is_empty():
            recovery.clear_active()
            return
        recovery.schedule(session.to_dict())

    def _clear_measure_dirty(self) -> None:
        """The workspace now matches a file, so the recovery copy is redundant."""
        self._measure_dirty = False
        recovery = getattr(self, "_measure_recovery", None)
        if recovery is not None:
            recovery.clear_active()
        self._refresh_window_title()

    def _new_measure_session(self) -> None:
        if self._state != AppState.IDLE:
            QMessageBox.information(
                self,
                "Busy",
                "A new Measure session can only be started while idle.",
            )
            return
        if not self._confirm_discard_measure_session():
            return
        # The save-or-discard prompt above already covered the question
        # ``_clear_all`` would ask, so the discard runs unprompted here; with
        # nothing kept there is nothing to discard at all.
        if self._has_kept_measurements():
            self._discard_all_measurements()
        self._kept_sweep_meta.clear()
        self._kept_pair_meta.clear()
        self._measure_session_path = None
        self._clear_measure_dirty()
        self._statusbar.showMessage("New Measure session.")
        self._log_event("INFO", "measure", "New Measure session started")

    def _save_measure_session(self, *, save_as: bool = False) -> bool:
        path = self._measure_session_path
        if save_as or path is None:
            default_path = path or (
                self._measure_default_dir()
                / f"fastgraph-measure-session{MEASURE_SESSION_EXTENSION}"
            )
            path_str, _ = QFileDialog.getSaveFileName(
                self,
                "Save Measure Session",
                str(default_path),
                f"Fastgraph Measure Session (*{MEASURE_SESSION_EXTENSION});;"
                "JSON Files (*.json);;All Files (*)",
            )
            if not path_str:
                return False
            path = ensure_measure_session_extension(Path(path_str))
            # The dialog checked the name the user typed; the canonical
            # extension is added afterwards, so "demo" can still land on an
            # existing "demo.fastgraph-measure.json" without a warning.
            if path.exists() and not same_measure_session_file(self._measure_session_path, path):
                choice = QMessageBox.question(
                    self,
                    "Replace Measure Session?",
                    f"Replace {path.name}?\n\n{path.parent}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if choice != QMessageBox.StandardButton.Yes:
                    return False
        try:
            written = save_measure_session(self._current_measure_session(), path)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Save Failed",
                f"Could not save the Measure session.\n\n{exc}",
            )
            return False
        self._measure_session_path = written
        self._settings.set("measure_session_directory", str(written.parent))
        self._settings_widget.refresh_from_settings()
        self._clear_measure_dirty()
        self._statusbar.showMessage(f"Saved Measure session: {written}")
        self._log_event("INFO", "measure", "Measure session saved", path=str(written))
        return True

    def _load_measure_session(self, requested_path: str | None = None) -> bool:
        if self._state != AppState.IDLE:
            QMessageBox.information(
                self,
                "Busy",
                "Measure sessions can only be loaded while idle.",
            )
            return False
        if not self._confirm_discard_measure_session():
            return False
        path_str = requested_path
        if path_str is None:
            path_str, _ = QFileDialog.getOpenFileName(
                self,
                "Load Measure Session",
                str(self._measure_default_dir()),
                "Fastgraph Measure Session (*.fastgraph-measure.json *.json);;All Files (*)",
            )
            if not path_str:
                return False
        try:
            session = load_measure_session(Path(path_str))
        except UnsupportedMeasureSessionVersion as exc:
            QMessageBox.warning(self, "Newer Measure Session", str(exc))
            return False
        except MeasureSessionLoadError as exc:
            QMessageBox.warning(
                self,
                "Load Failed",
                f"Could not load the Measure session.\n\n{exc}",
            )
            return False
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Load Failed",
                f"Could not load the Measure session.\n\n{exc}",
            )
            return False

        self._apply_measure_session(session)
        self._measure_session_path = Path(path_str)
        self._settings.set("measure_session_directory", str(Path(path_str).parent))
        self._settings_widget.refresh_from_settings()
        self._clear_measure_dirty()
        self._statusbar.showMessage(f"Loaded Measure session: {path_str}")
        self._log_event("INFO", "measure", "Measure session loaded", path=str(path_str))
        return True

    def _apply_measure_session(self, session: MeasureSession) -> None:
        """Replace the Measure workspace with a loaded session's state."""
        self._queue.reset()
        self._kept_distortion = None
        self._pending_curve = None
        self._pending_pair = None
        self._pending_pair_first_raw = None
        self._pending_pair_first_diagnostics = None
        self._two_channel_stage = 0
        self._queue_index = 0

        self._kept_curves = [sweep.curve for sweep in session.sweeps]
        self._kept_sweep_meta = [
            {
                "diagnostics": sweep.diagnostics,
                "timing_quality": sweep.timing_quality,
                "distortion": sweep.distortion_summary,
            }
            for sweep in session.sweeps
        ]
        self._two_channel_pairs = session.pair_objects()
        self._kept_pair_meta = [{} for _ in self._two_channel_pairs]
        self._average = None
        self._variation = None
        self._two_channel_averages = {}
        self._two_channel_variations = {}

        self._session = session.metadata
        self._refresh_session_labels()
        self._metadata_editor.set_session(self._session)

        if bool(session.two_channel) != bool(self._two_channel_enabled):
            self._two_channel_toggle.setChecked(bool(session.two_channel))

        index = self._bottom_layout_combo.findData(session.bottom_mode)
        if index >= 0 and index != self._bottom_layout_combo.currentIndex():
            self._bottom_layout_combo.setCurrentIndex(index)

        self._apply_session_level_mode(session.level_mode)
        self._apply_session_hrtf(session)

        self._recompute_average()
        self._recompute_variation()
        self._recompute_two_channel_results()
        self._update_queue_progress()
        self._update_plots()
        self._apply_state_ui()
        self._refresh_window_title()

    def _apply_session_level_mode(self, level_mode: str) -> None:
        wanted = "dbspl" if str(level_mode) == "dbspl" else "ref_1khz"
        if wanted == self._level_mode():
            return
        if wanted == "dbspl" and self._calibrated_sensitivity() is None:
            QMessageBox.warning(
                self,
                "Not Calibrated",
                "This session was saved in dB SPL, but the selected input "
                "device has no calibration. Levels stay at the 1 kHz "
                "reference.",
            )
            return
        self._settings.set("measure_level_mode", wanted)
        self._spl_uncalibrated_warned = False
        self._sync_level_mode_combo()

    def _apply_session_hrtf(self, session: MeasureSession) -> None:
        if not session.hrtf_path and not session.hrtf_name:
            return
        index = -1
        if session.hrtf_path:
            index = self._hrtf_combo.findData(session.hrtf_path)
        if index < 0 and session.hrtf_name:
            index = self._hrtf_combo.findText(session.hrtf_name)
        if index < 0:
            QMessageBox.warning(
                self,
                "Missing HRTF",
                f"The HRTF this session used ({session.hrtf_name or session.hrtf_path}) "
                "is not installed. It was left unset.",
            )
            return
        self._hrtf_combo.blockSignals(True)
        self._hrtf_combo.setCurrentIndex(index)
        self._hrtf_combo.blockSignals(False)
        self._on_hrtf_selected()
        self._hrtf_toggle.setChecked(bool(session.hrtf_enabled))

    def _confirm_discard_measure_session(self) -> bool:
        """Offer to save before something replaces the Measure workspace."""
        if not self._measure_dirty:
            return True
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle("Save Measure Session?")
        dialog.setText("The Measure session has unsaved changes.")
        dialog.setInformativeText("Save it before it is replaced?")
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        dialog.setDefaultButton(
            QMessageBox.StandardButton.Save
            if self._measure_session_path is not None
            else QMessageBox.StandardButton.Cancel
        )
        result = dialog.exec()
        if result == QMessageBox.StandardButton.Save:
            return self._save_measure_session()
        return result == QMessageBox.StandardButton.Discard

    def _initialize_measure_recovery(self) -> None:
        try:
            candidates = self._measure_recovery.candidates()
            if candidates:
                dialog = MeasureRecoveryDialog(candidates, self)
                dialog.exec()
                candidate = dialog.selected_candidate()
                if dialog.action == MeasureRecoveryDialog.RESTORE and getattr(
                    candidate, "unsupported", False
                ):
                    # Intact, but written by a newer build. It is left in place
                    # rather than quarantined so an update can read it.
                    QMessageBox.warning(
                        self,
                        "Newer Measure Session",
                        "That recovered session was saved by a newer Fastgraph "
                        "and cannot be opened by this version. It was left in "
                        "place so a newer Fastgraph can recover it.",
                    )
                elif dialog.action == MeasureRecoveryDialog.RESTORE:
                    session = self._measure_recovery.restore(candidate)
                    self._apply_measure_session(session)
                    self._measure_session_path = None
                    self._tabs.setCurrentWidget(self._measure_tab)
                    self._measure_dirty = not session.is_empty()
                    self._refresh_window_title()
                    self._restored_measure_candidate = (
                        candidate if candidate.kind == "deferred" else None
                    )
                elif dialog.action == MeasureRecoveryDialog.DISCARD:
                    self._measure_recovery.discard(candidate)
                else:
                    self._measure_recovery.keep_for_later(candidate)
        except Exception as exc:
            self._on_measure_recovery_failed(str(exc))
        finally:
            # Scheduling starts only once the prompt has been answered, so a
            # snapshot can never overwrite what the user is being offered.
            self._measure_recovery.enable()
            if self._measure_dirty:
                self._mark_measure_dirty()
            if self._restored_measure_candidate is not None:
                self._measure_recovery.discard(self._restored_measure_candidate)
                self._restored_measure_candidate = None

    def _on_measure_recovery_failed(self, error: str) -> None:
        self._statusbar.showMessage("Measure recovery save failed.")
        self._log_event("ERROR", "measure", "Measure recovery save failed", error=error)

    # ------------------------------------------------------------------
    # Target comparison
    # ------------------------------------------------------------------

    def _delta_view_enabled(self) -> bool:
        action = getattr(self, "_delta_view_action", None)
        return action is not None and action.isChecked() and self._measure_target is not None

    def _delta_offset_mode(self) -> str:
        mode = str(self._settings.get("measure_delta_offset_mode") or "1khz")
        return mode if mode in OFFSET_MODES else "1khz"

    def _restore_measure_comparison_state(self) -> None:
        """Reload the remembered target, dropping it if the file has gone."""
        stored = str(self._settings.get("measure_target_path") or "").strip()
        if stored:
            path = Path(stored).expanduser()
            if path.is_file():
                try:
                    freqs, mag_db, _warnings = load_target_curve(path)
                except Exception:
                    self._settings.set("measure_target_path", "")
                else:
                    self._measure_target = (freqs, mag_db)
                    self._measure_target_path = path
            else:
                self._settings.set("measure_target_path", "")
        self._sync_compare_layers()

    def _load_measure_target(self, requested_path: str | None = None) -> bool:
        path_str = requested_path
        if path_str is None:
            path_str, _ = QFileDialog.getOpenFileName(
                self,
                "Load Target Curve",
                str(self._measure_default_dir()),
                "Measurement TXT (*.txt);;All Files (*)",
            )
            if not path_str:
                return False
        try:
            freqs, mag_db, warnings = load_target_curve(Path(path_str))
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Target Load Failed",
                f"Could not read the target curve.\n\n{exc}",
            )
            return False
        self._measure_target = (freqs, mag_db)
        self._measure_target_path = Path(path_str)
        self._settings.set("measure_target_path", str(path_str))
        for warning in warnings[:4]:
            self._log_event("WARNING", "measure", warning)
        self._sync_compare_layers()
        self._update_plots()
        self._statusbar.showMessage(f"Target loaded: {Path(path_str).name}")
        self._log_event("INFO", "measure", "Target loaded", path=str(path_str))
        return True

    def _clear_measure_target(self) -> None:
        self._measure_target = None
        self._measure_target_path = None
        self._settings.set("measure_target_path", "")
        if getattr(self, "_delta_view_action", None) is not None:
            self._delta_view_action.setChecked(False)
        self._sync_compare_layers()
        self._update_plots()
        self._statusbar.showMessage("Target cleared.")

    def _on_delta_view_toggled(self, checked: bool) -> None:
        if checked and self._measure_target is None:
            self._delta_view_action.setChecked(False)
            QMessageBox.information(
                self,
                "No Target",
                "Load a target curve before switching to delta view.",
            )
            return
        self._settings.set("measure_delta_view", bool(checked))
        self._sync_compare_layers()
        self._update_plots()
        self._statusbar.showMessage("Delta view on." if checked else "Delta view off.")

    def _load_measure_reference(self, requested_path: str | None = None) -> bool:
        if len(self._measure_reference_layers) >= _MAX_REFERENCE_LAYERS:
            QMessageBox.information(
                self,
                "Reference Limit",
                f"At most {_MAX_REFERENCE_LAYERS} reference layers can be shown. "
                "Clear them before loading another.",
            )
            return False
        path_str = requested_path
        if path_str is None:
            path_str, _ = QFileDialog.getOpenFileName(
                self,
                "Load Reference Curve",
                str(self._measure_default_dir()),
                "Reference Curves (*.txt *.fastgraph-measure.json *.json);;All Files (*)",
            )
            if not path_str:
                return False
        path = Path(path_str)
        try:
            if path.name.lower().endswith(".json"):
                layer = load_reference_from_measure_session(path)
            else:
                layer = load_reference_from_txt(path)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Reference Load Failed",
                f"Could not read the reference curve.\n\n{exc}",
            )
            return False
        self._measure_reference_layers.append(layer)
        self._sync_compare_layers()
        self._statusbar.showMessage(f"Reference added: {layer.name}")
        self._log_event("INFO", "measure", "Reference layer added", path=str(path))
        return True

    def _clear_measure_references(self) -> None:
        self._measure_reference_layers.clear()
        self._sync_compare_layers()
        self._statusbar.showMessage("Reference layers cleared.")

    def _reference_colors(self) -> list[str]:
        """Trace colours for reference layers, never the average's own colour."""
        palette = theme_trace_palette(
            self._theme_controller.theme,
            brand_mode=self._theme_controller.brand_mode,
        )
        remaining = palette[1:] or palette
        return [remaining[index % len(remaining)] for index in range(_MAX_REFERENCE_LAYERS)]

    def _sync_compare_actions(self) -> None:
        has_target = self._measure_target is not None
        if getattr(self, "_clear_target_action", None) is not None:
            self._clear_target_action.setEnabled(has_target)
        if getattr(self, "_delta_view_action", None) is not None:
            self._delta_view_action.setEnabled(has_target)
            if not has_target and self._delta_view_action.isChecked():
                # Signals are blocked: this is bookkeeping after the target
                # went away, not the user turning delta view off.
                self._delta_view_action.blockSignals(True)
                self._delta_view_action.setChecked(False)
                self._delta_view_action.blockSignals(False)
        if getattr(self, "_clear_references_action", None) is not None:
            self._clear_references_action.setEnabled(bool(self._measure_reference_layers))
        if getattr(self, "_eq_suggestion_action", None) is not None:
            can_fit = has_target and self._bottom_curve_for_display_and_export() is not None
            self._eq_suggestion_action.setEnabled(bool(can_fit))
            self._eq_suggestion_action.setToolTip(
                "" if can_fit else "Needs a loaded target and at least one kept measurement."
            )

    def _sync_compare_layers(self) -> None:
        """Push the target, the reference layers and delta view to the plots."""
        plots = getattr(self, "_plots", None)
        if plots is None:
            return
        self._sync_compare_actions()
        single = plots.single
        delta_on = self._delta_view_enabled()
        single.set_delta_mode(delta_on)
        if self._measure_target is not None:
            single.set_target_curve(*self._measure_target)
        else:
            single.set_target_curve(None)
        colors = self._reference_colors()
        single.set_reference_layers(
            [
                (layer.name, layer.freqs, layer.mag_db, colors[index % len(colors)])
                for index, layer in enumerate(
                    self._measure_reference_layers[:_MAX_REFERENCE_LAYERS]
                )
            ]
        )

    def _measure_delta_result(
        self,
        curve: tuple[np.ndarray, np.ndarray] | None,
    ):
        """``curve - target`` on the shared grid, or ``None`` without either."""
        if curve is None or self._measure_target is None:
            return None
        try:
            return delta_curve(
                curve[0],
                curve[1],
                self._measure_target[0],
                self._measure_target[1],
                offset_mode=self._delta_offset_mode(),
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._log_event("ERROR", "measure", "Delta computation failed", error=str(exc))
            return None

    def _pending_deviation_summary(self) -> str | None:
        """Band-by-band deviation of the sweep awaiting review, if any."""
        if self._measure_target is None or self._two_channel_enabled:
            return None
        delta = self._measure_delta_result(self._pending_curve)
        if delta is None:
            return None
        return format_deviation_summary(deviation_score(delta))

    def _target_match_message(self) -> str | None:
        delta = self._measure_delta_result(self._bottom_curve_for_display_and_export())
        if delta is None:
            return None
        return f"Match: {deviation_score(delta).match_percent:.0f} %"

    def _open_eq_suggestion(self) -> None:
        average = self._bottom_curve_for_display_and_export()
        if self._measure_target is None:
            self._statusbar.showMessage("Load a target curve before asking for an EQ suggestion.")
            return
        if average is None:
            self._statusbar.showMessage(
                "No averaged measurement is available to fit an EQ against."
            )
            return
        dialog = EqSuggestionDialog(
            average,
            self._measure_target,
            offset_mode=self._delta_offset_mode(),
            parent=self,
        )
        dialog.exec()
        self._settings.set("measure_delta_offset_mode", dialog.offset_mode())
        dialog.deleteLater()
        self._update_plots()

    @staticmethod
    def _safe_filename(value: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in " ._-()" else "_" for ch in value).strip()
        return safe or "R&D Measurement"

    def _resolve_export_path(
        self,
        requested_path: str | None,
        filename: str,
        title: str,
        file_filter: str = "Text Files (*.txt);;All Files (*)",
    ) -> Path | None:
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
        path_str, _ = QFileDialog.getSaveFileName(self, title, default_path, file_filter)
        return Path(path_str) if path_str else None

    def _export_average(self, requested_path: str | None = None) -> None:
        # Export what is displayed: the same smoothed curve the bottom
        # viewport draws, with the smoothing recorded in the header.
        curve = self._bottom_curve_for_display()
        if curve is None:
            QMessageBox.information(self, "Nothing to Export", "No averaged curve available yet.")
            return

        compensated = self._is_hrtf_active()
        two_channel = bool(getattr(self, "_two_channel_enabled", False))
        channel_label = self._active_measure_label() if two_channel else ""
        export_session = self._active_measure_session() if two_channel else self._session
        active_count = self._active_measure_count() if two_channel else len(self._kept_curves)
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
                smoothing_fraction=_DISPLAY_AVG_SMOOTHING,
                level_mode=self._level_mode() if self._spl_offset_db() is not None else "ref_1khz",
            )
            self._statusbar.showMessage(f"Exported average: {path}")
            if hasattr(self, "_log_event"):
                self._log_event(
                    "INFO", "export", "Average exported", path=str(path), compensated=compensated
                )
            if hasattr(self, "_run_automation_trigger"):
                self._run_automation_trigger("export_complete")
        except Exception as exc:
            if hasattr(self, "_log_event"):
                self._log_event("ERROR", "export", f"Average export failed: {exc}")
            QMessageBox.warning(self, "Export Error", str(exc))

    def _export_variation(self, requested_path: str | None = None) -> None:
        two_channel = bool(getattr(self, "_two_channel_enabled", False))
        active_variation = self._active_measure_variation() if two_channel else self._variation
        if active_variation is None:
            QMessageBox.information(self, "Nothing to Export", "No variation band available yet.")
            return

        compensated = self._is_hrtf_active()
        channel_label = self._active_measure_label() if two_channel else ""
        export_session = self._active_measure_session() if two_channel else self._session
        active_count = self._active_measure_count() if two_channel else len(self._kept_curves)
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
                level_mode=self._level_mode() if self._spl_offset_db() is not None else "ref_1khz",
            )
            self._statusbar.showMessage(f"Exported variation: {path}")
            if hasattr(self, "_log_event"):
                self._log_event(
                    "INFO", "export", "Variation exported", path=str(path), compensated=compensated
                )
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
        dialog.setInformativeText("\n".join(path.name for path in conflicts))
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
            self._active_measure_label() if getattr(self, "_two_channel_enabled", False) else ""
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
                # Same smoothed curves and header as Export Average.
                raw_freqs, raw_mag = smooth_fractional_octave(
                    *raw_average, fraction=_DISPLAY_AVG_SMOOTHING
                )
                comp_freqs, comp_mag = smooth_fractional_octave(
                    *comp_average, fraction=_DISPLAY_AVG_SMOOTHING
                )
                level_mode = self._level_mode() if self._spl_offset_db() is not None else "ref_1khz"
                export_curve(
                    freqs=raw_freqs,
                    mag_db=raw_mag,
                    session=export_session,
                    output_path=temp_dir / filenames[0],
                    compensated=False,
                    hrtf=None,
                    n_sweeps=active_count,
                    smoothing_fraction=_DISPLAY_AVG_SMOOTHING,
                    level_mode=level_mode,
                )
                export_curve(
                    freqs=comp_freqs,
                    mag_db=comp_mag,
                    session=export_session,
                    output_path=temp_dir / filenames[1],
                    compensated=True,
                    hrtf=hrtf,
                    n_sweeps=active_count,
                    smoothing_fraction=_DISPLAY_AVG_SMOOTHING,
                    level_mode=level_mode,
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
                        level_mode=level_mode,
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

    def _export_console_log(self, requested_path: str | None = None) -> None:
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
        active_average = self._active_two_channel_average() if two_channel else self._average
        active_variation = self._active_measure_variation() if two_channel else self._variation
        if self._bottom_view_mode() == "variation":
            self._export_btn.setText("Export Variation…")
            self._export_btn.setToolTip(
                "Export the displayed variation band as percentile columns in a tab-delimited TXT file."
            )
            export_enabled = idle and frequency_mode and active_variation is not None
        else:
            self._export_btn.setText("Export Average…")
            self._export_btn.setToolTip("Export averaged FR as a REW-style TXT file.")
            export_enabled = idle and frequency_mode and active_average is not None
        self._export_btn.setEnabled(export_enabled)
        if hasattr(self, "_send_to_curator_btn"):
            self._send_to_curator_btn.setEnabled(export_enabled)
        if hasattr(self, "_send_to_rnd_btn"):
            unavailable = self._measure_to_rnd_unavailable_reason()
            self._send_to_rnd_btn.setEnabled(not unavailable)
            self._send_to_rnd_btn.setToolTip(
                unavailable or "Send the current average or all kept Var measurements to R&D."
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
                unavailable or "Export RAW AVG, COMP AVG, RAW VAR, and COMP VAR to one directory."
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

    def _failed_recording_dir(self) -> str | None:
        """Folder for failed-recording dumps, or None when the setting is off."""
        if not bool(self._settings.get("save_failed_recordings")):
            return None
        return str(config_dir() / "failed_recordings")

    def _upload_to_squiglink(self) -> None:
        # Upload what is displayed, exactly as Export Average writes it.
        curve = self._bottom_curve_for_display()
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
        if not remember:
            self._settings.set("squiglink_credentials_encrypted", None)
        # Credentials that turn out to be wrong (or that went to a server whose
        # host key was rejected) are never written to disk: the save happens in
        # _on_squiglink_upload_finished, after a successful upload.

        compensated = self._is_hrtf_active()
        channel_label = self._active_measure_label()
        # Squiglink requires exactly one channel side per file and has no way to
        # represent a combined L/R result. A Combined ("BOTH") upload is sent as
        # the L side on purpose; the "BOTH L" name modifier below marks it.
        required_side = (
            ("R" if channel_label == "R" else "L") if self._two_channel_enabled else None
        )
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

        tmp_path: Path | None = None
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
                smoothing_fraction=_DISPLAY_AVG_SMOOTHING,
                level_mode=self._level_mode() if self._spl_offset_db() is not None else "ref_1khz",
            )
        except Exception as exc:
            self._statusbar.showMessage(f"Upload to Squiglink failed: {exc}")
            self._log_exception("upload", "Squiglink upload failed", exc)
            QMessageBox.warning(self, "Upload Failed", f"Upload to Squiglink failed.\n\n{exc}")
            if tmp_path is not None:
                with contextlib.suppress(Exception):
                    tmp_path.unlink(missing_ok=True)
            return

        self._start_squiglink_upload(
            local_path=tmp_path,
            host=host,
            port=port,
            username=username,
            password=password,
            filename=filename,
            phone_book_stem=phone_book_stem,
            remember=remember,
        )

    def _start_squiglink_upload(
        self,
        *,
        local_path: Path,
        host: str,
        port: int,
        username: str,
        password: str,
        filename: str,
        phone_book_stem: str,
        remember: bool,
    ) -> None:
        """Hand the SFTP work to a worker thread behind a cancellable dialog."""
        host_keys = self._squiglink_host_keys()
        worker = SquiglinkUploadWorker(
            local_path=local_path,
            host=host,
            port=port,
            username=username,
            password=password,
            remote_filename=filename,
            phone_book_stem=phone_book_stem,
            host_keys=host_keys,
            sync_phone_book=self._sync_remote_phone_book,
            diagnostic=self._log_sftp_diagnostic,
        )
        thread = QThread(self)
        worker.moveToThread(thread)

        progress = QProgressDialog("Uploading to Squiglink…", "Cancel", 0, 0, self)
        progress.setWindowTitle("Squiglink Upload")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        # Direct, not queued: the worker thread is blocked inside run(), so a
        # queued cancel would not be delivered until the upload it is meant to
        # interrupt had already finished. cancel() only sets thread-safe flags.
        progress.canceled.connect(worker.cancel, Qt.ConnectionType.DirectConnection)

        self._squiglink_upload_context = {
            "worker": worker,
            "thread": thread,
            "progress": progress,
            "local_path": local_path,
            "username": username,
            "password": password,
            "remember": remember,
            "filename": filename,
        }

        worker.progress.connect(progress.setLabelText)
        worker.host_key_prompt.connect(self._on_squiglink_host_key_prompt)
        worker.host_key_accepted.connect(self._on_squiglink_host_key_accepted)
        worker.phone_book_fallback_needed.connect(self._on_squiglink_phone_book_fallback)
        worker.finished.connect(self._on_squiglink_upload_finished)
        worker.failed.connect(self._on_squiglink_upload_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.started.connect(worker.run)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        progress.show()
        thread.start()

    def _squiglink_host_keys(self) -> dict:
        stored = self._settings.get("squiglink_host_keys")
        return dict(stored) if isinstance(stored, dict) else {}

    def _on_squiglink_host_key_prompt(
        self,
        host: str,
        port: int,
        fingerprint: str,
        key_type: str,
    ) -> None:
        """Ask the user to trust an unknown SSH host key (GUI thread)."""
        context = self._squiglink_upload_context
        worker = context.get("worker") if context else None
        self._log_event(
            "WARNING",
            "squiglink",
            "Unknown SSH host key offered",
            host=host,
            port=int(port),
            key_type=key_type,
            fingerprint=fingerprint,
        )
        accepted = (
            QMessageBox.question(
                self,
                "Trust This Server?",
                f"{host}:{int(port)} has not been connected to before.\n\n"
                f"Key type: {key_type}\nFingerprint: {fingerprint}\n\n"
                "Trust this server and remember its key? Your Squiglink "
                "password is only sent after you accept.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )
        if worker is not None:
            worker.answer_host_key(accepted)

    def _on_squiglink_host_key_accepted(self, identifier: str, fingerprint: str) -> None:
        """Pin a newly trusted key so a later mismatch is caught."""
        host_keys = self._squiglink_host_keys()
        host_keys[identifier] = fingerprint
        self._settings.set("squiglink_host_keys", host_keys)
        self._log_event(
            "INFO",
            "squiglink",
            "SSH host key trusted and stored",
            endpoint=identifier,
            fingerprint=fingerprint,
        )

    def _on_squiglink_phone_book_fallback(self, detail_message: str) -> None:
        context = self._squiglink_upload_context
        worker = context.get("worker") if context else None
        mode = self._ask_phone_book_fallback_mode(detail_message)
        if worker is not None:
            worker.answer_phone_book_fallback(mode)

    def _finish_squiglink_upload(self) -> None:
        context = self._squiglink_upload_context or {}
        progress = context.get("progress")
        if progress is not None:
            progress.close()
            progress.deleteLater()
        local_path = context.get("local_path")
        if local_path is not None:
            with contextlib.suppress(Exception):
                Path(local_path).unlink(missing_ok=True)
        self._squiglink_upload_context = None

    def _on_squiglink_upload_finished(self, result: dict) -> None:
        context = self._squiglink_upload_context or {}
        filename = str(result.get("filename") or context.get("filename") or "")
        phone_book_status = str(result.get("phone_book_status") or "")
        if context.get("remember"):
            # Only a credential that actually worked is written to disk.
            self._settings.set(
                "squiglink_credentials_encrypted",
                encrypt_credentials(context.get("username", ""), context.get("password", "")),
            )
        self._finish_squiglink_upload()
        self._statusbar.showMessage("Upload to Squiglink completed successfully.")
        self._log_event("INFO", "upload", "Squiglink upload completed", filename=filename)
        QMessageBox.information(
            self,
            "Upload Complete",
            f"Upload to Squiglink completed successfully.\n\n{phone_book_status}",
        )

    def _on_squiglink_upload_failed(self, message: str) -> None:
        self._finish_squiglink_upload()
        if message.strip().lower().startswith("upload canceled"):
            self._statusbar.showMessage("Upload to Squiglink canceled.")
            self._log_event("INFO", "upload", "Squiglink upload canceled")
            return
        self._statusbar.showMessage(f"Upload to Squiglink failed: {message}")
        self._log_event("ERROR", "upload", "Squiglink upload failed", error=message)
        QMessageBox.warning(self, "Upload Failed", f"Upload to Squiglink failed.\n\n{message}")

    def _ensure_upload_metadata(self, *, required_side: str | None = None) -> bool:
        side = (getattr(self._session, "channel_side", "") or "").strip().upper()
        brand = (getattr(self._session, "brand", "") or "").strip()
        model = (getattr(self._session, "model", "") or "").strip()
        if brand and model and (required_side in {"L", "R"} or side in {"L", "R"}):
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
        dialog.setInformativeText(f"{detail_message}\n\nChoose how to proceed with this upload:")
        create_btn = dialog.addButton(
            "Create Fresh Phone Book",
            QMessageBox.ButtonRole.AcceptRole,
        )
        dialog.addButton(
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
        ask_fallback: Callable[[str], str] | None = None,
        host_keys: dict | None = None,
        confirm_host_key: Callable[[str, int, str, str], bool] | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    ) -> str:
        """Merge this upload into the remote phone book.

        ``ask_fallback`` defaults to the modal dialog, so this stays callable
        straight from the GUI thread; the upload worker passes its own
        thread-safe prompt instead.
        """
        ask = ask_fallback or self._ask_phone_book_fallback_mode
        transport, sftp = open_sftp_connection(
            host=host,
            port=port,
            username=username,
            password=password,
            diagnostic=self._log_sftp_diagnostic,
            host_keys=host_keys,
            confirm_host_key=confirm_host_key,
            connect_timeout=connect_timeout,
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
                    mode = ask(str(exc))
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
        if not self._confirm_measure_close():
            event.ignore()
            return

        self._close_pass_fail_dialog()
        self._close_rnd_review_dialog()
        with contextlib.suppress(Exception):
            self._device_poller.stop()

        # Join the sweep thread before Qt tears the window down; a live
        # PortAudio duplex stream at interpreter exit crashes on some hosts.
        with contextlib.suppress(Exception):
            self._sweep_runner.shutdown()

        with contextlib.suppress(Exception):
            self._level_monitor.stop()

        with contextlib.suppress(Exception):
            self._dual_level_monitor.stop()

        with contextlib.suppress(Exception):
            self._stop_channel_balance()

        try:
            if self._update_check_thread is not None and self._update_check_thread.isRunning():
                self._update_check_thread.quit()
                self._update_check_thread.wait(500)
        except Exception:
            pass

        # A Squiglink upload in flight: ask it to stop and give it a moment so
        # its QThread is not destroyed while running.
        try:
            context = self._squiglink_upload_context or {}
            upload_worker = context.get("worker")
            upload_thread = context.get("thread")
            if upload_worker is not None:
                upload_worker.cancel()
            if upload_thread is not None and upload_thread.isRunning():
                upload_thread.quit()
                upload_thread.wait(2000)
        except Exception:
            pass

        try:
            self._rnd_recovery.shutdown_clean()
        except Exception as exc:
            self._log_event("ERROR", "rnd", "R&D recovery cleanup failed", error=str(exc))

        try:
            self._measure_recovery.shutdown_clean()
        except Exception as exc:
            self._log_event("ERROR", "measure", "Measure recovery cleanup failed", error=str(exc))

        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().closeEvent(event)

    def _confirm_measure_close(self) -> bool:
        """Offer to save the Measure session before Fastgraph closes.

        A workspace with no unsaved changes closes silently: everything on
        screen is already in a file, so there is nothing to lose.
        """
        if not self._measure_dirty:
            return True
        if not bool(self._settings.get("confirm_discard_measurements")):
            return True
        kept = len(self._two_channel_pairs) if self._two_channel_enabled else len(self._kept_curves)
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle("Save Measure Session?")
        dialog.setText(
            f"The Measure session has unsaved changes "
            f"({kept} kept measurement{'s' if kept != 1 else ''})."
        )
        dialog.setInformativeText("Save the Measure session before Fastgraph closes?")
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        dialog.setDefaultButton(
            QMessageBox.StandardButton.Save
            if self._measure_session_path is not None
            else QMessageBox.StandardButton.Cancel
        )
        result = dialog.exec()
        if result == QMessageBox.StandardButton.Save:
            return self._save_measure_session()
        return result == QMessageBox.StandardButton.Discard

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
        output = (
            self._current_output_device_label()
            if hasattr(self, "_out_dev_combo")
            else "Not selected"
        )
        input_name = (
            self._current_input_device_label() if hasattr(self, "_in_dev_combo") else "Not selected"
        )
        channel = self._current_input_channel() + 1 if hasattr(self, "_ch_combo") else 1
        self._inputs_btn.setToolTip(
            f"Output: {output or 'Not selected'}\n"
            f"Input: {input_name or 'Not selected'}\n"
            f"Channel: {channel}"
        )

    def _refresh_window_title(self) -> None:
        # A packaged build may carry a different name (see dms_fastgraph.spec);
        # the source tree and the released bundle read "fastgraph Beta".
        app_name = os.environ.get("FASTGRAPH_APP_NAME", "").strip() or "fastgraph Beta"
        title = f"DMS {app_name} — {self._session.display_name()} @ {self._session.rig}"
        path = getattr(self, "_measure_session_path", None)
        if path is not None:
            # ``.fastgraph-measure.json`` is a two-part suffix, so one ``stem``
            # would leave ``.fastgraph-measure`` behind.
            name = path.name
            if name.lower().endswith(MEASURE_SESSION_EXTENSION):
                name = name[: -len(MEASURE_SESSION_EXTENSION)]
            else:
                name = path.stem
            title = f"{title} • {name}"
        if getattr(self, "_measure_dirty", False):
            title = f"{title}*"
        self.setWindowTitle(title)
