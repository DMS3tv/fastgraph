"""
Measure state and the measurement queue.

``MeasureController`` owns everything the Measure tab measures: the
``MeasurementQueue`` and the ``SweepRunner`` that drives it, the kept curves
and two-channel pairs with their per-sweep metadata, the average and variation
derived from them, the HRTF selection, the level mode and SPL offset, the
distortion overlay, the pass/fail review dialog and the Channel Balance
generator. The window and the other controllers read that state through
``window.measure``.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QMessageBox

from dms.audio_engine import SweepWorker
from dms.channel_balance import ChannelBalanceEngine, frequency_limit
from dms.curator.parser import load_two_column_txt_curve
from dms.hrtf import HRTFCurve
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
from dms.session import SessionData
from dms.settings_manager import config_dir
from dms.two_channel import (
    TwoChannelCurvePair,
    channel_curves,
    combined_pair_curves,
    curve_label_for_selection,
    shared_normalize_pair_at_1khz,
)
from dms.ui.measure_dialogs import PassFailDialog
from dms.ui.sweep_runner import SweepRunner

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from dms.ui.main_window import MainWindow

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


class _BalanceThread(QThread):
    def __init__(self, engine: ChannelBalanceEngine, **kwargs) -> None:
        super().__init__()
        self._engine = engine
        self._kwargs = kwargs

    def run(self) -> None:
        self._engine.run(**self._kwargs)


class MeasureController(QObject):
    #: Queue state or kept measurements changed; the window re-enables its
    #: controls from them.
    state_changed = pyqtSignal()
    #: The curves to draw changed; True also draws the pending sweep.
    curves_changed = pyqtSignal(bool)

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self.curves_changed.connect(self._redraw)
        settings = window._settings
        self.queue = MeasurementQueue(max_attempts=MAX_SWEEP_ATTEMPTS)
        # dB SPL without a calibration falls back to reference mode; the note
        # is shown once rather than after every sweep.
        self.spl_uncalibrated_warned = False

        self.kept_curves: list[tuple[np.ndarray, np.ndarray]] = []
        self.average: tuple[np.ndarray, np.ndarray] | None = None
        self.variation: VariationBand | None = None
        # Per-capture metadata kept positionally beside the curves, so a saved
        # session carries the diagnostics, timing and distortion the review
        # dialog showed. ``kept_pair_meta`` is bookkeeping only: a kept pair
        # already carries its own per-channel diagnostics.
        self.kept_sweep_meta: list[dict] = []
        self.kept_pair_meta: list[dict] = []
        self.two_channel_pairs: list[TwoChannelCurvePair] = []
        self.two_channel_averages: dict[str, object] = {}
        self.two_channel_variations: dict[str, object] = {}
        self.two_channel_enabled = bool(settings.get("measure_two_channel_enabled"))
        bottom_mode = str(settings.get("measure_two_channel_bottom_mode") or "combined")
        self.two_channel_bottom_mode = "separate" if bottom_mode == "separate" else "combined"
        self.two_channel_selection = "channel_1"
        self.channel_balance_active = False
        self._balance_engine: ChannelBalanceEngine | None = None
        self._balance_thread: QThread | None = None
        self._balance_waveform = "sine"
        self._balance_frequency = 500.0
        self._balance_level_db = -6.0
        self._balance_ui_timer = QTimer(self)
        self._balance_ui_timer.setInterval(33)
        self._balance_ui_timer.timeout.connect(self._refresh_balance_scope)

        self.hrtf: HRTFCurve | None = None
        self._hrtf_options: list[tuple[str, str]] = []

        self.sweep_runner = SweepRunner(self)
        self.sweep_runner.idle.connect(self._on_sweep_thread_finished)
        # Harmonic analysis of the most recently kept sweep. The queue's own
        # last_distortion is cleared by the reset that follows Keep, so the
        # overlay would otherwise vanish the moment a sweep is accepted.
        self.kept_distortion: HarmonicAnalysis | None = None
        self.pass_fail_dialog: PassFailDialog | None = None

    def on_two_channel_toggled(self, _state: int) -> None:
        if self.queue.state != QueueState.IDLE:
            self._window.measure_tab.two_channel_toggle.blockSignals(True)
            self._window.measure_tab.two_channel_toggle.setChecked(self.two_channel_enabled)
            self._window.measure_tab.two_channel_toggle.blockSignals(False)
            return
        enabled = bool(self._window.measure_tab.two_channel_toggle.isChecked())
        if not enabled:
            self.stop_channel_balance()
            self._window.measure_tab.measure_frequency_button.setChecked(True)
        self.two_channel_enabled = enabled
        self.queue.two_channel = enabled
        self._window._settings.set("measure_two_channel_enabled", enabled)
        self._window._plots.set_two_channel_enabled(enabled)
        self._window.measure_tab.measure_submode_control.setVisible(enabled)
        self._window.measure_tab.sync_queue_bar_submode_width()
        self._window.measure_tab.level_meter_2.setVisible(enabled)
        self._window.measure_tab.level_status_label_2.setVisible(enabled)
        self._window.measure_tab.bottom_layout_label.setVisible(enabled)
        self._window.measure_tab.bottom_layout_combo.setVisible(enabled)
        self._window.measure_tab.ch_combo.setEnabled(not enabled)
        self.update_queue_progress()
        self.curves_changed.emit(False)
        self._window.devices.start_level_monitor()
        self.state_changed.emit()
        mode = "Two Channel" if enabled else "Single Channel"
        self._window._statusbar.showMessage(f"Measure mode: {mode}.")

    def channel_balance_mode_active(self) -> bool:
        balance_button = getattr(
            getattr(self._window, "measure_tab", None), "measure_balance_button", None
        )
        return bool(
            self.two_channel_enabled and balance_button is not None and balance_button.isChecked()
        )

    def on_measure_submode_toggled(self, _checked: bool) -> None:
        balance = self.channel_balance_mode_active()
        if not balance:
            self.stop_channel_balance()
        self._window._plots.two.set_balance_mode(balance)
        self._window.measure_tab.bottom_layout_label.setVisible(
            self.two_channel_enabled and not balance
        )
        self._window.measure_tab.bottom_layout_combo.setVisible(
            self.two_channel_enabled and not balance
        )
        self._window.measure_tab.variation_toggle.setVisible(not balance)
        self._window.measure_tab.distortion_toggle.setVisible(not balance)
        self._window.measure_tab.hrtf_toggle.setVisible(not balance)
        self._window.measure_tab.hrtf_combo.setVisible(not balance)
        self._window.measure_tab.hrtf_label.setVisible(not balance)
        self._window.measure_tab.level_mode_label.setVisible(not balance)
        self._window.measure_tab.level_mode_combo.setVisible(not balance)
        self._window.measure_tab.level_meter.setVisible(not balance)
        self._window.measure_tab.level_meter_2.setVisible(self.two_channel_enabled and not balance)
        self._window.measure_tab.level_status_label.setVisible(not balance)
        self._window.measure_tab.level_status_label_2.setVisible(
            self.two_channel_enabled and not balance
        )
        self._window._plots.two.set_generator_level(
            float(self._window.measure_tab.queue_level_spin.value())
        )
        self._window._plots.two.set_frequency_limit(
            frequency_limit(int(self._window._settings.get("sample_rate")))
        )
        if balance:
            self._window.devices.stop_level_monitor()
        else:
            self._window.devices.start_level_monitor()
            self.curves_changed.emit(False)
        self.state_changed.emit()

    def on_two_channel_bottom_mode_changed(self, _index: int) -> None:
        mode = str(self._window.measure_tab.bottom_layout_combo.currentData() or "combined")
        self.two_channel_bottom_mode = "separate" if mode == "separate" else "combined"
        self._window._settings.set("measure_two_channel_bottom_mode", self.two_channel_bottom_mode)
        self._window._plots.two.set_bottom_mode(self.two_channel_bottom_mode)
        self.curves_changed.emit(False)

    def on_two_channel_selection_changed(self, selection: str) -> None:
        if selection in {"channel_1", "channel_2"}:
            self.two_channel_selection = selection
        self._window.measure_io.sync_export_button()

    def start_channel_balance(self) -> None:
        if self.channel_balance_active:
            return
        if self.queue.state != QueueState.IDLE or not self.channel_balance_mode_active():
            return
        if not self._window.devices.two_channel_devices_ready():
            QMessageBox.warning(
                self._window,
                "Two Channels Required",
                "Channel Balance needs an input device and an output device with at least two channels.",
            )
            return
        input_device = self._window.devices.current_input_device()
        output_device = self._window.devices.current_output_device()
        if input_device is None or output_device is None:
            return

        self._window.devices.stop_level_monitor()
        self._balance_level_db = float(self._window.measure_tab.queue_level_spin.value())
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
            sample_rate=int(self._window._settings.get("sample_rate")),
            block_size=int(self._window._settings.get("buffer_size")),
            latency=self._window.devices.sweep_latency_mode(),
        )
        thread.finished.connect(self._on_balance_thread_finished)
        self._balance_engine = engine
        self._balance_thread = thread
        self.channel_balance_active = True
        self._window._plots.two.set_balance_running(True)
        self._balance_ui_timer.start()
        thread.start()
        self._window._statusbar.showMessage("Channel Balance generator started.")

    def stop_channel_balance(self, *_args) -> None:
        engine = self._balance_engine
        thread = self._balance_thread
        self._balance_ui_timer.stop()
        if engine is not None:
            engine.stop()
        if thread is not None and thread.isRunning():
            thread.wait(1200)
        self.channel_balance_active = False
        if hasattr(self._window, "_plots"):
            self._window._plots.two.set_balance_running(False)
        if thread is None or not thread.isRunning():
            self._balance_engine = None
            self._balance_thread = None

    def _on_balance_thread_finished(self) -> None:
        thread = self._balance_thread
        if thread is not None:
            thread.deleteLater()
        self._balance_engine = None
        self._balance_thread = None
        self.channel_balance_active = False
        self._balance_ui_timer.stop()
        self._window._plots.two.set_balance_running(False)

    def _on_balance_error(self, message: str) -> None:
        logger.error(message, extra={"source": "channel_balance"})
        self._window._statusbar.showMessage(message)
        QMessageBox.warning(self._window, "Channel Balance Error", message)

    def on_balance_parameters_changed(
        self, waveform: str, frequency: float, level_db: float
    ) -> None:
        self._balance_waveform = "square" if waveform == "square" else "sine"
        self._balance_frequency = max(
            20.0,
            min(
                frequency_limit(int(self._window._settings.get("sample_rate"))),
                float(frequency),
            ),
        )
        self._balance_level_db = max(-120.0, min(0.0, float(level_db)))
        if (
            abs(float(self._window.measure_tab.queue_level_spin.value()) - self._balance_level_db)
            > 1e-9
        ):
            self._window.measure_tab.queue_level_spin.setValue(self._balance_level_db)
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
        sample_rate = int(self._window._settings.get("sample_rate"))
        sample_count = int(round(5.0 * sample_rate / max(20.0, self._balance_frequency)))
        sample_count = max(128, min(sample_count, int(0.25 * sample_rate)))
        left, right, left_db, right_db, delta_db = engine.snapshot(sample_count)
        self._window._plots.two.update_scope(left, right, sample_rate, left_db, right_db, delta_db)

    def queue_active(self) -> bool:
        return self.queue.target > 0

    def is_hrtf_active(self) -> bool:
        return self.hrtf is not None and self._window.measure_tab.hrtf_toggle.isChecked()

    def restore_hrtf_state(self) -> None:
        self.refresh_hrtf_options()
        path = self._window._settings.get("hrtf_path")

        if path:
            built_in_paths = self._built_in_hrtf_paths()
            try:
                resolved_path = str(Path(path).resolve())
            except Exception:
                resolved_path = ""
            if resolved_path not in built_in_paths:
                self.hrtf = None
                self._window._settings.set("hrtf_path", None)
            else:
                try:
                    self.hrtf = HRTFCurve(path)
                except Exception:
                    self.hrtf = None
                    self._window._settings.set("hrtf_path", None)

        self._sync_hrtf_ui()

    def refresh_hrtf_options(self) -> None:
        self._hrtf_options = [("None", "")]
        for path in sorted(HRTF_DIR.glob("*.txt")):
            self._hrtf_options.append((path.stem, str(path)))

        current_path = self.hrtf.path if self.hrtf is not None else ""
        self._window.measure_tab.hrtf_combo.blockSignals(True)
        self._window.measure_tab.hrtf_combo.clear()
        for label, value in self._hrtf_options:
            self._window.measure_tab.hrtf_combo.addItem(label, value)
        index = self._window.measure_tab.hrtf_combo.findData(current_path)
        self._window.measure_tab.hrtf_combo.setCurrentIndex(index if index >= 0 else 0)
        self._window.measure_tab.hrtf_combo.blockSignals(False)

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
        has_hrtf = self.hrtf is not None
        self._window.measure_tab.hrtf_toggle.setEnabled(has_hrtf)

        if has_hrtf:
            self._window.measure_tab.hrtf_label.setText(Path(self.hrtf.path).stem)
            self._window.measure_tab.hrtf_label.setToolTip(self.hrtf.path)
            index = self._window.measure_tab.hrtf_combo.findData(self.hrtf.path)
        else:
            self._window.measure_tab.hrtf_label.setText("None")
            self._window.measure_tab.hrtf_label.setToolTip("")
            self._window.measure_tab.hrtf_toggle.setChecked(False)
            index = 0

        self._window.measure_tab.hrtf_combo.blockSignals(True)
        self._window.measure_tab.hrtf_combo.setCurrentIndex(index if index >= 0 else 0)
        self._window.measure_tab.hrtf_combo.blockSignals(False)

    def abort_active_sweep(self) -> None:
        """Stop the running sweep and wait for its thread to end.

        Waiting matters: sounddevice's play/record state is process-global, so
        a stale thread that stops later would truncate the next sweep.
        """
        with contextlib.suppress(Exception):
            self.sweep_runner.abort()

    def start_queue(self) -> None:
        if self.queue.state != QueueState.IDLE:
            return

        # The Measure button is disabled in Channel Balance mode, but the
        # keyboard shortcut, the console and automations reach this method
        # directly; the guard must live here.
        if self.channel_balance_mode_active() or self.channel_balance_active:
            self._window._statusbar.showMessage(
                "Start blocked: switch to Frequency Response and stop Channel Balance first."
            )
            return

        if self._window.devices.current_output_device() is None:
            QMessageBox.warning(self._window, "No Output Device", "Select an output device.")
            return

        if self._window.devices.current_input_device() is None:
            QMessageBox.warning(self._window, "No Input Device", "Select an input device.")
            return

        if not self._window.devices.selected_audio_pair_is_compatible():
            QMessageBox.warning(
                self._window,
                "Windows Audio Driver Mismatch",
                self._window.devices.windows_audio_pair_message(),
            )
            self._window._statusbar.showMessage(
                "Queue start blocked: Windows input/output driver backends do not match."
            )
            return

        if self._window.measure_tab.ch_combo.count() == 0:
            QMessageBox.warning(
                self._window,
                "No Input Channel",
                "Selected input device has no available input channels.",
            )
            return

        if self.two_channel_enabled and not self._window.devices.two_channel_devices_ready():
            QMessageBox.warning(
                self._window,
                "Two Channels Required",
                "Two Channel measurement needs an input device and an output device with at least two channels.",
            )
            return

        ambient_dbfs = float(self._window.devices.last_level_dbfs)
        if ambient_dbfs > _QUEUE_AMBIENT_WARN_DBFS:
            choice = QMessageBox.question(
                self._window,
                "Ambient Level Warning",
                "Current ambient/input RMS looks high before queue start:\n"
                f"{ambient_dbfs:.1f} dBFS (warning threshold: {_QUEUE_AMBIENT_WARN_DBFS:.1f} dBFS).\n\n"
                "This can reduce measurement SNR.\n"
                "Start queue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                self._window._statusbar.showMessage(
                    "Queue start canceled due to high ambient level."
                )
                return

        self.queue.target = int(self._window.measure_tab.queue_n_spin.value())
        self.queue.index = 0
        self.queue.attempts = 0
        overrides = (
            self._window._settings.session_overrides()
            if hasattr(self._window._settings, "session_overrides")
            else {}
        )
        if "queue_count" not in overrides:
            self._window._settings.set("queue_count", self.queue.target)

        self._window.measure_tab.queue_progress_bar.setRange(0, max(1, self.queue.target))
        self._window.measure_tab.queue_progress_bar.setValue(0)
        kept_count = (
            len(self.two_channel_pairs) if self.two_channel_enabled else len(self.kept_curves)
        )
        self._window.measure_tab.queue_progress_label.setText(f"Kept: {kept_count}")

        self.queue.state = QueueState.QUEUE_RUNNING
        self.state_changed.emit()
        self._window._statusbar.showMessage("Queue started.")
        self.start_next_sweep()

    def start_next_sweep(self, *, second_stage: bool = False) -> None:
        if not self.queue_active():
            self.queue.state = QueueState.IDLE
            self.state_changed.emit()
            return

        if self.queue.index >= self.queue.target:
            self.finish_queue()
            return

        if second_stage:
            self.queue.stage = 2
        else:
            self.queue.attempts += 1
            if self.two_channel_enabled:
                self.queue.stage = 1
                self.queue.pending_pair = None
                self.queue.pending_pair_first_raw = None
                self.queue.pending_pair_first_diagnostics = None
        self.stop_channel_balance()
        self.queue.state = QueueState.SWEEPING
        self.state_changed.emit()
        self._window.measure_tab.sweep_progress.setValue(0)

        output_device = self._window.devices.current_output_device()
        input_device = self._window.devices.current_input_device()
        input_channel = (
            self.queue.stage - 1
            if self.two_channel_enabled
            else self._window.devices.current_input_channel()
        )
        output_channel = input_channel if self.two_channel_enabled else None

        if output_device is None or input_device is None:
            self.on_sweep_error("Selected device is unavailable.")
            return

        if not self._window.devices.selected_audio_pair_is_compatible():
            self.on_sweep_error(self._window.devices.windows_audio_pair_message())
            return

        self._window.devices.stop_level_monitor()
        self.queue.last_timing_quality = None
        self.queue.last_diagnostics = None

        sweep = generate_log_sweep(
            duration=float(self._window._settings.get("sweep_duration")),
            fs=int(self._window._settings.get("sample_rate")),
            f_low=_MEASUREMENT_F_MIN,
            f_high=_MEASUREMENT_F_MAX,
        )
        output_level_db = float(self._window.measure_tab.queue_level_spin.value())
        output_gain = 10.0 ** (output_level_db / 20.0)
        sweep = (sweep * output_gain).astype(np.float32, copy=False)

        worker = SweepWorker()
        worker.finished.connect(self.on_sweep_finished)
        worker.error.connect(self.on_sweep_error)
        worker.progress.connect(self.on_sweep_progress)
        worker.timing_quality.connect(self.on_timing_quality)
        worker.measurement_diagnostics.connect(self.on_measurement_diagnostics)

        started = self.sweep_runner.start(
            lambda: worker,
            sweep=sweep,
            output_device=output_device,
            input_device=input_device,
            output_device_label=self._window.devices.current_output_device_label(),
            input_device_label=self._window.devices.current_input_device_label(),
            input_channel=input_channel,
            output_channel=output_channel,
            fs=int(self._window._settings.get("sample_rate")),
            buffer_size=int(self._window._settings.get("buffer_size")),
            pre_silence=float(self._window._settings.get("pre_sweep_silence")),
            post_silence=float(self._window._settings.get("post_sweep_silence")),
            latency=self._window.devices.sweep_latency_mode(),
            bluetooth_headphone_mode=bool(self._window._settings.get("bluetooth_headphone_mode")),
            start_alignment_confidence_min=float(
                self._window._settings.get("start_alignment_confidence_min")
            ),
            end_marker_confidence_min=float(
                self._window._settings.get("end_marker_confidence_min")
            ),
            timing_drift_max_ms=float(self._window._settings.get("timing_drift_max_ms")),
            sweep_noise_margin_min_db=float(
                self._window._settings.get("sweep_noise_margin_min_db")
            ),
            snr_warn_db=float(self._window._settings.get("snr_warn_db")),
            failed_recording_dir=self.failed_recording_dir(),
            sweep_f_low=_MEASUREMENT_F_MIN,
            sweep_f_high=_MEASUREMENT_F_MAX,
        )
        if not started:
            self.on_sweep_error(
                "The previous sweep is still stopping. Wait a moment and try again."
            )
            return

        channel_text = f", channel {self.queue.stage}" if self.two_channel_enabled else ""
        self._window._statusbar.showMessage(
            f"Sweeping {self.queue.index + 1}/{self.queue.target}{channel_text} "
            f"(attempt {self.queue.attempts})..."
        )
        logger.info(
            "Sweep started",
            extra={
                "source": "measurement",
                "details": {
                    "index": self.queue.index + 1,
                    "total": self.queue.target,
                    "attempt": self.queue.attempts,
                    "sample_rate": int(self._window._settings.get("sample_rate")),
                    "buffer_size": int(self._window._settings.get("buffer_size")),
                    "output_level_db": float(self._window.measure_tab.queue_level_spin.value()),
                    "input_channel": input_channel + 1,
                    "output_channel": (output_channel + 1) if output_channel is not None else None,
                },
            },
        )

    def _start_second_two_channel_sweep(self) -> None:
        if self.queue_active() and self.queue.pending_pair_first_raw is not None:
            self.start_next_sweep(second_stage=True)

    def on_sweep_progress(self, frac: float) -> None:
        self._window.measure_tab.sweep_progress.setValue(int(max(0.0, min(1.0, frac)) * 100.0))

    def on_timing_quality(
        self, start_conf: float, end_conf: float, drift_ms: float, snr_db: float
    ) -> None:
        self.queue.last_timing_quality = (start_conf, end_conf, drift_ms, snr_db)

    def on_measurement_diagnostics(self, diagnostics: object) -> None:
        self.queue.last_diagnostics = diagnostics
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
        logger.info(
            "Measurement diagnostics received", extra={"source": "diagnostics", "details": details}
        )

    def on_sweep_finished(self, recording: np.ndarray, sweep: np.ndarray) -> None:
        try:
            (freqs, mag_db), _distortion = self.analyze_sweep(recording, sweep)
            spl_offset = self.spl_offset_db()
            if spl_offset is not None:
                mag_db = mag_db + spl_offset
            if self.two_channel_enabled:
                if self.queue.stage == 1:
                    self.queue.pending_pair_first_raw = (freqs, mag_db)
                    self.queue.pending_pair_first_diagnostics = self.queue.last_diagnostics
                    self.queue.start_second_stage = True
                    self.queue.state = QueueState.QUEUE_RUNNING
                    self.state_changed.emit()
                    self._window._statusbar.showMessage(
                        "Channel 1/L complete. Starting channel 2/R."
                    )
                    return
                if self.queue.stage != 2 or self.queue.pending_pair_first_raw is None:
                    raise ValueError("The first channel result is unavailable.")
                first_freqs, first_mag = self.queue.pending_pair_first_raw
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
                self.queue.pending_pair = TwoChannelCurvePair(
                    channel_1=first_ds,
                    channel_2=second_ds,
                    channel_1_diagnostics=self.queue.pending_pair_first_diagnostics,
                    channel_2_diagnostics=self.queue.last_diagnostics,
                )
                self.queue.state = QueueState.PASS_FAIL
                self.state_changed.emit()
                self.curves_changed.emit(True)
                self._window._statusbar.showMessage(
                    "Two-channel pair complete. Waiting for review."
                )
                QTimer.singleShot(0, self.show_pass_fail_dialog)
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

            self.queue.pending_curve = (freqs_ds, mag_ds)
            logger.info(
                "Frequency response processed",
                extra={
                    "source": "processing",
                    "details": {"input_points": len(freqs), "output_points": len(freqs_ds)},
                },
            )
            self.queue.state = QueueState.PASS_FAIL
            self.state_changed.emit()
            self.curves_changed.emit(True)
            timing_msg = ""
            if self.queue.last_timing_quality is not None:
                start_conf, end_conf, drift_ms, snr_db = self.queue.last_timing_quality
                bluetooth_mode = bool(
                    getattr(
                        self.queue.last_diagnostics,
                        "bluetooth_headphone_mode",
                        False,
                    )
                )
                warning_prefix = ""
                warning_message = None
                if self.queue.last_diagnostics is not None:
                    warning_message = getattr(
                        self.queue.last_diagnostics,
                        "warning_message",
                        None,
                    )
                if warning_message:
                    warning_reason = getattr(
                        self.queue.last_diagnostics,
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
            self._window._statusbar.showMessage(f"Sweep complete. Waiting for review.{timing_msg}")
            QTimer.singleShot(0, self.show_pass_fail_dialog)
        except Exception as exc:
            self.on_sweep_error(f"Processing error: {exc}")

    def on_sweep_error(self, message: str) -> None:
        logger.error(message, extra={"source": "measurement"})
        self.close_pass_fail_dialog()
        self.queue.pending_curve = None
        self.queue.pending_pair = None
        self.queue.pending_pair_first_raw = None
        self.queue.pending_pair_first_diagnostics = None
        self.queue.start_second_stage = False
        self.queue.stage = 0
        self.queue.last_timing_quality = None
        self._window.measure_tab.sweep_progress.setValue(0)

        failure_reason = None
        if self.queue.last_diagnostics is not None:
            failure_reason = getattr(self.queue.last_diagnostics, "failure_reason", None)
        is_timing_quality_error = is_retryable_timing_failure(
            message=message,
            failure_reason=failure_reason,
        )
        # A device or stream failure is terminal: retrying only repeats it, so
        # two-channel mode must not offer a pair retry for it.
        retry_complete_pair = bool(
            self.two_channel_enabled
            and self.queue_active()
            and not is_device_failure(message, failure_reason)
        )
        if (
            self.queue_active()
            and (is_timing_quality_error or retry_complete_pair)
            and self.queue.attempts < MAX_SWEEP_ATTEMPTS
        ):
            diagnostics_text = ""
            if (
                self.queue.last_diagnostics is not None
                and getattr(self.queue.last_diagnostics, "failure_reason", None) is not None
            ):
                diagnostics_text = "\n\n" + format_diagnostics_summary(self.queue.last_diagnostics)
            self.queue.state = QueueState.QUEUE_RUNNING
            self.state_changed.emit()
            self._window.devices.start_level_monitor()
            retry_subject = (
                "The two-channel pair failed. Both channels will be measured again."
                if retry_complete_pair
                else f"Measurement {self.queue.index + 1} did not meet timing quality."
            )
            retry_msg = (
                f"{message}\n\n{retry_subject}\n"
                f"Retry attempt {self.queue.attempts + 1} of {MAX_SWEEP_ATTEMPTS}?"
                f"{diagnostics_text}"
            )
            choice = QMessageBox.question(
                self._window,
                "Two-Channel Pair Retry" if retry_complete_pair else "Timing Quality Retry",
                retry_msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if choice == QMessageBox.StandardButton.Yes:
                self._window._statusbar.showMessage(
                    f"{message} Retrying measurement {self.queue.index + 1} "
                    f"({self.queue.attempts}/{MAX_SWEEP_ATTEMPTS})..."
                )
                QTimer.singleShot(150, self.start_next_sweep)
                return
            self.cancel_queue()
            cancel_reason = (
                "two-channel pair retry" if retry_complete_pair else "timing-quality retry"
            )
            self._window._statusbar.showMessage(
                f"Queue canceled by user after {cancel_reason} prompt."
            )
            return

        dialog_message = message
        if (
            self.queue.last_diagnostics is not None
            and getattr(self.queue.last_diagnostics, "failure_reason", None) is not None
        ):
            dialog_message = (
                f"{message}\n\n{format_diagnostics_summary(self.queue.last_diagnostics)}"
            )
        # Terminal: the queue is over. A full reset clears the counters too, so
        # no phantom queue survives in the progress bar or the console.
        self.queue.reset()
        self.queue.state = QueueState.IDLE
        self.update_queue_progress()
        self.state_changed.emit()
        self._window.devices.start_level_monitor()
        self._window._statusbar.showMessage(message)
        QMessageBox.warning(self._window, "Sweep Error", dialog_message)

    def _on_sweep_thread_finished(self) -> None:
        if self.queue.start_second_stage:
            self.queue.start_second_stage = False
            QTimer.singleShot(0, self._start_second_two_channel_sweep)
            return
        if self.queue.state != QueueState.PASS_FAIL:
            self._window.devices.start_level_monitor()

    def on_keep(self) -> None:
        if self.queue.state != QueueState.PASS_FAIL:
            return

        if self.two_channel_enabled:
            if self.queue.pending_pair is None:
                return
            self.close_pass_fail_dialog()
            self.two_channel_pairs.append(self.queue.pending_pair)
            self.kept_pair_meta.append({"timing_quality": self.queue.last_timing_quality})
            self.kept_distortion = self.queue.last_distortion
            self.queue.pending_pair = None
            self.queue.pending_pair_first_raw = None
            self.queue.pending_pair_first_diagnostics = None
            self.queue.stage = 0
            self.queue.index += 1
            self.queue.attempts = 0
            self.recompute_two_channel_results()
            self.update_queue_progress()
            self.curves_changed.emit(False)
            self._window.measure_io.mark_dirty()
            self._window.commands.trigger("measurement_kept")
            if self.queue.index >= self.queue.target:
                self.finish_queue()
                return
            self.queue.state = QueueState.QUEUE_RUNNING
            self.state_changed.emit()
            self.start_next_sweep()
            return

        if self.queue.pending_curve is None:
            return

        self.close_pass_fail_dialog()
        self.kept_curves.append(self.queue.pending_curve)
        self.kept_sweep_meta.append(
            {
                "diagnostics": self.queue.last_diagnostics,
                "timing_quality": self.queue.last_timing_quality,
                "distortion": self.queue.last_distortion,
            }
        )
        self.kept_distortion = self.queue.last_distortion
        logger.info(
            "Measurement kept",
            extra={
                "source": "review",
                "details": {"index": self.queue.index + 1, "kept_count": len(self.kept_curves)},
            },
        )
        self._window.commands.trigger("measurement_kept")
        self.queue.pending_curve = None
        self.queue.pending_pair = None
        self.queue.pending_pair_first_raw = None
        self.queue.pending_pair_first_diagnostics = None
        self.queue.stage = 0
        self.queue.index += 1
        self.queue.attempts = 0

        self.recompute_average()
        self.recompute_variation()
        self.update_queue_progress()
        self.curves_changed.emit(False)
        self._window.measure_io.mark_dirty()
        match = self._window.measure_compare.target_match_message()
        if match:
            self._window._statusbar.showMessage(
                f"Kept {len(self.kept_curves)} measurement(s). {match}"
            )

        if self.queue.index >= self.queue.target:
            self.finish_queue()
            return

        self.queue.state = QueueState.QUEUE_RUNNING
        self.state_changed.emit()
        self.start_next_sweep()

    def on_fail(self) -> None:
        if self.queue.state != QueueState.PASS_FAIL:
            return

        self.close_pass_fail_dialog()
        logger.warning(
            "Measurement rejected",
            extra={"source": "review", "details": {"index": self.queue.index + 1}},
        )
        # A manual Fail repeats the same index with a fresh retry budget; the
        # attempts that produced the rejected sweep were not timing failures.
        self.queue.reset(keep_counters=True)
        self.queue.attempts = 0
        self.queue.state = QueueState.QUEUE_RUNNING
        self.state_changed.emit()
        self.curves_changed.emit(False)
        self._window._statusbar.showMessage(
            f"Measurement {self.queue.index + 1} failed. Redoing same index."
        )
        self.start_next_sweep()

    def cancel_queue(self) -> None:
        self.abort_active_sweep()
        self.close_pass_fail_dialog()
        # One reset clears the counters and every pending curve or pair.
        self.queue.reset()
        self.queue.state = QueueState.IDLE
        self._window.measure_tab.sweep_progress.setValue(0)
        self.update_queue_progress()
        self.curves_changed.emit(False)
        self.state_changed.emit()
        self._window.devices.start_level_monitor()
        self._window._statusbar.showMessage("Queue canceled.")

    def finish_queue(self) -> None:
        self.queue.reset()
        self.queue.state = QueueState.IDLE
        self._window.measure_tab.sweep_progress.setValue(100)
        self.state_changed.emit()
        self._window.devices.start_level_monitor()
        match = self._window.measure_compare.target_match_message()
        self._window._statusbar.showMessage(
            f"Queue complete. {match}" if match else "Queue complete."
        )
        self._window.commands.trigger("queue_complete")

    def update_queue_progress(self) -> None:
        target = max(0, self.queue.target)
        self._window.measure_tab.queue_progress_bar.setRange(0, max(1, target))
        self._window.measure_tab.queue_progress_bar.setValue(min(self.queue.index, max(1, target)))
        kept_count = (
            len(self.two_channel_pairs) if self.two_channel_enabled else len(self.kept_curves)
        )
        self._window.measure_tab.queue_progress_label.setText(f"Kept: {kept_count}")

    def show_pass_fail_dialog(self) -> None:
        pending_available = (
            self.queue.pending_pair is not None
            if self.two_channel_enabled
            else self.queue.pending_curve is not None
        )
        if self.queue.state != QueueState.PASS_FAIL or not pending_available:
            return

        if self.pass_fail_dialog is not None:
            self.pass_fail_dialog.raise_()
            self.pass_fail_dialog.activateWindow()
            return

        dlg = PassFailDialog(
            index=self.queue.index + 1,
            total=max(self.queue.target, self.queue.index + 1),
            timing_quality=self.queue.last_timing_quality,
            diagnostics=self.queue.last_diagnostics,
            distortion=self.queue.last_distortion,
            deviation_summary=self._window.measure_compare.pending_deviation_summary(),
            parent=self._window,
        )
        dlg.adjustSize()
        target_rect = self._window._plots.bottom_plot_global_rect()
        x = target_rect.center().x() - dlg.width() // 2
        y = target_rect.center().y() - dlg.height() // 2
        x = max(target_rect.left() + 12, min(x, target_rect.right() - dlg.width() - 12))
        y = max(target_rect.top() + 12, min(y, target_rect.bottom() - dlg.height() - 12))
        dlg.move(x, y)
        dlg.finished.connect(lambda _result: self._handle_pass_fail_choice(dlg))
        self.pass_fail_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _handle_pass_fail_choice(self, dlg: PassFailDialog) -> None:
        if self.pass_fail_dialog is dlg:
            self.pass_fail_dialog = None

        choice = dlg.choice()
        if choice == PassFailDialog.KEEP:
            self.on_keep()
        elif choice == PassFailDialog.FAIL:
            self.on_fail()
        else:
            self.cancel_queue()

    def close_pass_fail_dialog(self) -> None:
        if self.pass_fail_dialog is None:
            return
        dlg = self.pass_fail_dialog
        self.pass_fail_dialog = None
        dlg.blockSignals(True)
        dlg.close()

    def recompute_average(self) -> None:
        if not self.kept_curves:
            self.average = None
            return

        freqs, mag_db = compute_rms_average(
            self.kept_curves,
            n_points=_DISPLAY_AVG_POINTS,
            f_ref=1000.0,
            f_min=_MEASUREMENT_F_MIN,
            f_max=_MEASUREMENT_F_MAX,
            # In dB SPL the average must keep the absolute level.
            normalize_ref=self.spl_offset_db() is None,
        )
        self.average = (freqs, mag_db)

    def _two_channel_curves_by_key(self) -> dict[str, list[tuple[np.ndarray, np.ndarray]]]:
        return {
            "channel_1": channel_curves(self.two_channel_pairs, 1),
            "channel_2": channel_curves(self.two_channel_pairs, 2),
            "combined": combined_pair_curves(
                self.two_channel_pairs,
                n_points=_DISPLAY_AVG_POINTS,
            ),
        }

    def recompute_two_channel_results(self) -> None:
        averages: dict[str, object] = {}
        for key, curves in self._two_channel_curves_by_key().items():
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
        self.two_channel_averages = averages
        self._recompute_two_channel_variations()

    def _recompute_two_channel_variations(self) -> None:
        """Variation band per channel key, with the active HRTF applied."""
        active_hrtf = self.hrtf if self.is_hrtf_active() else None
        variations: dict[str, object] = {}
        for key, curves in self._two_channel_curves_by_key().items():
            raw_average = self.two_channel_averages.get(key)
            variations[key] = self.variation_from_curves(
                curves,
                raw_average if isinstance(raw_average, tuple) else None,
                hrtf=active_hrtf,
            )
        self.two_channel_variations = variations

    def active_two_channel_key(self) -> str:
        if self.two_channel_bottom_mode == "combined":
            return "combined"
        return self.two_channel_selection

    def active_two_channel_average(
        self,
    ) -> tuple[np.ndarray, np.ndarray] | None:
        value = self.two_channel_averages.get(self.active_two_channel_key())
        return value if isinstance(value, tuple) else None

    def active_measure_curves(self) -> list[tuple[np.ndarray, np.ndarray]]:
        if not self.two_channel_enabled:
            return list(self.kept_curves)
        key = self.active_two_channel_key()
        if key == "channel_1":
            return channel_curves(self.two_channel_pairs, 1)
        if key == "channel_2":
            return channel_curves(self.two_channel_pairs, 2)
        return combined_pair_curves(
            self.two_channel_pairs,
            n_points=_DISPLAY_AVG_POINTS,
        )

    def active_measure_count(self) -> int:
        return len(self.two_channel_pairs) if self.two_channel_enabled else len(self.kept_curves)

    def active_measure_label(self) -> str:
        if not self.two_channel_enabled:
            return ""
        return curve_label_for_selection(self.active_two_channel_key())

    def active_measure_session(self) -> SessionData:
        label = self.active_measure_label()
        if label in {"L", "R"}:
            return replace(self._window._session, channel_side=label)
        if label == "BOTH":
            return replace(self._window._session, channel_side="")
        return self._window._session

    def active_measure_variation(self):
        if self.two_channel_enabled:
            return self.two_channel_variations.get(self.active_two_channel_key())
        return self.variation

    def recompute_variation(self) -> None:
        active_hrtf = self.hrtf if self.is_hrtf_active() else None
        self.variation = self.variation_from_kept_curves(hrtf=active_hrtf)

    def variation_from_kept_curves(
        self,
        *,
        hrtf: HRTFCurve | None,
    ) -> VariationBand | None:
        return self.variation_from_curves(
            self.kept_curves,
            self.average,
            hrtf=hrtf,
        )

    def variation_from_curves(
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

    def average_curve_with_hrtf(
        self,
        hrtf: HRTFCurve | None,
    ) -> tuple[np.ndarray, np.ndarray] | None:
        source = self.active_two_channel_average() if self.two_channel_enabled else self.average
        if source is None:
            return None
        freqs, mag_db = source
        if hrtf is None:
            return freqs, mag_db
        return freqs, hrtf.apply(freqs, mag_db)

    def bottom_curve_for_display_and_export(
        self,
    ) -> tuple[np.ndarray, np.ndarray] | None:
        active_hrtf = self.hrtf if self.is_hrtf_active() else None
        return self.average_curve_with_hrtf(active_hrtf)

    def bottom_curve_for_display(self) -> tuple[np.ndarray, np.ndarray] | None:
        curve = self.bottom_curve_for_display_and_export()
        if curve is None:
            return None

        freqs, mag_db = curve
        return smooth_fractional_octave(
            freqs,
            mag_db,
            fraction=_DISPLAY_AVG_SMOOTHING,
        )

    def refresh(self, *_args) -> None:
        """Redraw the plots from the kept curves."""
        self.curves_changed.emit(False)

    def _redraw(self, show_pending: bool = False) -> None:
        overlay_freqs, overlay_series = self._distortion_overlay_series()
        self._window._plots.set_distortion_overlay(overlay_freqs, overlay_series)
        self._window.measure_compare.sync_layers()
        delta_on = self._window.measure_compare.delta_view_enabled()
        if self.two_channel_enabled:
            active_hrtf = self.hrtf if self.is_hrtf_active() else None
            pairs = list(self.two_channel_pairs)
            if show_pending and self.queue.pending_pair is not None:
                pairs.append(self.queue.pending_pair)
            self._recompute_two_channel_variations()
            averages: dict[str, object] = {}
            for key in ("channel_1", "channel_2", "combined"):
                raw_average = self.two_channel_averages.get(key)
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
            show_variation = self.bottom_view_mode() == "variation"
            if delta_on:
                # Limitation: two-channel delta view replaces only the *active*
                # bottom viewport's curve. The other viewports keep showing
                # their own averages, the pane titles still read "Average", and
                # the target line and reference layers are single-channel only.
                active_key = self.active_two_channel_key()
                delta = self._window.measure_compare.delta_result(
                    averages.get(active_key)
                    if isinstance(averages.get(active_key), tuple)
                    else None
                )
                if delta is not None:
                    averages[active_key] = (delta.freqs, delta.delta_db)
                    show_variation = False
            self._window._plots.two.update_frequency_response(
                top_channel_1=channel_curves(pairs, 1),
                top_channel_2=channel_curves(pairs, 2),
                averages=averages,
                variations=self.two_channel_variations,
                show_variation=show_variation,
            )
            self._window.measure_io.sync_export_button()
            return

        avg = self.bottom_curve_for_display()
        self.recompute_variation()

        kept = list(self.kept_curves)
        if show_pending and self.queue.pending_curve is not None:
            kept = kept + [self.queue.pending_curve]

        bottom_mode = self.bottom_view_mode()
        if delta_on:
            # The bottom viewport shows measurement - target instead of the
            # average, so the variation band has nothing to describe.
            delta = self._window.measure_compare.delta_result(
                self.bottom_curve_for_display_and_export()
            )
            if delta is not None:
                avg = (delta.freqs, delta.delta_db)
                bottom_mode = "average"

        self._window._plots.update_curves(
            kept=kept,
            average=avg,
            variation=self.variation,
            bottom_mode=bottom_mode,
            animate_last=show_pending and self.queue.pending_curve is not None,
        )
        self._window.measure_io.sync_export_button()

    def bottom_view_mode(self) -> str:
        if self.is_hrtf_active() and self.hrtf.is_variation:
            return "variation"
        return "variation" if self._window.measure_tab.variation_toggle.isChecked() else "average"

    # ------------------------------------------------------------------
    # Distortion overlay
    # ------------------------------------------------------------------

    def _distortion_overlay_enabled(self) -> bool:
        toggle = getattr(getattr(self._window, "measure_tab", None), "distortion_toggle", None)
        return toggle is not None and bool(toggle.isChecked())

    def distortion_analysis_allowed(self) -> bool:
        """Whether the last sweep is clean enough to measure harmonics on.

        Below ``_DISTORTION_MIN_SNR_DB`` the Farina packets sit inside the
        noise floor and the numbers would be meaningless.
        """
        if not self._distortion_overlay_enabled():
            return False
        timing = self.queue.last_timing_quality
        if timing is None:
            return False
        try:
            return float(timing[3]) >= _DISTORTION_MIN_SNR_DB
        except (TypeError, ValueError, IndexError):
            return False

    def _distortion_overlay_series(self):
        # During review show the pending sweep's analysis; afterwards keep
        # showing the most recently kept sweep's.
        analysis = self.queue.last_distortion or self.kept_distortion
        if analysis is None or not self._distortion_overlay_enabled():
            return None, None
        series: dict[str, np.ndarray] = {"THD": np.asarray(analysis.thd_db)}
        for order, name in ((2, "H2"), (3, "H3")):
            values = analysis.orders.get(order)
            if values is not None:
                series[name] = np.asarray(values)
        return np.asarray(analysis.freqs), series

    def on_distortion_overlay_changed(self, *_args) -> None:
        self._window._settings.set("measure_distortion_overlay", self._distortion_overlay_enabled())
        self.curves_changed.emit(False)

    def analyze_sweep(
        self,
        recording: np.ndarray,
        sweep: np.ndarray,
    ) -> tuple[tuple[np.ndarray, np.ndarray], HarmonicAnalysis | None]:
        """Frequency response plus, when asked for, harmonic distortion.

        The distortion pass is strictly optional: any failure inside it is
        logged and dropped, because a distortion overlay must never cost the
        operator a measurement.
        """
        fs = int(self._window._settings.get("sample_rate"))
        freqs, mag_db = compute_frequency_response(
            recording=recording,
            sweep=sweep,
            fs=fs,
            f_low=_MEASUREMENT_F_MIN,
            f_high=_MEASUREMENT_F_MAX,
        )
        distortion: HarmonicAnalysis | None = None
        if self.distortion_analysis_allowed():
            try:
                distortion = harmonic_responses(
                    deconvolve_sweep(recording, sweep, fs),
                    f_low=_MEASUREMENT_F_MIN,
                    f_high=_MEASUREMENT_F_MAX,
                )
            except Exception as exc:  # never fail a sweep over the overlay
                logger.warning(
                    f"Distortion analysis skipped: {exc}", extra={"source": "processing"}
                )
                distortion = None
        self.queue.last_distortion = distortion
        return (freqs, mag_db), distortion

    # ------------------------------------------------------------------
    # Level mode (1 kHz reference vs absolute dB SPL)
    # ------------------------------------------------------------------

    def level_mode(self) -> str:
        mode = str(self._window._settings.get("measure_level_mode") or "ref_1khz")
        return "dbspl" if mode == "dbspl" else "ref_1khz"

    def spl_offset_db(self) -> float | None:
        """dB offset to absolute SPL, or None to stay in 1 kHz reference mode.

        Falls back to reference mode — with a single status-bar note — when
        dB SPL is selected but the input device has no calibration.
        """
        if self.level_mode() != "dbspl":
            return None
        sensitivity = self._window.devices.calibrated_sensitivity()
        if sensitivity is None:
            if not self.spl_uncalibrated_warned:
                self.spl_uncalibrated_warned = True
                self._window._statusbar.showMessage(
                    "dB SPL needs a calibrated input device; showing 1 kHz "
                    "reference levels instead."
                )
            return None
        try:
            return absolute_spl_offset_db(
                sensitivity_pa_per_fs=float(sensitivity),
                output_level_db=float(self._window.measure_tab.queue_level_spin.value()),
            )
        except (TypeError, ValueError):
            return None

    def has_kept_measurements(self) -> bool:
        if self.two_channel_enabled:
            return bool(self.two_channel_pairs) or self.queue.pending_pair is not None
        return bool(self.kept_curves) or self.queue.pending_curve is not None

    def sync_level_mode_combo(self) -> None:
        combo = getattr(getattr(self._window, "measure_tab", None), "level_mode_combo", None)
        if combo is None:
            return
        combo.blockSignals(True)
        combo.setCurrentIndex(1 if self.level_mode() == "dbspl" else 0)
        combo.blockSignals(False)

    def on_level_mode_changed(self, *_args) -> None:
        """Switch level modes, refusing to mix modes inside one kept set."""
        combo = self._window.measure_tab.level_mode_combo
        chosen = str(combo.currentData() or "ref_1khz")
        chosen = "dbspl" if chosen == "dbspl" else "ref_1khz"
        if chosen == self.level_mode():
            return
        if self.queue.state != QueueState.IDLE:
            QMessageBox.information(
                self._window,
                "Busy",
                "Cannot change the level mode while a measurement is running.",
            )
            self.sync_level_mode_combo()
            return
        if self.has_kept_measurements():
            label = "dB SPL" if chosen == "dbspl" else "1 kHz reference"
            choice = QMessageBox.question(
                self._window,
                "Clear Measurements?",
                "Kept measurements use the current level mode and cannot be "
                f"mixed with {label}.\n\nClear all measurements and switch?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                self.sync_level_mode_combo()
                self._window._statusbar.showMessage("Level mode unchanged.")
                return
            self.discard_all_measurements()
        self._window._settings.set("measure_level_mode", chosen)
        self.spl_uncalibrated_warned = False
        self.sync_level_mode_combo()
        if chosen == "dbspl" and self._window.devices.calibrated_sensitivity() is None:
            self._window._statusbar.showMessage(
                "dB SPL selected, but the input device is not calibrated; "
                "curves stay at 1 kHz reference until it is."
            )
        else:
            self._window._statusbar.showMessage(
                "Level mode: dB SPL." if chosen == "dbspl" else "Level mode: 1 kHz reference."
            )
        self.curves_changed.emit(False)

    def on_hrtf_selected(self) -> None:
        path = self._window.measure_tab.hrtf_combo.currentData()
        if not path:
            self.hrtf = None
            self._window._settings.set("hrtf_path", None)
            self._sync_hrtf_ui()
            self.curves_changed.emit(False)
            self._window.measure_io.mark_dirty()
            self._window._statusbar.showMessage("HRTF cleared.")
            return

        try:
            self.hrtf = HRTFCurve(path)
            self._window._settings.set("hrtf_path", path)
            self._sync_hrtf_ui()
            self._window.measure_tab.hrtf_toggle.setChecked(True)
            if self.hrtf.is_variation:
                self._window.measure_tab.variation_toggle.setChecked(True)
            self.curves_changed.emit(False)
            self._window.measure_io.mark_dirty()
            kind = "population variation compensation" if self.hrtf.is_variation else "HRTF"
            self._window._statusbar.showMessage(f"Loaded {kind}: {Path(path).name}")
        except Exception as exc:
            QMessageBox.warning(self._window, "HRTF Load Error", str(exc))
            self.hrtf = None
            self._window._settings.set("hrtf_path", None)
            self._sync_hrtf_ui()
            self.curves_changed.emit(False)

    def import_dropped_measurement_files(self, paths: list[str]) -> None:
        if self.two_channel_enabled:
            QMessageBox.information(
                self._window,
                "Single Channel Only",
                "TXT drag-and-drop import is available only in Single Channel mode.",
            )
            self._window._statusbar.showMessage(
                "Measurement import blocked: Two Channel mode is active."
            )
            return
        if self.queue.state != QueueState.IDLE:
            QMessageBox.information(
                self._window,
                "Busy",
                "Measurement import is only available while idle.",
            )
            self._window._statusbar.showMessage("Measurement import blocked: queue is active.")
            return

        loaded = 0
        failed: list[str] = []
        for path in paths:
            try:
                curve = load_two_column_txt_curve(path, label="Measurement")
            except Exception as exc:
                failed.append(f"{Path(path).name}: {exc}")
                continue
            self.kept_curves.append(curve)
            # An imported curve carries no sweep of its own, so its metadata
            # slot stays empty; the lists must still line up one for one.
            self.kept_sweep_meta.append({})
            loaded += 1

        if loaded > 0:
            self.recompute_average()
            self.recompute_variation()
            self.update_queue_progress()
            self.curves_changed.emit(False)
            self._window.measure_io.mark_dirty()

        if loaded == 0 and failed:
            QMessageBox.warning(
                self._window,
                "Import Failed",
                "No files were imported.\n\n" + "\n".join(failed[:8]),
            )
            self._window._statusbar.showMessage("Measurement import failed.")
            return

        if failed:
            QMessageBox.warning(
                self._window,
                "Import Completed With Warnings",
                f"Loaded {loaded} file(s), failed {len(failed)} file(s).\n\n"
                + "\n".join(failed[:8]),
            )

        self._window._statusbar.showMessage(
            f"Measurement import complete: loaded {loaded}, failed {len(failed)}."
        )

    def clear_all(self) -> None:
        if self.queue.state != QueueState.IDLE:
            QMessageBox.information(
                self._window,
                "Busy",
                "Cannot clear measurements while queue is active.",
            )
            return

        active_has_data = (
            bool(self.two_channel_pairs) or self.queue.pending_pair is not None
            if self.two_channel_enabled
            else bool(self.kept_curves) or self.queue.pending_curve is not None
        )
        if not active_has_data:
            return

        if bool(self._window._settings.get("confirm_clear_measurements")):
            confirmed, dont_show_again = self.confirm_clear_all()
            if not confirmed:
                return
            if dont_show_again:
                self._window._settings.set("confirm_clear_measurements", False)
                self._window._settings_widget.refresh_from_settings()

        self.discard_all_measurements()
        self._window._statusbar.showMessage("All measurements cleared.")

    def discard_all_measurements(self) -> None:
        """Drop every kept curve and reset the queue. No prompts, no guards.

        Split out of :meth:`clear_all` so the level-mode switch can clear
        without asking the user a second time.
        """
        if self.two_channel_enabled:
            self.two_channel_pairs.clear()
            self.kept_pair_meta.clear()
            self.two_channel_averages.clear()
            self.two_channel_variations.clear()
            self.queue.pending_pair = None
            self.queue.pending_pair_first_raw = None
            self.queue.pending_pair_first_diagnostics = None
            self.curves_changed.emit(False)
        else:
            self.kept_curves.clear()
            self.kept_sweep_meta.clear()
            self.average = None
            self.variation = None
            self.queue.pending_curve = None
            # ``clear_all`` resets the comparison layers too, so the loaded
            # target and references are pushed straight back onto the plot.
            self._window._plots.clear_all()
            self._window.measure_compare.sync_layers()
        self.queue.reset()
        self.kept_distortion = None
        self.update_queue_progress()
        self._window.measure_tab.sweep_progress.setValue(0)
        self._window.measure_io.sync_export_button()
        self.state_changed.emit()
        self._window.measure_io.mark_dirty()

    def confirm_clear_all(self) -> tuple[bool, bool]:
        dialog = QMessageBox(self._window)
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

    def undo_last_measurement(self) -> None:
        if self.queue.state != QueueState.IDLE:
            QMessageBox.information(
                self._window,
                "Busy",
                "Undo is only available while idle.",
            )
            return

        # The overlay described the sweep being undone.
        self.kept_distortion = None

        if self.two_channel_enabled:
            if not self.two_channel_pairs:
                return
            self.two_channel_pairs.pop()
            if self.kept_pair_meta:
                self.kept_pair_meta.pop()
            self.recompute_two_channel_results()
        else:
            if not self.kept_curves:
                return
            self.kept_curves.pop()
            if self.kept_sweep_meta:
                self.kept_sweep_meta.pop()
            self.recompute_average()
            self.recompute_variation()
        self.update_queue_progress()
        self.curves_changed.emit(False)
        self.state_changed.emit()
        self._window.measure_io.mark_dirty()
        self._window._statusbar.showMessage("Last kept measurement removed.")

    def on_queue_level_changed(self, value: float) -> None:
        if hasattr(self._window._settings, "clear_session"):
            self._window._settings.clear_session("queue_output_level_db")
        clamped = max(-120.0, min(0.0, float(value)))
        if abs(clamped - float(value)) > 1e-9:
            self._window.measure_tab.queue_level_spin.blockSignals(True)
            self._window.measure_tab.queue_level_spin.setValue(clamped)
            self._window.measure_tab.queue_level_spin.blockSignals(False)
        if self._window.measure_tab.queue_level_persist_toggle.isChecked():
            self._window._settings.set("queue_output_level_db", clamped)
        if hasattr(self._window, "_plots"):
            self._window._plots.two.set_generator_level(clamped)
        self._balance_level_db = clamped
        if self._balance_engine is not None:
            self._balance_engine.set_parameters(
                self._balance_waveform,
                self._balance_frequency,
                self._balance_level_db,
            )

    def on_queue_count_changed(self, _value: int) -> None:
        if hasattr(self._window._settings, "clear_session"):
            self._window._settings.clear_session("queue_count")

    def on_queue_level_persist_changed(self, _state: int) -> None:
        persist = self._window.measure_tab.queue_level_persist_toggle.isChecked()
        self._window._settings.set("queue_output_level_persist", persist)
        if persist:
            self._window._settings.set(
                "queue_output_level_db", float(self._window.measure_tab.queue_level_spin.value())
            )

    def failed_recording_dir(self) -> str | None:
        """Folder for failed-recording dumps, or None when the setting is off."""
        if not bool(self._window._settings.get("save_failed_recordings")):
            return None
        return str(config_dir() / "failed_recordings")
