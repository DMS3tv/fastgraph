from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from dms.automation import (
    ACTIONS,
    CONDITIONS,
    TRIGGERS,
    AUTOMATION_SUFFIX,
    AutomationCondition,
    AutomationDefinition,
    AutomationStep,
    load_automation,
    safe_automation_filename,
    save_automation,
    scan_automation_directory,
)
from dms.ui.console_widget import ConsoleWidget


class AutomationGuideWidget(QWidget):
    close_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("Automation Guide")
        title.setProperty("role", "sectionTitle")
        header.addWidget(title, 1)
        close_btn = QPushButton("Close Guide")
        close_btn.clicked.connect(self.close_requested)
        header.addWidget(close_btn)
        layout.addLayout(header)

        guide = QTextBrowser()
        guide.setOpenExternalLinks(False)
        guide.setHtml(
            """
            <h2>Events Cheat-Sheet</h2>
            <p>Automations are saved workflows made from rows. Each row reads like:
            <b>If</b> a condition is true, run an <b>Action</b> against a
            <b>Target</b> using the optional <b>Value</b>.</p>

            <h3>Common Row Pieces</h3>
            <ul>
              <li><b>When</b>: manual, app start, measurement kept, queue complete,
              R&amp;D measurement kept, export complete, or app error.</li>
              <li><b>If</b>: leave as always, or check a variable, app state, or
              measurement count before the row runs.</li>
              <li><b>Target</b>: the tab, command, device label, channel number,
              variable name, message text, or export target for the action.</li>
              <li><b>Value</b>: the text or number the action should use. You can
              reuse variables with braces, such as <code>{answer}</code>.</li>
              <li><b>Confirm / Skip</b>: risky steps normally ask first. Check Skip
              only for automations you trust.</li>
            </ul>

            <h3>Useful Actions</h3>
            <ul>
              <li><b>navigate</b>: switch to Measure, R&amp;D, Curator, Automation,
              or Settings.</li>
              <li><b>switch_input_device</b>: target a saved input device label or
              index. This only runs while Fastgraph is idle.</li>
              <li><b>switch_input_channel</b>: target a channel number or channel
              label. This also only runs while idle.</li>
              <li><b>console_command</b>: run a safe command from the Automation
              console, such as <code>status</code> or <code>devices</code>.</li>
              <li><b>prompt_yes_no</b>: ask a question and store yes/no in the
              variable named by Target.</li>
              <li><b>set_variable</b>, <b>increment_variable</b>, and
              <b>clear_variable</b>: keep temporary state during one run.</li>
              <li><b>rnd_start</b>, <b>rnd_save_session</b>,
              <b>rnd_export_selected</b>, and <b>rnd_send_to_curator</b>: run common
              R&amp;D actions.</li>
            </ul>

            <h3>Example: Prep A Measurement</h3>
            <ol>
              <li><b>always</b> / <b>navigate</b> / Target: <code>measure</code></li>
              <li><b>always</b> / <b>switch_input_device</b> / Target:
              <code>Your interface name</code></li>
              <li><b>always</b> / <b>switch_input_channel</b> / Target:
              <code>2</code></li>
              <li><b>always</b> / <b>console_command</b> / Target:
              <code>status</code></li>
            </ol>

            <h3>Example: Ask Before Exporting</h3>
            <ol>
              <li><b>always</b> / <b>prompt_yes_no</b> / Target:
              <code>do_export</code> / Value: <code>Export the current average?</code></li>
              <li><b>variable_is_true</b> / Left: <code>do_export</code> /
              <b>export_average</b></li>
              <li><b>variable_is_true</b> / Left: <code>do_export</code> /
              <b>prompt_info</b> / Target: <code>Export finished.</code></li>
            </ol>

            <h3>Example: R&amp;D Keep Follow-Up</h3>
            <ol>
              <li>Set <b>When</b> to <code>rnd_measurement_kept</code>.</li>
              <li><b>always</b> / <b>prompt_info</b> / Target:
              <code>R&amp;D measurement was kept.</code></li>
              <li><b>always</b> / <b>rnd_save_session</b></li>
            </ol>

            <h3>Safety Notes</h3>
            <p>Measurement, review, file overwrite, load/clear, and upload-style
            actions are treated as risky. Fastgraph stops an automation if a step
            asks for hardware, a channel, or an app state that is not available.</p>
            """
        )
        layout.addWidget(guide, 1)


