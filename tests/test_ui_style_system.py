import pytest
from helpers import pump_until
from PyQt6.QtCore import QAbstractAnimation, QEvent, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

import dms.dither_fonts as dither_fonts
import dms.settings_manager as settings_module
from dms.graph_display import aperiodic_dither_band_image
from dms.settings_manager import SettingsManager
from dms.style_tokens import (
    DARK_TOKENS,
    DITHER_TOKENS,
    FASTGRAPH_95_DARK_TOKENS,
    FASTGRAPH_95_TOKENS,
    HACKERMAN_95_TOKENS,
    LIGHT_TOKENS,
    THEME_DEFINITIONS,
    tokens_for,
)
from dms.theme import (
    DARK,
    DITHER,
    FASTGRAPH_95,
    FASTGRAPH_95_DARK,
    HACKERMAN_95,
    LIGHT,
    ThemeController,
    application_stylesheet,
    theme_colors,
)
from dms.ui.modern_button import (
    _FLAT_LABEL_HORIZONTAL_INSET,
    _FLAT_PAINT_RECT_WIDTH_LOSS,
    ModernButton,
)
from dms.ui.modern_spinbox import ModernDoubleSpinBox, ModernSpinBox
from dms.ui.rounded_viewport import RoundedViewportFrame
from dms.ui.theme_surface import _dither_tile, dither_brush


def test_style_tokens_define_the_documented_scale() -> None:
    geometry = DARK_TOKENS.geometry
    assert geometry.radius_micro == 4
    assert geometry.radius_field == 8
    assert geometry.radius_tab == 8
    assert geometry.radius_button == 12
    assert geometry.radius_surface == 12
    assert (
        geometry.spacing_xs,
        geometry.spacing_sm,
        geometry.spacing_md,
        geometry.spacing_lg,
        geometry.spacing_xl,
    ) == (4, 8, 12, 16, 24)
    assert DARK_TOKENS.typography.ui_family == "Inter"
    assert DARK_TOKENS.typography.technical_family == "Inconsolata"


def test_theme_surface_tokens_and_brand_values() -> None:
    assert DARK_TOKENS.background == "#07090C"
    assert DARK_TOKENS.viewport == "#0C1015"
    assert DARK_TOKENS.panel == "#121820"
    assert DARK_TOKENS.raised == "#18212B"
    assert DARK_TOKENS.control == "#1E2833"
    assert DARK_TOKENS.plot_bg == "#1A1A1A"
    assert LIGHT_TOKENS.viewport == "#FFFFFF"
    assert tokens_for(LIGHT) is LIGHT_TOKENS
    assert tokens_for(FASTGRAPH_95) is FASTGRAPH_95_TOKENS
    assert tokens_for(FASTGRAPH_95_DARK) is FASTGRAPH_95_DARK_TOKENS
    assert tokens_for(HACKERMAN_95) is HACKERMAN_95_TOKENS
    assert tokens_for(DITHER) is DITHER_TOKENS
    assert tokens_for(DARK, brand_mode=True) is DARK_TOKENS
    assert [definition.label for definition in THEME_DEFINITIONS] == [
        "Default dark",
        "Default light",
        "FastGraph 95",
        "FastGraph 95 Dark",
        "Hackerman 95",
        "Dither",
    ]


