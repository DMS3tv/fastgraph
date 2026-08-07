# Changelog

## [Unreleased] - 2026-07-30

### Added

- Local R&D crash recovery with two active generations, deferred bundles,
  attachment recovery, startup restore choices, and normal-close dirty checks.
- Measure-to-R&D transfer for one average or a grouped set of kept Var curves.
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
