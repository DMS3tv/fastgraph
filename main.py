import os
import sys

from PyQt6.QtWidgets import QApplication, QMainWindow

from dms.session import SessionData
from dms.settings_manager import SettingsManager
from dms.theme import ThemeController
from dms.ui.main_window import MainWindow
from dms.ui.present_window import PresentWindow
from dms.version import __version__


def wants_present_mode(argv: list[str]) -> bool:
    return "--present" in argv or os.environ.get("FASTGRAPH_MODE", "").strip() == "present"


def build_window(
    settings: SettingsManager, theme_controller: ThemeController, *, present: bool
) -> QMainWindow:
    if present:
        return PresentWindow(settings, theme_controller)
    # Launch directly into main UI; metadata can be edited from a top-level button.
    session = SessionData(
        rig="Unknown Rig",
        brand="Unknown",
        model="Unknown",
    )
    return MainWindow(session, settings, theme_controller)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("DMS Fastgraph Beta")
    app.setApplicationVersion(__version__)

    settings = SettingsManager()
    theme_controller = ThemeController(app, settings)

    window = build_window(settings, theme_controller, present=wants_present_mode(sys.argv))
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
