from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from dms.theme import (
    DARK,
    FASTGRAPH_95,
    FASTGRAPH_95_DARK,
    HACKERMAN_95,
    LIGHT,
    ThemeController,
    application_stylesheet,
)
from dms.ui.modern_button import ModernButton
from dms.ui.modern_spinbox import ModernDoubleSpinBox, ModernSpinBox
from dms.ui.rounded_viewport import RoundedViewportFrame
from dms.ui.style_tokens import (
    DARK_TOKENS,
    BRAND_TOKENS,
    LIGHT_TOKENS,
    FASTGRAPH_95_TOKENS,
    FASTGRAPH_95_DARK_TOKENS,
    HACKERMAN_95_TOKENS,
    theme_definitions,
    tokens_for,
)


_APP: QApplication | None = None


def _app() -> QApplication:
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


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
    assert BRAND_TOKENS.background == DARK_TOKENS.background == "#07090C"
    assert BRAND_TOKENS.accent == "#7A7A7A"
    assert BRAND_TOKENS.danger == "#6E6E6E"
    assert BRAND_TOKENS.typography.heading_family == "Heading"
    assert tokens_for(LIGHT) is LIGHT_TOKENS
    assert tokens_for(FASTGRAPH_95) is FASTGRAPH_95_TOKENS
    assert tokens_for(FASTGRAPH_95_DARK) is FASTGRAPH_95_DARK_TOKENS
    assert tokens_for(HACKERMAN_95) is HACKERMAN_95_TOKENS
    assert tokens_for(DARK, brand_mode=True) is BRAND_TOKENS
    assert [definition.label for definition in theme_definitions()] == [
        "Default dark",
        "Default light",
        "FastGraph 95",
        "FastGraph 95 Dark",
        "Hackerman 95",
    ]


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


def test_modern_button_roles_cover_semantic_actions() -> None:
    app = _app()
    assert app is not None
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


def test_modern_button_can_use_a_dark_mode_only_accent() -> None:
    app = _app()
    button = ModernButton("Inputs")
    button.setProperty("darkAccentColor", "#A970FF")

    app.setProperty("fastgraphVisualMode", "dark")
    assert button._accent(button._tokens()).name().upper() == "#A970FF"
    app.setProperty("fastgraphVisualMode", "light")
    assert button._accent(button._tokens()).name().upper() == LIGHT_TOKENS.accent
    app.setProperty("fastgraphVisualMode", "brand")
    assert button._accent(button._tokens()).name().upper() == BRAND_TOKENS.accent


def test_modern_button_hover_and_press_endpoints() -> None:
    app = _app()
    app.setProperty("fastgraphVisualMode", "dark")
    button = ModernButton("Action")
    button.resize(button.sizeHint())
    button.show()
    app.processEvents()

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


def test_fastgraph95_button_disables_glow_and_uses_square_geometry() -> None:
    app = _app()
    app.setProperty("fastgraphVisualMode", FASTGRAPH_95)
    button = ModernButton("Action")
    button.resize(button.sizeHint())

    button._set_hover_progress(1.0)
    button._set_press_progress(1.0)

    assert button._tokens() is FASTGRAPH_95_TOKENS
    assert button._effective_hover() == 0.0
    assert button._glow_profile()["center_alpha"] == 0
    assert FASTGRAPH_95_TOKENS.geometry.radius_button == 0


def test_fastgraph95_dark_uses_classic_renderer_without_bright_surfaces() -> None:
    app = _app()
    app.setProperty("fastgraphVisualMode", FASTGRAPH_95_DARK)
    button = ModernButton("Action")

    assert button._tokens() is FASTGRAPH_95_DARK_TOKENS
    assert button._glow_profile()["center_alpha"] == 0
    assert FASTGRAPH_95_DARK_TOKENS.raised == "#292929"
    assert FASTGRAPH_95_DARK_TOKENS.plot_bg == "#202020"


def test_hackerman95_uses_terminal_tokens_and_neon_trace_palette() -> None:
    app = _app()
    app.setProperty("fastgraphVisualMode", HACKERMAN_95)
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


def test_modern_button_animates_hover_and_press() -> None:
    app = _app()
    app.setProperty("fastgraphVisualMode", "dark")
    button = ModernButton("Action")
    button.resize(button.sizeHint())
    button._animate_hover(1.0)
    QTest.qWait(DARK_TOKENS.motion.hover_in_ms + 30)
    assert button.hoverProgress() > 0.95

    button._animate_press(1.0)
    QTest.qWait(DARK_TOKENS.motion.press_ms + 20)
    assert button.pressProgress() > 0.95
    button._animate_press(0.0)
    QTest.qWait(DARK_TOKENS.motion.press_ms + 20)
    assert button.pressProgress() < 0.05


def test_modern_button_focus_and_disabled_states() -> None:
    app = _app()
    app.setProperty("fastgraphVisualMode", "brand")
    button = ModernButton("Action")
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    button.resize(button.sizeHint())
    button.show()
    button.setFocus()
    app.processEvents()
    assert button._effective_hover() >= BRAND_TOKENS.motion.focus_glow_strength

    button.setEnabled(False)
    app.processEvents()
    assert button.graphicsEffect() is None
    assert button._effective_hover() == 0.0
    assert button._glow_profile()["center_alpha"] == 0


def test_button_uses_nested_rectangles_for_the_recessed_well() -> None:
    app = _app()
    app.setProperty("fastgraphVisualMode", "dark")
    button = ModernButton("Action")
    button.resize(button.sizeHint())
    button.show()
    app.processEvents()
    outer, well, face = button._paint_rects()
    assert outer.contains(well)
    assert well.contains(face)
    assert well.width() - face.width() == 4.0
    assert outer.width() - well.width() == 3.0
    assert button.sizeHint().height() >= DARK_TOKENS.geometry.button_height + 4


def test_button_click_flash_has_a_visible_release_tail() -> None:
    app = _app()
    app.setProperty("fastgraphVisualMode", "dark")
    button = ModernButton("Action")
    button._begin_click_flash()
    assert button.pressProgress() >= 0.32
    button._release_click_flash()
    assert button.pressProgress() >= 0.58


def test_palette_change_refreshes_button_mode() -> None:
    app = _app()
    button = ModernButton("Action")
    app.setProperty("fastgraphVisualMode", "brand")
    QApplication.sendEvent(button, QEvent(QEvent.Type.ApplicationPaletteChange))
    assert button._tokens() is BRAND_TOKENS


def test_rounded_viewport_keeps_the_plot_as_a_direct_capture_target() -> None:
    app = _app()
    child = QWidget()
    frame = RoundedViewportFrame(child)
    frame.resize(320, 180)
    frame.show()
    app.processEvents()
    assert frame.radius == 10
    assert frame.child is child
    assert child.parentWidget() is frame
    assert frame._overlay.geometry() == frame.rect()


def test_modern_spin_boxes_preserve_value_behaviour() -> None:
    app = _app()
    integer = ModernSpinBox()
    integer.setRange(1, 5)
    integer.setValue(2)
    integer.resize(110, 36)
    integer.show()
    app.processEvents()
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
