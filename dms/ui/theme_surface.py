"""Small painted surfaces used by the theme selector and classic theme."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

import numpy as np
from PyQt6.QtCore import QRect, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap, QTransform
from PyQt6.QtWidgets import QApplication, QGraphicsPixmapItem, QWidget

from dms.ui.style_tokens import mode_tokens, tokens_for

_BAYER_8 = (
    (0, 48, 12, 60, 3, 51, 15, 63),
    (32, 16, 44, 28, 35, 19, 47, 31),
    (8, 56, 4, 52, 11, 59, 7, 55),
    (40, 24, 36, 20, 43, 27, 39, 23),
    (2, 50, 14, 62, 1, 49, 13, 61),
    (34, 18, 46, 30, 33, 17, 45, 29),
    (10, 58, 6, 54, 9, 57, 5, 53),
    (42, 26, 38, 22, 41, 25, 37, 21),
)


@lru_cache(maxsize=256)
def _dither_tile(foreground_rgba: int, background_rgba: int, density_steps: int) -> QPixmap:
    foreground = QColor.fromRgba(foreground_rgba)
    background = QColor.fromRgba(background_rgba)
    image = QImage(8, 8, QImage.Format.Format_ARGB32)
    for y, row in enumerate(_BAYER_8):
        for x, threshold in enumerate(row):
            image.setPixelColor(x, y, foreground if threshold < density_steps else background)
    return QPixmap.fromImage(image)


def dither_brush(
    foreground: QColor,
    *,
    background: QColor | None = None,
    density: float = 0.25,
) -> QBrush:
    """Return a cached 8-by-8 ordered pixel pattern."""

    steps = max(0, min(64, round(float(density) * 64)))
    clear = QColor(0, 0, 0, 0) if background is None else QColor(background)
    return QBrush(_dither_tile(QColor(foreground).rgba(), clear.rgba(), steps))


def paint_dither(
    painter: QPainter,
    rect: QRect | QRectF,
    *,
    foreground: QColor,
    background: QColor | None = None,
    density: float = 0.25,
) -> None:
    """Fill a rectangle with the shared variable-density pixel pattern."""

    painter.fillRect(
        rect,
        dither_brush(foreground, background=background, density=density),
    )


def aperiodic_dither_band_image(
    width: int,
    height: int,
    upper_points: Sequence[tuple[float, float]],
    lower_points: Sequence[tuple[float, float]],
    *,
    foreground: QColor,
    minimum_density: float = 0.045,
    maximum_density: float = 0.34,
    seed: int = 0xD17E3,
) -> QImage:
    """Return a transparent, aperiodic dither band.

    Point coordinates are normalized to the image. The Y coordinate is zero at
    the top. Ink density is highest at each boundary and lowest at the center.
    """

    image_width = max(1, int(width))
    image_height = max(1, int(height))
    rgba = np.zeros((image_height, image_width, 4), dtype=np.uint8)
    if not upper_points or not lower_points:
        return _rgba_image(rgba)

    upper = _sample_normalized_curve(upper_points, image_width)
    lower = _sample_normalized_curve(lower_points, image_width)
    top = np.minimum(upper, lower) * max(0, image_height - 1)
    bottom = np.maximum(upper, lower) * max(0, image_height - 1)

    rows = np.arange(image_height, dtype=np.float64)[:, None]
    inside = (rows >= top[None, :]) & (rows <= bottom[None, :])
    nearest_edge = np.minimum(rows - top[None, :], bottom[None, :] - rows)
    half_span = np.maximum((bottom - top) * 0.5, 1.0)[None, :]
    center_distance = np.clip(nearest_edge / half_span, 0.0, 1.0)
    edge_weight = np.square(1.0 - center_distance)
    low = max(0.0, min(1.0, float(minimum_density)))
    high = max(low, min(1.0, float(maximum_density)))
    density = low + (high - low) * edge_weight

    columns_u32 = np.arange(image_width, dtype=np.uint32)[None, :]
    rows_u32 = np.arange(image_height, dtype=np.uint32)[:, None]
    hashed = (columns_u32 * np.uint32(0x9E3779B1)) ^ (rows_u32 * np.uint32(0x85EBCA77))
    hashed ^= np.uint32(seed & 0xFFFFFFFF)
    hashed ^= hashed >> np.uint32(16)
    hashed *= np.uint32(0x7FEB352D)
    hashed ^= hashed >> np.uint32(15)
    hashed *= np.uint32(0x846CA68B)
    hashed ^= hashed >> np.uint32(16)
    threshold = hashed.astype(np.float64) / float(np.iinfo(np.uint32).max)
    ink = inside & (threshold < density)

    color = QColor(foreground)
    rgba[ink] = np.array(color.getRgb(), dtype=np.uint8)
    return _rgba_image(rgba)


def paint_aperiodic_dither_band(
    painter: QPainter,
    rect: QRectF,
    upper_points: Sequence[tuple[float, float]],
    lower_points: Sequence[tuple[float, float]],
    *,
    foreground: QColor,
    minimum_density: float = 0.045,
    maximum_density: float = 0.34,
) -> None:
    """Paint the shared aperiodic dither band into a target rectangle."""

    width = max(1, round(rect.width()))
    height = max(1, round(rect.height()))
    image = aperiodic_dither_band_image(
        width,
        height,
        upper_points,
        lower_points,
        foreground=foreground,
        minimum_density=minimum_density,
        maximum_density=maximum_density,
    )
    painter.drawImage(rect, image)


def aperiodic_dither_band_item(
    x_values: Sequence[float],
    upper_values: Sequence[float],
    lower_values: Sequence[float],
    *,
    foreground: QColor,
    sample_width: int = 640,
    sample_height: int = 240,
    minimum_density: float = 0.045,
    maximum_density: float = 0.34,
) -> QGraphicsPixmapItem | None:
    """Return a graph item that maps the shared dither band to data space."""

    x_data = np.asarray(x_values, dtype=np.float64)
    upper_data = np.asarray(upper_values, dtype=np.float64)
    lower_data = np.asarray(lower_values, dtype=np.float64)
    length = min(len(x_data), len(upper_data), len(lower_data))
    if length < 2:
        return None
    x_data = x_data[:length]
    upper_data = upper_data[:length]
    lower_data = lower_data[:length]
    finite = np.isfinite(x_data) & np.isfinite(upper_data) & np.isfinite(lower_data)
    x_data = x_data[finite]
    upper_data = upper_data[finite]
    lower_data = lower_data[finite]
    if len(x_data) < 2:
        return None

    x_min = float(np.min(x_data))
    x_max = float(np.max(x_data))
    y_min = float(np.min(np.minimum(upper_data, lower_data)))
    y_max = float(np.max(np.maximum(upper_data, lower_data)))
    x_span = x_max - x_min
    y_span = y_max - y_min
    if x_span <= 0.0 or y_span <= 0.0:
        return None

    upper_points = list(zip((x_data - x_min) / x_span, (y_max - upper_data) / y_span))
    lower_points = list(zip((x_data - x_min) / x_span, (y_max - lower_data) / y_span))
    image = aperiodic_dither_band_image(
        sample_width,
        sample_height,
        upper_points,
        lower_points,
        foreground=foreground,
        minimum_density=minimum_density,
        maximum_density=maximum_density,
    )
    item = QGraphicsPixmapItem(QPixmap.fromImage(image))
    item.setTransformationMode(Qt.TransformationMode.FastTransformation)
    transform = QTransform()
    transform.translate(x_min, y_max)
    transform.scale(x_span / image.width(), -y_span / image.height())
    item.setTransform(transform)
    return item


def _sample_normalized_curve(points: Sequence[tuple[float, float]], width: int) -> np.ndarray:
    values = np.asarray(points, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 2:
        raise ValueError("Dither boundary points must contain X and Y pairs")
    finite = np.isfinite(values).all(axis=1)
    values = values[finite]
    if len(values) == 0:
        return np.zeros(width, dtype=np.float64)
    order = np.argsort(values[:, 0], kind="stable")
    values = values[order]
    sample_x = np.linspace(0.0, 1.0, width)
    return np.clip(np.interp(sample_x, values[:, 0], values[:, 1]), 0.0, 1.0)


def _rgba_image(rgba: np.ndarray) -> QImage:
    height, width, _channels = rgba.shape
    image = QImage(
        rgba.data,
        width,
        height,
        int(rgba.strides[0]),
        QImage.Format.Format_RGBA8888,
    )
    return image.copy()


def _paint_dither(painter: QPainter, rect: QRect, *, light: QColor, dark: QColor) -> None:
    """Keep the original helper signature for existing callers."""

    paint_dither(
        painter,
        rect,
        foreground=light,
        background=dark,
        density=0.25,
    )


def _chrome_dither_colors(tokens, *, preview: bool = False) -> tuple[QColor, QColor]:
    if tokens.dither_chrome and not tokens.classic_controls:
        light = QColor(tokens.text)
        light.setAlpha(38)
        dark = QColor(tokens.shadow)
        dark.setAlpha(34)
        return light, dark
    return (
        QColor(tokens.border)
        if tokens.terminal_chrome
        else QColor(255, 255, 255, 100 if preview else 92),
        QColor(0, 0, 0, 48 if preview else 42),
    )


class DitherSurface(QWidget):
    """Paint a restrained pixel texture on theme chrome."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("ditherSurface", True)

    def paintEvent(self, event) -> None:
        app = QApplication.instance()
        mode = app.property("fastgraphVisualMode") if app is not None else "dark"
        tokens = mode_tokens(mode)
        if not (tokens.classic_controls or tokens.dither_chrome):
            super().paintEvent(event)
            return

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(tokens.panel))
        light, dark = _chrome_dither_colors(tokens)
        _paint_dither(
            painter,
            self.rect(),
            light=light,
            dark=dark,
        )
        if tokens.flat_controls:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(tokens.border), tokens.geometry.border_px))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()


