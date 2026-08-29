# FastGraph UI Style Guide

## Purpose

Use this guide for all FastGraph interface work. Keep graph data, saved exports,
measurement behavior, and audio behavior separate from the interface style.

## Typography

| Use | Standard mode | FastGraph 95 mode | brand mode | Size |
|---|---|---|---|---:|
| Caption | Inter Regular | Tahoma | Inter Regular | 11 px |
| Body and controls | Inter Regular | Tahoma | Inter Regular | 13 px |
| Section heading | Inter Semibold | MS Sans Serif | Heading Semibold | 15 px |
| Screen heading | Inter Bold | MS Sans Serif | Heading ExtraBold | 20 px |
| Technical value | Inconsolata Regular | Fixedsys | Inconsolata Regular | 13 px |

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

The standard dark theme uses `#07090C` for the application background. Use
`#0C1015` for viewports, `#121820` for panels, `#18212B` for raised surfaces,
and `#1E2833` for controls. Keep each level dark. Use borders and small tonal
steps to show the surface order.

Use a 10 px visible radius on plot viewports. Layout-only wrappers must stay
transparent so they do not cover a rounded parent corner.

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

A normal button sits inside a thin dark recessed well. The surrounding rim has
a small brightness change so the control reads as part of the interface
surface. Do not put a drop shadow behind the control.

At rest, the button face is dark and a contained accent light rises from the
bottom center. Hover expands and brightens the light until it softly fills the
button. Press briefly flashes the inner light like a bulb. The light must stay
inside the recessed well.

Use cyan light in standard FastGraph. Use orange light in Brand mode. Use red
light for destructive actions. Disabled buttons have no glow or motion.

FastGraph 95 mode is an exception. Use a square two-step bevel for each button.
Do not use the inner light, hover glow, rounded well, or click flash in this
mode. Move the label by one pixel when the user presses the button.

The global Inputs button uses purple `#A970FF` in the standard dark theme. It
uses the normal mode accent in the standard light and brand themes.

## Segmented Controls

Use a segmented control for two or more named modes. Join the checkable buttons
with no gap. Fill the selected segment with the theme accent. Keep the other
segments muted and readable.

Use a 12 px radius on the two outer ends in modern themes. Use square ends in
FastGraph 95 and Dither themes. Show a clear hover state and keyboard-focus
border. Keep the selected segment clear when the control is disabled.

Size each segment from its full label and its current font metrics. Do not let a
layout make a segment narrower than its label.

## Input Level Meter

Use the shared visual tokens for the input meter in all modes. Draw the meter
as a recessed, rounded well. Do not use a separate BRAND paint path.

The active light starts at the signal origin and spreads across the well as the
level rises from -60 dBFS to 0 dBFS. Keep the strongest light at the origin and
use a soft falloff across the remaining distance. Change the full glow smoothly
from the mode accent to warning and danger colors as the level approaches
clipping. Do not split the track into fixed green, yellow, and red areas.

Interpolate the displayed level between audio updates. Use a faster rise and a
slower fall so signal changes remain smooth. Do not increase the audio sampling
rate for display motion. Use small scale marks and no peak or RMS tracking
line.

FastGraph 95 and FastGraph 95 Dark are exceptions. Use a square recessed track
with separate progress blocks. Use the selected blue for normal blocks, the
warning color near the top of the range, and the danger color at clipping. Do
not draw a glow in these two themes.

## Tabs

Use rounded top corners and a small gap between tabs. The selected tab is one
surface level above the others and has a 2 px accent edge. Use cyan in standard
mode and orange in Brand mode.

FastGraph 95 mode uses square tabs. Use white top and left edges. Use black right
edges. Join the selected tab to the gray tab pane.

## Theme Framework

For the complete procedure, examples, tests, and review checklist, see
[`HOW_TO_ADD_THEMES.md`](HOW_TO_ADD_THEMES.md).

Add a selectable theme in `dms/ui/style_tokens.py`. Add one `ThemeTokens`
value and one `ThemeDefinition` entry. Use a `style_family` only when the theme
needs control shapes that the standard stylesheet cannot provide. Register the
matching style builder in `dms/theme.py`.

Keep data colors and export colors separate from application chrome. A theme
can change graph backgrounds, axes, controls, and screen surfaces. A theme must
not change saved measurements or numerical export data. A theme can change the
presentation of a Curator image export when the user selects that theme.

FastGraph 95 uses the gray, navy, teal, white, and black palette. FastGraph 95
Dark uses charcoal surfaces, muted gray bevels, pale gray text, and dark graph
fields. Do not use pure white surfaces in the dark version. Use small dither
patterns only on non-data chrome. Keep graph fields solid so the data stays
clear. Draw graph paths as fine staircases in these two themes. Reduce the
display path in logarithmic frequency bins and retain the local minimum and
maximum values in each bin. Draw a horizontal run before each vertical change.
This change must affect the displayed path only. It must not resample or round
the stored curve or the numerical export data.

Curator image exports in the two FastGraph 95 themes use a square title bar,
a small dither strip, recessed graph and footer panels, and the theme palette.
Reserve a separate lower area for frequency labels and a separate footer area.
No label can touch the graph border or image edge.

Hackerman 95 uses the same classic control and staircase graph framework. Use
layered charcoal-gray application surfaces, neutral light-gray body text, a
monospace interface font, and a black graph field. Reserve neon green for
selected tabs, focus, active controls, the level meter, and primary graph data.
Its first trace and variation color is neon green `#39FF14`. Additional layers
use cyan, magenta, yellow, orange, violet, mint, and pink in that order. Do not
change a saved layer color when the user changes themes.

Dialogs, message boxes, input prompts, file dialogs, and their buttons must use
the active theme. FastGraph 95 dialog buttons use the same square bevel as the
main interface. The pressed state reverses the bevel and moves the label by one
pixel.

## Measure Layout

Keep the queue bar directly above the top viewport. Keep the input level meter,
Variation controls, and HRTF controls between the two viewports. Keep export
controls below the bottom viewport. The queue bar can move its progress displays
to a second row at narrow widths.

## brand

Use these primary brand values:

- Curator poster and image-export canvas: `#232323`
- Off-white: `#F2F1F1`
- White: `#FFFFFF`
- Orange: `#7A7A7A`
- Red: `#6E6E6E`
- Magenta: `#626262`

Use the standard dark `#07090C` value for the BRAND application backdrop. Keep
the `#232323` canvas for Curator BRAND previews and image exports. Do not change
export colors when changing application surfaces.

Use `#1C1C1C`, `#2A2A2A`, `#303030`, and `#333333` only as neutral surface
steps. Reserve the orange-red-magenta gradient for key Brand accents and primary
actions. Do not apply the gradient to every control.

## Review Checklist

- Check dark, light, FastGraph 95, FastGraph 95 Dark, Hackerman 95, and Brand modes.
- Check keyboard focus and disabled controls.
- Check 1280 x 700 and 1800 x 1100 windows.
- Confirm that the inner glow does not cross the recessed well.
- Confirm that tab shapes and control radii are consistent.
- Confirm that saved graph values did not change.
- Confirm that Curator frequency labels and footer text do not touch an edge.
