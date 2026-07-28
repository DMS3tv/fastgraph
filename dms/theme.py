"""Application-wide light/dark theme management."""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from dms import brand_brand
from dms.settings_manager import SettingsManager


DARK = "dark"
LIGHT = "light"
VALID_THEMES = {DARK, LIGHT}


def normalize_theme(value: object) -> str:
    value = str(value or "").strip().lower()
    return value if value in VALID_THEMES else DARK


def theme_colors(theme: str) -> dict[str, str]:
    if normalize_theme(theme) == LIGHT:
        return {
            "window": "#f3f5f8", "base": "#ffffff", "alternate": "#e9edf2",
            "text": "#20252d", "muted": "#5f6977", "disabled": "#99a1ac",
            "border": "#b9c1cc", "control": "#e8ecf1", "control_hover": "#dce3eb",
            "selected": "#cfe4f6", "accent": "#176ea6", "plot_bg": "#fafbfc",
            "plot_fg": "#4f5967", "plot_grid": "#aeb7c2", "meter_bg": "#e1e5ea",
            "meter_mark": "#87909c", "meter_peak": "#20252d",
        }
    return {
        "window": "#14171c", "base": "#242b36", "alternate": "#20262f",
        "text": "#e3e7ee", "muted": "#91a2ba", "disabled": "#6e7785",
        "border": "#455064", "control": "#2a303b", "control_hover": "#394356",
        "selected": "#38536f", "accent": "#66ccff", "plot_bg": "#1a1a1a",
        "plot_fg": "#aab0b9", "plot_grid": "#555d68", "meter_bg": "#111111",
        "meter_mark": "#555555", "meter_peak": "#ffffff",
    }


def _status_accent_colors(light: bool) -> dict[str, tuple[str, str, str]]:
    """(background, hover, text) triples for the semantic status buttons."""
    if light:
        return {
            "keep": ("#d9f2e1", "#c5e8d0", "#176b37"),
            "fail": ("#f7dddd", "#efc8c8", "#8a2525"),
            "start": ("#dcecf8", "#c8e0f1", "#17577f"),
            "cancel": ("#f8e5d4", "#f1d4b9", "#86440f"),
            "amber": ("#f7ead0", "#efdab0", "#764d00"),
        }
    return {
        "keep": ("#1d5e33", "#257743", "#9deab5"),
        "fail": ("#67292a", "#7a3133", "#f0a0a0"),
        "start": ("#204f73", "#296082", "#9ad3f6"),
        "cancel": ("#5a3218", "#6a3c1d", "#ffbb73"),
        "amber": ("#5a3c12", "#704b17", "#ffdca1"),
    }


def application_stylesheet(theme: str) -> str:
    c = theme_colors(theme)
    light = normalize_theme(theme) == LIGHT
    return _stylesheet_body(
        c,
        status=_status_accent_colors(light),
        tab_bg="#e5e9ee" if light else "#222222",
        tab_selected="#ffffff" if light else "#2d2d2d",
        group_bg="rgba(0, 0, 0, 0.018)" if light else "rgba(255, 255, 255, 0.015)",
    )