def test_dither_is_registered_with_reusable_behavior_flags() -> None:
    definitions = THEME_DEFINITIONS
    by_key = {definition.key: definition for definition in definitions}

    assert by_key["dither"].label == "Dither"
    assert by_key["dither"].tokens is DITHER_TOKENS
    assert DITHER_TOKENS.dither_chrome is True
    assert DITHER_TOKENS.stipple_traces is True
    assert DITHER_TOKENS.classic_controls is False
    assert DITHER_TOKENS.flat_controls is True
    assert by_key["dither"].style_family == "dither"
    assert all(
        not definition.tokens.dither_chrome
        and not definition.tokens.stipple_traces
        and not definition.tokens.flat_controls
        for definition in definitions
        if definition.key != "dither"
    )
    assert DITHER_TOKENS.geometry.radius_button == 0
    assert DITHER_TOKENS.geometry.focus_border_px == 2
    assert DITHER_TOKENS.motion.hover_in_ms == 120
    assert DITHER_TOKENS.motion.hover_out_ms == 160
    assert DITHER_TOKENS.motion.press_ms == 0
    assert DITHER_TOKENS.motion.hover_tint_alpha == 0.0
    assert DITHER_TOKENS.motion.focus_glow_strength == 0.0
    assert DITHER_TOKENS.motion.hover_glow_alpha == 0
    assert DITHER_TOKENS.motion.rest_shadow_alpha == 0
    assert DITHER_TOKENS.motion.rest_shadow_blur == 0.0
    assert DITHER_TOKENS.motion.hover_shadow_blur == 0.0
    assert DITHER_TOKENS.typography.heading_family == "DIN Condensed"


def test_dither_font_resolver_reports_a_missing_din_condensed(qapp, monkeypatch) -> None:
    dither_fonts.reset_font_cache()
    monkeypatch.setattr(
        QFontDatabase,
        "families",
        staticmethod(lambda: ["Inter", "Arial Narrow"]),
    )

    status = dither_fonts.dither_font_status()
    font = dither_fonts.dither_heading_font(QFont("Inter"))

    assert status.heading_family == "Arial Narrow"
    assert status.missing_families == ("DIN Condensed",)
    assert status.uses_fallback is True
    assert font.capitalization() == QFont.Capitalization.AllUppercase
    assert font.letterSpacing() == 1.0
    dither_fonts.reset_font_cache()


def test_dither_style_family_uses_square_solid_selected_tabs() -> None:
    stylesheet = application_stylesheet(DITHER)

    assert "QTabBar::tab:selected" in stylesheet
    assert "background: #c4542e" in stylesheet
    assert "color: #0a0a09" in stylesheet
    assert "border-radius: 0px" in stylesheet
    assert "QMessageBox, QInputDialog, QFileDialog" in stylesheet
    assert "gradient" not in stylesheet.lower()


def test_stylesheet_exposes_surface_and_tab_hierarchy() -> None:
    stylesheet = application_stylesheet(DARK)
    assert 'QWidget[surfaceLevel="viewport"]' in stylesheet
    assert 'QWidget[surfaceLevel="panel"]' in stylesheet
    assert 'QWidget[layoutRole="transparent"]' in stylesheet
    assert "border-top-left-radius: 8px" in stylesheet
    assert "border-bottom: 2px solid #66ccff" in stylesheet
    assert "QScrollBar:horizontal" in stylesheet
    assert "QScrollBar::add-line, QScrollBar::sub-line" in stylesheet
    assert "width: 0px; height: 0px" in stylesheet
    assert "QToolButton#section_toggle" in stylesheet


@pytest.mark.parametrize(
    "theme",
    (DARK, LIGHT, FASTGRAPH_95, FASTGRAPH_95_DARK, HACKERMAN_95, DITHER),
)
def test_each_theme_styles_measure_submode_selected_state(theme: str) -> None:
    stylesheet = application_stylesheet(theme)
    selected_selector = 'QPushButton[measureSegment="true"]:checked {'
    selected_start = stylesheet.rfind(selected_selector)

    assert selected_start >= 0
    selected_rule = stylesheet[selected_start:].split("}", 1)[0]
    assert f"background-color: {theme_colors(theme)['accent']}" in selected_rule
    assert 'QPushButton[measureSegment="true"]:hover' in stylesheet
    assert 'QPushButton[measureSegment="true"]:focus' in stylesheet
    assert 'QPushButton[measureSegment="true"]:checked:disabled' in stylesheet


