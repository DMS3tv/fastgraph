from pathlib import Path

import numpy as np
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

import dms.settings_manager as settings_module
from dms.settings_manager import SettingsManager
from dms.theme import (
    DARK,
    LIGHT,
    ThemeController,
    application_stylesheet,
    brand_application_stylesheet,
    brand_theme_colors,
    normalize_theme,
)
from dms.ui.dual_plot_widget import DualPlotWidget
from dms.ui.toggle_switch import ThemeToggleWidget, ToggleSwitch


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_theme_defaults_and_validation() -> None:
    assert normalize_theme(None) == DARK
    assert normalize_theme("unexpected") == DARK
    assert normalize_theme("LIGHT") == LIGHT
    assert "#f3f5f8" in application_stylesheet(LIGHT)


def test_theme_controller_applies_and_persists(monkeypatch, tmp_path: Path) -> None:
    app = _app()
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    settings = SettingsManager()
    controller = ThemeController(app, settings)

    assert controller.theme == DARK
    controller.set_theme(LIGHT)

    assert controller.theme == LIGHT
    assert settings.get("theme") == LIGHT
    assert "#f3f5f8" in app.styleSheet()


def test_theme_controller_brand_mode_persists_and_signals(monkeypatch, tmp_path: Path) -> None:
    app = _app()
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    settings = SettingsManager()
    controller = ThemeController(app, settings)

    received: list[bool] = []
    controller.brand_mode_changed.connect(received.append)

    controller.set_brand_mode(True)

    assert controller.brand_mode is True
    assert settings.get("brand_mode") is True
    assert "#232323" in app.styleSheet()
    assert "#7A7A7A" in app.styleSheet()
    assert received == [True]

    controller.set_brand_mode(True)
    assert received == [True]

    controller.set_brand_mode(False)

    assert controller.brand_mode is False
    assert settings.get("brand_mode") is False
    assert app.styleSheet() == application_stylesheet(controller.theme)
    assert received == [True, False]


def test_brand_stylesheet_has_visible_accent_hierarchy() -> None:
    stylesheet = brand_application_stylesheet()

    assert brand_theme_colors()["border"] == "#5C5C5C"
    assert "QGroupBox#brandPosterBox" in stylesheet
    assert "QGroupBox#brandPosterBox QLabel#brandMetadataStatus" in stylesheet
    assert "background-color: transparent" in stylesheet
    assert "QPushButton#exportButton" in stylesheet
    assert 'QLineEdit[metadataState="manual"]' in stylesheet
    assert "border-bottom: 3px solid #7A7A7A" in stylesheet


def test_theme_toggle_direction() -> None:
    _app()
    toggle = ThemeToggleWidget(dark=True)
    assert toggle.is_dark() is True
    toggle.set_dark(False)
    assert toggle.is_dark() is False
    assert "Light mode" in toggle.toolTip()


def test_custom_switch_uses_full_painted_hitbox() -> None:
    _app()
    switch = ToggleSwitch("")
    switch.resize(54, 30)
    assert switch.hitButton(QPoint(2, 2)) is True
    assert switch.hitButton(QPoint(51, 27)) is True


def test_custom_switch_reserves_space_for_painted_track() -> None:
    _app()
    switch = ToggleSwitch("")
    assert switch.sizeHint().width() >= 54
    assert switch.minimumSizeHint().width() >= 54


def test_theme_toggle_container_click_changes_mode() -> None:
    _app()
    toggle = ThemeToggleWidget(dark=True)
    toggle.resize(toggle.sizeHint())
    QTest.mouseClick(toggle, Qt.MouseButton.LeftButton, pos=QPoint(5, 11))
    assert toggle.is_dark() is False


def test_plot_theme_change_preserves_curves() -> None:
    _app()
    widget = DualPlotWidget()
    curve = (np.array([100.0, 1000.0]), np.array([1.0, 0.0]))
    widget.update_curves([curve], curve)
    top_count = len(widget._top_items)

    widget.apply_theme(LIGHT)
    widget.apply_theme(DARK)

    assert len(widget._top_items) == top_count
    assert widget._top_plot.backgroundBrush().color().name() == "#1a1a1a"
