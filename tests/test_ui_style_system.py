from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from dms.theme import DARK, LIGHT, ThemeController, application_stylesheet
from dms.ui.modern_button import ModernButton
from dms.ui.style_tokens import DARK_TOKENS, BRAND_TOKENS, LIGHT_TOKENS, tokens_for


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
    assert DARK_TOKENS.background == "#14171C"
    assert DARK_TOKENS.viewport == "#1A1A1A"
    assert LIGHT_TOKENS.viewport == "#FFFFFF"
    assert BRAND_TOKENS.background == "#232323"
    assert BRAND_TOKENS.accent == "#7A7A7A"
    assert BRAND_TOKENS.danger == "#6E6E6E"
    assert BRAND_TOKENS.typography.heading_family == "Heading"
    assert tokens_for(LIGHT) is LIGHT_TOKENS
    assert tokens_for(DARK, brand_mode=True) is BRAND_TOKENS


def test_stylesheet_exposes_surface_and_tab_hierarchy() -> None:
    stylesheet = application_stylesheet(DARK)
    assert 'QWidget[surfaceLevel="viewport"]' in stylesheet
    assert 'QWidget[surfaceLevel="panel"]' in stylesheet
    assert "border-top-left-radius: 8px" in stylesheet
    assert "border-bottom: 2px solid #66ccff" in stylesheet


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
