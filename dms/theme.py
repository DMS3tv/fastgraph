"""Application-wide theme management."""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from dms import brand_brand
from dms.dither_fonts import configure_dither_typography, dither_font_status
from dms.settings_manager import SettingsManager
from dms.ui.style_tokens import (
    BRAND_TOKENS,
    THEME_DEFINITIONS,
    ThemeTokens,
    theme_definition,
    tokens_for,
)

DARK = "dark"
LIGHT = "light"
FASTGRAPH_95 = "fastgraph95"
FASTGRAPH_95_DARK = "fastgraph95_dark"
HACKERMAN_95 = "hackerman95"
DITHER = "dither"
VALID_THEMES = {definition.key for definition in THEME_DEFINITIONS}


def normalize_theme(value: object) -> str:
    value = str(value or "").strip().lower()
    return value if value in VALID_THEMES else DARK


def _relative_luminance(color: QColor) -> float:
    channels = []
    for value in (color.redF(), color.greenF(), color.blueF()):
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def graph_contrast_ratio(foreground: object, background: object) -> float:
    """Return the WCAG contrast ratio for two solid colors."""
    foreground_color = QColor(foreground)
    background_color = QColor(background)
    lighter = max(_relative_luminance(foreground_color), _relative_luminance(background_color))
    darker = min(_relative_luminance(foreground_color), _relative_luminance(background_color))
    return (lighter + 0.05) / (darker + 0.05)


def ensure_graph_color(
    foreground: object,
    background: object,
    *,
    minimum_ratio: float = 4.5,
) -> QColor:
    """Adjust a display trace toward black or white until it stays visible."""
    source = QColor(foreground)
    backdrop = QColor(background)
    if not source.isValid():
        source = QColor("#000000")
    if not backdrop.isValid():
        backdrop = QColor("#ffffff")
    if graph_contrast_ratio(source, backdrop) >= minimum_ratio:
        return source

    black = QColor("#000000")
    white = QColor("#ffffff")
    target = (
        black
        if graph_contrast_ratio(black, backdrop) >= graph_contrast_ratio(white, backdrop)
        else white
    )
    low = 0.0
    high = 1.0
    for _ in range(14):
        amount = (low + high) / 2.0
        candidate = QColor(
            round(source.red() + (target.red() - source.red()) * amount),
            round(source.green() + (target.green() - source.green()) * amount),
            round(source.blue() + (target.blue() - source.blue()) * amount),
            source.alpha(),
        )
        if graph_contrast_ratio(candidate, backdrop) >= minimum_ratio:
            high = amount
        else:
            low = amount
    return QColor(
        round(source.red() + (target.red() - source.red()) * high),
        round(source.green() + (target.green() - source.green()) * high),
        round(source.blue() + (target.blue() - source.blue()) * high),
        source.alpha(),
    )


def theme_colors(theme: str) -> dict[str, str]:
    token = tokens_for(normalize_theme(theme))
    light = token.name == LIGHT
    return _color_dict(
        token,
        meter_bg="#e1e5ea" if light else "#111111",
        meter_mark="#87909c" if light else "#555555",
        meter_peak="#20252d" if light else "#ffffff",
        lowercase=True,
    )


def theme_trace_palette(theme: str, *, brand_mode: bool = False) -> list[str]:
    """Return a copy of the ordered trace palette for one visual mode."""
    if brand_mode:
        return list(brand_brand.TRACE_PALETTE)
    palette = tokens_for(normalize_theme(theme)).trace_palette
    return list(palette) if palette else list(brand_brand.NON_BRAND_DEFAULT_COLORS)


def _color_dict(
    token: ThemeTokens,
    *,
    meter_bg: str,
    meter_mark: str,
    meter_peak: str,
    lowercase: bool = False,
) -> dict[str, str]:
    values = {
        "window": token.background,
        "background": token.background,
        "viewport": token.viewport,
        "panel": token.panel,
        "raised": token.raised,
        "base": token.raised,
        "alternate": token.alternate,
        "text": token.text,
        "muted": token.muted,
        "disabled": token.disabled,
        "border": token.border,
        "control": token.control,
        "control_hover": token.control_hover,
        "selected": token.selected,
        "accent": token.accent,
        "plot_bg": token.plot_bg,
        "plot_fg": token.plot_fg,
        "plot_grid": token.plot_grid,
        "meter_bg": meter_bg,
        "meter_mark": meter_mark,
        "meter_peak": meter_peak,
    }
    if lowercase:
        return {key: value.lower() for key, value in values.items()}
    return values


