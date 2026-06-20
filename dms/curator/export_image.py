from __future__ import annotations

import math
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen

from dms.curator.models import CurveData, GraphState
from dms.curator.transforms import visible_display_layers


FREQ_MIN = 20.0
FREQ_MAX = 20000.0
ACCENT_COLOR = "#FCBE11"
PLOT_INSET_LEFT = 14.0
PLOT_INSET_TOP = 14.0
PLOT_INSET_RIGHT = 36.0
PLOT_INSET_BOTTOM = 14.0
FREQUENCY_TICKS = [
    (20, "20"),
    (50, "50"),
    (100, "100"),
    (200, "200"),
    (500, "500"),
    (1000, "1k"),
    (2000, "2k"),
    (3000, "3k"),
    (5000, "5k"),
    (8000, "8k"),
    (10000, "10k"),
    (20000, "20k"),
]
FREQUENCY_MARKERS = {
    1000: (145, 152, 168, 135, 2.2),
    3000: (145, 152, 168, 92, 1.4),
    10000: (145, 152, 168, 128, 2.0),
}


def export_graph_image(
    state: GraphState,
    output_path: str | Path,
    size: tuple[int, int] = (1920, 1080),
) -> None:
    image = QImage(QSize(size[0], size[1]), QImage.Format.Format_ARGB32)
    image.fill(QColor(state.background))

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    try:
        _draw_poster(painter, state, size)
    finally:
        painter.end()

    if not image.save(str(output_path), "PNG"):
        raise OSError(f"Could not save image to {output_path}")


def _draw_poster(painter: QPainter, state: GraphState, size: tuple[int, int]) -> None:
    width, height = size
    graph_rect = QRectF(72, 150, width - 144, height - 250)
    text = state.export_text
    bg = QColor(state.background)
    light_background = bg.lightness() >= 150
    fg = QColor("#20252d" if light_background else "#f2f5f4")
    accent = QColor(ACCENT_COLOR)
    muted = QColor("#5f6977" if light_background else "#8f98a8")

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(bg).lighter(118))
    painter.drawRect(QRectF(0, 0, width, 96))

    painter.setPen(fg)
    painter.setFont(QFont("Arial", 42, QFont.Weight.Black))
    painter.drawText(QRectF(72, 16, width - 260, 58), text.title.strip() or "Curator")
    painter.setFont(QFont("Arial", 20, QFont.Weight.DemiBold))
    painter.setPen(accent)
    painter.drawText(QRectF(74, 74, width - 320, 34), text.fixture.strip())

    painter.setBrush(QColor(bg).lighter(108))
    painter.setPen(QPen(QColor("#313846"), 2))
    painter.drawRoundedRect(graph_rect, 4, 4)

    plot_rect = graph_rect.adjusted(
        PLOT_INSET_LEFT,
        PLOT_INSET_TOP,
        -PLOT_INSET_RIGHT,
        -PLOT_INSET_BOTTOM,
    )
    _draw_grid(painter, plot_rect, state.y_min, state.y_max, muted)
    painter.save()
    painter.setClipRect(plot_rect)
    _draw_bounds(painter, plot_rect, state)
    for layer, curve in visible_display_layers(state.layers):
        _draw_curve_data(painter, plot_rect, curve, QColor(layer.color), state.y_min, state.y_max)
    painter.restore()

    painter.setFont(QFont("Arial", 18, QFont.Weight.DemiBold))
    painter.setPen(QColor("#3f4854" if light_background else "#cfd6df"))
    footer = "    ".join(item for item in (text.hrtf_note.strip(), text.notes.strip()) if item)
    painter.drawText(QRectF(72, height - 82, width - 144, 52), footer)


def _draw_grid(painter: QPainter, rect: QRectF, y_min: float, y_max: float, color: QColor) -> None:
    painter.setFont(QFont("Arial", 15, QFont.Weight.DemiBold))
    for freq, label in FREQUENCY_TICKS:
        x = _x_for_freq(rect, freq)
        marker = FREQUENCY_MARKERS.get(freq)
        if marker is None:
            painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 70), 1))
        else:
            painter.setPen(QPen(QColor(marker[0], marker[1], marker[2], marker[3]), marker[4]))
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        painter.setPen(color)
        painter.drawText(
            QRectF(x - 28, rect.bottom() + 10, 56, 24),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            label,
        )

    step = 5.0
    start = math.ceil(y_min / step) * step
    y = start
    while y <= y_max + 1e-9:
        py = _y_for_db(rect, y, y_min, y_max)
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 55), 1))
        painter.drawLine(QPointF(rect.left(), py), QPointF(rect.right(), py))
        painter.setPen(color)
        painter.drawText(QRectF(rect.left() - 64, py - 11, 54, 22), f"{y:g}")
        y += step


