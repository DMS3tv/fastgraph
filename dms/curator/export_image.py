from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen

from dms.curator.models import CurveData, GraphState
from dms.curator.transforms import visible_display_layers
from dms.graph_display import (
    EXPORT_FREQUENCY_MARKERS,
    FREQUENCY_TICKS,
    paint_aperiodic_dither_band,
    retro_step_group,
    retro_step_series,
    stipple_trace_pen,
)
from dms.style_tokens import ThemeTokens, tokens_for
from dms.theme import ensure_graph_color, normalize_theme

FREQ_MIN = 20.0
FREQ_MAX = 20000.0
ACCENT_COLOR = "#FCBE11"
PLOT_INSET_LEFT = 14.0
PLOT_INSET_TOP = 14.0
PLOT_INSET_RIGHT = 36.0
PLOT_INSET_BOTTOM = 54.0
ASPECT_LOCK_DB_PER_DECADE = 25.0
DITHER_FOOTER_HEIGHT = 78.0
DITHER_FOOTER_HORIZONTAL_MARGIN = 40.0


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
    tokens = tokens_for(theme)
    classic = tokens.classic_controls
    dither = tokens.dither_chrome
    bg = QColor(tokens.background if dither else state.background)
    light_background = bg.lightness() >= 150
    if dither:
        fg = QColor(tokens.text)
        accent = QColor(tokens.accent)
        muted = QColor(tokens.plot_fg)
    else:
        fg = QColor(tokens.text if classic else ("#20252d" if light_background else "#f2f5f4"))
        accent = QColor(ACCENT_COLOR)
        muted = QColor(
            tokens.plot_fg if classic else ("#5f6977" if light_background else "#8f98a8")
        )

    if classic:
        painter.fillRect(QRectF(0, 0, width, height), QColor(tokens.panel))
        title_bar = QRectF(24, 16, width - 48, 64)
        painter.fillRect(title_bar, QColor(tokens.selected))
        painter.setPen(QPen(QColor(tokens.border), 2))
        painter.drawRect(title_bar)
        _draw_dither_strip(painter, QRectF(24, 116, width - 48, 14), tokens)
    elif dither:
        painter.fillRect(QRectF(0, 0, width, height), QColor(tokens.background))
        _draw_dither_masthead(painter, text, width, tokens)
    else:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg).lighter(118))
        painter.drawRect(QRectF(0, 0, width, 96))

    if not dither:
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
        painter.drawText(
            QRectF(44 if classic else 74, 78, width - 320, 34),
            text.fixture.strip(),
        )

    if classic:
        painter.fillRect(graph_rect, bg)
        _draw_classic_bevel(painter, graph_rect, tokens, recessed=True)
    elif dither:
        painter.setPen(QPen(QColor(tokens.border), 2))
        painter.setBrush(QColor(tokens.plot_bg))
        painter.drawRoundedRect(graph_rect, 12, 12)
    else:
        painter.setBrush(QColor(bg).lighter(108))
        painter.setPen(QPen(QColor("#313846"), 2))
        painter.drawRoundedRect(graph_rect, 4, 4)

    frame_rect = graph_rect.adjusted(
        PLOT_INSET_LEFT,
        PLOT_INSET_TOP,
        -PLOT_INSET_RIGHT,
        -PLOT_INSET_BOTTOM,
    )
    plot_rect = (
        aspect_locked_rect(frame_rect, state.y_min, state.y_max)
        if state.aspect_locked_25db
        else frame_rect
    )
    _draw_grid(
        painter,
        plot_rect,
        state.y_min,
        state.y_max,
        QColor(tokens.plot_grid) if dither else muted,
        label_color=QColor(tokens.plot_fg) if dither else None,
        marker_color=QColor(tokens.plot_grid) if dither else None,
    )
    painter.save()
    painter.setClipRect(plot_rect)
    _draw_bounds(
        painter,
        plot_rect,
        state,
        retro=tokens.retro_graph,
        dither_tokens=tokens if dither else None,
    )
    visible_layers = visible_display_layers(state.layers, state.smoothing_fraction)
    trace_indexes_by_id = {layer.id: index for index, layer in enumerate(state.layers)}
    visible_trace_indexes = [
        trace_indexes_by_id.get(layer.id, fallback_index)
        for fallback_index, (layer, _curve) in enumerate(visible_layers)
    ]
    for trace_index, (layer, curve) in zip(visible_trace_indexes, visible_layers):
        display_color = ensure_graph_color(
            layer.color,
            tokens.plot_bg if dither else state.background,
        )
        _draw_curve_data(
            painter,
            plot_rect,
            curve,
            display_color,
            state.y_min,
            state.y_max,
            retro=tokens.retro_graph,
            trace_index=trace_index,
            trace_tokens=tokens,
        )
    painter.restore()
    if state.show_layer_names:
        _draw_legend(
            painter,
            frame_rect,
            visible_layers,
            light_background,
            classic_tokens=tokens if classic else None,
            surface_tokens=tokens if dither else None,
            trace_tokens=tokens,
            trace_indexes=visible_trace_indexes,
        )

    if dither:
        _draw_dither_footer(painter, text, width, height, tokens)
        return

    footer_rect = QRectF(72, height - 88, width - 144, 58)
    if classic:
        painter.fillRect(footer_rect, QColor(tokens.control))
        _draw_classic_bevel(painter, footer_rect, tokens, recessed=True)
    painter.setFont(
        QFont(
            tokens.typography.ui_family if classic or dither else "Arial",
            17 if classic or dither else 18,
            QFont.Weight.DemiBold,
        )
    )
    painter.setPen(
        QColor(tokens.text)
        if classic or dither
        else QColor("#3f4854" if light_background else "#cfd6df")
    )
    footer = "    ".join(item for item in (text.hrtf_note.strip(), text.notes.strip()) if item)
    painter.drawText(
        footer_rect.adjusted(
            14 if classic or dither else 0,
            0,
            -14 if classic or dither else 0,
            0,
        ),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        footer,
    )


