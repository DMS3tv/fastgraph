import hashlib
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest

import dms.settings_manager as settings_module
from dms.settings_manager import SettingsManager
from dms.theme import (
    DARK,
    DITHER,
    LIGHT,
    FASTGRAPH_95,
    FASTGRAPH_95_DARK,
    HACKERMAN_95,
    ThemeController,
    _status_accent_colors,
    application_stylesheet,
    ensure_graph_color,
    graph_contrast_ratio,
    brand_application_stylesheet,
    brand_theme_colors,
    normalize_theme,
    theme_trace_palette,
)
from dms.ui.style_tokens import DARK_TOKENS, DITHER_TOKENS, tokens_for
from dms.ui.dual_plot_widget import DualPlotWidget
from dms.ui.settings_dialog import SettingsWidget
from dms.ui.toggle_switch import ThemeToggleWidget, ToggleSwitch


def test_theme_defaults_and_validation() -> None:
    assert normalize_theme(None) == DARK
    assert normalize_theme("unexpected") == DARK
    assert normalize_theme("LIGHT") == LIGHT
    assert normalize_theme("FastGraph95") == FASTGRAPH_95
    assert normalize_theme("FastGraph95_Dark") == FASTGRAPH_95_DARK
    assert normalize_theme("Hackerman95") == HACKERMAN_95
    assert normalize_theme("Dither") == DITHER
    assert tokens_for("dither") is DITHER_TOKENS
    assert "#f3f5f8" in application_stylesheet(LIGHT)
    assert "QWidget[ditherSurface=\"true\"]" in application_stylesheet(FASTGRAPH_95)
    assert "border-top: 2px solid #ffffff" in application_stylesheet(FASTGRAPH_95)
    dark_classic = application_stylesheet(FASTGRAPH_95_DARK)
    assert "border-top: 2px solid #8f8f8f" in dark_classic
    assert "QDialogButtonBox QPushButton" in dark_classic
    assert "background-color: #292929" in dark_classic
    terminal_classic = application_stylesheet(HACKERMAN_95)
    assert "font-family: 'Monaco', 'Courier New', monospace" in terminal_classic
    assert "border-top: 2px solid #596259" in terminal_classic
    assert "background-color: #121512" in terminal_classic
    assert theme_trace_palette(HACKERMAN_95)[0] == "#39FF14"


def test_dither_trace_palette_has_contrast_and_distinct_entries() -> None:
    palette = theme_trace_palette(DITHER)

    assert palette[0] == "#E9E2D4"
    assert len(palette) == 8
    assert len(set(palette)) == 8
    assert all(
        graph_contrast_ratio(color, DITHER_TOKENS.plot_bg) >= 4.5
        for color in palette
    )


def test_status_accent_colors_use_dither_rust_and_keep_dark_blue() -> None:
    assert _status_accent_colors(DITHER_TOKENS)["start"] == (
        "#4A2318",
        "#5C2D1E",
        "#E8845C",
    )
    assert _status_accent_colors(DARK_TOKENS)["start"] == (
        "#204f73",
        "#296082",
        "#9ad3f6",
    )


def test_dither_stylesheet_uses_rust_start_accent() -> None:
    stylesheet = application_stylesheet(DITHER)

    assert "#E8845C" in stylesheet
    assert "#9ad3f6" not in stylesheet


def test_existing_theme_stylesheets_match_pre_dither_status_snapshots() -> None:
    expected = {
        DARK: "f08dd7ee40fad3aa38e441596f6ce1d4f59afc4849887ec0dda9ee60de5c0c0f",
        LIGHT: "b0c41c6ef5a85db3ee68bb2264d29c098bd8a99776f5a5ce8b4e42c9ca680741",
        FASTGRAPH_95: "bc3b97e6949eb76d9363c11780c154b01f6a701d3c748617ad78658778f78c72",
        FASTGRAPH_95_DARK: "99a28cf3738a5216c22104dffbf3413069691cf02432ac6123c44423c3fe7a63",
        HACKERMAN_95: "235ff7e5cf43ac3b15ed6af47ef62e79b5d5eeacbb6a95e610a477a21426724c",
    }

    for theme, expected_digest in expected.items():
        stylesheet = application_stylesheet(theme)
        assert hashlib.sha256(stylesheet.encode()).hexdigest() == expected_digest

    brand_stylesheet = brand_application_stylesheet()
    assert hashlib.sha256(brand_stylesheet.encode()).hexdigest() == (
        "944b3a73f2681d07cfdeb66ae68238afc0919bd592c24a9b4548b7251c7403ae"
    )


def test_graph_colors_are_adjusted_for_light_and_dark_backgrounds() -> None:
    light_adjusted = ensure_graph_color("#c8ff00", "#ffffff")
    dark_adjusted = ensure_graph_color("#101010", "#07090c")

    assert graph_contrast_ratio(light_adjusted, "#ffffff") >= 4.5
    assert graph_contrast_ratio(dark_adjusted, "#07090c") >= 4.5