class EventsWidget(QWidget):
    run_requested = pyqtSignal(object)
    status_message = pyqtSignal(str)

    def __init__(self, directory_provider, version_provider, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._directory_provider = directory_provider
        self._version_provider = version_provider
        self._current_path: Path | None = None
        self._automation = AutomationDefinition()
        self._syncing = False
        self._build_ui()
        self.reload_library()
        self._load_into_editor(self._automation, None)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        library_box = QGroupBox("Events Library")
        library_layout = QVBoxLayout(library_box)
        self._library = QListWidget()
        self._library.currentRowChanged.connect(self._load_selected_library_item)
        library_layout.addWidget(self._library, 1)
        library_buttons = QHBoxLayout()
        reload_btn = QPushButton("Reload Library")
        reload_btn.clicked.connect(self.reload_library)
        library_buttons.addWidget(reload_btn)
        new_btn = QPushButton("New")
        new_btn.clicked.connect(self.new_automation)
        library_buttons.addWidget(new_btn)
        duplicate_btn = QPushButton("Duplicate")
        duplicate_btn.clicked.connect(self.duplicate_automation)
        library_buttons.addWidget(duplicate_btn)
        library_layout.addLayout(library_buttons)
        root.addWidget(library_box, 1)

        meta_box = QGroupBox("Automation")
        form = QFormLayout(meta_box)
        self._name = QLineEdit()
        self._name.textChanged.connect(self._update_model_from_meta)
        form.addRow("Name", self._name)
        self._enabled = QCheckBox("Enabled for app events")
        self._enabled.toggled.connect(self._update_model_from_meta)
        form.addRow("", self._enabled)
        self._trigger = QComboBox()
        self._trigger.addItems(TRIGGERS)
        self._trigger.currentTextChanged.connect(self._update_model_from_meta)
        form.addRow("When", self._trigger)
        self._description = QTextEdit()
        self._description.setMaximumHeight(70)
        self._description.textChanged.connect(self._update_model_from_meta)
        form.addRow("Notes", self._description)
        root.addWidget(meta_box)

        steps_box = QGroupBox("Event Rows")
        steps_layout = QVBoxLayout(steps_box)
        self._steps = QTableWidget(0, 6)
        self._steps.setHorizontalHeaderLabels(["If", "Left", "Action", "Target", "Value", "Confirm"])
        self._steps.horizontalHeader().setStretchLastSection(False)
        self._steps.setColumnWidth(0, 132)
        self._steps.setColumnWidth(1, 120)
        self._steps.setColumnWidth(2, 150)
        self._steps.setColumnWidth(3, 165)
        self._steps.setColumnWidth(4, 170)
        self._steps.setColumnWidth(5, 84)
        steps_layout.addWidget(self._steps, 1)
        step_buttons = QHBoxLayout()
        add_step = QPushButton("Add Row")
        add_step.clicked.connect(self.add_step)
        step_buttons.addWidget(add_step)
        remove_step = QPushButton("Delete Row")
        remove_step.clicked.connect(self.remove_selected_step)
        step_buttons.addWidget(remove_step)
        up_step = QPushButton("Move Up")
        up_step.clicked.connect(lambda: self.move_selected_step(-1))
        step_buttons.addWidget(up_step)
        down_step = QPushButton("Move Down")
        down_step.clicked.connect(lambda: self.move_selected_step(1))
        step_buttons.addWidget(down_step)
        steps_layout.addLayout(step_buttons)
        root.addWidget(steps_box, 2)

        action_buttons = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.save_current)
        action_buttons.addWidget(save_btn)
        save_as_btn = QPushButton("Save As")
        save_as_btn.clicked.connect(lambda: self.save_current(force_new=True))
        action_buttons.addWidget(save_as_btn)
        run_btn = QPushButton("Run")
        run_btn.setObjectName("btn_export")
        run_btn.clicked.connect(lambda: self.run_requested.emit(self.current_automation()))
        action_buttons.addWidget(run_btn)
        root.addLayout(action_buttons)
        self._status = QLabel("")
        self._status.setProperty("tone", "muted")
        root.addWidget(self._status)

    def automation_directory(self) -> Path:
        return Path(self._directory_provider()).expanduser()

    def reload_library(self) -> None:
        self._library.clear()
        self._library_items, skipped = scan_automation_directory(
            self.automation_directory()
        )
        for path, automation in self._library_items:
            self._library.addItem(f"{automation.name} ({path.name})")
        status = f"Loaded {len(self._library_items)} automation file(s)"
        if skipped:
            reasons = "; ".join(f"{path.name} ({reason})" for path, reason in skipped)
            status += f"; {len(skipped)} failed to load: {reasons}"
        self._set_status(status + ".")

    def new_automation(self) -> None:
        self._load_into_editor(AutomationDefinition(), None)

    def duplicate_automation(self) -> None:
        current = self.current_automation()
        current.id = ""
        copy = AutomationDefinition.from_dict(current.to_dict() | {"id": "", "name": current.name + " Copy"})
        self._load_into_editor(copy, None)

    def _load_selected_library_item(self, row: int) -> None:
        if self._syncing or row < 0 or row >= len(getattr(self, "_library_items", [])):
            return
        path, automation = self._library_items[row]
        self._load_into_editor(automation, path)

    def _load_into_editor(self, automation: AutomationDefinition, path: Path | None) -> None:
        self._syncing = True
        try:
            self._automation = automation
            self._current_path = path
            self._name.setText(automation.name)
            self._enabled.setChecked(automation.enabled)
            self._trigger.setCurrentText(automation.trigger)
            self._description.setPlainText(automation.description)
            self._steps.setRowCount(0)
            for step in automation.steps:
                self._insert_step_row(step)
        finally:
            self._syncing = False
        self._set_status("Ready")

    def _update_model_from_meta(self, *_args) -> None:
        if self._syncing:
            return
        self._automation.name = self._name.text().strip() or "New Automation"
        self._automation.enabled = self._enabled.isChecked()
        self._automation.trigger = self._trigger.currentText()
        self._automation.description = self._description.toPlainText()

    def add_step(self) -> None:
        self._insert_step_row(AutomationStep())

    def remove_selected_step(self) -> None:
        row = self._steps.currentRow()
        if row >= 0:
            self._steps.removeRow(row)

    def move_selected_step(self, delta: int) -> None:
        row = self._steps.currentRow()
        if row < 0:
            return
        new_row = max(0, min(self._steps.rowCount() - 1, row + delta))
        if new_row == row:
            return
        steps = self._steps_from_table()
        steps[row], steps[new_row] = steps[new_row], steps[row]
        self._steps.setRowCount(0)
        for step in steps:
            self._insert_step_row(step)
        self._steps.selectRow(new_row)

    def _insert_step_row(self, step: AutomationStep) -> None:
        row = self._steps.rowCount()
        self._steps.insertRow(row)
        condition = QComboBox()
        condition.addItems(CONDITIONS)
        condition.setCurrentText(step.condition.kind)
        self._steps.setCellWidget(row, 0, condition)
        self._steps.setItem(row, 1, QTableWidgetItem(step.condition.left))
        action = QComboBox()
        action.addItems(ACTIONS)
        action.setCurrentText(step.action)
        self._steps.setCellWidget(row, 2, action)
        self._steps.setItem(row, 3, QTableWidgetItem(step.target))
        self._steps.setItem(row, 4, QTableWidgetItem(step.value))
        confirm = QCheckBox()
        confirm.setToolTip("Skip risky-action confirmation for this step")
        confirm.setChecked(step.skip_risky_confirmation)
        confirm.setText("Skip")
        self._steps.setCellWidget(row, 5, confirm)

    def _steps_from_table(self) -> list[AutomationStep]:
        steps: list[AutomationStep] = []
        for row in range(self._steps.rowCount()):
            condition_widget = self._steps.cellWidget(row, 0)
            action_widget = self._steps.cellWidget(row, 2)
            confirm_widget = self._steps.cellWidget(row, 5)
            condition = AutomationCondition(
                kind=condition_widget.currentText() if isinstance(condition_widget, QComboBox) else "always",
                left=self._item_text(row, 1),
                value=self._item_text(row, 4),
            )
            steps.append(AutomationStep(
                action=action_widget.currentText() if isinstance(action_widget, QComboBox) else "navigate",
                target=self._item_text(row, 3),
                value=self._item_text(row, 4),
                condition=condition,
                skip_risky_confirmation=(
                    confirm_widget.isChecked() if isinstance(confirm_widget, QCheckBox) else False
                ),
            ))
        return steps

    def _item_text(self, row: int, column: int) -> str:
        item = self._steps.item(row, column)
        return item.text().strip() if item is not None else ""

    def current_automation(self) -> AutomationDefinition:
        self._update_model_from_meta()
        self._automation.steps = self._steps_from_table()
        return self._automation

    def save_current(self, *, force_new: bool = False) -> Path:
        automation = self.current_automation()
        automation.updated_app_version = str(self._version_provider())
        if not automation.created_app_version:
            automation.created_app_version = automation.updated_app_version
        path = None if force_new else self._current_path
        if path is None:
            path = self.automation_directory() / safe_automation_filename(automation.name)
        save_automation(path, automation)
        self._current_path = path
        self.reload_library()
        self._set_status(f"Saved: {path}")
        return path

    def load_file(self, path: Path) -> None:
        self._load_into_editor(load_automation(path), path)

    def automations_for_trigger(self, trigger: str) -> list[AutomationDefinition]:
        loaded, _skipped = scan_automation_directory(self.automation_directory())
        return [
            automation
            for _path, automation in loaded
            if automation.enabled and automation.trigger == trigger
        ]

    def _set_status(self, text: str) -> None:
        self._status.setText(text)
        self.status_message.emit(text)


