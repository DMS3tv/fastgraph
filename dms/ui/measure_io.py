"""
Measure exports and Measure session files.

``MeasureIO`` owns the Session ▾ menu, the session file the Measure workspace
belongs to and its unsaved-changes flag, the workspace's crash recovery, and
Export Average / Export Variation / Export All with the export directory they
share.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QFileDialog, QMenu, QMessageBox, QToolButton

from dms.export import (
    build_filename,
    build_variation_filename,
    export_curve,
    export_variation,
)
from dms.file_io import ensure_extension, same_session_file
from dms.measure_persistence import (
    MEASURE_SESSION_EXTENSION,
    MeasureSessionLoadError,
    load_measure_session,
    save_measure_session,
)
from dms.measure_queue import QueueState
from dms.measure_session import MeasureSession, UnsupportedMeasureSessionVersion
from dms.processing import smooth_fractional_octave
from dms.recovery import RecoveryCandidate, measure_recovery_manager
from dms.settings_manager import config_dir
from dms.ui.measure_dialogs import MeasureRecoveryDialog

if TYPE_CHECKING:
    from dms.ui.main_window import MainWindow


class MeasureIO(QObject):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        # Measure session file the workspace currently belongs to, and whether
        # it holds changes that file does not.
        self.session_path: Path | None = None
        self.dirty = False
        self._restored_measure_candidate: RecoveryCandidate | None = None
        # The manager appends its own ``measure/`` segment, so both workspaces
        # share one recovery root without colliding.
        self._measure_recovery = measure_recovery_manager(
            config_dir() / "recovery",
            parent=window,
        )
        self._measure_recovery.save_failed.connect(self._on_measure_recovery_failed)

    def build_session_menu(self) -> QToolButton:
        """The Session ▾ button with its menu; the tab builder places it."""
        button = QToolButton()
        button.setText("Session ▾")
        button.setProperty("menuButton", True)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setToolTip("Save, reopen or start over on a Measure session file.")
        self._session_menu = QMenu(button)
        self._new_session_action = self._session_menu.addAction("New Session")
        self._new_session_action.triggered.connect(self.new_session)
        self._save_session_action = self._session_menu.addAction("Save Session")
        self._save_session_action.triggered.connect(lambda: self.save_session())
        self._save_session_as_action = self._session_menu.addAction("Save Session As…")
        self._save_session_as_action.triggered.connect(lambda: self.save_session(save_as=True))
        self._load_session_action = self._session_menu.addAction("Load Session…")
        self._load_session_action.triggered.connect(lambda: self.load_session())
        button.setMenu(self._session_menu)
        return button

    def shutdown(self) -> None:
        """Stop recovery saves and leave the clean-exit marker."""
        try:
            self._measure_recovery.shutdown_clean()
        except Exception as exc:
            self._window._log_event(
                "ERROR", "measure", "Measure recovery cleanup failed", error=str(exc)
            )

    # ------------------------------------------------------------------
    # Measure sessions
    # ------------------------------------------------------------------

    def measure_default_dir(self) -> Path:
        configured = str(self._window._settings.get("measure_session_directory") or "").strip()
        if configured:
            return Path(configured).expanduser()
        documents = Path.home() / "Documents"
        return documents if documents.exists() else Path.home()

    def current_session(self) -> MeasureSession:
        """The Measure workspace's live state as a serializable session."""
        window = self._window
        hrtf_path = window._hrtf.path if window._hrtf is not None else None
        return MeasureSession.from_window_state(
            session_data=window._session,
            kept_curves=window._kept_curves,
            pairs=window._two_channel_pairs,
            two_channel=window._two_channel_enabled,
            bottom_mode=window._two_channel_bottom_mode,
            level_mode=window._level_mode(),
            hrtf_path=hrtf_path,
            hrtf_name=Path(hrtf_path).stem if hrtf_path else None,
            hrtf_enabled=window._is_hrtf_active(),
            sweep_diagnostics=[meta.get("diagnostics") for meta in window._kept_sweep_meta],
            sweep_timing_quality=[meta.get("timing_quality") for meta in window._kept_sweep_meta],
            sweep_distortion=[meta.get("distortion") for meta in window._kept_sweep_meta],
            source_path=(str(self.session_path) if self.session_path is not None else None),
        )

    def mark_dirty(self) -> None:
        """Record an unsaved change and queue a crash-recovery snapshot."""
        self.dirty = True
        self._window._refresh_window_title()
        recovery = getattr(self, "_measure_recovery", None)
        if recovery is None:
            return
        session = self.current_session()
        if session.is_empty():
            recovery.clear_active()
            return
        recovery.schedule(session.to_dict())

    def clear_dirty(self) -> None:
        """The workspace now matches a file, so the recovery copy is redundant."""
        self.dirty = False
        recovery = getattr(self, "_measure_recovery", None)
        if recovery is not None:
            recovery.clear_active()
        self._window._refresh_window_title()

    def new_session(self) -> None:
        window = self._window
        if window._state != QueueState.IDLE:
            QMessageBox.information(
                window,
                "Busy",
                "A new Measure session can only be started while idle.",
            )
            return
        if not self._confirm_discard_measure_session():
            return
        # The save-or-discard prompt above already covered the question
        # ``_clear_all`` would ask, so the discard runs unprompted here; with
        # nothing kept there is nothing to discard at all.
        if window._has_kept_measurements():
            window._discard_all_measurements()
        window._kept_sweep_meta.clear()
        window._kept_pair_meta.clear()
        self.session_path = None
        self.clear_dirty()
        window._statusbar.showMessage("New Measure session.")
        window._log_event("INFO", "measure", "New Measure session started")

    def save_session(self, *, save_as: bool = False) -> bool:
        window = self._window
        path = self.session_path
        if save_as or path is None:
            default_path = path or (
                self.measure_default_dir() / f"fastgraph-measure-session{MEASURE_SESSION_EXTENSION}"
            )
            path_str, _ = QFileDialog.getSaveFileName(
                window,
                "Save Measure Session",
                str(default_path),
                f"Fastgraph Measure Session (*{MEASURE_SESSION_EXTENSION});;"
                "JSON Files (*.json);;All Files (*)",
            )
            if not path_str:
                return False
            path = ensure_extension(Path(path_str), MEASURE_SESSION_EXTENSION)
            # The dialog checked the name the user typed; the canonical
            # extension is added afterwards, so "demo" can still land on an
            # existing "demo.fastgraph-measure.json" without a warning.
            if path.exists() and not same_session_file(self.session_path, path):
                choice = QMessageBox.question(
                    window,
                    "Replace Measure Session?",
                    f"Replace {path.name}?\n\n{path.parent}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if choice != QMessageBox.StandardButton.Yes:
                    return False
        try:
            written = save_measure_session(self.current_session(), path)
        except Exception as exc:
            QMessageBox.warning(
                window,
                "Save Failed",
                f"Could not save the Measure session.\n\n{exc}",
            )
            return False
        self.session_path = written
        window._settings.set("measure_session_directory", str(written.parent))
        window._settings_widget.refresh_from_settings()
        self.clear_dirty()
        window._statusbar.showMessage(f"Saved Measure session: {written}")
        window._log_event("INFO", "measure", "Measure session saved", path=str(written))
        return True

    def load_session(self, requested_path: str | None = None) -> bool:
        window = self._window
        if window._state != QueueState.IDLE:
            QMessageBox.information(
                window,
                "Busy",
                "Measure sessions can only be loaded while idle.",
            )
            return False
        if not self._confirm_discard_measure_session():
            return False
        path_str = requested_path
        if path_str is None:
            path_str, _ = QFileDialog.getOpenFileName(
                window,
                "Load Measure Session",
                str(self.measure_default_dir()),
                "Fastgraph Measure Session (*.fastgraph-measure.json *.json);;All Files (*)",
            )
            if not path_str:
                return False
        try:
            session = load_measure_session(Path(path_str))
        except UnsupportedMeasureSessionVersion as exc:
            QMessageBox.warning(window, "Newer Measure Session", str(exc))
            return False
        except MeasureSessionLoadError as exc:
            QMessageBox.warning(
                window,
                "Load Failed",
                f"Could not load the Measure session.\n\n{exc}",
            )
            return False
        except Exception as exc:
            QMessageBox.warning(
                window,
                "Load Failed",
                f"Could not load the Measure session.\n\n{exc}",
            )
            return False

        self._apply_measure_session(session)
        self.session_path = Path(path_str)
        window._settings.set("measure_session_directory", str(Path(path_str).parent))
        window._settings_widget.refresh_from_settings()
        self.clear_dirty()
        window._statusbar.showMessage(f"Loaded Measure session: {path_str}")
        window._log_event("INFO", "measure", "Measure session loaded", path=str(path_str))
        return True

    def _apply_measure_session(self, session: MeasureSession) -> None:
        """Replace the Measure workspace with a loaded session's state."""
        window = self._window
        window._queue.reset()
        window._kept_distortion = None
        window._pending_curve = None
        window._pending_pair = None
        window._pending_pair_first_raw = None
        window._pending_pair_first_diagnostics = None
        window._two_channel_stage = 0
        window._queue_index = 0

        window._kept_curves = [sweep.curve for sweep in session.sweeps]
        window._kept_sweep_meta = [
            {
                "diagnostics": sweep.diagnostics,
                "timing_quality": sweep.timing_quality,
                "distortion": sweep.distortion_summary,
            }
            for sweep in session.sweeps
        ]
        window._two_channel_pairs = session.pair_objects()
        window._kept_pair_meta = [{} for _ in window._two_channel_pairs]
        window._average = None
        window._variation = None
        window._two_channel_averages = {}
        window._two_channel_variations = {}

        window._session = session.metadata
        window._refresh_session_labels()
        window._metadata_editor.set_session(window._session)

        if bool(session.two_channel) != bool(window._two_channel_enabled):
            window.measure_tab.two_channel_toggle.setChecked(bool(session.two_channel))

        index = window.measure_tab.bottom_layout_combo.findData(session.bottom_mode)
        if index >= 0 and index != window.measure_tab.bottom_layout_combo.currentIndex():
            window.measure_tab.bottom_layout_combo.setCurrentIndex(index)

        self._apply_session_level_mode(session.level_mode)
        self._apply_session_hrtf(session)

        window._recompute_average()
        window._recompute_variation()
        window._recompute_two_channel_results()
        window._update_queue_progress()
        window._update_plots()
        window._apply_state_ui()
        window._refresh_window_title()

    def _apply_session_level_mode(self, level_mode: str) -> None:
        window = self._window
        wanted = "dbspl" if str(level_mode) == "dbspl" else "ref_1khz"
        if wanted == window._level_mode():
            return
        if wanted == "dbspl" and window.devices.calibrated_sensitivity() is None:
            QMessageBox.warning(
                window,
                "Not Calibrated",
                "This session was saved in dB SPL, but the selected input "
                "device has no calibration. Levels stay at the 1 kHz "
                "reference.",
            )
            return
        window._settings.set("measure_level_mode", wanted)
        window._spl_uncalibrated_warned = False
        window._sync_level_mode_combo()

    def _apply_session_hrtf(self, session: MeasureSession) -> None:
        window = self._window
        if not session.hrtf_path and not session.hrtf_name:
            return
        index = -1
        if session.hrtf_path:
            index = window.measure_tab.hrtf_combo.findData(session.hrtf_path)
        if index < 0 and session.hrtf_name:
            index = window.measure_tab.hrtf_combo.findText(session.hrtf_name)
        if index < 0:
            QMessageBox.warning(
                window,
                "Missing HRTF",
                f"The HRTF this session used ({session.hrtf_name or session.hrtf_path}) "
                "is not installed. It was left unset.",
            )
            return
        window.measure_tab.hrtf_combo.blockSignals(True)
        window.measure_tab.hrtf_combo.setCurrentIndex(index)
        window.measure_tab.hrtf_combo.blockSignals(False)
        window._on_hrtf_selected()
        window.measure_tab.hrtf_toggle.setChecked(bool(session.hrtf_enabled))

    def _confirm_discard_measure_session(self) -> bool:
        """Offer to save before something replaces the Measure workspace."""
        if not self.dirty:
            return True
        dialog = QMessageBox(self._window)
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
            if self.session_path is not None
            else QMessageBox.StandardButton.Cancel
        )
        result = dialog.exec()
        if result == QMessageBox.StandardButton.Save:
            return self.save_session()
        return result == QMessageBox.StandardButton.Discard

    def confirm_close(self) -> bool:
        """Offer to save the Measure session before Fastgraph closes.

        A workspace with no unsaved changes closes silently: everything on
        screen is already in a file, so there is nothing to lose.
        """
        window = self._window
        if not self.dirty:
            return True
        if not bool(window._settings.get("confirm_discard_measurements")):
            return True
        kept = (
            len(window._two_channel_pairs)
            if window._two_channel_enabled
            else len(window._kept_curves)
        )
        dialog = QMessageBox(window)
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
            if self.session_path is not None
            else QMessageBox.StandardButton.Cancel
        )
        result = dialog.exec()
        if result == QMessageBox.StandardButton.Save:
            return self.save_session()
        return result == QMessageBox.StandardButton.Discard

    def initialize_recovery(self) -> None:
        window = self._window
        try:
            candidates = self._measure_recovery.candidates()
            if candidates:
                dialog = MeasureRecoveryDialog(candidates, window)
                dialog.exec()
                candidate = dialog.selected_candidate()
                if dialog.action == MeasureRecoveryDialog.RESTORE and getattr(
                    candidate, "unsupported", False
                ):
                    # Intact, but written by a newer build. It is left in place
                    # rather than quarantined so an update can read it.
                    QMessageBox.warning(
                        window,
                        "Newer Measure Session",
                        "That recovered session was saved by a newer Fastgraph "
                        "and cannot be opened by this version. It was left in "
                        "place so a newer Fastgraph can recover it.",
                    )
                elif dialog.action == MeasureRecoveryDialog.RESTORE:
                    session = self._measure_recovery.restore(candidate)
                    self._apply_measure_session(session)
                    self.session_path = None
                    window._tabs.setCurrentWidget(window.measure_tab)
                    self.dirty = not session.is_empty()
                    window._refresh_window_title()
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
            if self.dirty:
                self.mark_dirty()
            if self._restored_measure_candidate is not None:
                self._measure_recovery.discard(self._restored_measure_candidate)
                self._restored_measure_candidate = None

    def _on_measure_recovery_failed(self, error: str) -> None:
        self._window._statusbar.showMessage("Measure recovery save failed.")
        self._window._log_event("ERROR", "measure", "Measure recovery save failed", error=error)

    # ------------------------------------------------------------------
    # Exports
    # ------------------------------------------------------------------

    def choose_export_directory(self) -> None:
        window = self._window
        current = window.measure_tab.export_dir_input.text().strip()
        chosen = QFileDialog.getExistingDirectory(
            window,
            "Choose Export Directory",
            current or "",
        )
        if not chosen:
            return
        window.measure_tab.export_dir_input.setText(chosen)
        window._settings.set("export_directory", chosen)

    def resolve_export_path(
        self,
        requested_path: str | None,
        filename: str,
        title: str,
        file_filter: str = "Text Files (*.txt);;All Files (*)",
    ) -> Path | None:
        window = self._window
        if requested_path:
            path = Path(requested_path).expanduser()
            if path.exists() and path.is_dir():
                path = path / filename
            if not path.parent.exists():
                raise ValueError(f"Export directory does not exist: {path.parent}")
            if path.exists():
                choice = QMessageBox.question(
                    window,
                    "Confirm Overwrite",
                    f"Overwrite existing file?\n\n{path}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if choice != QMessageBox.StandardButton.Yes:
                    return None
            return path
        default_dir = window.measure_tab.export_dir_input.text().strip() or str(
            window._settings.get("export_directory") or ""
        )
        default_path = str(Path(default_dir) / filename) if default_dir else filename
        path_str, _ = QFileDialog.getSaveFileName(window, title, default_path, file_filter)
        return Path(path_str) if path_str else None

    def export(self) -> None:
        if self._window._bottom_view_mode() == "variation":
            self.export_variation()
            return
        self.export_average()

    def export_average(self, requested_path: str | None = None) -> None:
        from dms.ui.main_window import _DISPLAY_AVG_SMOOTHING

        window = self._window
        # Export what is displayed: the same smoothed curve the bottom
        # viewport draws, with the smoothing recorded in the header.
        curve = window._bottom_curve_for_display()
        if curve is None:
            QMessageBox.information(window, "Nothing to Export", "No averaged curve available yet.")
            return

        compensated = window._is_hrtf_active()
        two_channel = window._two_channel_enabled
        channel_label = window._active_measure_label() if two_channel else ""
        export_session = window._active_measure_session() if two_channel else window._session
        active_count = window._active_measure_count() if two_channel else len(window._kept_curves)
        filename = build_filename(
            window._session,
            compensated=compensated,
            channel_label=channel_label,
        )
        path = self.resolve_export_path(requested_path, filename, "Export Average")
        if path is None:
            return
        export_dir = str(path.parent)
        window.measure_tab.export_dir_input.setText(export_dir)
        window._settings.set("export_directory", export_dir)

        freqs, mag_db = curve
        try:
            export_curve(
                freqs=freqs,
                mag_db=mag_db,
                session=export_session,
                output_path=path,
                compensated=compensated,
                hrtf=window._hrtf if compensated else None,
                n_sweeps=active_count,
                smoothing_fraction=_DISPLAY_AVG_SMOOTHING,
                level_mode=window._level_mode()
                if window._spl_offset_db() is not None
                else "ref_1khz",
            )
            window._statusbar.showMessage(f"Exported average: {path}")
            window._log_event(
                "INFO", "export", "Average exported", path=str(path), compensated=compensated
            )
            window._run_automation_trigger("export_complete")
        except Exception as exc:
            window._log_event("ERROR", "export", f"Average export failed: {exc}")
            QMessageBox.warning(window, "Export Error", str(exc))

    def export_variation(self, requested_path: str | None = None) -> None:
        from dms.ui.main_window import _DISPLAY_AVG_SMOOTHING

        window = self._window
        two_channel = window._two_channel_enabled
        active_variation = window._active_measure_variation() if two_channel else window._variation
        if active_variation is None:
            QMessageBox.information(window, "Nothing to Export", "No variation band available yet.")
            return

        compensated = window._is_hrtf_active()
        channel_label = window._active_measure_label() if two_channel else ""
        export_session = window._active_measure_session() if two_channel else window._session
        active_count = window._active_measure_count() if two_channel else len(window._kept_curves)
        filename = build_variation_filename(
            window._session,
            compensated=compensated,
            channel_label=channel_label,
        )
        path = self.resolve_export_path(requested_path, filename, "Export Variation")
        if path is None:
            return
        export_dir = str(path.parent)
        window.measure_tab.export_dir_input.setText(export_dir)
        window._settings.set("export_directory", export_dir)

        try:
            export_variation(
                freqs=active_variation.freqs,
                p10_db=active_variation.p10,
                p25_db=active_variation.p25,
                median_db=active_variation.median,
                p75_db=active_variation.p75,
                p90_db=active_variation.p90,
                session=export_session,
                output_path=path,
                compensated=compensated,
                hrtf=window._hrtf if compensated else None,
                n_sweeps=active_count,
                smoothing_fraction=_DISPLAY_AVG_SMOOTHING,
                level_mode=window._level_mode()
                if window._spl_offset_db() is not None
                else "ref_1khz",
            )
            window._statusbar.showMessage(f"Exported variation: {path}")
            window._log_event(
                "INFO", "export", "Variation exported", path=str(path), compensated=compensated
            )
            window._run_automation_trigger("export_complete")
        except Exception as exc:
            window._log_event("ERROR", "export", f"Variation export failed: {exc}")
            QMessageBox.warning(window, "Export Error", str(exc))

    def run_upload_action(self) -> None:
        if self._brand_mode_active():
            self.export_all()
            return
        self._window.squiglink.upload()

    def _brand_mode_active(self) -> bool:
        return bool(self._window._theme_controller.brand_mode)

    def _export_all_unavailable_reason(self) -> str:
        window = self._window
        if window._state != QueueState.IDLE:
            return "Export All is available while Measure is idle."
        active_average = (
            window._active_two_channel_average() if window._two_channel_enabled else window._average
        )
        if active_average is None:
            return "Keep at least one measurement to create the average."
        active_count = (
            window._active_measure_count()
            if window._two_channel_enabled
            else len(window._kept_curves)
        )
        if active_count < 2:
            return "Keep at least two measurements to create variation files."
        if window._hrtf is None:
            return "Select an HRTF to create the COMP files."
        return ""

    def _measure_export_directory(self) -> Path | None:
        window = self._window
        configured = window.measure_tab.export_dir_input.text().strip()
        if configured:
            path = Path(configured).expanduser()
            if path.is_dir():
                return path
        saved = str(window._settings.get("export_directory") or "").strip()
        start = Path(saved).expanduser() if saved else Path.home()
        if not start.is_dir():
            start = start.parent if start.parent.is_dir() else Path.home()
        selected = QFileDialog.getExistingDirectory(
            window,
            "Choose Export All Directory",
            str(start),
        )
        if not selected:
            return None
        directory = Path(selected)
        window.measure_tab.export_dir_input.setText(str(directory))
        window._settings.set("export_directory", str(directory))
        return directory

    def _confirm_export_all_overwrite(self, conflicts: list[Path]) -> bool:
        if not conflicts:
            return True
        dialog = QMessageBox(self._window)
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

    def export_all(self) -> None:
        from dms.ui.main_window import _DISPLAY_AVG_SMOOTHING

        window = self._window
        reason = self._export_all_unavailable_reason()
        if reason:
            QMessageBox.information(window, "Export All Unavailable", reason)
            return
        directory = self._measure_export_directory()
        if directory is None:
            return

        hrtf = window._hrtf
        raw_average = window._average_curve_with_hrtf(None)
        comp_average = window._average_curve_with_hrtf(hrtf)
        active_average_raw = (
            window._active_two_channel_average() if window._two_channel_enabled else window._average
        )
        active_curves = (
            window._active_measure_curves()
            if window._two_channel_enabled
            else list(window._kept_curves)
        )
        if window._two_channel_enabled:
            raw_variation = window._variation_from_curves(
                active_curves, active_average_raw, hrtf=None
            )
            comp_variation = window._variation_from_curves(
                active_curves, active_average_raw, hrtf=hrtf
            )
        else:
            raw_variation = window._variation_from_kept_curves(hrtf=None)
            comp_variation = window._variation_from_kept_curves(hrtf=hrtf)
        if (
            hrtf is None
            or raw_average is None
            or comp_average is None
            or raw_variation is None
            or comp_variation is None
        ):
            QMessageBox.warning(
                window,
                "Export All Failed",
                "Fastgraph could not prepare all four Measure exports.",
            )
            return

        channel_label = window._active_measure_label() if window._two_channel_enabled else ""
        active_count = (
            window._active_measure_count()
            if window._two_channel_enabled
            else len(window._kept_curves)
        )
        export_session = (
            window._active_measure_session() if window._two_channel_enabled else window._session
        )
        filenames = [
            build_filename(window._session, compensated=False, channel_label=channel_label),
            build_filename(window._session, compensated=True, channel_label=channel_label),
            build_variation_filename(
                window._session, compensated=False, channel_label=channel_label
            ),
            build_variation_filename(
                window._session, compensated=True, channel_label=channel_label
            ),
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
                level_mode = (
                    window._level_mode() if window._spl_offset_db() is not None else "ref_1khz"
                )
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
                    export_variation(
                        freqs=variation.freqs,
                        p10_db=variation.p10,
                        p25_db=variation.p25,
                        median_db=variation.median,
                        p75_db=variation.p75,
                        p90_db=variation.p90,
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
            window._log_event("ERROR", "export", "Export All failed", error=str(exc))
            QMessageBox.warning(window, "Export All Failed", str(exc))
            return

        window.measure_tab.export_dir_input.setText(str(directory))
        window._settings.set("export_directory", str(directory))
        window._statusbar.showMessage(f"Exported all Measure files: {directory}")
        window._log_event(
            "INFO",
            "export",
            "Export All completed",
            directory=str(directory),
            files=filenames,
        )
        window._run_automation_trigger("export_complete")
        QMessageBox.information(
            window,
            "Export All Complete",
            f"Exported to:\n{directory}\n\n" + "\n".join(filenames),
        )

    def sync_export_button(self) -> None:
        window = self._window
        idle = window._state == QueueState.IDLE
        two_channel = window._two_channel_enabled
        frequency_mode = not window._channel_balance_mode_active()
        active_average = window._active_two_channel_average() if two_channel else window._average
        active_variation = window._active_measure_variation() if two_channel else window._variation
        if window._bottom_view_mode() == "variation":
            window.measure_tab.export_btn.setText("Export Variation…")
            window.measure_tab.export_btn.setToolTip(
                "Export the displayed variation band as percentile columns in a tab-delimited TXT file."
            )
            export_enabled = idle and frequency_mode and active_variation is not None
        else:
            window.measure_tab.export_btn.setText("Export Average…")
            window.measure_tab.export_btn.setToolTip("Export averaged FR as a REW-style TXT file.")
            export_enabled = idle and frequency_mode and active_average is not None
        window.measure_tab.export_btn.setEnabled(export_enabled)
        window.measure_tab.send_to_curator_btn.setEnabled(export_enabled)
        unavailable = window._measure_to_rnd_unavailable_reason()
        window.measure_tab.send_to_rnd_btn.setEnabled(not unavailable)
        window.measure_tab.send_to_rnd_btn.setToolTip(
            unavailable or "Send the current average or all kept Var measurements to R&D."
        )
        if self._brand_mode_active():
            window.measure_tab.upload_btn.setText("Export All…")
            window.measure_tab.upload_btn.setObjectName("btn_export")
            window.measure_tab.upload_btn.setRole("primary")
            unavailable = self._export_all_unavailable_reason()
            window.measure_tab.upload_btn.setEnabled(not unavailable)
            window.measure_tab.upload_btn.setToolTip(
                unavailable or "Export RAW AVG, COMP AVG, RAW VAR, and COMP VAR to one directory."
            )
        else:
            window.measure_tab.upload_btn.setText("Upload to Squiglink")
            window.measure_tab.upload_btn.setObjectName("btn_upload")
            window.measure_tab.upload_btn.setRole("positive")
            window.measure_tab.upload_btn.setEnabled(
                idle and frequency_mode and active_average is not None
            )
            window.measure_tab.upload_btn.setToolTip("Upload the current average to Squiglink.")
        window.measure_tab.undo_btn.setEnabled(idle and window._active_measure_count() > 0)
        window.measure_tab.clear_btn.setEnabled(
            idle
            and (
                bool(window._two_channel_pairs) or window._pending_pair is not None
                if window._two_channel_enabled
                else bool(window._kept_curves) or window._pending_curve is not None
            )
        )
