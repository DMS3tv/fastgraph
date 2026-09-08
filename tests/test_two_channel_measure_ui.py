import numpy as np
from PyQt6.QtWidgets import QLabel

from dms.ui.measure_workspace import MeasureWorkspace, TwoChannelMeasureWidget


def test_workspace_moves_shared_controls_between_measure_modes(qapp) -> None:
    assert qapp is not None
    workspace = MeasureWorkspace()
    header = QLabel("Header")
    middle = QLabel("Middle")
    footer = QLabel("Footer")
    workspace.set_header_widget(header)
    workspace.set_between_plots_widget(middle)
    workspace.set_footer_widget(footer)

    assert workspace._stack.currentWidget() is workspace.single
    workspace.set_two_channel_enabled(True)

    assert workspace._stack.currentWidget() is workspace.two
    assert workspace.two._header_widget is header
    assert workspace.two._between_widget is middle
    assert workspace.two._footer_widget is footer

    workspace.set_two_channel_enabled(False)
    assert workspace._stack.currentWidget() is workspace.single
    assert workspace.single._header_widget is header


def test_two_channel_layout_defaults_to_combined_and_channel_one_selection(qapp) -> None:
    assert qapp is not None
    widget = TwoChannelMeasureWidget()

    assert widget.selection == "combined"
    assert "Channel 2" in widget._top_2.plot.getPlotItem().titleLabel.text
    assert "Channel 1" in widget._top_1.plot.getPlotItem().titleLabel.text

    widget.set_bottom_mode("separate")
    assert widget.selection == "channel_1"
    assert "2px solid" in widget._bottom_1.frame.styleSheet()
    assert "1px solid" in widget._bottom_2.frame.styleSheet()
    widget.set_selection("channel_2")
    assert widget.selection == "channel_2"
    assert "2px solid" in widget._bottom_2.frame.styleSheet()


def test_scope_uses_synchronized_time_and_amplitude_ranges(qapp) -> None:
    assert qapp is not None
    widget = TwoChannelMeasureWidget()
    samples = np.sin(2.0 * np.pi * np.arange(480, dtype=float) / 48.0)
    widget.update_scope(samples, samples * 0.8, 48000, -3.0, -5.0, 2.0)

    overlay_range = widget._overlay_scope.plot.viewRange()
    delta_range = widget._delta_scope.plot.viewRange()
    np.testing.assert_allclose(overlay_range[0], delta_range[0])
    np.testing.assert_allclose(overlay_range[1], delta_range[1])
    assert "L -3.0 dBFS" in widget._readout.text()
    assert "R -5.0 dBFS" in widget._readout.text()
    assert "Δ +2.0 dB" in widget._readout.text()
