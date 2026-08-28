# Changelog

## Unreleased

### Added

- Added Two Channel measurement to the Measure tab. Each queue item now captures
  channel 1/L and channel 2/R as one pair with explicit input and output routing.
- Added Combined and Separate two-channel result layouts. Separate mode routes
  exports and transfers through the selected L or R viewport. Combined mode uses
  the power mean and the `BOTH` file label.
- Added Channel Balance with synchronized L/R and L-minus-R scopes, live RMS
  values, and a continuous sine or band-limited square generator.

### Improved

- Added one shared 1 kHz pair offset. The offset preserves the L/R level
  difference while it places the pair's power mean at 0 dB.
- Kept separate single-channel and two-channel Measure workspaces in memory.
  Undo and Clear All now operate on the active workspace.
- Added theme-aware two-channel plot panes for all standard themes and BRAND
  mode.
- Replaced the two-channel mode dropdown with a themed two-label switch for
  Frequency Response and Channel Balance.

## [0.4.1] - 2026-08-14

### Added

- Theme choices in Settings: Default dark, Default light, FastGraph 95,
  FastGraph 95 Dark, and Hackerman 95.
- A registered theme definition system so future themes can provide their own
  colors, typography, geometry, motion, graph palette, and renderer family.
- FastGraph 95 interface surfaces, classic buttons, tabs, fields, menus,
  dialogs, and dither details.
- FastGraph 95 Dark and Hackerman 95 variants. Hackerman 95 uses charcoal
  interface surfaces with selected neon-green controls and multi-color neon
  graph traces.
- Stepped graph rendering for the 95 themes. It preserves the measured curve
  while giving the display a fine retro step appearance.

### Improved

- Kept graph backgrounds and trace palettes readable in every theme, including
  black graph fields in Hackerman 95.
- Changed the 95-theme level meter to a segmented progress-bar display.
- Made headphone metadata available from a menu that matches the Inputs menu.
- Updated Curator exports with theme-aware graph styling and corrected bottom
  text spacing so labels do not cut off.

### Fixed

- Prevented Curator export legend names from being shortened with an ellipsis.
  Each legend column now expands to fit its longest complete layer name.
- Prevented Linux PipeWire startup and calibration crashes by opening only
  enough input channels to include the selected channel.

## [0.4.0] - 2026-08-08

### Added

- Local R&D crash recovery with two active generations, deferred bundles,
  attachment recovery, startup restore choices, and normal-close dirty checks.
- Measure-to-R&D transfer for one average or a grouped set of kept Var curves.
- A Curator BRAND Clean Slate export with only the graph, BRAND logo, and optional
  layer names.
- Collapsible Curator controls with stable list and panel scroll positions.
- A display-independent 50/50 R&D splitter default with a saved user ratio.
- A shared animated Inputs panel that makes device controls available from
  every main tab.
- A responsive Measure queue bar above the top viewport, with device controls
  removed from the old sidebar.
- A BRAND Measure batch that exports `RAW AVG`, `COMP AVG`, `RAW VAR`, and
  `COMP VAR` without changing the active graph state.
- Six-column population HRTF support for P10, P25, median, P75, and P90
  compensation data.
- A shared interface style system with mode tokens, modern buttons, compact
  spin boxes, rounded viewports, and a visual style guide.

### Improved

- Expanded the diagnostic console with a persistent rotating log, a system
  report, compact exception chains, and detailed Squiglink SFTP stages. Secret
  values remain redacted.
- Replaced the Measure input bar with a smooth, token-based glow meter. Its
  display animation now softens rise and fall changes without increasing the
  audio sampling rate.
- Moved the Measure queue controls into a responsive bar above the top graph
  and renamed Start Queue to Measure. The level meter, Variation, and HRTF
  controls remain between the two viewports.
- Added a purple Inputs accent for the standard dark theme while keeping the
  standard light and brand accents unchanged.
- Removed the translucent curve halo from Curator previews and PNG exports in
  standard and brand modes.
- Improved compact R&D toolbar layout and the surface order for dark, light,
  and brand modes.
- Replaced R&D list toggles with View 1 and View 2 checkboxes, preserved group
  collapse state, and changed the initial workspace split to 50/50.
- Kept the standard dark application backdrop in BRAND mode while retaining
  BRAND panel, control, graph, and viewport colors.
- Added BRAND Curator poster layout, metadata controls, font checks, access
  control, and 3840x2160 export.

### Compatibility and Safety

- Manual R&D saves now use atomic JSON replacement and share the recovery
  persistence path.
- Existing two-column HRTF files remain supported.
- Standard mode keeps Squiglink upload and adds password-safe connection
  diagnostics to its console.
- Curator BRAND image exports keep the existing `#232323` canvas and brand
  colors.

### Notes

- This release covers all user-facing changes since v0.3.5.
- Release packages are built for Apple Silicon macOS, Windows x64, and Linux
  x64.

## [0.3.5] - 2026-07-19

### Added

- R&D workspace for documenting measurements, HRTF options, and reference photos.
- Curator graph workspace with parsing, transformations, bounds, export, and presentation controls.
- Automation builder and application command console.
- Persistent light and dark themes, plus measurement-workflow refinements.
- Linux build script and packaged Linux application folder.

### Improved

- R&D session HRTF handling, photo controls, and workflow integration.
- Curator exports and graph presentation workflows.
- Windows, macOS, and Linux packaging paths.
- Fastgraph's shared app icon, now using colored analysis traces.

### Notes

- This release covers all user-facing changes since v0.2.6.
