"""
Everything the window does on behalf of the R&D tab.

``RndBridge`` wires the R&D tab's requests: its sweeps (run on the window's
sweep runner and queue, shared with Measure), the review dialog, R&D session
files and crash recovery, exports, and the hand-offs between tabs (Measure →
R&D, Measure → Curator, R&D → Curator).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from dms.audio_engine import SweepWorker
from dms.curator.metadata import shared_metadata
from dms.curator.models import CurveData
from dms.export import export_curve, export_variation
from dms.file_io import ensure_extension, same_session_file
from dms.hrtf import HRTFCurve
from dms.measure_queue import MAX_SWEEP_ATTEMPTS, QueueState
from dms.measurement_alignment import is_retryable_timing_failure
from dms.processing import downsample_to_log_points, generate_log_sweep, normalize_at_1khz
from dms.recovery import RecoveryCandidate, rnd_recovery_manager
from dms.rnd.models import RnDGroup, RnDMeasurement, generate_measurement_name
from dms.rnd.models import group_variation as rnd_group_variation
from dms.rnd.persistence import RND_SESSION_EXTENSION, load_rnd_session, save_rnd_session
from dms.rnd.persistence import session_snapshot as rnd_persistence_snapshot
from dms.session import SessionData
from dms.settings_manager import config_dir
from dms.ui.measure_dialogs import RnDRecoveryDialog, RnDReviewDialog
from dms.version import __version__

if TYPE_CHECKING:
    from dms.ui.main_window import MainWindow


class RndBridge(QObject):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self.review_dialog: RnDReviewDialog | None = None
        self.sweep_active = False
        # Whether the R&D session holds changes no saved file has.
        self.dirty = False
        self._restored_recovery_candidate: RecoveryCandidate | None = None
        self._recovery = rnd_recovery_manager(
            config_dir() / "recovery" / "rnd",
            self._rnd_recovery_snapshot,
            parent=window,
        )
        self._recovery.save_succeeded.connect(self._on_rnd_recovery_saved)
        self._recovery.save_failed.connect(self._on_rnd_recovery_failed)
        rnd = window._rnd_widget
        rnd.state_changed.connect(self._on_rnd_state_changed)
        rnd.selection_changed.connect(self._on_rnd_selection_changed)
        rnd.view_state_changed.connect(self._on_rnd_selection_changed)
        rnd.measure_requested.connect(self.start_measurement)
        rnd.cancel_requested.connect(self._cancel_rnd_measurement)
        rnd.export_requested.connect(self.export_selected)
        rnd.send_to_curator_requested.connect(self.send_rnd_to_curator)
        rnd.save_requested.connect(self.save_session)
        rnd.load_requested.connect(self.load_session)
        rnd.input_channel_changed.connect(window.devices.on_rnd_input_channel_changed)
        rnd.notes_expanded_changed.connect(
            lambda expanded: window._settings.set("rnd_notes_expanded", bool(expanded))
        )
        rnd.splitter_ratio_changed.connect(
            lambda ratio: window._settings.set("rnd_splitter_ratio", float(ratio))
        )

    def shutdown(self) -> None:
        """Stop recovery saves and leave the clean-exit marker."""
        try:
            self._recovery.shutdown_clean()
        except Exception as exc:
            self._window._log_event("ERROR", "rnd", "R&D recovery cleanup failed", error=str(exc))

    def start_measurement(self) -> None:
        from dms.ui.measure_controller import _QUEUE_AMBIENT_WARN_DBFS

        if self._window.measure.queue.state != QueueState.IDLE:
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
            return
        if self._window.measure_tab.ch_combo.count() == 0:
            QMessageBox.warning(
                self._window,
                "No Input Channel",
                "Selected input device has no available input channels.",
            )
            return
        ambient_dbfs = float(self._window.devices.last_level_dbfs)
        if ambient_dbfs > _QUEUE_AMBIENT_WARN_DBFS:
            choice = QMessageBox.question(
                self._window,
                "Ambient Level Warning",
                "Current ambient/input RMS looks high before R&D measurement:\n"
                f"{ambient_dbfs:.1f} dBFS (warning threshold: {_QUEUE_AMBIENT_WARN_DBFS:.1f} dBFS).\n\n"
                "This can reduce measurement SNR.\n"
                "Start measurement anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                self._window._statusbar.showMessage(
                    "R&D measurement canceled due to high ambient level."
                )
                return

        self.sweep_active = True
        self._window.measure.queue.attempts = 0
        self._start_rnd_sweep()

    def _start_rnd_sweep(self) -> None:
        from dms.ui.measure_controller import _MEASUREMENT_F_MAX, _MEASUREMENT_F_MIN

        self._window.measure.stop_channel_balance()
        self._window.measure.queue.attempts += 1
        self._window.measure.queue.state = QueueState.SWEEPING
        self._window._apply_state_ui()
        self._window._rnd_widget.set_status(
            f"Sweeping attempt {self._window.measure.queue.attempts}..."
        )
        self._window.measure_tab.sweep_progress.setValue(0)

        output_device = self._window.devices.current_output_device()
        input_device = self._window.devices.current_input_device()
        input_channel = self._window.devices.current_input_channel()
        if output_device is None or input_device is None:
            self._on_rnd_sweep_error("Selected device is unavailable.")
            return
        if not self._window.devices.selected_audio_pair_is_compatible():
            self._on_rnd_sweep_error(self._window.devices.windows_audio_pair_message())
            return

        self._window.devices.level_monitor.stop()
        self._window.measure.queue.last_timing_quality = None
        self._window.measure.queue.last_diagnostics = None

        sweep = generate_log_sweep(
            duration=float(self._window._settings.get("sweep_duration")),
            fs=int(self._window._settings.get("sample_rate")),
            f_low=_MEASUREMENT_F_MIN,
            f_high=_MEASUREMENT_F_MAX,
        )
        output_level_db = float(self._window.measure_tab.queue_level_spin.value())
        sweep = (sweep * (10.0 ** (output_level_db / 20.0))).astype(np.float32, copy=False)

        worker = SweepWorker()
        worker.finished.connect(self._on_rnd_sweep_finished)
        worker.error.connect(self._on_rnd_sweep_error)
        worker.progress.connect(self._window.measure.on_sweep_progress)
        worker.timing_quality.connect(self._window.measure.on_timing_quality)
        worker.measurement_diagnostics.connect(self._window.measure.on_measurement_diagnostics)
        started = self._window.measure.sweep_runner.start(
            lambda: worker,
            sweep=sweep,
            output_device=output_device,
            input_device=input_device,
            output_device_label=self._window.devices.current_output_device_label(),
            input_device_label=self._window.devices.current_input_device_label(),
            input_channel=input_channel,
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
            failed_recording_dir=self._window.measure.failed_recording_dir(),
            sweep_f_low=_MEASUREMENT_F_MIN,
            sweep_f_high=_MEASUREMENT_F_MAX,
        )
        if not started:
            self._on_rnd_sweep_error(
                "The previous sweep is still stopping. Wait a moment and try again."
            )
            return
        self._window._statusbar.showMessage(
            f"R&D sweep started (attempt {self._window.measure.queue.attempts})."
        )
        self._window._log_event(
            "INFO",
            "rnd",
            "R&D sweep started",
            attempt=self._window.measure.queue.attempts,
            sample_rate=int(self._window._settings.get("sample_rate")),
            buffer_size=int(self._window._settings.get("buffer_size")),
            output_level_db=output_level_db,
        )

    def _on_rnd_sweep_finished(self, recording: np.ndarray, sweep: np.ndarray) -> None:
        try:
            (freqs, mag_db), _distortion = self._window.measure.analyze_sweep(recording, sweep)
            spl_offset = self._window.measure.spl_offset_db()
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
            self._window.measure.queue.pending_curve = (freqs_ds, mag_ds)
            self._window._rnd_widget.set_review_curve(self._window.measure.queue.pending_curve)
            self._window.measure.queue.state = QueueState.PASS_FAIL
            self._window._apply_state_ui()
            self._window._rnd_widget.set_status("Sweep complete. Waiting for review.")
            self._window._statusbar.showMessage("R&D sweep complete. Waiting for review.")
            QTimer.singleShot(0, self._show_rnd_review_dialog)
        except Exception as exc:
            self._on_rnd_sweep_error(f"Processing error: {exc}")

    def _on_rnd_sweep_error(self, message: str) -> None:
        self._window._log_event("ERROR", "rnd", message)
        self.close_review_dialog()
        self._window._rnd_widget.set_review_curve(None)
        self._window.measure.queue.pending_curve = None
        failure_reason = None
        if self._window.measure.queue.last_diagnostics is not None:
            failure_reason = getattr(
                self._window.measure.queue.last_diagnostics, "failure_reason", None
            )
        is_timing_quality_error = is_retryable_timing_failure(
            message=message,
            failure_reason=failure_reason,
        )
        if is_timing_quality_error and self._window.measure.queue.attempts < MAX_SWEEP_ATTEMPTS:
            choice = QMessageBox.question(
                self._window,
                "Timing Quality Retry",
                f"{message}\n\nRetry R&D measurement attempt {self._window.measure.queue.attempts + 1} of {MAX_SWEEP_ATTEMPTS}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if choice == QMessageBox.StandardButton.Yes:
                self._window.measure.queue.state = QueueState.IDLE
                self._window._apply_state_ui()
                QTimer.singleShot(150, self._start_rnd_sweep)
                return
        self.sweep_active = False
        self._window.measure.queue.attempts = 0
        self._window.measure.queue.state = QueueState.IDLE
        self._window.measure_tab.sweep_progress.setValue(0)
        self._window._apply_state_ui()
        self._window.devices.start_level_monitor()
        self._window._rnd_widget.set_status("Ready")
        self._window._statusbar.showMessage(message)
        QMessageBox.warning(self._window, "R&D Sweep Error", message)

    def _show_rnd_review_dialog(self) -> None:
        if (
            self._window.measure.queue.state != QueueState.PASS_FAIL
            or self._window.measure.queue.pending_curve is None
        ):
            return
        if self.review_dialog is not None:
            self.review_dialog.raise_()
            self.review_dialog.activateWindow()
            return
        previous = (
            self._window._rnd_widget.session.measurements[-1].name
            if self._window._rnd_widget.session.measurements
            else ""
        )
        dlg = RnDReviewDialog(
            previous,
            timing_quality=self._window.measure.queue.last_timing_quality,
            diagnostics=self._window.measure.queue.last_diagnostics,
            parent=self._window,
        )
        dlg.adjustSize()
        dlg.finished.connect(lambda _result: self._handle_rnd_review_choice(dlg))
        self.review_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _handle_rnd_review_choice(self, dlg: RnDReviewDialog) -> None:
        if self.review_dialog is dlg:
            self.review_dialog = None
        choice = dlg.choice()
        if choice == RnDReviewDialog.FAIL:
            self._window._rnd_widget.set_review_curve(None)
            self._window.measure.queue.pending_curve = None
            self._window.measure.queue.state = QueueState.IDLE
            self._window._apply_state_ui()
            self._window._statusbar.showMessage("R&D measurement rejected. Redoing...")
            QTimer.singleShot(100, self.start_measurement)
            return
        if choice in {RnDReviewDialog.KEEP_NO_CHANGE, RnDReviewDialog.KEEP_CHANGE}:
            self._keep_rnd_measurement(
                change_status="changed" if choice == RnDReviewDialog.KEEP_CHANGE else "no_change",
                notes=dlg.notes() if choice == RnDReviewDialog.KEEP_CHANGE else dlg.notes(),
            )
            return
        self._cancel_rnd_measurement()

    def _keep_rnd_measurement(self, *, change_status: str, notes: str) -> None:
        if self._window.measure.queue.pending_curve is None:
            return
        freqs, mag_db = self._window.measure.queue.pending_curve
        channel_label = (
            self._window.measure_tab.ch_combo.currentText().strip()
            or f"Channel {self._window.devices.current_input_channel() + 1}"
        )
        existing_names = {item.name for item in self._window._rnd_widget.session.measurements}
        measurement = RnDMeasurement(
            name=generate_measurement_name(
                self._window._session,
                self._window.devices.current_input_device_label(),
                channel_label,
                existing_names,
            ),
            freqs=np.array(freqs, dtype=float, copy=True),
            mag_db=np.array(mag_db, dtype=float, copy=True),
            metadata=self._window._session.to_dict(),
            rig=self._window._session.rig,
            input_device_label=self._window.devices.current_input_device_label(),
            input_channel_index=self._window.devices.current_input_channel(),
            input_channel_label=channel_label,
            output_device_label=self._window.devices.current_output_device_label(),
            notes=notes,
            change_status=change_status,
            top_visible=True,
            pinned=False,
        )
        self._window._rnd_widget.set_review_curve(None)
        self._window._rnd_widget.add_measurement(measurement)
        self._window.measure.queue.pending_curve = None
        self.sweep_active = False
        self._window.measure.queue.attempts = 0
        self._window.measure.queue.state = QueueState.IDLE
        self._window.measure_tab.sweep_progress.setValue(100)
        self._window._apply_state_ui()
        self._window.devices.start_level_monitor()
        self._window._rnd_widget.set_status("Ready")
        self._window._statusbar.showMessage(f"R&D measurement kept: {measurement.name}")
        self._window._log_event(
            "INFO", "rnd", "R&D measurement kept", name=measurement.name, status=change_status
        )
        self._window.commands.trigger("rnd_measurement_kept")

    def _cancel_rnd_measurement(self) -> None:
        self._window.measure.abort_active_sweep()
        self.close_review_dialog()
        self._window._rnd_widget.set_review_curve(None)
        self._window.measure.queue.pending_curve = None
        self.sweep_active = False
        self._window.measure.queue.attempts = 0
        self._window.measure.queue.state = QueueState.IDLE
        self._window.measure_tab.sweep_progress.setValue(0)
        self._window._apply_state_ui()
        self._window.devices.start_level_monitor()
        self._window._rnd_widget.set_status("Ready")
        self._window._statusbar.showMessage("R&D measurement canceled.")

    def close_review_dialog(self) -> None:
        if self.review_dialog is None:
            return
        dlg = self.review_dialog
        self.review_dialog = None
        dlg.blockSignals(True)
        dlg.close()

    def send_to_curator(self) -> None:
        if self._window.measure.queue.state != QueueState.IDLE:
            raise ValueError("Measurements can only be sent to Curator while idle.")

        mode = self._window.measure.bottom_view_mode()
        active_hrtf = self._window.measure.hrtf if self._window.measure.is_hrtf_active() else None
        correction = None
        curve: CurveData
        curator_metadata = self._window._session.to_dict()
        curator_metadata.update(
            {
                "hrtf_name": active_hrtf.name if active_hrtf is not None else "",
                "compensated": active_hrtf is not None,
                "measure_channel": self._window.measure.active_measure_label(),
            }
        )
        if mode == "variation":
            active_variation = self._window.measure.active_measure_variation()
            if active_variation is None:
                raise ValueError("No variation band is available to send.")
            source_variation = None
            if active_hrtf is not None and getattr(active_hrtf, "is_variation", False):
                source_variation = self._window.measure.variation_from_curves(
                    self._window.measure.active_measure_curves(),
                    self._window.measure.active_two_channel_average()
                    if self._window.measure.two_channel_enabled
                    else self._window.measure.average,
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
            displayed = self._window.measure.bottom_curve_for_display()
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

        identity = self._window._session.asset_tag.strip() or " ".join(
            part
            for part in (self._window._session.brand.strip(), self._window._session.model.strip())
            if part
        )
        if not identity:
            identity = "Fastgraph"
        comp_label = "COMP" if active_hrtf is not None else "RAW"
        channel_label = self._window.measure.active_measure_label()
        channel_part = f" {channel_label}" if channel_label else ""
        name = f"{identity}{channel_part} {comp_label} {kind_label}"
        # Make Curator visible before its reveal animation starts. Some Qt
        # platforms defer animation paints for hidden tab pages.
        self._window._tabs.setCurrentWidget(self._window._curator_widget)
        layer = self._window._curator_widget.add_curve(
            curve,
            name,
            source_path="<fastgraph>",
            hrtf=active_hrtf,
            normalize=False,
        )
        self._window._curator_widget.offset_layer_to_zero_at_1khz(layer)
        self._window._statusbar.showMessage(f"Sent to Curator: {layer.name}")
        self._window._log_event(
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

    def measure_to_rnd_unavailable_reason(self) -> str:
        if self._window.measure.queue.state != QueueState.IDLE:
            return "Measurements can only be sent to R&D while Measure is idle."
        if self._window.measure.channel_balance_mode_active():
            return "Switch to Frequency Response before sending data to R&D."
        if self._window.measure.bottom_view_mode() == "variation":
            if not self._window.measure.active_measure_curves():
                return "Keep at least one measurement before sending Var to R&D."
        elif (
            self._window.measure.active_two_channel_average()
            if self._window.measure.two_channel_enabled
            else self._window.measure.average
        ) is None:
            return "Create an average before sending it to R&D."
        return ""

    def send_measure_to_rnd(self) -> None:
        unavailable = self.measure_to_rnd_unavailable_reason()
        if unavailable:
            QMessageBox.information(self._window, "Send to R&D Unavailable", unavailable)
            return

        active_hrtf = self._window.measure.hrtf if self._window.measure.is_hrtf_active() else None
        hrtf_path = str(active_hrtf.path) if active_hrtf is not None else ""
        hrtf_name = active_hrtf.name if active_hrtf is not None else ""
        metadata = self._window._session.to_dict()
        input_label = self._window.devices.current_input_device_label()
        output_label = self._window.devices.current_output_device_label()
        active_label = self._window.measure.active_measure_label()
        input_channel_index = (
            (1 if active_label == "R" else 0)
            if self._window.measure.two_channel_enabled
            else self._window.devices.current_input_channel()
        )
        channel_label = active_label or (
            self._window.measure_tab.ch_combo.currentText().strip()
            or f"Channel {input_channel_index + 1}"
        )
        metadata["measure_channel"] = channel_label
        identity = self._window._session.asset_tag.strip() or " ".join(
            part
            for part in (self._window._session.brand.strip(), self._window._session.model.strip())
            if part
        )
        identity = identity or "Fastgraph"

        if self._window.measure.bottom_view_mode() == "variation":
            group_name = self._unique_rnd_transfer_name(
                f"{identity} VAR",
                {group.name for group in self._window._rnd_widget.session.groups},
            )
            existing_names = {
                measurement.name for measurement in self._window._rnd_widget.session.measurements
            }
            measurements: list[RnDMeasurement] = []
            for index, (freqs, mag_db) in enumerate(
                self._window.measure.active_measure_curves(), start=1
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
                        rig=self._window._session.rig,
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
            self._window._rnd_widget.add_measurement_batch(
                measurements,
                group=group,
                inherit_default_hrtf=False,
            )
            transferred_name = group_name
            transferred_count = len(measurements)
            transfer_mode = "variation"
        else:
            active_average = (
                self._window.measure.active_two_channel_average()
                if self._window.measure.two_channel_enabled
                else self._window.measure.average
            )
            assert active_average is not None
            freqs, mag_db = active_average
            name = self._unique_rnd_transfer_name(
                f"{identity} AVG",
                {measurement.name for measurement in self._window._rnd_widget.session.measurements},
            )
            measurement = RnDMeasurement(
                name=name,
                freqs=np.array(freqs, dtype=float, copy=True),
                mag_db=np.array(mag_db, dtype=float, copy=True),
                metadata=dict(metadata),
                rig=self._window._session.rig,
                input_device_label=input_label,
                input_channel_index=input_channel_index,
                input_channel_label=channel_label,
                output_device_label=output_label,
                top_visible=True,
                pinned=False,
                hrtf_path=hrtf_path,
                hrtf_name=hrtf_name,
            )
            self._window._rnd_widget.add_measurement_batch(
                [measurement],
                inherit_default_hrtf=False,
            )
            transferred_name = name
            transferred_count = 1
            transfer_mode = "average"

        self._window._tabs.setCurrentWidget(self._window._rnd_widget)
        self._window._statusbar.showMessage(
            f"Sent to R&D: {transferred_name} ({transferred_count} measurement"
            f"{'s' if transferred_count != 1 else ''})"
        )
        self._window._log_event(
            "INFO",
            "rnd",
            "Measure view sent to R&D",
            name=transferred_name,
            mode=transfer_mode,
            measurement_count=transferred_count,
            hrtf=hrtf_name or None,
        )

    def send_rnd_to_curator(self) -> None:
        if self._window.measure.queue.state != QueueState.IDLE:
            return
        measurement = self._window._rnd_widget.selected_measurement()
        group = self._window._rnd_widget.selected_group()
        if measurement is not None:
            if not self._ensure_rnd_hrtfs_available([measurement]):
                return
            freqs, mag = self._window._rnd_widget.displayed_measurement_curve(measurement)
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
                    self._window,
                    "Variation Disabled",
                    "Enable variation for the selected group before sending it to Curator.",
                )
                return
            measurements = self._window._rnd_widget.displayed_group_measurements(group)
            if not self._ensure_rnd_hrtfs_available(measurements):
                return
            variation = rnd_group_variation(
                measurements,
                smoothing_fraction=int(self._window._rnd_widget.session.smoothing_fraction or 48),
            )
            if variation is None:
                QMessageBox.information(
                    self._window,
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
                self._window, "Nothing Selected", "Select an R&D measurement or group first."
            )
            return

        self._window._tabs.setCurrentWidget(self._window._curator_widget)
        layer = self._window._curator_widget.add_curve(
            curve,
            name,
            source_path="<fastgraph-rnd>",
            hrtf=None,
            normalize=False,
        )
        self._window._curator_widget.offset_layer_to_zero_at_1khz(layer)
        self._window._statusbar.showMessage(f"Sent R&D item to Curator: {layer.name}")
        self._window._log_event(
            "INFO", "rnd", "R&D item sent to Curator", name=layer.name, kind=curve.kind
        )

    def export_selected(self) -> None:
        if self._window.measure.queue.state != QueueState.IDLE:
            return
        measurement = self._window._rnd_widget.selected_measurement()
        group = self._window._rnd_widget.selected_group()
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
            self._window, "Nothing Selected", "Select an R&D measurement or group first."
        )

    def _export_rnd_measurement(
        self, measurement: RnDMeasurement, requested_path: str | None = None
    ) -> None:
        session = SessionData.from_dict({"rig": measurement.rig, **measurement.metadata})
        hrtf_path = self._window._rnd_widget.resolve_hrtf_path(
            measurement.hrtf_path, measurement.hrtf_name
        )
        if not self._ensure_rnd_hrtfs_available([measurement]):
            return
        compensated = bool(hrtf_path)
        filename = f"{self._safe_filename(measurement.name)} {'COMP' if compensated else 'RAW'}.txt"
        path = self._window.measure_io.resolve_export_path(
            requested_path, filename, "Export R&D Measurement"
        )
        if path is None:
            return
        freqs, mag = self._window._rnd_widget.displayed_measurement_curve(measurement)
        hrtf = HRTFCurve(hrtf_path) if compensated else None
        export_curve(
            freqs=freqs,
            mag_db=mag,
            session=session,
            output_path=path,
            compensated=compensated,
            hrtf=hrtf,
            n_sweeps=1,
            smoothing_fraction=int(self._window._rnd_widget.session.smoothing_fraction or 48),
            offset_db=self._window._rnd_widget.displayed_offset_db(measurement),
        )
        self._window._settings.set("export_directory", str(path.parent))
        self._window.measure_tab.export_dir_input.setText(str(path.parent))
        self._window._statusbar.showMessage(f"Exported R&D measurement: {path}")
        self._window._log_event("INFO", "rnd", "R&D measurement exported", path=str(path))
        self._window.commands.trigger("export_complete")

    def _export_rnd_group_variation(self, group) -> None:
        measurements = self._window._rnd_widget.displayed_group_measurements(group)
        if not self._ensure_rnd_hrtfs_available(measurements):
            return
        variation = rnd_group_variation(
            measurements,
            smoothing_fraction=int(self._window._rnd_widget.session.smoothing_fraction or 48),
        )
        if variation is None:
            QMessageBox.information(
                self._window,
                "Nothing to Export",
                "Selected group needs at least two measurements for variation export.",
            )
            return
        compensated = any(
            bool(
                self._window._rnd_widget.resolve_hrtf_path(
                    measurement.hrtf_path, measurement.hrtf_name
                )
            )
            for measurement in measurements
        )
        filename = f"{self._safe_filename(group.name)} {'COMP' if compensated else 'RAW'} VAR.txt"
        path = self._window.measure_io.resolve_export_path(
            None, filename, "Export R&D Group Variation"
        )
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
            smoothing_fraction=int(self._window._rnd_widget.session.smoothing_fraction or 48),
        )
        self._window._settings.set("export_directory", str(path.parent))
        self._window.measure_tab.export_dir_input.setText(str(path.parent))
        self._window._statusbar.showMessage(f"Exported R&D variation: {path}")
        self._window._log_event("INFO", "rnd", "R&D variation exported", path=str(path))
        self._window.commands.trigger("export_complete")

    def _export_rnd_group_measurements(self, group) -> None:
        measurements = [
            measurement
            for measurement_id in group.measurement_ids
            if (measurement := self._window._rnd_widget.session.measurement_by_id(measurement_id))
            is not None
        ]
        if not measurements:
            QMessageBox.information(
                self._window, "Nothing to Export", "Selected group has no measurements."
            )
            return
        if not self._ensure_rnd_hrtfs_available(measurements):
            return
        directory = QFileDialog.getExistingDirectory(
            self._window,
            "Export R&D Group Measurements",
            str(self._window._settings.get("export_directory") or ""),
        )
        if not directory:
            return
        for measurement in measurements:
            path = Path(directory) / f"{self._safe_filename(measurement.name)}.txt"
            self._export_rnd_measurement(measurement, str(path))
        self._window._statusbar.showMessage(f"Exported {len(measurements)} R&D measurements.")

    def _ensure_rnd_hrtfs_available(self, measurements: list[RnDMeasurement]) -> bool:
        missing = []
        for measurement in measurements:
            if not (measurement.hrtf_path or measurement.hrtf_name):
                continue
            if self._window._rnd_widget.resolve_hrtf_path(
                measurement.hrtf_path, measurement.hrtf_name
            ):
                continue
            label = (
                measurement.hrtf_name or Path(measurement.hrtf_path).stem or measurement.hrtf_path
            )
            missing.append(f"{measurement.name}: {label}")
        if not missing:
            return True
        QMessageBox.warning(
            self._window,
            "Missing R&D HRTF",
            "One or more R&D measurements reference an HRTF that is not available on this machine.\n\n"
            + "\n".join(missing[:6])
            + ("\n..." if len(missing) > 6 else "")
            + "\n\nChoose an available HRTF or set the row to None before exporting or sending to Curator.",
        )
        self._window._rnd_widget.set_status("Ready - missing R&D HRTF")
        return False

    def _rnd_default_dir(self) -> Path:
        configured = str(self._window._settings.get("rnd_session_directory") or "").strip()
        if configured:
            return Path(configured).expanduser()
        documents = Path.home() / "Documents"
        return documents if documents.exists() else Path.home()

    def initialize_recovery(self) -> None:
        try:
            candidates = self._recovery.candidates()
            if candidates:
                dialog = RnDRecoveryDialog(candidates, self._window)
                dialog.exec()
                candidate = dialog.selected_candidate()
                if dialog.action == RnDRecoveryDialog.RESTORE and getattr(
                    candidate, "unsupported", False
                ):
                    # Intact, but written by a newer build. It is left in place
                    # rather than quarantined so a Fastgraph update can read it.
                    QMessageBox.warning(
                        self._window,
                        "Newer R&D Session",
                        "That recovered session was saved by a newer Fastgraph "
                        "and cannot be opened by this version. It was left in "
                        "place so a newer Fastgraph can recover it.",
                    )
                elif dialog.action == RnDRecoveryDialog.RESTORE:
                    session, missing_photos = self._recovery.restore(
                        candidate,
                        self._window._rnd_widget.photo_store,
                    )
                    self._window._rnd_widget.replace_session(session)
                    self._window._tabs.setCurrentWidget(self._window._rnd_widget)
                    self.dirty = not session.is_empty()
                    self._restored_recovery_candidate = (
                        candidate if candidate.kind == "deferred" else None
                    )
                    if missing_photos:
                        QMessageBox.warning(
                            self._window,
                            "Missing R&D Photos",
                            f"{len(missing_photos)} recovered photo attachment(s) could not be found.",
                        )
                elif dialog.action == RnDRecoveryDialog.DISCARD:
                    self._recovery.discard(candidate)
                else:
                    self._recovery.keep_for_later(candidate)
        except Exception as exc:
            self._on_rnd_recovery_failed(str(exc))
        finally:
            self._recovery.enable()
            if self.dirty:
                self._recovery.schedule()
            self._window.commands.trigger("app_start")

    def _rnd_recovery_snapshot(self):
        self._window._rnd_widget.session.saved_app_version = __version__
        return rnd_persistence_snapshot(
            self._window._rnd_widget.session,
            self._window._rnd_widget.photo_store,
        )

    def _on_rnd_state_changed(self) -> None:
        if self._window._rnd_widget.session.is_empty():
            self.dirty = False
        else:
            self.dirty = True
        self._recovery.schedule()

    def _on_rnd_selection_changed(self) -> None:
        self._recovery.schedule()

    def _on_rnd_recovery_saved(self) -> None:
        # The newest snapshot is safe either way; "degraded" means only the
        # previous generation could not be kept, and it clears itself once a
        # rotation succeeds.
        self._window._rnd_widget.set_recovery_warning(
            "R&D recovery degraded" if getattr(self._recovery, "rotation_degraded", False) else ""
        )
        if self._restored_recovery_candidate is not None:
            self._recovery.discard(self._restored_recovery_candidate)
            self._restored_recovery_candidate = None

    def _on_rnd_recovery_failed(self, error: str) -> None:
        self._window._rnd_widget.set_recovery_warning("R&D recovery save failed")
        self._window._log_event("ERROR", "rnd", "R&D recovery save failed", error=error)

    def save_session(self) -> bool:
        default_dir = self._rnd_default_dir()
        default_path = default_dir / f"fastgraph-rnd-session{RND_SESSION_EXTENSION}"
        path_str, _ = QFileDialog.getSaveFileName(
            self._window,
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
            getattr(self._window._rnd_widget.session, "source_path", ""), path
        ):
            choice = QMessageBox.question(
                self._window,
                "Replace R&D Session?",
                f"Replace {path.name}?\n\n{path.parent}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return False
        self._window._rnd_widget.session.saved_app_version = __version__
        try:
            save_rnd_session(
                self._window._rnd_widget.session,
                self._window._rnd_widget.photo_store,
                path,
            )
        except Exception as exc:
            QMessageBox.warning(
                self._window, "Save Failed", f"Could not save the R&D session.\n\n{exc}"
            )
            return False
        self._window._settings.set("rnd_session_directory", str(path.parent))
        self._window._settings_widget.refresh_from_settings()
        self._window._statusbar.showMessage(f"Saved R&D session: {path}")
        self._window._log_event("INFO", "rnd", "R&D session saved", path=str(path))
        self.dirty = False
        return True

    def load_session(self) -> None:
        default_dir = self._rnd_default_dir()
        path_str, _ = QFileDialog.getOpenFileName(
            self._window,
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
                self._window._rnd_widget.photo_store,
            )
        except Exception as exc:
            QMessageBox.warning(
                self._window, "Load Failed", f"Could not load R&D session.\n\n{exc}"
            )
            return

        mode = self._choose_rnd_load_mode()
        if mode == "cancel":
            return
        if mode == "clear":
            if not self._window._rnd_widget.session.is_empty():
                save_choice = QMessageBox.question(
                    self._window,
                    "Save Current R&D Session?",
                    "Save the current R&D session before clearing it?",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Yes,
                )
                if save_choice == QMessageBox.StandardButton.Cancel:
                    return
                if save_choice == QMessageBox.StandardButton.Yes and not self.save_session():
                    return
            self._window._rnd_widget.replace_session(incoming)
            self.dirty = False
        else:
            self._window._rnd_widget.merge_session(incoming)
            self.dirty = not self._window._rnd_widget.session.is_empty()
        self._window._settings.set("rnd_session_directory", str(Path(path_str).parent))
        self._window._settings_widget.refresh_from_settings()
        self._window._statusbar.showMessage(f"Loaded R&D session: {path_str}")
        self._window._log_event("INFO", "rnd", "R&D session loaded", path=path_str, mode=mode)
        if missing_photos:
            QMessageBox.warning(
                self._window,
                "Missing R&D Photos",
                f"{len(missing_photos)} photo attachment(s) could not be found beside this session. "
                "They will remain listed as unavailable.",
            )

    def _choose_rnd_load_mode(self) -> str:
        dialog = QMessageBox(self._window)
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

    def confirm_close(self) -> bool:
        if self._window._rnd_widget.session.is_empty() or not self.dirty:
            return True
        dialog = QMessageBox(self._window)
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
            return self.save_session()
        return result == QMessageBox.StandardButton.Discard
