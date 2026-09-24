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


def test_settings_has_no_brand_toggle_without_a_brand(qapp) -> None:
    widget = SettingsWidget(SettingsManager())

    assert widget._brand_mode is None
    widget.refresh_from_settings()
    widget.set_editing_enabled(False)
    widget.close()


def test_brand_toggle_is_a_plain_checkbox(qapp, fake_brand) -> None:
    settings = SettingsManager()
    widget = SettingsWidget(settings)
    received: list[tuple[str, object]] = []
    widget.settings_changed.connect(lambda key, value: received.append((key, value)))

    assert widget._appearance_group.title() == fake_brand.label
    widget._brand_mode.setChecked(True)

    assert settings.get("brand_mode") is True
    assert received == [("brand_mode", True)]
    assert not widget._theme_choice_rows.isEnabled()
    widget.close()


def test_brand_can_adjust_settings_on_load(qapp, monkeypatch, fake_brand) -> None:
    from dataclasses import replace

    from dms import branding

    def on_load(data: dict) -> None:
        data["brand_mode"] = True

    monkeypatch.setattr(branding, "_brand", replace(fake_brand, on_settings_load=on_load))

    assert SettingsManager().get("brand_mode") is True
