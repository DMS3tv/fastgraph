import hashlib
from pathlib import Path

import dms.settings_manager as settings_module
from dms.settings_manager import SettingsManager
from dms.style_tokens import DARK_TOKENS, DITHER_TOKENS, tokens_for
from dms.theme import (
    DARK,
    DITHER,
    FASTGRAPH_95,
    FASTGRAPH_95_DARK,
    HACKERMAN_95,
    LIGHT,
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
    assert 'QWidget[ditherSurface="true"]' in application_stylesheet(FASTGRAPH_95)
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
    assert all(graph_contrast_ratio(color, DITHER_TOKENS.plot_bg) >= 4.5 for color in palette)


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
    # A blunt guard: it fails for any stylesheet edit at all, including a
    # purely additive one. Re-snapshot it only after confirming with
    # ``git diff dms/theme.py`` that nothing existing was changed.
    # Last re-snapshotted 2026-09-10 for the ::menu-indicator rule that stops
    # the Session and Compare menu buttons from drawing a second arrow.
    expected = {
        DARK: "ad3eeca399558168b9791e20a9a3a1fbec9da1c65bfa78c23c0ea8a1f3c0ec92",
        LIGHT: "745c3bcd340ad60d69266e4bb23a712fd71b7733d1ab7b3673c330b749163bc7",
        FASTGRAPH_95: "b6c7fdc885853021c23320c9041080a3a16f38093a99abe91821705c258f7d0b",
        FASTGRAPH_95_DARK: "3b111c2cd3bb06b33276913fd6be3581126917f352242eb7c7d8907fb7977d6e",
        HACKERMAN_95: "99c5db59235f4393e83dda005f7c65fe00b180f41fc308bffb07267eba7ec0f1",
    }

    for theme, expected_digest in expected.items():
        stylesheet = application_stylesheet(theme)
        assert hashlib.sha256(stylesheet.encode()).hexdigest() == expected_digest

    brand_stylesheet = brand_application_stylesheet()
    assert hashlib.sha256(brand_stylesheet.encode()).hexdigest() == (
        "0c5c06085bf0a6df156aa5cc469e9b63131bbbec4cd624e14e22cb23b2342d09"
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
