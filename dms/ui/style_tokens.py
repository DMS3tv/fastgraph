"""Shared visual design tokens for the FastGraph interface."""

from __future__ import annotations

from dataclasses import dataclass

from dms import brand_brand


@dataclass(frozen=True)
class TypographyTokens:
    ui_family: str
    heading_family: str
    technical_family: str
    caption_px: int = 11
    body_px: int = 13
    section_px: int = 15
    screen_px: int = 20


@dataclass(frozen=True)
class GeometryTokens:
    radius_micro: int = 4
    radius_field: int = 8
    radius_tab: int = 8
    radius_button: int = 12
    radius_surface: int = 12
    spacing_xs: int = 4
    spacing_sm: int = 8
    spacing_md: int = 12
    spacing_lg: int = 16
    spacing_xl: int = 24
    border_px: int = 1
    focus_border_px: int = 2
    button_height: int = 32
    compact_button_height: int = 26
    primary_button_height: int = 36


@dataclass(frozen=True)
class MotionTokens:
    hover_in_ms: int = 160
    hover_out_ms: int = 180
    press_ms: int = 90
    hover_tint_alpha: float = 0.18
    focus_glow_strength: float = 0.60
    hover_glow_alpha: int = 71
    rest_shadow_alpha: int = 140
    rest_shadow_blur: float = 8.0
    hover_shadow_blur: float = 14.0


@dataclass(frozen=True)
class ThemeTokens:
    name: str
    background: str
    viewport: str
    panel: str
    raised: str
    control: str
    control_hover: str
    alternate: str
    text: str
    muted: str
    disabled: str
    border: str
    selected: str
    accent: str
    focus: str
    shadow: str
    danger: str
    positive: str
    warning: str
    plot_bg: str
    plot_fg: str
    plot_grid: str
    typography: TypographyTokens
    geometry: GeometryTokens = GeometryTokens()
    motion: MotionTokens = MotionTokens()


STANDARD_TYPOGRAPHY = TypographyTokens(
    ui_family="Inter",
    heading_family="Inter",
    technical_family="Inconsolata",
)

BRAND_TYPOGRAPHY = TypographyTokens(
    ui_family="Inter",
    heading_family="Heading",
    technical_family="Inconsolata",
)


DARK_TOKENS = ThemeTokens(
    name="dark",
    background="#14171C",
    viewport="#1A1A1A",
    panel="#20262F",
    raised="#242B36",
    control="#2A303B",
    control_hover="#394356",
    alternate="#20262F",
    text="#E3E7EE",
    muted="#91A2BA",
    disabled="#6E7785",
    border="#455064",
    selected="#38536F",
    accent="#66CCFF",
    focus="#66CCFF",
    shadow="#000000",
    danger="#F0A0A0",
    positive="#9DEAB5",
    warning="#FFDCA1",
    plot_bg="#1A1A1A",
    plot_fg="#AAB0B9",
    plot_grid="#555D68",
    typography=STANDARD_TYPOGRAPHY,
)

LIGHT_TOKENS = ThemeTokens(
    name="light",
    background="#F3F5F8",
    viewport="#FFFFFF",
    panel="#F8FAFC",
    raised="#FFFFFF",
    control="#E8ECF1",
    control_hover="#DCE3EB",
    alternate="#E9EDF2",
    text="#20252D",
    muted="#5F6977",
    disabled="#99A1AC",
    border="#B9C1CC",
    selected="#CFE4F6",
    accent="#176EA6",
    focus="#176EA6",
    shadow="#536174",
    danger="#8A2525",
    positive="#176B37",
    warning="#764D00",
    plot_bg="#FAFBFC",
    plot_fg="#4F5967",
    plot_grid="#AEB7C2",
    typography=STANDARD_TYPOGRAPHY,
)

BRAND_TOKENS = ThemeTokens(
    name="brand",
    background=brand_brand.BACKGROUND,
    viewport="#1C1C1C",
    panel=brand_brand.SURFACE,
    raised="#303030",
    control="#333333",
    control_hover="#3A332D",
    alternate="#1C1C1C",
    text=brand_brand.OFF_WHITE,
    muted="#A8A8A8",
    disabled="#6B6B6B",
    border="#5C5C5C",
    selected="#5A3018",
    accent=brand_brand.GRADIENT_ORANGE,
    focus=brand_brand.GRADIENT_ORANGE,
    shadow="#000000",
    danger=brand_brand.GRADIENT_RED,
    positive="#303030",
    warning=brand_brand.GRADIENT_ORANGE,
    plot_bg=brand_brand.BACKGROUND,
    plot_fg=brand_brand.OFF_WHITE,
    plot_grid="#4A4A4A",
    typography=BRAND_TYPOGRAPHY,
)


def tokens_for(theme: str = "dark", *, brand_mode: bool = False) -> ThemeTokens:
    """Return the complete token set for one application mode."""
    if brand_mode:
        return BRAND_TOKENS
    return LIGHT_TOKENS if str(theme).strip().lower() == "light" else DARK_TOKENS


def mode_tokens(mode: object) -> ThemeTokens:
    """Return tokens for the mode string stored on QApplication."""
    normalized = str(mode or "dark").strip().lower()
    if normalized == "brand":
        return BRAND_TOKENS
    return tokens_for(normalized)
