from pathlib import Path

import dms.settings_manager as settings_module
from dms.settings_manager import SettingsManager
from dms.theme import FASTGRAPH_95
from dms.ui.settings_dialog import SettingsWidget


def test_settings_theme_options_save_registered_theme(qapp, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    settings = SettingsManager()
    widget = SettingsWidget(settings)
    received: list[tuple[str, object]] = []
    widget.settings_changed.connect(lambda key, value: received.append((key, value)))

    widget._theme_buttons[FASTGRAPH_95].click()

    assert settings.get("theme") == FASTGRAPH_95
    assert ("theme", FASTGRAPH_95) in received
    assert widget._theme_buttons[FASTGRAPH_95].isChecked()
