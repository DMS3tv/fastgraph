"""Animated, theme-aware button used by FastGraph user actions."""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QEvent, QPointF, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import (
    QColor,
    QGradient,
    QLinearGradient,
    QPainter,
    QPalette,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QApplication,
    QPushButton,
    QStyle,
    QStyleOptionButton,
)

from dms.ui.style_tokens import ThemeTokens, mode_tokens


_OBJECT_ROLES = {
    "btn_keep": "positive",
    "btn_upload": "positive",
    "btn_update": "positive",
    "btn_fail": "danger",
    "btn_danger": "danger",
    "btn_feedback": "compact",
    "btn_start": "primary",
    "btn_cancel": "warning",
    "btn_export": "primary",
    "exportButton": "primary",
}


def _mix(first: QColor, second: QColor, amount: float) -> QColor:
    amount = max(0.0, min(1.0, float(amount)))
    return QColor(
        round(first.red() + (second.red() - first.red()) * amount),
        round(first.green() + (second.green() - first.green()) * amount),
        round(first.blue() + (second.blue() - first.blue()) * amount),
        round(first.alpha() + (second.alpha() - first.alpha()) * amount),
    )


def _shade(color: QColor, amount: float) -> QColor:
    return _mix(color, QColor("#FFFFFF") if amount >= 0 else QColor("#000000"), abs(amount))


