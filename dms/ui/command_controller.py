"""
Console commands and automations.

``CommandController`` runs what is typed into the console and the automations
the Automation tab defines: the command parsers, the risky-action
confirmation, variable substitution, and the trigger queue that runs
automations one after another.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QMessageBox

from dms.automation import AutomationDefinition, AutomationStep, default_automation_directory
from dms.console import runtime_diagnostics
from dms.curator.parser import parse_measurement_txt
from dms.file_io import ensure_extension
from dms.measure_persistence import MEASURE_SESSION_EXTENSION, save_measure_session
from dms.measure_queue import QueueState
from dms.measurement_alignment import format_diagnostics_summary
from dms.measurement_profiles import BLUETOOTH_PROFILE_DEFAULTS, PROFILE_SNAPSHOT_SETTING

if TYPE_CHECKING:
    from dms.ui.main_window import MainWindow

_CONSOLE_SETTING_SPECS = {
    "sweep_duration": ("float", 0.5, 30.0),
    "sample_rate": ("choice", {44100, 48000, 88200, 96000, 192000}),
    "buffer_size": ("choice", {64, 128, 256, 512, 1024, 2048, 4096}),
    "pre_sweep_silence": ("float", 0.05, 2.0),
    "post_sweep_silence": ("float", 0.1, 3.0),
    "latency": ("choice", {"low", "high"}),
    "start_alignment_confidence_min": ("float", 0.0, 30.0),
    "sweep_noise_margin_min_db": ("float", 0.0, 60.0),
    "snr_warn_db": ("float", 0.0, 60.0),
    "end_marker_confidence_min": ("float", 2.0, 30.0),
    "timing_drift_max_ms": ("float", 5.0, 250.0),
    "bluetooth_mode": ("bool",),
    "queue_count": ("int", 1, 100),
    "output_level": ("float", -120.0, 0.0),
}

#: Triggers an automation step can raise itself. Queueing these would let an
#: automation re-trigger itself without end, so the re-entrancy guard stays.
_AUTOMATION_REENTRANT_TRIGGERS = {"export_complete", "app_error"}
_AUTOMATION_QUEUE_LIMIT = 32

#: Console commands that do what a risky action does, spelled as text.
_RISKY_CONSOLE_PREFIXES = (
    "measure",
    "export",
    "settings set",
    "curator export",
    "rnd",
)

#: ``{name}`` placeholders, substituted in one pass so a value that contains
#: another variable's placeholder is never expanded a second time.
_AUTOMATION_VARIABLE_PATTERN = re.compile(r"\{([^{}]+)\}")

_CONSOLE_SETTING_KEYS = {
    "bluetooth_mode": "bluetooth_headphone_mode",
    "output_level": "queue_output_level_db",
}


class CommandController(QObject):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window
        self.running = False

    def _command_reply(self, message: str, error: bool = False) -> None:
        self._window._log_event("ERROR" if error else "INFO", "console", message)

    def automation_default_dir(self) -> Path:
        configured = str(self._window._settings.get("automation_directory") or "").strip()
        if configured:
            return Path(configured).expanduser()
        return default_automation_directory()

    def trigger(self, trigger: str) -> None:
        """Queue every automation for ``trigger`` and run them in order.

        Two automations on the same trigger used to mean the second one was
        dropped with a warning. They are queued instead and run one after the
        other. ``export_complete`` and ``app_error`` keep the old guard: those
        two are raised by automation steps themselves, so queueing them would
        let an automation re-trigger itself forever.
        """
        widget = getattr(self._window, "_automation_widget", None)
        if widget is None:
            return
        running = self.running
        if running and trigger in _AUTOMATION_REENTRANT_TRIGGERS:
            self._window._log_event(
                "DEBUG",
                "automation",
                "Automation trigger ignored while an automation is running",
                trigger=trigger,
            )
            return
        queue = self._automation_pending()
        for automation in widget.events.automations_for_trigger(trigger):
            if len(queue) >= _AUTOMATION_QUEUE_LIMIT:
                self._window._log_event(
                    "WARNING",
                    "automation",
                    "Automation queue is full; dropped an automation",
                    name=automation.name,
                    trigger=trigger,
                )
                break
            queue.append((automation, trigger))
        if not running:
            self._drain_automation_queue()

    def _automation_pending(self) -> list[tuple[AutomationDefinition, str]]:
        """The trigger queue, created on first use."""
        queue = getattr(self, "_automation_queue", None)
        if queue is None:
            queue = []
            self._automation_queue = queue
        return queue

    def _drain_automation_queue(self) -> None:
        """Run queued automations sequentially, never re-entering a run."""
        if self.running or getattr(self, "_automation_draining", False):
            return
        queue = self._automation_pending()
        self._automation_draining = True
        try:
            while queue:
                automation, trigger = queue.pop(0)
                self.run_automation(automation, triggered_by=trigger)
        finally:
            self._automation_draining = False

    def run_automation(
        self, automation: AutomationDefinition, triggered_by: str = "manual"
    ) -> None:
        if self.running:
            self._window._log_event(
                "WARNING", "automation", "Automation already running", name=automation.name
            )
            return
        self.running = True
        variables = dict(automation.variables)
        self._window._log_event(
            "INFO", "automation", "Automation started", name=automation.name, trigger=triggered_by
        )
        try:
            for index, step in enumerate(automation.steps, start=1):
                if not self._automation_condition_matches(step, variables):
                    self._window._log_event(
                        "DEBUG", "automation", "Automation step skipped", step=index
                    )
                    continue
                if self._automation_step_is_risky(step) and not step.skip_risky_confirmation:
                    choice = QMessageBox.question(
                        self._window,
                        "Confirm Automation Action",
                        f"Run risky automation action?\n\n{step.action}: {step.target} {step.value}",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if choice != QMessageBox.StandardButton.Yes:
                        raise RuntimeError(f"Automation canceled before step {index}.")
                self._execute_automation_step(step, variables)
                self._window._log_event(
                    "INFO", "automation", "Automation step complete", step=index, action=step.action
                )
            self._window._log_event(
                "INFO", "automation", "Automation complete", name=automation.name
            )
        except Exception as exc:
            self._window._log_event(
                "ERROR", "automation", f"Automation failed: {exc}", name=automation.name
            )
            if triggered_by == "manual":
                QMessageBox.warning(self._window, "Automation Failed", str(exc))
        finally:
            self.running = False
            self._drain_automation_queue()

    def _automation_condition_matches(
        self, step: AutomationStep, variables: dict[str, object]
    ) -> bool:
        condition = step.condition
        value = str(condition.value)
        current = str(variables.get(condition.left, ""))
        if condition.kind == "always":
            return True
        if condition.kind == "variable_equals":
            return current == value
        if condition.kind == "variable_not_equals":
            return current != value
        if condition.kind == "variable_contains":
            return value in current
        if condition.kind == "variable_true":
            return bool(variables.get(condition.left))
        if condition.kind == "variable_false":
            return not bool(variables.get(condition.left))
        if condition.kind == "app_state":
            return self._window.measure.queue.state == value
        if condition.kind == "kept_count_at_least":
            return len(self._window.measure.kept_curves) >= int(value or 0)
        if condition.kind == "rnd_count_at_least":
            return len(self._window._rnd_widget.session.measurements) >= int(value or 0)
        if condition.kind == "curator_layers_at_least":
            return len(self._window._curator_widget.graph_state.layers) >= int(value or 0)
        return False

    @staticmethod
    def _automation_step_is_risky(step: AutomationStep) -> bool:
        """Whether this step needs the risky-action confirmation.

        ``console_command`` is judged by what it would run: the console can
        start a queue, export, or change a setting, and those are exactly the
        actions that ask first when spelled as their own action name.
        """
        if step.action == "console_command":
            command = " ".join(f"{step.target} {step.value}".split()).casefold()
            return command.startswith(_RISKY_CONSOLE_PREFIXES)
        return step.action in {
            "measure_start",
            "measure_pass",
            "measure_fail",
            "measure_cancel",
            "rnd_start",
            "rnd_load_session",
            "rnd_export_selected",
            "rnd_send_to_curator",
            "curator_send_measure",
            "curator_export_png",
            "export_average",
            "export_variation",
        }

    def _execute_automation_step(self, step: AutomationStep, variables: dict[str, object]) -> None:
        action = step.action
        target = self._expand_automation_text(step.target, variables)
        value = self._expand_automation_text(step.value, variables)
        if action == "navigate":
            self._automation_navigate(target)
        elif action == "switch_input_device":
            self._window.devices.automation_switch_input_device(target or value)
        elif action == "switch_input_channel":
            self._window.devices.automation_switch_input_channel(target or value)
        elif action == "console_command":
            self.run_console_command(target or value)
        elif action == "prompt_info":
            QMessageBox.information(self._window, "Automation", value or target)
        elif action == "prompt_warning":
            QMessageBox.warning(self._window, "Automation", value or target)
        elif action == "prompt_yes_no":
            result = QMessageBox.question(
                self._window,
                "Automation",
                value or target,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            variables[target or "prompt_result"] = result == QMessageBox.StandardButton.Yes
        elif action == "set_variable":
            variables[target] = value
        elif action == "clear_variable":
            variables.pop(target, None)
        elif action in {"increment_variable", "decrement_variable"}:
            raw = variables.get(target, 0)
            current = float(raw or 0)
            delta = float(value or 1)
            result = current + delta if action == "increment_variable" else current - delta
            # A counter that started as an int stays an int: "3" reads better
            # than "3.0" in a prompt, an export name, or a comparison.
            keeps_int = isinstance(raw, int) and not isinstance(raw, bool)
            if keeps_int and float(delta).is_integer():
                variables[target] = int(result)
            else:
                variables[target] = result
        elif action == "measure_start":
            args = ["start"]
            if target:
                args.append(target)
            if value:
                args.append(value)
            self._run_measure_command(args)
        elif action == "measure_pass":
            self._run_measure_command(["pass"])
        elif action == "measure_fail":
            self._run_measure_command(["fail"])
        elif action == "measure_cancel":
            self._run_measure_command(["cancel"])
        elif action == "rnd_start":
            self._window.rnd.start_measurement()
        elif action == "rnd_save_session":
            if not self._window.rnd.save_session():
                raise RuntimeError("R&D session save canceled.")
        elif action == "rnd_load_session":
            self._window.rnd.load_session()
        elif action == "rnd_export_selected":
            self._window.rnd.export_selected()
        elif action == "rnd_send_to_curator":
            self._window.rnd.send_rnd_to_curator()
        elif action == "curator_send_measure":
            self._window.rnd.send_to_curator()
        elif action == "curator_command":
            self._run_curator_command(shlex.split(target or value))
        elif action == "curator_export_png":
            self._window._curator_widget.export_png(target or value)
            self.trigger("export_complete")
        elif action == "export_average":
            self._window.measure_io.export_average(target or None)
            self.trigger("export_complete")
        elif action == "export_variation":
            self._window.measure_io.export_variation(target or None)
            self.trigger("export_complete")
        elif action == "export_log":
            self._export_console_log(target or None)
            self.trigger("export_complete")
        else:
            raise ValueError(f"Unsupported automation action: {action}")

    @staticmethod
    def _expand_automation_text(text: str, variables: dict[str, object]) -> str:
        """Replace every ``{name}`` placeholder in one pass.

        Substituting one variable at a time meant a value that itself contained
        ``{other}`` was expanded again by a later variable, so the result
        depended on dictionary order. Unknown names are left as written.
        """
        lookup = {str(key): value for key, value in variables.items()}

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key in lookup:
                return str(lookup[key])
            return match.group(0)

        return _AUTOMATION_VARIABLE_PATTERN.sub(replace, str(text or ""))

    def _automation_navigate(self, target: str) -> None:
        normalized = target.strip().lower()
        labels = {
            "measure": "Measure",
            "r&d": "R&&D",
            "rnd": "R&&D",
            "curator": "Curator",
            "automation": "Automation",
            "console": "Automation",
            "settings": "Settings",
        }
        label = labels.get(normalized, target)
        for index in range(self._window._tabs.count()):
            if self._window._tabs.tabText(index) == label:
                self._window._tabs.setCurrentIndex(index)
                return
        raise ValueError(f"Automation tab target not found: {target}")

    def run_console_command(self, command: str) -> None:
        echo = command
        if any(word in command.lower() for word in ("password", "credential", "secret", "token")):
            echo = "<redacted command>"
        self._window._log_event("COMMAND", "console", f"> {echo}")
        try:
            args = shlex.split(command)
        except ValueError as exc:
            self._command_reply(f"Parse error: {exc}", error=True)
            return
        if not args:
            return
        args[0] = args[0].lower()
        try:
            if args == ["help"]:
                self._command_reply(self._console_help())
            elif args == ["clear"]:
                self._window._console_events.clear()
                self._command_reply("Console cleared.")
            elif args == ["status"]:
                self._command_reply(self._console_status())
            elif args == ["devices"]:
                self._command_reply(self._window.devices.console_devices())
            elif args[0] == "settings":
                self._run_settings_command(args[1:])
            elif args == ["diagnostics", "system"]:
                self._window._log_event(
                    "INFO",
                    "diagnostics",
                    "System information",
                    session_id=self._window._console_events.session_id,
                    persistent_log=str(self._window._console_events.log_path),
                    **runtime_diagnostics(),
                )
            elif args == ["diagnostics", "last"]:
                if self._window.measure.queue.last_diagnostics is None:
                    self._command_reply("No measurement diagnostics are available yet.")
                else:
                    self._command_reply(
                        format_diagnostics_summary(self._window.measure.queue.last_diagnostics)
                    )
            elif args[0] == "measure":
                self._run_measure_command(args[1:])
            elif args[0] == "export":
                self._run_export_command(args[1:])
            elif args[0] == "curator":
                try:
                    self._run_curator_command(args[1:])
                except Exception as exc:
                    self._window._log_event(
                        "ERROR",
                        "curator",
                        "Curator command failed",
                        command=" ".join(args[1:]),
                        error=str(exc),
                    )
                    raise
            else:
                self._command_reply(
                    "Unknown command. Type 'help' for available commands.", error=True
                )
        except Exception as exc:
            self._command_reply(f"Command failed: {exc}", error=True)

    @staticmethod
    def _console_help() -> str:
        return "\n".join(
            (
                "Commands:",
                "  help | clear | status | devices | diagnostics system | diagnostics last",
                "  settings list | settings get <name> | settings set <name> <value>",
                "  settings save [<name>|all]",
                "  measure start [count] [level_db] | measure pass | measure fail | measure cancel",
                "  measure session save|load <path> | measure target <path>|clear | measure eq [max_filters]",
                "  export average [path] | export variation [path] | export squiglink | export log [path]",
                "  curator help  (Curator workspace commands)",
            )
        )

    def _console_status(self) -> str:
        return "\n".join(
            (
                f"State: {self._window.measure.queue.state.value}",
                f"Queue: {self._window.measure.queue.index}/{self._window.measure.queue.target or 0}",
                f"Kept curves: {len(self._window.measure.kept_curves)}",
                f"Curator layers: {len(self._window._curator_widget.graph_state.layers)} "
                f"({sum(layer.visible for layer in self._window._curator_widget.graph_state.layers)} visible)",
                f"Output: {self._window.devices.current_output_device_label() or 'none'}",
                f"Input: {self._window.devices.current_input_device_label() or 'none'} / channel {self._window.devices.current_input_channel() + 1}",
                f"Bluetooth mode: {bool(self._window._settings.get('bluetooth_headphone_mode'))}",
                f"Sweep: {self._window._settings.get('sweep_duration')} s @ {self._window._settings.get('sample_rate')} Hz, buffer {self._window._settings.get('buffer_size')}",
            )
        )

    def _run_settings_command(self, args: list[str]) -> None:
        if args == ["list"]:
            overrides = self._window._settings.session_overrides()
            lines = []
            for name in _CONSOLE_SETTING_SPECS:
                key = _CONSOLE_SETTING_KEYS.get(name, name)
                suffix = " (session)" if key in overrides else ""
                value = (
                    self._window.measure_tab.queue_level_spin.value()
                    if name == "output_level"
                    else self._window._settings.get(key)
                )
                lines.append(f"{name} = {value}{suffix}")
            self._command_reply("\n".join(lines))
            return
        if len(args) == 2 and args[0] == "get":
            name = args[1].lower()
            if name not in _CONSOLE_SETTING_SPECS:
                raise ValueError(f"Unknown editable setting: {name}")
            key = _CONSOLE_SETTING_KEYS.get(name, name)
            value = (
                self._window.measure_tab.queue_level_spin.value()
                if name == "output_level"
                else self._window._settings.get(key)
            )
            session = " (session)" if key in self._window._settings.session_overrides() else ""
            self._command_reply(f"{name} = {value}{session}")
            return
        if len(args) == 3 and args[0] == "set":
            if self._window.measure.queue.state != QueueState.IDLE:
                raise ValueError("Settings can only be changed while idle.")
            name = args[1].lower()
            value = self._parse_console_setting(name, args[2])
            self._set_console_setting(name, value)
            self._command_reply(f"Session setting applied: {name} = {value}")
            self._window._log_event(
                "INFO", "settings", "Session setting changed", name=name, value=value
            )
            return
        if args and args[0] == "save" and len(args) <= 2:
            requested = args[1].lower() if len(args) == 2 else "all"
            if requested == "all":
                saved = self._window._settings.save_session()
            else:
                if requested not in _CONSOLE_SETTING_SPECS:
                    raise ValueError(f"Unknown editable setting: {requested}")
                if requested == "bluetooth_mode":
                    bluetooth_keys = [
                        "bluetooth_headphone_mode",
                        PROFILE_SNAPSHOT_SETTING,
                        *BLUETOOTH_PROFILE_DEFAULTS,
                    ]
                    saved = []
                    for key in bluetooth_keys:
                        saved.extend(self._window._settings.save_session(key))
                else:
                    saved = self._window._settings.save_session(
                        _CONSOLE_SETTING_KEYS.get(requested, requested)
                    )
            if not saved:
                self._command_reply("No matching session overrides to save.")
            else:
                if "queue_output_level_db" in saved:
                    self._window._settings.set("queue_output_level_persist", True)
                    self._window.measure_tab.queue_level_persist_toggle.blockSignals(True)
                    self._window.measure_tab.queue_level_persist_toggle.setChecked(True)
                    self._window.measure_tab.queue_level_persist_toggle.blockSignals(False)
                self._command_reply("Saved settings: " + ", ".join(saved))
                self._window._log_event(
                    "INFO", "settings", "Session settings persisted", keys=saved
                )
            return
        raise ValueError("Usage: settings list|get <name>|set <name> <value>|save [<name>|all]")

    @staticmethod
    def _parse_console_setting(name: str, raw: str):
        if name not in _CONSOLE_SETTING_SPECS:
            raise ValueError(f"Unknown editable setting: {name}")
        spec = _CONSOLE_SETTING_SPECS[name]
        kind = spec[0]
        if kind == "bool":
            lowered = raw.lower()
            if lowered not in {"true", "false", "on", "off", "1", "0"}:
                raise ValueError(f"{name} expects true or false")
            return lowered in {"true", "on", "1"}
        if kind == "choice":
            choices = spec[1]
            value = int(raw) if all(isinstance(item, int) for item in choices) else raw.lower()
            if value not in choices:
                raise ValueError(f"{name} must be one of: {', '.join(map(str, sorted(choices)))}")
            return value
        value = int(raw) if kind == "int" else float(raw)
        if value < spec[1] or value > spec[2]:
            raise ValueError(f"{name} must be between {spec[1]} and {spec[2]}")
        return value

    def _set_console_setting(self, name: str, value) -> None:
        key = _CONSOLE_SETTING_KEYS.get(name, name)
        if name == "bluetooth_mode":
            self._window.devices.set_console_bluetooth_mode(bool(value))
            return
        self._window._settings.set_session(key, value)
        if name == "queue_count":
            self._window.measure_tab.queue_n_spin.blockSignals(True)
            self._window.measure_tab.queue_n_spin.setValue(int(value))
            self._window.measure_tab.queue_n_spin.blockSignals(False)
        elif name == "output_level":
            self._window.measure_tab.queue_level_spin.blockSignals(True)
            self._window.measure_tab.queue_level_spin.setValue(float(value))
            self._window.measure_tab.queue_level_spin.blockSignals(False)
        if name in {"sample_rate", "buffer_size"}:
            self._window.devices.start_level_monitor()
        self._window._settings_widget.refresh_from_settings()

    def _run_measure_command(self, args: list[str]) -> None:
        if args and args[0] == "start" and len(args) <= 3:
            if self._window.measure.queue.state != QueueState.IDLE:
                raise ValueError("A measurement can only be started while idle.")
            if (
                self._window.measure.channel_balance_mode_active()
                or self._window.measure.channel_balance_active
            ):
                raise ValueError(
                    "Switch to Frequency Response and stop Channel Balance before "
                    "starting a measurement."
                )
            if len(args) >= 2:
                count = self._parse_console_setting("queue_count", args[1])
                self._window._settings.set_session("queue_count", count)
                self._window.measure_tab.queue_n_spin.blockSignals(True)
                self._window.measure_tab.queue_n_spin.setValue(int(count))
                self._window.measure_tab.queue_n_spin.blockSignals(False)
            if len(args) == 3:
                level = self._parse_console_setting("output_level", args[2])
                self._window._settings.set_session("queue_output_level_db", level)
                self._window.measure_tab.queue_level_spin.blockSignals(True)
                self._window.measure_tab.queue_level_spin.setValue(float(level))
                self._window.measure_tab.queue_level_spin.blockSignals(False)
            self._window.measure.start_queue()
            return
        if args == ["pass"]:
            if (
                self._window.measure.queue.state != QueueState.PASS_FAIL
                or self._window.measure.queue.pending_curve is None
            ):
                raise ValueError("There is no measurement awaiting review.")
            self._window._log_event("INFO", "review", "Measurement passed from console")
            self._window.measure.on_keep()
            return
        if args == ["fail"]:
            if (
                self._window.measure.queue.state != QueueState.PASS_FAIL
                or self._window.measure.queue.pending_curve is None
            ):
                raise ValueError("There is no measurement awaiting review.")
            self._window._log_event("WARNING", "review", "Measurement failed from console")
            self._window.measure.on_fail()
            return
        if args == ["cancel"]:
            if (
                self._window.measure.queue.state == QueueState.IDLE
                and not self._window.measure.queue_active()
            ):
                raise ValueError("There is no active measurement queue to cancel.")
            self._window.measure.cancel_queue()
            return
        if args[:1] == ["session"] and len(args) == 3:
            action = args[1].lower()
            if action == "save":
                path = ensure_extension(Path(args[2]).expanduser(), MEASURE_SESSION_EXTENSION)
                save_measure_session(self._window.measure_io.current_session(), path)
                self._window.measure_io.session_path = path
                self._window.measure_io.clear_dirty()
                self._command_reply(f"Saved Measure session: {path}")
                return
            if action == "load":
                if not self._window.measure_io.load_session(str(Path(args[2]).expanduser())):
                    raise ValueError("The Measure session was not loaded.")
                return
            raise ValueError("Usage: measure session save|load <path>")
        if args[:1] == ["target"] and len(args) == 2:
            if args[1].lower() == "clear":
                self._window.measure_compare.clear_target()
                self._command_reply("Target cleared.")
                return
            if not self._window.measure_compare.load_target(str(Path(args[1]).expanduser())):
                raise ValueError("The target curve was not loaded.")
            self._command_reply(f"Target loaded: {args[1]}")
            return
        if args[:1] == ["eq"] and len(args) <= 2:
            average = self._window.measure.bottom_curve_for_display_and_export()
            if self._window.measure_compare._measure_target is None:
                raise ValueError("Load a target curve first: measure target <path>")
            if average is None:
                raise ValueError("No averaged measurement is available yet.")
            max_filters = int(args[1]) if len(args) == 2 else 8
            if not 1 <= max_filters <= 10:
                raise ValueError("measure eq accepts 1 to 10 filters.")
            from dms.comparison import format_eq_apo, suggest_eq

            delta = self._window.measure_compare.delta_result(average)
            self._command_reply(format_eq_apo(suggest_eq(delta, max_filters=max_filters)))
            return
        raise ValueError(
            "Usage: measure start [count] [level_db]|pass|fail|cancel"
            " | measure session save|load <path> | measure target <path>|clear"
            " | measure eq [max_filters]"
        )

    def _run_export_command(self, args: list[str]) -> None:
        if not args:
            raise ValueError("Usage: export average|variation|squiglink|log [path]")
        kind = args[0].lower()
        path = args[1] if len(args) == 2 else None
        if len(args) > 2:
            raise ValueError("Export paths containing spaces must be quoted.")
        if kind == "average":
            if self._window.measure.queue.state != QueueState.IDLE:
                raise ValueError("Average export is only available while idle.")
            if self._window.measure.bottom_curve_for_display_and_export() is None:
                raise ValueError("No averaged curve is available yet.")
            self._window.measure_io.export_average(path)
        elif kind == "variation":
            if self._window.measure.queue.state != QueueState.IDLE:
                raise ValueError("Variation export is only available while idle.")
            if self._window.measure.variation is None:
                raise ValueError("No variation band is available yet.")
            self._window.measure_io.export_variation(path)
        elif kind == "squiglink" and path is None:
            if self._window.measure.queue.state != QueueState.IDLE:
                raise ValueError("Squiglink upload is only available while idle.")
            self._window.squiglink.upload()
        elif kind == "log":
            self._export_console_log(path)
        else:
            raise ValueError("Usage: export average|variation|squiglink|log [path]")

    @staticmethod
    def _curator_help() -> str:
        return "\n".join(
            (
                "Curator commands:",
                "  curator status | curator layers | curator send",
                "  curator import <path> [<path>...]",
                "  curator layer <n> show|hide|remove",
                "  curator layer <n> offset <db> | color <#RRGGBB> | hrtf <name|none>",
                "  curator combine <n> <n> [...] | curator clear",
                "  curator bounds on|off",
                "  curator view limits <min_db> <max_db> | aspect on|off",
                "  curator view background <#RRGGBB|theme>",
                "  curator text title|fixture|footer <text>",
                "  curator reset | curator export <path>",
            )
        )

    @staticmethod
    def _console_on_off(value: str) -> bool:
        lowered = value.lower()
        if lowered not in {"on", "off"}:
            raise ValueError("Expected on or off.")
        return lowered == "on"

    def _run_curator_command(self, args: list[str]) -> None:
        curator = self._window._curator_widget
        if args == ["help"]:
            self._command_reply(self._curator_help())
            return
        if args == ["status"]:
            layers = curator.graph_state.layers
            self._command_reply(
                "\n".join(
                    (
                        f"Layers: {len(layers)}",
                        f"Visible: {sum(layer.visible for layer in layers)}",
                        f"Bounds: {'on' if curator.graph_state.bounds.enabled else 'off'}",
                        f"Limits: {curator.graph_state.y_min:g} to {curator.graph_state.y_max:g} dB",
                        f"25 dB/decade: {'on' if curator.graph_state.aspect_locked_25db else 'off'}",
                        f"Background: {curator.graph_state.background}",
                    )
                )
            )
            return
        if args == ["layers"]:
            self._command_reply(curator.layer_summary())
            return
        if args == ["send"]:
            self._window.rnd.send_to_curator()
            return
        if args and args[0] == "import" and len(args) >= 2:
            paths = [Path(raw).expanduser() for raw in args[1:]]
            for path in paths:
                if not path.exists() or not path.is_file():
                    raise ValueError(f"Import file does not exist: {path}")
            parsed = [(path, parse_measurement_txt(path)) for path in paths]
            for path, curve in parsed:
                curator.add_curve(curve, path.stem, source_path=path, normalize=True)
            self._command_reply(f"Imported {len(parsed)} Curator file(s).")
            return
        if args and args[0] == "layer" and len(args) >= 3:
            try:
                number = int(args[1])
            except ValueError as exc:
                raise ValueError("Layer number must be an integer.") from exc
            action = args[2].lower()
            if len(args) == 3 and action in {"show", "hide"}:
                curator.set_layer_number_visible(number, action == "show")
            elif len(args) == 3 and action == "remove":
                curator.remove_layer_number(number)
            elif len(args) == 4 and action == "offset":
                curator.set_layer_number_offset(number, float(args[3]))
            elif len(args) == 4 and action == "color":
                curator.set_layer_number_color(number, args[3])
            elif len(args) >= 4 and action == "hrtf":
                curator.set_layer_number_hrtf(number, " ".join(args[3:]))
            else:
                raise ValueError(
                    "Usage: curator layer <n> show|hide|remove|offset <db>|"
                    "color <#RRGGBB>|hrtf <name|none>"
                )
            self._command_reply(f"Curator layer {number} updated.")
            return
        if args and args[0] == "combine" and len(args) >= 3:
            try:
                numbers = [int(value) for value in args[1:]]
            except ValueError as exc:
                raise ValueError("Combine expects integer layer numbers.") from exc
            layer = curator.combine_layer_numbers(numbers)
            self._command_reply(
                f"Created Curator layer {len(curator.graph_state.layers)}: {layer.name}"
            )
            return
        if args == ["clear"]:
            curator.clear_layers()
            self._command_reply("Curator layers cleared.")
            return
        if len(args) == 2 and args[0] == "bounds":
            curator.set_bounds_enabled(self._console_on_off(args[1]))
            self._command_reply(f"Curator bounds {args[1].lower()}.")
            return
        if len(args) == 4 and args[:2] == ["view", "limits"]:
            curator.set_y_limits(float(args[2]), float(args[3]))
            self._command_reply(f"Curator limits set to {args[2]}..{args[3]} dB.")
            return
        if len(args) == 3 and args[:2] == ["view", "aspect"]:
            curator.set_aspect_locked(self._console_on_off(args[2]))
            self._command_reply(f"Curator aspect lock {args[2].lower()}.")
            return
        if len(args) == 3 and args[:2] == ["view", "background"]:
            if args[2].lower() == "theme":
                curator.reset_background_to_theme()
            else:
                curator.set_background(args[2])
            self._command_reply(f"Curator background set to {curator.graph_state.background}.")
            return
        if len(args) >= 3 and args[0] == "text" and args[1] in {"title", "fixture", "footer"}:
            curator.set_export_text(args[1], " ".join(args[2:]))
            self._command_reply(f"Curator {args[1]} updated.")
            return
        if args == ["reset"]:
            curator.reset_view()
            self._command_reply("Curator view reset.")
            return
        if len(args) == 2 and args[0] == "export":
            path = curator.export_png(args[1])
            self._command_reply(f"Exported Curator PNG: {path}")
            return
        raise ValueError("Unknown Curator command. Type 'curator help' for available commands.")

    def _export_console_log(self, requested_path: str | None = None) -> None:
        path = self._window.measure_io.resolve_export_path(
            requested_path,
            "fastgraph-console.log",
            "Export Console Log",
            "Log Files (*.log *.txt);;All Files (*)",
        )
        if path is None:
            return
        self._window._console_events.export(path)
        self._window._log_event("INFO", "export", "Console log exported", path=str(path))
        self.trigger("export_complete")
