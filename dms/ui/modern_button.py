"""Animated, theme-aware button used by FastGraph user actions."""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QEvent, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPalette, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
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
    """A QPushButton with a softly raised surface and animated accent light."""

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

        self._shadow = QGraphicsDropShadowEffect(self)
        self.setGraphicsEffect(self._shadow)
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
        role = self.role()
        if role == "danger":
            return QColor(tokens.danger)
        if role == "positive":
            return QColor(tokens.positive)
        if role == "warning":
            return QColor(tokens.warning)
        return QColor(tokens.accent)

    def _surface_and_text(self, tokens: ThemeTokens) -> tuple[QColor, QColor]:
        role = self.role()
        control = QColor(tokens.control)
        text = QColor(tokens.text)
        if role == "primary":
            return _mix(control, QColor(tokens.accent), 0.16), text
        if role == "positive":
            return _mix(control, QColor(tokens.positive), 0.15), text
        if role == "warning":
            return _mix(control, QColor(tokens.warning), 0.14), text
        if role == "danger":
            return _mix(control, QColor(tokens.danger), 0.16), text
        if role == "ghost":
            ghost = QColor(tokens.panel)
            ghost.setAlpha(180)
            return ghost, QColor(tokens.muted)
        return control, text

    def _effective_hover(self) -> float:
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

    def _animate_press(self, target: float) -> None:
        if not self.isEnabled() or self.role() == "swatch":
            return
        self._press_animation.stop()
        self._press_animation.setDuration(self._tokens().motion.press_ms)
        self._press_animation.setStartValue(self._press_progress)
        self._press_animation.setEndValue(float(target))
        self._press_animation.start()

    def _sync_visual_state(self) -> None:
        if self.role() == "swatch":
            self._shadow.setEnabled(False)
            self.update()
            return
        self._shadow.setEnabled(self.isEnabled())
        tokens = self._tokens()
        if not self.isEnabled():
            self._shadow.setBlurRadius(0)
            self._shadow.setOffset(0, 0)
            self.update()
            return
        progress = self._effective_hover()
        accent = self._accent(tokens)
        rest_shadow = QColor(tokens.shadow)
        rest_shadow.setAlpha(tokens.motion.rest_shadow_alpha)
        glow = QColor(accent)
        glow.setAlpha(tokens.motion.hover_glow_alpha)
        self._shadow.setColor(_mix(rest_shadow, glow, progress))
        self._shadow.setBlurRadius(
            tokens.motion.rest_shadow_blur
            + (tokens.motion.hover_shadow_blur - tokens.motion.rest_shadow_blur) * progress
            - 4.0 * self._press_progress
        )
        self._shadow.setOffset(
            0,
            3.0 - 2.0 * progress - 2.0 * self._press_progress,
        )
        self.update()

    def enterEvent(self, event) -> None:
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._animate_press(1.0)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._animate_press(0.0)
        super().mouseReleaseEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._sync_visual_state()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._sync_visual_state()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if not hasattr(self, "_shadow"):
            return
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
        pressed = self._press_progress if self.isEnabled() else 0.0
        accent = self._accent(tokens)
        base, text = self._surface_and_text(tokens)
        if not self.isEnabled():
            base = QColor(tokens.alternate)
            text = QColor(tokens.disabled)

        tint_amount = tokens.motion.hover_tint_alpha * progress
        surface = _mix(base, accent, tint_amount)
        top = _shade(surface, 0.045 + 0.025 * progress)
        bottom = _shade(surface, -0.065 + 0.02 * progress)

        y_offset = pressed
        rect = QRectF(3.0, 2.0 + y_offset, self.width() - 6.0, self.height() - 8.0)
        radius = min(float(tokens.geometry.radius_button), rect.height() / 2.0)
        if self.role() == "compact":
            radius = rect.height() / 2.0

        lower_edge = QRectF(rect)
        lower_edge.translate(0.0, 2.0 - pressed)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 120 if self.isEnabled() else 55))
        painter.drawRoundedRect(lower_edge, radius, radius)

        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        painter.setBrush(gradient)
        border = _mix(QColor(tokens.border), accent, 0.45 * progress)
        border_width = tokens.geometry.focus_border_px if self.hasFocus() else tokens.geometry.border_px
        painter.setPen(QPen(border, border_width))
        painter.drawRoundedRect(rect, radius, radius)

        option = QStyleOptionButton()
        option.initFrom(self)
        option.text = self.text()
        option.icon = self.icon()
        option.iconSize = self.iconSize()
        option.rect = rect.adjusted(8, 0, -8, 0).toRect()
        option.rect.translate(0, round(y_offset))
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