class ModernButton(QPushButton):
    """A QPushButton recessed into its surface with an animated inner light."""

    VALID_ROLES = {
        "default",
        "primary",
        "positive",
        "warning",
        "danger",
        "ghost",
        "compact",
        "swatch",
    }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover_progress = 0.0
        self._press_progress = 0.0

        self._hover_animation = QVariantAnimation(self)
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_animation.valueChanged.connect(self._set_hover_progress)

        self._press_animation = QVariantAnimation(self)
        self._press_animation.setDuration(90)
        self._press_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._press_animation.valueChanged.connect(self._set_press_progress)

        self.pressed.connect(self._begin_click_flash)
        self.released.connect(self._release_click_flash)
        self._sync_visual_state()

    def setRole(self, role: str) -> None:
        normalized = str(role or "default").strip().lower()
        if normalized not in self.VALID_ROLES:
            raise ValueError(f"Unknown button role: {role}")
        self.setProperty("buttonRole", normalized)
        self._sync_visual_state()

    def role(self) -> str:
        explicit = str(self.property("buttonRole") or "").strip().lower()
        if explicit in self.VALID_ROLES:
            return explicit
        if self.objectName() in _OBJECT_ROLES:
            return _OBJECT_ROLES[self.objectName()]
        if not self.text().strip() and self.styleSheet():
            return "swatch"
        parent = self.parentWidget()
        if parent is not None and parent.objectName() == "tab_header_controls":
            return "compact"
        label = self.text().strip().lower()
        if any(word in label for word in ("remove", "delete", "clear all", "fail / redo")):
            return "danger"
        if label.startswith("cancel"):
            return "warning"
        if label.startswith(("keep", "accept", "save", "upload", "update")):
            return "positive"
        if label.startswith(("export", "run", "measure", "capture", "send to curator")):
            return "primary"
        return "default"

    def hoverProgress(self) -> float:
        return self._hover_progress

    def pressProgress(self) -> float:
        return self._press_progress

    def _tokens(self) -> ThemeTokens:
        app = QApplication.instance()
        mode = app.property("fastgraphVisualMode") if app is not None else "dark"
        return mode_tokens(mode)

    def _accent(self, tokens: ThemeTokens) -> QColor:
        dark_accent = str(self.property("darkAccentColor") or "").strip()
        if tokens.name == "dark" and dark_accent:
            return QColor(dark_accent)
        role = self.role()
        if role == "danger":
            return QColor(tokens.danger)
        if role == "positive":
            return QColor(tokens.positive)
        if role == "warning":
            return QColor(tokens.warning)
        return QColor(tokens.accent)

    def _surface_and_text(self, tokens: ThemeTokens) -> tuple[QColor, QColor]:
        light_mode = tokens.name == "light"
        control = QColor(tokens.control if light_mode else tokens.viewport)
        text = QColor(tokens.text)
        darkness = 0.18 if light_mode else 0.58
        base = _mix(control, QColor("#000000"), darkness)
        if self.role() == "ghost":
            base = _mix(base, QColor(tokens.panel), 0.22)
            text = QColor(tokens.muted)
        return base, text

    def _effective_hover(self) -> float:
        if not self.isEnabled():
            return 0.0
        focus_progress = (
            self._tokens().motion.focus_glow_strength
            if self.hasFocus() and self.focusPolicy() != Qt.FocusPolicy.NoFocus
            else 0.0
        )
        return max(self._hover_progress, focus_progress)

    def _set_hover_progress(self, value: object) -> None:
        self._hover_progress = max(0.0, min(1.0, float(value)))
        self._sync_visual_state()

    def _set_press_progress(self, value: object) -> None:
        self._press_progress = max(0.0, min(1.0, float(value)))
        self._sync_visual_state()

    def _animate_hover(self, target: float) -> None:
        if not self.isEnabled() or self.role() == "swatch":
            return
        motion = self._tokens().motion
        self._hover_animation.stop()
        self._hover_animation.setDuration(
            motion.hover_in_ms if target > self._hover_progress else motion.hover_out_ms
        )
        self._hover_animation.setStartValue(self._hover_progress)
        self._hover_animation.setEndValue(float(target))
        self._hover_animation.start()

    def _animate_press(self, target: float, *, duration: int | None = None) -> None:
        if not self.isEnabled() or self.role() == "swatch":
            return
        self._press_animation.stop()
        self._press_animation.setDuration(
            self._tokens().motion.press_ms if duration is None else int(duration)
        )
        self._press_animation.setStartValue(self._press_progress)
        self._press_animation.setEndValue(float(target))
        self._press_animation.start()

    def _begin_click_flash(self) -> None:
        self._press_progress = max(self._press_progress, 0.32)
        self._animate_press(1.0)

    def _release_click_flash(self) -> None:
        self._press_progress = max(self._press_progress, 0.58)
        self._animate_press(0.0, duration=130)

    def _sync_visual_state(self) -> None:
        self.update()

    def _glow_profile(self) -> dict[str, float | int]:
        if not self.isEnabled():
            return {
                "center_y": 1.05,
                "radius": 0.50,
                "center_alpha": 0,
                "middle_alpha": 0,
                "edge_alpha": 0,
                "uniform_alpha": 0,
            }
        hover = self._effective_hover() if self.isEnabled() else 0.0
        flash = self._press_progress if self.isEnabled() else 0.0
        return {
            "center_y": 1.05 - 0.27 * hover - 0.12 * flash,
            "radius": 0.50 + 0.48 * hover + 0.10 * flash,
            "center_alpha": round(76 + 32 * hover + 48 * flash),
            "middle_alpha": round(34 + 34 * hover + 32 * flash),
            "edge_alpha": round(1 + 10 * hover + 5 * flash),
            "uniform_alpha": round(1 + 6 * hover + 10 * flash),
        }

    def _paint_rects(self) -> tuple[QRectF, QRectF, QRectF]:
        outer = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        well = outer.adjusted(1.5, 1.5, -1.5, -1.5)
        button = well.adjusted(2.0, 2.0, -2.0, -2.0)
        return outer, well, button

    def _has_persistent_outline(self) -> bool:
        return self.objectName() == "btn_start" or bool(self.property("emphasized"))

    def enterEvent(self, event) -> None:
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._sync_visual_state()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._sync_visual_state()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self._hover_animation.stop()
            self._press_animation.stop()
            self._hover_progress = 0.0
            self._press_progress = 0.0
        if event.type() in {
            QEvent.Type.EnabledChange,
            QEvent.Type.PaletteChange,
            QEvent.Type.StyleChange,
            QEvent.Type.ApplicationPaletteChange,
        }:
            self._sync_visual_state()

    def sizeHint(self):
        hint = super().sizeHint()
        geometry = self._tokens().geometry
        role = self.role()
        if role == "compact":
            minimum = geometry.compact_button_height
        elif role == "primary":
            minimum = geometry.primary_button_height
        else:
            minimum = geometry.button_height
        hint.setHeight(max(hint.height(), minimum + 4))
        return hint

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event) -> None:
        if self.role() == "swatch":
            super().paintEvent(event)
            return

        tokens = self._tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        progress = self._effective_hover() if self.isEnabled() else 0.0
        flash = self._press_progress if self.isEnabled() else 0.0
        accent = self._accent(tokens)
        base, text = self._surface_and_text(tokens)
        if not self.isEnabled():
            base = _mix(QColor(tokens.alternate), QColor("#000000"), 0.28)
            text = QColor(tokens.disabled)

        outer, well, rect = self._paint_rects()
        radius = min(float(tokens.geometry.radius_button), rect.height() / 2.0)
        if self.role() == "compact":
            radius = rect.height() / 2.0

        outer_radius = min(radius + 4.0, outer.height() / 2.0)
        well_radius = min(radius + 2.0, well.height() / 2.0)
        surround = QColor(tokens.panel)
        surround_top = _shade(surround, 0.035)
        surround_bottom = _shade(surround, -0.045)
        surround_gradient = QLinearGradient(outer.topLeft(), outer.bottomLeft())
        surround_gradient.setColorAt(0.0, surround_top)
        surround_gradient.setColorAt(1.0, surround_bottom)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(surround_gradient)
        painter.drawRoundedRect(outer, outer_radius, outer_radius)

        painter.setBrush(QColor(1, 3, 4, 238 if self.isEnabled() else 175))
        painter.setPen(QPen(QColor(0, 0, 0, 220), 1.0))
        painter.drawRoundedRect(well, well_radius, well_radius)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(base)
        painter.drawRoundedRect(rect, radius, radius)

        profile = self._glow_profile()
        uniform = QColor(accent)
        uniform.setAlpha(int(profile["uniform_alpha"]))
        painter.setBrush(uniform)
        painter.drawRoundedRect(rect, radius, radius)

        glow = QRadialGradient(
            QPointF(0.5, float(profile["center_y"])),
            float(profile["radius"]),
        )
        glow.setCoordinateMode(QGradient.CoordinateMode.ObjectBoundingMode)
        center = QColor(accent)
        center.setAlpha(int(profile["center_alpha"]))
        middle = QColor(accent)
        middle.setAlpha(int(profile["middle_alpha"]))
        edge = QColor(accent)
        edge.setAlpha(int(profile["edge_alpha"]))
        transparent = QColor(accent)
        transparent.setAlpha(0)
        glow.setColorAt(0.0, center)
        glow.setColorAt(0.48, middle)
        glow.setColorAt(0.88, edge)
        glow.setColorAt(1.0, transparent)
        painter.setBrush(glow)
        painter.drawRoundedRect(rect, radius, radius)

        inner_border = _mix(QColor("#050607"), accent, 0.13 + 0.22 * progress)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(inner_border, 1.0))
        painter.drawRoundedRect(rect, radius, radius)

        if self.isEnabled() and self._has_persistent_outline():
            painter.setPen(QPen(accent, 1.5))
            painter.drawRoundedRect(
                outer.adjusted(1, 1, -1, -1),
                outer_radius,
                outer_radius,
            )

        if self.hasFocus():
            painter.setPen(QPen(accent, tokens.geometry.focus_border_px))
            painter.drawRoundedRect(outer.adjusted(1, 1, -1, -1), outer_radius, outer_radius)

        text = _mix(text, accent, 0.30 + 0.16 * progress + 0.12 * flash)
        option = QStyleOptionButton()
        option.initFrom(self)
        option.text = self.text()
        option.icon = self.icon()
        option.iconSize = self.iconSize()
        option.rect = rect.adjusted(8, 0, -8, 0).toRect()
        option.palette = QPalette(option.palette)
        option.palette.setColor(QPalette.ColorRole.ButtonText, text)
        if self.isDown():
            option.state |= QStyle.StateFlag.State_Sunken
        self.style().drawControl(
            QStyle.ControlElement.CE_PushButtonLabel,
            option,
            painter,
            self,
        )
        painter.end()
