from __future__ import annotations

import math
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen

from dms.curator.models import CurveData, GraphState
from dms.curator.transforms import visible_display_layers
from dms.graph_display import retro_step_group, retro_step_series
from dms.theme import ensure_graph_color, normalize_theme
from dms.ui.style_tokens import ThemeTokens, tokens_for


FREQ_MIN = 20.0
FREQ_MAX = 20000.0
ACCENT_COLOR = "#FCBE11"
PLOT_INSET_LEFT = 14.0
PLOT_INSET_TOP = 14.0
PLOT_INSET_RIGHT = 36.0
PLOT_INSET_BOTTOM = 54.0
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
    *,
    brand_mode: bool = False,
    theme: str = "dark",
) -> None:
    image = QImage(QSize(size[0], size[1]), QImage.Format.Format_ARGB32)
    image.fill(QColor(state.background))

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    try:
        if brand_mode:
            # Local import: export_brand imports helpers from this module.
            from dms.curator.export_brand import draw_brand_poster

            draw_brand_poster(painter, state, size)
        else:
            _draw_poster(painter, state, size, theme=normalize_theme(theme))
    finally:
        painter.end()

    if not image.save(str(output_path), "PNG"):
        raise OSError(f"Could not save image to {output_path}")


def _draw_poster(
    painter: QPainter,
    state: GraphState,
    size: tuple[int, int],
    *,
    theme: str,
) -> None:
    width, height = size
    graph_rect = QRectF(72, 150, width - 144, height - 280)
    text = state.export_text
    bg = QColor(state.background)
    light_background = bg.lightness() >= 150
    tokens = tokens_for(theme)
    classic = tokens.classic_controls
    fg = QColor(tokens.text if classic else ("#20252d" if light_background else "#f2f5f4"))
    accent = QColor(ACCENT_COLOR)
    muted = QColor(tokens.plot_fg if classic else ("#5f6977" if light_background else "#8f98a8"))

    if classic:
        painter.fillRect(QRectF(0, 0, width, height), QColor(tokens.panel))
        title_bar = QRectF(24, 16, width - 48, 64)
        painter.fillRect(title_bar, QColor(tokens.selected))
        painter.setPen(QPen(QColor(tokens.border), 2))
        painter.drawRect(title_bar)
        _draw_dither_strip(painter, QRectF(24, 116, width - 48, 14), tokens)
    else:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg).lighter(118))
        painter.drawRect(QRectF(0, 0, width, 96))

    title_color = QColor(tokens.text if tokens.dark_bevel else "#FFFFFF") if classic else fg
    painter.setPen(title_color)
    title_rect = QRectF(44 if classic else 72, 18 if classic else 16, width - 280, 58)
    title, title_font = fit_title(text.title.strip() or "Curator", title_rect.width())
    if classic:
        title_font.setFamily(tokens.typography.heading_family)
        title_font.setPointSize(min(title_font.pointSize(), 36))
    painter.setFont(title_font)
    painter.drawText(
        title_rect,
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine,
        title,
    )
    painter.setFont(
        QFont(
            tokens.typography.ui_family if classic else "Arial",
            18 if classic else 20,
            QFont.Weight.DemiBold,
        )
    )
    painter.setPen(QColor(tokens.text) if classic else accent)
    painter.drawText(QRectF(44 if classic else 74, 78, width - 320, 34), text.fixture.strip())

    if classic:
        painter.fillRect(graph_rect, bg)
        _draw_classic_bevel(painter, graph_rect, tokens, recessed=True)
    else:
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
    _draw_bounds(painter, plot_rect, state, retro=classic)
    visible_layers = visible_display_layers(state.layers, state.smoothing_fraction)
    for layer, curve in visible_layers:
        display_color = ensure_graph_color(layer.color, state.background)
        _draw_curve_data(
            painter,
            plot_rect,
            curve,
            display_color,
            state.y_min,
            state.y_max,
            retro=classic,
        )
    painter.restore()
    if state.show_layer_names:
        _draw_legend(
            painter,
            plot_rect,
            visible_layers,
            light_background,
            classic_tokens=tokens if classic else None,
        )

    footer_rect = QRectF(72, height - 88, width - 144, 58)
    if classic:
        painter.fillRect(footer_rect, QColor(tokens.control))
        _draw_classic_bevel(painter, footer_rect, tokens, recessed=True)
    painter.setFont(
        QFont(
            tokens.typography.ui_family if classic else "Arial",
            17 if classic else 18,
            QFont.Weight.DemiBold,
        )
    )
    painter.setPen(QColor(tokens.text) if classic else QColor("#3f4854" if light_background else "#cfd6df"))
    footer = "    ".join(item for item in (text.hrtf_note.strip(), text.notes.strip()) if item)
    painter.drawText(
        footer_rect.adjusted(14 if classic else 0, 0, -14 if classic else 0, 0),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        footer,
    )