def test_modern_button_roles_cover_semantic_actions(qapp) -> None:
    assert qapp is not None
    assert ModernButton("Export Average").role() == "primary"
    assert ModernButton("Keep").role() == "positive"
    assert ModernButton("Remove").role() == "danger"
    assert ModernButton("Cancel").role() == "warning"
    button = ModernButton("Custom")
    button.setRole("ghost")
    assert button.role() == "ghost"

    measure = ModernButton("Measure")
    measure.setObjectName("btn_start")
    start_queue = ModernButton("Start Queue")
    start_queue.setObjectName("btn_start")
    assert measure._has_persistent_outline() is True
    assert start_queue._has_persistent_outline() is True


def _painted_button_label_requirement(button: ModernButton) -> int:
    tokens = button._tokens()
    path = button._control_paint_path()
    if path == "flat":
        base = QFont()
        base.setPixelSize(tokens.typography.body_px)
        font = dither_fonts.dither_heading_font(base)
        text = button.text().upper()
        border_width = max(
            tokens.geometry.border_px,
            tokens.geometry.focus_border_px,
        )
        horizontal_space = 2 * 8 + 2 * border_width
    elif path == "classic":
        font = button.font()
        text = button.text()
        horizontal_space = 1 + 2 * 1 + 2 * 5
    else:
        font = button.font()
        text = button.text()
        horizontal_space = 2 * 4 + 2 * 8
    return QFontMetrics(font).horizontalAdvance(text) + horizontal_space


def test_modern_button_size_hints_fit_painted_labels_in_each_theme(
    qapp,
    monkeypatch,
    tmp_path,
    fake_brand,
) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    controller = ThemeController(qapp, SettingsManager())
    labels = (
        "Measure",
        "Cancel Queue",
        "Inputs",
        "Headphone Metadata",
        "Clear Metadata",
    )

    for theme in (
        DARK,
        LIGHT,
        FASTGRAPH_95,
        FASTGRAPH_95_DARK,
        HACKERMAN_95,
        DITHER,
    ):
        controller.set_brand_mode(False, persist=False)
        controller.set_theme(theme, persist=False)
        for label in labels:
            button = ModernButton(label)
            button.ensurePolished()
            required_width = _painted_button_label_requirement(button)

            assert button.sizeHint().width() > required_width
            assert button.minimumSizeHint().width() > required_width

    controller.set_brand_mode(True, persist=False)
    for label in labels:
        button = ModernButton(label)
        button.ensurePolished()
        required_width = _painted_button_label_requirement(button)

        assert button.sizeHint().width() > required_width
        assert button.minimumSizeHint().width() > required_width


