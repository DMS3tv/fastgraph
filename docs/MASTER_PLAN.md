# FastGraph Master Plan — Hardening 2026-09

Permanent record of the hardening and next-level work that started on
2026-09-08 from the audit in `FASTGRAPH_REVIEW_2026-09-08.md`. The approved
implementation plan lives outside the repo (Claude plan file); this file is
the in-repo summary and the progress log. One dated row per shipped slice.

## Phases

| # | Phase | Status |
|---|---|---|
| 0 | Records and hygiene (branch, records, .gitignore, PR #8, README fixes) | done 2026-09-08 |
| 1 | Test infrastructure and the widget leak (conftest, ThemeController lifetime) | done 2026-09-08; full suite 78 s |
| 2 | Measurement integrity gate for standard mode | done 2026-09-08 |
| 3 | Bluetooth reliability | done 2026-09-08 (hardware acceptance pending, Phase 10) |
| 4 | Measurement queue extraction and audio-thread hygiene | done 2026-09-08 (B1–B8, B10, B11, E5 closed) |
| 5 | Persistence and transport hardening (PR #9 themes, credited) | 5a done 2026-09-08; 5b R&D/automation done 2026-09-08 |
| 6 | Curator correctness and rendering | done 2026-09-08 |
| 7 | Release engineering | done 2026-09-08 (signing slot documented, not enabled) |
| 8 | DSP upgrade (deconvolution, distortion, band averaging, SPL) | done 2026-09-08 |
| 9 | Measure sessions and comparison features | done 2026-09-08 |
| 10 | Hardware acceptance (when hardware is available) | pending |

## Standing decisions

- Squiglink Combined uploads carry the `L` side on purpose: the site requires
  L or R and cannot accept both. The `BOTH L` name modifier marks them.
- New measurement gates must not reject sweeps whose band edges are silent
  because of headphone or device rolloff.
- macOS signing and notarization are deferred; a documented slot exists in CI.
- Squiglink credentials stay in the file store, locked to mode 0600.
- Ideas ported from GoldenSound's PR #9 are credited in commit messages.

## Progress log

| Date | Slice | Result |
|---|---|---|
| 2026-09-08 | Audit of 0.4.2 completed; `FASTGRAPH_REVIEW_2026-09-08.md` written | 401 tests passed, ~50 findings |
| 2026-09-08 | PR #8 merged to `main`; branch `hardening/2026-09` created | done |
| 2026-09-08 | Phase 1 core: `tests/conftest.py` flushes Qt deferred deletes, isolates app data, warns on widget leaks; `ThemeController` no longer app-parented and skips unchanged re-apply | `test_rnd_integration.py` 258 s → 16 s |
| 2026-09-08 | Phase 2: integrity gate. Start confidence is now peak-to-background (default 6.0, all modes); hard floor on the correlation coefficient (0.10); mid-band noise-margin check (3 dB); rejection needs confidence AND margin to fail; low-SNR warning (10 dB); non-finite guard; diagnostics carry coverage-by-tenth; settings migrate a stored 9.0 to 6.0 once (schema v2); dead `f_low`/`f_high` removed | 8 valid + 6 garbage synthetic cases in `tests/test_measurement_integrity.py`; alignment suite green |
| 2026-09-08 | Phase 3: Bluetooth reliability. Weak chip agreement or a small identity margin now routes to the sweep-correlation fallback instead of vetoing it (only reversed marker order blocks); identity floor 1.08 → 1.02; fallback SNR floor 18 → 10 dB and the raw sweep dot product replaced by the correlation coefficient; the fallback applies the Phase-2 integrity gate; every Bluetooth failure carries SNR and integrity metrics; opt-in "Save failed recordings" writes WAV + JSON and `tools/replay_failed_recording.py` replays them. Marker frequency-set widening deferred until real dumps exist | Codec-smear model reproduced the real failure pattern (agreement 0.03, confidence 117) and now recovers; `tests/test_bluetooth_reliability.py` (9 tests); 88 alignment tests green |
| 2026-09-08 | Phase 7: release workflow split into test → build → publish; per-asset upload loop (fixes the Windows zip space bug) and the zip renamed `FastGraph-Beta-windows-x64.zip`; `SHA256SUMS.txt` published; `requirements.lock` (27 pins) used by all three build scripts; PyInstaller pinned 6.21.0; CI Python 3.14 to match dev (wheels verified for all three platforms); `upx=False`; commented notarization step + README section; `ubuntu_deps.sh` picks package names per release; `build_linux.sh` no longer crashes under `set -u` without arguments; Compress-Archive check fixed | `bash -n`, YAML parse, PyInstaller version verified; PowerShell not machine-checked (no pwsh on this Mac) |
| 2026-09-08 | Phase 4 part 1: pure `dms/measure_queue.py` (22-row transition table), `dms/ui/sweep_runner.py`, `is_device_failure()` | 67 new tests; not yet wired into the window |
| 2026-09-08 | Phase 6: parser handles BOM/UTF-16/decimal commas/mixed row widths/duplicate frequencies; 1 kHz normalization warns instead of clamping; smoothing works on linear grids (log grids byte-identical); combine is a sweep-weighted mixture of normals (±1 dB with ±10 dB → ±6.57 dB, was ±5.5); HRTF edge hold; PNG drops out-of-band points and honours the 25 dB/decade lock; BRAND contrast guard, Show Names, bounds interpolation, multi-select Remove, Move Up/Down, STALE badge on combined layers, bounds toggle refuses to latch without files | 169 curator/brand/export tests green; dark, BRAND and poster renders inspected |
| 2026-09-08 | Phase 5a: `dms/file_io.py` atomic writes + backup-on-corrupt for settings (mode 0600), calibration, automations, with a startup warning; settings type coercion; Squiglink trust-on-first-use host keys (`squiglink_host_keys`, SHA256 fingerprint prompt, mismatch fails before credentials are sent), 20 s connect timeout, upload on a worker thread behind a cancellable progress dialog, credentials saved only after success, password scrubbed from error logs; update checker https-only and github-only with `packaging` version compare; console appends incrementally. Credit: adapted from GoldenSound's PR #9 | 135 focused tests; full suite 627 passed |
| 2026-09-08 | Phase 1 migration: 19 test files moved onto the shared conftest (session `qapp`, `make_main_window`, no per-file platform/env lines); the factory now releases the window reference instead of deleteLater(); fixed a real per-module QApplication teardown failure that broke shortcut + automation tests when run together | full suite 630 passed in 78 s (was 26–50 min); 6 leak warnings left in Squiglink window tests (follow-up) |
| 2026-09-08 | Phase 4 integration: `MainWindow` holds a `MeasurementQueue` behind its historical field names (properties) and starts sweeps through `SweepRunner`, which joins any previous thread first and on close. One `reset()` now serves terminal errors, Fail, Cancel, Finish, Clear All and a vanished device (B4, B5, B6). Device errors are terminal in two-channel mode while stream errors keep one retry (B8). Queue start from the shortcut, console or automations is blocked in Channel Balance and every sweep start stops the generator (B7). Device polling defers re-selection until idle and aborts a review when the selected device vanishes (B3). Close joins the sweep and upload threads (B1) and asks before discarding kept Measure curves (B11, setting `confirm_discard_measurements`) | `tests/test_main_window_queue_state.py` (12 tests); full suite 642 passed in 104 s |
| 2026-09-08 | Phase 8 core (5fc343f): impulse-response deconvolution with circular windowing (matches legacy within 0.074 dB; legacy kept behind `window=False`), harmonic analysis H2–H5 + THD (−32.17/−46.15 dB recovered), band-averaged log resampling, SPL offset helper | 17 new tests; window wiring pending |
| 2026-09-08 | Phase 5b (1697506 + window half in a1790a9): Save-As never deletes another session's photos and asks before replacing; staged recovery rotation with a degraded flag; newer-schema sessions reported not quarantined; View 2 honours grouped rows; measurements cannot be dropped onto measurements (groups stay drop targets, nested groups salvaged); multi-select Remove with one confirmation; rename keeps `" ("`; HRTF cache; automation editor round-trips operator/values/confirm flag; Duplicate copies; Save As asks; triggers queue instead of dropping; console_command risk by prefix; single-pass variables | 698 tests green |
| 2026-09-08 | Phase 8 wiring: alignment returns the post-sweep tail (standard: post-silence; Bluetooth: up to the end-marker gap) and the worker emits it, so the windowed deconvolution is live; Distortion toggle between the plots with THD/H2/H3 on a right axis (−110..−20 dB rel.) with a legend, gated on SNR ≥ 20 dB, persisted for the last kept sweep; harmonic orders masked 10 % below their band limit to hide the taper rise; Level selector "1 kHz ref / dB SPL" using the calibration store (falls back with a message when uncalibrated; refuses to mix modes over kept curves); exports and Squiglink uploads now match the displayed 1/48-octave curve with `* Smoothing:` and `* Level:` headers; population-HRTF bands combine in quadrature (`hrtf_variation_combination`, legacy `worst_case` kept) | dark + BRAND Measure renders inspected with a synthetic x+0.05x²+0.02x³ system; full suite green in 106 s |
| 2026-09-08 | Phase 9 modules (3d43944, f488f3d): `.fastgraph-measure.json` sessions with per-sweep diagnostics, atomic parse-back-validated saves, two-generation crash recovery with staging and a clean-exit marker; target loading, delta on a shared 1200-point grid with three offset modes, per-band deviation score with a match percentage, greedy peaking-EQ fit (single filter recovered within 0.05 dB / 0.2 % / 3 %), Equalizer APO export, A/B reference layers | 100 new tests |
| 2026-09-08 | Phase 9 wiring: Session ▾ menu (New/Save/Save As/Load) at the start of the export row with dirty tracking in the title, a Save option in the close prompt, crash-recovery prompt at startup; Compare ▾ menu after Level (Load/Clear Target, Delta View, Load/Clear Reference, EQ Suggestion…); target and reference layers with a legend, delta view on the bottom plot, deviation summary in the review dialog and "Match" in the status bar, EQ dialog with copy/save APO; console commands `measure session/target/eq`; frequency axes no longer auto-prefix to kHz/MHz. Limitation: delta view and reference layers apply to the single-channel bottom viewport only | 23 new tests; three dark renders inspected; full suite 816 passed in 115 s |
| 2026-09-08 | Phase 4 audio-thread hygiene (a1790a9): level monitors store block RMS in the callback and emit from a 50 ms GUI timer; `DevicePoller` enumerates on its own thread, emits on change only, pauses during queue/review/R&D sweep (a device lost mid-sweep is reported by the stream) | 698 passed in 86 s |
