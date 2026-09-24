from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QFormLayout, QMenu, QWidget

import dms.ui.curator_widget as curator_widget_module
from dms.curator.models import CurveData
from dms.theme import DARK
from dms.ui.modern_button import ModernButton


@pytest.fixture(autouse=True)
def _brand(fake_brand):
    return fake_brand


def _fr_curve() -> CurveData:
    return CurveData(kind="fr", freqs=np.array([100.0, 1000.0]), mag_db=np.array([1.0, 2.0]))


def _metadata_curve(brand: str, model: str, rig: str = "B&K 5128") -> CurveData:
    return CurveData(
        kind="fr",
        freqs=np.array([100.0, 1000.0]),
        mag_db=np.array([1.0, 2.0]),
        metadata={
            "brand": brand,
            "model": model,
            "rig": rig,
            "anc_mode": True,
            "eq_applied": False,
            "connection": "Bluetooth",
        },
    )


def test_apply_theme_toggles_export_button_text_and_poster_group_visibility(
    make_curator, qapp
) -> None:
    window = make_curator()
    window.show()
    qapp.processEvents()

    window.apply_theme(DARK, brand_mode=True)
    qapp.processEvents()
    assert window._export_btn.text() == "Export 4K PNG..."
    assert window._poster_section.isVisible()

    window.apply_theme(DARK, brand_mode=False)
    qapp.processEvents()
    assert window._export_btn.text() == "Export 1080p PNG..."
    assert not window._poster_section.isVisible()

    window.close()


def test_poster_clean_slate_disables_all_poster_text_fields(make_curator, qapp) -> None:
    window = make_curator(brand_mode=True)

    window._poster_clean_slate_enabled.setChecked(True)

    assert window.graph_state.poster_clean_slate is True
    assert all(not editor.isEnabled() for editor in window._export_field_widgets.values())
    assert not window._poster_metadata_source_combo.isEnabled()
    assert not window._poster_fill_metadata_btn.isEnabled()

    window._poster_clean_slate_enabled.setChecked(False)

    assert window.graph_state.poster_clean_slate is False
    assert all(editor.isEnabled() for editor in window._export_field_widgets.values())
    window.close()


def test_curator_uses_modern_buttons_and_scrolls_small_poster_panel(make_curator, qapp) -> None:
    window = make_curator(brand_mode=True)
    window.resize(1280, 620)
    window.show()
    qapp.processEvents()

    controls_scroll = window._splitter.widget(1)
    assert isinstance(window._export_btn, ModernButton)
    assert controls_scroll.verticalScrollBar().maximum() > 0
    assert window._poster_section.parent() is window.findChild(QWidget, "controlPanel")
    window.close()


def test_curator_sections_below_data_are_collapsible_in_both_modes(make_curator, qapp) -> None:
    window = make_curator(brand_mode=True)
    window.show()
    qapp.processEvents()

    window._view_section_toggle.setChecked(False)
    window._poster_section_toggle.setChecked(False)
    qapp.processEvents()
    assert not window._view_box.isVisible()
    assert not window._poster_box.isVisible()

    window.apply_theme(DARK, brand_mode=False)
    window._view_section_toggle.setChecked(True)
    qapp.processEvents()
    assert window._view_box.isVisible()
    assert not window._poster_section.isVisible()
    window.close()


def test_layer_and_panel_scroll_positions_survive_layer_rebuild(make_curator, qapp) -> None:
    window = make_curator(brand_mode=True)
    window._layer_list.setFixedHeight(220)
    for index in range(24):
        window.add_curve(_fr_curve(), f"Layer {index}", animate=False)
    window._sync_ui()
    window.resize(1050, 620)
    window.show()
    qapp.processEvents()

    layer_bar = window._layer_list.verticalScrollBar()
    panel_bar = window._controls_scroll.verticalScrollBar()
    assert layer_bar.maximum() > 0
    assert panel_bar.maximum() > 0
    layer_target = layer_bar.maximum() // 2
    panel_target = panel_bar.maximum() // 2
    layer_bar.setValue(layer_target)
    panel_bar.setValue(panel_target)

    window._set_layer_visible(window.graph_state.layers[4].id, False)
    qapp.processEvents()

    assert layer_bar.value() == layer_target
    assert panel_bar.value() == panel_target
    window.close()


