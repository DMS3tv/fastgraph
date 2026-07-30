from __future__ import annotations

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

from dms.ui.style_tokens import ThemeTokens, mode_tokens


def _mix(first: QColor, second: QColor, amount: float) -> QColor:
    amount = max(0.0, min(1.0, float(amount)))
    return QColor(
        round(first.red() + (second.red() - first.red()) * amount),
        round(first.green() + (second.green() - first.green()) * amount),
        round(first.blue() + (second.blue() - first.blue()) * amount),
        round(first.alpha() + (second.alpha() - first.alpha()) * amount),
    )


class LevelMeterWidget(QWidget):
    """A token-based RMS meter with a recessed, glowing fill."""

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
            return _mix(accent, warning, amount)
        amount = cls._smoothstep(cls._WARNING_BLEND_DB, cls._DANGER_BLEND_DB, db)
        return _mix(warning, danger, amount)

    def _paint_rects(self) -> tuple[QRectF, QRectF, QRectF]:
        outer = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        well = outer.adjusted(1.5, 1.5, -1.5, -1.5)
        track = well.adjusted(2.5, 2.5, -2.5, -2.5)
        return outer, well, track

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
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        outer, well, track = self._paint_rects()
        radius = min(float(tokens.geometry.radius_button), track.height() / 2.0, track.width() / 2.0)

        surround = QLinearGradient(outer.topLeft(), outer.bottomLeft())
        surround.setColorAt(0.0, _mix(QColor(tokens.panel), QColor("#FFFFFF"), 0.035))
        surround.setColorAt(1.0, _mix(QColor(tokens.panel), QColor("#000000"), 0.08))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(surround)
        painter.drawRoundedRect(outer, radius + 4.0, radius + 4.0)

        painter.setBrush(QColor(1, 3, 4, 238))
        painter.setPen(QPen(QColor(0, 0, 0, 220), 1.0))
        painter.drawRoundedRect(well, radius + 2.0, radius + 2.0)

        track_base = _mix(QColor(tokens.control), QColor("#000000"), 0.68)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_base)
        painter.drawRoundedRect(track, radius, radius)

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

        border = _mix(QColor("#050607"), QColor(tokens.border), 0.44)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(border, 1.0))
        painter.drawRoundedRect(track, radius, radius)
        painter.end()
