"""Font selection and chrome typography for flat print themes."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication, QGroupBox, QTabBar, QWidget


HEADING_FAMILY = "DIN Condensed"
HEADING_FALLBACKS = (
    "DIN Condensed",
    "Oswald",
    "Archivo Narrow",
    "Arial Narrow",
    "Avenir Next Condensed",
    "Inter",
)

_heading_family_cache: str | None = None
_typography_filter: _DitherTypographyFilter | None = None


@dataclass(frozen=True)
class DitherFontStatus:
    heading_family: str
    missing_families: tuple[str, ...]

    @property
    def uses_fallback(self) -> bool:
        return bool(self.missing_families)


def _resolve_heading_family() -> str:
    global _heading_family_cache
    if _heading_family_cache is None:
        if QApplication.instance() is None:
            return HEADING_FAMILY
        families = set(QFontDatabase.families())
        _heading_family_cache = next(
            (family for family in HEADING_FALLBACKS if family in families),
            HEADING_FALLBACKS[-1],
        )
    return _heading_family_cache


def dither_font_status() -> DitherFontStatus:
    heading = _resolve_heading_family()
    missing = () if heading == HEADING_FAMILY else (HEADING_FAMILY,)
    return DitherFontStatus(heading_family=heading, missing_families=missing)


def reset_font_cache() -> None:
    """Reset the cached family choice after a font list change."""

    global _heading_family_cache
    _heading_family_cache = None


def dither_heading_font(base: QFont | None = None) -> QFont:
    """Return the condensed, uppercase font used for chrome labels."""

    font = QFont(base) if base is not None else QFont()
    font.setFamily(_resolve_heading_family())
    font.setWeight(QFont.Weight.Bold)
    font.setCapitalization(QFont.Capitalization.AllUppercase)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
    return font


def _is_chrome_widget(widget: QWidget) -> bool:
    return isinstance(widget, (QTabBar, QGroupBox)) or widget.__class__.__name__ == "ModernButton"


def _set_flat_font(widget: QWidget, enabled: bool) -> None:
    original_property = "_fastgraphOriginalChromeFont"
    original = widget.property(original_property)
    if enabled:
        if original is None:
            widget.setProperty(original_property, QFont(widget.font()))
        base = QFont(widget.font())
        font = dither_heading_font(base)
        if widget.__class__.__name__ == "ModernButton":
            # ModernButton resolves the heading family in its custom size and
            # paint paths. Keep the stylesheet family here so Qt gives startup
            # and runtime theme changes the same native size-hint input.
            font.setFamilies(base.families())
        widget.setFont(font)
    elif isinstance(original, QFont):
        widget.setFont(original)
        widget.setProperty(original_property, None)
    widget.updateGeometry()
    widget.update()


class _DitherTypographyFilter(QObject):
    """Apply the print font to chrome widgets that Qt creates later."""

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self.enabled = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            self.enabled
            and event.type() == QEvent.Type.Polish
            and isinstance(watched, QWidget)
            and _is_chrome_widget(watched)
        ):
            _set_flat_font(watched, True)
        return False


def configure_dither_typography(app: QApplication, enabled: bool) -> DitherFontStatus:
    """Apply or remove the Dither chrome font in the running application."""

    global _typography_filter
    if _typography_filter is None or _typography_filter.parent() is not app:
        _typography_filter = _DitherTypographyFilter(app)
        app.installEventFilter(_typography_filter)
    _typography_filter.enabled = bool(enabled)
    for widget in app.allWidgets():
        if _is_chrome_widget(widget):
            _set_flat_font(widget, bool(enabled))
    status = dither_font_status()
    app.setProperty("ditherFontStatus", status)
    return status
