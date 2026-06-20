import sys
from PyQt6.QtWidgets import QApplication
from dms.ui.main_window import MainWindow
from dms.settings_manager import SettingsManager
from dms.session import SessionData
from dms.theme import ThemeController
from dms.version import __version__


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("DMS Fastgraph Beta")
    app.setApplicationVersion(__version__)

    settings = SettingsManager()
    theme_controller = ThemeController(app, settings)

    # Launch directly into main UI; metadata can be edited from a top-level button.
    session = SessionData(
        rig="Unknown Rig",
        brand="Unknown",
        model="Unknown",
    )

    window = MainWindow(session, settings, theme_controller)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
