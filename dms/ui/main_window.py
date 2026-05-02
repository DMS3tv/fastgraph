"""
Main application window.
Orchestrates: device selectors, level meter, dual plot, queue control,
pass/fail UI, HRTF selector, settings/calibration, and export.
"""

from pathlib import Path
from typing import Callable, Optional

import numpy as np
from PyQt6.QtCore import QThread, QTimer, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from dms.audio_engine import (
    LevelMonitor,
    SweepWorker,
    device_channel_count,
    get_input_devices,
    get_output_devices,
)
from dms.calibration import CalibrationStore
from dms.export import build_filename, export_curve
from dms.hrtf import HRTFCurve
from dms.processing import (
    compute_frequency_response,
    compute_rms_average,
    downsample_to_log_points,
    generate_log_sweep,
    normalize_at_1khz,
    smooth_fractional_octave,
)
from dms.session import SessionData
from dms.settings_manager import SettingsManager
from dms.ui.calibration_dialog import CalibrationDialog
from dms.ui.dual_plot_widget import DualPlotWidget
from dms.ui.level_meter import LevelMeterWidget
from dms.ui.settings_dialog import SettingsDialog


class AppState:
    IDLE = "idle"
    SWEEPING = "sweeping"
    PASS_FAIL = "pass_fail"
    QUEUE_RUNNING = "queue_running"


_MEASUREMENT_F_MIN = 20.0
_MEASUREMENT_F_MAX = 20000.0
_DISPLAY_AVG_POINTS = 1200
_DISPLAY_AVG_SMOOTHING = 48
_METER_UPDATE_MS = 220


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
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._snapshot_fn = snapshot_fn
        self.setWindowTitle("DMS fastgraph — Test Level")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Live input test level for the currently selected input channel.\n"
            "Use only after device calibration is available."
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


class PassFailDialog(QDialog):
    KEEP = "keep"
    FAIL = "fail"
    CANCEL = "cancel"

    def __init__(self, index: int, total: int, parent=None) -> None:
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


