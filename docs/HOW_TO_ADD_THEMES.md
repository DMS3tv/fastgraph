# How to Add a Theme to FastGraph

FastGraph keeps theme definitions in one registry. A basic theme needs one
token set and one registry entry. Settings reads the registry and adds the
theme choice automatically.

Use this guide for application themes. brand mode is a separate branded
mode. Do not add it to the selectable theme registry.

## Theme Files

| File | Purpose |
|---|---|
| `dms/ui/style_tokens.py` | Theme tokens, flags, and the selectable theme registry |
| `dms/theme.py` | Theme normalization, shared Qt stylesheet, trace colors, and optional style-family builders |
| `dms/graph_display.py` | Display-only stepped graph behavior |
| `dms/curator/export_image.py` | Theme-aware Curator image exports |
| `tools/render_ui_style_gallery.py` | Offscreen component images for visual review |
| `tests/test_theme.py` | Theme selection, persistence, and stylesheet tests |
| `tests/test_ui_style_system.py` | Token, widget, and renderer tests |
| `tests/test_graph_display.py` | Display-only graph conversion tests |
| `tests/test_curator_ui_export.py` | Curator export tests |

## 1. Choose a Key and Label

Use a stable lowercase key. Use underscores between words. The key is saved in
the user settings file, so do not rename it after release.

Example:

```python
MY_THEME = "my_theme"
```

The label is the text that the user sees in Settings:

```python
ThemeDefinition("my_theme", "My Theme", MY_THEME_TOKENS)
```

Adding a constant to `dms/theme.py` is optional, but it gives tests and other
modules one clear name to import.

## 2. Define the Tokens

Add a `ThemeTokens` value in `dms/ui/style_tokens.py`. Start from an existing
theme that has similar geometry and behavior.

```python
MY_THEME_TOKENS = ThemeTokens(
    name="my_theme",
    background="#101418",
    viewport="#151B21",
    panel="#1B232B",
    raised="#222C36",
    control="#2A3642",
    control_hover="#344453",
    alternate="#12181E",
    text="#E7EDF3",
    muted="#98A7B5",
    disabled="#66727D",
    border="#465564",
    selected="#27465C",
    accent="#5CC8FF",
    focus="#5CC8FF",
    shadow="#000000",
    danger="#FF8E96",
    positive="#91E6AD",
    warning="#FFD37A",
    plot_bg="#090C0F",
    plot_fg="#D7E0E8",
    plot_grid="#47515C",
    typography=STANDARD_TYPOGRAPHY,
)
```

Use the tokens as follows:

- `background`: main application background.
- `viewport`: large content area behind a tool or graph.
- `panel`: grouped controls and side panels.
- `raised`: input fields and raised surfaces.
- `control` and `control_hover`: normal and hover control colors.
- `alternate`: recessed or alternate surface.
- `text`, `muted`, and `disabled`: primary and secondary text states.
- `border`, `selected`, `accent`, and `focus`: edges and active states.
- `danger`, `positive`, and `warning`: action meanings. Keep these meanings
  consistent in every theme.
- `plot_bg`, `plot_fg`, and `plot_grid`: graph field, axis text, and grid.
- `typography`, `geometry`, and `motion`: shared font, shape, spacing, and
  animation settings.

Use valid six-digit hexadecimal colors. Check text, controls, graph axes, and
traces against their actual backgrounds.

## 3. Register the Theme

Add the definition to `THEME_DEFINITIONS` in `dms/ui/style_tokens.py`:

```python
THEME_DEFINITIONS: tuple[ThemeDefinition, ...] = (
    ThemeDefinition("dark", "Default dark", DARK_TOKENS),
    ThemeDefinition("light", "Default light", LIGHT_TOKENS),
    # Keep the existing definitions here.
    ThemeDefinition("my_theme", "My Theme", MY_THEME_TOKENS),
)
```

The registry order is the Settings order. This entry also makes the key valid
for `normalize_theme()`. No separate Settings dialog change is necessary.

Do not add duplicate keys. An unknown saved key falls back to Default dark.

## 4. Select Optional Theme Behavior

`ThemeTokens` has optional behavior flags:

- `classic_controls=True` uses square custom buttons, switches, meters,
  surfaces, and theme-aware classic Curator exports.
- `dark_bevel=True` changes classic edges for a dark surface set.
- `retro_graph=True` draws a fine stepped display path. It does not change the
  stored measurement or numerical export data.