def _draw_bounds(painter: QPainter, rect: QRectF, state: GraphState) -> None:
    bounds = state.bounds
    if not bounds.enabled or bounds.upper is None or bounds.lower is None:
        return
    upper = bounds.upper
    lower = bounds.lower
    if upper.mag_db is None or lower.mag_db is None:
        return
    upper_path = _curve_path(rect, upper.freqs, upper.mag_db, state.y_min, state.y_max)
    lower_path = _curve_path(rect, lower.freqs, lower.mag_db, state.y_min, state.y_max)
    fill = QPainterPath(upper_path)
    points = [
        QPointF(_x_for_freq(rect, f), _y_for_db(rect, m, state.y_min, state.y_max))
        for f, m in zip(reversed(lower.freqs), reversed(lower.mag_db))
    ]
    for point in points:
        fill.lineTo(point)
    fill.closeSubpath()
    painter.setBrush(QColor(150, 150, 150, 102))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPath(fill)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(150, 150, 150, 180), 2))
    painter.drawPath(upper_path)
    painter.drawPath(lower_path)


def _draw_curve_data(
    painter: QPainter,
    rect: QRectF,
    curve: CurveData,
    color: QColor,
    y_min: float,
    y_max: float,
) -> None:
    if curve.kind == "fr" and curve.mag_db is not None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        glow = QColor(color)
        glow.setAlpha(58)
        painter.setPen(QPen(glow, 10))
        painter.drawPath(_curve_path(rect, curve.freqs, curve.mag_db, y_min, y_max))
        painter.setPen(QPen(color, 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_curve_path(rect, curve.freqs, curve.mag_db, y_min, y_max))
        return

    if curve.kind == "variation" and curve.p10_db is not None and curve.p90_db is not None:
        _draw_variation_band(painter, rect, curve, color, y_min, y_max)


def _draw_variation_band(
    painter: QPainter,
    rect: QRectF,
    curve: CurveData,
    color: QColor,
    y_min: float,
    y_max: float,
) -> None:
    if curve.p10_db is None or curve.p25_db is None or curve.median_db is None:
        return
    if curve.p75_db is None or curve.p90_db is None:
        return
    _fill_between(painter, rect, curve.freqs, curve.p90_db, curve.p10_db, color, 50, y_min, y_max)
    _fill_between(painter, rect, curve.freqs, curve.p75_db, curve.p25_db, color, 85, y_min, y_max)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    glow = QColor(color)
    glow.setAlpha(58)
    painter.setPen(QPen(glow, 10))
    painter.drawPath(_curve_path(rect, curve.freqs, curve.median_db, y_min, y_max))
    painter.setPen(QPen(color, 3))
    painter.drawPath(_curve_path(rect, curve.freqs, curve.median_db, y_min, y_max))


def _fill_between(
    painter: QPainter,
    rect: QRectF,
    freqs,
    upper,
    lower,
    color: QColor,
    alpha: int,
    y_min: float,
    y_max: float,
) -> None:
    path = _curve_path(rect, freqs, upper, y_min, y_max)
    for f, m in zip(reversed(freqs), reversed(lower)):
        path.lineTo(QPointF(_x_for_freq(rect, float(f)), _y_for_db(rect, float(m), y_min, y_max)))
    path.closeSubpath()
    fill = QColor(color)
    fill.setAlpha(alpha)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawPath(path)


def _curve_path(rect: QRectF, freqs, mags, y_min: float, y_max: float) -> QPainterPath:
    path = QPainterPath()
    first = True
    for freq, mag in zip(freqs, mags):
        x = _x_for_freq(rect, float(freq))
        y = _y_for_db(rect, float(mag), y_min, y_max)
        if first:
            path.moveTo(x, y)
            first = False
        else:
            path.lineTo(x, y)
    return path


def _x_for_freq(rect: QRectF, freq: float) -> float:
    log_min = math.log10(FREQ_MIN)
    log_max = math.log10(FREQ_MAX)
    frac = (math.log10(max(FREQ_MIN, min(FREQ_MAX, freq))) - log_min) / (log_max - log_min)
    return rect.left() + rect.width() * frac


def _y_for_db(rect: QRectF, db: float, y_min: float, y_max: float) -> float:
    if y_max <= y_min:
        y_max = y_min + 1.0
    frac = (db - y_min) / (y_max - y_min)
    return rect.bottom() - rect.height() * frac
