# FastGraph architecture

A map of the code for someone who has to change it. It says where each kind
of code lives, which direction imports go, where state is kept, and how to add
a tab. For what the app does to a measurement, read
`MEASUREMENT_DATA_PATH.md`.

## Layers

```
dms/            core: pure Python + numpy/scipy, no Qt except where noted
dms/curator/    Curator import, transforms and image export (Qt painting only)
dms/rnd/        R&D session models, persistence, photos
dms/ui/         everything that shows on screen; imports core, never the reverse
tests/          one file per module, shared helpers in tests/helpers.py
tools/          developer scripts (render_tabs.py, replay_failed_recording.py)
```

Imports go one way: `dms/ui` imports `dms`, `dms/curator` and `dms/rnd`;
nothing under `dms/`, `dms/curator/` or `dms/rnd/` imports `dms/ui`. A test
guards the Curator image exporter against pulling in a UI module.

## Core modules

| Module | Owns |
|---|---|
| `processing.py` | Sweep generation, deconvolution, windowing, spectrum, log-grid resampling, 1 kHz normalization, RMS averaging, fractional-octave smoothing, `VariationBand`, the grid and reference constants (`GRID_POINTS`, `F_LOW`, `F_HIGH`, `F_REF`, `DEFAULT_SMOOTHING`) |
| `measurement_layout.py` | Where the sweep, silences and Bluetooth markers sit in the output signal |
| `measurement_alignment.py` | Finding the sweep in a recording, the integrity gate, Bluetooth marker handling, diagnostics |
| `measure_queue.py` | The measurement queue as a pure state machine (`QueueState`, `MeasurementQueue`) |
| `audio_engine.py` | Device enumeration, level monitors, `SweepWorker` (play/record on a thread), `DevicePoller` |
| `hrtf.py` | HRTF curves: load, evaluate, apply to a curve or a variation band |
| `two_channel.py` | Paired L/R captures and their combination |
| `comparison.py` | Target delta, deviation score, EQ suggestion, reference layers |
| `export.py` | REW-style text export (`export_curve`, `export_variation`) |
| `file_io.py` | Atomic writes with validation, backup-on-corrupt loads, `ensure_extension`, `same_session_file` |
| `recovery.py` | Crash-recovery manager shared by Measure and R&D |
| `measure_session.py`, `measure_persistence.py` | Measure session data and its file format |
| `session.py`, `settings_manager.py`, `calibration.py`, `secure_store.py` | Headphone metadata, settings (schema-versioned, atomic, mode 0600), microphone calibration, obfuscated credential store |
| `squiglink.py` | SFTP upload with trust-on-first-use host keys |
| `console.py` | Console event store and the `logging` handler that feeds it |
| `theme.py`, `style_tokens.py`, `graph_display.py`, `dither_fonts.py`, `branding.py` | Themes, design tokens, display-only plot helpers (retro steps, tick lists, band drawing), fonts, the optional brand-plugin hook |
| `automation.py`, `measurement_profiles.py`, `update_checker.py`, `recording_dump.py` | Automation files, measurement profiles, release check, failed-recording dumps |

Core modules that import Qt do so for a reason stated in their docstring
(`console.py` for the queued signal, `theme.py`, `channel_balance.py`,
`update_checker.py`, the recovery timer).

## UI modules

`dms/ui/main_window.py` is scaffolding only: it builds the tabs and header,
owns the overlays, shortcuts, theme wiring and the close sequence, and creates
the controllers below. Each controller is a `QObject` that holds a reference
to the window and owns one concern. Controllers reach the window only for its
scaffolding (settings, session, status bar, tabs, the plot widgets) and reach
each other through their public methods.

| Attribute on the window | Module | Owns |
|---|---|---|
| `measure` | `measure_controller.py` | The queue, the sweep runner, kept curves and pairs, averages, variation, HRTF selection, level mode, distortion overlay, channel balance. Emits `state_changed` and `curves_changed`. |
| `measure_tab` | `measure_tab.py` | The Measure tab's widgets, exposed as public attributes |
| `measure_io` | `measure_io.py` | Export Average / Variation / All, Measure session files, the Session ▾ menu, crash recovery |
| `measure_compare` | `measure_compare.py` | Target and reference layers, delta view, deviation score, the Compare ▾ menu, EQ suggestion |
| `devices` | `device_controller.py` | Device and channel selection, the poller, level monitors, Bluetooth mode, calibration and test level |
| `squiglink` | `squiglink_controller.py` | Upload, credentials, host-key and phone-book prompts, the upload worker thread |
| `commands` | `command_controller.py` | Console commands and automation execution (`trigger(name)`) |
| `rnd` | `rnd_bridge.py` | Everything the window does for the R&D tab: its sweep, review, exports, sessions, recovery, and the hand-offs between Measure, R&D and Curator |
| `update_check` | `update_check.py` | The quiet release check |

Widgets: `dual_plot_widget.py` and `measure_workspace.py` (the Measure plots),
`curator_widget.py` + `curator_graph_widget.py`, `rnd_widget.py`,
`console_widget.py`, `automation_widget.py`, `settings_dialog.py`, the
dialogs in `measure_dialogs.py`, and the themed controls
(`modern_button.py`, `modern_spinbox.py`, `toggle_switch.py`, `level_meter.py`,
`rounded_viewport.py`, `theme_surface.py`).

## Where state lives

- Measurement state (queue, kept curves, averages, HRTF, level mode) is on
  `window.measure`. Tests read the queue directly: `window.measure.queue.state`.
- R&D state is the `RnDSession` on `window._rnd_widget.session`; the bridge
  tracks dirtiness and recovery.
- Curator state is `CuratorWidget.graph_state` (layers, view settings).
- Settings are one `SettingsManager`; widgets call `refresh_from_settings()`
  after a change. Saved files use `file_io.atomic_write_text`.
- Logging: modules use `logging.getLogger(__name__)`. `ConsoleLogHandler`
  turns records into console events (severity from the level, source from the
  `source` extra or the logger name, details from the `details` extra), so a
  `logger.warning("...", extra={"source": "squiglink"})` appears in the app
  console.

## Threads

Three things leave the GUI thread: `SweepWorker` (owned by `SweepRunner`,
joined on close), the Squiglink upload worker (joined on close), and
`DevicePoller`. Level monitors run in PortAudio callbacks and hand block RMS
to a GUI timer. Everything else is GUI-thread only.

## Adding a tab

1. Put the widget in `dms/ui/<name>_widget.py`, with no knowledge of the
   window.
2. If the window has to do work for it (sweeps, exports, hand-offs), put that
   in `dms/ui/<name>_bridge.py` as a `QObject` constructed by `MainWindow`,
   following `rnd_bridge.py`.
3. Add the tab in `MainWindow._build_ui`, a shortcut in
   `_configure_keyboard_shortcuts`, and a `tests/test_<name>_widget.py`
   that builds it through `make_main_window` from `tests/conftest.py`.
4. Render it with `tools/render_tabs.py` before and after any later change
   and compare the PNGs.

## Rules that are not obvious from the code

- Exports write what the screen shows: Export Average, Export All and the
  Squiglink upload all use the smoothed display curve and say so in the
  header.
- New measurement gates must not reject sweeps whose band edges are silent
  because of headphone or device rolloff.
- Squiglink Combined uploads carry the `L` side because the site needs L or R.
- `tests/test_theme.py` snapshots the stylesheets; re-snapshot only after
  confirming with `git diff dms/theme.py` that the change was intended.
