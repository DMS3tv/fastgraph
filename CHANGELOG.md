# Changelog

## Unreleased

### Fixed

- Console logging: every console message now goes through Python's standard
  logging (one handler feeds the console and its log file), including worker
  threads, the Curator and Squiglink upload diagnostics. Any ERROR that reaches
  the console, including Curator errors, can now fire the app_error automation
  trigger.
- Audio device, settings and calibration failures that used to be swallowed
  are now logged: device enumeration, level-monitor and sweep errors log a
  warning with the exception, and a settings or calibration file that cannot
  be written logs an error.
- Standard-mode measurements now reject silent or unrelated recordings
  instead of showing a flat 0 dB curve. Alignment confidence is measured as
  peak-to-background of the sweep correlation in every mode (default 6.0),
  with a hard floor on the correlation coefficient and a mid-band noise
  margin; a recording is rejected only when both confidence and margin fail,
  so headphones with strong band-edge rolloff, echoes, and clipped sweeps are
  still accepted. Low SNR warns in the review dialog. A stored 9.0 confidence
  setting from earlier versions moves to the new default once.
- Bluetooth sweeps whose end markers are degraded by codec smear now use the
  sweep-correlation fallback with a warning instead of failing. Only a
  reversed marker order blocks the fallback, and every Bluetooth failure now
  reports SNR and signal-margin diagnostics.
- Test suite: Qt deferred deletes are flushed between tests, so the suite no
  longer slows down quadratically (the slowest file went from 258 s to 16 s).
  The theme controller skips re-applying an unchanged stylesheet.

- Measure queue: a terminal sweep error, a manual Fail, Cancel Queue and
  Clear All now reset the whole queue state, so no phantom queue remains and
  a manual Fail restores the retry budget. Device-unavailable errors are
  terminal in Two Channel mode instead of prompting three pair retries. The
  keyboard shortcut, console and automations can no longer start a queue
  while Channel Balance is active. Device changes no longer re-select
  devices mid-queue. Closing waits for a running sweep or upload and asks
  before discarding kept Measure curves.
- Settings, calibration and automation files are written atomically; a
  damaged file is backed up and reported instead of silently replaced.
- Squiglink uploads verify the server's SSH host key on first use, time out
  after 20 s, run on a background thread with a cancellable progress dialog,
  and save credentials only after a successful upload.
- Curator: decimal-comma and BOM files import correctly, combined variation
  bands use a proper mixture model instead of percentiles of percentiles,
  PNG exports drop out-of-band
  points and honour the 25 dB/decade lock, brand-mode colours pass the contrast
  guard, Show Names works outside Clean Slate, Remove acts on every selected
  layer, layers can be reordered, and combined layers show STALE when a
  source changes.
- Release builds run the test suite first, upload assets one at a time (the
  Windows zip name broke the old upload), publish SHA256SUMS.txt, and
  install from a pinned requirements.lock.
- R&D: saving a session never deletes photos that belong to another
  session, and Save As asks before replacing a file; crash recovery survives
  a failed generation rotation and reports sessions saved by a newer
  Fastgraph instead of quarantining them; View 2 honours a grouped
  measurement's own checkbox; measurements cannot be dropped onto other
  measurements; Remove acts on every selected row; group rename keeps text
  in parentheses; HRTF files are cached instead of re-read on every redraw.
- Automation: the editor keeps the condition operator and values and the
  confirmation flag when an automation is re-saved, Duplicate no longer
  blanks the source, triggers that fire while another automation runs are
  queued instead of dropped, and `console_command` steps that start
  measurements or exports ask for confirmation like the direct actions.
- Audio: level meters no longer emit Qt signals from the PortAudio callback,
  and device enumeration runs on a background thread and pauses during a
  queue, so the interface no longer hitches every 1.5 seconds.

### Changed

- The branded mode is no longer part of the public app. A private brand
  package plugs in through `dms/branding.py` (`FASTGRAPH_BRAND_PLUGIN`);
  without one, Settings shows no brand toggle. The mode is a plain toggle,
  with no password, and its setting is stored as `brand_mode`.
- Code conventions: the default 1/48-octave smoothing and the analysis grid
  use named constants, measurement failure and warning reasons are string
  enums (saved files and dumps keep the same text), module-only helpers are
  private, long UI builders are split into sections, and ruff now enforces
  function size and complexity limits. Internal only.
- Frequency responses are now computed through an impulse-response window
  (deconvolution, circular window with tapers, transform back). The result
  matches the previous method within 0.07 dB on a linear system and keeps
  the headphone's decay while rejecting harmonic distortion products.
  Per-sweep resampling to the log grid averages the power within each cell
  instead of sampling single FFT bins.
- Exported averages and Squiglink uploads now contain the curve shown on
  screen, smoothed at 1/48 octave; the file header says so.
