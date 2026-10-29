"""Target, reference-layer and delta-view bookkeeping in the bottom viewport."""

import numpy as np
from helpers import ramp_curve

from dms.processing import VariationBand
from dms.theme import DARK, FASTGRAPH_95, HACKERMAN_95, LIGHT
from dms.ui.dual_plot_widget import _AVERAGE_TITLE, _DELTA_TITLE, _DELTA_Y_LIMIT_DB, DualPlotWidget


def _plot_widget(qapp) -> DualPlotWidget:
    widget = DualPlotWidget()
    widget.update_curves(kept=[ramp_curve()], average=ramp_curve(), variation=None)
    return widget


def test_target_and_reference_layers_are_tracked_and_cleared(qapp) -> None:
    widget = _plot_widget(qapp)
    try:
        assert widget._compare_items == []

        widget.set_target_curve(*ramp_curve(-1.0))
        assert len(widget._compare_items) == 1
        assert widget._compare_legend is not None
        assert widget._compare_legend.isVisible()

        freqs, mag = ramp_curve(2.0)
        widget.set_reference_layers(
            [
                ("Ref A", freqs, mag, "#4c9be8"),
                ("Ref B", freqs, mag + 1.0, "#e8845c"),
            ]
        )
        assert len(widget._compare_items) == 3

        widget.set_reference_layers(None)
        assert len(widget._compare_items) == 1

        widget.set_target_curve(None)
        assert widget._compare_items == []
        assert not widget._compare_legend.isVisible()
    finally:
        widget.deleteLater()


def test_reference_layers_reject_mismatched_arrays(qapp) -> None:
    widget = _plot_widget(qapp)
    try:
        freqs, mag = ramp_curve()
        widget.set_reference_layers(
            [
                ("Short", freqs[:4], mag, "#4c9be8"),
                ("Empty", np.array([]), np.array([]), "#4c9be8"),
            ]
        )
        assert widget._compare_items == []
    finally:
        widget.deleteLater()


def test_delta_mode_retitles_pins_the_range_and_hides_comparisons(qapp) -> None:
    widget = _plot_widget(qapp)
    try:
        widget.set_target_curve(*ramp_curve(-1.0))
        assert len(widget._compare_items) == 1

        widget.set_delta_mode(True)
        assert widget._delta_mode is True
        assert widget._delta_zero_line is not None
        assert widget._bot_plot.getPlotItem().titleLabel.text == _DELTA_TITLE
        low, high = widget._bot_plot.getPlotItem().getViewBox().viewRange()[1]
        assert low == -_DELTA_Y_LIMIT_DB
        assert high == _DELTA_Y_LIMIT_DB
        # A delta is not a response curve, so the aspect lock is released.
        assert widget._bot_plot.getPlotItem().getViewBox().state["aspectLocked"] is False
        # The target itself is meaningless on a delta axis.
        assert widget._compare_items == []

        widget.set_delta_mode(False)
        assert widget._delta_zero_line is None
        assert widget._bot_plot.getPlotItem().titleLabel.text == _AVERAGE_TITLE
        assert len(widget._compare_items) == 1
    finally:
        widget.deleteLater()


def test_clear_all_removes_every_comparison_item(qapp) -> None:
    widget = _plot_widget(qapp)
    try:
        freqs, mag = ramp_curve()
        widget.set_target_curve(freqs, mag - 1.0)
        widget.set_reference_layers([("Ref", freqs, mag, "#4c9be8")])
        widget.set_delta_mode(True)
        assert widget._delta_mode is True

        widget.clear_all()

        assert widget._compare_items == []
        assert widget._target_curve is None
        assert widget._reference_layers == []
        assert widget._delta_mode is False
        assert widget._delta_zero_line is None
    finally:
        widget.deleteLater()


def test_plot_theme_change_preserves_curves(qapp) -> None:
    widget = DualPlotWidget()
    curve = (np.array([100.0, 1000.0]), np.array([1.0, 0.0]))
    widget.update_curves([curve], curve)
    top_count = len(widget._sweep_items)

    widget.apply_theme(LIGHT)
    widget.apply_theme(FASTGRAPH_95)
    assert all(item.opts["antialias"] is True for item in widget._sweep_items)
    assert widget._bot_item is not None and widget._bot_item.opts["antialias"] is True
    np.testing.assert_array_equal(widget._sweep_items[0].xData, [100.0, 1000.0, 1000.0])
    np.testing.assert_array_equal(widget._sweep_items[0].yData, [1.0, 1.0, 0.0])
    np.testing.assert_array_equal(widget._kept_curves[0][0], curve[0])
    np.testing.assert_array_equal(widget._kept_curves[0][1], curve[1])
    widget.apply_theme(HACKERMAN_95)
    assert widget._bot_item is not None
    assert widget._bot_item.opts["pen"].color().name().upper() == "#39FF14"
    np.testing.assert_array_equal(widget._sweep_items[0].xData, [100.0, 1000.0, 1000.0])
    widget.apply_theme(DARK)

    assert len(widget._sweep_items) == top_count
    assert all(item.opts["antialias"] is True for item in widget._sweep_items)
    np.testing.assert_array_equal(widget._sweep_items[0].xData, curve[0])
    np.testing.assert_array_equal(widget._sweep_items[0].yData, curve[1])
    assert widget._bot_plot.backgroundBrush().color().name() == "#1a1a1a"


def test_sweeps_average_and_pending_share_one_graph(qapp) -> None:
    widget = DualPlotWidget()
    try:
        sweep = (np.array([100.0, 1000.0]), np.array([1.0, 0.0]))
        pending = (np.array([100.0, 1000.0]), np.array([3.0, 0.0]))
        widget.update_curves([sweep, sweep], sweep, pending=pending)

        assert widget._bot_plot.getPlotItem().titleLabel.text == _AVERAGE_TITLE
        assert len(widget._sweep_items) == 2
        assert widget._bot_item is not None
        assert widget._pending_item is not None
        assert widget._pending_item.zValue() > widget._bot_item.zValue()
        assert widget._pending_item.opts["pen"].color() != widget._bot_item.opts["pen"].color()

        # Variation replaces sweeps and average; the pending sweep stays on top.
        flat = np.zeros(2)
        band = VariationBand(
            freqs=np.array([100.0, 1000.0]), p10=flat, p25=flat, median=flat, p75=flat, p90=flat
        )
        widget.update_curves([sweep], sweep, band, "variation", pending=pending)
        assert widget._sweep_items == [] and widget._bot_item is None
        assert widget._pending_item is not None

        # Keep or Fail clears it.
        widget.update_curves([sweep, sweep], sweep)
        assert widget._pending_item is None
    finally:
        widget.deleteLater()