def _draw_dither_strip(painter: QPainter, rect: QRectF, tokens: ThemeTokens) -> None:
    painter.fillRect(rect, QColor(tokens.alternate))
    first = QColor(tokens.panel)
    second = QColor(tokens.alternate)
    cell = 4
    top = int(rect.top())
    bottom = int(rect.bottom())
    left = int(rect.left())
    right = int(rect.right())
    for y in range(top, bottom, cell):
        for x in range(left, right, cell):
            painter.fillRect(QRectF(x, y, cell, cell), first if ((x + y) // cell) % 2 else second)


def _draw_classic_bevel(
    painter: QPainter,
    rect: QRectF,
    tokens: ThemeTokens,
    *,
    recessed: bool,
) -> None:
    dark_variant = tokens.dark_bevel
    highlight = QColor(
        tokens.accent
        if tokens.terminal_chrome
        else ("#8F8F8F" if dark_variant else "#FFFFFF")
    )
    shadow = QColor(tokens.border)
    top_left = shadow if recessed else highlight
    bottom_right = highlight if recessed else shadow
    painter.setPen(QPen(top_left, 2))
    painter.drawLine(rect.topLeft(), rect.topRight())
    painter.drawLine(rect.topLeft(), rect.bottomLeft())
    painter.setPen(QPen(bottom_right, 2))
    painter.drawLine(rect.bottomLeft(), rect.bottomRight())
    painter.drawLine(rect.topRight(), rect.bottomRight())


def fit_title(text: str, max_width: float) -> tuple[str, QFont]:
    """Fit a single-line poster title, shrinking before eliding."""
    font = QFont("Arial", 42, QFont.Weight.Black)
    for size in range(42, 23, -1):
        font.setPointSize(size)
        if QFontMetrics(font).horizontalAdvance(text) <= max_width:
            return text, QFont(font)
    metrics = QFontMetrics(font)
    return metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(max_width)), QFont(font)


def _draw_legend(
    painter: QPainter,
    rect: QRectF,
    layers,
    light_background: bool,
    *,
    classic_tokens: ThemeTokens | None = None,
) -> None:
    if not layers:
        return
    shown = layers[:16]
    columns = 2 if len(shown) > 8 else 1
    rows = min(8, len(shown))
    column_width = 270.0
    row_height = 26.0
    box_width = columns * column_width + 20.0
    box_height = rows * row_height + 20.0 + (row_height if len(layers) > 16 else 0.0)
    box = QRectF(rect.right() - box_width - 14, rect.top() + 14, box_width, box_height)
    if classic_tokens is not None:
        painter.fillRect(box, QColor(classic_tokens.control))
        _draw_classic_bevel(painter, box, classic_tokens, recessed=False)
    else:
        painter.setPen(QPen(QColor(95, 105, 120, 170), 1))
        painter.setBrush(
            QColor(245, 247, 250, 225) if light_background else QColor(20, 23, 29, 220)
        )
        painter.drawRoundedRect(box, 6, 6)
    font = QFont(
        classic_tokens.typography.ui_family if classic_tokens is not None else "Arial",
        15,
        QFont.Weight.DemiBold,
    )
    painter.setFont(font)
    metrics = QFontMetrics(font)
    text_color = QColor(
        classic_tokens.text
        if classic_tokens is not None
        else ("#20252d" if light_background else "#f2f5f4")
    )
    for index, (layer, _curve) in enumerate(shown):
        column = index // 8
        row = index % 8
        x = box.left() + 12 + column * column_width
        y = box.top() + 12 + row * row_height
        legend_color = (
            ensure_graph_color(layer.color, classic_tokens.control)
            if classic_tokens
            else QColor(layer.color)
        )
        painter.setPen(QPen(legend_color, 4))
        painter.drawLine(QPointF(x, y + 10), QPointF(x + 28, y + 10))
        painter.setPen(text_color)
        label = metrics.elidedText(layer.name, Qt.TextElideMode.ElideRight, int(column_width - 52))
        painter.drawText(QRectF(x + 38, y, column_width - 50, row_height), Qt.AlignmentFlag.AlignVCenter, label)
    if len(layers) > 16:
        painter.setPen(
            QColor(classic_tokens.muted)
            if classic_tokens is not None
            else QColor("#5f6977" if light_background else "#8f98a8")
        )
        painter.drawText(
            QRectF(box.left() + 12, box.bottom() - row_height - 5, box.width() - 24, row_height),
            Qt.AlignmentFlag.AlignVCenter,
            f"+{len(layers) - 16} more",
        )


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


def _draw_bounds(
    painter: QPainter,
    rect: QRectF,
    state: GraphState,
    *,
    retro: bool = False,
) -> None:
    bounds = state.bounds
    if not bounds.enabled or bounds.upper is None or bounds.lower is None:
        return
    upper = bounds.upper
    lower = bounds.lower
    if upper.mag_db is None or lower.mag_db is None:
        return
    freqs = upper.freqs
    upper_values = upper.mag_db
    lower_values = lower.mag_db
    if retro:
        freqs, upper_values, lower_values = retro_step_group(
            freqs, (upper_values, lower_values)
        )
    upper_path = _curve_path(rect, freqs, upper_values, state.y_min, state.y_max)
    lower_path = _curve_path(rect, freqs, lower_values, state.y_min, state.y_max)
    fill = QPainterPath(upper_path)
    points = [
        QPointF(_x_for_freq(rect, f), _y_for_db(rect, m, state.y_min, state.y_max))
        for f, m in zip(reversed(freqs), reversed(lower_values))
    ]
    for point in points:
        fill.lineTo(point)
    fill.closeSubpath()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(QColor(150, 150, 150, 102))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPath(fill)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(150, 150, 150, 180), 2))
    painter.drawPath(upper_path)
    painter.drawPath(lower_path)
    painter.restore()