- Population-HRTF variation bands combine measurement and compensation
  spread in quadrature instead of pairing the widest percentiles. The
  unreachable `hrtf_variation_combination = "worst_case"` option has been
  removed, and the key is dropped from saved settings once.
- Bluetooth mode's standard-profile fallback now restores a start alignment
  confidence minimum of 6.0, matching the new default, instead of 9.0.
- Export All writes the same average curves as Export Average: smoothed at
  1/48 octave, with the smoothing and level recorded in the header.
- R&D measurement exports record the R&D smoothing and any vertical offset
  (`* Offset: <x> dB`) in the file header.
- Variation exports record the level mode (1 kHz reference or calibrated dB
  SPL) in the header, the same way average exports do.
- Removed dead and legacy code paths: unused helpers and widgets (the old
  session dialog, theme toggle and chirp marker among them), the unwindowed
  frequency-response and point-sampled resampling paths, the `AppState`
  mirror of the measure queue's states, and several one-line wrappers.
- Duplicated helpers now have one home each: atomic session writes
  (`file_io`), crash recovery for Measure and R&D (`dms/recovery.py`), the
  shared log grid and 1 kHz lookup (`processing.log_grid`, `value_at`),
  TXT parsing including six-column HRTF files (`curator.parser`),
  `SessionData.from_dict`, and the REW export writer. Interpolation uses
  `np.interp` instead of scipy's `interp1d`; outputs are unchanged to
  within 1e-12 dB.
- A reference layer whose file does not reach 1 kHz is now rejected with a
  message instead of being anchored on its nearest end (target curves still
  anchor that way, with the existing warning).
- Combined Curator variation bands use exact normal quantiles, which moves
  them by about 0.0001 dB.
- R&D exports take blank headphone fields as blank instead of writing
  "Unknown".
- Variation bands are one named type, `VariationBand`, built by
  `processing.percentile_band` everywhere. The Measure, R&D, HRTF and
  Curator code previously passed the five percentiles in two different
  orders. Band values are unchanged.
- Graph helpers now have one home each: variation and preference-bounds
  drawing, plot setup, frequency ticks and markers, and the dither band
  (`dms/graph_display.py`); the brand-or-theme colour choice and colour
  mixing (`theme.colors_for`, `theme.mix_colors`). Theme tokens moved to
  `dms/style_tokens.py`, so Curator image export no longer loads UI
  modules. Graphs look the same.

### Added

- Measure: a Distortion toggle between the plots draws THD, H2 and H3
  relative to the fundamental on a right-hand axis when the sweep's SNR is
  at least 20 dB, and the review dialog reports THD over 100 Hz to 10 kHz.
- Measure: a Level selector for 1 kHz reference or absolute dB SPL using
  the input device's SPL calibration and the output level.
- Measure sessions: a Session menu saves and loads `.fastgraph-measure.json`
  files holding the kept sweeps or pairs with their diagnostics, the
  headphone metadata, the HRTF selection and the level mode, so a sitting
  can be reopened and re-exported later. Unsaved work is marked in the
  window title, the close prompt offers Save, and a crash-recovery copy is
  kept like the R&D one. Settings gains a default session folder.
- Compare: a Compare menu loads a target curve, shows the measurement minus
  target as a delta view with a 0 dB line, overlays up to three reference
  curves from TXT or session files with a legend, reports a per-band
  deviation score and match percentage in the review dialog and status bar,
  and suggests a parametric EQ that can be copied or saved as an Equalizer
  APO block. Console commands `measure session`, `measure target` and
  `measure eq` drive the same features.
- Frequency axes are always labelled in Hz instead of an automatic kHz or
  MHz prefix.

- Settings: "Sweep Noise Margin Min", "SNR Warning Below", and "Save failed
  recordings for diagnosis". Saved dumps can be replayed offline with
  `tools/replay_failed_recording.py`.
- Settings: "Squiglink host keys" pinning (`squiglink_host_keys`) and
  "Confirm before discarding kept measurements".
- Diagnostics summaries include the sweep correlation peak, the next-best
  alignment ratio, the mid-band level above noise, and sweep coverage by
  tenth.

### Notes

- The all-modes enforcement, non-finite sample guard and several persistence
  ideas are adapted from GoldenSound's pull request #9 with thanks.

## [0.4.2] - 2026-08-29

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
- Added theme-aware two-channel plot panes for all standard themes and brand
  mode.
- Replaced the two-channel mode switch with a themed segmented control for
  Frequency Response and Channel Balance. Each segment keeps its full label.

### Fixed

- Prevented Dither button labels from cutting off at the left and right edges.

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
- A Curator brand-mode Clean Slate export with only the graph, brand logo, and optional
  layer names.
- Collapsible Curator controls with stable list and panel scroll positions.
- A display-independent 50/50 R&D splitter default with a saved user ratio.
- A shared animated Inputs panel that makes device controls available from
  every main tab.
- A responsive Measure queue bar above the top viewport, with device controls
  removed from the old sidebar.