def _stylesheet_body(
    c: dict[str, str],
    *,
    status: dict[str, tuple[str, str, str]],
    tab_bg: str,
    tab_selected: str,
    group_bg: str,
) -> str:
    keep_bg, keep_hover, keep_text = status["keep"]
    fail_bg, fail_hover, fail_text = status["fail"]
    start_bg, start_hover, start_text = status["start"]
    cancel_bg, cancel_hover, cancel_text = status["cancel"]
    amber_bg, amber_hover, amber_text = status["amber"]
    return f"""
    QWidget {{ background-color: {c['window']}; color: {c['text']}; font-family: 'Segoe UI', 'Inter', 'Helvetica Neue', Arial, sans-serif; font-size: 13px; }}
    QMainWindow, QDialog {{ background-color: {c['window']}; }}
    QToolTip {{ background-color: {c['base']}; color: {c['text']}; border: 1px solid {c['border']}; }}
    QPushButton {{ background-color: {c['control']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 6px 14px; min-height: 28px; }}
    QPushButton:hover {{ background-color: {c['control_hover']}; }}
    QPushButton:pressed {{ background-color: {c['alternate']}; padding-top: 7px; }}
    QPushButton:disabled {{ color: {c['disabled']}; border-color: {c['border']}; background-color: {c['alternate']}; }}
    QWidget#tab_header_controls QPushButton {{ min-height: 20px; max-height: 24px; padding: 2px 10px; border-radius: 6px; }}
    QWidget#tab_header_controls QPushButton:pressed {{ padding-top: 3px; }}
    QPushButton#btn_keep {{ background-color: {keep_bg}; color: {keep_text}; font-weight: bold; }}
    QPushButton#btn_keep:hover {{ background-color: {keep_hover}; }}
    QPushButton#btn_fail {{ background-color: {fail_bg}; color: {fail_text}; font-weight: bold; }}
    QPushButton#btn_fail:hover {{ background-color: {fail_hover}; }}
    QPushButton#btn_danger {{ background-color: {fail_bg}; color: {fail_text}; font-weight: bold; }}
    QPushButton#btn_danger:hover {{ background-color: {fail_hover}; }}
    QPushButton#btn_start {{ background-color: {start_bg}; color: {start_text}; font-weight: bold; }}
    QPushButton#btn_start:hover {{ background-color: {start_hover}; }}
    QPushButton#btn_cancel {{ background-color: {cancel_bg}; color: {cancel_text}; font-weight: bold; }}
    QPushButton#btn_cancel:hover {{ background-color: {cancel_hover}; }}
    QPushButton#btn_metadata {{ font-weight: 600; }}
    QPushButton#btn_feedback {{ background-color: {fail_bg}; color: {fail_text}; font-size: 11px; font-weight: 600; min-height: 18px; padding: 2px 10px; border-radius: 10px; }}
    QPushButton#btn_feedback:hover {{ background-color: {fail_hover}; }}
    QPushButton#btn_export, QToolButton#section_toggle {{ background-color: {amber_bg}; color: {amber_text}; font-weight: 700; }}
    QPushButton#btn_export:hover, QToolButton#section_toggle:hover {{ background-color: {amber_hover}; }}
    QPushButton#btn_upload, QPushButton#btn_update {{ background-color: {keep_bg}; color: {keep_text}; font-weight: 600; }}
    QPushButton#btn_upload:hover, QPushButton#btn_update:hover {{ background-color: {keep_hover}; }}
    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit {{ background-color: {c['base']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 3px 8px; min-height: 24px; selection-background-color: {c['selected']}; }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView {{ background-color: {c['base']}; color: {c['text']}; selection-background-color: {c['selected']}; }}
    QSpinBox#queue_count_spin {{ font-size: 15px; font-weight: 700; color: {c['accent']}; padding-right: 34px; }}
    QSpinBox#queue_count_spin::up-button, QSpinBox#queue_count_spin::down-button {{ width: 22px; margin: 2px; border: 1px solid {c['border']}; background-color: {c['control']}; border-radius: 6px; }}
    QLabel#label_channel_active {{ color: {c['accent']}; font-weight: bold; font-size: 14px; }}
    QLabel[tone="muted"] {{ color: {c['muted']}; }}
    QLabel[tone="error"] {{ color: {fail_text}; }}
    QLabel[tone="accent"] {{ color: {c['accent']}; font-weight: 600; }}
    QLabel[tone="warning"] {{ color: {amber_text}; }}
    QFrame#diagnostic_box {{ border: 1px solid {c['border']}; border-radius: 6px; background-color: {c['alternate']}; }}
    QLabel#diagnostic_details {{ color: {c['muted']}; background-color: {c['alternate']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 8px; font-family: monospace; }}
    QGroupBox {{ border: 1px solid {c['border']}; border-radius: 10px; margin-top: 10px; padding-top: 8px; background-color: {group_bg}; }}
    QGroupBox::title {{ color: {c['muted']}; subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
    QScrollArea, QScrollBar {{ background-color: {c['window']}; }}
    QScrollBar:vertical {{ width: 8px; }}
    QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; }}
    QTabWidget::pane {{ border: 1px solid {c['border']}; }}
    QTabBar::tab {{ background: {tab_bg}; padding: 6px 14px; border: 1px solid {c['border']}; color: {c['text']}; }}
    QTabBar::tab:selected {{ background: {tab_selected}; color: {c['accent']}; }}
    QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {c['border']}; border-radius: 3px; background: {c['base']}; }}
    QCheckBox::indicator:checked {{ background: #3a7abf; }}
    QStatusBar {{ border-top: 1px solid {c['border']}; }}
    """


def brand_theme_colors() -> dict[str, str]:
    """Color set for brand mode (see BRAND Brand Guide)."""
    return {
        "window": brand_brand.BACKGROUND, "base": brand_brand.SURFACE, "alternate": "#1C1C1C",
        "text": brand_brand.OFF_WHITE, "muted": "#A8A8A8", "disabled": "#6B6B6B",
        "border": "#5C5C5C", "control": brand_brand.SURFACE, "control_hover": "#363636",
        "selected": "#5A3018", "accent": brand_brand.GRADIENT_ORANGE,
        "plot_bg": brand_brand.BACKGROUND, "plot_fg": brand_brand.OFF_WHITE,
        "plot_grid": "#4A4A4A", "meter_bg": "#141414", "meter_mark": "#555555",
        "meter_peak": brand_brand.WHITE,
    }