def _draw_curve_data(
    painter: QPainter,
    rect: QRectF,
    curve: CurveData,
    color: QColor,
    y_min: float,
    y_max: float,
    *,
    retro: bool = False,
) -> None:
    if curve.kind == "fr" and curve.mag_db is not None:
        freqs = curve.freqs
        mag_db = curve.mag_db
        if retro:
            freqs, mag_db = retro_step_series(freqs, mag_db)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_curve_path(rect, freqs, mag_db, y_min, y_max))
        painter.restore()
        return

    if curve.kind == "variation" and curve.p10_db is not None and curve.p90_db is not None:
        _draw_variation_band(painter, rect, curve, color, y_min, y_max, retro=retro)


def _draw_variation_band(
    painter: QPainter,
    rect: QRectF,
    curve: CurveData,
    color: QColor,
    y_min: float,
    y_max: float,
    *,
    retro: bool = False,
) -> None:
    if curve.p10_db is None or curve.p25_db is None or curve.median_db is None:
        return
    if curve.p75_db is None or curve.p90_db is None:
        return
    freqs = curve.freqs
    p10 = curve.p10_db
    p25 = curve.p25_db
    median = curve.median_db
    p75 = curve.p75_db
    p90 = curve.p90_db
    if retro:
        freqs, p10, p25, median, p75, p90 = retro_step_group(
            freqs, (p10, p25, median, p75, p90)
        )
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    _fill_between(painter, rect, freqs, p90, p10, color, 50, y_min, y_max)
    _fill_between(painter, rect, freqs, p75, p25, color, 85, y_min, y_max)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(color, 3))
    painter.drawPath(_curve_path(rect, freqs, median, y_min, y_max))
    painter.restore()


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