class ThemePreview(QWidget):
    """Compact token preview for one registered theme."""

    def __init__(self, theme: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = str(theme)
        self.setFixedSize(78, 30)
        self.setAccessibleName(f"{self._theme} theme preview")

    def paintEvent(self, event) -> None:
        tokens = tokens_for(self._theme)
        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            not tokens.classic_controls,
        )
        outer = self.rect().adjusted(0, 0, -1, -1)
        radius = min(5, tokens.geometry.radius_surface)
        painter.setPen(QPen(QColor(tokens.border), 1))
        painter.setBrush(QColor(tokens.background))
        painter.drawRoundedRect(outer, radius, radius)

        panel = QRect(4, 4, 34, 21)
        painter.fillRect(panel, QColor(tokens.panel))
        if tokens.classic_controls or tokens.dither_chrome:
            light, dark = _chrome_dither_colors(tokens, preview=True)
            _paint_dither(
                painter,
                panel,
                light=light,
                dark=dark,
            )
        if tokens.classic_controls:
            painter.setPen(
                QPen(
                    QColor(tokens.border if tokens.terminal_chrome else "#ffffff"),
                    1,
                )
            )
            painter.drawLine(panel.topLeft(), panel.topRight())
            painter.drawLine(panel.topLeft(), panel.bottomLeft())
            painter.setPen(QPen(QColor("#000000"), 1))
            painter.drawLine(panel.bottomLeft(), panel.bottomRight())
            painter.drawLine(panel.topRight(), panel.bottomRight())

        painter.fillRect(QRect(42, 4, 31, 9), QColor(tokens.selected))
        painter.fillRect(QRect(42, 16, 31, 9), QColor(tokens.plot_bg))
        painter.setPen(QPen(QColor(tokens.accent), 1))
        painter.drawLine(44, 22, 51, 19)
        painter.drawLine(51, 19, 58, 22)
        painter.drawLine(58, 22, 70, 18)
        painter.end()
