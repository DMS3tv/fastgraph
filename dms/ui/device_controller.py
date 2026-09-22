"""
Audio device selection for the whole window.

``DeviceController`` enumerates and selects the input and output devices and
the input channel, owns the device poller and the live input level monitors,
applies and restores Bluetooth Headphone Mode, and opens the calibration and
test-level dialogs for the selected input.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import sounddevice as sd
from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QMessageBox

from dms.audio_engine import (
    DevicePoller,
    DualLevelMonitor,
    LevelMonitor,
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
from dms.measure_queue import QueueState
from dms.measurement_profiles import (
    BLUETOOTH_PROFILE_DEFAULTS,
    PROFILE_SNAPSHOT_SETTING,
    restore_standard_profile_updates,
    snapshot_measurement_profile,
)
from dms.ui.calibration_dialog import CalibrationDialog
from dms.ui.measure_dialogs import TestLevelDialog

if TYPE_CHECKING:
    from dms.ui.main_window import MainWindow

_METER_UPDATE_MS = 140


class DeviceController(QObject):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        #: Set when a device change arrived while the devices were in use; the
        #: refresh happens as soon as the window returns to idle.
        self.dirty = False
        self.last_level_dbfs = -120.0
        self._displayed_level_dbfs = -60.0
        self._last_input_devices: list[tuple[int, str, int]] = []
        self._last_output_devices: list[tuple[int, str, int]] = []
        self._input_devices_by_index: dict[int, dict] = {}
        self._output_devices_by_index: dict[int, dict] = {}
        self._input_device_labels_by_index: dict[int, str] = {}
        self._output_device_labels_by_index: dict[int, str] = {}

        self.level_monitor = LevelMonitor()
        self.level_monitor.level_updated.connect(self._on_level_update)
        self.level_monitor.error_occurred.connect(self._on_level_error)
        self.dual_level_monitor = DualLevelMonitor()
        self.dual_level_monitor.levels_updated.connect(self._on_dual_level_update)
        self.dual_level_monitor.error_occurred.connect(self._on_level_error)
        self._last_dual_levels = (-120.0, -120.0)

    def start(self) -> None:
        """Start the meter refresh and the device poller once the window is built."""
        self._meter_ui_timer = QTimer(self._window)
        self._meter_ui_timer.timeout.connect(self._refresh_level_meter_display)
        self._meter_ui_timer.start(_METER_UPDATE_MS)

        # Device enumeration is four PortAudio calls; it runs on the poller's
        # own thread and only reports back when the device set changed.
        self.device_poller = DevicePoller(parent=self._window)
        self.device_poller.devices_changed.connect(self.check_devices)
        self.sync_device_poller()
        self.device_poller.start()

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def current_output_device(self) -> int | None:
        value = self._window.measure_tab.out_dev_combo.currentData()
        return int(value) if value is not None else None

    def current_input_device(self) -> int | None:
        value = self._window.measure_tab.in_dev_combo.currentData()
        return int(value) if value is not None else None

    def current_output_device_info(self) -> dict | None:
        index = self.current_output_device()
        if index is None:
            return None
        return self._output_devices_by_index.get(index)

    def current_input_device_info(self) -> dict | None:
        index = self.current_input_device()
        if index is None:
            return None
        return self._input_devices_by_index.get(index)

    def current_output_device_label(self) -> str:
        index = self.current_output_device()
        if index is None:
            return ""
        return self._output_device_labels_by_index.get(index, str(index))

    def current_input_device_label(self) -> str:
        index = self.current_input_device()
        if index is None:
            return ""
        return self._input_device_labels_by_index.get(index, str(index))

    def current_output_device_setting(self) -> dict | None:
        device = self.current_output_device_info()
        return device_setting(device, "output") if device is not None else None

    def current_input_device_setting(self) -> dict | None:
        device = self.current_input_device_info()
        return device_setting(device, "input") if device is not None else None

    def current_input_channel(self) -> int:
        value = self._window.measure_tab.ch_combo.currentData()
        return int(value) if value is not None else 0

    def _use_advanced_windows_drivers(self) -> bool:
        return bool(self._window.measure_tab.advanced_windows_drivers_toggle.isChecked())

    def selected_audio_pair_is_compatible(self) -> bool:
        return is_compatible_device_pair(
            self.current_input_device_info(),
            self.current_output_device_info(),
        )

    def windows_audio_pair_message(self) -> str:
        in_label = self.current_input_device_label() or "selected input"
        out_label = self.current_output_device_label() or "selected output"
        return (
            "On Windows, input and output must use the same audio driver backend "
            f"for stable timing.\n\nInput: {in_label}\nOutput: {out_label}"
        )

    def two_channel_devices_ready(self) -> bool:
        input_info = self.current_input_device_info()
        output_info = self.current_output_device_info()
        return bool(
            input_info is not None
            and output_info is not None
            and int(input_info.get("max_input_channels", 0) or 0) >= 2
            and int(output_info.get("max_output_channels", 0) or 0) >= 2
            and self.selected_audio_pair_is_compatible()
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
        window = self._window
        if not is_windows_audio_host():
            return
        input_device = self.current_input_device_info()
        output_device = self.current_output_device_info()
        if input_device is None:
            return
        if is_compatible_device_pair(input_device, output_device):
            return
        matched_output = self._matching_output_for_input(input_device)
        if matched_output is not None:
            idx = window.measure_tab.out_dev_combo.findData(int(matched_output["index"]))
            if idx >= 0:
                window.measure_tab.out_dev_combo.setCurrentIndex(idx)
                window._settings.set("output_device", self.current_output_device_setting())
                if show_status:
                    window._statusbar.showMessage(
                        "Matched Windows input/output to the same audio driver backend."
                    )
                return
        window.measure_tab.out_dev_combo.setCurrentIndex(-1)
        window._settings.set("output_device", None)
        if show_status:
            window._statusbar.showMessage(
                "No matching Windows output backend found for the selected input."
            )

    def sweep_latency_mode(self) -> str:
        settings = self._window._settings
        configured = str(settings.get("latency"))
        if bool(settings.get("bluetooth_headphone_mode")):
            return configured
        if is_windows_audio_host() and not bool(settings.get("latency_user_override")):
            return "high"
        return configured

    def console_devices(self) -> str:
        output_lines = ["Output devices:"]
        for index, label in self._output_device_labels_by_index.items():
            marker = "*" if index == self.current_output_device() else " "
            output_lines.append(f" {marker} [{index}] {label}")
        input_lines = ["Input devices:"]
        for index, label in self._input_device_labels_by_index.items():
            marker = "*" if index == self.current_input_device() else " "
            input_lines.append(f" {marker} [{index}] {label}")
        if len(output_lines) == 1:
            output_lines.append("   none")
        if len(input_lines) == 1:
            input_lines.append("   none")
        return "\n".join(output_lines + input_lines)

    # ------------------------------------------------------------------
    # Enumeration
    # ------------------------------------------------------------------

    def refresh_devices(self) -> None:
        window = self._window
        selected_out = (
            self.current_output_device_setting()
            if self.current_output_device() is not None
            else window._settings.get("output_device")
        )
        selected_in = (
            self.current_input_device_setting()
            if self.current_input_device() is not None
            else window._settings.get("input_device")
        )
        selected_ch = self.current_input_channel()

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

        window.measure_tab.out_dev_combo.blockSignals(True)
        window.measure_tab.in_dev_combo.blockSignals(True)
        window.measure_tab.ch_combo.blockSignals(True)

        self._output_devices_by_index = {int(d["index"]): d for d in out_devices}
        self._input_devices_by_index = {int(d["index"]): d for d in in_devices}
        self._output_device_labels_by_index = {
            int(d["index"]): device_label(d, out_duplicates) for d in out_devices
        }
        self._input_device_labels_by_index = {
            int(d["index"]): device_label(d, in_duplicates) for d in in_devices
        }

        window.measure_tab.out_dev_combo.clear()
        for d in out_devices:
            window.measure_tab.out_dev_combo.addItem(
                self._output_device_labels_by_index[int(d["index"])],
                int(d["index"]),
            )

        window.measure_tab.in_dev_combo.clear()
        for d in in_devices:
            window.measure_tab.in_dev_combo.addItem(
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
                window.measure_tab.out_dev_combo.setCurrentIndex(
                    window.measure_tab.out_dev_combo.findData(int(selected_out_device["index"]))
                )
            elif out_ambiguous:
                window.measure_tab.out_dev_combo.setCurrentIndex(-1)
            else:
                window.measure_tab.out_dev_combo.setCurrentIndex(0)

        if in_devices:
            if selected_in_device is not None:
                window.measure_tab.in_dev_combo.setCurrentIndex(
                    window.measure_tab.in_dev_combo.findData(int(selected_in_device["index"]))
                )
            elif in_ambiguous:
                window.measure_tab.in_dev_combo.setCurrentIndex(-1)
            else:
                window.measure_tab.in_dev_combo.setCurrentIndex(0)

        window.measure_tab.out_dev_combo.blockSignals(False)
        window.measure_tab.in_dev_combo.blockSignals(False)

        self._sync_windows_output_to_input(show_status=False)

        self._refresh_channels(selected_ch=selected_ch)
        window.measure_tab.ch_combo.blockSignals(False)

        window._settings.set("output_device", self.current_output_device_setting())
        window._settings.set("input_device", self.current_input_device_setting())

        self._last_output_devices = out_signature
        self._last_input_devices = in_signature
        window._log_event(
            "INFO",
            "devices",
            "Audio devices refreshed",
            output_count=len(out_devices),
            input_count=len(in_devices),
            selected_output=self.current_output_device_label(),
            selected_input=self.current_input_device_label(),
            input_channel=self.current_input_channel() + 1,
        )

        if out_ambiguous or in_ambiguous:
            window._statusbar.showMessage(
                "Saved audio device name is ambiguous. Select the desired host API once."
            )
        elif is_windows_audio_host() and preferred_hostapi is not None:
            window._statusbar.showMessage(
                "Windows audio set to matched driver backend for stable timing."
            )

        window._apply_state_ui()
        self.start_level_monitor()
        window._refresh_session_labels()

    def manual_refresh_devices(self) -> None:
        previous_out = self.current_output_device()
        previous_in = self.current_input_device()
        previous_ch = self.current_input_channel()

        self._window._stop_channel_balance()
        self.stop_level_monitor()
        refresh_audio_backend()
        self.refresh_devices()

        current_out = self.current_output_device()
        current_in = self.current_input_device()
        current_ch = self.current_input_channel()
        if previous_out == current_out and previous_in == current_in and previous_ch == current_ch:
            self._window._statusbar.showMessage("Audio devices refreshed.")
        else:
            self._window._statusbar.showMessage("Audio devices refreshed; selection changed.")

    def _refresh_channels(self, selected_ch: int | None = None) -> None:
        window = self._window
        input_device = self.current_input_device()
        count = device_channel_count(input_device, "input") if input_device is not None else 0

        window.measure_tab.ch_combo.clear()
        for idx in range(count):
            window.measure_tab.ch_combo.addItem(f"Ch {idx + 1}", idx)

        want_ch = (
            selected_ch
            if selected_ch is not None
            else int(window._settings.get("input_channel") or 0)
        )

        if count > 0:
            want_ch = max(0, min(want_ch, count - 1))
            window.measure_tab.ch_combo.setCurrentIndex(want_ch)
            window._settings.set("input_channel", want_ch)
            window.measure_tab.active_ch_label.setText(f"Active input channel: Ch {want_ch + 1}")
        else:
            window.measure_tab.active_ch_label.setText("Active input channel: —")
        window._rnd_widget.set_input_channels(
            [
                (
                    window.measure_tab.ch_combo.itemText(index),
                    int(window.measure_tab.ch_combo.itemData(index)),
                )
                for index in range(window.measure_tab.ch_combo.count())
            ],
            self.current_input_channel(),
        )

    def sync_device_poller(self) -> None:
        """Pause polling whenever PortAudio must not be re-enumerated."""
        poller = getattr(self, "device_poller", None)
        if poller is None:
            return
        busy = not self._window._queue.allows_device_reselect() or self._window._rnd_sweep_active
        poller.pause(busy)

    def check_devices(
        self,
        output_devices: list[dict] | None = None,
        input_devices: list[dict] | None = None,
    ) -> None:
        """React to a device-set change reported by :class:`DevicePoller`.

        The lists arrive from the poller thread; they are only enumerated here
        when a caller (a test, or a manual check) passes nothing.
        """
        window = self._window
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

        window._stop_channel_balance()

        selected_out = self.current_output_device()
        selected_in = self.current_input_device()
        selected_vanished = selected_out not in {
            idx for idx, _name, _hostapi in current_out
        } or selected_in not in {idx for idx, _name, _hostapi in current_in}

        if selected_vanished and window._state != QueueState.IDLE:
            # Whether a sweep is running, a pair is between channels, or a
            # review is open: the device is gone, so the queue is over.
            window._abort_active_sweep()
            window._close_pass_fail_dialog()
            window._queue.reset()
            window._state = QueueState.IDLE
            window._update_queue_progress()
            window._apply_state_ui()
            window._statusbar.showMessage(
                "Audio device change detected. Active measurement aborted safely."
            )

        if window._queue.allows_device_reselect() and not window._rnd_sweep_active:
            self.refresh_devices()
        else:
            # Never re-select devices under a running queue or an open review;
            # the refresh happens as soon as the window returns to idle.
            self.dirty = True

    # ------------------------------------------------------------------
    # Combo handlers
    # ------------------------------------------------------------------

    def on_output_device_changed(self) -> None:
        window = self._window
        window._stop_channel_balance()
        window._settings.set("output_device", self.current_output_device_setting())
        window._refresh_session_labels()
        window._apply_state_ui()
        if (
            is_windows_audio_host()
            and self.current_output_device() is not None
            and self.current_input_device() is not None
            and not self.selected_audio_pair_is_compatible()
        ):
            window._statusbar.showMessage("Windows input/output driver backends do not match.")

    def on_input_device_changed(self) -> None:
        window = self._window
        window._stop_channel_balance()
        window._settings.set("input_device", self.current_input_device_setting())
        self._sync_windows_output_to_input()
        self._refresh_channels()
        self.start_level_monitor()
        window._apply_state_ui()
        window._refresh_session_labels()

    def on_advanced_windows_drivers_changed(self) -> None:
        enabled = self._use_advanced_windows_drivers()
        self._window._settings.set("windows_advanced_audio_drivers", enabled)
        self.refresh_devices()
        if enabled:
            self._window._statusbar.showMessage(
                "Advanced Windows drivers visible. Keep input/output on the same backend."
            )
        else:
            self._window._statusbar.showMessage(
                "Advanced Windows drivers hidden. Using the preferred matched backend."
            )

    def on_channel_changed(self) -> None:
        window = self._window
        window._settings.set("input_channel", self.current_input_channel())
        window.measure_tab.active_ch_label.setText(
            f"Active input channel: Ch {self.current_input_channel() + 1}"
        )
        window._rnd_widget.set_input_channel(self.current_input_channel())
        self.start_level_monitor()
        window._refresh_session_labels()

    def on_rnd_input_channel_changed(self, channel: int) -> None:
        index = self._window.measure_tab.ch_combo.findData(int(channel))
        if index >= 0 and index != self._window.measure_tab.ch_combo.currentIndex():
            self._window.measure_tab.ch_combo.setCurrentIndex(index)

    def automation_switch_input_device(self, requested: str) -> None:
        window = self._window
        if window._state != QueueState.IDLE:
            raise ValueError("Input device can only be changed while idle.")
        text = requested.strip()
        for index in range(window.measure_tab.in_dev_combo.count()):
            data = window.measure_tab.in_dev_combo.itemData(index)
            label = window.measure_tab.in_dev_combo.itemText(index)
            if text == str(data) or text.casefold() in label.casefold():
                window.measure_tab.in_dev_combo.setCurrentIndex(index)
                return
        raise ValueError(f"Input device unavailable: {requested}")

    def automation_switch_input_channel(self, requested: str) -> None:
        window = self._window
        if window._state != QueueState.IDLE:
            raise ValueError("Input channel can only be changed while idle.")
        raw = requested.strip().lower()
        text = raw.removeprefix("ch").strip()
        for index in range(window.measure_tab.ch_combo.count()):
            data = window.measure_tab.ch_combo.itemData(index)
            label = window.measure_tab.ch_combo.itemText(index).lower()
            if text == str(data) or text == str(int(data) + 1) or raw == label:
                window.measure_tab.ch_combo.setCurrentIndex(index)
                return
        raise ValueError(f"Input channel unavailable: {requested}")

    # ------------------------------------------------------------------
    # Level monitor
    # ------------------------------------------------------------------

    def stop_level_monitor(self) -> None:
        self.level_monitor.stop()
        self.dual_level_monitor.stop()

    def start_level_monitor(self) -> None:
        window = self._window
        self.stop_level_monitor()

        if window._state == QueueState.SWEEPING or window._channel_balance_active:
            return

        input_device = self.current_input_device()
        if input_device is None:
            self._displayed_level_dbfs = -60.0
            window.measure_tab.level_meter.set_level(-60.0)
            window.measure_tab.level_status_label.setText("No input")
            return

        try:
            if window._two_channel_enabled:
                if not self.two_channel_devices_ready():
                    window.measure_tab.level_status_label.setText("Two inputs needed")
                    window.measure_tab.level_status_label_2.setText("R")
                    return
                self.dual_level_monitor.start(
                    device_index=input_device,
                    device_label=self.current_input_device_label(),
                    fs=int(window._settings.get("sample_rate")),
                    buffer_size=int(window._settings.get("buffer_size")),
                )
                window.measure_tab.level_status_label.setText("L")
                window.measure_tab.level_status_label_2.setText("R")
                return
            self.level_monitor.start(
                device_index=input_device,
                device_label=self.current_input_device_label(),
                channel_index=self.current_input_channel(),
                fs=int(window._settings.get("sample_rate")),
                buffer_size=int(window._settings.get("buffer_size")),
            )
            window.measure_tab.level_status_label.setText("RMS")
        except Exception as exc:
            window._statusbar.showMessage(f"Level monitor start failed: {exc}")

    def _on_level_update(self, dbfs: float) -> None:
        self.last_level_dbfs = float(dbfs)

    def _on_dual_level_update(self, left_dbfs: float, right_dbfs: float) -> None:
        self._last_dual_levels = (float(left_dbfs), float(right_dbfs))
        self.last_level_dbfs = max(self._last_dual_levels)

    def _refresh_level_meter_display(self) -> None:
        window = self._window
        if window._two_channel_enabled:
            left_db, right_db = self._last_dual_levels
            window.measure_tab.level_meter.set_level(max(-60.0, min(0.0, left_db)))
            window.measure_tab.level_meter_2.set_level(max(-60.0, min(0.0, right_db)))
            return
        target_db = max(-60.0, min(0.0, self.last_level_dbfs))
        self._displayed_level_dbfs = self._displayed_level_dbfs * 0.5 + target_db * 0.5
        if abs(self._displayed_level_dbfs - target_db) < 0.2:
            self._displayed_level_dbfs = target_db
        window.measure_tab.level_meter.set_level(self._displayed_level_dbfs)

    def _on_level_error(self, message: str) -> None:
        self._window._statusbar.showMessage(message)

    # ------------------------------------------------------------------
    # Bluetooth Headphone Mode
    # ------------------------------------------------------------------

    def on_bluetooth_mode_changed(self, _state: int) -> None:
        window = self._window
        enabled = window._bluetooth_mode_toggle.isChecked()
        window._settings.set("bluetooth_headphone_mode", enabled)
        if enabled:
            self.apply_bluetooth_headphone_mode_settings(
                notify=True,
                preserve_standard=True,
            )
            return
        used_fallback = self._apply_standard_measurement_mode_settings()
        if used_fallback:
            window._statusbar.showMessage(
                "Bluetooth headphone mode disabled. Restored standard measurement defaults."
            )
        else:
            window._statusbar.showMessage(
                "Bluetooth headphone mode disabled. Restored standard measurement settings."
            )

    def apply_bluetooth_headphone_mode_settings(
        self,
        notify: bool,
        preserve_standard: bool,
    ) -> None:
        window = self._window
        updates = dict(BLUETOOTH_PROFILE_DEFAULTS)
        if preserve_standard:
            current_profile = {key: window._settings.get(key) for key in updates}
            updates[PROFILE_SNAPSHOT_SETTING] = snapshot_measurement_profile(current_profile)
        window._settings.update(updates)
        window._settings_widget.refresh_from_settings()
        if notify:
            window._statusbar.showMessage(
                "Bluetooth mode applied: high latency, 512 buffer, and Bluetooth-safe timing thresholds."
            )

    def _apply_standard_measurement_mode_settings(self) -> bool:
        window = self._window
        updates, used_fallback = restore_standard_profile_updates(
            window._settings.get(PROFILE_SNAPSHOT_SETTING)
        )
        updates[PROFILE_SNAPSHOT_SETTING] = None
        window._settings.update(updates)
        window._settings_widget.refresh_from_settings()
        return used_fallback

    def set_console_bluetooth_mode(self, enabled: bool) -> None:
        window = self._window
        settings = window._settings
        current = bool(settings.get("bluetooth_headphone_mode"))
        if enabled == current:
            settings.set_session("bluetooth_headphone_mode", enabled)
        elif enabled:
            updates = dict(BLUETOOTH_PROFILE_DEFAULTS)
            self._console_bt_snapshot = {key: settings.get(key) for key in updates}
            settings.set_session("bluetooth_headphone_mode", True)
            settings.set_session(
                PROFILE_SNAPSHOT_SETTING,
                snapshot_measurement_profile(self._console_bt_snapshot),
            )
            for key, value in updates.items():
                settings.set_session(key, value)
        else:
            settings.set_session("bluetooth_headphone_mode", False)
            for key, value in getattr(self, "_console_bt_snapshot", {}).items():
                settings.set_session(key, value)
            settings.set_session(PROFILE_SNAPSHOT_SETTING, None)
        window._bluetooth_mode_toggle.blockSignals(True)
        window._bluetooth_mode_toggle.setChecked(enabled)
        window._bluetooth_mode_toggle.blockSignals(False)
        window._settings_widget.refresh_from_settings()

    # ------------------------------------------------------------------
    # Calibration and test level
    # ------------------------------------------------------------------

    def calibrated_sensitivity(self) -> float | None:
        """Pa/FS for the selected input device, or None when uncalibrated.

        ``CalibrationStore`` is keyed by the device's raw name — the same key
        ``CalibrationDialog`` writes — not by the disambiguated UI label.
        """
        info = self.current_input_device_info()
        if info is None:
            return None
        name = str(info.get("name") or "")
        if not name or not self._window._cal_store.is_calibrated(name):
            return None
        return self._window._cal_store.get_sensitivity(name)

    def open_calibration(self) -> None:
        window = self._window
        input_device = self.current_input_device()
        input_info = self.current_input_device_info()
        if input_device is None or input_info is None:
            QMessageBox.warning(
                window,
                "No Input Device",
                "Select an input device first.",
            )
            return

        dlg = CalibrationDialog(
            device_index=input_device,
            device_name=str(input_info["name"]),
            device_label=self.current_input_device_label(),
            channel=self.current_input_channel(),
            fs=int(window._settings.get("sample_rate")),
            buffer_size=int(window._settings.get("buffer_size")),
            cal_store=window._cal_store,
            parent=window,
        )
        dlg.calibration_done.connect(self._on_calibration_done)
        dlg.exec()

    def _on_calibration_done(self, device_name: str, sensitivity: float) -> None:
        label = self.current_input_device_label() or device_name
        self._window._statusbar.showMessage(
            f"Calibration saved for {label}: {sensitivity:.6f} Pa/FS"
        )

    def open_test_level(self) -> None:
        window = self._window
        input_device = self.current_input_device()
        input_info = self.current_input_device_info()
        if input_device is None or input_info is None:
            QMessageBox.information(
                window,
                "No Input Device",
                "Select an input device first.",
            )
            return

        calibrated = window._cal_store.is_calibrated(str(input_info["name"]))
        dlg = TestLevelDialog(
            self._level_snapshot,
            play_noise_fn=self._play_test_noise,
            calibrated=calibrated,
            parent=window,
        )
        dlg.exec()

    def _play_test_noise(self) -> str | None:
        output_device = self.current_output_device()
        if output_device is None:
            return "No output device selected."
        fs = int(self._window._settings.get("sample_rate"))
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
        self._window._statusbar.showMessage("Played test noise ping on selected output device.")
        return None

    def _level_snapshot(self) -> tuple[float, float | None, str]:
        cal_store = self._window._cal_store
        input_info = self.current_input_device_info()
        input_device_name = str(input_info["name"]) if input_info is not None else ""
        input_label = self.current_input_device_label()
        dbfs = self.last_level_dbfs
        spl = None
        if input_device_name and cal_store.is_calibrated(input_device_name):
            rms_fs = 10.0 ** (dbfs / 20.0) if dbfs > -120.0 else 0.0
            spl = cal_store.rms_to_dbspl(input_device_name, rms_fs)
        return dbfs, spl, input_label