def brand_application_stylesheet() -> str:
    c = brand_theme_colors()
    base = _stylesheet_body(
        c,
        status=_status_accent_colors(light=False),
        tab_bg=c["alternate"],
        tab_selected=c["base"],
        group_bg="rgba(255, 255, 255, 0.02)",
    )
    return base + f"""
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QPlainTextEdit:focus, QListWidget:focus {{
        border: 2px solid {brand_brand.GRADIENT_ORANGE};
    }}
    QTabBar::tab:selected {{
        border-bottom: 3px solid {brand_brand.GRADIENT_ORANGE};
        color: {brand_brand.OFF_WHITE};
    }}
    QListWidget::item:selected {{
        border-left: 3px solid {brand_brand.GRADIENT_ORANGE};
        background-color: #46301F;
    }}
    QGroupBox#brandPosterBox {{
        border: 1px solid {brand_brand.GRADIENT_ORANGE};
        border-left: 4px solid {brand_brand.GRADIENT_ORANGE};
    }}
    QGroupBox#brandPosterBox::title {{
        color: {brand_brand.GRADIENT_ORANGE};
        font-weight: 700;
    }}
    QGroupBox#brandPosterBox QLabel {{
        background-color: transparent;
    }}
    QGroupBox#brandPosterBox QLabel#brandMetadataStatus,
    QGroupBox#brandPosterBox QLabel#brandFontStatus {{
        background-color: {brand_brand.SURFACE};
        border: 1px solid #5C5C5C;
        border-left: 3px solid {brand_brand.GRADIENT_ORANGE};
        border-radius: 6px;
        padding: 5px 8px;
    }}
    QGroupBox#brandPosterBox QLabel#brandFontStatus[tone="warning"] {{
        border-left-color: {brand_brand.GRADIENT_RED};
    }}
    QPushButton#exportButton {{
        border: 2px solid {brand_brand.GRADIENT_ORANGE};
        background-color: #4A3018;
        color: {brand_brand.OFF_WHITE};
        font-weight: 700;
    }}
    QPushButton#exportButton:hover {{
        background-color: #5E3B1B;
    }}
    QLineEdit[metadataState="manual"] {{
        border-left: 3px solid {brand_brand.GRADIENT_RED};
    }}
    QLineEdit[metadataState="auto"] {{
        border-left: 3px solid {brand_brand.GRADIENT_ORANGE};
    }}
    QWidget#controlPanel {{
        border-left: 1px solid #707070;
    }}
    """


def _palette(theme: str) -> QPalette:
    return _palette_from_colors(theme_colors(theme))


def _brand_palette() -> QPalette:
    return _palette_from_colors(brand_theme_colors())


def _palette_from_colors(c: dict[str, str]) -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(c["window"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(c["base"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(c["alternate"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(c["control"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(c["selected"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.Mid, QColor(c["border"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(c["disabled"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(c["disabled"]))
    return palette


class ThemeController(QObject):
    theme_changed = pyqtSignal(str)
    brand_mode_changed = pyqtSignal(bool)

    def __init__(self, app: QApplication, settings: SettingsManager) -> None:
        super().__init__(app)
        self._app = app
        self._settings = settings
        self._theme = normalize_theme(settings.get("theme"))
        self._brand_mode = bool(settings.get("brand_mode"))
        self._apply()

    @property
    def theme(self) -> str:
        return self._theme

    @property
    def brand_mode(self) -> bool:
        return self._brand_mode

    def set_theme(self, theme: str, *, persist: bool = True) -> None:
        normalized = normalize_theme(theme)
        changed = normalized != self._theme
        self._theme = normalized
        self._apply()
        if persist:
            self._settings.set("theme", normalized)
        if changed:
            self.theme_changed.emit(normalized)

    def set_brand_mode(self, enabled: bool, *, persist: bool = True) -> None:
        enabled = bool(enabled)
        changed = enabled != self._brand_mode
        self._brand_mode = enabled
        self._apply()
        if persist:
            self._settings.set("brand_mode", enabled)
        if changed:
            self.brand_mode_changed.emit(enabled)

    def _apply(self) -> None:
        if self._brand_mode:
            self._app.setPalette(_brand_palette())
            self._app.setStyleSheet(brand_application_stylesheet())
        else:
            self._app.setPalette(_palette(self._theme))
            self._app.setStyleSheet(application_stylesheet(self._theme))