def _draw_dither_footer(
    painter: QPainter,
    text,
    width: int,
    height: int,
    tokens: ThemeTokens,
) -> None:
    footer_rect = QRectF(
        0,
        height - DITHER_FOOTER_HEIGHT,
        width,
        DITHER_FOOTER_HEIGHT,
    )
    painter.fillRect(footer_rect, QColor(tokens.text))

    footer = "    ".join(item for item in (text.hrtf_note.strip(), text.notes.strip()) if item)
    if not footer:
        return

    text_rect = footer_rect.adjusted(
        DITHER_FOOTER_HORIZONTAL_MARGIN,
        8,
        -DITHER_FOOTER_HORIZONTAL_MARGIN,
        -8,
    )
    font = QFont(tokens.typography.ui_family, 17, QFont.Weight.DemiBold)
    for point_size in range(17, 0, -1):
        font.setPointSize(point_size)
        if QFontMetrics(font).horizontalAdvance(footer) <= text_rect.width():
            break

    painter.setFont(font)
    painter.setPen(QColor(tokens.background))
    painter.drawText(
        text_rect,
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine,
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


def _draw_dither_masthead(painter: QPainter, text, width: int, tokens: ThemeTokens) -> None:
    masthead = QRectF(0, 0, width, 118)
    painter.fillRect(masthead, QColor(tokens.text))
    knockout = QColor(tokens.background)

    fixture_text = text.fixture.strip().upper()
    fixture_limit = max(160.0, width * 0.34) if fixture_text else 0.0
    fixture_font = QFont(tokens.typography.ui_family, 20, QFont.Weight.DemiBold)
    if fixture_text:
        for size in range(20, 9, -1):
            fixture_font.setPointSize(size)
            if QFontMetrics(fixture_font).horizontalAdvance(fixture_text) <= fixture_limit - 24:
                break
        fixture_width = min(
            fixture_limit,
            float(QFontMetrics(fixture_font).horizontalAdvance(fixture_text) + 24),
        )
    else:
        fixture_width = 0.0

    title_rect = QRectF(40, 4, max(120.0, width - fixture_width - 104), 110)
    title, title_font = fit_title(
        (text.title.strip() or "Curator").upper(),
        title_rect.width(),
        family=tokens.typography.heading_family,
        maximum_size=58,
        minimum_size=24,
    )
    title_font.setCapitalization(QFont.Capitalization.AllUppercase)
    painter.setPen(knockout)
    painter.setFont(title_font)
    painter.drawText(
        title_rect,
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine,
        title,
    )

    if fixture_text:
        fixture_rect = QRectF(
            width - fixture_width - 40,
            4,
            fixture_width,
            110,
        )
        painter.setFont(fixture_font)
        painter.drawText(
            fixture_rect,
            Qt.AlignmentFlag.AlignRight
            | Qt.AlignmentFlag.AlignVCenter
            | Qt.TextFlag.TextSingleLine,
            fixture_text,
        )


def _draw_classic_bevel(
    painter: QPainter,
    rect: QRectF,
    tokens: ThemeTokens,
    *,
    recessed: bool,
) -> None:
    dark_variant = tokens.dark_bevel
    highlight = QColor(
        tokens.accent if tokens.terminal_chrome else ("#8F8F8F" if dark_variant else "#FFFFFF")
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


def fit_title(
    text: str,
    max_width: float,
    *,
    family: str = "Arial",
    maximum_size: int = 42,
    minimum_size: int = 24,
) -> tuple[str, QFont]:
    """Fit a single-line poster title, shrinking before eliding."""
    font = QFont(family, maximum_size, QFont.Weight.Black)
    for size in range(maximum_size, minimum_size - 1, -1):
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
    surface_tokens: ThemeTokens | None = None,
    trace_tokens: ThemeTokens | None = None,
    trace_indexes: list[int] | None = None,
) -> None:
    if not layers:
        return
    shown = layers[:16]
    columns = 2 if len(shown) > 8 else 1
    rows = min(8, len(shown))
    row_height = 26.0
    font = QFont(
        (
            classic_tokens.typography.ui_family
            if classic_tokens is not None
            else surface_tokens.typography.ui_family
            if surface_tokens is not None
            else "Arial"
        ),
        15,
        QFont.Weight.DemiBold,
    )
    painter.setFont(font)
    metrics = QFontMetrics(font)
    column_widths = []
    for column in range(columns):
        column_layers = shown[column * 8 : (column + 1) * 8]
        widest_label = max(metrics.horizontalAdvance(layer.name) for layer, _curve in column_layers)
        column_widths.append(max(270.0, float(widest_label + 56)))
    box_width = sum(column_widths) + 24.0
    box_height = rows * row_height + 20.0 + (row_height if len(layers) > 16 else 0.0)
    box = QRectF(rect.right() - box_width - 14, rect.top() + 14, box_width, box_height)
    if classic_tokens is not None:
        painter.fillRect(box, QColor(classic_tokens.control))
        _draw_classic_bevel(painter, box, classic_tokens, recessed=False)
    elif surface_tokens is not None:
        painter.setPen(QPen(QColor(surface_tokens.border), 1))
        painter.setBrush(QColor(surface_tokens.panel))
        painter.drawRoundedRect(box, 8, 8)
    else:
        painter.setPen(QPen(QColor(95, 105, 120, 170), 1))
        painter.setBrush(
            QColor(245, 247, 250, 225) if light_background else QColor(20, 23, 29, 220)
        )
        painter.drawRoundedRect(box, 6, 6)
    text_color = QColor(
        classic_tokens.text
        if classic_tokens is not None
        else surface_tokens.text
        if surface_tokens is not None
        else ("#20252d" if light_background else "#f2f5f4")
    )
    for index, (layer, _curve) in enumerate(shown):
        column = index // 8
        row = index % 8
        column_left = sum(column_widths[:column])
        column_width = column_widths[column]
        x = box.left() + 12 + column_left
        y = box.top() + 12 + row * row_height
        legend_color = (
            ensure_graph_color(layer.color, classic_tokens.control)
            if classic_tokens
            else ensure_graph_color(layer.color, surface_tokens.panel)
            if surface_tokens
            else QColor(layer.color)
        )
        painter.setPen(
            stipple_trace_pen(
                QPen(legend_color, 4),
                trace_indexes[index] if trace_indexes is not None else index,
                trace_tokens or "dark",
            )
        )
        painter.drawLine(QPointF(x, y + 10), QPointF(x + 28, y + 10))
        painter.setPen(text_color)
        painter.drawText(
            QRectF(x + 38, y, column_width - 50, row_height),
            Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine,
            layer.name,
        )
    if len(layers) > 16:
        painter.setPen(
            QColor(classic_tokens.muted)
            if classic_tokens is not None
            else QColor(surface_tokens.muted)
            if surface_tokens is not None
            else QColor("#5f6977" if light_background else "#8f98a8")
        )
        painter.drawText(
            QRectF(box.left() + 12, box.bottom() - row_height - 5, box.width() - 24, row_height),
            Qt.AlignmentFlag.AlignVCenter,
            f"+{len(layers) - 16} more",
        )


def _draw_grid(
    painter: QPainter,
    rect: QRectF,
    y_min: float,
    y_max: float,
    color: QColor,
    *,
    label_color: QColor | None = None,
    marker_color: QColor | None = None,
) -> None:
    painter.setFont(QFont("Arial", 15, QFont.Weight.DemiBold))
    labels = label_color or color
    for freq, label in FREQUENCY_TICKS:
        x = _x_for_freq(rect, freq)
        marker = EXPORT_FREQUENCY_MARKERS.get(freq)
        if marker is None:
            painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 70), 1))
        elif marker_color is not None:
            painter.setPen(
                QPen(
                    QColor(
                        marker_color.red(),
                        marker_color.green(),
                        marker_color.blue(),
                        marker[3],
                    ),
                    marker[4],
                )
            )
        else:
            painter.setPen(QPen(QColor(marker[0], marker[1], marker[2], marker[3]), marker[4]))
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        painter.setPen(labels)
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
        painter.setPen(labels)
        painter.drawText(QRectF(rect.left() - 64, py - 11, 54, 22), f"{y:g}")
        y += step