class AutomationWidget(QWidget):
    run_requested = pyqtSignal(object)

    def __init__(
        self,
        console: ConsoleWidget,
        directory_provider,
        version_provider,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 8, 8, 4)
        toolbar.addStretch(1)
        self.guide_button = QPushButton("Guide")
        self.guide_button.setObjectName("btn_danger")
        self.guide_button.setCheckable(True)
        self.guide_button.toggled.connect(self._set_guide_visible)
        toolbar.addWidget(self.guide_button)
        layout.addLayout(toolbar)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.console = console
        self.guide = AutomationGuideWidget()
        self.guide.close_requested.connect(lambda: self.guide_button.setChecked(False))
        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(console)
        self.left_stack.addWidget(self.guide)
        self.events = EventsWidget(directory_provider, version_provider)
        self.events.run_requested.connect(self.run_requested)
        self.splitter.addWidget(self.left_stack)
        self.splitter.addWidget(self.events)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        layout.addWidget(self.splitter, 1)

    def _set_guide_visible(self, visible: bool) -> None:
        self.left_stack.setCurrentWidget(self.guide if visible else self.console)
        self.guide_button.setText("Close Guide" if visible else "Guide")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.splitter.width() > 0:
            half = max(1, self.splitter.width() // 2)
            self.splitter.setSizes([half, half])