class MainWindow(QMainWindow):
    def __init__(self, session: SessionData, settings: SettingsManager) -> None:
        super().__init__()
        self._session = session
        self._settings = settings
        self._cal_store = CalibrationStore()

        self._state = AppState.IDLE
        self._kept_curves: list[tuple[np.ndarray, np.ndarray]] = []
        self._average: Optional[tuple[np.ndarray, np.ndarray]] = None
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
        self._last_input_devices: list[str] = []
        self._last_output_devices: list[str] = []

        self._level_monitor = LevelMonitor()
        self._level_monitor.level_updated.connect(self._on_level_update)
        self._level_monitor.error_occurred.connect(self._on_level_error)

        self.setWindowTitle(
            f"DMS fastgraph — {session.display_name()} @ {session.rig}"
        )
        self.setMinimumSize(1100, 700)

        self._build_ui()
        self._restore_hrtf_state()
        self._refresh_devices()
        self._start_level_monitor()
        self._apply_state_ui()

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

    def _build_control_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        session_box = QGroupBox("Session")
        sb_layout = QVBoxLayout(session_box)
        sb_layout.addWidget(QLabel(f"<b>{self._session.display_name()}</b>"))
        sb_layout.addWidget(QLabel(f"Rig: {self._session.rig}"))
        layout.addWidget(session_box)

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

        refresh_btn = QPushButton("Refresh Devices")
        refresh_btn.clicked.connect(self._refresh_devices)
        dev_layout.addWidget(refresh_btn)

        layout.addWidget(dev_box)

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
        layout.addWidget(meter_box)

        queue_box = QGroupBox("Queue")
        queue_layout = QVBoxLayout(queue_box)

        n_layout = QHBoxLayout()
        n_layout.addWidget(QLabel("N kept measurements:"))
        self._queue_n_spin = QSpinBox()
        self._queue_n_spin.setRange(1, 100)
        self._queue_n_spin.setValue(int(self._settings.get("queue_count") or 5))
        n_layout.addWidget(self._queue_n_spin)
        queue_layout.addLayout(n_layout)

        self._queue_progress_label = QLabel("Kept: 0 / 0")
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

        layout.addWidget(queue_box)

        hrtf_box = QGroupBox("HRTF Compensation")
        hrtf_layout = QVBoxLayout(hrtf_box)

        self._hrtf_mode_combo = QComboBox()
        self._hrtf_mode_combo.addItem("Off", "off")
        self._hrtf_mode_combo.addItem("Apply loaded HRTF", "apply")
        self._hrtf_mode_combo.currentIndexChanged.connect(self._update_plots)
        hrtf_layout.addWidget(self._hrtf_mode_combo)

        hrtf_btn_row = QHBoxLayout()
        self._hrtf_load_btn = QPushButton("Load HRTF…")
        self._hrtf_load_btn.clicked.connect(self._load_hrtf)
        hrtf_btn_row.addWidget(self._hrtf_load_btn)

        self._hrtf_clear_btn = QPushButton("Clear")
        self._hrtf_clear_btn.clicked.connect(self._clear_hrtf)
        hrtf_btn_row.addWidget(self._hrtf_clear_btn)
        hrtf_layout.addLayout(hrtf_btn_row)

        self._hrtf_label = QLabel("No HRTF loaded")
        self._hrtf_label.setWordWrap(True)
        hrtf_layout.addWidget(self._hrtf_label)

        self._hrtf_invert_cb = QCheckBox("Invert sign (add instead of subtract)")
        self._hrtf_invert_cb.stateChanged.connect(self._on_hrtf_invert_changed)
        hrtf_layout.addWidget(self._hrtf_invert_cb)

        layout.addWidget(hrtf_box, 1)

        misc_box = QGroupBox("Tools")
        misc_layout = QVBoxLayout(misc_box)

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
        misc_layout.addWidget(export_btn)
        self._export_btn = export_btn

        layout.addWidget(misc_box)
        layout.addStretch(1)
        return panel

    def _current_output_device(self) -> Optional[str]:
        return self._out_dev_combo.currentData()

    def _current_input_device(self) -> Optional[str]:
        return self._in_dev_combo.currentData()

    def _current_input_channel(self) -> int:
        value = self._ch_combo.currentData()
        return int(value) if value is not None else 0

    def _queue_active(self) -> bool:
        return self._queue_target > 0

    def _is_hrtf_active(self) -> bool:
        return (
            self._hrtf is not None
            and self._hrtf_mode_combo.currentData() == "apply"
        )

    def _restore_hrtf_state(self) -> None:
        path = self._settings.get("hrtf_path")
        invert = bool(self._settings.get("hrtf_invert"))

        self._hrtf_invert_cb.setChecked(invert)

        if path:
            try:
                self._hrtf = HRTFCurve(path)
            except Exception:
                self._hrtf = None
                self._settings.set("hrtf_path", None)

        self._sync_hrtf_ui()

    def _sync_hrtf_ui(self) -> None:
        has_hrtf = self._hrtf is not None
        self._hrtf_mode_combo.setEnabled(has_hrtf)
        self._hrtf_clear_btn.setEnabled(has_hrtf)

        if has_hrtf:
            self._hrtf_label.setText(self._hrtf.path)
            if self._hrtf_mode_combo.currentData() not in {"off", "apply"}:
                self._hrtf_mode_combo.setCurrentIndex(1)
        else:
            self._hrtf_label.setText("No HRTF loaded")
            self._hrtf_mode_combo.setCurrentIndex(0)

    def _refresh_devices(self) -> None:
        selected_out = self._current_output_device() or self._settings.get(
            "output_device"
        )
        selected_in = self._current_input_device() or self._settings.get(
            "input_device"
        )
        selected_ch = self._current_input_channel()

        out_devices = get_output_devices()
        in_devices = get_input_devices()

        out_names = [d["name"] for d in out_devices]
        in_names = [d["name"] for d in in_devices]

        self._out_dev_combo.blockSignals(True)
        self._in_dev_combo.blockSignals(True)
        self._ch_combo.blockSignals(True)

        self._out_dev_combo.clear()
        for d in out_devices:
            self._out_dev_combo.addItem(d["name"], d["name"])

        self._in_dev_combo.clear()
        for d in in_devices:
            self._in_dev_combo.addItem(d["name"], d["name"])

        if out_names:
            if selected_out in out_names:
                self._out_dev_combo.setCurrentIndex(
                    self._out_dev_combo.findData(selected_out)
                )
            else:
                self._out_dev_combo.setCurrentIndex(0)

        if in_names:
            if selected_in in in_names:
                self._in_dev_combo.setCurrentIndex(
                    self._in_dev_combo.findData(selected_in)
                )
            else:
                self._in_dev_combo.setCurrentIndex(0)

        self._out_dev_combo.blockSignals(False)
        self._in_dev_combo.blockSignals(False)

        self._refresh_channels(selected_ch=selected_ch)
        self._ch_combo.blockSignals(False)

        current_out = self._current_output_device()
        current_in = self._current_input_device()

        self._settings.set("output_device", current_out)
        self._settings.set("input_device", current_in)

        self._last_output_devices = out_names
        self._last_input_devices = in_names

        self._apply_state_ui()
        self._start_level_monitor()

    def _refresh_channels(self, selected_ch: Optional[int] = None) -> None:
        input_device = self._current_input_device()
        count = device_channel_count(input_device, "input") if input_device else 0

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
        current_out = [d["name"] for d in get_output_devices()]
        current_in = [d["name"] for d in get_input_devices()]

        if current_out == self._last_output_devices and current_in == (
            self._last_input_devices
        ):
            return

        selected_out = self._current_output_device()
        selected_in = self._current_input_device()

        if (
            self._state == AppState.SWEEPING
            and (
                selected_out not in current_out
                or selected_in not in current_in
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
        if not input_device:
            self._displayed_level_dbfs = -60.0
            self._level_meter.set_level(-60.0)
            self._level_status_label.setText("No input device selected")
            return

        try:
            self._level_monitor.start(
                device_name=input_device,
                channel_index=self._current_input_channel(),
                fs=int(self._settings.get("sample_rate")),
                buffer_size=int(self._settings.get("buffer_size")),
            )
            self._level_status_label.setText("Live RMS monitor")
        except Exception as exc:
            self._statusbar.showMessage(f"Level monitor start failed: {exc}")

    def _on_output_device_changed(self) -> None:
        self._settings.set("output_device", self._current_output_device())

    def _on_input_device_changed(self) -> None:
        self._settings.set("input_device", self._current_input_device())
        self._refresh_channels()
        self._start_level_monitor()
        self._apply_state_ui()

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
            self._displayed_level_dbfs * 0.6
            + target_db * 0.4
        )
        if abs(self._displayed_level_dbfs - target_db) < 0.2:
            self._displayed_level_dbfs = target_db
        self._level_meter.set_level(self._displayed_level_dbfs)

    def _on_level_error(self, message: str) -> None:
        self._statusbar.showMessage(message)

    def _apply_state_ui(self) -> None:
        idle = self._state == AppState.IDLE
        pass_fail = self._state == AppState.PASS_FAIL
        busy = self._state in {AppState.SWEEPING, AppState.QUEUE_RUNNING}

        device_ok = (
            self._current_output_device() is not None
            and self._current_input_device() is not None
            and self._ch_combo.count() > 0
        )

        for widget in (
            self._out_dev_combo,
            self._in_dev_combo,
            self._ch_combo,
            self._queue_n_spin,
            self._hrtf_mode_combo,
            self._hrtf_load_btn,
            self._hrtf_clear_btn,
            self._hrtf_invert_cb,
            self._settings_btn,
            self._cal_btn,
            self._test_level_btn,
            self._export_btn,
            self._clear_btn,
        ):
            widget.setEnabled(idle)

        self._start_queue_btn.setEnabled(idle and device_ok)
        self._cancel_queue_btn.setEnabled(busy or pass_fail)

    def _start_queue(self) -> None:
        if self._state != AppState.IDLE:
            return

        if not self._current_output_device():
            QMessageBox.warning(self, "No Output Device", "Select an output device.")
            return

        if not self._current_input_device():
            QMessageBox.warning(self, "No Input Device", "Select an input device.")
            return

        if self._ch_combo.count() == 0:
            QMessageBox.warning(
                self,
                "No Input Channel",
                "Selected input device has no available input channels.",
            )
            return

        self._queue_target = int(self._queue_n_spin.value())
        self._queue_index = 0
        self._current_sweep_attempts = 0
        self._settings.set("queue_count", self._queue_target)

        self._queue_progress_bar.setRange(0, max(1, self._queue_target))
        self._queue_progress_bar.setValue(0)
        self._queue_progress_label.setText(
            f"Kept: {self._queue_index} / {self._queue_target}"
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

        if not output_device or not input_device:
            self._on_sweep_error("Selected device is unavailable.")
            return

        self._level_monitor.stop()

        sweep = generate_log_sweep(
            duration=float(self._settings.get("sweep_duration")),
            fs=int(self._settings.get("sample_rate")),
            f_low=_MEASUREMENT_F_MIN,
            f_high=_MEASUREMENT_F_MAX,
        )

        worker = SweepWorker()
        worker.finished.connect(self._on_sweep_finished)
        worker.error.connect(self._on_sweep_error)
        worker.progress.connect(self._on_sweep_progress)

        self._active_sweep_worker = worker
        self._sweep_thread = _SweepThread(
            worker,
            sweep=sweep,
            output_device=output_device,
            input_device=input_device,
            input_channel=input_channel,
            fs=int(self._settings.get("sample_rate")),
            buffer_size=int(self._settings.get("buffer_size")),
            pre_silence=float(self._settings.get("pre_sweep_silence")),
            post_silence=float(self._settings.get("post_sweep_silence")),
            latency=str(self._settings.get("latency")),
        )
        self._sweep_thread.finished.connect(self._on_sweep_thread_finished)
        self._sweep_thread.start()

        self._statusbar.showMessage(
            f"Sweeping {self._queue_index + 1}/{self._queue_target} "
            f"(attempt {self._current_sweep_attempts})..."
        )

    def _on_sweep_progress(self, frac: float) -> None:
        self._sweep_progress.setValue(int(max(0.0, min(1.0, frac)) * 100.0))

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
                n_points=300,
                f_ref=1000.0,
                normalize_ref=True,
            )

            self._pending_curve = (freqs_ds, mag_ds)
            self._state = AppState.PASS_FAIL
            self._apply_state_ui()
            self._update_plots(show_pending=True)
            self._statusbar.showMessage("Sweep complete. Waiting for review.")
            QTimer.singleShot(0, self._show_pass_fail_dialog)
        except Exception as exc:
            self._on_sweep_error(f"Processing error: {exc}")

    def _on_sweep_error(self, message: str) -> None:
        self._cleanup_sweep_thread()
        self._close_pass_fail_dialog()
        self._pending_curve = None
        self._sweep_progress.setValue(0)

        if self._queue_active():
            self._state = AppState.IDLE
        else:
            self._state = AppState.IDLE

        self._apply_state_ui()
        self._start_level_monitor()
        self._statusbar.showMessage(message)
        QMessageBox.warning(self, "Sweep Error", message)

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
        self._queue_progress_label.setText(
            f"Kept: {self._queue_index} / {target}"
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
                invert=self._hrtf_invert_cb.isChecked(),
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

        kept = list(self._kept_curves)
        if show_pending and self._pending_curve is not None:
            kept = kept + [self._pending_curve]

        self._plots.update_curves(kept=kept, average=avg)

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
            if self._hrtf_mode_combo.currentData() == "off":
                self._hrtf_mode_combo.setCurrentIndex(1)
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

    def _on_hrtf_invert_changed(self) -> None:
        self._settings.set("hrtf_invert", self._hrtf_invert_cb.isChecked())
        self._update_plots()

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
        self._pending_curve = None
        self._queue_target = 0
        self._queue_index = 0
        self._current_sweep_attempts = 0
        self._plots.clear_all()
        self._update_queue_progress()
        self._sweep_progress.setValue(0)
        self._statusbar.showMessage("All measurements cleared.")

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._settings, self)
        if dlg.exec():
            self._start_level_monitor()

    def _open_calibration(self) -> None:
        input_device = self._current_input_device()
        if not input_device:
            QMessageBox.warning(
                self,
                "No Input Device",
                "Select an input device first.",
            )
            return

        dlg = CalibrationDialog(
            device_name=input_device,
            channel=self._current_input_channel(),
            fs=int(self._settings.get("sample_rate")),
            buffer_size=int(self._settings.get("buffer_size")),
            cal_store=self._cal_store,
            parent=self,
        )
        dlg.calibration_done.connect(self._on_calibration_done)
        dlg.exec()

    def _on_calibration_done(self, device_name: str, sensitivity: float) -> None:
        self._statusbar.showMessage(
            f"Calibration saved for {device_name}: {sensitivity:.6f} Pa/FS"
        )

    def _open_test_level(self) -> None:
        input_device = self._current_input_device()
        if not input_device:
            QMessageBox.information(
                self,
                "No Input Device",
                "Select an input device first.",
            )
            return

        if not self._cal_store.is_calibrated(input_device):
            QMessageBox.information(
                self,
                "Calibration Required",
                "This feature requires 94 dB SPL calibration for the "
                "currently selected input device.",
            )
            return

        dlg = TestLevelDialog(self._level_snapshot, self)
        dlg.exec()

    def _level_snapshot(self) -> tuple[float, Optional[float], str]:
        input_device = self._current_input_device() or ""
        dbfs = self._last_level_dbfs
        spl = None
        if input_device and self._cal_store.is_calibrated(input_device):
            rms_fs = 10.0 ** (dbfs / 20.0) if dbfs > -120.0 else 0.0
            spl = self._cal_store.rms_to_dbspl(input_device, rms_fs)
        return dbfs, spl, input_device

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

        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Export Average",
            filename,
            "Text Files (*.txt);;All Files (*)",
        )
        if not path_str:
            return

        freqs, mag_db = curve
        try:
            export_curve(
                freqs=freqs,
                mag_db=mag_db,
                session=self._session,
                output_path=Path(path_str),
                compensated=compensated,
                hrtf=self._hrtf if compensated else None,
                hrtf_invert=self._hrtf_invert_cb.isChecked(),
            )
            self._statusbar.showMessage(f"Exported: {path_str}")
        except Exception as exc:
            QMessageBox.warning(self, "Export Error", str(exc))

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

        super().closeEvent(event)