def _status_accent_colors(tokens: ThemeTokens) -> dict[str, tuple[str, str, str]]:
    """(background, hover, text) triples for the semantic status buttons."""
    if tokens.dither_chrome:
        return {
            "keep": ("#1E3A26", "#284A31", "#9CCFA6"),
            "fail": ("#4A2320", "#5C2C28", "#EE8C86"),
            "start": ("#4A2318", "#5C2D1E", "#E8845C"),
            "cancel": ("#4E3520", "#603F26", "#E0A87A"),
            "amber": ("#453A1C", "#554823", "#E3C67C"),
        }
    if tokens.name == LIGHT:
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
    normalized = normalize_theme(theme)
    c = theme_colors(normalized)
    light = normalized == LIGHT
    definition = theme_definition(normalized)
    token = definition.tokens
    base = _stylesheet_body(
        c,
        visual_tokens=token,
        status=_status_accent_colors(token),
        tab_bg="#e5e9ee" if light else token.alternate,
        tab_selected=token.raised,
        group_bg=token.panel,
    )
    style_builder = _STYLE_FAMILY_BUILDERS.get(definition.style_family)
    return base + style_builder(c) if style_builder is not None else base


def _fastgraph95_stylesheet(
    c: dict[str, str],
    *,
    dark_variant: bool = False,
    terminal_variant: bool = False,
) -> str:
    """Square, beveled FastGraph 95 overrides for the shared widget rules."""
    highlight = "#596259" if terminal_variant else ("#8f8f8f" if dark_variant else "#ffffff")
    mid_edge = "#121512" if terminal_variant else ("#1b1b1b" if dark_variant else "#808080")
    field_bg = c["raised"]
    field_text = c["text"]
    selection_text = c["text"] if terminal_variant else ("#eeeeee" if dark_variant else "#ffffff")
    segment_text = "#000000" if dark_variant or terminal_variant else "#ffffff"
    tooltip_bg = c["raised"] if terminal_variant else ("#303030" if dark_variant else "#ffffe1")
    font_stack = (
        "'Monaco', 'Courier New', monospace"
        if terminal_variant
        else "'Tahoma', 'MS Sans Serif', sans-serif"
    )
    return f"""
    QWidget {{
        font-family: {font_stack};
        font-size: 12px;
        border-radius: 0px;
    }}
    QWidget[ditherSurface="true"] {{ background: transparent; border: none; }}
    QWidget[surfaceLevel="viewport"], QWidget[surfaceLevel="panel"],
    QWidget[surfaceLevel="raised"] {{
        background-color: {c["panel"]};
        border-top: 2px solid {highlight};
        border-left: 2px solid {highlight};
        border-right: 2px solid #000000;
        border-bottom: 2px solid #000000;
        border-radius: 0px;
    }}
    QPushButton {{
        background-color: {c["control"]};
        color: {c["text"]};
        border: none;
        border-radius: 0px;
        padding: 4px 10px;
        min-height: 24px;
    }}
    QPushButton:hover {{ background-color: {c["control_hover"]}; }}
    QPushButton:pressed {{ background-color: {c["control"]}; padding: 5px 9px 3px 11px; }}
    QPushButton:disabled {{ color: {c["disabled"]}; background-color: {c["control"]}; }}
    QToolButton[menuButton="true"] {{
        background-color: {c["control"]};
        color: {c["text"]};
        border: none;
        border-radius: 0px;
        padding: 4px 10px;
        min-height: 24px;
    }}
    QToolButton[menuButton="true"]:hover {{ background-color: {c["control_hover"]}; }}
    QToolButton[menuButton="true"]:disabled {{ color: {c["disabled"]}; background-color: {c["control"]}; }}
    QToolButton[menuButton="true"]::menu-indicator {{ image: none; width: 0px; }}
    QPushButton[measureSegment="true"] {{
        background-color: {c["control"]};
        color: {c["muted"]};
        border-top: 2px solid {highlight};
        border-left: 2px solid {highlight};
        border-right: 2px solid #000000;
        border-bottom: 2px solid #000000;
        border-radius: 0px;
        padding: 4px 10px;
        min-height: 22px;
    }}
    QPushButton[measureSegment="true"]:hover {{
        background-color: {c["control_hover"]};
        color: {c["text"]};
    }}
    QPushButton[measureSegment="true"]:focus {{
        border: 2px dotted {c["accent"]};
        color: {c["text"]};
    }}
    QPushButton[measureSegment="true"]:checked {{
        background-color: {c["accent"]};
        color: {segment_text};
        border-top: 2px solid #000000;
        border-left: 2px solid #000000;
        border-right: 2px solid {highlight};
        border-bottom: 2px solid {highlight};
    }}
    QPushButton[measureSegment="true"]:checked:hover {{
        border-color: {segment_text};
    }}
    QPushButton[measureSegment="true"]:checked:focus {{
        border: 2px dotted {segment_text};
    }}
    QPushButton[measureSegment="true"]:disabled {{
        background-color: {c["alternate"]};
        color: {c["disabled"]};
        border-color: {c["border"]};
    }}
    QPushButton[measureSegment="true"]:checked:disabled {{
        background-color: {c["selected"]};
        color: {selection_text};
        border: 2px dotted {c["disabled"]};
    }}
    QMessageBox, QInputDialog, QFileDialog, QDialog {{
        background-color: {c["panel"]};
        color: {c["text"]};
    }}
    QMessageBox QLabel, QInputDialog QLabel, QFileDialog QLabel,
    QDialogButtonBox {{ background: transparent; color: {c["text"]}; border: none; }}
    QDialogButtonBox QPushButton, QMessageBox QPushButton,
    QInputDialog QPushButton, QFileDialog QPushButton {{
        background-color: {c["control"]};
        color: {c["text"]};
        border-top: 2px solid {highlight};
        border-left: 2px solid {highlight};
        border-right: 2px solid #000000;
        border-bottom: 2px solid #000000;
        border-radius: 0px;
        padding: 4px 12px;
        min-width: 64px;
        min-height: 22px;
    }}
    QDialogButtonBox QPushButton:pressed, QMessageBox QPushButton:pressed,
    QInputDialog QPushButton:pressed, QFileDialog QPushButton:pressed {{
        border-top: 2px solid #000000;
        border-left: 2px solid #000000;
        border-right: 2px solid {highlight};
        border-bottom: 2px solid {highlight};
        padding: 5px 11px 3px 13px;
    }}
    QPushButton#btn_keep, QPushButton#btn_fail, QPushButton#btn_danger,
    QPushButton#btn_start, QPushButton#btn_cancel, QPushButton#btn_export,
    QPushButton#btn_upload, QPushButton#btn_update, QPushButton#btn_feedback {{
        background-color: {c["control"]}; color: {c["text"]}; border-radius: 0px;
    }}
    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit,
    QListWidget, QTreeWidget, QTableWidget, QKeySequenceEdit {{
        background-color: {field_bg};
        color: {field_text};
        border-top: 2px solid #000000;
        border-left: 2px solid #000000;
        border-right: 2px solid {highlight};
        border-bottom: 2px solid {highlight};
        border-radius: 0px;
        selection-background-color: {c["selected"]};
        selection-color: {selection_text};
    }}
    QComboBox::drop-down {{
        width: 20px;
        background: {c["control"]};
        border-left: 1px solid {mid_edge};
    }}
    QComboBox QAbstractItemView {{
        background: {field_bg}; color: {field_text};
        border: 1px solid #000000;
        selection-background-color: {c["selected"]};
        selection-color: {selection_text};
    }}
    QGroupBox {{
        background-color: {c["panel"]};
        border: 1px solid {mid_edge};
        border-radius: 0px;
        margin-top: 14px;
    }}
    QGroupBox::title {{
        color: {c["text"]};
        background-color: {c["panel"]};
        left: 8px;
        padding: 0 4px;
        font-family: 'MS Sans Serif', 'Tahoma', sans-serif;
        font-size: 12px;
        font-weight: normal;
    }}
    QTabWidget::pane {{
        background: {c["panel"]};
        border-top: 2px solid {highlight};
        border-left: 2px solid {highlight};
        border-right: 2px solid #000000;
        border-bottom: 2px solid #000000;
        border-radius: 0px;
        top: -2px;
    }}
    QTabBar::tab {{
        background: {c["control"]};
        color: {c["text"]};
        padding: 5px 14px;
        margin-right: 1px;
        border-top: 2px solid {highlight};
        border-left: 2px solid {highlight};
        border-right: 2px solid #000000;
        border-bottom: 2px solid {mid_edge};
        border-radius: 0px;
    }}
    QTabBar::tab:hover {{ background: {c["control_hover"]}; }}
    QTabBar::tab:selected {{
        background: {c["panel"]};
        color: {c["text"]};
        border-bottom: 2px solid {c["panel"]};
        padding-top: 6px;
    }}
    QToolButton#section_toggle {{
        background-color: {c["control"]}; color: {c["text"]};
        border-top: 2px solid {highlight};
        border-left: 2px solid {highlight};
        border-right: 2px solid #000000;
        border-bottom: 2px solid #000000;
        border-radius: 0px;
    }}
    QToolButton#section_toggle:hover,
    QToolButton#section_toggle:checked {{ background-color: {c["control_hover"]}; border-left: 2px solid {highlight}; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 13px; height: 13px;
        background: {field_bg};
        border-top: 1px solid #000000;
        border-left: 1px solid #000000;
        border-right: 1px solid {highlight};
        border-bottom: 1px solid {highlight};
        border-radius: 0px;
    }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {c["selected"]};
        border: 2px solid {highlight};
    }}
    QScrollBar:vertical {{ width: 17px; margin: 17px 0 17px 0; background: {c["alternate"]}; border-radius: 0px; }}
    QScrollBar:horizontal {{ height: 17px; margin: 0 17px 0 17px; background: {c["alternate"]}; border-radius: 0px; }}
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
        background: {c["control"]};
        border-top: 2px solid {highlight};
        border-left: 2px solid {highlight};
        border-right: 2px solid #000000;
        border-bottom: 2px solid #000000;
        border-radius: 0px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        background: {c["control"]};
        border-top: 2px solid {highlight};
        border-left: 2px solid {highlight};
        border-right: 2px solid #000000;
        border-bottom: 2px solid #000000;
        width: 17px; height: 17px;
    }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: {c["alternate"]}; }}
    QHeaderView::section {{
        background: {c["control"]}; color: {c["text"]};
        border-top: 1px solid {highlight}; border-left: 1px solid {highlight};
        border-right: 1px solid #000000; border-bottom: 1px solid #000000;
        padding: 3px;
    }}
    QMenuBar, QMenu {{ background: {c["panel"]}; color: {c["text"]}; border: 1px solid #000000; }}
    QMenu::item:selected {{ background: {c["selected"]}; color: {selection_text}; }}
    QStatusBar {{
        background: {c["panel"]};
        color: {c["text"]};
        border-top: 2px solid {highlight};
    }}
    QToolTip {{ background: {tooltip_bg}; color: {c["text"]}; border: 1px solid #000000; }}
    """


