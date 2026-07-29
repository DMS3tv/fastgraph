# FastGraph UI Style Guide

## Purpose

Use this guide for all FastGraph interface work. Keep graph data, saved exports,
measurement behavior, and audio behavior separate from the interface style.

## Typography

| Use | Standard mode | brand mode | Size |
|---|---|---|---:|
| Caption | Inter Regular | Inter Regular | 11 px |
| Body and controls | Inter Regular | Inter Regular | 13 px |
| Section heading | Inter Semibold | Heading Semibold | 15 px |
| Screen heading | Inter Bold | Heading ExtraBold | 20 px |
| Technical value | Inconsolata Regular | Inconsolata Regular | 13 px |

Use the existing font fallback check when a Brand font is not installed. Do not
silently replace a missing Brand font.

## Geometry and Spacing

- Use 4 px for checkboxes, color swatches, and other small shapes.
- Use 8 px for text fields and the top corners of tabs.
- Use 12 px for buttons, panels, and viewport frames.
- Use a full pill only for toggles, status controls, and compact badges.
- Use the spacing scale 4, 8, 12, 16, and 24 px.
- Use a 1 px normal border and a 2 px keyboard-focus border.

## Surface Levels

Use the following levels from back to front:

1. `background`: The application window.
2. `viewport`: Graphs, previews, tables, and other content areas.
3. `panel`: Control columns and grouped sections.
4. `raised`: Inputs, selected tabs, and emphasized containers.
5. `control`: Buttons and editable controls.

Use color and a clear border to separate large surfaces. Do not add a large
shadow to a panel or viewport.

## Buttons

All normal user actions use `ModernButton`.

| Role | Use |
|---|---|
| `default` | Normal actions |
| `primary` | The main action in a section |
| `positive` | Keep, accept, upload, or update |
| `warning` | Cancel or caution actions |
| `danger` | Remove, clear, fail, or destructive actions |
| `ghost` | Low-priority toolbar actions |
| `compact` | Short tab-header and status-bar actions |
| `swatch` | A color-only control |

A normal button has a shallow vertical gradient, a dark lower edge, and a soft
shadow. Hover adds the current theme accent as an inner light and a soft outer
glow. Press moves the surface down by 1 px and reduces the shadow.

Use cyan light in standard FastGraph. Use orange light in Brand mode. Use red
light for destructive actions. Disabled buttons have no glow or motion.

## Tabs

Use rounded top corners and a small gap between tabs. The selected tab is one
surface level above the others and has a 2 px accent edge. Use cyan in standard
mode and orange in Brand mode.

## brand

Use these primary brand values:

- Background: `#232323`
- Off-white: `#F2F1F1`
- White: `#FFFFFF`
- Orange: `#7A7A7A`
- Red: `#6E6E6E`
- Magenta: `#626262`

Use `#1C1C1C`, `#2A2A2A`, `#303030`, and `#333333` only as neutral surface
steps. Reserve the orange-red-magenta gradient for key Brand accents and primary
actions. Do not apply the gradient to every control.

## Review Checklist

- Check dark, light, and Brand modes.
- Check keyboard focus and disabled controls.
- Check 1280 x 700 and 1800 x 1100 windows.
- Confirm that glow and shadows do not clip.
- Confirm that tab shapes and control radii are consistent.
- Confirm that graph and export output did not change.
