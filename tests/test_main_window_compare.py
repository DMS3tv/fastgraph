"""Target comparison in the Measure tab: delta view, deviation, EQ, A/B layers."""

from pathlib import Path

import numpy as np
from PyQt6.QtWidgets import QLabel

import dms.ui.measure_compare as compare_module
from dms.measure_queue import QueueState
from dms.ui.dual_plot_widget import _AVERAGE_TITLE, _DELTA_TITLE
from dms.ui.eq_suggestion_dialog import EqSuggestionDialog


def _shaped_curve() -> tuple[np.ndarray, np.ndarray]:
    """A measurement with a broad bass lift and a treble dip."""
    freqs = np.geomspace(20.0, 20000.0, 240)
    octaves = np.log2(freqs / 1000.0)
    mag = 4.0 * np.exp(-((octaves + 4.0) ** 2) / 4.0)
    mag -= 5.0 * np.exp(-((octaves - 2.5) ** 2) / 0.5)
    return freqs, mag - float(np.interp(1000.0, freqs, mag))


def _write_target(path: Path, tilt_db: float = 6.0) -> Path:
    """A two-column tilted line: ``tilt_db`` at 20 Hz down to -tilt at 20 kHz."""
    freqs = np.geomspace(20.0, 20000.0, 120)
    values = np.linspace(tilt_db, -tilt_db, freqs.size)
    path.write_text(
        "\n".join(f"{f:.4f}\t{v:.4f}" for f, v in zip(freqs, values)) + "\n",
        encoding="utf-8",
    )
    return path


def _window_with_target(make_main_window, tmp_path, **kwargs):
    window = make_main_window(**kwargs)
    window.measure.kept_curves.append(_shaped_curve())
    window.measure.kept_sweep_meta.append({})
    window.measure.recompute_average()
    window.measure.refresh()
    target = _write_target(tmp_path / "target.txt")
    assert window.measure_compare.load_target(str(target)) is True
    return window, target


def test_compare_menu_sits_after_the_level_combo(make_main_window) -> None:
    window = make_main_window()
    row = window._plots._between_plots_widget.layout()
    widgets = [row.itemAt(index).widget() for index in range(row.count())]

    level_index = widgets.index(window.measure_tab.level_mode_combo)
    assert widgets[level_index + 1] is window.measure_tab.compare_menu_btn
    assert widgets[level_index + 2] is window.measure_tab.undo_btn
    assert window.measure_tab.compare_menu_btn.property("menuButton") is True
    assert [
        action.text()
        for action in window.measure_compare._compare_menu.actions()
        if not action.isSeparator()
    ] == [
        "Load Target…",
        "Clear Target",
        "Delta View (measurement − target)",
        "Load Reference…",
        "Clear References",
        "EQ Suggestion…",
    ]


def test_loading_a_target_draws_it_and_is_remembered(tmp_path, make_main_window) -> None:
    window, target = _window_with_target(make_main_window, tmp_path)

    assert window.measure_compare._measure_target is not None
    assert window._settings.get("measure_target_path") == str(target)
    assert len(window._plots.single._compare_items) == 1
    assert window.measure_compare._delta_view_action.isEnabled() is True

    window.measure_compare.clear_target()
    assert window.measure_compare._measure_target is None
    assert window._settings.get("measure_target_path") == ""
    assert window._plots.single._compare_items == []
    assert window.measure_compare._delta_view_action.isEnabled() is False


def test_delta_view_swaps_the_bottom_curve(tmp_path, make_main_window) -> None:
    window, _target = _window_with_target(make_main_window, tmp_path)
    plot = window._plots.single
    plain_average = plot._last_average
    assert plot._bot_plot.getPlotItem().titleLabel.text == _AVERAGE_TITLE

    window.measure_compare._delta_view_action.setChecked(True)

    assert window._settings.get("measure_delta_view") is True
    assert plot._delta_mode is True
    assert plot._bot_plot.getPlotItem().titleLabel.text == _DELTA_TITLE
    delta_curve = plot._last_average
    assert delta_curve is not None
    assert delta_curve[1].shape != plain_average[1].shape or not np.allclose(
        delta_curve[1], plain_average[1]
    )
    # A delta against a tilted target is nothing like the response itself.
    expected = window.measure_compare.delta_result(
        window.measure.bottom_curve_for_display_and_export()
    )
    np.testing.assert_allclose(delta_curve[1], expected.delta_db)

    window.measure_compare._delta_view_action.setChecked(False)
    assert plot._delta_mode is False
    assert plot._bot_plot.getPlotItem().titleLabel.text == _AVERAGE_TITLE