def _fastgraph95_dark_stylesheet(c: dict[str, str]) -> str:
    return _fastgraph95_stylesheet(c, dark_variant=True)


def _hackerman95_stylesheet(c: dict[str, str]) -> str:
    return (
        _fastgraph95_stylesheet(c, dark_variant=True, terminal_variant=True)
        + f"""
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QPlainTextEdit:focus, QListWidget:focus, QTreeWidget:focus {{
        border: 2px solid {c["accent"]};
    }}
    QTabBar::tab:selected {{ color: {c["accent"]}; border-bottom-color: {c["accent"]}; }}
    QMenu::item:selected {{ background: {c["selected"]}; color: {c["accent"]}; }}
    QLabel[tone="accent"], QLabel#label_channel_active {{ color: {c["accent"]}; }}
    """
    )


def _dither_stylesheet(c: dict[str, str]) -> str:
    """Return hard-edged print overrides for flat control themes."""

    heading = dither_font_status().heading_family
    return f"""
    QWidget[ditherSurface="true"] {{
        background: transparent;
        border: 1px solid {c["border"]};
        border-radius: 0px;
    }}
    QWidget[surfaceLevel="panel"] {{
        border: 1px solid {c["border"]};
        border-radius: 0px;
    }}
    QWidget[surfaceLevel="viewport"], QWidget[surfaceLevel="raised"] {{
        border: 1px solid {c["border"]};
        border-radius: 0px;
    }}
    QMainWindow, QDialog, QMessageBox, QInputDialog, QFileDialog {{
        background-color: {c["window"]};
        color: {c["text"]};
        border-radius: 0px;
    }}
    QMessageBox QLabel, QInputDialog QLabel, QFileDialog QLabel,
    QDialogButtonBox {{ background: transparent; color: {c["text"]}; border: none; }}
    QPushButton, QDialogButtonBox QPushButton, QMessageBox QPushButton,
    QInputDialog QPushButton, QFileDialog QPushButton {{
        background-color: {c["control"]};
        color: {c["text"]};
        border: 1px solid {c["border"]};
        border-radius: 0px;
        padding: 6px 14px;
    }}
    QPushButton:hover, QDialogButtonBox QPushButton:hover,
    QMessageBox QPushButton:hover, QInputDialog QPushButton:hover,
    QFileDialog QPushButton:hover {{
        background-color: {c["control_hover"]};
        border-color: {c["text"]};
    }}
    QPushButton:pressed, QDialogButtonBox QPushButton:pressed,
    QMessageBox QPushButton:pressed, QInputDialog QPushButton:pressed,
    QFileDialog QPushButton:pressed {{
        background-color: {c["accent"]};
        color: {c["window"]};
        border-color: {c["accent"]};
        padding: 6px 14px;
    }}
    QPushButton:focus, QDialogButtonBox QPushButton:focus,
    QMessageBox QPushButton:focus, QInputDialog QPushButton:focus,
    QFileDialog QPushButton:focus {{ border: 2px solid {c["accent"]}; }}
    QPushButton:disabled, QDialogButtonBox QPushButton:disabled,
    QMessageBox QPushButton:disabled, QInputDialog QPushButton:disabled,
    QFileDialog QPushButton:disabled {{
        background-color: {c["alternate"]};
        color: {c["disabled"]};
        border: 1px solid {c["border"]};
    }}
    QToolButton[menuButton="true"] {{
        background-color: {c["control"]};
        color: {c["text"]};
        border: 1px solid {c["border"]};
        border-radius: 0px;
        padding: 6px 14px;
    }}
    QToolButton[menuButton="true"]:hover {{
        background-color: {c["control_hover"]};
        border-color: {c["text"]};
    }}
    QToolButton[menuButton="true"]:disabled {{
        background-color: {c["alternate"]};
        color: {c["disabled"]};
        border: 1px solid {c["border"]};
    }}
    QToolButton[menuButton="true"]::menu-indicator {{ image: none; width: 0px; }}
    QPushButton[measureSegment="true"] {{
        background-color: {c["control"]};
        color: {c["muted"]};
        border: 1px solid {c["border"]};
        border-radius: 0px;
        padding: 5px 12px;
        min-height: 24px;
        font-family: '{heading}', 'DIN Condensed', 'Oswald', 'Archivo Narrow',
            'Arial Narrow', 'Avenir Next Condensed', 'Inter', sans-serif;
        font-weight: 700;
    }}
    QPushButton[measureSegment="true"]:hover {{
        background-color: {c["control_hover"]};
        color: {c["text"]};
        border-color: {c["text"]};
    }}
    QPushButton[measureSegment="true"]:focus {{
        background-color: {c["control"]};
        color: {c["text"]};
        border: 2px solid {c["accent"]};
    }}
    QPushButton[measureSegment="true"]:checked {{
        background-color: {c["accent"]};
        color: #000000;
        border: 1px solid {c["accent"]};
    }}
    QPushButton[measureSegment="true"]:checked:hover {{
        border: 2px solid {c["text"]};
    }}
    QPushButton[measureSegment="true"]:checked:focus {{
        border: 2px solid {c["text"]};
    }}
    QPushButton[measureSegment="true"]:disabled {{
        background-color: {c["alternate"]};
        color: {c["disabled"]};
        border: 1px solid {c["border"]};
    }}
    QPushButton[measureSegment="true"]:checked:disabled {{
        background-color: {c["selected"]};
        color: {c["muted"]};
        border: 1px solid {c["accent"]};
    }}
    QWidget#tab_header_controls QPushButton {{ border-radius: 0px; }}
    QGroupBox {{
        background-color: {c["panel"]};
        border: 1px solid {c["border"]};
        border-radius: 0px;
    }}
    QGroupBox::title {{
        background-color: {c["panel"]};
        color: {c["text"]};
        font-family: '{heading}', 'DIN Condensed', 'Oswald', 'Archivo Narrow',
            'Arial Narrow', 'Avenir Next Condensed', 'Inter', sans-serif;
        font-weight: 700;
        left: 8px;
        padding: 0 5px;
    }}
    QTabWidget::pane {{
        background: {c["viewport"]};
        border: 1px solid {c["border"]};
        border-radius: 0px;
        top: -1px;
    }}
    QTabBar::tab {{
        background: {c["alternate"]};
        color: {c["text"]};
        border: 1px solid {c["border"]};
        border-radius: 0px;
        margin-right: 1px;
        padding: 7px 16px;
    }}
    QTabBar::tab:hover {{ background: {c["control"]}; border-color: {c["text"]}; }}
    QTabBar::tab:selected {{
        background: {c["accent"]};
        color: {c["window"]};
        border: 1px solid {c["accent"]};
    }}
    QTabBar::tab:disabled {{
        background: {c["alternate"]};
        color: {c["disabled"]};
        border-color: {c["border"]};
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit,
    QTextEdit, QListWidget, QTreeWidget, QTableWidget, QKeySequenceEdit {{
        background-color: {c["raised"]};
        color: {c["text"]};
        border: 1px solid {c["border"]};
        border-radius: 0px;
        selection-background-color: {c["accent"]};
        selection-color: {c["window"]};
    }}
    QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover,
    QPlainTextEdit:hover, QTextEdit:hover, QListWidget:hover,
    QTreeWidget:hover, QTableWidget:hover {{ border-color: {c["text"]}; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QPlainTextEdit:focus, QTextEdit:focus, QListWidget:focus,
    QTreeWidget:focus, QTableWidget:focus, QKeySequenceEdit:focus {{
        border: 2px solid {c["accent"]};
    }}
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled,
    QDoubleSpinBox:disabled, QPlainTextEdit:disabled, QTextEdit:disabled,
    QListWidget:disabled, QTreeWidget:disabled, QTableWidget:disabled {{
        background-color: {c["alternate"]}; color: {c["disabled"]};
    }}
    QComboBox::drop-down {{
        background: {c["control"]};
        border-left: 1px solid {c["border"]};
        border-radius: 0px;
    }}
    QComboBox QAbstractItemView {{
        background: {c["raised"]}; color: {c["text"]};
        border: 1px solid {c["border"]};
        selection-background-color: {c["accent"]};
        selection-color: {c["window"]};
    }}
    QScrollBar:vertical {{
        width: 13px; margin: 0; background: {c["alternate"]};
        border: 1px solid {c["border"]}; border-radius: 0px;
    }}
    QScrollBar:horizontal {{
        height: 13px; margin: 0; background: {c["alternate"]};
        border: 1px solid {c["border"]}; border-radius: 0px;
    }}
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
        min-width: 18px; min-height: 18px;
        background: {c["text"]};
        border: 1px solid {c["border"]};
        border-radius: 0px;
    }}
    QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
        background: {c["accent"]};
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        width: 0px; height: 0px; background: transparent; border: none;
    }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: {c["alternate"]}; }}
    QMenuBar, QMenu {{
        background: {c["panel"]}; color: {c["text"]};
        border: 1px solid {c["border"]}; border-radius: 0px;
    }}
    QMenuBar::item:selected, QMenu::item:selected {{
        background: {c["accent"]}; color: {c["window"]};
    }}
    QMenu::item:disabled {{ color: {c["disabled"]}; background: {c["panel"]}; }}
    QToolTip {{
        background: {c["text"]}; color: {c["window"]};
        border: 1px solid {c["accent"]}; border-radius: 0px;
        padding: 3px;
    }}
    """


