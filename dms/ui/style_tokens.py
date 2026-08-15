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
    classic_controls: bool = False
    dark_bevel: bool = False
    retro_graph: bool = False
    terminal_chrome: bool = False
    trace_palette: tuple[str, ...] = ()


@dataclass(frozen=True)
class ThemeDefinition:
    """One selectable theme and the style renderer that it uses."""

    key: str
    label: str
    tokens: ThemeTokens
    style_family: str = "standard"


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

FASTGRAPH_95_TYPOGRAPHY = TypographyTokens(
    ui_family="Tahoma",
    heading_family="MS Sans Serif",
    technical_family="Fixedsys",
    caption_px=11,
    body_px=12,
    section_px=13,
    screen_px=18,
)

HACKERMAN_95_TYPOGRAPHY = TypographyTokens(
    ui_family="Monaco",
    heading_family="Monaco",
    technical_family="Monaco",
    caption_px=11,
    body_px=12,
    section_px=13,
    screen_px=18,
)

FASTGRAPH_95_GEOMETRY = GeometryTokens(
    radius_micro=0,
    radius_field=0,
    radius_tab=0,
    radius_button=0,
    radius_surface=0,
    border_px=2,
    focus_border_px=1,
    button_height=28,
    compact_button_height=24,
    primary_button_height=30,
)

FASTGRAPH_95_MOTION = MotionTokens(
    hover_in_ms=0,
    hover_out_ms=0,
    press_ms=0,
    hover_tint_alpha=0.0,
    focus_glow_strength=0.0,
    hover_glow_alpha=0,
    rest_shadow_alpha=0,
    rest_shadow_blur=0.0,
    hover_shadow_blur=0.0,
)


DARK_TOKENS = ThemeTokens(
    name="dark",
    background="#07090C",
    viewport="#0C1015",
    panel="#121820",
    raised="#18212B",
    control="#1E2833",
    control_hover="#293847",
    alternate="#10161D",
    text="#E3E7EE",
    muted="#91A2BA",
    disabled="#6E7785",
    border="#334152",
    selected="#203B50",
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

FASTGRAPH_95_TOKENS = ThemeTokens(
    name="fastgraph95",
    background="#008080",
    viewport="#C0C0C0",
    panel="#C0C0C0",
    raised="#FFFFFF",
    control="#C0C0C0",
    control_hover="#D4D4D4",
    alternate="#A0A0A0",
    text="#000000",
    muted="#404040",
    disabled="#808080",
    border="#000000",
    selected="#000080",
    accent="#000080",
    focus="#000080",
    shadow="#000000",
    danger="#800000",
    positive="#008000",
    warning="#808000",
    plot_bg="#FFFFFF",
    plot_fg="#000000",
    plot_grid="#808080",
    typography=FASTGRAPH_95_TYPOGRAPHY,
    geometry=FASTGRAPH_95_GEOMETRY,
    motion=FASTGRAPH_95_MOTION,
    classic_controls=True,
    retro_graph=True,
)

FASTGRAPH_95_DARK_TOKENS = ThemeTokens(
    name="fastgraph95_dark",
    background="#242424",
    viewport="#353535",
    panel="#3C3C3C",
    raised="#292929",
    control="#444444",
    control_hover="#535353",
    alternate="#2D2D2D",
    text="#E2E2E2",
    muted="#B0B0B0",
    disabled="#777777",
    border="#0A0A0A",
    selected="#315B85",
    accent="#78AEDD",
    focus="#78AEDD",
    shadow="#000000",
    danger="#E09191",
    positive="#8CCB9A",
    warning="#D6BD77",
    plot_bg="#202020",
    plot_fg="#E2E2E2",
    plot_grid="#666666",
    typography=FASTGRAPH_95_TYPOGRAPHY,
    geometry=FASTGRAPH_95_GEOMETRY,
    motion=FASTGRAPH_95_MOTION,
    classic_controls=True,
    dark_bevel=True,
    retro_graph=True,
)

HACKERMAN_95_TOKENS = ThemeTokens(
    name="hackerman95",
    background="#111411",
    viewport="#171B17",
    panel="#1D211D",
    raised="#121512",
    control="#282D28",
    control_hover="#333A33",
    alternate="#0F120F",
    text="#D3D9D3",
    muted="#899589",
    disabled="#5C665C",
    border="#465046",
    selected="#234B2A",
    accent="#39FF14",
    focus="#39FF14",
    shadow="#000000",
    danger="#FF3B6B",
    positive="#39FF14",
    warning="#F5E642",
    plot_bg="#010301",
    plot_fg="#39FF14",
    plot_grid="#0D5E1C",
    typography=HACKERMAN_95_TYPOGRAPHY,
    geometry=FASTGRAPH_95_GEOMETRY,
    motion=FASTGRAPH_95_MOTION,
    classic_controls=True,
    dark_bevel=True,
    retro_graph=True,
    terminal_chrome=True,
    trace_palette=(
        "#39FF14",
        "#00E5FF",
        "#FF3BF4",
        "#F5E642",
        "#FF6B35",
        "#9B7BFF",
        "#00FF9C",
        "#FF4F79",
    ),
)

BRAND_TOKENS = ThemeTokens(
    name="brand",
    background=DARK_TOKENS.background,
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


THEME_DEFINITIONS: tuple[ThemeDefinition, ...] = (
    ThemeDefinition("dark", "Default dark", DARK_TOKENS),
    ThemeDefinition("light", "Default light", LIGHT_TOKENS),
    ThemeDefinition("fastgraph95", "FastGraph 95", FASTGRAPH_95_TOKENS, "fastgraph95"),
    ThemeDefinition(
        "fastgraph95_dark",
        "FastGraph 95 Dark",
        FASTGRAPH_95_DARK_TOKENS,
        "fastgraph95_dark",
    ),
    ThemeDefinition("hackerman95", "Hackerman 95", HACKERMAN_95_TOKENS, "hackerman95"),
)

_THEMES_BY_KEY = {definition.key: definition for definition in THEME_DEFINITIONS}


def theme_definitions() -> tuple[ThemeDefinition, ...]:
    """Return selectable themes in their Settings order."""
    return THEME_DEFINITIONS


def theme_definition(theme: object) -> ThemeDefinition:
    """Return a registered theme, or the default dark theme."""
    key = str(theme or "").strip().lower()
    return _THEMES_BY_KEY.get(key, _THEMES_BY_KEY["dark"])


def tokens_for(theme: str = "dark", *, brand_mode: bool = False) -> ThemeTokens:
    """Return the complete token set for one application mode."""
    if brand_mode:
        return BRAND_TOKENS
    return theme_definition(theme).tokens


def mode_tokens(mode: object) -> ThemeTokens:
    """Return tokens for the mode string stored on QApplication."""
    normalized = str(mode or "dark").strip().lower()
    if normalized == "brand":
        return BRAND_TOKENS
    return tokens_for(normalized)
