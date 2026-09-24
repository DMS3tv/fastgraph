"""Fit text into a rectangle by shrinking it, for poster renderers."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QFont, QFontMetricsF, QPainter


@dataclass(frozen=True)
class FittedText:
    text: str
    font: QFont
    bounds: QRectF
    line_limit: int
    fits: bool
    warning: str = ""


def fit_text(
    text: str,
    font: QFont,
    target: QRectF,
    *,
    maximum_pixel_size: int,
    minimum_readable_pixel_size: int,
    line_limit: int = 1,
    field_name: str = "Text",
) -> FittedText:
    """Fit text inside a rectangle.

    The function measures width, height, line count, and italic bearings. It can
    continue below the preferred readable size to prevent clipping. A warning
    reports that condition to the caller.
    """

    value = str(text or "")
    maximum = max(1, int(maximum_pixel_size))
    readable_minimum = max(1, min(maximum, int(minimum_readable_pixel_size)))
    lines = max(1, int(line_limit))
    base_font = QFont(font)
    last_result: FittedText | None = None

    for pixel_size in range(maximum, 0, -1):
        candidate = QFont(base_font)
        candidate.setPixelSize(pixel_size)
        bounds, fits = _measure(value, candidate, target, lines)
        warning = ""
        if fits and pixel_size < readable_minimum:
            warning = (
                f"{field_name} requires {pixel_size}px text. "
                f"The preferred minimum is {readable_minimum}px."
            )
        result = FittedText(value, candidate, bounds, lines, fits, warning)
        if fits:
            return result
        last_result = result

    if last_result is None:
        candidate = QFont(base_font)
        candidate.setPixelSize(1)
        bounds, _fits = _measure(value, candidate, target, lines)
        last_result = FittedText(value, candidate, bounds, lines, False)

    metrics = QFontMetricsF(last_result.font)
    rendered = value
    if lines == 1:
        rendered = metrics.elidedText(
            value,
            Qt.TextElideMode.ElideRight,
            max(1, int(target.width())),
        )
    bounds, fits = _measure(rendered, last_result.font, target, lines)
    return FittedText(
        rendered,
        last_result.font,
        bounds,
        lines,
        fits,
        f"{field_name} is too long for its export area.",
    )


def draw_fitted_text(
    painter: QPainter,
    fitted: FittedText,
    target: QRectF,
    alignment: Qt.AlignmentFlag,
) -> None:
    painter.setFont(fitted.font)
    flags = alignment
    if fitted.line_limit == 1:
        flags |= Qt.TextFlag.TextSingleLine
    else:
        flags |= Qt.TextFlag.TextWordWrap
    painter.drawText(target, flags, fitted.text)


def _measure(
    text: str,
    font: QFont,
    target: QRectF,
    line_limit: int,
) -> tuple[QRectF, bool]:
    metrics = QFontMetricsF(font)
    if not text:
        return QRectF(0.0, 0.0, 0.0, metrics.height()), metrics.height() <= target.height()

    if line_limit == 1:
        ink = metrics.tightBoundingRect(text)
        normal = metrics.boundingRect(text)
        width = max(
            ink.width(),
            normal.width(),
            metrics.horizontalAdvance(text),
        )
        height = max(ink.height(), normal.height(), metrics.height())
        bounds = QRectF(0.0, 0.0, width, height)
        return bounds, width <= target.width() and height <= target.height()

    line_height = max(1.0, metrics.lineSpacing())
    allowed_height = min(target.height(), line_height * line_limit)
    measure_rect = QRectF(0.0, 0.0, max(1.0, target.width()), max(1.0, allowed_height))
    flags = Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft
    bounds = metrics.boundingRect(measure_rect, flags, text)
    measured_lines = max(1, round(bounds.height() / line_height))
    fits = (
        bounds.width() <= target.width()
        and bounds.height() <= target.height()
        and measured_lines <= line_limit
    )
    return bounds, fits