def test_dither_button_size_hint_does_not_depend_on_plain_style_measurement(
    qapp,
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(settings_module, "_config_dir", lambda: tmp_path)
    controller = ThemeController(qapp, SettingsManager())
    controller.set_theme(DITHER, persist=False)
    button = ModernButton("Headphone Metadata")
    button.ensurePolished()
    required_width = _painted_button_label_requirement(button)
    base_height = QPushButton.sizeHint(button).height()
    monkeypatch.setattr(
        QPushButton,
        "sizeHint",
        lambda _button: QSize(required_width, base_height),
    )

    assert button.sizeHint().width() > required_width
    assert button.minimumSizeHint().width() > required_width


def test_modern_button_can_use_a_dark_mode_only_accent(qapp, fake_brand) -> None:
    button = ModernButton("Inputs")
    button.setProperty("darkAccentColor", "#A970FF")

    qapp.setProperty("fastgraphVisualMode", "dark")
    assert button._accent(button._tokens()).name().upper() == "#A970FF"
    qapp.setProperty("fastgraphVisualMode", "light")
    assert button._accent(button._tokens()).name().upper() == LIGHT_TOKENS.accent
    qapp.setProperty("fastgraphVisualMode", "brand")
    assert button._accent(button._tokens()).name().upper() == fake_brand.tokens.accent


def test_modern_button_hover_and_press_endpoints(qapp) -> None:
    qapp.setProperty("fastgraphVisualMode", "dark")
    button = ModernButton("Action")
    button.resize(button.sizeHint())
    button.show()
    qapp.processEvents()

    rest = button._glow_profile()
    assert button.graphicsEffect() is None

    button._set_hover_progress(1.0)
    assert button.hoverProgress() == 1.0
    hover = button._glow_profile()
    assert hover["center_y"] < rest["center_y"]
    assert hover["radius"] > rest["radius"]
    assert hover["center_alpha"] > rest["center_alpha"]

    button._set_press_progress(1.0)
    assert button.pressProgress() == 1.0
    flash = button._glow_profile()
    assert flash["center_alpha"] > hover["center_alpha"]
    assert flash["uniform_alpha"] > hover["uniform_alpha"]


def test_fastgraph95_button_disables_glow_and_uses_square_geometry(qapp) -> None:
    qapp.setProperty("fastgraphVisualMode", FASTGRAPH_95)
    button = ModernButton("Action")
    button.resize(button.sizeHint())

    button._set_hover_progress(1.0)
    button._set_press_progress(1.0)

    assert button._tokens() is FASTGRAPH_95_TOKENS
    assert button._effective_hover() == 0.0
    assert button._glow_profile()["center_alpha"] == 0
    assert FASTGRAPH_95_TOKENS.geometry.radius_button == 0


def test_dither_button_selects_the_flat_third_paint_path(qapp) -> None:
    button = ModernButton("Action")

    qapp.setProperty("fastgraphVisualMode", DITHER)
    assert button._control_paint_path() == "flat"
    assert button._glow_profile()["center_alpha"] == 0
    assert button._flat_role_color(DITHER_TOKENS).name().upper() == DITHER_TOKENS.text

    qapp.setProperty("fastgraphVisualMode", FASTGRAPH_95)
    assert button._control_paint_path() == "classic"
    qapp.setProperty("fastgraphVisualMode", DARK)
    assert button._control_paint_path() == "modern"


def test_dither_button_hover_uses_cached_discrete_density_steps(qapp) -> None:
    qapp.setProperty("fastgraphVisualMode", DITHER)
    button = ModernButton("Action")

    button._set_hover_progress(0.18)
    first_state = button._flat_hover_dither_state()
    button._set_hover_progress(0.20)
    assert button._flat_hover_dither_state() == first_state
    button._set_hover_progress(0.30)
    later_state = button._flat_hover_dither_state()
    assert later_state[0] > first_state[0]
    assert later_state[1] > first_state[1]

    button._animate_hover(1.0)
    assert button._hover_animation.duration() == 120
    button._hover_animation.stop()
    button._set_hover_progress(0.5)
    button._animate_hover(0.0)
    assert button._hover_animation.duration() == 160
    button._hover_animation.stop()

    role_color = button._flat_role_color(DITHER_TOKENS)
    _dither_tile.cache_clear()
    first_brush = dither_brush(role_color, density=first_state[0])
    dither_brush(role_color, density=first_state[0])
    assert _dither_tile.cache_info().hits == 1
    tile = first_brush.texture().toImage()
    ink_alphas = {
        tile.pixelColor(x, y).alpha()
        for y in range(tile.height())
        for x in range(tile.width())
        if tile.pixelColor(x, y).alpha() > 0
    }
    assert ink_alphas == {255}

    button.setEnabled(False)
    button._animate_hover(1.0)
    assert button._flat_hover_dither_state() == (0.0, 0.0)


def test_aperiodic_bounds_dither_is_denser_near_boundaries() -> None:
    ink = QColor(DITHER_TOKENS.plot_grid)
    image = aperiodic_dither_band_image(
        320,
        120,
        [(0.0, 0.2), (1.0, 0.2)],
        [(0.0, 0.8), (1.0, 0.8)],
        foreground=ink,
    )

    def ink_count(y_start: int, y_stop: int) -> int:
        return sum(
            image.pixelColor(x, y).rgba() == ink.rgba()
            for y in range(y_start, y_stop)
            for x in range(image.width())
        )

    boundary_coverage = ink_count(24, 34) + ink_count(86, 96)
    center_coverage = 2 * ink_count(55, 65)
    assert boundary_coverage > center_coverage * 2


def test_fastgraph95_dark_uses_classic_renderer_without_bright_surfaces(qapp) -> None:
    qapp.setProperty("fastgraphVisualMode", FASTGRAPH_95_DARK)
    button = ModernButton("Action")

    assert button._tokens() is FASTGRAPH_95_DARK_TOKENS
    assert button._glow_profile()["center_alpha"] == 0
    assert FASTGRAPH_95_DARK_TOKENS.raised == "#292929"
    assert FASTGRAPH_95_DARK_TOKENS.plot_bg == "#202020"


def test_hackerman95_uses_terminal_tokens_and_neon_trace_palette(qapp) -> None:
    qapp.setProperty("fastgraphVisualMode", HACKERMAN_95)
    button = ModernButton("Action")

    assert button._tokens() is HACKERMAN_95_TOKENS
    assert button._glow_profile()["center_alpha"] == 0
    assert HACKERMAN_95_TOKENS.classic_controls is True
    assert HACKERMAN_95_TOKENS.dark_bevel is True
    assert HACKERMAN_95_TOKENS.retro_graph is True
    assert HACKERMAN_95_TOKENS.terminal_chrome is True
    assert HACKERMAN_95_TOKENS.background == "#111411"
    assert HACKERMAN_95_TOKENS.panel == "#1D211D"
    assert HACKERMAN_95_TOKENS.text == "#D3D9D3"
    assert HACKERMAN_95_TOKENS.plot_bg == "#010301"
    assert HACKERMAN_95_TOKENS.trace_palette[0] == "#39FF14"


def test_modern_button_animates_hover_and_press(qapp) -> None:
    qapp.setProperty("fastgraphVisualMode", "dark")
    button = ModernButton("Action")
    button.resize(button.sizeHint())

    def finish(animation) -> None:
        # Wait for the animation to end, not for a fixed time: a loaded
        # machine can deliver the animation's frames late.
        assert pump_until(qapp, lambda: animation.state() == QAbstractAnimation.State.Stopped)

    button._animate_hover(1.0)
    assert button._hover_animation.duration() == DARK_TOKENS.motion.hover_in_ms
    finish(button._hover_animation)
    assert button.hoverProgress() > 0.95

    button._animate_press(1.0)
    finish(button._press_animation)
    assert button.pressProgress() > 0.95
    button._animate_press(0.0)
    finish(button._press_animation)
    assert button.pressProgress() < 0.05


def test_modern_button_focus_and_disabled_states(qapp, fake_brand) -> None:
    qapp.setProperty("fastgraphVisualMode", "brand")
    button = ModernButton("Action")
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    button.resize(button.sizeHint())
    button.show()
    button.setFocus()
    qapp.processEvents()
    assert button._effective_hover() >= fake_brand.tokens.motion.focus_glow_strength

    button.setEnabled(False)
    qapp.processEvents()
    assert button.graphicsEffect() is None
    assert button._effective_hover() == 0.0
    assert button._glow_profile()["center_alpha"] == 0


def test_button_uses_nested_rectangles_for_the_recessed_well(qapp) -> None:
    qapp.setProperty("fastgraphVisualMode", "dark")
    button = ModernButton("Action")
    button.resize(button.sizeHint())
    button.show()
    qapp.processEvents()
    outer, well, face = button._paint_rects()
    assert outer.contains(well)
    assert well.contains(face)
    assert well.width() - face.width() == 4.0
    assert outer.width() - well.width() == 3.0
    assert button.sizeHint().height() >= DARK_TOKENS.geometry.button_height + 4


def test_button_click_flash_has_a_visible_release_tail(qapp) -> None:
    qapp.setProperty("fastgraphVisualMode", "dark")
    button = ModernButton("Action")
    button._begin_click_flash()
    assert button.pressProgress() >= 0.32
    button._release_click_flash()
    assert button.pressProgress() >= 0.58


def test_palette_change_refreshes_button_mode(qapp, fake_brand) -> None:
    button = ModernButton("Action")
    qapp.setProperty("fastgraphVisualMode", "brand")
    QApplication.sendEvent(button, QEvent(QEvent.Type.ApplicationPaletteChange))
    assert button._tokens() is fake_brand.tokens


def test_rounded_viewport_keeps_the_plot_as_a_direct_capture_target(qapp) -> None:
    child = QWidget()
    frame = RoundedViewportFrame(child)
    frame.resize(320, 180)
    frame.show()
    qapp.processEvents()
    assert frame.radius == 10
    assert frame.child is child
    assert child.parentWidget() is frame
    assert frame._overlay.geometry() == frame.rect()


def test_modern_spin_boxes_preserve_value_behaviour(qapp) -> None:
    integer = ModernSpinBox()
    integer.setRange(1, 5)
    integer.setValue(2)
    integer.resize(110, 36)
    integer.show()
    qapp.processEvents()
    QTest.mouseClick(integer._step_up_button, Qt.MouseButton.LeftButton)
    assert integer.value() == 3
    QTest.keyClick(integer, Qt.Key.Key_Down)
    assert integer.value() == 2

    decimal = ModernDoubleSpinBox()
    decimal.setRange(-20.0, 20.0)
    decimal.setSingleStep(0.5)
    decimal.setSuffix(" dB")
    decimal.setValue(0.0)
    QTest.mouseClick(decimal._step_down_button, Qt.MouseButton.LeftButton)
    assert decimal.value() == -0.5
    assert decimal.suffix() == " dB"


def _process_theme_change(qapp) -> None:
    for _ in range(8):
        qapp.processEvents()


def _independent_dither_label_width(label: str) -> int:
    base = QFont()
    base.setPixelSize(DITHER_TOKENS.typography.body_px)
    heading = dither_fonts.dither_heading_font(base)
    label_width = QFontMetrics(heading).horizontalAdvance(label.upper())
    border_width = max(
        DITHER_TOKENS.geometry.border_px,
        DITHER_TOKENS.geometry.focus_border_px,
    )
    return (
        label_width
        + 2 * _FLAT_LABEL_HORIZONTAL_INSET
        + 2 * border_width
        + _FLAT_PAINT_RECT_WIDTH_LOSS
    )


def test_measure_button_width_matches_dither_startup_and_switch_paths(
    qapp,
    make_main_window,
) -> None:
    startup_window = make_main_window(theme=DITHER)
    startup_window.show()
    _process_theme_change(qapp)
    startup_button = startup_window.measure_tab.start_queue_btn
    startup_image = startup_button.grab().toImage()
    startup_width = startup_image.width()
    startup_hint_width = startup_button.sizeHint().width()

    assert startup_image.isNull() is False
    assert startup_width >= _independent_dither_label_width("Measure")

    startup_window.close()
    startup_window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    switch_window = make_main_window(theme=DARK)
    switch_window.show()
    _process_theme_change(qapp)
    switch_button = switch_window.measure_tab.start_queue_btn
    dark_width = switch_button.grab().toImage().width()
    dark_hint_width = switch_button.sizeHint().width()

    switch_window._theme_controller.set_theme(DITHER, persist=False)
    _process_theme_change(qapp)
    switch_image = switch_button.grab().toImage()

    assert switch_image.isNull() is False
    assert switch_image.width() >= _independent_dither_label_width("Measure")
    assert switch_image.width() == startup_width
    assert switch_button.sizeHint().width() == startup_hint_width

    switch_window._theme_controller.set_theme(DARK, persist=False)
    _process_theme_change(qapp)

    assert switch_button.grab().toImage().width() == dark_width
    assert switch_button.sizeHint().width() == dark_hint_width
