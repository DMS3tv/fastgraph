"""
Main application window.
Orchestrates: device selectors, level meter, dual plot, queue control,
pass/fail UI, HRTF selector, settings/calibration, and export.
"""

import contextlib
import os
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QRect,
    Qt,
    QThread,
    QTimer,
    QUrl,
)
from PyQt6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from dms.audio_engine import SweepWorker
from dms.calibration import CalibrationStore
from dms.channel_balance import ChannelBalanceEngine, frequency_limit
from dms.console import ConsoleEventStore, runtime_diagnostics
from dms.curator.metadata import shared_metadata
from dms.curator.models import CurveData
from dms.curator.parser import load_two_column_txt_curve
from dms.export import (
    export_curve,
    export_variation,
)
from dms.file_io import ensure_extension, same_session_file
from dms.hrtf import HRTFCurve
from dms.measure_persistence import (
    MEASURE_SESSION_EXTENSION,
)
from dms.measure_queue import MAX_SWEEP_ATTEMPTS, MeasurementQueue, QueueState
from dms.measurement_alignment import (
    MeasurementWarningReason,
    format_diagnostics_summary,
    is_device_failure,
    is_retryable_timing_failure,
)
from dms.processing import (
    HarmonicAnalysis,
    VariationBand,
    absolute_spl_offset_db,
    compute_frequency_response,
    compute_rms_average,
    deconvolve_sweep,
    downsample_to_log_points,
    generate_log_sweep,
    harmonic_responses,
    normalize_at_1khz,
    percentile_band,
    smooth_fractional_octave,
)
from dms.recovery import RecoveryCandidate, rnd_recovery_manager
from dms.rnd.models import (
    RnDGroup,
    RnDMeasurement,
    generate_measurement_name,
)
from dms.rnd.models import (
    group_variation as rnd_group_variation,
)
from dms.rnd.persistence import (
    RND_SESSION_EXTENSION,
    load_rnd_session,
    save_rnd_session,
)
from dms.rnd.persistence import (
    session_snapshot as rnd_persistence_snapshot,
)
from dms.session import SessionData
from dms.settings_manager import SettingsManager, config_dir
from dms.shortcuts import SHORTCUT_ACTIONS, shortcut_bindings_from_settings
from dms.theme import ThemeController
from dms.two_channel import (
    TwoChannelCurvePair,
    channel_curves,
    combined_pair_curves,
    curve_label_for_selection,
    shared_normalize_pair_at_1khz,
)
from dms.ui.automation_widget import AutomationWidget
from dms.ui.command_controller import CommandController
from dms.ui.console_widget import ConsoleWidget
from dms.ui.curator_widget import CuratorWidget
from dms.ui.device_controller import DeviceController
from dms.ui.measure_compare import MeasureCompare
from dms.ui.measure_dialogs import (
    PassFailDialog,
    RnDRecoveryDialog,
    RnDReviewDialog,
)
from dms.ui.measure_io import MeasureIO
from dms.ui.measure_tab import MeasureTab
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
from dms.ui.squiglink_controller import SquiglinkController
from dms.ui.sweep_runner import SweepRunner
from dms.ui.theme_surface import DitherSurface
from dms.ui.toggle_switch import ToggleSwitch
from dms.ui.update_check import UpdateCheck
from dms.version import __version__

_MEASUREMENT_F_MIN = 20.0
_MEASUREMENT_F_MAX = 20000.0
_DISPLAY_AVG_POINTS = 1200
_DISPLAY_AVG_SMOOTHING = 48
#: Harmonic analysis needs a clean recording; below this SNR the distortion
#: packets are indistinguishable from the noise floor, so nothing is computed.
_DISTORTION_MIN_SNR_DB = 20.0


_QUEUE_AMBIENT_WARN_DBFS = -45.0
ROOT_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
HRTF_DIR = ROOT_DIR / "HRTFs"


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