- A brand-mode Measure batch that exports `RAW AVG`, `COMP AVG`, `RAW VAR`, and
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
  standard light and brand mode accents unchanged.
- Removed the translucent curve halo from Curator previews and PNG exports in
  standard and brand modes.
- Improved compact R&D toolbar layout and the surface order for dark, light,
  and brand modes.
- Replaced R&D list toggles with View 1 and View 2 checkboxes, preserved group
  collapse state, and changed the initial workspace split to 50/50.
- Kept the standard dark application backdrop in brand mode while retaining
  brand panel, control, graph, and viewport colors.
- Added brand-mode Curator poster layout, metadata controls, font checks, access
  control, and 3840x2160 export.

### Compatibility and Safety

- Manual R&D saves now use atomic JSON replacement and share the recovery
  persistence path.
- Existing two-column HRTF files remain supported.
- Standard mode keeps Squiglink upload and adds password-safe connection
  diagnostics to its console.
- Curator brand-mode image exports keep the existing canvas and brand
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

## [0.3.4]

The R&D tab now includes a between-viewport smoothing control with `1/48`,
`1/24`, `1/12`, `1/6`, and `1/3` options. The selected smoothing applies to R&D
plotting, TXT export, and Send to Curator while leaving saved raw session curves
unchanged.

R&D also adds bottom-viewport Delta Mode. When enabled, the first bottom item is
used as the reference and later bottom-visible measurements or Var bands are
shown as deltas from that reference. The top viewport remains normal.

The R&D measurement list now has a more obvious vertical scrollbar and supports
measurement multi-select. Ctrl/shift selection can be used to pick measurements,
and New Group moves the selected measurements into a new group in visual order.

## [0.3.3]

R&D session HRTF selections now save both the original file path and the HRTF
name. When a session is opened on another machine or install folder, Fastgraph
tries to reconnect the selection to a matching file in the current `HRTFs`
folder. If an HRTF is still missing, the R&D status line and row dropdown show
that clearly, and export/Curator transfer is blocked until the row is changed to
an available HRTF or `None`.

R&D **Var** behavior is stricter: when a group has **Var** enabled, its original
traces are hidden even if there are not yet enough measurements to draw the
band. The status line reports that the group needs at least two measurements.
The group **View 2** checkbox continues to show the full group in View 2.

## [0.3.2]

R&D groups now draw **Var** with the same confidence-style band used by Measure
and Curator: p10-p90 outer fill, p25-p75 inner fill, and a median line. When a
group's **Var** is active, the band replaces that group's individual traces in
that viewport. Multiple visible group variation bands use distinct colors.

The R&D tab also adds top-of-viewport preference bounds, an importable custom
session target with its own dB offset, row-level Curator-style dB offsets for
measurements and groups, and a default HRTF dropdown next to the R&D Measure
button. Group and measurement offsets are additive, so a group can shift a whole
prototype set while individual measurements can still be fine-tuned. New R&D
measurements inherit the default R&D HRTF selection when they are kept.

R&D session saves include the new offset, target, HRTF, and preference-bounds
state. Older R&D session files still load with offsets set to `0 dB`, no target,
and bounds off.

## [0.3.1]

Fastgraph now includes an **R&D** tab for exploratory single-sweep measurement
sessions. R&D measurements are kept as individual named entries with metadata,
input/channel identity, notes, milestone flags, per-measurement HRTF selection,
View 1 visibility, and View 2 visibility. Measurements can be organized into
named groups with group notes, view checkboxes, milestone marking, and optional
group variation bands.

R&D group variation follows the viewports where the group is visible.
**View 1** and **View 2** control group visibility in each viewport. **Var**
adds the variation band only to those active group viewports. R&D sessions can be saved
and loaded as `.fastgraph-rnd.json`
files; Settings includes a default folder for those session files.

Selected R&D measurements and groups can also keep photo notes. Use **Capture**
to take a webcam photo or **Import** to attach an existing image, then click a
thumbnail to view, caption, or remove it. Photos are saved as compact JPEGs in
a sibling `<session-name>.attachments` folder, so move or copy that folder with
the `.fastgraph-rnd.json` file when sharing a session.

R&D exports operate on the selected measurement or group. Measurements export as
TXT, groups with variation enabled export as variation TXT, and selected
measurements or group variations can be sent directly to Curator.

## [0.3.0]

Fastgraph now includes **Curator**, a graph image generation tool for comparing
headphone frequency-response and variation-band measurements and turning them
into presentation-ready graph images. Curator lives between the Measure and
Automation tabs, shares Fastgraph's HRTF library and theme, and reports its
actions to the same diagnostic console.

The Measure tab can send its current average or variation view directly to
Curator. The transferred layer receives an editable 1 kHz offset so it sits at
0 dB without changing the source data or frequency-response shape. Any active
HRTF remains selected and can be changed or removed in Curator.
