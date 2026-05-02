"""
Main application window.
Orchestrates: device selectors, level meter, dual plot, queue control,
pass/fail UI, HRTF selector, settings/calibration, and export.
"""

import threading
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QComboBox, QSpinBox, QGroupBox, QFileDialog,
    QMessageBox, QProgressBar, QStatusBar, QSizePolicy,
    QSplitter,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, QTimer

from dms.audio_engine import (
    LevelMonitor, SweepWorker,
    get_input_devices, get_output_devices, device_channel_count,
)
from dms.processing import (
    generate_log_sweep, compute_frequency_response,
    normalize_at_1khz, downsample_to_log_points, compute_rms_average,
)
from dms.session import SessionData
from dms.settings_manager import SettingsManager
from dms.calibration import CalibrationStore
from dms.hrtf import HRTFCurve
from dms.export import build_filename, export_curve

from dms.ui.dual_plot_widget import DualPlotWidget
from dms.ui.level_meter import LevelMeterWidget
from dms.ui.settings_dialog import SettingsDialog
from dms.ui.calibration_dialog import CalibrationDialog


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

class AppState:
    IDLE = "idle"
    SWEEPING = "sweeping"
    PASS_FAIL = "pass_fail"
    QUEUE_RUNNING = "queue_running"


# ---------------------------------------------------------------------------
# Worker thread wrapper
# ---------------------------------------------------------------------------