def _draw_bounds(
    painter: QPainter,
    rect: QRectF,
    state: GraphState,
    *,
    retro: bool = False,
    dither_tokens: ThemeTokens | None = None,
) -> None:
    bounds = state.bounds
    if not bounds.enabled or bounds.upper is None or bounds.lower is None:
        return
    upper = bounds.upper
    lower = bounds.lower
    if upper.mag_db is None or lower.mag_db is None:
        return
    freqs, upper_values, lower_values = aligned_bounds(upper, lower)
    if retro:
        freqs, upper_values, lower_values = retro_step_group(freqs, (upper_values, lower_values))
    upper_path = _curve_path(rect, freqs, upper_values, state.y_min, state.y_max)
    lower_path = _curve_path(rect, freqs, lower_values, state.y_min, state.y_max)
    fill = QPainterPath(upper_path)
    points = [
        QPointF(_x_for_freq(rect, f), _y_for_db(rect, m, state.y_min, state.y_max))
        for f, m in reversed(_band_points(freqs, lower_values))
    ]
    for point in points:
        fill.lineTo(point)
    fill.closeSubpath()
    painter.save()
    if dither_tokens is not None and dither_tokens.dither_chrome:
        upper_points = [
            (
                (_x_for_freq(rect, float(freq)) - rect.left()) / rect.width(),
                (_y_for_db(rect, float(value), state.y_min, state.y_max) - rect.top())
                / rect.height(),
            )
            for freq, value in zip(freqs, upper_values)
        ]
        lower_points = [
            (
                (_x_for_freq(rect, float(freq)) - rect.left()) / rect.width(),
                (_y_for_db(rect, float(value), state.y_min, state.y_max) - rect.top())
                / rect.height(),
            )
            for freq, value in zip(freqs, lower_values)
        ]
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        paint_aperiodic_dither_band(
            painter,
            rect,
            upper_points,
            lower_points,
            foreground=QColor(dither_tokens.plot_grid),
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(dither_tokens.muted), 1))
        painter.drawPath(upper_path)
        painter.drawPath(lower_path)
        painter.restore()
        return
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
    trace_index: int = 0,
    trace_tokens: ThemeTokens | None = None,
) -> None:
    if curve.kind == "fr" and curve.mag_db is not None:
        freqs = curve.freqs
        mag_db = curve.mag_db
        if retro:
            freqs, mag_db = retro_step_series(freqs, mag_db)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            stipple_trace_pen(
                QPen(color, 3),
                trace_index,
                trace_tokens or "dark",
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_curve_path(rect, freqs, mag_db, y_min, y_max))
        painter.restore()
        return

    if curve.kind == "variation" and curve.p10_db is not None and curve.p90_db is not None:
        _draw_variation_band(
            painter,
            rect,
            curve,
            color,
            y_min,
            y_max,
            retro=retro,
            trace_index=trace_index,
            trace_tokens=trace_tokens,
        )


