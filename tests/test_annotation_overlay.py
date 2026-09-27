import math

import numpy as np
import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtTest import QTest

from dms.ui.annotation_overlay import (
    AnnotationOverlay,
    Stroke,
    Tape,
    format_frequency,
    readout_text,
    tape_label,
)
from dms.ui.curator_graph_widget import GraphWidget

LOG_F = np.log10(np.geomspace(20.0, 20000.0, 200))
CURVES = [("low", LOG_F, np.full_like(LOG_F, -3.0)), ("high", LOG_F, np.full_like(LOG_F, 4.0))]


@pytest.fixture
def overlay(qapp):
    graph = GraphWidget()
    graph.setYRange(-10.0, 10.0, padding=0)
    graph.resize(800, 450)
    graph.show()
    qapp.processEvents()
    widget = AnnotationOverlay(graph, curves=lambda: CURVES)
    yield widget
    graph.close()
    graph.deleteLater()


def test_undo_order_and_clear(qapp):
    graph = GraphWidget()
    overlay = AnnotationOverlay(graph)
    overlay.begin_stroke((2.0, 0.0))
    overlay.extend_stroke((2.5, 1.0))
    overlay.tape_click((3.0, 0.0), snap=False)
    tape = overlay.tape_click((3.3, -2.0), snap=False)
    overlay.begin_stroke((1.5, 3.0))
    assert [type(item) for item in overlay.items] == [Stroke, Tape, Stroke]
    overlay.undo()
    assert overlay.items[-1] is tape
    overlay.undo()
    assert [type(item) for item in overlay.items] == [Stroke]
    overlay.clear()
    assert overlay.items == []
    overlay.undo()
    graph.deleteLater()


def test_labels():
    assert tape_label((3.0, 2.0), (3.0 + 2 * math.log10(2.0), -4.2)) == "−6.2 dB · 2.0 oct"
    assert readout_text(math.log10(3020.0), 4.3) == "3.02 kHz  +4.3 dB"
    assert format_frequency(740.0) == "740 Hz"
    assert format_frequency(12500.0) == "12.5 kHz"


def test_snap_picks_nearest_curve_sample_only_when_close(qapp):
    graph = GraphWidget()
    graph.resize(1000, 600)
    overlay = AnnotationOverlay(graph, curves=lambda: CURVES)
    # Within a few pixels of the curve at +4 dB: snaps onto its sample.
    x, y = overlay.snap(3.001, 3.7)
    assert y == 4.0
    assert x == LOG_F[np.argmin(np.abs(LOG_F - 3.001))]
    assert overlay.snap(3.001, -2.7)[1] == -3.0
    # Far from every curve: the point stays where it was clicked.
    assert overlay.snap(3.001, 12.0) == (3.001, 12.0)
    overlay.curves = lambda: []
    assert overlay.snap(3.001, 3.7) == (3.001, 3.7)
    graph.deleteLater()


def test_pen_width_is_clamped(qapp):
    graph = GraphWidget()
    overlay = AnnotationOverlay(graph)
    overlay.set_pen_width(9)
    assert overlay.pen_width == 6
    overlay.set_pen_width(1)
    assert overlay.pen_width == 2
    graph.deleteLater()


def test_laser_dots_fade_and_stop_the_timer(qapp):
    graph = GraphWidget()
    overlay = AnnotationOverlay(graph)
    overlay.add_laser_dot((3.0, 0.0), now=100.0)
    assert overlay._laser_timer.isActive()
    overlay._tick_laser(now=101.0)
    assert len(overlay._laser) == 1
    overlay._tick_laser(now=102.5)
    assert overlay._laser == []
    assert not overlay._laser_timer.isActive()
    graph.deleteLater()


def test_round_trip_after_resize(overlay, qapp):
    overlay.parentWidget().resize(1000, 600)
    qapp.processEvents()
    assert overlay.size() == overlay.parentWidget().viewport().size()
    point = overlay.data_to_widget(3.0, 1.5)
    assert 0 < point.x() < overlay.width()
    x, y = overlay.widget_to_data(point)
    assert abs(x - 3.0) < 1e-9
    assert abs(y - 1.5) < 1e-9


def test_draw_and_shift_drag_with_mouse(overlay):
    overlay.set_mode("draw")
    QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(100, 200))
    QTest.mouseMove(overlay, QPoint(150, 210))
    QTest.mouseMove(overlay, QPoint(200, 220))
    QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(200, 220))
    stroke = overlay.items[-1]
    assert isinstance(stroke, Stroke)
    assert len(stroke.points) >= 3
    start = overlay.widget_to_data(QPointF(100, 200))
    assert stroke.points[0] == pytest.approx(start)

    shift = Qt.KeyboardModifier.ShiftModifier
    QTest.mousePress(overlay, Qt.MouseButton.LeftButton, shift, QPoint(300, 100))
    QTest.mouseMove(overlay, QPoint(320, 140))
    QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, shift, QPoint(400, 150))
    line = overlay.items[-1]
    assert len(line.points) == 2
    assert line.points[1] == pytest.approx(overlay.widget_to_data(QPointF(400, 150)))


def test_tape_snaps_with_mouse_and_alt_bypasses(overlay):
    overlay.set_mode("tape")
    near_high = overlay.data_to_widget(3.0, 3.0).toPoint()
    near_low = overlay.data_to_widget(3.0, -2.0).toPoint()
    QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=near_high)
    QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=near_low)
    tape = overlay.items[-1]
    assert isinstance(tape, Tape)
    assert (tape.a[1], tape.b[1]) == (4.0, -3.0)
    assert tape_label(tape.a, tape.b).startswith("−7.0 dB")

    alt = Qt.KeyboardModifier.AltModifier
    QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, alt, near_high)
    QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, alt, near_low)
    free = overlay.items[-1]
    assert free.a == pytest.approx(overlay.widget_to_data(QPointF(near_high)))
    assert abs(free.a[1] - 4.0) > 0.1
