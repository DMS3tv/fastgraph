"""
The Measure tab's widgets.

``MeasureTab`` is the Measure page: it builds the queue bar, the controls
between the two plots and the export row around the window's measurement
workspace, and the device controls inside the Inputs overlay. The widgets are
its public attributes; their signals go straight to the window and its
controllers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from dms.audio_engine import is_windows_audio_host
from dms.ui.level_meter import LevelMeterWidget
from dms.ui.measure_controls import _MeasureSubmodeControl, _ResponsiveQueueBar
from dms.ui.modern_button import ModernButton as QPushButton
from dms.ui.modern_spinbox import (
    ModernDoubleSpinBox as QDoubleSpinBox,
)
from dms.ui.modern_spinbox import (
    ModernSpinBox as QSpinBox,
)
from dms.ui.toggle_switch import ToggleSwitch

if TYPE_CHECKING:
    from dms.ui.main_window import MainWindow


class MeasureTab(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self._window = window
        self._build_inputs_overlay()

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        plots = window._plots
        plots.set_header_widget(self._build_queue_bar())
        plots.set_between_plots_widget(self._build_plot_controls())
        plots.set_footer_widget(self._build_export_controls())
        plots.set_two_channel_enabled(window.measure.two_channel_enabled)
        plots.two.set_bottom_mode(window.measure.two_channel_bottom_mode)
        root.addWidget(plots, 1)

    def _build_inputs_overlay(self) -> None:
        overlay = QFrame(self._window._tabs)
        overlay.setObjectName("inputs_overlay")
        overlay.setProperty("surfaceLevel", "raised")
        overlay.setMinimumWidth(430)
        overlay.hide()
        layout = QVBoxLayout(overlay)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        layout.addWidget(QLabel("Output Device"))
        self.out_dev_combo = QComboBox()
        self.out_dev_combo.currentIndexChanged.connect(
            self._window.devices.on_output_device_changed
        )
        layout.addWidget(self.out_dev_combo)

        layout.addWidget(QLabel("Input Device"))
        self.in_dev_combo = QComboBox()
        self.in_dev_combo.currentIndexChanged.connect(self._window.devices.on_input_device_changed)
        layout.addWidget(self.in_dev_combo)

        layout.addWidget(QLabel("Input Channel"))
        self.ch_combo = QComboBox()
        self.ch_combo.currentIndexChanged.connect(self._window.devices.on_channel_changed)
        layout.addWidget(self.ch_combo)

        self.active_ch_label = QLabel("Active input channel: —")
        self.active_ch_label.setObjectName("label_channel_active")
        layout.addWidget(self.active_ch_label)

        self.advanced_windows_drivers_toggle = ToggleSwitch("Advanced Windows Drivers")
        self.advanced_windows_drivers_toggle.setChecked(
            bool(self._window._settings.get("windows_advanced_audio_drivers"))
        )
        self.advanced_windows_drivers_toggle.setVisible(is_windows_audio_host())
        self.advanced_windows_drivers_toggle.stateChanged.connect(
            self._window.devices.on_advanced_windows_drivers_changed
        )
        layout.addWidget(self.advanced_windows_drivers_toggle)

        self.refresh_devices_btn = QPushButton("Refresh Devices")
        self.refresh_devices_btn.clicked.connect(self._window.devices.manual_refresh_devices)
        layout.addWidget(self.refresh_devices_btn)

        self.inputs_overlay = overlay
        self.inputs_overlay_animation = QPropertyAnimation(
            overlay,
            b"geometry",
            self._window,
        )
        self.inputs_overlay_animation.setDuration(180)
        self.inputs_overlay_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.inputs_overlay_animation.finished.connect(
            self._window._on_inputs_overlay_animation_finished
        )

    def _build_queue_bar(self) -> QWidget:
        bar = _ResponsiveQueueBar()
        self.queue_bar = bar
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

        self.start_queue_btn = QPushButton("Measure")
        self.start_queue_btn.setObjectName("btn_start")
        self.start_queue_btn.clicked.connect(self._window.measure.start_queue)
        primary.addWidget(self.start_queue_btn)

        self.cancel_queue_btn = QPushButton("Cancel Queue")
        self.cancel_queue_btn.setObjectName("btn_cancel")
        self.cancel_queue_btn.clicked.connect(self._window.measure.cancel_queue)
        primary.addWidget(self.cancel_queue_btn)

        self.two_channel_toggle = ToggleSwitch("Two Channel")
        self.two_channel_toggle.setChecked(self._window.measure.two_channel_enabled)
        self.two_channel_toggle.setToolTip(
            "Measure output/input channel 1 as L and channel 2 as R."
        )
        self.two_channel_toggle.stateChanged.connect(self._window.measure.on_two_channel_toggled)
        primary.addWidget(self.two_channel_toggle)

        self.measure_submode_control = _MeasureSubmodeControl()
        self.measure_frequency_button = self.measure_submode_control.frequency_button
        self.measure_balance_button = self.measure_submode_control.balance_button
        self.measure_submode_control.balance_toggled.connect(
            self._window.measure.on_measure_submode_toggled
        )
        self.measure_submode_control.minimum_width_changed.connect(
            self.sync_queue_bar_submode_width
        )
        self.measure_submode_control.setVisible(self._window.measure.two_channel_enabled)
        primary.addWidget(self.measure_submode_control)

        n_label = QLabel("Count")
        n_label.setProperty("tone", "accent")
        primary.addWidget(n_label)
        self.queue_n_spin = QSpinBox()
        self.queue_n_spin.setObjectName("queue_count_spin")
        self.queue_n_spin.setRange(1, 100)
        self.queue_n_spin.setValue(int(self._window._settings.get("queue_count") or 5))
        self.queue_n_spin.setFixedWidth(110)
        self.queue_n_spin.valueChanged.connect(self._window.measure.on_queue_count_changed)
        primary.addWidget(self.queue_n_spin)

        level_label = QLabel("Output")
        level_label.setProperty("tone", "accent")
        primary.addWidget(level_label)
        self.queue_level_spin = QDoubleSpinBox()
        self.queue_level_spin.setRange(-120.0, 0.0)
        self.queue_level_spin.setSingleStep(0.5)
        self.queue_level_spin.setDecimals(1)
        self.queue_level_spin.setSuffix(" dB")
        self.queue_level_spin.setFixedWidth(110)
        persist_output_level = bool(self._window._settings.get("queue_output_level_persist"))
        initial_output_level = float(self._window._settings.get("queue_output_level_db") or -6.0)
        if not persist_output_level:
            initial_output_level = -6.0
        self.queue_level_spin.setValue(max(-120.0, min(0.0, initial_output_level)))
        self.queue_level_spin.valueChanged.connect(self._window.measure.on_queue_level_changed)
        primary.addWidget(self.queue_level_spin)
        self.queue_level_persist_toggle = ToggleSwitch("")
        self.queue_level_persist_toggle.setChecked(persist_output_level)
        self.queue_level_persist_toggle.stateChanged.connect(
            self._window.measure.on_queue_level_persist_changed
        )
        primary.addWidget(
            self.queue_level_persist_toggle,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        self.queue_level_persist_label = QLabel("Remember")
        self.queue_level_persist_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        primary.addWidget(self.queue_level_persist_label)
        primary.addStretch(1)

        self.queue_progress_label = QLabel("Kept: 0")
        progress.addWidget(self.queue_progress_label)

        self.queue_progress_bar = QProgressBar()
        self.queue_progress_bar.setRange(0, 1)
        self.queue_progress_bar.setValue(0)
        self.queue_progress_bar.setMinimumWidth(120)
        progress.addWidget(self.queue_progress_bar, 1)

        sweep_label = QLabel("Sweep")
        self.queue_sweep_label = sweep_label
        progress.addWidget(sweep_label)
        self.sweep_progress = QProgressBar()
        self.sweep_progress.setRange(0, 100)
        self.sweep_progress.setValue(0)
        self.sweep_progress.setMinimumWidth(120)
        progress.addWidget(self.sweep_progress, 1)

        self.queue_primary_widget = primary_widget
        self.queue_primary_layout = primary
        self.queue_progress_widget = progress_widget
        self.queue_progress_layout = progress
        bar.compact_changed.connect(self._set_queue_bar_compact)
        self.queue_bar_compact = True
        self._set_queue_bar_compact(True)
        self.sync_queue_bar_submode_width()
        return bar

    def sync_queue_bar_submode_width(self, _width: int | None = None) -> None:
        bar = getattr(self, "queue_bar", None)
        control = getattr(self, "measure_submode_control", None)
        if bar is None or control is None:
            return
        reservation = 0 if control.isHidden() else control.minimum_control_width
        bar.set_additional_compact_width(reservation)

    def _set_queue_bar_compact(self, compact: bool) -> None:
        self.queue_bar_compact = bool(compact)
        widgets = (
            self.queue_progress_label,
            self.queue_progress_bar,
            self.queue_sweep_label,
            self.sweep_progress,
        )
        if compact:
            for widget in widgets:
                self.queue_primary_layout.removeWidget(widget)
            self.queue_progress_layout.addWidget(self.queue_progress_label)
            self.queue_progress_layout.addWidget(self.queue_progress_bar, 1)
            self.queue_progress_layout.addWidget(self.queue_sweep_label)
            self.queue_progress_layout.addWidget(self.sweep_progress, 1)
            self.queue_progress_widget.setVisible(True)
            return
        for widget in widgets:
            self.queue_progress_layout.removeWidget(widget)
        self.queue_primary_layout.addWidget(self.queue_progress_label)
        self.queue_primary_layout.addWidget(self.queue_progress_bar, 1)
        self.queue_primary_layout.addWidget(self.queue_sweep_label)
        self.queue_primary_layout.addWidget(self.sweep_progress, 1)
        self.queue_progress_widget.setVisible(False)

    def _build_plot_controls(self) -> QWidget:
        from dms.ui.measure_controller import _DISTORTION_MIN_SNR_DB

        row_widget = QWidget()
        row_widget.setObjectName("measure_interplot_controls")
        row_widget.setProperty("layoutRole", "transparent")
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(8)

        input_label = QLabel("Input")
        input_label.setProperty("tone", "muted")
        row.addWidget(input_label)
        self.level_meter = LevelMeterWidget(orientation=Qt.Orientation.Horizontal)
        self.level_meter.setMinimumWidth(160)
        row.addWidget(self.level_meter, 1, Qt.AlignmentFlag.AlignVCenter)
        self.level_status_label = QLabel("RMS")
        self.level_status_label.setProperty("tone", "muted")
        self.level_status_label.setMinimumWidth(30)
        self.level_status_label.setToolTip("Live input RMS monitor")
        row.addWidget(self.level_status_label)

        self.level_meter_2 = LevelMeterWidget(orientation=Qt.Orientation.Horizontal)
        self.level_meter_2.setMinimumWidth(120)
        self.level_meter_2.setVisible(self._window.measure.two_channel_enabled)
        row.addWidget(self.level_meter_2, 1, Qt.AlignmentFlag.AlignVCenter)
        self.level_status_label_2 = QLabel("R")
        self.level_status_label_2.setProperty("tone", "muted")
        self.level_status_label_2.setVisible(self._window.measure.two_channel_enabled)
        row.addWidget(self.level_status_label_2)

        self.bottom_layout_label = QLabel("Bottom")
        self.bottom_layout_label.setProperty("tone", "muted")
        self.bottom_layout_label.setVisible(self._window.measure.two_channel_enabled)
        row.addWidget(self.bottom_layout_label)
        self.bottom_layout_combo = QComboBox()
        self.bottom_layout_combo.addItem("Combined", "combined")
        self.bottom_layout_combo.addItem("Separate", "separate")
        self.bottom_layout_combo.setCurrentIndex(
            1 if self._window.measure.two_channel_bottom_mode == "separate" else 0
        )
        self.bottom_layout_combo.setVisible(self._window.measure.two_channel_enabled)
        self.bottom_layout_combo.currentIndexChanged.connect(
            self._window.measure.on_two_channel_bottom_mode_changed
        )
        row.addWidget(self.bottom_layout_combo)

        self.variation_toggle = ToggleSwitch("Variation")
        self.variation_toggle.setToolTip(
            "Show confidence-style spread of kept measurements in the bottom viewport."
        )
        self.variation_toggle.stateChanged.connect(self._window.measure.refresh)
        row.addWidget(self.variation_toggle)

        self.distortion_toggle = ToggleSwitch("Distortion")
        self.distortion_toggle.setToolTip(
            "Overlay THD and the 2nd/3rd harmonics of the last sweep on a "
            "secondary axis in the bottom viewport. Needs at least "
            f"{_DISTORTION_MIN_SNR_DB:.0f} dB SNR."
        )
        self.distortion_toggle.setChecked(
            bool(self._window._settings.get("measure_distortion_overlay"))
        )
        self.distortion_toggle.stateChanged.connect(
            self._window.measure.on_distortion_overlay_changed
        )
        row.addWidget(self.distortion_toggle)

        self.hrtf_toggle = ToggleSwitch("HRTF")
        self.hrtf_toggle.setToolTip("Apply the selected HRTF to the bottom viewport.")
        self.hrtf_toggle.stateChanged.connect(self._window.measure.refresh)
        row.addWidget(self.hrtf_toggle)

        self.hrtf_combo = QComboBox()
        self.hrtf_combo.setMinimumWidth(120)
        self.hrtf_combo.setToolTip("Select the HRTF used for compensation.")
        self.hrtf_combo.currentIndexChanged.connect(self._window.measure.on_hrtf_selected)
        row.addWidget(self.hrtf_combo)
        self.hrtf_label = QLabel("None")
        self.hrtf_label.setProperty("tone", "muted")
        self.hrtf_label.setMaximumWidth(90)
        row.addWidget(self.hrtf_label)

        self.level_mode_label = QLabel("Level")
        self.level_mode_label.setProperty("tone", "muted")
        row.addWidget(self.level_mode_label)
        self.level_mode_combo = QComboBox()
        self.level_mode_combo.addItem("1 kHz ref", "ref_1khz")
        self.level_mode_combo.addItem("dB SPL", "dbspl")
        self.level_mode_combo.setCurrentIndex(
            1 if self._window.measure.level_mode() == "dbspl" else 0
        )
        self.level_mode_combo.setToolTip(
            "1 kHz ref normalizes every curve to 0 dB at 1 kHz. dB SPL keeps "
            "the absolute level and needs a calibrated input device."
        )
        self.level_mode_combo.currentIndexChanged.connect(
            self._window.measure.on_level_mode_changed
        )
        row.addWidget(self.level_mode_combo)

        self.compare_menu_btn = self._window.measure_compare.build_menu()
        row.addWidget(self.compare_menu_btn)

        self.undo_btn = QPushButton("Undo")
        self.undo_btn.clicked.connect(self._window.measure.undo_last_measurement)
        row.addWidget(self.undo_btn)

        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.setObjectName("btn_danger")
        self.clear_btn.clicked.connect(self._window.measure.clear_all)
        row.addWidget(self.clear_btn)
        return row_widget

    def _build_export_controls(self) -> QWidget:
        row_widget = QWidget()
        row_widget.setObjectName("measure_export_controls")
        row_widget.setProperty("layoutRole", "transparent")
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(8)

        self.session_menu_btn = self._window.measure_io.build_session_menu()
        row.addWidget(self.session_menu_btn)

        row.addWidget(QLabel("Export directory:"))
        self.export_dir_input = QLineEdit()
        self.export_dir_input.setPlaceholderText("Default: choose at export")
        self.export_dir_input.setText(str(self._window._settings.get("export_directory") or ""))
        self.export_dir_input.setMinimumWidth(140)
        self.export_dir_input.setMaximumWidth(240)
        row.addWidget(self.export_dir_input)
        export_dir_btn = QPushButton("Browse…")
        export_dir_btn.clicked.connect(self._window.measure_io.choose_export_directory)
        row.addWidget(export_dir_btn)

        self.send_to_rnd_btn = QPushButton("Send to R&D")
        # The R&D bridge is built after this tab, so it is looked up on click.
        self.send_to_rnd_btn.clicked.connect(lambda: self._window.rnd.send_measure_to_rnd())
        row.addWidget(self.send_to_rnd_btn)

        self.export_btn = QPushButton("Export Average…")
        self.export_btn.setObjectName("btn_export")
        self.export_btn.clicked.connect(self._window.measure_io.export)
        row.addWidget(self.export_btn)

        self.send_to_curator_btn = QPushButton("Send to Curator")
        self.send_to_curator_btn.clicked.connect(lambda: self._window.rnd.send_to_curator())
        self.send_to_curator_btn.setToolTip("Add the current average or variation view to Curator.")
        row.addWidget(self.send_to_curator_btn)

        self.upload_btn = QPushButton("Upload to Squiglink")
        self.upload_btn.setObjectName("btn_upload")
        self.upload_btn.clicked.connect(self._window.measure_io.run_upload_action)
        row.addWidget(self.upload_btn)
        return row_widget
