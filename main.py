import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from dms.ui.session_dialog import SessionDialog
from dms.ui.main_window import MainWindow
from dms.settings_manager import SettingsManager


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("DMS Fastgraph")

    settings = SettingsManager()

    # Apply dark stylesheet
    app.setStyleSheet(_dark_stylesheet())

    # Session form blocks until filled
    session_dialog = SessionDialog(settings)
    if session_dialog.exec() != SessionDialog.DialogCode.Accepted:
        sys.exit(0)

    session = session_dialog.session_data()

    window = MainWindow(session, settings)
    window.show()
    sys.exit(app.exec())


def _dark_stylesheet() -> str:
    return """
    QWidget {
        background-color: #1a1a1a;
        color: #e0e0e0;
        font-family: 'Segoe UI', 'Inter', 'Helvetica Neue', Arial, sans-serif;
        font-size: 13px;
    }
    QMainWindow, QDialog {
        background-color: #1a1a1a;
    }
    QPushButton {
        background-color: #2d2d2d;
        color: #e0e0e0;
        border: 1px solid #444;
        border-radius: 4px;
        padding: 5px 14px;
        min-height: 26px;
    }
    QPushButton:hover { background-color: #383838; }
    QPushButton:pressed { background-color: #222; }
    QPushButton:disabled { color: #555; border-color: #333; }
    QPushButton#btn_keep {
        background-color: #1e5c2a;
        border-color: #2a7a38;
        color: #90ee90;
        font-weight: bold;
    }
    QPushButton#btn_keep:hover { background-color: #26733a; }
    QPushButton#btn_fail {
        background-color: #5c1e1e;
        border-color: #7a2a2a;
        color: #ee9090;
        font-weight: bold;
    }
    QPushButton#btn_fail:hover { background-color: #731e1e; }
    QPushButton#btn_start {
        background-color: #1e3d5c;
        border-color: #2a5a80;
        color: #90c8ee;
        font-weight: bold;
    }
    QPushButton#btn_start:hover { background-color: #245070; }
    QPushButton#btn_cancel {
        background-color: #4a2000;
        border-color: #6a3000;
        color: #ffaa55;
        font-weight: bold;
    }
    QComboBox {
        background-color: #2d2d2d;
        border: 1px solid #444;
        border-radius: 4px;
        padding: 3px 8px;
        min-height: 24px;
    }
    QComboBox::drop-down { border: none; width: 20px; }
    QComboBox QAbstractItemView {
        background-color: #2d2d2d;
        selection-background-color: #3a5a7a;
    }
    QSpinBox, QDoubleSpinBox {
        background-color: #2d2d2d;
        border: 1px solid #444;
        border-radius: 4px;
        padding: 3px 6px;
    }
    QLineEdit {
        background-color: #2d2d2d;
        border: 1px solid #444;
        border-radius: 4px;
        padding: 4px 8px;
    }
    QLabel#label_channel_active {
        color: #6cf;
        font-weight: bold;
        font-size: 14px;
    }
    QLabel#status_label {
        color: #aaa;
        font-style: italic;
    }
    QGroupBox {
        border: 1px solid #3a3a3a;
        border-radius: 6px;
        margin-top: 8px;
        padding-top: 6px;
    }
    QGroupBox::title {
        color: #888;
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
    }
    QScrollArea, QScrollBar { background-color: #1a1a1a; }
    QScrollBar:vertical { width: 8px; }
    QScrollBar::handle:vertical { background: #444; border-radius: 4px; }
    QTabWidget::pane { border: 1px solid #333; }
    QTabBar::tab {
        background: #222;
        padding: 6px 14px;
        border: 1px solid #333;
    }
    QTabBar::tab:selected { background: #2d2d2d; color: #6cf; }
    QCheckBox::indicator {
        width: 14px; height: 14px;
        border: 1px solid #555;
        border-radius: 3px;
        background: #2d2d2d;
    }
    QCheckBox::indicator:checked { background: #3a7abf; }
    """


if __name__ == "__main__":
    main()