def _draw_variation_band(
    painter: QPainter,
    rect: QRectF,
    curve: CurveData,
    color: QColor,
    y_min: float,
    y_max: float,
    *,
    retro: bool = False,
    trace_index: int = 0,
    trace_tokens: ThemeTokens | None = None,
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
        freqs, p10, p25, median, p75, p90 = retro_step_group(freqs, (p10, p25, median, p75, p90))
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
    upper_points = _band_points(freqs, upper)
    lower_points = _band_points(freqs, lower)
    if len(upper_points) < 2 or len(lower_points) < 2:
        return
    path = QPainterPath()
    path.moveTo(
        _x_for_freq(rect, upper_points[0][0]),
        _y_for_db(rect, upper_points[0][1], y_min, y_max),
    )
    for freq, value in upper_points[1:]:
        path.lineTo(_x_for_freq(rect, freq), _y_for_db(rect, value, y_min, y_max))
    for freq, value in reversed(lower_points):
        path.lineTo(QPointF(_x_for_freq(rect, freq), _y_for_db(rect, value, y_min, y_max)))
    path.closeSubpath()
    fill = QColor(color)
    fill.setAlpha(alpha)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawPath(path)


def aspect_locked_rect(
    rect: QRectF,
    y_min: float,
    y_max: float,
    *,
    ratio: float = ASPECT_LOCK_DB_PER_DECADE,
) -> QRectF:
    """Shrink the data area so one decade spans ``ratio`` dB, as on screen.

    The preview locks its view box to 25 dB per decade. pyqtgraph honours that
    by widening the visible range on one axis, which in a fixed-size export is
    the same as centring the data inside a smaller rectangle.
    """
    span_x = math.log10(FREQ_MAX) - math.log10(FREQ_MIN)
    span_y = float(y_max) - float(y_min)
    if span_x <= 0.0 or span_y <= 0.0 or rect.width() <= 0.0 or rect.height() <= 0.0:
        return rect
    view_ratio = (rect.width() / rect.height()) / float(ratio)
    if view_ratio <= 0.0:
        return rect
    target_ratio = span_x / span_y
    if target_ratio > view_ratio:
        # Keep the full frequency span; the dB range grows, so the data is
        # drawn into a shorter band centred vertically.
        height = rect.height() * (view_ratio / target_ratio)
        top = rect.top() + (rect.height() - height) / 2.0
        return QRectF(rect.left(), top, rect.width(), height)
    # Keep the dB range; the frequency range grows, so the data is drawn into a
    # narrower band centred horizontally.
    width = rect.width() * (target_ratio / view_ratio)
    left = rect.left() + (rect.width() - width) / 2.0
    return QRectF(left, rect.top(), width, rect.height())


def aligned_bounds(upper: CurveData, lower: CurveData):
    """Return (freqs, upper, lower) with the lower bound on the upper's grid.

    The two preference-bound files are independent exports and need not share a
    frequency grid, so pairing them by index skewed the band.
    """
    freqs = np.asarray(upper.freqs, dtype=float)
    upper_values = np.asarray(upper.mag_db, dtype=float)
    lower_freqs = np.asarray(lower.freqs, dtype=float)
    lower_values = np.asarray(lower.mag_db, dtype=float)
    if lower_freqs.shape == freqs.shape and np.array_equal(lower_freqs, freqs):
        return freqs, upper_values, lower_values
    return freqs, upper_values, np.interp(freqs, lower_freqs, lower_values)


def _band_points(freqs, values) -> list[tuple[float, float]]:
    """Frequency/value pairs inside the drawn band; out-of-band points are dropped.

    Clamping them onto the edge instead drew a vertical spike at 20 Hz or
    20 kHz in the saved PNG that the clipped preview never showed.
    """
    return [
        (float(freq), float(value))
        for freq, value in zip(freqs, values)
        if FREQ_MIN <= float(freq) <= FREQ_MAX
    ]


def _curve_path(rect: QRectF, freqs, mags, y_min: float, y_max: float) -> QPainterPath:
    path = QPainterPath()
    first = True
    for freq, mag in _band_points(freqs, mags):
        x = _x_for_freq(rect, freq)
        y = _y_for_db(rect, mag, y_min, y_max)
        if first:
            path.moveTo(x, y)
            first = False
        else:
            path.lineTo(x, y)
    return path


def _x_for_freq(rect: QRectF, freq: float) -> float:
    log_min = math.log10(FREQ_MIN)
    log_max = math.log10(FREQ_MAX)
    frac = (math.log10(max(1e-9, freq)) - log_min) / (log_max - log_min)
    return rect.left() + rect.width() * frac


def _y_for_db(rect: QRectF, db: float, y_min: float, y_max: float) -> float:
    if y_max <= y_min:
        y_max = y_min + 1.0
    frac = (db - y_min) / (y_max - y_min)
    return rect.bottom() - rect.height() * frac