def test_theme_controller_applies_and_persists(qapp, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    settings = SettingsManager()
    controller = ThemeController(qapp, settings)

    assert controller.theme == DARK
    controller.set_theme(LIGHT)

    assert controller.theme == LIGHT
    assert settings.get("theme") == LIGHT
    assert "#f3f5f8" in qapp.styleSheet()


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


def test_theme_controller_brand_mode_persists_and_signals(qapp, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    settings = SettingsManager()
    controller = ThemeController(qapp, settings)

    received: list[bool] = []
    controller.brand_mode_changed.connect(received.append)

    controller.set_brand_mode(True)

    assert controller.brand_mode is True
    assert settings.get("brand_mode") is True
    assert "#07090C" in qapp.styleSheet()
    assert "#7A7A7A" in qapp.styleSheet()
    assert received == [True]

    controller.set_brand_mode(True)
    assert received == [True]

    controller.set_brand_mode(False)

    assert controller.brand_mode is False
    assert settings.get("brand_mode") is False
    assert qapp.styleSheet() == application_stylesheet(controller.theme)
    assert received == [True, False]


def test_brand_stylesheet_has_visible_accent_hierarchy() -> None:
    stylesheet = brand_application_stylesheet()

    assert brand_theme_colors()["window"] == "#07090C"
    assert brand_theme_colors()["panel"] == "#2A2A2A"
    assert brand_theme_colors()["plot_bg"] == "#232323"
    assert brand_theme_colors()["border"] == "#5C5C5C"
    assert "QGroupBox#brandPosterBox" in stylesheet
    assert "QGroupBox#brandPosterBox QLabel#brandMetadataStatus" in stylesheet
    assert "background-color: transparent" in stylesheet
    assert "QPushButton#exportButton" in stylesheet
    assert 'QLineEdit[metadataState="manual"]' in stylesheet
    assert "border-bottom: 3px solid #7A7A7A" in stylesheet


def test_theme_toggle_direction(qapp) -> None:
    toggle = ThemeToggleWidget(dark=True)
    assert toggle.is_dark() is True
    toggle.set_dark(False)
    assert toggle.is_dark() is False
    assert "Light mode" in toggle.toolTip()


def test_custom_switch_uses_full_painted_hitbox(qapp) -> None:
    switch = ToggleSwitch("")
    switch.resize(54, 30)
    assert switch.hitButton(QPoint(2, 2)) is True
    assert switch.hitButton(QPoint(51, 27)) is True


def test_custom_switch_reserves_space_for_painted_track(qapp) -> None:
    switch = ToggleSwitch("")
    assert switch.sizeHint().width() >= 54
    assert switch.minimumSizeHint().width() >= 54


def test_theme_toggle_container_click_changes_mode(qapp) -> None:
    toggle = ThemeToggleWidget(dark=True)
    toggle.resize(toggle.sizeHint())
    QTest.mouseClick(toggle, Qt.MouseButton.LeftButton, pos=QPoint(5, 11))
    assert toggle.is_dark() is False


def test_plot_theme_change_preserves_curves(qapp) -> None:
    widget = DualPlotWidget()
    curve = (np.array([100.0, 1000.0]), np.array([1.0, 0.0]))
    widget.update_curves([curve], curve)
    top_count = len(widget._top_items)

    widget.apply_theme(LIGHT)
    widget.apply_theme(FASTGRAPH_95)
    assert all(item.opts["antialias"] is True for item in widget._top_items)
    assert widget._bot_item is not None and widget._bot_item.opts["antialias"] is True
    np.testing.assert_array_equal(widget._top_items[0].xData, [100.0, 1000.0, 1000.0])
    np.testing.assert_array_equal(widget._top_items[0].yData, [1.0, 1.0, 0.0])
    np.testing.assert_array_equal(widget._kept_curves[0][0], curve[0])
    np.testing.assert_array_equal(widget._kept_curves[0][1], curve[1])
    widget.apply_theme(HACKERMAN_95)
    assert widget._top_items[0].opts["pen"].color().name().upper() == "#39FF14"
    assert widget._bot_item is not None
    assert widget._bot_item.opts["pen"].color().name().upper() == "#39FF14"
    np.testing.assert_array_equal(widget._top_items[0].xData, [100.0, 1000.0, 1000.0])
    widget.apply_theme(DARK)

    assert len(widget._top_items) == top_count
    assert all(item.opts["antialias"] is True for item in widget._top_items)
    np.testing.assert_array_equal(widget._top_items[0].xData, curve[0])
    np.testing.assert_array_equal(widget._top_items[0].yData, curve[1])
    assert widget._top_plot.backgroundBrush().color().name() == "#1a1a1a"
