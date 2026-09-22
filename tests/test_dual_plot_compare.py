"""Target, reference-layer and delta-view bookkeeping in the bottom viewport."""

import numpy as np
from helpers import ramp_curve

from dms.ui.dual_plot_widget import (
    _AVERAGE_TITLE,
    _DELTA_TITLE,
    _DELTA_Y_LIMIT_DB,
    DualPlotWidget,
)


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