_STYLE_FAMILY_BUILDERS = {
    "fastgraph95": _fastgraph95_stylesheet,
    "fastgraph95_dark": _fastgraph95_dark_stylesheet,
    "hackerman95": _hackerman95_stylesheet,
    "dither": _dither_stylesheet,
}


def _stylesheet_body(
    c: dict[str, str],
    *,
    visual_tokens: ThemeTokens,
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
    geometry = visual_tokens.geometry
    typography = visual_tokens.typography
    segment_checked_text = visual_tokens.background
    return f"""
    QWidget {{ background-color: {c["window"]}; color: {c["text"]}; font-family: '{typography.ui_family}', 'Helvetica Neue', Arial, sans-serif; font-size: {typography.body_px}px; }}
    QMainWindow, QDialog {{ background-color: {c["window"]}; }}
    QMessageBox, QInputDialog, QFileDialog {{ background-color: {c["panel"]}; color: {c["text"]}; }}
    QMessageBox QLabel, QInputDialog QLabel, QFileDialog QLabel, QDialogButtonBox {{ background-color: transparent; color: {c["text"]}; border: none; }}
    QDialogButtonBox QPushButton {{ min-width: 72px; }}
    QLabel {{ background-color: transparent; }}
    QCheckBox, QRadioButton {{ background-color: transparent; }}
    QToolTip {{ background-color: {c["base"]}; color: {c["text"]}; border: 1px solid {c["border"]}; }}
    QWidget[surfaceLevel="viewport"] {{ background-color: {c["viewport"]}; border: 1px solid {c["border"]}; border-radius: {geometry.radius_surface}px; }}
    QWidget[surfaceLevel="panel"] {{ background-color: {c["panel"]}; border: 1px solid {c["border"]}; border-radius: {geometry.radius_surface}px; }}
    QWidget[surfaceLevel="raised"] {{ background-color: {c["raised"]}; border: 1px solid {c["border"]}; border-radius: {geometry.radius_surface}px; }}
    QWidget[layoutRole="transparent"] {{ background-color: transparent; border: none; }}
    QLabel[typographyRole="caption"] {{ font-size: {typography.caption_px}px; color: {c["muted"]}; }}
    QLabel[typographyRole="section"] {{ font-family: '{typography.heading_family}', '{typography.ui_family}', sans-serif; font-size: {typography.section_px}px; font-weight: 600; }}
    QLabel[typographyRole="screen"] {{ font-family: '{typography.heading_family}', '{typography.ui_family}', sans-serif; font-size: {typography.screen_px}px; font-weight: 700; }}
    QLabel[typographyRole="technical"], QLineEdit[typographyRole="technical"] {{ font-family: '{typography.technical_family}', monospace; font-size: {typography.body_px}px; }}
    QPushButton {{ background-color: {c["control"]}; color: {c["text"]}; border: 1px solid {c["border"]}; border-radius: {geometry.radius_button}px; padding: 6px 14px; min-height: 28px; }}
    QPushButton:hover {{ background-color: {c["control_hover"]}; }}
    QPushButton:pressed {{ background-color: {c["alternate"]}; padding-top: 7px; }}
    QPushButton:disabled {{ color: {c["disabled"]}; border-color: {c["border"]}; background-color: {c["alternate"]}; }}
    QToolButton[menuButton="true"] {{ background-color: {c["control"]}; color: {c["text"]}; border: 1px solid {c["border"]}; border-radius: {geometry.radius_button}px; padding: 6px 14px; min-height: 28px; }}
    QToolButton[menuButton="true"]:hover {{ background-color: {c["control_hover"]}; }}
    QToolButton[menuButton="true"]:disabled {{ color: {c["disabled"]}; border-color: {c["border"]}; background-color: {c["alternate"]}; }}
    QToolButton[menuButton="true"]::menu-indicator {{ image: none; width: 0px; }}
    QPushButton[measureSegment="true"] {{
        background-color: {c["control"]};
        color: {c["muted"]};
        border: 1px solid {c["border"]};
        border-radius: 0px;
        padding: 5px 12px;
        min-height: 24px;
    }}
    QPushButton[measureSegment="true"][segmentPosition="first"] {{
        border-top-left-radius: {geometry.radius_button}px;
        border-bottom-left-radius: {geometry.radius_button}px;
        border-right: 0px;
    }}
    QPushButton[measureSegment="true"][segmentPosition="last"] {{
        border-top-right-radius: {geometry.radius_button}px;
        border-bottom-right-radius: {geometry.radius_button}px;
    }}
    QPushButton[measureSegment="true"]:hover {{
        background-color: {c["control_hover"]};
        color: {c["text"]};
        border-color: {c["accent"]};
    }}
    QPushButton[measureSegment="true"]:focus {{
        background-color: {c["control"]};
        color: {c["text"]};
        border: 2px solid {c["accent"]};
    }}
    QPushButton[measureSegment="true"]:checked {{
        background-color: {c["accent"]};
        color: {segment_checked_text};
        border: 1px solid {c["accent"]};
    }}
    QPushButton[measureSegment="true"]:checked:hover {{
        border: 2px solid {c["text"]};
    }}
    QPushButton[measureSegment="true"]:checked:focus {{
        border: 2px solid {c["text"]};
    }}
    QPushButton[measureSegment="true"]:disabled {{
        background-color: {c["alternate"]};
        color: {c["disabled"]};
        border-color: {c["border"]};
    }}
    QPushButton[measureSegment="true"]:checked:disabled {{
        background-color: {c["selected"]};
        color: {c["text"]};
        border-color: {c["disabled"]};
    }}
    QWidget#tab_header_controls QPushButton {{ min-height: 20px; max-height: 26px; padding: 2px 10px; border-radius: 12px; }}
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
    QPushButton#btn_export {{ background-color: {amber_bg}; color: {amber_text}; font-weight: 700; }}
    QPushButton#btn_export:hover {{ background-color: {amber_hover}; }}
    QToolButton#section_toggle {{ background-color: {c["raised"]}; color: {amber_text}; border: 1px solid {c["border"]}; border-radius: {geometry.radius_field}px; padding: 7px 10px; min-height: 24px; font-weight: 700; text-align: left; }}
    QToolButton#section_toggle:hover {{ background-color: {c["control_hover"]}; border-color: {amber_text}; }}
    QToolButton#section_toggle:checked {{ border-left: 3px solid {amber_text}; }}
    QPushButton#btn_upload, QPushButton#btn_update {{ background-color: {keep_bg}; color: {keep_text}; font-weight: 600; }}
    QPushButton#btn_upload:hover, QPushButton#btn_update:hover {{ background-color: {keep_hover}; }}
    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit {{ background-color: {c["raised"]}; color: {c["text"]}; border: 1px solid {c["border"]}; border-radius: {geometry.radius_field}px; padding: 3px 8px; min-height: 24px; selection-background-color: {c["selected"]}; }}
    QSpinBox[modernSpinBox="true"], QDoubleSpinBox[modernSpinBox="true"] {{ padding-right: 30px; }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView {{ background-color: {c["base"]}; color: {c["text"]}; selection-background-color: {c["selected"]}; }}
    QSpinBox#queue_count_spin {{ font-size: 15px; font-weight: 700; color: {c["accent"]}; padding-right: 34px; }}
    QLabel#label_channel_active {{ color: {c["accent"]}; font-weight: bold; font-size: 14px; }}
    QLabel[tone="muted"] {{ color: {c["muted"]}; }}
    QLabel[tone="error"] {{ color: {fail_text}; }}
    QLabel[tone="accent"] {{ color: {c["accent"]}; font-weight: 600; }}
    QLabel[tone="warning"] {{ color: {amber_text}; }}
    QFrame#diagnostic_box {{ border: 1px solid {c["border"]}; border-radius: {geometry.radius_field}px; background-color: {c["raised"]}; }}
    QLabel#diagnostic_details {{ color: {c["muted"]}; background-color: {c["raised"]}; border: 1px solid {c["border"]}; border-radius: {geometry.radius_field}px; padding: 8px; font-family: '{typography.technical_family}', monospace; }}
    QGroupBox {{ border: 1px solid {c["border"]}; border-radius: {geometry.radius_surface}px; margin-top: 14px; padding-top: 10px; background-color: {group_bg}; }}
    QGroupBox::title {{ color: {c["muted"]}; subcontrol-origin: margin; left: 12px; padding: 0 5px; font-family: '{typography.heading_family}', '{typography.ui_family}', sans-serif; font-size: {typography.section_px}px; font-weight: 600; }}
    QScrollArea {{ background-color: transparent; border: none; }}
    QScrollBar:vertical {{ width: 12px; margin: 2px; background: {c["alternate"]}; border-radius: 6px; }}
    QScrollBar:horizontal {{ height: 12px; margin: 2px; background: {c["alternate"]}; border-radius: 6px; }}
    QScrollBar::handle:vertical {{ min-height: 32px; background: {c["border"]}; border-radius: 4px; }}
    QScrollBar::handle:horizontal {{ min-width: 32px; background: {c["border"]}; border-radius: 4px; }}
    QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {c["accent"]}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; background: transparent; border: none; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QTabWidget::pane {{ background: {c["viewport"]}; border: 1px solid {c["border"]}; border-radius: {geometry.radius_surface}px; top: -1px; }}
    QTabBar::tab {{ background: {tab_bg}; padding: 7px 16px; margin-right: 3px; border: 1px solid {c["border"]}; border-bottom: 2px solid {c["border"]}; border-top-left-radius: {geometry.radius_tab}px; border-top-right-radius: {geometry.radius_tab}px; color: {c["text"]}; }}
    QTabBar::tab:hover {{ background: {c["control_hover"]}; }}
    QTabBar::tab:selected {{ background: {tab_selected}; color: {c["accent"]}; border-bottom: 2px solid {c["accent"]}; }}
    QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {c["border"]}; border-radius: {geometry.radius_micro}px; background: {c["base"]}; }}
    QCheckBox::indicator:checked {{ background: #3a7abf; }}
    QStatusBar {{ border-top: 1px solid {c["border"]}; }}
    """


def brand_theme_colors() -> dict[str, str]:
    """Color set for brand mode (see BRAND Brand Guide)."""
    return _color_dict(
        BRAND_TOKENS,
        meter_bg="#141414",
        meter_mark="#555555",
        meter_peak=brand_brand.WHITE,
    )


def brand_application_stylesheet() -> str:
    c = brand_theme_colors()
    base = _stylesheet_body(
        c,
        visual_tokens=BRAND_TOKENS,
        status=_status_accent_colors(BRAND_TOKENS),
        tab_bg=c["alternate"],
        tab_selected=c["base"],
        group_bg=c["panel"],
    )
    return (
        base
        + f"""
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QPlainTextEdit:focus, QListWidget:focus {{
        border: 2px solid {brand_brand.GRADIENT_ORANGE};
    }}
    QTabBar::tab:selected {{
        border-bottom: 3px solid {brand_brand.GRADIENT_ORANGE};
        color: {brand_brand.OFF_WHITE};
    }}
    QPushButton[measureSegment="true"] {{
        background-color: {c["control"]};
        color: {c["muted"]};
        border-color: {c["border"]};
        font-family: 'Heading', 'Inter', sans-serif;
    }}
    QPushButton[measureSegment="true"]:hover {{
        background-color: {c["control_hover"]};
        color: {brand_brand.OFF_WHITE};
        border-color: {brand_brand.GRADIENT_ORANGE};
    }}
    QPushButton[measureSegment="true"]:focus {{
        background-color: {c["control"]};
        color: {brand_brand.OFF_WHITE};
        border: 2px solid {brand_brand.GRADIENT_ORANGE};
    }}
    QPushButton[measureSegment="true"]:checked {{
        background-color: {brand_brand.GRADIENT_ORANGE};
        color: {BRAND_TOKENS.background};
        border-color: {brand_brand.GRADIENT_ORANGE};
    }}
    QPushButton[measureSegment="true"]:checked:hover,
    QPushButton[measureSegment="true"]:checked:focus {{
        border: 2px solid {brand_brand.OFF_WHITE};
    }}
    QPushButton[measureSegment="true"]:disabled {{
        background-color: {c["alternate"]};
        color: {c["disabled"]};
        border-color: {c["border"]};
    }}
    QPushButton[measureSegment="true"]:checked:disabled {{
        background-color: {c["selected"]};
        color: {c["muted"]};
        border-color: {c["disabled"]};
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
        background-color: {BRAND_TOKENS.panel};
        border: 1px solid #707070;
        border-radius: {BRAND_TOKENS.geometry.radius_surface}px;
    }}
    """
    )


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
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(c["disabled"])
    )
    return palette


