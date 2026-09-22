"""
Target and reference comparison on the Measure tab.

``MeasureCompare`` owns the Compare ▾ menu, the loaded target curve, the A/B
reference layers and delta view, and produces the deviation summary for the
review dialog, the status-bar "Match" text and the EQ suggestion.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QFileDialog, QMenu, QMessageBox, QToolButton

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
from dms.theme import theme_trace_palette
from dms.ui.eq_suggestion_dialog import EqSuggestionDialog

if TYPE_CHECKING:
    from dms.ui.main_window import MainWindow

#: A/B reference layers held alongside the measurement. Three is as many as
#: the bottom viewport can carry before the average stops being the subject.
_MAX_REFERENCE_LAYERS = 3


class MeasureCompare(QObject):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        # Target comparison. The target survives restarts through settings;
        # reference layers are deliberately session-only.
        self._measure_target: tuple[np.ndarray, np.ndarray] | None = None
        self._measure_target_path: Path | None = None
        self._measure_reference_layers: list[ReferenceLayer] = []

    def build_menu(self) -> QToolButton:
        """The Compare ▾ button with its menu; the tab builder places it."""
        button = QToolButton()
        button.setText("Compare ▾")
        button.setProperty("menuButton", True)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setToolTip("Compare the average against a target curve or other measurements.")
        self._compare_menu = QMenu(button)
        self._load_target_action = self._compare_menu.addAction("Load Target…")
        self._load_target_action.triggered.connect(lambda: self.load_target())
        self._clear_target_action = self._compare_menu.addAction("Clear Target")
        self._clear_target_action.triggered.connect(self.clear_target)
        self._delta_view_action = self._compare_menu.addAction("Delta View (measurement − target)")
        self._delta_view_action.setCheckable(True)
        self._delta_view_action.setChecked(bool(self._window._settings.get("measure_delta_view")))
        self._delta_view_action.toggled.connect(self._on_delta_view_toggled)
        self._compare_menu.addSeparator()
        self._load_reference_action = self._compare_menu.addAction("Load Reference…")
        self._load_reference_action.triggered.connect(lambda: self.load_reference())
        self._clear_references_action = self._compare_menu.addAction("Clear References")
        self._clear_references_action.triggered.connect(self.clear_references)
        self._compare_menu.addSeparator()
        self._eq_suggestion_action = self._compare_menu.addAction("EQ Suggestion…")
        self._eq_suggestion_action.triggered.connect(self.open_eq_suggestion)
        button.setMenu(self._compare_menu)
        return button

    def delta_view_enabled(self) -> bool:
        action = getattr(self, "_delta_view_action", None)
        return action is not None and action.isChecked() and self._measure_target is not None

    def delta_offset_mode(self) -> str:
        mode = str(self._window._settings.get("measure_delta_offset_mode") or "1khz")
        return mode if mode in OFFSET_MODES else "1khz"

    def restore(self) -> None:
        """Reload the remembered target, dropping it if the file has gone."""
        stored = str(self._window._settings.get("measure_target_path") or "").strip()
        if stored:
            path = Path(stored).expanduser()
            if path.is_file():
                try:
                    freqs, mag_db, _warnings = load_target_curve(path)
                except Exception:
                    self._window._settings.set("measure_target_path", "")
                else:
                    self._measure_target = (freqs, mag_db)
                    self._measure_target_path = path
            else:
                self._window._settings.set("measure_target_path", "")
        self.sync_layers()

    def load_target(self, requested_path: str | None = None) -> bool:
        path_str = requested_path
        if path_str is None:
            path_str, _ = QFileDialog.getOpenFileName(
                self._window,
                "Load Target Curve",
                str(self._window.measure_io.measure_default_dir()),
                "Measurement TXT (*.txt);;All Files (*)",
            )
            if not path_str:
                return False
        try:
            freqs, mag_db, warnings = load_target_curve(Path(path_str))
        except Exception as exc:
            QMessageBox.warning(
                self._window,
                "Target Load Failed",
                f"Could not read the target curve.\n\n{exc}",
            )
            return False
        self._measure_target = (freqs, mag_db)
        self._measure_target_path = Path(path_str)
        self._window._settings.set("measure_target_path", str(path_str))
        for warning in warnings[:4]:
            self._window._log_event("WARNING", "measure", warning)
        self.sync_layers()
        self._window.measure.refresh()
        self._window._statusbar.showMessage(f"Target loaded: {Path(path_str).name}")
        self._window._log_event("INFO", "measure", "Target loaded", path=str(path_str))
        return True

    def clear_target(self) -> None:
        self._measure_target = None
        self._measure_target_path = None
        self._window._settings.set("measure_target_path", "")
        if getattr(self, "_delta_view_action", None) is not None:
            self._delta_view_action.setChecked(False)
        self.sync_layers()
        self._window.measure.refresh()
        self._window._statusbar.showMessage("Target cleared.")

    def _on_delta_view_toggled(self, checked: bool) -> None:
        if checked and self._measure_target is None:
            self._delta_view_action.setChecked(False)
            QMessageBox.information(
                self._window,
                "No Target",
                "Load a target curve before switching to delta view.",
            )
            return
        self._window._settings.set("measure_delta_view", bool(checked))
        self.sync_layers()
        self._window.measure.refresh()
        self._window._statusbar.showMessage("Delta view on." if checked else "Delta view off.")

    def load_reference(self, requested_path: str | None = None) -> bool:
        if len(self._measure_reference_layers) >= _MAX_REFERENCE_LAYERS:
            QMessageBox.information(
                self._window,
                "Reference Limit",
                f"At most {_MAX_REFERENCE_LAYERS} reference layers can be shown. "
                "Clear them before loading another.",
            )
            return False
        path_str = requested_path
        if path_str is None:
            path_str, _ = QFileDialog.getOpenFileName(
                self._window,
                "Load Reference Curve",
                str(self._window.measure_io.measure_default_dir()),
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
                self._window,
                "Reference Load Failed",
                f"Could not read the reference curve.\n\n{exc}",
            )
            return False
        self._measure_reference_layers.append(layer)
        self.sync_layers()
        self._window._statusbar.showMessage(f"Reference added: {layer.name}")
        self._window._log_event("INFO", "measure", "Reference layer added", path=str(path))
        return True

    def clear_references(self) -> None:
        self._measure_reference_layers.clear()
        self.sync_layers()
        self._window._statusbar.showMessage("Reference layers cleared.")

    def _reference_colors(self) -> list[str]:
        """Trace colours for reference layers, never the average's own colour."""
        palette = theme_trace_palette(
            self._window._theme_controller.theme,
            brand_mode=self._window._theme_controller.brand_mode,
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
            can_fit = (
                has_target
                and self._window.measure.bottom_curve_for_display_and_export() is not None
            )
            self._eq_suggestion_action.setEnabled(bool(can_fit))
            self._eq_suggestion_action.setToolTip(
                "" if can_fit else "Needs a loaded target and at least one kept measurement."
            )

    def sync_layers(self) -> None:
        """Push the target, the reference layers and delta view to the plots."""
        plots = getattr(self._window, "_plots", None)
        if plots is None:
            return
        self._sync_compare_actions()
        single = plots.single
        delta_on = self.delta_view_enabled()
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

    def delta_result(
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
                offset_mode=self.delta_offset_mode(),
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._window._log_event("ERROR", "measure", "Delta computation failed", error=str(exc))
            return None

    def pending_deviation_summary(self) -> str | None:
        """Band-by-band deviation of the sweep awaiting review, if any."""
        if self._measure_target is None or self._window.measure.two_channel_enabled:
            return None
        delta = self.delta_result(self._window.measure.queue.pending_curve)
        if delta is None:
            return None
        return format_deviation_summary(deviation_score(delta))

    def target_match_message(self) -> str | None:
        delta = self.delta_result(self._window.measure.bottom_curve_for_display_and_export())
        if delta is None:
            return None
        return f"Match: {deviation_score(delta).match_percent:.0f} %"

    def open_eq_suggestion(self) -> None:
        average = self._window.measure.bottom_curve_for_display_and_export()
        if self._measure_target is None:
            self._window._statusbar.showMessage(
                "Load a target curve before asking for an EQ suggestion."
            )
            return
        if average is None:
            self._window._statusbar.showMessage(
                "No averaged measurement is available to fit an EQ against."
            )
            return
        dialog = EqSuggestionDialog(
            average,
            self._measure_target,
            offset_mode=self.delta_offset_mode(),
            parent=self._window,
        )
        dialog.exec()
        self._window._settings.set("measure_delta_offset_mode", dialog.offset_mode())
        dialog.deleteLater()
        self._window.measure.refresh()
