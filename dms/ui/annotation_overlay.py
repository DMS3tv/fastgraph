"""Ephemeral drawing, measuring tape, crosshair and laser over a Curator graph.

Items live in data coordinates (log10 Hz, dB) and are mapped through the
plot's ViewBox on every paint, so resizing or changing the y range never
detaches them from the curves.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from PyQt6.QtCore import QEvent, QObject, QPointF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import QWidget

Point = tuple[float, float]
Curves = list[tuple[str, np.ndarray, np.ndarray]]

MODES = ("pointer", "draw", "tape", "laser")
DEFAULT_PEN_COLOR = "#FCBE11"
LASER_FADE_S = 2.0
_CURSORS = {
    "pointer": Qt.CursorShape.ArrowCursor,
    "draw": Qt.CursorShape.CrossCursor,
    "tape": Qt.CursorShape.CrossCursor,
    "laser": Qt.CursorShape.BlankCursor,
}


@dataclass
class Stroke:
    points: list[Point] = field(default_factory=list)
    color: str = DEFAULT_PEN_COLOR
    width: int = 3


@dataclass
class Tape:
    a: Point
    b: Point
    color: str = DEFAULT_PEN_COLOR


def format_frequency(hz: float) -> str:
    if hz < 1000.0:
        return f"{hz:.0f} Hz"
    return f"{hz / 1000.0:.{2 if hz < 10000.0 else 1}f} kHz"


def _signed(value: float) -> str:
    return f"{value:+.1f}".replace("-", "−")


def readout_text(x: float, y: float) -> str:
    return f"{format_frequency(10.0**x)}  {_signed(y)} dB"


def tape_label(a: Point, b: Point) -> str:
    octaves = abs(b[0] - a[0]) / math.log10(2.0)
    return f"{_signed(b[1] - a[1])} dB · {octaves:.1f} oct"


class AnnotationOverlay(QWidget):
    def __init__(self, graph, curves: Callable[[], Curves] | None = None) -> None:
        super().__init__(graph)
        self._graph = graph
        self.curves = curves or (lambda: [])
        self.items: list[Stroke | Tape] = []
        self.mode = "pointer"
        self.pen_color = DEFAULT_PEN_COLOR
        self.pen_width = 3
        self.crosshair = False
        self.font_scale = 1.0
        self._active: Stroke | None = None
        self._tape_a: Point | None = None
        self._cursor: QPointF | None = None
        self._laser: list[tuple[float, float, float]] = []
        self._laser_timer = QTimer(self)
        self._laser_timer.setInterval(33)
        self._laser_timer.timeout.connect(self._tick_laser)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        vb = graph.getPlotItem().getViewBox()
        vb.sigTransformChanged.connect(self.update)
        vb.sigResized.connect(self.update)
        graph.viewport().installEventFilter(self)
        self._fit()

    # Geometry -----------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._graph.viewport() and event.type() == QEvent.Type.Resize:
            self._fit()
        return False

    def _fit(self) -> None:
        self.setGeometry(self._graph.viewport().geometry())
        self.raise_()

    def _vb(self):
        return self._graph.getPlotItem().getViewBox()

    def data_to_widget(self, x: float, y: float) -> QPointF:
        scene = self._vb().mapViewToScene(QPointF(x, y))
        return self._graph.viewportTransform().map(scene)

    def widget_to_data(self, point: QPointF) -> Point:
        scene = self._graph.viewportTransform().inverted()[0].map(QPointF(point))
        view = self._vb().mapSceneToView(scene)
        return (view.x(), view.y())

    # Model --------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"Unknown annotation mode: {mode}")
        self.mode = mode
        self._active = None
        self._tape_a = None
        self.setCursor(_CURSORS[mode])
        self.update()

    def set_pen_width(self, width: int) -> None:
        self.pen_width = max(2, min(6, int(width)))

    def set_font_scale(self, scale: float) -> None:
        self.font_scale = float(scale)
        self.update()

    def undo(self) -> None:
        if self.items:
            removed = self.items.pop()
            if removed is self._active:
                self._active = None
        self.update()

    def clear(self) -> None:
        self.items.clear()
        self._active = None
        self._tape_a = None
        self.update()

    def snap(self, x: float, y: float) -> Point:
        """Nearest sample of the nearest curve at frequency ``x``."""
        best: tuple[float, Point] | None = None
        for _name, log_f, db in self.curves():
            if len(log_f) == 0:
                continue
            index = int(np.argmin(np.abs(np.asarray(log_f) - x)))
            distance = abs(float(db[index]) - y)
            if best is None or distance < best[0]:
                best = (distance, (float(log_f[index]), float(db[index])))
        return best[1] if best is not None else (x, y)

    def begin_stroke(self, point: Point) -> None:
        self._active = Stroke([point], self.pen_color, self.pen_width)
        self.items.append(self._active)
        self.update()

    def extend_stroke(self, point: Point, *, straight: bool = False) -> None:
        if self._active is None:
            return
        if straight:
            self._active.points[1:] = [point]
        else:
            self._active.points.append(point)
        self.update()

    def tape_click(self, point: Point, *, snap: bool = True) -> Tape | None:
        if snap:
            point = self.snap(*point)
        if self._tape_a is None:
            self._tape_a = point
            self.update()
            return None
        tape = Tape(self._tape_a, point, self.pen_color)
        self._tape_a = None
        self.items.append(tape)
        self.update()
        return tape

    def add_laser_dot(self, point: Point, now: float | None = None) -> None:
        self._laser.append((point[0], point[1], time.monotonic() if now is None else now))
        if not self._laser_timer.isActive():
            self._laser_timer.start()

    def _tick_laser(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._laser = [dot for dot in self._laser if now - dot[2] < LASER_FADE_S]
        if not self._laser:
            self._laser_timer.stop()
        self.update()

    # Mouse --------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self.mode == "pointer":
            event.ignore()
            return
        point = self.widget_to_data(event.position())
        if self.mode == "draw":
            self.begin_stroke(point)
        elif self.mode == "tape":
            alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
            self.tape_click(point, snap=not alt)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        self._cursor = QPointF(event.position())
        point = self.widget_to_data(event.position())
        if self.mode == "draw":
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.extend_stroke(point, straight=shift)
        elif self.mode == "laser":
            self.add_laser_dot(point)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._active is not None:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.extend_stroke(self.widget_to_data(event.position()), straight=True)
            self._active = None
        event.accept()

    def leaveEvent(self, event) -> None:
        self._cursor = None
        self.update()
        super().leaveEvent(event)

    # Painting -----------------------------------------------------------

    def _font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(round(13 * self.font_scale))
        font.setBold(True)
        return font

    def _text(self, painter: QPainter, anchor: QPointF, text: str, color: QColor) -> None:
        """Draw ``text`` with its top-left near ``anchor``, kept inside the widget."""
        font = self._font()
        path = QPainterPath()
        path.addText(0.0, 0.0, font, text)
        box = path.boundingRect()
        x = min(max(4.0, anchor.x()), self.width() - box.width() - 4.0)
        y = min(max(4.0, anchor.y()), self.height() - box.height() - 4.0)
        path.translate(x - box.left(), y - box.top())
        halo = QColor("#000000" if color.lightnessF() > 0.5 else "#FFFFFF")
        halo.setAlpha(220)
        painter.setPen(
            QPen(
                halo,
                4.0 * self.font_scale,
                cap=Qt.PenCapStyle.RoundCap,
                join=Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.fillPath(path, color)

    def _text_width(self, text: str) -> float:
        path = QPainterPath()
        path.addText(0.0, 0.0, self._font(), text)
        return path.boundingRect().width()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            for item in self.items:
                if isinstance(item, Stroke):
                    self._paint_stroke(painter, item)
                else:
                    self._paint_tape(painter, item)
            self._paint_pending_tape(painter)
            self._paint_laser(painter)
            if self.crosshair and self._cursor is not None:
                self._paint_crosshair(painter, self._cursor)
        finally:
            painter.end()

    def _paint_stroke(self, painter: QPainter, stroke: Stroke) -> None:
        pen = QPen(QColor(stroke.color), stroke.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        points = [self.data_to_widget(*point) for point in stroke.points]
        if len(points) == 1:
            painter.drawPoint(points[0])
        else:
            painter.drawPolyline(QPolygonF(points))

    def _paint_tape(self, painter: QPainter, tape: Tape) -> None:
        color = QColor(tape.color)
        pa = self.data_to_widget(*tape.a)
        pb = self.data_to_widget(*tape.b)
        corner = QPointF(pb.x(), pa.y())
        painter.setBrush(color)
        painter.setPen(QPen(color, 1.5, Qt.PenStyle.DashLine))
        painter.drawLine(pa, pb)
        painter.drawLine(pa, corner)
        painter.setPen(QPen(color, 2.0))
        painter.drawLine(corner, pb)
        for end in (corner, pb):
            painter.drawLine(QPointF(end.x() - 6, end.y()), QPointF(end.x() + 6, end.y()))
        for end in (pa, pb):
            painter.drawEllipse(end, 3.5, 3.5)
        label = tape_label(tape.a, tape.b)
        mid_y = (pa.y() + pb.y()) / 2.0 - 8.0 * self.font_scale
        x = pb.x() + 10.0
        if x + self._text_width(label) > self.width() - 4.0:
            x = pb.x() - 10.0 - self._text_width(label)
        self._text(painter, QPointF(x, mid_y), label, color)

    def _paint_pending_tape(self, painter: QPainter) -> None:
        if self._tape_a is None:
            return
        color = QColor(self.pen_color)
        pa = self.data_to_widget(*self._tape_a)
        painter.setPen(QPen(color, 1.5))
        painter.setBrush(color)
        painter.drawEllipse(pa, 3.5, 3.5)
        if self._cursor is not None:
            painter.setPen(QPen(color, 1.0, Qt.PenStyle.DotLine))
            painter.drawLine(pa, self._cursor)

    def _paint_laser(self, painter: QPainter) -> None:
        now = time.monotonic()
        painter.setPen(Qt.PenStyle.NoPen)
        for x, y, stamp in self._laser:
            fade = max(0.0, 1.0 - (now - stamp) / LASER_FADE_S)
            color = QColor(self.pen_color)
            color.setAlphaF(fade)
            painter.setBrush(color)
            radius = (3.0 + 4.0 * fade) * self.font_scale
            painter.drawEllipse(self.data_to_widget(x, y), radius, radius)

    def _paint_crosshair(self, painter: QPainter, cursor: QPointF) -> None:
        color = QColor(self._graph.getAxis("left").textPen().color())
        line = QColor(color)
        line.setAlpha(150)
        painter.setPen(QPen(line, 1.0))
        painter.drawLine(QPointF(0.0, cursor.y()), QPointF(self.width(), cursor.y()))
        painter.drawLine(QPointF(cursor.x(), 0.0), QPointF(cursor.x(), self.height()))
        text = readout_text(*self.widget_to_data(cursor))
        offset = 12.0 * self.font_scale
        anchor = QPointF(cursor.x() + offset, cursor.y() - offset - 16.0 * self.font_scale)
        if anchor.x() + self._text_width(text) > self.width() - 4.0:
            anchor.setX(cursor.x() - offset - self._text_width(text))
        self._text(painter, anchor, text, color)
