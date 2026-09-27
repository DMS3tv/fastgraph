import numpy as np
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from dms.curator.models import CurveData
from dms.settings_manager import SettingsManager
from dms.theme import ThemeController
from dms.ui.present_window import PresentWindow

FREQS = np.geomspace(20.0, 20000.0, 64)


def _add_layers(curator, count: int = 2) -> None:
    for index in range(count):
        curve = CurveData(kind="fr", freqs=FREQS, mag_db=np.full_like(FREQS, float(index)))
        curator.add_curve(curve, f"Layer {index + 1}", normalize=False, animate=False)


@pytest.fixture
def present(qapp):
    settings = SettingsManager()
    controller = ThemeController(qapp, settings)
    window = PresentWindow(settings, controller)
    window.show()
    window.activateWindow()
    QTest.qWaitForWindowActive(window)
    yield window
    window.close()
    window.deleteLater()


def _key(window, key, modifier=Qt.KeyboardModifier.NoModifier) -> None:
    QTest.keyClick(window, key, modifier)
    QApplication.processEvents()


def _assert_presentation_chrome(window) -> None:
    curator = window.curator
    stage = curator._graph_stage
    assert not curator._export_btn.isVisible()
    assert not curator._poster_section.isVisible()
    for widget in (stage.title_input, stage.fixture_input, stage.hrtf_note_input):
        assert not widget.isVisible()
    assert window.graph_frame.isVisible()
    assert not stage._poster_preview.isVisible()
    assert not hasattr(curator, "_present_btn")


def test_window_hides_export_and_poster_ui_across_theme_changes(present, fake_brand) -> None:
    _assert_presentation_chrome(present)
    present._theme_controller.set_theme("dither", persist=False)
    _assert_presentation_chrome(present)
    present._theme_controller.set_brand_mode(True, persist=False)
    assert present.curator._brand_mode
    _assert_presentation_chrome(present)


def test_mode_keys_set_the_overlay_mode(present) -> None:
    for key, mode in (
        (Qt.Key.Key_D, "draw"),
        (Qt.Key.Key_T, "tape"),
        (Qt.Key.Key_L, "laser"),
        (Qt.Key.Key_Escape, "pointer"),
    ):
        _key(present, key)
        assert present.overlay.mode == mode
    assert present._mode_buttons["pointer"].role() == "primary"


def test_number_keys_toggle_layers_and_h_solos(present) -> None:
    _add_layers(present.curator, 3)
    layers = present.curator.graph_state.layers
    _key(present, Qt.Key.Key_1)
    assert [layer.visible for layer in layers] == [False, True, True]
    _key(present, Qt.Key.Key_1)
    _key(present, Qt.Key.Key_9)
    assert all(layer.visible for layer in layers)

    present.curator._selected_layer_id = layers[1].id
    _key(present, Qt.Key.Key_H)
    assert [layer.visible for layer in layers] == [False, True, False]
    _key(present, Qt.Key.Key_H)
    assert all(layer.visible for layer in layers)


def test_panel_scale_and_pen_keys(present) -> None:
    _key(present, Qt.Key.Key_F)
    assert not present.curator._controls_scroll.isVisible()
    _key(present, Qt.Key.Key_F)
    assert present.curator._controls_scroll.isVisible()
    _key(present, Qt.Key.Key_S)
    assert present.overlay.font_scale == 1.5
    _key(present, Qt.Key.Key_BracketRight)
    assert present.overlay.pen_width == 4
    _key(present, Qt.Key.Key_C)
    assert present.overlay.crosshair


def test_cmd_c_copies_the_graph_to_the_clipboard(present) -> None:
    QApplication.clipboard().clear()
    _key(present, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert not QApplication.clipboard().pixmap().isNull()


def test_present_button_opens_a_window_with_the_same_layers(make_main_window) -> None:
    window = make_main_window()
    _add_layers(window._curator_widget, 2)
    window._curator_widget.graph_state.layers[1].visible = False
    window._curator_widget._present_btn.click()
    present = window._present_window
    assert present is not None
    layers = present.curator.graph_state.layers
    assert [layer.name for layer in layers] == ["Layer 1", "Layer 2"]
    assert [layer.visible for layer in layers] == [True, False]
    present.close()


def test_main_selects_the_present_window(qapp, monkeypatch) -> None:
    import main

    monkeypatch.delenv("FASTGRAPH_MODE", raising=False)
    assert main.wants_present_mode(["main.py", "--present"])
    assert not main.wants_present_mode(["main.py"])
    monkeypatch.setenv("FASTGRAPH_MODE", "present")
    assert main.wants_present_mode(["main.py"])

    settings = SettingsManager()
    controller = ThemeController(qapp, settings)
    window = main.build_window(settings, controller, present=True)
    assert isinstance(window, PresentWindow)
    window.close()
    window.deleteLater()