class _BalanceThread(QThread):
    def __init__(self, engine: ChannelBalanceEngine, **kwargs) -> None:
        super().__init__()
        self._engine = engine
        self._kwargs = kwargs

    def run(self) -> None:
        self._engine.run(**self._kwargs)


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
        self._queue = MeasurementQueue(max_attempts=MAX_SWEEP_ATTEMPTS)
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

        self._state = QueueState.IDLE
        self._kept_curves: list[tuple[np.ndarray, np.ndarray]] = []
        self._average: tuple[np.ndarray, np.ndarray] | None = None
        self._variation: VariationBand | None = None
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

        self._sweep_runner = SweepRunner(self)
        self._sweep_runner.idle.connect(self._on_sweep_thread_finished)
        # Harmonic analysis of the most recently kept sweep. The queue's own
        # last_distortion is cleared by the reset that follows Keep, so the
        # overlay would otherwise vanish the moment a sweep is accepted.
        self._kept_distortion: HarmonicAnalysis | None = None
        self._pass_fail_dialog: PassFailDialog | None = None
        self._rnd_review_dialog: RnDReviewDialog | None = None
        self._rnd_sweep_active = False

        self._last_timing_quality: tuple[float, float, float, float] | None = None
        self._last_measurement_diagnostics: object | None = None
        self._hrtf_options: list[tuple[str, str]] = []
        self._console_events = ConsoleEventStore(
            parent=self,
            log_path=config_dir() / "logs" / "fastgraph-console.log",
        )
        self._report_settings_load_problems()
        self.squiglink = SquiglinkController(self)
        self.commands = CommandController(self)
        self._keyboard_shortcuts: list[QShortcut] = []
        self._rnd_dirty = False
        self._restored_recovery_candidate: RecoveryCandidate | None = None
        self.measure_io = MeasureIO(self)
        self.measure_compare = MeasureCompare(self)
        self.devices = DeviceController(self)

        self._refresh_window_title()
        self.setMinimumSize(1280, 700)

        self._build_ui()
        self._rnd_recovery = rnd_recovery_manager(
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
            self.devices.apply_bluetooth_headphone_mode_settings(
                notify=False,
                preserve_standard=False,
            )
        self._restore_hrtf_state()
        self.measure_compare.restore()
        self.devices.refresh_devices()
        self.devices.start_level_monitor()
        self._apply_state_ui()
        self.update_check.start()
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
        QTimer.singleShot(0, self.measure_io.initialize_recovery)

        self._balance_ui_timer = QTimer(self)
        self._balance_ui_timer.setInterval(33)
        self._balance_ui_timer.timeout.connect(self._refresh_balance_scope)

        self.devices.start()

    def _build_ui(self) -> None:
        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)
        self._tabs.setCornerWidget(
            self._build_tab_header(),
            Qt.Corner.TopLeftCorner,
        )
        self._plots = MeasureWorkspace()
        self._plots.measurement_files_dropped.connect(self._import_dropped_measurement_files)
        self._plots.selection_changed.connect(self._on_two_channel_selection_changed)
        self._plots.balance_start_requested.connect(self._start_channel_balance)
        self._plots.balance_stop_requested.connect(self._stop_channel_balance)
        self._plots.balance_parameters_changed.connect(self._on_balance_parameters_changed)
        self.measure_tab = MeasureTab(self)
        self._inputs_overlay_open = False
        QApplication.instance().installEventFilter(self)
        self._refresh_hrtf_options()
        self._build_metadata_overlay()
        self._tabs.addTab(self.measure_tab, "Measure")

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
        self._rnd_widget.input_channel_changed.connect(self.devices.on_rnd_input_channel_changed)
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
        self._console_widget.command_submitted.connect(self.commands.run_console_command)
        self._automation_widget = AutomationWidget(
            self._console_widget,
            self.commands.automation_default_dir,
            lambda: __version__,
            parent=self,
        )
        self._automation_widget.run_requested.connect(self.commands.run_automation)
        self._tabs.addTab(self._automation_widget, "Automation")

        self._settings_widget = SettingsWidget(self._settings, self)
        self._settings_widget.settings_changed.connect(self._on_settings_tab_changed)
        self._settings_widget.calibration_requested.connect(self.devices.open_calibration)
        self._settings_widget.test_level_requested.connect(self.devices.open_test_level)
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
        self.update_check = UpdateCheck(self)

    def _on_tab_changed(self, _index: int) -> None:
        self._close_inputs_overlay()
        self._close_metadata_overlay()
        if self._tabs.currentWidget() is not self.measure_tab:
            self._stop_channel_balance()
        if self._tabs.currentWidget() is self._settings_scroll:
            self._settings_widget.refresh_from_settings()

    def _on_two_channel_toggled(self, _state: int) -> None:
        if self._state != QueueState.IDLE:
            self.measure_tab.two_channel_toggle.blockSignals(True)
            self.measure_tab.two_channel_toggle.setChecked(self._two_channel_enabled)
            self.measure_tab.two_channel_toggle.blockSignals(False)
            return
        enabled = bool(self.measure_tab.two_channel_toggle.isChecked())
        if not enabled:
            self._stop_channel_balance()
            self.measure_tab.measure_frequency_button.setChecked(True)
        self._two_channel_enabled = enabled
        self._queue.two_channel = enabled
        self._settings.set("measure_two_channel_enabled", enabled)
        self._plots.set_two_channel_enabled(enabled)
        self.measure_tab.measure_submode_control.setVisible(enabled)
        self.measure_tab.sync_queue_bar_submode_width()
        self.measure_tab.level_meter_2.setVisible(enabled)
        self.measure_tab.level_status_label_2.setVisible(enabled)
        self.measure_tab.bottom_layout_label.setVisible(enabled)
        self.measure_tab.bottom_layout_combo.setVisible(enabled)
        self.measure_tab.ch_combo.setEnabled(not enabled)
        self._update_queue_progress()
        self._update_plots()
        self.devices.start_level_monitor()
        self._apply_state_ui()
        mode = "Two Channel" if enabled else "Single Channel"
        self._statusbar.showMessage(f"Measure mode: {mode}.")

    def _channel_balance_mode_active(self) -> bool:
        balance_button = getattr(getattr(self, "measure_tab", None), "measure_balance_button", None)
        return bool(
            self._two_channel_enabled and balance_button is not None and balance_button.isChecked()
        )

    def _on_measure_submode_toggled(self, _checked: bool) -> None:
        balance = self._channel_balance_mode_active()
        if not balance:
            self._stop_channel_balance()
        self._plots.two.set_balance_mode(balance)
        self.measure_tab.bottom_layout_label.setVisible(self._two_channel_enabled and not balance)
        self.measure_tab.bottom_layout_combo.setVisible(self._two_channel_enabled and not balance)
        self.measure_tab.variation_toggle.setVisible(not balance)
        self.measure_tab.distortion_toggle.setVisible(not balance)
        self.measure_tab.hrtf_toggle.setVisible(not balance)
        self.measure_tab.hrtf_combo.setVisible(not balance)
        self.measure_tab.hrtf_label.setVisible(not balance)
        self.measure_tab.level_mode_label.setVisible(not balance)
        self.measure_tab.level_mode_combo.setVisible(not balance)
        self.measure_tab.level_meter.setVisible(not balance)
        self.measure_tab.level_meter_2.setVisible(self._two_channel_enabled and not balance)
        self.measure_tab.level_status_label.setVisible(not balance)
        self.measure_tab.level_status_label_2.setVisible(self._two_channel_enabled and not balance)
        self._plots.two.set_generator_level(float(self.measure_tab.queue_level_spin.value()))
        self._plots.two.set_frequency_limit(frequency_limit(int(self._settings.get("sample_rate"))))
        if balance:
            self.devices.stop_level_monitor()
        else:
            self.devices.start_level_monitor()
            self._update_plots()
        self._apply_state_ui()

    def _on_two_channel_bottom_mode_changed(self, _index: int) -> None:
        mode = str(self.measure_tab.bottom_layout_combo.currentData() or "combined")
        self._two_channel_bottom_mode = "separate" if mode == "separate" else "combined"
        self._settings.set("measure_two_channel_bottom_mode", self._two_channel_bottom_mode)
        self._plots.two.set_bottom_mode(self._two_channel_bottom_mode)
        self._update_plots()

    def _on_two_channel_selection_changed(self, selection: str) -> None:
        if selection in {"channel_1", "channel_2"}:
            self._two_channel_selection = selection
        self.measure_io.sync_export_button()

    def _start_channel_balance(self) -> None:
        if self._channel_balance_active:
            return
        if self._state != QueueState.IDLE or not self._channel_balance_mode_active():
            return
        if not self.devices.two_channel_devices_ready():
            QMessageBox.warning(
                self,
                "Two Channels Required",
                "Channel Balance needs an input device and an output device with at least two channels.",
            )
            return
        input_device = self.devices.current_input_device()
        output_device = self.devices.current_output_device()
        if input_device is None or output_device is None:
            return

        self.devices.stop_level_monitor()
        self._balance_level_db = float(self.measure_tab.queue_level_spin.value())
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
            latency=self.devices.sweep_latency_mode(),
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
        if abs(float(self.measure_tab.queue_level_spin.value()) - self._balance_level_db) > 1e-9:
            self.measure_tab.queue_level_spin.setValue(self._balance_level_db)
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
        target = self._inputs_overlay_geometry(
            max(1, self.measure_tab.inputs_overlay.sizeHint().height())
        )
        start = QRect(target.x(), target.y(), target.width(), 0)
        self.measure_tab.inputs_overlay.setGeometry(start)
        self.measure_tab.inputs_overlay.show()
        self.measure_tab.inputs_overlay.raise_()
        self.measure_tab.inputs_overlay_animation.stop()
        self.measure_tab.inputs_overlay_animation.setStartValue(start)
        self.measure_tab.inputs_overlay_animation.setEndValue(target)
        self.measure_tab.inputs_overlay_animation.start()

    def _close_inputs_overlay(self) -> None:
        if not getattr(self, "_inputs_overlay_open", False):
            return
        self._inputs_overlay_open = False
        self.measure_tab.inputs_overlay_animation.stop()
        start = self.measure_tab.inputs_overlay.geometry()
        self.measure_tab.inputs_overlay_animation.setStartValue(start)
        self.measure_tab.inputs_overlay_animation.setEndValue(
            QRect(start.x(), start.y(), start.width(), 0)
        )
        self.measure_tab.inputs_overlay_animation.start()

    def _on_inputs_overlay_animation_finished(self) -> None:
        if not self._inputs_overlay_open:
            self.measure_tab.inputs_overlay.hide()

    def _position_inputs_overlay(self) -> None:
        if not hasattr(self, "measure_tab"):
            return
        self.measure_tab.inputs_overlay.setGeometry(
            self._inputs_overlay_geometry(self.measure_tab.inputs_overlay.height())
        )
        self.measure_tab.inputs_overlay.raise_()

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
                    and not self._global_point_inside(self.measure_tab.inputs_overlay, point)
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
        if hasattr(self, "measure_tab"):
            self._position_inputs_overlay()
        if hasattr(self, "_metadata_overlay"):
            self._position_metadata_overlay()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if hasattr(self, "measure_tab"):
            self._position_inputs_overlay()
        if hasattr(self, "_metadata_overlay"):
            self._position_metadata_overlay()

    def _on_settings_tab_changed(self, key: str, _value: object) -> None:
        if key in {"sample_rate", "buffer_size", "latency"}:
            self._stop_channel_balance()
            self._plots.two.set_frequency_limit(
                frequency_limit(int(self._settings.get("sample_rate")))
            )
            self.devices.start_level_monitor()
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
        for shortcut in self._keyboard_shortcuts:
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
        if self._state != QueueState.PASS_FAIL:
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
        self._bluetooth_mode_toggle.stateChanged.connect(self.devices.on_bluetooth_mode_changed)
        row.addWidget(self._bluetooth_mode_toggle)
        self._refresh_session_labels()
        return header

    def _on_theme_changed(self, theme: str, log: bool = True) -> None:
        brand = self._theme_controller.brand_mode
        measure_tab = getattr(self, "measure_tab", None)
        if measure_tab is not None:
            measure_tab.measure_submode_control.refresh_segment_widths()
        plots = getattr(self, "_plots", None)
        if plots is not None:
            plots.apply_theme(theme, brand_mode=brand)
        rnd = getattr(self, "_rnd_widget", None)
        if rnd is not None:
            rnd.apply_theme(theme, brand_mode=brand)
        curator = getattr(self, "_curator_widget", None)
        if curator is not None:
            curator.apply_theme(theme, brand_mode=brand)
        if measure_tab is not None:
            measure_tab.level_meter.update()
            measure_tab.level_meter_2.update()
        if log and hasattr(self, "_console_events"):
            self._log_event("INFO", "theme", "Application theme changed", theme=theme)

    def _on_brand_mode_changed(self, enabled: bool) -> None:
        settings_widget = getattr(self, "_settings_widget", None)
        if settings_widget is not None:
            settings_widget.refresh_from_settings()
        self._on_theme_changed(self._theme_controller.theme, log=False)
        if hasattr(self, "measure_tab"):
            self.measure_io.sync_export_button()
        if hasattr(self, "_console_events"):
            self._log_event("INFO", "theme", "brand mode changed", brand_mode=enabled)

    def _log_event(self, severity: str, source: str, message: str, **details) -> None:
        self._console_events.publish(severity, source, message, details)
        if (
            severity.upper() == "ERROR"
            and source != "automation"
            and hasattr(self, "_automation_widget")
            and not self.commands.running
        ):
            QTimer.singleShot(0, lambda: self.commands.trigger("app_error"))

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

        cal_error = getattr(self._cal_store, "load_error", None)
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

    def _queue_active(self) -> bool:
        return self._queue_target > 0

    def _is_hrtf_active(self) -> bool:
        return self._hrtf is not None and self.measure_tab.hrtf_toggle.isChecked()

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

        current_path = self._hrtf.path if self._hrtf is not None else ""
        self.measure_tab.hrtf_combo.blockSignals(True)
        self.measure_tab.hrtf_combo.clear()
        for label, value in self._hrtf_options:
            self.measure_tab.hrtf_combo.addItem(label, value)
        index = self.measure_tab.hrtf_combo.findData(current_path)
        self.measure_tab.hrtf_combo.setCurrentIndex(index if index >= 0 else 0)
        self.measure_tab.hrtf_combo.blockSignals(False)

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
        self.measure_tab.hrtf_toggle.setEnabled(has_hrtf)

        if has_hrtf:
            self.measure_tab.hrtf_label.setText(Path(self._hrtf.path).stem)
            self.measure_tab.hrtf_label.setToolTip(self._hrtf.path)
            index = self.measure_tab.hrtf_combo.findData(self._hrtf.path)
        else:
            self.measure_tab.hrtf_label.setText("None")
            self.measure_tab.hrtf_label.setToolTip("")
            self.measure_tab.hrtf_toggle.setChecked(False)
            index = 0

        self.measure_tab.hrtf_combo.blockSignals(True)
        self.measure_tab.hrtf_combo.setCurrentIndex(index if index >= 0 else 0)
        self.measure_tab.hrtf_combo.blockSignals(False)

    def _abort_active_sweep(self) -> None:
        """Stop the running sweep and wait for its thread to end.

        Waiting matters: sounddevice's play/record state is process-global, so
        a stale thread that stops later would truncate the next sweep.
        """
        with contextlib.suppress(Exception):
            self._sweep_runner.abort()

    def _apply_state_ui(self) -> None:
        if (
            self.devices.dirty
            and self._queue.allows_device_reselect()
            and not self._rnd_sweep_active
        ):
            self.devices.dirty = False
            self.devices.refresh_devices()
        self.devices.sync_device_poller()
        idle = self._state == QueueState.IDLE
        pass_fail = self._state == QueueState.PASS_FAIL
        busy = self._state in {QueueState.SWEEPING, QueueState.QUEUE_RUNNING}
        balance_mode = self._channel_balance_mode_active()

        single_device_ok = (
            self.devices.current_output_device() is not None
            and self.devices.current_input_device() is not None
            and self.measure_tab.ch_combo.count() > 0
            and self.devices.selected_audio_pair_is_compatible()
        )
        device_ok = (
            self.devices.two_channel_devices_ready()
            if self._two_channel_enabled
            else single_device_ok
        )

        for widget in (
            self.measure_tab.out_dev_combo,
            self.measure_tab.in_dev_combo,
            self.measure_tab.ch_combo,
            self.measure_tab.queue_n_spin,
            self.measure_tab.queue_level_spin,
            self.measure_tab.queue_level_persist_toggle,
            self.measure_tab.queue_level_persist_label,
            self._bluetooth_mode_toggle,
            self.measure_tab.variation_toggle,
            self.measure_tab.distortion_toggle,
            self.measure_tab.level_mode_combo,
            self.measure_tab.hrtf_combo,
            self.measure_tab.undo_btn,
            self.measure_tab.clear_btn,
            self._metadata_btn,
            self._clear_metadata_btn,
            self.measure_tab.advanced_windows_drivers_toggle,
            self.measure_tab.refresh_devices_btn,
        ):
            widget.setEnabled(idle)

        for name in ("session_menu_btn", "compare_menu_btn"):
            widget = getattr(self.measure_tab, name, None)
            if widget is not None:
                widget.setEnabled(idle)

        self.measure_tab.two_channel_toggle.setEnabled(idle)
        self.measure_tab.measure_submode_control.setEnabled(idle)
        self.measure_tab.bottom_layout_combo.setEnabled(idle)
        self.measure_tab.ch_combo.setEnabled(idle and not self._two_channel_enabled)

        self.measure_tab.hrtf_toggle.setEnabled(idle and self._hrtf is not None)
        self._settings_widget.set_editing_enabled(idle)
        if not idle:
            self._close_metadata_overlay()
        self._rnd_widget.set_busy(not idle)
        self.measure_tab.start_queue_btn.setEnabled(idle and device_ok and not balance_mode)
        self.measure_tab.cancel_queue_btn.setEnabled(busy or pass_fail)
        active_count = (
            len(self._two_channel_pairs) if self._two_channel_enabled else len(self._kept_curves)
        )
        self.measure_tab.undo_btn.setEnabled(idle and active_count > 0)
        has_measurements = (
            bool(self._two_channel_pairs) or self._pending_pair is not None
            if self._two_channel_enabled
            else bool(self._kept_curves) or self._pending_curve is not None
        )
        self.measure_tab.clear_btn.setEnabled(idle and has_measurements)
        self.measure_io.sync_export_button()

    def _start_queue(self) -> None:
        if self._state != QueueState.IDLE:
            return

        # The Measure button is disabled in Channel Balance mode, but the
        # keyboard shortcut, the console and automations reach this method
        # directly; the guard must live here.
        if self._channel_balance_mode_active() or self._channel_balance_active:
            self._statusbar.showMessage(
                "Start blocked: switch to Frequency Response and stop Channel Balance first."
            )
            return

        if self.devices.current_output_device() is None:
            QMessageBox.warning(self, "No Output Device", "Select an output device.")
            return

        if self.devices.current_input_device() is None:
            QMessageBox.warning(self, "No Input Device", "Select an input device.")
            return

        if not self.devices.selected_audio_pair_is_compatible():
            QMessageBox.warning(
                self,
                "Windows Audio Driver Mismatch",
                self.devices.windows_audio_pair_message(),
            )
            self._statusbar.showMessage(
                "Queue start blocked: Windows input/output driver backends do not match."
            )
            return

        if self.measure_tab.ch_combo.count() == 0:
            QMessageBox.warning(
                self,
                "No Input Channel",
                "Selected input device has no available input channels.",
            )
            return

        if self._two_channel_enabled and not self.devices.two_channel_devices_ready():
            QMessageBox.warning(
                self,
                "Two Channels Required",
                "Two Channel measurement needs an input device and an output device with at least two channels.",
            )
            return

        ambient_dbfs = float(self.devices.last_level_dbfs)
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

        self._queue_target = int(self.measure_tab.queue_n_spin.value())
        self._queue_index = 0
        self._current_sweep_attempts = 0
        overrides = (
            self._settings.session_overrides()
            if hasattr(self._settings, "session_overrides")
            else {}
        )
        if "queue_count" not in overrides:
            self._settings.set("queue_count", self._queue_target)

        self.measure_tab.queue_progress_bar.setRange(0, max(1, self._queue_target))
        self.measure_tab.queue_progress_bar.setValue(0)
        kept_count = (
            len(self._two_channel_pairs) if self._two_channel_enabled else len(self._kept_curves)
        )
        self.measure_tab.queue_progress_label.setText(f"Kept: {kept_count}")

        self._state = QueueState.QUEUE_RUNNING
        self._apply_state_ui()
        self._statusbar.showMessage("Queue started.")
        self._start_next_sweep()

    def _start_next_sweep(self, *, second_stage: bool = False) -> None:
        if not self._queue_active():
            self._state = QueueState.IDLE
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
        self._state = QueueState.SWEEPING
        self._apply_state_ui()
        self.measure_tab.sweep_progress.setValue(0)

        output_device = self.devices.current_output_device()
        input_device = self.devices.current_input_device()
        input_channel = (
            self._two_channel_stage - 1
            if self._two_channel_enabled
            else self.devices.current_input_channel()
        )
        output_channel = input_channel if self._two_channel_enabled else None

        if output_device is None or input_device is None:
            self._on_sweep_error("Selected device is unavailable.")
            return

        if not self.devices.selected_audio_pair_is_compatible():
            self._on_sweep_error(self.devices.windows_audio_pair_message())
            return

        self.devices.stop_level_monitor()
        self._last_timing_quality = None
        self._last_measurement_diagnostics = None

        sweep = generate_log_sweep(
            duration=float(self._settings.get("sweep_duration")),
            fs=int(self._settings.get("sample_rate")),
            f_low=_MEASUREMENT_F_MIN,
            f_high=_MEASUREMENT_F_MAX,
        )
        output_level_db = float(self.measure_tab.queue_level_spin.value())
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
            output_device_label=self.devices.current_output_device_label(),
            input_device_label=self.devices.current_input_device_label(),
            input_channel=input_channel,
            output_channel=output_channel,
            fs=int(self._settings.get("sample_rate")),
            buffer_size=int(self._settings.get("buffer_size")),
            pre_silence=float(self._settings.get("pre_sweep_silence")),
            post_silence=float(self._settings.get("post_sweep_silence")),
            latency=self.devices.sweep_latency_mode(),
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
            output_level_db=float(self.measure_tab.queue_level_spin.value()),
            input_channel=input_channel + 1,
            output_channel=(output_channel + 1) if output_channel is not None else None,
        )

    def _start_second_two_channel_sweep(self) -> None:
        if self._queue_active() and self._pending_pair_first_raw is not None:
            self._start_next_sweep(second_stage=True)

    def _on_sweep_progress(self, frac: float) -> None:
        self.measure_tab.sweep_progress.setValue(int(max(0.0, min(1.0, frac)) * 100.0))

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
                    self._state = QueueState.QUEUE_RUNNING
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
                self._state = QueueState.PASS_FAIL
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
            self._state = QueueState.PASS_FAIL
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
        self._close_pass_fail_dialog()
        self._pending_curve = None
        self._pending_pair = None
        self._pending_pair_first_raw = None
        self._pending_pair_first_diagnostics = None
        self._start_second_pair_stage = False
        self._two_channel_stage = 0
        self._last_timing_quality = None
        self.measure_tab.sweep_progress.setValue(0)

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
            self._two_channel_enabled
            and self._queue_active()
            and not is_device_failure(message, failure_reason)
        )
        if (
            self._queue_active()
            and (is_timing_quality_error or retry_complete_pair)
            and self._current_sweep_attempts < MAX_SWEEP_ATTEMPTS
        ):
            diagnostics_text = ""
            if (
                self._last_measurement_diagnostics is not None
                and getattr(self._last_measurement_diagnostics, "failure_reason", None) is not None
            ):
                diagnostics_text = "\n\n" + format_diagnostics_summary(
                    self._last_measurement_diagnostics
                )
            self._state = QueueState.QUEUE_RUNNING
            self._apply_state_ui()
            self.devices.start_level_monitor()
            retry_subject = (
                "The two-channel pair failed. Both channels will be measured again."
                if retry_complete_pair
                else f"Measurement {self._queue_index + 1} did not meet timing quality."
            )
            retry_msg = (
                f"{message}\n\n{retry_subject}\n"
                f"Retry attempt {self._current_sweep_attempts + 1} of {MAX_SWEEP_ATTEMPTS}?"
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
                    f"({self._current_sweep_attempts}/{MAX_SWEEP_ATTEMPTS})..."
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
        self._state = QueueState.IDLE
        self._update_queue_progress()
        self._apply_state_ui()
        self.devices.start_level_monitor()
        self._statusbar.showMessage(message)
        QMessageBox.warning(self, "Sweep Error", dialog_message)

    def _on_sweep_thread_finished(self) -> None:
        if self._start_second_pair_stage:
            self._start_second_pair_stage = False
            QTimer.singleShot(0, self._start_second_two_channel_sweep)
            return
        if self._state != QueueState.PASS_FAIL:
            self.devices.start_level_monitor()

    def _on_keep(self) -> None:
        if self._state != QueueState.PASS_FAIL:
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
            self.measure_io.mark_dirty()
            self.commands.trigger("measurement_kept")
            if self._queue_index >= self._queue_target:
                self._finish_queue()
                return
            self._state = QueueState.QUEUE_RUNNING
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
        self.commands.trigger("measurement_kept")
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
        self.measure_io.mark_dirty()
        match = self.measure_compare.target_match_message()
        if match:
            self._statusbar.showMessage(f"Kept {len(self._kept_curves)} measurement(s). {match}")

        if self._queue_index >= self._queue_target:
            self._finish_queue()
            return

        self._state = QueueState.QUEUE_RUNNING
        self._apply_state_ui()
        self._start_next_sweep()

    def _on_fail(self) -> None:
        if self._state != QueueState.PASS_FAIL:
            return

        self._close_pass_fail_dialog()
        self._log_event("WARNING", "review", "Measurement rejected", index=self._queue_index + 1)
        # A manual Fail repeats the same index with a fresh retry budget; the
        # attempts that produced the rejected sweep were not timing failures.
        self._queue.reset(keep_counters=True)
        self._current_sweep_attempts = 0
        self._state = QueueState.QUEUE_RUNNING
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
        self._state = QueueState.IDLE
        self.measure_tab.sweep_progress.setValue(0)
        self._update_queue_progress()
        self._update_plots()
        self._apply_state_ui()
        self.devices.start_level_monitor()
        self._statusbar.showMessage("Queue canceled.")

    def _finish_queue(self) -> None:
        self._queue.reset()
        self._state = QueueState.IDLE
        self.measure_tab.sweep_progress.setValue(100)
        self._apply_state_ui()
        self.devices.start_level_monitor()
        match = self.measure_compare.target_match_message()
        self._statusbar.showMessage(f"Queue complete. {match}" if match else "Queue complete.")
        self.commands.trigger("queue_complete")

    def _update_queue_progress(self) -> None:
        target = max(0, self._queue_target)
        self.measure_tab.queue_progress_bar.setRange(0, max(1, target))
        self.measure_tab.queue_progress_bar.setValue(min(self._queue_index, max(1, target)))
        kept_count = (
            len(self._two_channel_pairs) if self._two_channel_enabled else len(self._kept_curves)
        )
        self.measure_tab.queue_progress_label.setText(f"Kept: {kept_count}")

    def _show_pass_fail_dialog(self) -> None:
        pending_available = (
            self._pending_pair is not None
            if self._two_channel_enabled
            else self._pending_curve is not None
        )
        if self._state != QueueState.PASS_FAIL or not pending_available:
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
            deviation_summary=self.measure_compare.pending_deviation_summary(),
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
        if self._state != QueueState.IDLE:
            return
        if self.devices.current_output_device() is None:
            QMessageBox.warning(self, "No Output Device", "Select an output device.")
            return
        if self.devices.current_input_device() is None:
            QMessageBox.warning(self, "No Input Device", "Select an input device.")
            return
        if not self.devices.selected_audio_pair_is_compatible():
            QMessageBox.warning(
                self,
                "Windows Audio Driver Mismatch",
                self.devices.windows_audio_pair_message(),
            )
            return
        if self.measure_tab.ch_combo.count() == 0:
            QMessageBox.warning(
                self,
                "No Input Channel",
                "Selected input device has no available input channels.",
            )
            return
        ambient_dbfs = float(self.devices.last_level_dbfs)
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
        self._state = QueueState.SWEEPING
        self._apply_state_ui()
        self._rnd_widget.set_status(f"Sweeping attempt {self._current_sweep_attempts}...")
        self.measure_tab.sweep_progress.setValue(0)

        output_device = self.devices.current_output_device()
        input_device = self.devices.current_input_device()
        input_channel = self.devices.current_input_channel()
        if output_device is None or input_device is None:
            self._on_rnd_sweep_error("Selected device is unavailable.")
            return
        if not self.devices.selected_audio_pair_is_compatible():
            self._on_rnd_sweep_error(self.devices.windows_audio_pair_message())
            return

        self.devices.level_monitor.stop()
        self._last_timing_quality = None
        self._last_measurement_diagnostics = None

        sweep = generate_log_sweep(
            duration=float(self._settings.get("sweep_duration")),
            fs=int(self._settings.get("sample_rate")),
            f_low=_MEASUREMENT_F_MIN,
            f_high=_MEASUREMENT_F_MAX,
        )
        output_level_db = float(self.measure_tab.queue_level_spin.value())
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
            output_device_label=self.devices.current_output_device_label(),
            input_device_label=self.devices.current_input_device_label(),
            input_channel=input_channel,
            fs=int(self._settings.get("sample_rate")),
            buffer_size=int(self._settings.get("buffer_size")),
            pre_silence=float(self._settings.get("pre_sweep_silence")),
            post_silence=float(self._settings.get("post_sweep_silence")),
            latency=self.devices.sweep_latency_mode(),
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
            self._state = QueueState.PASS_FAIL
            self._apply_state_ui()
            self._rnd_widget.set_status("Sweep complete. Waiting for review.")
            self._statusbar.showMessage("R&D sweep complete. Waiting for review.")
            QTimer.singleShot(0, self._show_rnd_review_dialog)
        except Exception as exc:
            self._on_rnd_sweep_error(f"Processing error: {exc}")

    def _on_rnd_sweep_error(self, message: str) -> None:
        self._log_event("ERROR", "rnd", message)
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
        if is_timing_quality_error and self._current_sweep_attempts < MAX_SWEEP_ATTEMPTS:
            choice = QMessageBox.question(
                self,
                "Timing Quality Retry",
                f"{message}\n\nRetry R&D measurement attempt {self._current_sweep_attempts + 1} of {MAX_SWEEP_ATTEMPTS}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if choice == QMessageBox.StandardButton.Yes:
                self._state = QueueState.IDLE
                self._apply_state_ui()
                QTimer.singleShot(150, self._start_rnd_sweep)
                return
        self._rnd_sweep_active = False
        self._current_sweep_attempts = 0
        self._state = QueueState.IDLE
        self.measure_tab.sweep_progress.setValue(0)
        self._apply_state_ui()
        self.devices.start_level_monitor()
        self._rnd_widget.set_status("Ready")
        self._statusbar.showMessage(message)
        QMessageBox.warning(self, "R&D Sweep Error", message)

    def _show_rnd_review_dialog(self) -> None:
        if self._state != QueueState.PASS_FAIL or self._pending_curve is None:
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
            self._state = QueueState.IDLE
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
            self.measure_tab.ch_combo.currentText().strip()
            or f"Channel {self.devices.current_input_channel() + 1}"
        )
        existing_names = {item.name for item in self._rnd_widget.session.measurements}
        measurement = RnDMeasurement(
            name=generate_measurement_name(
                self._session,
                self.devices.current_input_device_label(),
                channel_label,
                existing_names,
            ),
            freqs=np.array(freqs, dtype=float, copy=True),
            mag_db=np.array(mag_db, dtype=float, copy=True),
            metadata=self._session.to_dict(),
            rig=self._session.rig,
            input_device_label=self.devices.current_input_device_label(),
            input_channel_index=self.devices.current_input_channel(),
            input_channel_label=channel_label,
            output_device_label=self.devices.current_output_device_label(),
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
        self._state = QueueState.IDLE
        self.measure_tab.sweep_progress.setValue(100)
        self._apply_state_ui()
        self.devices.start_level_monitor()
        self._rnd_widget.set_status("Ready")
        self._statusbar.showMessage(f"R&D measurement kept: {measurement.name}")
        self._log_event(
            "INFO", "rnd", "R&D measurement kept", name=measurement.name, status=change_status
        )
        self.commands.trigger("rnd_measurement_kept")

    def _cancel_rnd_measurement(self) -> None:
        self._abort_active_sweep()
        self._close_rnd_review_dialog()
        self._rnd_widget.set_review_curve(None)
        self._pending_curve = None
        self._rnd_sweep_active = False
        self._current_sweep_attempts = 0
        self._state = QueueState.IDLE
        self.measure_tab.sweep_progress.setValue(0)
        self._apply_state_ui()
        self.devices.start_level_monitor()
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
        if not self._two_channel_enabled:
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
        return len(self._two_channel_pairs) if self._two_channel_enabled else len(self._kept_curves)

    def _active_measure_label(self) -> str:
        if not self._two_channel_enabled:
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
        if self._two_channel_enabled:
            return self._two_channel_variations.get(self._active_two_channel_key())
        return self._variation

    def _recompute_variation(self) -> None:
        active_hrtf = self._hrtf if self._is_hrtf_active() else None
        self._variation = self._variation_from_kept_curves(hrtf=active_hrtf)

    def _variation_from_kept_curves(
        self,
        *,
        hrtf: HRTFCurve | None,
    ) -> VariationBand | None:
        return self._variation_from_curves(
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
    ) -> VariationBand | None:
        if not curves or average is None:
            return None
        variation_hrtf = hrtf is not None and getattr(hrtf, "is_variation", False)
        band = percentile_band(
            curves,
            grid=average[0],
            smoothing=_DISPLAY_AVG_SMOOTHING,
            hrtf=None if variation_hrtf else hrtf,
        )
        return hrtf.apply_to_variation(band) if variation_hrtf else band

    def _average_curve_with_hrtf(
        self,
        hrtf: HRTFCurve | None,
    ) -> tuple[np.ndarray, np.ndarray] | None:
        source = self._active_two_channel_average() if self._two_channel_enabled else self._average
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
        self.measure_compare.sync_layers()
        delta_on = self.measure_compare.delta_view_enabled()
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
                delta = self.measure_compare.delta_result(
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
            self.measure_io.sync_export_button()
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
            delta = self.measure_compare.delta_result(self._bottom_curve_for_display_and_export())
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
        self.measure_io.sync_export_button()

    def _bottom_view_mode(self) -> str:
        if self._is_hrtf_active() and self._hrtf.is_variation:
            return "variation"
        return "variation" if self.measure_tab.variation_toggle.isChecked() else "average"

    def _on_bottom_view_changed(self, *_args) -> None:
        self._update_plots()

    # ------------------------------------------------------------------
    # Distortion overlay
    # ------------------------------------------------------------------

    def _distortion_overlay_enabled(self) -> bool:
        toggle = getattr(getattr(self, "measure_tab", None), "distortion_toggle", None)
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

    def _spl_offset_db(self) -> float | None:
        """dB offset to absolute SPL, or None to stay in 1 kHz reference mode.

        Falls back to reference mode — with a single status-bar note — when
        dB SPL is selected but the input device has no calibration.
        """
        if self._level_mode() != "dbspl":
            return None
        sensitivity = self.devices.calibrated_sensitivity()
        if sensitivity is None:
            if not self._spl_uncalibrated_warned:
                self._spl_uncalibrated_warned = True
                self._statusbar.showMessage(
                    "dB SPL needs a calibrated input device; showing 1 kHz "
                    "reference levels instead."
                )
            return None
        try:
            return absolute_spl_offset_db(
                sensitivity_pa_per_fs=float(sensitivity),
                output_level_db=float(self.measure_tab.queue_level_spin.value()),
            )
        except (TypeError, ValueError):
            return None

    def _has_kept_measurements(self) -> bool:
        if self._two_channel_enabled:
            return bool(self._two_channel_pairs) or self._pending_pair is not None
        return bool(self._kept_curves) or self._pending_curve is not None

    def _sync_level_mode_combo(self) -> None:
        combo = getattr(getattr(self, "measure_tab", None), "level_mode_combo", None)
        if combo is None:
            return
        combo.blockSignals(True)
        combo.setCurrentIndex(1 if self._level_mode() == "dbspl" else 0)
        combo.blockSignals(False)

    def _on_level_mode_changed(self, *_args) -> None:
        """Switch level modes, refusing to mix modes inside one kept set."""
        combo = self.measure_tab.level_mode_combo
        chosen = str(combo.currentData() or "ref_1khz")
        chosen = "dbspl" if chosen == "dbspl" else "ref_1khz"
        if chosen == self._level_mode():
            return
        if self._state != QueueState.IDLE:
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
        if chosen == "dbspl" and self.devices.calibrated_sensitivity() is None:
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
        path = self.measure_tab.hrtf_combo.currentData()
        if not path:
            self._hrtf = None
            self._settings.set("hrtf_path", None)
            self._sync_hrtf_ui()
            self._update_plots()
            self.measure_io.mark_dirty()
            self._statusbar.showMessage("HRTF cleared.")
            return

        try:
            self._hrtf = HRTFCurve(path)
            self._settings.set("hrtf_path", path)
            self._sync_hrtf_ui()
            self.measure_tab.hrtf_toggle.setChecked(True)
            if self._hrtf.is_variation:
                self.measure_tab.variation_toggle.setChecked(True)
            self._update_plots()
            self.measure_io.mark_dirty()
            kind = "population variation compensation" if self._hrtf.is_variation else "HRTF"
            self._statusbar.showMessage(f"Loaded {kind}: {Path(path).name}")
        except Exception as exc:
            QMessageBox.warning(self, "HRTF Load Error", str(exc))
            self._hrtf = None
            self._settings.set("hrtf_path", None)
            self._sync_hrtf_ui()
            self._update_plots()

    def _import_dropped_measurement_files(self, paths: list[str]) -> None:
        if self._two_channel_enabled:
            QMessageBox.information(
                self,
                "Single Channel Only",
                "TXT drag-and-drop import is available only in Single Channel mode.",
            )
            self._statusbar.showMessage("Measurement import blocked: Two Channel mode is active.")
            return
        if self._state != QueueState.IDLE:
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
            self.measure_io.mark_dirty()

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
        if self._state != QueueState.IDLE:
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
            self.measure_compare.sync_layers()
        self._queue.reset()
        self._kept_distortion = None
        self._update_queue_progress()
        self.measure_tab.sweep_progress.setValue(0)
        self.measure_io.sync_export_button()
        self._apply_state_ui()
        self.measure_io.mark_dirty()

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
        if self._state != QueueState.IDLE:
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
        self.measure_io.mark_dirty()
        self._statusbar.showMessage("Last kept measurement removed.")

    def _save_metadata_overlay(self) -> None:
        if not self._metadata_editor.validate():
            return
        self._session = self._metadata_editor.session_data()
        self._refresh_session_labels()
        self._refresh_window_title()
        self._close_metadata_overlay()
        self.measure_io.mark_dirty()
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
        self.measure_io.mark_dirty()
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
            self.measure_tab.queue_level_spin.blockSignals(True)
            self.measure_tab.queue_level_spin.setValue(clamped)
            self.measure_tab.queue_level_spin.blockSignals(False)
        if self.measure_tab.queue_level_persist_toggle.isChecked():
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
        persist = self.measure_tab.queue_level_persist_toggle.isChecked()
        self._settings.set("queue_output_level_persist", persist)
        if persist:
            self._settings.set(
                "queue_output_level_db", float(self.measure_tab.queue_level_spin.value())
            )

    def _send_to_curator(self) -> None:
        if self._state != QueueState.IDLE:
            raise ValueError("Measurements can only be sent to Curator while idle.")

        mode = self._bottom_view_mode()
        active_hrtf = self._hrtf if self._is_hrtf_active() else None
        correction = None
        curve: CurveData
        curator_metadata = self._session.to_dict()
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
            band = source_variation if source_variation is not None else active_variation
            if active_hrtf is not None and not getattr(active_hrtf, "is_variation", False):
                correction = active_hrtf.evaluate(band.freqs)
            curve = CurveData(
                kind="variation",
                freqs=np.array(band.freqs, dtype=float, copy=True),
                p10_db=np.array(band.p10, dtype=float, copy=True)
                + (correction if correction is not None else 0.0),
                p25_db=np.array(band.p25, dtype=float, copy=True)
                + (correction if correction is not None else 0.0),
                median_db=np.array(band.median, dtype=float, copy=True)
                + (correction if correction is not None else 0.0),
                p75_db=np.array(band.p75, dtype=float, copy=True)
                + (correction if correction is not None else 0.0),
                p90_db=np.array(band.p90, dtype=float, copy=True)
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
        if self._state != QueueState.IDLE:
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
        metadata = self._session.to_dict()
        input_label = self.devices.current_input_device_label()
        output_label = self.devices.current_output_device_label()
        active_label = self._active_measure_label()
        input_channel_index = (
            (1 if active_label == "R" else 0)
            if self._two_channel_enabled
            else self.devices.current_input_channel()
        )
        channel_label = active_label or (
            self.measure_tab.ch_combo.currentText().strip() or f"Channel {input_channel_index + 1}"
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
        if self._state != QueueState.IDLE:
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
                freqs=np.array(variation.freqs, dtype=float, copy=True),
                p10_db=np.array(variation.p10, dtype=float, copy=True),
                p25_db=np.array(variation.p25, dtype=float, copy=True),
                median_db=np.array(variation.median, dtype=float, copy=True),
                p75_db=np.array(variation.p75, dtype=float, copy=True),
                p90_db=np.array(variation.p90, dtype=float, copy=True),
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
        if self._state != QueueState.IDLE:
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
        session = SessionData.from_dict({"rig": measurement.rig, **measurement.metadata})
        hrtf_path = self._rnd_widget.resolve_hrtf_path(measurement.hrtf_path, measurement.hrtf_name)
        if not self._ensure_rnd_hrtfs_available([measurement]):
            return
        compensated = bool(hrtf_path)
        filename = f"{self._safe_filename(measurement.name)} {'COMP' if compensated else 'RAW'}.txt"
        path = self.measure_io.resolve_export_path(
            requested_path, filename, "Export R&D Measurement"
        )
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
        self.measure_tab.export_dir_input.setText(str(path.parent))
        self._statusbar.showMessage(f"Exported R&D measurement: {path}")
        self._log_event("INFO", "rnd", "R&D measurement exported", path=str(path))
        self.commands.trigger("export_complete")

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
        path = self.measure_io.resolve_export_path(None, filename, "Export R&D Group Variation")
        if path is None:
            return
        hrtf = None
        session = SessionData.from_dict({"rig": measurements[0].rig, **measurements[0].metadata})
        export_variation(
            freqs=variation.freqs,
            p10_db=variation.p10,
            p25_db=variation.p25,
            median_db=variation.median,
            p75_db=variation.p75,
            p90_db=variation.p90,
            session=session,
            output_path=path,
            compensated=compensated,
            hrtf=hrtf,
            n_sweeps=len(measurements),
            smoothing_fraction=int(self._rnd_widget.session.smoothing_fraction or 48),
        )
        self._settings.set("export_directory", str(path.parent))
        self.measure_tab.export_dir_input.setText(str(path.parent))
        self._statusbar.showMessage(f"Exported R&D variation: {path}")
        self._log_event("INFO", "rnd", "R&D variation exported", path=str(path))
        self.commands.trigger("export_complete")

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
                    session, missing_photos = self._rnd_recovery.restore(
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
            self.commands.trigger("app_start")

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
        path = ensure_extension(Path(path_str), RND_SESSION_EXTENSION)
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

    @staticmethod
    def _safe_filename(value: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in " ._-()" else "_" for ch in value).strip()
        return safe or "R&D Measurement"

    def _failed_recording_dir(self) -> str | None:
        """Folder for failed-recording dumps, or None when the setting is off."""
        if not bool(self._settings.get("save_failed_recordings")):
            return None
        return str(config_dir() / "failed_recordings")

    def closeEvent(self, event) -> None:
        if not self._confirm_rnd_close():
            event.ignore()
            return
        if not self.measure_io.confirm_close():
            event.ignore()
            return

        self._close_pass_fail_dialog()
        self._close_rnd_review_dialog()
        with contextlib.suppress(Exception):
            self.devices.device_poller.stop()

        # Join the sweep thread before Qt tears the window down; a live
        # PortAudio duplex stream at interpreter exit crashes on some hosts.
        with contextlib.suppress(Exception):
            self._sweep_runner.shutdown()

        with contextlib.suppress(Exception):
            self.devices.level_monitor.stop()

        with contextlib.suppress(Exception):
            self.devices.dual_level_monitor.stop()

        with contextlib.suppress(Exception):
            self._stop_channel_balance()

        with contextlib.suppress(Exception):
            self.update_check.shutdown()

        # A Squiglink upload in flight: ask it to stop and give it a moment so
        # its QThread is not destroyed while running.
        with contextlib.suppress(Exception):
            self.squiglink.shutdown()

        try:
            self._rnd_recovery.shutdown_clean()
        except Exception as exc:
            self._log_event("ERROR", "rnd", "R&D recovery cleanup failed", error=str(exc))

        self.measure_io.shutdown()

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
        output = (
            self.devices.current_output_device_label()
            if hasattr(self, "measure_tab")
            else "Not selected"
        )
        input_name = (
            self.devices.current_input_device_label()
            if hasattr(self, "measure_tab")
            else "Not selected"
        )
        channel = self.devices.current_input_channel() + 1 if hasattr(self, "measure_tab") else 1
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
        path = self.measure_io.session_path
        if path is not None:
            # ``.fastgraph-measure.json`` is a two-part suffix, so one ``stem``
            # would leave ``.fastgraph-measure`` behind.
            name = path.name
            if name.lower().endswith(MEASURE_SESSION_EXTENSION):
                name = name[: -len(MEASURE_SESSION_EXTENSION)]
            else:
                name = path.stem
            title = f"{title} • {name}"
        if self.measure_io.dirty:
            title = f"{title}*"
        self.setWindowTitle(title)