def test_new_layers_cycle_brand_trace_palette_colors(make_curator, qapp, fake_brand) -> None:
    window = make_curator(brand_mode=True)
    curve = _fr_curve()

    layer_count = len(fake_brand.trace_palette) + 2
    for index in range(layer_count):
        window.add_curve(curve, f"Layer {index}", animate=False)

    colors = [layer.color for layer in window.graph_state.layers]
    palette = fake_brand.trace_palette
    expected = [palette[index % len(palette)] for index in range(layer_count)]
    assert colors == expected
    window.close()


def test_poster_footer_field_survives_title_edit(make_curator, qapp) -> None:
    window = make_curator(brand_mode=True)

    window._poster_footer1_edit.setText("Custom Footer")
    assert window.graph_state.export_text.poster_footer_1 == "Custom Footer"

    window._graph_stage.title_input.setText("New Title")

    assert window.graph_state.export_text.poster_footer_1 == "Custom Footer"
    assert window.graph_state.export_text.title == "New Title"
    window.close()


def test_title_survives_poster_footer_field_edit(make_curator, qapp) -> None:
    window = make_curator(brand_mode=True)

    window._graph_stage.title_input.setText("Kept Title")
    assert window.graph_state.export_text.title == "Kept Title"

    window._poster_footer1_edit.setText("New Footer")

    assert window.graph_state.export_text.title == "Kept Title"
    assert window.graph_state.export_text.poster_footer_1 == "New Footer"
    window.close()


def test_choose_layer_color_menu_orders_current_color_first_in_brand_mode(
    make_curator, qapp, monkeypatch, fake_brand
) -> None:
    window = make_curator(brand_mode=True)
    layer = window.add_curve(_fr_curve(), "Layer", animate=False)
    window._sync_ui()
    row = window._layer_list.itemWidget(window._layer_list.item(0))

    captured_menus: list[QMenu] = []

    def fake_exec(self, *args, **kwargs):
        captured_menus.append(self)
        return None

    monkeypatch.setattr(QMenu, "exec", fake_exec)

    window._choose_layer_color(layer.id, row.color_btn)

    assert len(captured_menus) == 1
    actions = captured_menus[0].actions()
    assert layer.color in actions[0].text()
    assert "(current)" in actions[0].text()

    remaining_texts = {action.text() for action in actions[1:]}
    expected_remaining = {
        color for color in fake_brand.trace_palette if color.lower() != layer.color.lower()
    }
    assert remaining_texts == expected_remaining
    window.close()


def test_export_png_uses_brand_size_and_brand_mode_flag(
    make_curator, qapp, tmp_path: Path, monkeypatch, fake_brand
) -> None:
    window = make_curator(brand_mode=True)
    captured: dict[str, object] = {}

    def fake_export(state, path, size=(1920, 1080), *, brand_mode=False, theme="dark"):
        captured["size"] = size
        captured["brand_mode"] = brand_mode
        captured["theme"] = theme

    monkeypatch.setattr(curator_widget_module, "export_graph_image", fake_export)

    window.export_png(tmp_path / "out.png")

    assert captured["size"] == fake_brand.poster_size
    assert captured["brand_mode"] is True
    window.close()


def test_export_png_uses_1080p_size_without_brand_mode(
    make_curator, qapp, tmp_path: Path, monkeypatch
) -> None:
    window = make_curator(brand_mode=False)
    captured: dict[str, object] = {}

    def fake_export(state, path, size=(1920, 1080), *, brand_mode=False, theme="dark"):
        captured["size"] = size
        captured["brand_mode"] = brand_mode
        captured["theme"] = theme

    monkeypatch.setattr(curator_widget_module, "export_graph_image", fake_export)

    window.export_png(tmp_path / "out.png")

    assert captured["size"] == (1920, 1080)
    assert captured["brand_mode"] is False
    assert captured["theme"] == "dark"
    window.close()


def test_poster_fields_fill_from_primary_layer_metadata(make_curator, qapp) -> None:
    window = make_curator(brand_mode=True)

    window.add_curve(_metadata_curve("Sony", "WH-1000XM5"), "Sony", animate=False)

    assert window.graph_state.export_text.title == "FREQUENCY RESPONSE"
    assert (
        window.graph_state.export_text.fixture == "SONY WH-1000XM5 | ANC ON | STANDARD | BLUETOOTH"
    )
    assert window.graph_state.export_text.hrtf_note == "B&K 5128"
    assert window._poster_metadata_source_combo.currentData() == window.graph_state.layers[0].id
    window.close()


