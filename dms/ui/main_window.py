"""
Main application window.
Orchestrates: device selectors, level meter, dual plot, queue control,
pass/fail UI, HRTF selector, settings/calibration, and export.
"""

import contextlib
import os

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    QUrl,
)
from PyQt6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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

from dms.calibration import CalibrationStore
from dms.channel_balance import frequency_limit
from dms.console import ConsoleEventStore, runtime_diagnostics
from dms.measure_persistence import (
    MEASURE_SESSION_EXTENSION,
)
from dms.measure_queue import QueueState
from dms.session import SessionData
from dms.settings_manager import SettingsManager, config_dir
from dms.shortcuts import SHORTCUT_ACTIONS, shortcut_bindings_from_settings
from dms.theme import ThemeController
from dms.ui.automation_widget import AutomationWidget
from dms.ui.command_controller import CommandController
from dms.ui.console_widget import ConsoleWidget
from dms.ui.curator_widget import CuratorWidget
from dms.ui.device_controller import DeviceController
from dms.ui.measure_compare import MeasureCompare
from dms.ui.measure_controller import MeasureController
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
from dms.ui.rnd_bridge import RndBridge
from dms.ui.rnd_widget import RnDWidget
from dms.ui.session_dialog import SessionEditor
from dms.ui.settings_dialog import SettingsWidget
from dms.ui.squiglink_controller import SquiglinkController
from dms.ui.theme_surface import DitherSurface
from dms.ui.toggle_switch import ToggleSwitch
from dms.ui.update_check import UpdateCheck
from dms.version import __version__


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