class ThemeController(QObject):
    theme_changed = pyqtSignal(str)
    brand_mode_changed = pyqtSignal(bool)

    def __init__(
        self,
        app: QApplication,
        settings: SettingsManager,
        parent: QObject | None = None,
    ) -> None:
        # The controller is deliberately not parented to the application. The
        # owner (main.py or a MainWindow) keeps it alive; parenting it to the
        # QApplication made every controller permanent for the process
        # lifetime, which mattered for tests that build many windows.
        super().__init__(parent)
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
            mode = "brand"
            palette = _brand_palette()
            stylesheet = brand_application_stylesheet()
            flat_controls = False
        else:
            mode = self._theme
            palette = _palette(self._theme)
            stylesheet = application_stylesheet(self._theme)
            flat_controls = tokens_for(self._theme).flat_controls

        # Qt re-polishes every live widget on setStyleSheet even when the
        # sheet is unchanged, so skip the whole application when nothing
        # differs from what is already applied.
        if (
            self._app.property("fastgraphVisualMode") == mode
            and self._app.styleSheet() == stylesheet
        ):
            return

        self._app.setProperty("fastgraphVisualMode", mode)
        self._app.setPalette(palette)
        self._app.setStyleSheet(stylesheet)
        configure_dither_typography(self._app, flat_controls)