- `terminal_chrome=True` uses the terminal-style classic control details.
- `trace_palette=(...)` sets the ordered colors for new graph layers. An empty
  tuple uses the shared FastGraph trace palette.

Use flags only when the complete theme needs that behavior. Do not check a
theme key in individual widgets when a token or reusable flag can describe the
same rule.

FastGraph calls `ensure_graph_color()` when it draws a trace on a graph. This
function corrects poor display contrast. It does not replace the need for a
clear and distinct trace palette. Test every palette color on `plot_bg` and on
the Curator export background.

A theme change must not alter saved layer colors, stored measurements,
smoothing data, or numerical exports.

## 5. Add a Custom Style Family Only When Necessary

Most themes should use the standard style family. In that case, omit the last
`ThemeDefinition` argument.

Use a custom style family only when token changes cannot produce the required
control shapes or states:

```python
ThemeDefinition(
    "my_theme",
    "My Theme",
    MY_THEME_TOKENS,
    "my_theme",
)
```

Add the matching builder to `dms/theme.py`:

```python
def _my_theme_stylesheet(colors: dict[str, str]) -> str:
    return f"""
    QTabBar::tab:selected {{
        color: {colors['accent']};
    }}
    """


_STYLE_FAMILY_BUILDERS = {
    # Existing builders...
    "my_theme": _my_theme_stylesheet,
}
```

The shared stylesheet always loads first. A style-family builder returns only
the required overrides. Keep selectors narrow. Check dialogs, menus, tooltips,
tabs, scroll bars, disabled controls, focus, hover, and pressed states.

## 6. Check Graphs and Curator Exports

Confirm these points in Measure, R&D, and Curator:

- Axis text and grid lines are visible on `plot_bg`.
- The first trace is clear and each added trace remains distinct.
- Bounds, variation bands, reference lines, and the zero line remain visible.
- The level meter and its text remain clear.
- A Curator image export uses the intended theme surfaces and trace colors.
- Curator legend boxes show complete layer names. They must not use an
  ellipsis.
- Graph styling changes only the displayed path. It must not change source
  samples or text exports.

If the theme needs a special export layout, use token flags in
`dms/curator/export_image.py`. Keep label and footer areas inside the output
canvas.

## 7. Add Tests

At minimum, add or update tests for:

1. The theme key and label in `THEME_DEFINITIONS`.
2. `normalize_theme()` and `tokens_for()`.
3. Important token values and optional flags.
4. The generated stylesheet when the theme has a style family.
5. Settings selection and persistence.
6. Graph contrast and the trace palette.
7. Curator export behavior when the theme changes the export layout.

Run the focused theme tests:

```bash
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_theme.py \
  tests/test_ui_style_system.py \
  tests/test_graph_display.py \
  tests/test_curator_ui_export.py
```

Then run the full suite:

```bash
PYTHONPATH=. .venv/bin/pytest -q
```

Also run these static checks:

```bash
git diff --check
PYTHONPATH=. .venv/bin/python -m compileall -q dms tests tools
```

## 8. Render the Component Gallery

Add the theme key to the mode list in `tools/render_ui_style_gallery.py`. Then
render all theme galleries:

```bash
PYTHONPATH=. .venv/bin/python tools/render_ui_style_gallery.py /tmp/fastgraph-theme-gallery
```

Review the new PNG at normal size and at a small window size. The gallery
checks the main surface levels, tabs, fields, buttons, scroll area, typography,
hover state, pressed state, disabled state, and action colors.

The gallery is not a complete application check. Open FastGraph and inspect
Settings, Measure, R&D, Curator, Automation, dialogs, menus, and image exports
before release.

## Completion Checklist

- [ ] The key is stable, lowercase, and unique.
- [ ] The token set defines every required color.
- [ ] `THEME_DEFINITIONS` contains the key and user label.
- [ ] Settings shows and saves the theme.
- [ ] Text and controls have sufficient contrast.
- [ ] Graph traces and overlays remain visible.
- [ ] Added trace colors remain distinct.
- [ ] Curator preview and image export remain readable.
- [ ] Long Curator legend names remain complete.
- [ ] Theme changes do not change measurement data.
- [ ] Focused tests and the full test suite pass.
- [ ] The component gallery and the application pass visual review.