class MainWindow(QMainWindow):
    def __init__(
        self,
        session: SessionData,
        settings: SettingsManager,
        theme_controller: ThemeController | None = None,
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
        self.measure = MeasureController(self)
        self.measure.state_changed.connect(self._apply_state_ui)
        self._console_events = ConsoleEventStore(
            parent=self,
            log_path=config_dir() / "logs" / "fastgraph-console.log",
        )
        self._report_settings_load_problems()
        self.squiglink = SquiglinkController(self)
        self.commands = CommandController(self)
        self._keyboard_shortcuts: list[QShortcut] = []
        self.measure_io = MeasureIO(self)
        self.measure_compare = MeasureCompare(self)
        self.devices = DeviceController(self)

        self._refresh_window_title()
        self.setMinimumSize(1280, 700)

        self._build_ui()
        self.rnd = RndBridge(self)
        self._configure_keyboard_shortcuts()
        self._on_theme_changed(self._theme_controller.theme, log=False)
        if bool(self._settings.get("bluetooth_headphone_mode")):
            self.devices.apply_bluetooth_headphone_mode_settings(
                notify=False,
                preserve_standard=False,
            )
        self.measure.restore_hrtf_state()
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
        QTimer.singleShot(0, self.rnd.initialize_recovery)
        QTimer.singleShot(0, self.measure_io.initialize_recovery)

        self.devices.start()

    def _build_ui(self) -> None:
        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)
        self._tabs.setCornerWidget(
            self._build_tab_header(),
            Qt.Corner.TopLeftCorner,
        )
        self._plots = MeasureWorkspace()
        self._plots.measurement_files_dropped.connect(self.measure.import_dropped_measurement_files)
        self._plots.selection_changed.connect(self.measure.on_two_channel_selection_changed)
        self._plots.balance_start_requested.connect(self.measure.start_channel_balance)
        self._plots.balance_stop_requested.connect(self.measure.stop_channel_balance)
        self._plots.balance_parameters_changed.connect(self.measure.on_balance_parameters_changed)
        self.measure_tab = MeasureTab(self)
        self._inputs_overlay_open = False
        QApplication.instance().installEventFilter(self)
        self.measure.refresh_hrtf_options()
        self._build_metadata_overlay()
        self._tabs.addTab(self.measure_tab, "Measure")

        self._rnd_widget = RnDWidget(
            parent=self,
            notes_expanded=bool(self._settings.get("rnd_notes_expanded")),
            splitter_ratio=float(self._settings.get("rnd_splitter_ratio") or 0.5),
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
            self.measure.stop_channel_balance()
        if self._tabs.currentWidget() is self._settings_scroll:
            self._settings_widget.refresh_from_settings()

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
            self.measure.stop_channel_balance()
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
            self.rnd.start_measurement()
            return
        if self._tabs.currentIndex() == 0:
            self.measure.start_queue()
            return
        self._statusbar.showMessage(
            "Shortcut ignored: switch to Measure or R&D to start a measurement."
        )

    def _shortcut_fail_review(self) -> None:
        if self.measure.queue.state != QueueState.PASS_FAIL:
            return
        if self.rnd.review_dialog is not None:
            self.rnd.review_dialog._accept_fail()
            return
        self.measure.on_fail()

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

    def _apply_state_ui(self) -> None:
        if (
            self.devices.dirty
            and self.measure.queue.allows_device_reselect()
            and not self.rnd.sweep_active
        ):
            self.devices.dirty = False
            self.devices.refresh_devices()
        self.devices.sync_device_poller()
        idle = self.measure.queue.state == QueueState.IDLE
        pass_fail = self.measure.queue.state == QueueState.PASS_FAIL
        busy = self.measure.queue.state in {QueueState.SWEEPING, QueueState.QUEUE_RUNNING}
        balance_mode = self.measure.channel_balance_mode_active()

        single_device_ok = (
            self.devices.current_output_device() is not None
            and self.devices.current_input_device() is not None
            and self.measure_tab.ch_combo.count() > 0
            and self.devices.selected_audio_pair_is_compatible()
        )
        device_ok = (
            self.devices.two_channel_devices_ready()
            if self.measure.two_channel_enabled
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
        self.measure_tab.ch_combo.setEnabled(idle and not self.measure.two_channel_enabled)

        self.measure_tab.hrtf_toggle.setEnabled(idle and self.measure.hrtf is not None)
        self._settings_widget.set_editing_enabled(idle)
        if not idle:
            self._close_metadata_overlay()
        self._rnd_widget.set_busy(not idle)
        self.measure_tab.start_queue_btn.setEnabled(idle and device_ok and not balance_mode)
        self.measure_tab.cancel_queue_btn.setEnabled(busy or pass_fail)
        active_count = (
            len(self.measure.two_channel_pairs)
            if self.measure.two_channel_enabled
            else len(self.measure.kept_curves)
        )
        self.measure_tab.undo_btn.setEnabled(idle and active_count > 0)
        has_measurements = (
            bool(self.measure.two_channel_pairs) or self.measure.queue.pending_pair is not None
            if self.measure.two_channel_enabled
            else bool(self.measure.kept_curves) or self.measure.queue.pending_curve is not None
        )
        self.measure_tab.clear_btn.setEnabled(idle and has_measurements)
        self.measure_io.sync_export_button()

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

    def closeEvent(self, event) -> None:
        if not self.rnd.confirm_close():
            event.ignore()
            return
        if not self.measure_io.confirm_close():
            event.ignore()
            return

        self.measure.close_pass_fail_dialog()
        self.rnd.close_review_dialog()
        with contextlib.suppress(Exception):
            self.devices.device_poller.stop()

        # Join the sweep thread before Qt tears the window down; a live
        # PortAudio duplex stream at interpreter exit crashes on some hosts.
        with contextlib.suppress(Exception):
            self.measure.sweep_runner.shutdown()

        with contextlib.suppress(Exception):
            self.devices.level_monitor.stop()

        with contextlib.suppress(Exception):
            self.devices.dual_level_monitor.stop()

        with contextlib.suppress(Exception):
            self.measure.stop_channel_balance()

        with contextlib.suppress(Exception):
            self.update_check.shutdown()

        # A Squiglink upload in flight: ask it to stop and give it a moment so
        # its QThread is not destroyed while running.
        with contextlib.suppress(Exception):
            self.squiglink.shutdown()

        self.rnd.shutdown()

        self.measure_io.shutdown()

        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().closeEvent(event)

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