class _SweepThread(QThread):
    finished = pyqtSignal(np.ndarray, np.ndarray)
    error = pyqtSignal(str)
    progress = pyqtSignal(float)

    def __init__(self, worker: SweepWorker, **kwargs) -> None:
        super().__init__()
        self._worker = worker
        self._kwargs = kwargs

    def run(self) -> None:
        self._worker.run(**self._kwargs)

    def abort(self) -> None:
        self._worker.abort()


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, session: SessionData, settings: SettingsManager) -> None:
        super().__init__()
        self._session = session
        self._settings = settings
        self._cal_store = CalibrationStore()

        # Measurement state
        self._state = AppState.IDLE
        self._kept_curves: list[tuple[np.ndarray, np.ndarray]] = []
        self._average: Optional[tuple[np.ndarray, np.ndarray]] = None
        self._queue_target = 0
        self._queue_index = 0  # how many kept so far in current queue run
        self._current_sweep_attempts = 0
        self._hrtf: Optional[HRTFCurve] = None
        self._sweep_thread: Optional[_SweepThread] = None

        # Audio
        self._level_monitor = LevelMonitor()
        self._level_monitor.level_updated.connect(self._on_level_update)
        self._level_monitor.error_occurred.connect(self._on_level_error)
        self._sweep_worker = SweepWorker()
        self._sweep_worker.finished.connect(self._on_sweep_finished)
        self._sweep_worker.error.connect(self._on_sweep_error)
        self._sweep_worker.progress.connect(self._on_sweep_progress)

        self.setWindowTitle(
            f"DMS Fastgraph — {session.display_name()} @ {session.rig}"
        )
        self.setMinimumSize(1100, 700)

        self._build_ui()
        self._refresh_devices()
        self._start_level_monitor()

        # Periodically check for device changes
        self._device_check_timer = QTimer(self)
        self._device_check_timer.timeout.connect(self._check_devices)
        self._device_check_timer.start(3000)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # Left panel: controls
        left = self._build_left_panel()
        root.addWidget(left, 0)

        # Right: plots
        self._plots = DualPlotWidget()
        root.addWidget(self._plots, 1)

        # Status bar
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("Ready.")

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(280)
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)

        # --- Session info ---
        session_box = QGroupBox("Session")
        sb_layout = QVBoxLayout(session_box)
        sb_layout.addWidget(QLabel(f"<b>{self._session.display_name()}</b>"))
        sb_layout.addWidget(QLabel(f"Rig: {self._session.rig}"))
        layout.addWidget(session_box)

        # --- Device selection ---
        dev_box = QGroupBox("Devices")
        dev_layout = QVBoxLayout(dev_box)

        dev_layout.addWidget(QLabel("Output Device:"))
        self._out_dev_combo = QComboBox()
        self._out_dev_combo.currentIndexChanged.connect(self._on_output_device_changed)
        dev_layout.addWidget(self._out_dev_combo)

        dev_layout.addWidget(QLabel("Input Device:"))
        self._in_dev_combo = QComboBox()
        self._in_dev_combo.currentIndexChanged.connect(self._on_input_device_changed)
        dev_layout.addWidget(self._in_dev_combo)

        dev_layout.addWidget(QLabel("Input Channel:"))
        self._ch_combo = QComboBox()
        self._ch_combo.currentIndexChanged.connect(self._on_channel_changed)
        dev_layout.addWidget(self._ch_combo)

        self._active_ch_label = QLabel("Active: Ch 1 (Left)")
        self._active_ch_label.setObjectName("label_channel_active")
        dev_layout.addWidget(self._active_ch_label)

        refresh_btn = QPushButton("↻ Refresh Devices")
        refresh_btn.clicked.connect(self._refresh_devices)
        dev_layout.addWidget(refresh_btn)

        layout.addWidget(dev_box)

        # --- Level meter ---
        meter_box = QGroupBox("Input Level")
        meter_layout = QHBoxLayout(meter_box)
        self._level_meter = LevelMeterWidget()
        self._level_dbfs_label = QLabel("—")
        self._level_dbfs_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        meter_layout.addWidget(self._level_meter)
        meter_layout.addWidget(self._level_dbfs_label, 1)
        layout.addWidget(meter_box)

        # --- Queue controls ---
        queue_box = QGroupBox("Queue")
        queue_layout = QVBoxLayout(queue_box)

        n_layout = QHBoxLayout()
        n_layout.addWidget(QLabel("N measurements:"))
        self._queue_n_spin = QSpinBox()
        self._queue_n_spin.setRange(1, 100)
        self._queue_n_spin.setValue(self._settings.get("queue_count"))
        n_layout.addWidget(self._queue_n_spin)
        queue_layout.addLayout(n_layout)

        self._queue_progress_label = QLabel("Kept: 0 / 0")
        queue_layout.addWidget(self._queue_progress_label)

        self._queue_progress_bar = QProgressBar()
        self._queue_progress_bar.setRange(0, 1)
        self._queue_progress_bar.setValue(0)
        queue_layout.addWidget(self._queue_progress_bar)

        btn_row = QHBoxLayout()
        self._start_queue_btn = QPushButton("▶ Start Queue")
        self._start_queue_btn.setObjectName("btn_start")
        self._start_queue_btn.clicked.connect(self._start_queue)
        btn_row.addWidget(self._start_queue_btn)

        self._cancel_queue_btn = QPushButton("✕ Cancel")
        self._cancel_queue_btn.setObjectName("btn_cancel")
        self._cancel_queue_btn.setEnabled(False)
        self._cancel_queue_btn.clicked.connect(self._cancel_queue)
        btn_row.addWidget(self._cancel_queue_btn)
        queue_layout.addLayout(btn_row)

        # Pass/Fail buttons (shown during pass_fail state)
        pf_row = QHBoxLayout()
        self._keep_btn = QPushButton("✓ Keep")
        self._keep_btn.setObjectName("btn_keep")
        self._keep_btn.setEnabled(False)
        self._keep_btn.clicked.connect(self._on_keep)
        pf_row.addWidget(self._keep_btn)

        self._fail_btn = QPushButton("✕ Redo")
        self._fail_btn.setObjectName("btn_fail")
        self._fail_btn.setEnabled(False)
        self._fail_btn.clicked.connect(self._on_fail)
        pf_row.addWidget(self._fail_btn)
        queue_layout.addLayout(pf_row)

        self._sweep_progress = QProgressBar()
        self._sweep_progress.setRange(0, 100)
        self._sweep_progress.setValue(0)
        queue_layout.addWidget(self._sweep_progress)

        layout.addWidget(queue_box)

        # --- HRTF ---
        hrtf_box = QGroupBox("HRTF Compensation (bottom viewport)")
        hrtf_layout = QVBoxLayout(hrtf_box)

        hrtf_btn_row = QHBoxLayout()
        self._hrtf_load_btn = QPushButton("Load HRTF…")
        self._hrtf_load_btn.clicked.connect(self._load_hrtf)
        hrtf_btn_row.addWidget(self._hrtf_load_btn)

        self._hrtf_clear_btn = QPushButton("Clear")
        self._hrtf_clear_btn.clicked.connect(self._clear_hrtf)
        hrtf_btn_row.addWidget(self._hrtf_clear_btn)
        hrtf_layout.addLayout(hrtf_btn_row)

        self._hrtf_label = QLabel("No HRTF loaded")
        self._hrtf_label.setStyleSheet("color: #888; font-size: 11px;")
        self._hrtf_label.setWordWrap(True)
        hrtf_layout.addWidget(self._hrtf_label)

        from PyQt6.QtWidgets import QCheckBox
        self._hrtf_invert_cb = QCheckBox("Invert sign (add instead of subtract)")
        self._hrtf_invert_cb.stateChanged.connect(self._update_plots)
        hrtf_layout.addWidget(self._hrtf_invert_cb)

        layout.addWidget(hrtf_box)

        # --- Misc buttons ---
        misc_box = QGroupBox("Tools")
        misc_layout = QVBoxLayout(misc_box)

        clear_btn = QPushButton("Clear All Measurements")
        clear_btn.clicked.connect(self._clear_all)
        misc_layout.addWidget(clear_btn)

        settings_btn = QPushButton("⚙ Settings…")
        settings_btn.clicked.connect(self._open_settings)
        misc_layout.addWidget(settings_btn)

        cal_btn = QPushButton("SPL Calibration…")
        cal_btn.clicked.connect(self._open_calibration)
        misc_layout.addWidget(cal_btn)

        export_btn = QPushButton("Export Average…")
        export_btn.clicked.connect(self._export)
        misc_layout.addWidget(export_btn)

        layout.addWidget(misc_box)

        layout.addStretch(1)
        return panel

    # ------------------------------------------------------------------
    # Device management
    # ------------------------------------------------------------------

    def _refresh_devices(self) -> None:
        prev_out = self._settings.get("output_device")
        prev_in = self._settings.get("input_device")

        self._out_dev_combo.blockSignals(True)
        self._in_dev_combo.blockSignals(True)

        self._out_dev_combo.clear()
        for d in get_output_devices():
            self._out_dev_combo.addItem(d["name"], d["name"])

        self._in_dev_combo.clear()
        for d in get_input_devices():
            self._in_dev_combo.addItem(d["name"], d["name"])

        # Restore selections
        if prev_out:
            idx = self._out_dev_combo.findData(prev_out)
            if idx >= 0:
                self._out_dev_combo.setCurrentIndex(idx)

        if prev_in:
            idx = self._in_dev_combo.findData(prev_in)
            if idx >= 0:
                self._in_dev_combo.setCurrentIndex(idx)

        self._out_dev_combo.blockSignals(False)
        self._in_dev_combo.blockSignals(False)

        self._