def test_delta_view_needs_a_target(make_main_window) -> None:
    window = make_main_window()
    seen: list[str] = []
    monkeypatched = type(
        "QMessageBoxStub",
        (),
        {
            "Icon": compare_module.QMessageBox.Icon,
            "StandardButton": compare_module.QMessageBox.StandardButton,
            "information": staticmethod(lambda *args, **kwargs: seen.append("information")),
        },
    )
    original = compare_module.QMessageBox
    compare_module.QMessageBox = monkeypatched
    try:
        window.measure_compare._delta_view_action.setEnabled(True)
        window.measure_compare._delta_view_action.setChecked(True)
    finally:
        compare_module.QMessageBox = original

    assert seen == ["information"]
    assert window.measure_compare._delta_view_action.isChecked() is False


def test_review_dialog_shows_the_deviation_summary(tmp_path, make_main_window) -> None:
    window, _target = _window_with_target(make_main_window, tmp_path)
    window.measure.queue.state = QueueState.PASS_FAIL
    window.measure.queue.target = 1
    window.measure.queue.index = 0
    window.measure.queue.pending_curve = _shaped_curve()

    summary = window.measure_compare.pending_deviation_summary()
    assert summary is not None
    assert "Overall" in summary

    window.measure.show_pass_fail_dialog()
    dialog = window.measure.pass_fail_dialog
    assert dialog is not None
    try:
        texts = [label.text() for label in dialog.findChildren(QLabel)]
        assert "Deviation from target" in texts
        assert summary in texts
    finally:
        window.measure.close_pass_fail_dialog()
        window.measure.queue.state = QueueState.IDLE


def test_keeping_appends_a_match_percentage(tmp_path, make_main_window) -> None:
    window, _target = _window_with_target(make_main_window, tmp_path)
    window.measure.queue.state = QueueState.PASS_FAIL
    window.measure.queue.target = 2
    window.measure.queue.index = 0
    window.measure.queue.pending_curve = _shaped_curve()
    window.measure.start_next_sweep = lambda **_kwargs: None

    window.measure.on_keep()

    assert "Match:" in window._statusbar.currentMessage()


def test_eq_dialog_reports_an_apo_preset(tmp_path, make_main_window) -> None:
    window, _target = _window_with_target(make_main_window, tmp_path)
    dialog = EqSuggestionDialog(
        window.measure.bottom_curve_for_display_and_export(),
        window.measure_compare._measure_target,
        offset_mode=window.measure_compare.delta_offset_mode(),
        parent=window,
    )
    try:
        assert dialog.apo_text().startswith("Preamp:")
        report = dialog.report_text()
        assert "Preamp:" in report
        assert "Residual RMS" in report
        assert "Match" in report
        assert dialog._max_filters_spin.value() == 8
        assert dialog._max_gain_spin.value() == 12.0

        dialog._max_filters_spin.setValue(2)
        assert dialog.apo_text().count("Filter ") <= 2
    finally:
        dialog.deleteLater()


def test_eq_suggestion_needs_a_target_and_an_average(tmp_path, make_main_window) -> None:
    window = make_main_window()

    window.measure_compare.open_eq_suggestion()
    assert "target" in window._statusbar.currentMessage().lower()

    target = _write_target(tmp_path / "t.txt")
    assert window.measure_compare.load_target(str(target)) is True
    window.measure_compare.open_eq_suggestion()
    assert "averaged" in window._statusbar.currentMessage().lower()


def test_reference_layers_draw_and_clear(tmp_path, make_main_window) -> None:
    window = make_main_window()
    window.measure.kept_curves.append(_shaped_curve())
    window.measure.kept_sweep_meta.append({})
    window.measure.recompute_average()
    window.measure.refresh()

    for index in range(3):
        path = _write_target(tmp_path / f"ref{index}.txt", tilt_db=float(index + 1))
        assert window.measure_compare.load_reference(str(path)) is True

    assert len(window.measure_compare._measure_reference_layers) == 3
    assert len(window._plots.single._compare_items) == 3
    colors = {layer[3] for layer in window._plots.single._reference_layers}
    assert len(colors) == 3

    # A fourth is refused rather than crowding the viewport.
    extra = _write_target(tmp_path / "ref3.txt")
    original = compare_module.QMessageBox
    compare_module.QMessageBox = type(
        "QMessageBoxStub",
        (),
        {
            "Icon": original.Icon,
            "StandardButton": original.StandardButton,
            "information": staticmethod(lambda *args, **kwargs: None),
        },
    )
    try:
        assert window.measure_compare.load_reference(str(extra)) is False
    finally:
        compare_module.QMessageBox = original
    assert len(window.measure_compare._measure_reference_layers) == 3

    window.measure_compare.clear_references()
    assert window.measure_compare._measure_reference_layers == []
    assert window._plots.single._compare_items == []


def test_missing_remembered_target_is_dropped_silently(tmp_path, make_main_window) -> None:
    missing = tmp_path / "gone.txt"
    window = make_main_window(settings={"measure_target_path": str(missing)})

    assert window.measure_compare._measure_target is None
    assert window._settings.get("measure_target_path") == ""