def test_manual_field_survives_metadata_source_change_and_can_reset(make_curator, qapp) -> None:
    window = make_curator(brand_mode=True)
    first = window.add_curve(_metadata_curve("Sony", "One"), "One", animate=False)
    second = window.add_curve(_metadata_curve("Bose", "Two"), "Two", animate=False)
    window.show()
    qapp.processEvents()

    editor = window._graph_stage.fixture_input
    editor.setFocus()
    editor.selectAll()
    QTest.keyClicks(editor, "CUSTOM HEADPHONE TEXT")
    assert "fixture" in window._manual_export_fields

    source_index = window._poster_metadata_source_combo.findData(second.id)
    window._poster_metadata_source_combo.setCurrentIndex(source_index)
    assert editor.text() == "CUSTOM HEADPHONE TEXT"
    assert editor.property("metadataState") == "manual"

    window._reset_export_field("fixture")
    assert editor.text() == "BOSE TWO | ANC ON | STANDARD | BLUETOOTH"
    assert "fixture" not in window._manual_export_fields
    assert editor.property("metadataState") == "auto"
    assert first.id != second.id
    window.close()


def test_fill_from_metadata_clears_all_manual_states(make_curator, qapp) -> None:
    window = make_curator(brand_mode=True)
    window.add_curve(_metadata_curve("Sony", "One"), "One", animate=False)

    window.set_export_text("title", "MANUAL TITLE")
    window.set_export_text("footer1", "MANUAL FOOTER")
    assert window._manual_export_fields == {"title", "footer1"}

    window._poster_fill_metadata_btn.click()

    assert window._manual_export_fields == set()
    assert window.graph_state.export_text.title == "FREQUENCY RESPONSE"
    assert window.graph_state.export_text.poster_footer_1 == "Sony One"
    window.close()


def test_poster_preview_uses_final_renderer_and_replaces_graph_view(make_curator, qapp) -> None:
    window = make_curator(brand_mode=True)
    window.resize(1200, 760)
    window.show()
    qapp.processEvents()

    assert window._graph_stage._poster_preview.isVisible()
    assert not window._graph_stage._graph_frame.isVisible()
    assert (
        window._graph_stage._poster_preview.geometry()
        == window._graph_stage._graph_frame.geometry()
    )
    window.close()


@pytest.mark.parametrize("size", [(1000, 700), (1800, 1100)])
def test_poster_rows_do_not_overlap_at_common_window_sizes(make_curator, qapp, size) -> None:
    window = make_curator(brand_mode=True)
    window.resize(*size)
    window.show()
    qapp.processEvents()

    assert (
        window._poster_form.fieldGrowthPolicy()
        == QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
    )
    assert (
        window._poster_form.getWidgetPosition(window._poster_metadata_status)[1]
        == QFormLayout.ItemRole.SpanningRole
    )
    assert (
        window._poster_form.getWidgetPosition(window._poster_font_status)[1]
        == QFormLayout.ItemRole.SpanningRole
    )

    rows = [
        window._poster_metadata_source_combo,
        window._poster_fill_metadata_btn,
        window._poster_metadata_status,
        window._poster_font_status,
        window._poster_footer1_edit,
        window._poster_footer2_edit,
        window._poster_legend_bounds_edit,
        window._poster_legend_variation_edit,
    ]
    for previous, current in zip(rows, rows[1:]):
        assert previous.geometry().bottom() < current.geometry().top()

    assert window._poster_footer1_edit.width() >= 180
    window.close()


def test_clear_layers_restores_automatic_field_state(make_curator, qapp) -> None:
    window = make_curator(brand_mode=True)
    window.add_curve(_metadata_curve("Sony", "One"), "One", animate=False)
    window.set_export_text("fixture", "MANUAL")

    window.clear_layers()

    assert window._manual_export_fields == set()
    assert window._poster_metadata_source_combo.currentData() == ""
    assert window._graph_stage.fixture_input.property("metadataState") == "auto"
    assert window._graph_stage.fixture_input.text() == ""
    window.close()
