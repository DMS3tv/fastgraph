from __future__ import annotations

import math

from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, pyqtSlot
from PyQt6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QApplication, QWidget

from dms.style_tokens import ThemeTokens, mode_tokens
from dms.theme import mix_colors
from dms.ui.theme_surface import paint_dither


class LevelMeterWidget(QWidget):
    """A token-based RMS meter with modern and classic renderers."""

    _FLOOR = -60.0
    _CLIP = 0.0
    _ACCENT_BLEND_DB = -24.0
    _WARNING_BLEND_DB = -9.0
    _DANGER_BLEND_DB = -0.5
    _RISE_MS = 170
    _FALL_MS = 460

    def __init__(
        self,
        parent=None,
        orientation: Qt.Orientation = Qt.Orientation.Vertical,
    ) -> None:
        super().__init__(parent)
        self._orientation = orientation
        self._level_db = self._FLOOR
        self._display_db = self._FLOOR
        self._level_animation = QVariantAnimation(self)
        self._level_animation.valueChanged.connect(self._set_display_level)
        self.setAccessibleName("Input level")
        if self._orientation == Qt.Orientation.Horizontal:
            self.setMinimumSize(160, 28)
            self.setMaximumHeight(34)
        else:
            self.setMinimumSize(28, 120)
            self.setMaximumWidth(34)

    def _tokens(self) -> ThemeTokens:
        app = QApplication.instance()
        mode = app.property("fastgraphVisualMode") if app is not None else "dark"
        return mode_tokens(mode)

    def _uses_classic_blocks(self) -> bool:
        return self._tokens().classic_controls

    def _uses_flat_dither(self) -> bool:
        return self._tokens().flat_controls

    def _control_paint_path(self) -> str:
        tokens = self._tokens()
        if tokens.classic_controls:
            return "classic"
        if tokens.flat_controls:
            return "flat"
        return "modern"

    @classmethod
    def _fraction(cls, db: float) -> float:
        return max(0.0, min(1.0, (float(db) - cls._FLOOR) / (cls._CLIP - cls._FLOOR)))

    @classmethod
    def _smoothstep(cls, edge0: float, edge1: float, value: float) -> float:
        if edge1 == edge0:
            return 1.0 if value >= edge1 else 0.0
        amount = max(0.0, min(1.0, (value - edge0) / (edge1 - edge0)))
        return amount * amount * (3.0 - 2.0 * amount)

    @classmethod
    def _glow_color(cls, tokens: ThemeTokens, db: float) -> QColor:
        accent = QColor(tokens.accent)
        warning = QColor(tokens.warning)
        danger = QColor(tokens.danger)
        if db <= cls._ACCENT_BLEND_DB:
            return accent
        if db < cls._WARNING_BLEND_DB:
            amount = cls._smoothstep(cls._ACCENT_BLEND_DB, cls._WARNING_BLEND_DB, db)
            return mix_colors(accent, warning, amount)
        amount = cls._smoothstep(cls._WARNING_BLEND_DB, cls._DANGER_BLEND_DB, db)
        return mix_colors(warning, danger, amount)

    def _paint_rects(self) -> tuple[QRectF, QRectF, QRectF]:
        outer = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        well = outer.adjusted(1.5, 1.5, -1.5, -1.5)
        track = well.adjusted(2.5, 2.5, -2.5, -2.5)
        return outer, well, track

    def _classic_block_rects(self, track: QRectF) -> list[QRectF]:
        gap = 2.0
        if self._orientation == Qt.Orientation.Horizontal:
            count = max(1, int((track.width() + gap) // 14.0))
            extent = (track.width() - gap * (count - 1)) / count
            return [
                QRectF(track.left() + index * (extent + gap), track.top(), extent, track.height())
                for index in range(count)
            ]
        count = max(1, int((track.height() + gap) // 12.0))
        extent = (track.height() - gap * (count - 1)) / count
        return [
            QRectF(
                track.left(),
                track.bottom() - (index + 1) * extent - index * gap,
                track.width(),
                extent,
            )
            for index in range(count)
        ]

    @pyqtSlot(float)
    def set_level(self, db: float) -> None:
        target = max(self._FLOOR, min(self._CLIP, float(db)))
        self._level_db = target
        self._level_animation.stop()
        rising = target > self._display_db
        self._level_animation.setDuration(self._RISE_MS if rising else self._FALL_MS)
        self._level_animation.setEasingCurve(
            QEasingCurve.Type.OutCubic if rising else QEasingCurve.Type.OutQuart
        )
        self._level_animation.setStartValue(self._display_db)
        self._level_animation.setEndValue(target)
        self._level_animation.start()
        self.setToolTip(f"Input level: {self._level_db:.1f} dBFS")

    def _set_display_level(self, value: object) -> None:
        self._display_db = max(self._FLOOR, min(self._CLIP, float(value)))
        self.update()

    def paintEvent(self, event) -> None:
        tokens = self._tokens()
        painter = QPainter(self)
        if tokens.classic_controls:
            self._paint_classic_blocks(painter, tokens)
            painter.end()
            return
        if tokens.flat_controls:
            self._paint_flat_dither(painter, tokens)
            painter.end()
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        outer, well, track = self._paint_rects()
        radius = min(
            float(tokens.geometry.radius_button), track.height() / 2.0, track.width() / 2.0
        )

        surround = QLinearGradient(outer.topLeft(), outer.bottomLeft())
        surround.setColorAt(0.0, mix_colors(QColor(tokens.panel), QColor("#FFFFFF"), 0.035))
        surround.setColorAt(1.0, mix_colors(QColor(tokens.panel), QColor("#000000"), 0.08))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(surround)
        painter.drawRoundedRect(outer, radius + 4.0, radius + 4.0)

        painter.setBrush(QColor(1, 3, 4, 238))
        painter.setPen(QPen(QColor(0, 0, 0, 220), 1.0))
        painter.drawRoundedRect(well, radius + 2.0, radius + 2.0)

        track_base = mix_colors(QColor(tokens.control), QColor("#000000"), 0.68)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_base)
        painter.drawRoundedRect(track, radius, radius)

        self._paint_glow(painter, tokens, track, radius)
        self._paint_track_details(painter, tokens, track, radius)
        painter.end()

    def _paint_glow(
        self, painter: QPainter, tokens: ThemeTokens, track: QRectF, radius: float
    ) -> None:
        track_path = QPainterPath()
        track_path.addRoundedRect(track, radius, radius)
        painter.save()
        painter.setClipPath(track_path)
        fraction = self._fraction(self._display_db)
        activity = self._smoothstep(0.0, 0.14, fraction)
        color = self._glow_color(tokens, self._display_db)
        if self._orientation == Qt.Orientation.Horizontal:
            reach = track.width() * (0.06 + 0.94 * fraction)
            glow_end = min(track.right(), track.left() + reach + track.width() * 0.10)
            glow_gradient = QLinearGradient(
                QPointF(track.left(), track.center().y()),
                QPointF(glow_end, track.center().y()),
            )
            source_point = QPointF(track.left() + track.height() * 0.20, track.center().y())
            source_radius = max(track.height() * 2.8, reach * 0.56)
        else:
            reach = track.height() * (0.06 + 0.94 * fraction)
            glow_end = max(track.top(), track.bottom() - reach - track.height() * 0.10)
            glow_gradient = QLinearGradient(
                QPointF(track.center().x(), track.bottom()),
                QPointF(track.center().x(), glow_end),
            )
            source_point = QPointF(track.center().x(), track.bottom() - track.width() * 0.20)
            source_radius = max(track.width() * 2.8, reach * 0.56)

        if activity > 0.0:
            strongest = QColor(color)
            strongest.setAlpha(round(220 * activity))
            body = QColor(color)
            body.setAlpha(round(142 * activity))
            tail = QColor(color)
            tail.setAlpha(round(54 * activity))
            transparent = QColor(color)
            transparent.setAlpha(0)
            glow_gradient.setColorAt(0.0, strongest)
            glow_gradient.setColorAt(0.38, body)
            glow_gradient.setColorAt(0.76, tail)
            glow_gradient.setColorAt(1.0, transparent)
            painter.setBrush(glow_gradient)
            painter.drawRect(track)

            source_glow = QRadialGradient(source_point, source_radius)
            center = QColor(color)
            center.setAlpha(round(205 * activity))
            middle = QColor(color)
            middle.setAlpha(round(68 * activity))
            source_glow.setColorAt(0.0, center)
            source_glow.setColorAt(0.48, middle)
            source_glow.setColorAt(1.0, transparent)
            painter.setBrush(source_glow)
            painter.drawRect(track)

        painter.restore()

    def _paint_track_details(
        self, painter: QPainter, tokens: ThemeTokens, track: QRectF, radius: float
    ) -> None:
        glass = QLinearGradient(track.topLeft(), track.bottomLeft())
        glass_top = QColor("#FFFFFF")
        glass_top.setAlpha(24)
        glass_mid = QColor("#FFFFFF")
        glass_mid.setAlpha(5)
        glass_bottom = QColor("#000000")
        glass_bottom.setAlpha(34)
        glass.setColorAt(0.0, glass_top)
        glass.setColorAt(0.46, glass_mid)
        glass.setColorAt(1.0, glass_bottom)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glass)
        painter.drawRoundedRect(track, radius, radius)

        mark_color = QColor(tokens.muted)
        mark_color.setAlpha(48)
        painter.setPen(QPen(mark_color, 1.0))
        for mark_db in (-48.0, -36.0, -24.0, -12.0, -3.0):
            mark = self._fraction(mark_db)
            if self._orientation == Qt.Orientation.Horizontal:
                x = track.left() + track.width() * mark
                painter.drawLine(QPointF(x, track.bottom() - 3.0), QPointF(x, track.bottom()))
            else:
                y = track.bottom() - track.height() * mark
                painter.drawLine(QPointF(track.left(), y), QPointF(track.left() + 3.0, y))

        danger_amount = self._smoothstep(self._WARNING_BLEND_DB, self._CLIP, self._display_db)
        if danger_amount > 0.0:
            clip = QColor(tokens.danger)
            clip.setAlpha(round(150 * danger_amount))
            painter.setPen(QPen(clip, 1.0 + danger_amount))
            painter.drawRoundedRect(track.adjusted(1, 1, -1, -1), radius, radius)

        border = mix_colors(QColor("#050607"), QColor(tokens.border), 0.44)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(border, 1.0))
        painter.drawRoundedRect(track, radius, radius)

    def _paint_flat_dither(self, painter: QPainter, tokens: ThemeTokens) -> None:
        """Paint level as ordered pixel density inside a hard square track."""

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        outer = self.rect().adjusted(0, 0, -1, -1)
        track = outer.adjusted(3, 3, -3, -3)
        painter.fillRect(outer, QColor(tokens.control))
        painter.fillRect(track, QColor(tokens.alternate))

        fraction = self._fraction(self._display_db)
        if fraction > 0.0:
            paint_dither(
                painter,
                track.adjusted(1, 1, -1, -1),
                foreground=self._glow_color(tokens, self._display_db),
                density=fraction,
            )

        border = QColor(
            tokens.danger if self._display_db >= self._DANGER_BLEND_DB else tokens.border
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(border, tokens.geometry.border_px))
        painter.drawRect(track)

    def _paint_classic_blocks(self, painter: QPainter, tokens: ThemeTokens) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        outer, well, track = self._paint_rects()
        dark_variant = tokens.dark_bevel
        terminal_variant = tokens.terminal_chrome
        highlight = QColor(
            tokens.accent if terminal_variant else ("#8F8F8F" if dark_variant else "#FFFFFF")
        )
        mid_shadow = QColor(
            "#082E0E" if terminal_variant else ("#1B1B1B" if dark_variant else "#808080")
        )
        shadow = QColor(tokens.border)

        painter.fillRect(outer, QColor(tokens.control))
        painter.setPen(QPen(highlight, 1.0))
        painter.drawLine(outer.topLeft(), outer.topRight())
        painter.drawLine(outer.topLeft(), outer.bottomLeft())
        painter.setPen(QPen(shadow, 1.0))
        painter.drawLine(outer.bottomLeft(), outer.bottomRight())
        painter.drawLine(outer.topRight(), outer.bottomRight())

        painter.fillRect(well, QColor(tokens.raised))
        painter.setPen(QPen(shadow, 1.0))
        painter.drawLine(well.topLeft(), well.topRight())
        painter.drawLine(well.topLeft(), well.bottomLeft())
        painter.setPen(QPen(mid_shadow, 1.0))
        painter.drawLine(well.bottomLeft(), well.bottomRight())
        painter.drawLine(well.topRight(), well.bottomRight())

        painter.fillRect(track, QColor(tokens.alternate))
        blocks = self._classic_block_rects(track.adjusted(1.0, 1.0, -1.0, -1.0))
        fraction = self._fraction(self._display_db)
        active_count = (
            min(len(blocks), int(math.ceil(fraction * len(blocks)))) if fraction > 0 else 0
        )
        for index, block in enumerate(blocks):
            if index >= active_count:
                painter.fillRect(block, QColor(tokens.raised))
                continue
            block_fraction = (index + 1) / len(blocks)
            block_db = self._FLOOR + (self._CLIP - self._FLOOR) * block_fraction
            if block_db >= self._DANGER_BLEND_DB:
                color = QColor(tokens.danger)
            elif block_db >= self._WARNING_BLEND_DB:
                color = QColor(tokens.warning)
            else:
                color = QColor(tokens.accent if terminal_variant else tokens.selected)
            painter.fillRect(block, color)
            painter.setPen(QPen(mix_colors(color, highlight, 0.22), 1.0))
            painter.drawLine(block.topLeft(), block.topRight())
            painter.drawLine(block.topLeft(), block.bottomLeft())
            painter.setPen(QPen(mix_colors(color, shadow, 0.35), 1.0))
            painter.drawLine(block.bottomLeft(), block.bottomRight())
            painter.drawLine(block.topRight(), block.bottomRight())
