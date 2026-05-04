import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from dms.ui.main_window import MainWindow
from dms.settings_manager import SettingsManager
from dms.session import SessionData
from dms.version import __version__


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("DMS Fastgraph")
    app.setApplicationVersion(__version__)

    settings = SettingsManager()

    # Apply dark stylesheet
    app.setStyleSheet(_dark_stylesheet())

    # Launch directly into main UI; metadata can be edited from a top-level button.
    session = SessionData(
        rig="Unknown Rig",
        brand="Unknown",
        model="Unknown",
    )

    window = MainWindow(session, settings)
    window.show()
    sys.exit(app.exec())


def _dark_stylesheet() -> str:
    return """
    QWidget {
        background-color: #14171c;
        color: #e3e7ee;
        font-family: 'Segoe UI', 'Inter', 'Helvetica Neue', Arial, sans-serif;
        font-size: 13px;
    }
    QMainWindow, QDialog {
        background-color: #14171c;
    }
    QPushButton {
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2f3540, stop:1 #262b34);
        color: #e7edf7;
        border: 1px solid #454e5e;
        border-radius: 8px;
        padding: 6px 14px;
        min-height: 28px;
    }
    QPushButton:hover {
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #394356, stop:1 #2c3340);
        border-color: #5a6982;
    }
    QPushButton:pressed { background-color: #20262f; padding-top: 7px; }
    QPushButton:disabled { color: #6e7785; border-color: #343b46; background-color: #222832; }
    QPushButton#btn_keep {
        background-color: #1d5e33;
        border-color: #2f7f49;
        color: #9deab5;
        font-weight: bold;
    }
    QPushButton#btn_keep:hover { background-color: #257743; }
    QPushButton#btn_fail {
        background-color: #67292a;
        border-color: #874040;
        color: #f0a0a0;
        font-weight: bold;
    }
    QPushButton#btn_fail:hover { background-color: #7a3133; }
    QPushButton#btn_start {
        background-color: #204f73;
        border-color: #2f648e;
        color: #9ad3f6;
        font-weight: bold;
    }
    QPushButton#btn_start:hover { background-color: #296082; }
    QPushButton#btn_cancel {
        background-color: #5a3218;
        border-color: #744427;
        color: #ffbb73;
        font-weight: bold;
    }
    QPushButton#btn_cancel:hover { background-color: #6a3c1d; }
    QPushButton#btn_metadata {
        font-weight: 600;
        border-color: #5e7192;
    }
    QComboBox {
        background-color: #242b36;
        border: 1px solid #455064;
        border-radius: 8px;
        padding: 3px 8px;
        min-height: 24px;
    }
    QComboBox::drop-down { border: none; width: 20px; }
    QComboBox QAbstractItemView {
        background-color: #242b36;
        selection-background-color: #38536f;
    }
    QSpinBox, QDoubleSpinBox {
        background-color: #242b36;
        border: 1px solid #455064;
        border-radius: 8px;
        padding: 3px 6px;
    }
    QSpinBox#queue_count_spin {
        font-size: 15px;
        font-weight: 700;
        color: #b7deff;
        border: 1px solid #5d7cad;
        background-color: #223247;
        padding-right: 34px;
        selection-background-color: #3b6898;
    }
    QSpinBox#queue_count_spin::up-button, QSpinBox#queue_count_spin::down-button {
        width: 22px;
        margin: 2px;
        border: 1px solid #587297;
        background-color: #2b4461;
        border-radius: 6px;
    }
    QSpinBox#queue_count_spin::up-button:hover, QSpinBox#queue_count_spin::down-button:hover {
        background-color: #375779;
    }
    QSpinBox#queue_count_spin::up-arrow, QSpinBox#queue_count_spin::down-arrow {
        width: 9px;
        height: 9px;
    }
    QLineEdit {
        background-color: #242b36;
        border: 1px solid #455064;
        border-radius: 8px;
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
        border: 1px solid #313947;
        border-radius: 10px;
        margin-top: 10px;
        padding-top: 8px;
        background-color: rgba(255, 255, 255, 0.015);
    }
    QGroupBox::title {
        color: #9fb2cc;
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
    }
    QScrollArea, QScrollBar { background-color: #14171c; }
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
