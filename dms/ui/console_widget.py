"""Interactive diagnostics console UI."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontDatabase, QKeyEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dms.console import ConsoleEvent, ConsoleEventStore


class HistoryLineEdit(QLineEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._history: list[str] = []
        self._history_index = 0

    def remember(self, command: str) -> None:
        if command and (not self._history or self._history[-1] != command):
            self._history.append(command)
        self._history_index = len(self._history)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Up and self._history:
            self._history_index = max(0, self._history_index - 1)
            self.setText(self._history[self._history_index])
            return
        if event.key() == Qt.Key.Key_Down and self._history:
            self._history_index = min(len(self._history), self._history_index + 1)
            self.setText(
                "" if self._history_index == len(self._history)
                else self._history[self._history_index]
            )
            return
        super().keyPressEvent(event)


class ConsoleWidget(QWidget):
    command_submitted = pyqtSignal(str)

    def __init__(self, store: ConsoleEventStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._build_ui()
        store.event_added.connect(self._on_event_added)
        store.cleared.connect(self._refresh)
        self._refresh_sources()
        self._refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        tools = QHBoxLayout()
        tools.addWidget(QLabel("Level:"))
        self._level = QComboBox()
        self._level.addItems(["All", "DEBUG", "INFO", "WARNING", "ERROR", "COMMAND"])
        self._level.currentIndexChanged.connect(self._refresh)
        tools.addWidget(self._level)
        tools.addWidget(QLabel("Source:"))
        self._source = QComboBox()
        self._source.addItem("All")
        self._source.currentIndexChanged.connect(self._refresh)
        tools.addWidget(self._source)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search console…")
        self._search.textChanged.connect(self._refresh)
        tools.addWidget(self._search, 1)
        self._auto_scroll = QCheckBox("Auto-scroll")
        self._auto_scroll.setChecked(True)
        tools.addWidget(self._auto_scroll)
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self._copy)
        tools.addWidget(copy_btn)
        save_btn = QPushButton("Save As…")
        save_btn.clicked.connect(self._save_as)
        tools.addWidget(save_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._store.clear)
        tools.addWidget(clear_btn)
        layout.addLayout(tools)

        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._output.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        layout.addWidget(self._output, 1)

        command_row = QHBoxLayout()
        command_row.addWidget(QLabel(">"))
        self._command = HistoryLineEdit()
        self._command.setPlaceholderText("Type 'help' for available commands")
        self._command.returnPressed.connect(self._submit)
        command_row.addWidget(self._command, 1)
        run_btn = QPushButton("Run")
        run_btn.clicked.connect(self._submit)
        command_row.addWidget(run_btn)
        layout.addLayout(command_row)

    def focus_command(self) -> None:
        self._command.setFocus()

    def filtered_events(self) -> list[ConsoleEvent]:
        level = self._level.currentText()
        source = self._source.currentText()
        search = self._search.text().strip().lower()
        result: list[ConsoleEvent] = []
        for event in self._store.events():
            if level != "All" and event.severity != level:
                continue
            if source != "All" and event.source != source:
                continue
            if search and search not in event.format().lower():
                continue
            result.append(event)
        return result

    def _refresh_sources(self) -> None:
        current = self._source.currentText()
        sources = sorted({event.source for event in self._store.events()})
        self._source.blockSignals(True)
        self._source.clear()
        self._source.addItem("All")
        self._source.addItems(sources)
        index = self._source.findText(current)
        self._source.setCurrentIndex(index if index >= 0 else 0)
        self._source.blockSignals(False)

    def _refresh(self) -> None:
        self._output.setPlainText(self._store.formatted(self.filtered_events()))
        if self._auto_scroll.isChecked():
            self._output.verticalScrollBar().setValue(
                self._output.verticalScrollBar().maximum()
            )

    def _on_event_added(self, event: ConsoleEvent) -> None:
        if self._source.findText(event.source) < 0:
            self._refresh_sources()
        self._refresh()

    def _submit(self) -> None:
        command = self._command.text().strip()
        if not command:
            return
        self._command.remember(command)
        self._command.clear()
        self.command_submitted.emit(command)

    def _copy(self) -> None:
        cursor = self._output.textCursor()
        text = cursor.selectedText().replace("\u2029", "\n")
        if not text:
            text = self._store.formatted(self.filtered_events())
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)

    def _save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Console Log", "fastgraph-console.log", "Log Files (*.log *.txt);;All Files (*)"
        )
        if not path:
            return
        try:
            self._store.export(Path(path))
            self._store.publish("INFO", "export", "Console log exported", {"path": path})
        except Exception as exc:
            self._store.publish("ERROR", "export", f"Console log export failed: {exc}")
