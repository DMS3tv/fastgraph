# DMS Fastgraph Improvements Plan

This is a living working plan for improving DMS Fastgraph over the next few sessions. The priority order is intentional: measurement trust first, then architecture and tests, then workflow polish, persistence, export, and release quality.

## Fast-Track Priority Guide

For the next few sessions, optimize for faster progress on measurement reliability rather than broad app hardening.

- [x] **[P0] Bluetooth profile behavior** - Bluetooth mode now preserves custom standard-mode settings and restores them when toggled off.
- [x] **[P0] Structured retry cleanup** - Retry decisions now use typed failure reasons when diagnostics are available, with string matching kept only as a compatibility fallback.
- [x] **[P0] Bluetooth marginal drift handling** - Bluetooth measurements with usable marker evidence and moderately high drift can now reach review with a visible warning instead of hard-failing.
- [x] **[P0] Coded multi-tone marker packets** - Start/end timing references now use broadband coded audio packets with distinct end-marker identities to reduce false locks from resonant peaks.
- [x] **[P0] Standard-mode DAC wake primer with sweep-only alignment** - Standard wired measurements play the shared wake primer before the sweep, but still omit coded timing-reference markers; sweep correlation plus SNR drive review diagnostics.
- [x] **[P0] Bluetooth sweep-correlation fallback** - Bluetooth mode now keeps marker validation when it works, but can use a guarded sweep-correlation fallback with a visible warning when end markers are missing or weak.
- [ ] **[NEXT] Real-headphone validation pass** - Test several Bluetooth headphones/IEMs against the hybrid marker/fallback alignment and record pass rate plus warning frequency before more tuning.
- [ ] **[NEXT] Focused fake audio backend** - Add only enough fake audio plumbing to test queue retry behavior and diagnostics flow without real headphones.
- [x] **[P1] Top-viewport TXT drag-drop measurement import for fixture-free testing** - Local `.txt` measurement files can now be dropped onto the top plot and imported as kept curves for testing without live fixture hardware.
- [x] **[P1] Squiglink phone_book.json sync on upload** - Upload now supports metadata-based variant naming, merges uploaded measurements into account-specific `data/phone_book.json`, and writes updated phone book back over SFTP.
- [ ] **[NEXT] Settings validation for measurement-critical fields** - Validate sweep duration, buffer, latency, silence, confidence, and drift settings before broader settings architecture work.
- [ ] **[DEFER] Large architecture split** - Main-window/controller extraction is valuable, but not needed before the Bluetooth workflow is reliable.
- [ ] **[DEFER] Persistence, release docs, upload polish, and broad CI** - Keep these visible but postpone until measurement reliability feels stable in real use.

## Review Snapshot

### App Purpose

DMS Fastgraph is a PyQt6 desktop app for taking headphone measurements. It uses `sounddevice` for playback/recording, `numpy`/`scipy` for sweep generation and DSP, `pyqtgraph` for measurement plots, and SFTP export support for Squiglink workflows.

### Current Strengths

- [ ] **[P0] Compact codebase** - The project is small enough to review and refactor incrementally without a full rewrite.
- [ ] **[P0] Clear user workflow** - Device selection, measurement queue, pass/fail review, average display, HRTF compensation, export, and upload are already connected end to end.
- [ ] **[P0] Compile-clean baseline** - `python3 -m py_compile main.py dms/*.py dms/ui/*.py` passes today.
- [ ] **[P0] Recent Bluetooth diagnostics** - The app already has Bluetooth mode, timing confidence checks, start/end markers, drift checks, retries, and SNR reporting.
- [x] **[P0] Pure measurement layout foundation** - `dms/measurement_layout.py` now builds shared wake-primer playback, sweep-only standard excitation, Bluetooth markers, excitation layout, output buffers, and expected timing positions without device or UI dependencies.
- [x] **[P0] Pure alignment foundation** - `dms/measurement_alignment.py` now handles sweep correlation, start-marker lock, paired end-marker detection, drift scoring, and SNR estimation without device or UI dependencies.
- [x] **[P0] Expanded focused measurement tests** - `tests/test_measurement_layout.py` and `tests/test_measurement_alignment.py` cover layout timing, output shape, synthetic latency/jitter, Bluetooth marker lock, clipped starts, truncated tails, confidence failures, drift rejection, SNR, sample-rate drift, retry-after-failure, and candidate choice.
- [x] **[P0] Coded marker timing references** - Marker construction now uses coded multi-tone audio packets instead of short chirps, with separate end marker A/B identities verified by pure alignment tests.
- [ ] **[P1] Simple packaging scripts** - macOS and Windows build scripts exist and are easy to smoke test.

### Current Concerns

- [ ] **[P0] Bluetooth measurement reliability is the top product risk** - Wireless headphones can introduce wake-up delay, codec buffering, latency jitter, sample-rate drift, and false marker locks. The current app has some compensation, but the detection logic is still hard to reason about and hard to regression test.
- [ ] **[P1][NEXT] Automated test suite is still narrow outside measurement timing** - Focus next test expansion on settings/profile behavior and queue retry only. Defer processing, export, HRTF, and broad UI-controller coverage.
- [ ] **[P1][NEXT] Hardware-dependent logic still needs a focused simulator** - Add a minimal fake audio backend for queue/retry diagnostics. Defer a full play/record orchestration simulator.
- [x] **[P1] Fixture-free measurement replay path exists** - Dropping one or more local `.txt` files on the top viewport now appends valid curves into `self._kept_curves`, recomputes average/variation, and updates export eligibility exactly like kept live sweeps.
- [x] **[P1] Upload catalog sync path exists** - Squiglink upload now updates the remote account's `data/phone_book.json` with brand/model merges and unique variant filenames, including fallback handling when the remote phone book is missing or invalid.
- [x] **[P0] False marker peak regression is covered** - End-marker scoring now evaluates ordered marker pairs, so louder false marker-like peaks no longer beat the valid pair in the focused synthetic Bluetooth test.
- [x] **[P0] Single-band marker artifacts are covered** - Coded marker tests now reject loud single-tone false peaks, reversed end-marker order, and duplicated same-marker end pairs.
- [ ] **[P2][DEFER] Measurement engine is improved but still concentrated** - `SweepWorker._run_inner()` still mixes I/O, progress polling, Qt signals, and error flow. Defer unless it blocks fake-audio retry tests.
- [ ] **[P2][DEFER] Main window does too much** - A controller split is valuable but large. Defer until Bluetooth measurement behavior is solid.
- [x] **[P0] Measurement settings need profile-aware handling** - Bluetooth mode now snapshots the user's standard measurement profile before applying Bluetooth defaults and restores that snapshot when disabled.
- [ ] **[P2][DEFER] Measurement sessions are not durable** - Useful for long-term workflow quality, but not needed for the immediate Bluetooth reliability push.
- [ ] **[P2][DEFER] Network upload blocks the UI** - Annoying during upload workflows, but not central to measurement trust. Defer unless users hit it often.

## P0: Measurement Trust And Bluetooth Reliability

- [x] **Extract pure measurement-building functions** - Done in `dms/measurement_layout.py`. Wake primer, start marker, end marker, excitation layout, expected timing positions, and mono/stereo output buffer construction are pure and covered by tests.
- [x] **Extract alignment and marker detection** - Done in `dms/measurement_alignment.py`. Normalized correlation, start candidate selection, marker lock, paired end-marker scoring, drift calculation, spacing validation, and SNR estimation are pure and covered by tests.
- [x] **Expand synthetic Bluetooth fixtures** - Done in `tests/test_measurement_alignment.py`. Coverage now includes random Bluetooth latency jitter, clipped/missing start audio, truncated tail audio, false marker-like peaks, codec-like ringing, sample-rate drift, and retry-after-failure behavior.
- [x] **Add acceptance tests for current failure modes** - Done for low start confidence, low end-marker confidence, short/truncated recordings, drift rejection, sample-rate drift, and retry-after-failure.
- [x] **Harden end-marker pair scoring** - Done in `dms/measurement_alignment.py`. Detection now compares ordered marker-pair candidates, prefers low timing/spacing error before raw peak strength, and rejects duplicated or reversed marker selections.
- [x] **Introduce typed diagnostics** - `MeasurementDiagnostics`, `MeasurementAlignmentError`, failure reason constants, and a formatter now expose selected start candidate, marker positions, confidence metrics, drift, SNR, Bluetooth mode, latency, thresholds, retry reason, and buffer size when available.
- [x] **[P0] Separate retry reason from user copy** - `MainWindow` now uses `MeasurementDiagnostics.failure_reason` for retry eligibility when available, with string matching retained only as a fallback for non-diagnostic errors.
- [x] **[P0] Preserve custom normal-mode settings** - Bluetooth mode now stores the user's previous non-Bluetooth sweep duration, buffer, latency, silence, confidence, and drift settings, then restores them when disabled.
- [x] **[P0] Make Bluetooth mode a named profile** - `dms/measurement_profiles.py` now defines standard and Bluetooth profile defaults plus pure snapshot/restore helpers.
- [x] **[P0] Accept marginal Bluetooth drift with warning** - Bluetooth runs with passing marker confidence, strong start-marker evidence, reasonable marker spacing, and drift up to 160 ms now continue to review with a `bluetooth_marginal_drift` warning.
- [x] **[P0] Replace chirp timing markers with coded multi-tone packets** - `dms/measurement_layout.py` now builds deterministic coded audio markers, and `dms/measurement_alignment.py` verifies end marker A/B identity plus chip-level agreement before accepting marker pairs.
- [x] **[P0] Remove wired-mode timing references** - Standard wired playback now uses the shared DAC/headphone wake primer before pre-silence and sweep-only excitation. Bluetooth mode keeps coded markers, drift validation, retries, and warning diagnostics.
- [x] **[P0] Add guarded Bluetooth sweep fallback** - Bluetooth mode now accepts a sweep-correlation fallback when marker evidence is missing or weak but sweep confidence, SNR, non-silent level, and sweep-window match are usable. Strong invalid marker identity or strong excessive drift still fail.
- [x] **Add diagnostics UI for failed runs** - Retry prompts, final sweep errors, and successful review dialogs now include formatted diagnostics with confidence, marker positions, drift, SNR, Bluetooth mode, latency mode, thresholds, and buffer size where available.
- [ ] **[DEFER] Add a measurement debug export** - Diagnostics are visible in-app now. Defer file export until real-world failures show we need shareable debug bundles.

## P1: Architecture And Testability

- [ ] **[DEFER] Split queue orchestration from UI** - Defer the full controller extraction. If queue retry becomes hard to test, add a narrow fake-audio seam first.
- [ ] **[DEFER] Split device handling from UI** - Defer until duplicate device names or hot-plug behavior become a real blocker.
- [ ] **[DEFER] Split export/upload flow** - Defer; not measurement-critical.
- [ ] **[DEFER] Keep view composition in `MainWindow`** - Defer as part of the larger main-window split.
- [ ] **[NEXT] Add targeted `pytest` coverage across critical paths** - Cover Bluetooth profile restore, settings validation, and queue retry diagnostics first. Defer processing/export/HRTF breadth.
- [x] **[P1] Added targeted tests for drag-drop measurement import and parser behavior** - `tests/test_measurement_txt.py` and `tests/test_main_window_measurement_import.py` cover permissive parsing, sorted/positive frequency normalization, mixed valid/invalid imports, and busy-state blocking.
- [x] **[P1] Added targeted tests for Squiglink phone book merge and fallback behavior** - `tests/test_squiglink_phone_book.py` and `tests/test_main_window_squiglink_phone_book.py` cover upload name stems, string/list file normalization, unique variant appends, new brand/model entry creation, and missing/invalid remote phone-book fallback branches.
- [ ] **[NEXT] Add a minimal fake audio backend** - Create only the seam needed to simulate play/record success, timing failure, retry, and diagnostics emission.
- [ ] **[NEXT] Add typed measurement-setting validation** - Start with defaults/ranges for Bluetooth-critical settings only. Defer a full settings schema/migration system.
- [ ] **[DEFER] Reduce silent exception handling broadly** - Fix only measurement/settings failures for now. Defer calibration/export/upload cleanup.
- [ ] **[DEFER] Add lightweight CI checks** - Useful before release, but local compile/tests are enough while iterating quickly.

## P2: Workflow, Data, And Release Quality

- [ ] **[DEFER] Add session/project save-load** - Valuable for workflow, but postpone until measurement acceptance is more stable.
- [ ] **[DEFER] Add autosave or recovery** - Defer with session/project persistence.
- [ ] **[DEFER] Sanitize export filenames** - Small and worthwhile, but not part of the current reliability push.
- [ ] **[DEFER] Validate HRTF files** - Defer unless HRTF import errors become common.
- [ ] **[DEFER] Move Squiglink upload off the UI thread** - Defer unless upload freezes interrupt active measurement sessions.
- [ ] **[DEFER] Improve device identity persistence** - Defer unless duplicate device names or device reordering cause bad selections.
- [ ] **[DEFER] Improve calibration durability** - Defer until calibration workflow gets a dedicated pass.
- [ ] **[DEFER] Update README and build docs** - Save for release hardening.
- [ ] **[DEFER] Add release checklist** - Save for release hardening.

## Suggested Session Order

- [x] **Session 1A: Measurement layout extraction** - Completed. `MeasurementSignalLayout` and pure output construction make excitation timing explicit.
- [x] **Session 1B: Alignment and marker extraction** - Completed. `AlignmentSettings`, `StartAlignmentResult`, `EndMarkerResult`, and `MeasurementAlignmentResult` now support pure synthetic tests.
- [x] **Session 1C: Expanded synthetic Bluetooth fixtures** - Completed. Added broader failure fixtures for jitter, truncated tails, clipped starts, false peaks, codec ringing, sample-rate drift, and retry-after-failure.
- [x] **Session 1D: Marker scoring and drift robustness** - Completed. The false-marker `xfail` is now a passing regression test, and ordered pair scoring protects against loud false, reversed, or duplicated marker artifacts.
- [x] **Session 2: Diagnostics object and UI details** - Completed. Alignment success/failure now emits structured diagnostics, and the UI surfaces those details without changing retry behavior.
- [x] **Session 3: Bluetooth profile behavior** - Completed. Normal-mode settings are preserved/restored around Bluetooth mode, Bluetooth defaults live in a pure profile helper, and retry eligibility now prefers structured failure reasons.
- [x] **Session 4: Bluetooth marginal drift handling** - Completed. Moderately high Bluetooth drift can now be reviewed with a warning when marker evidence is still usable.
- [x] **Session 5: Coded multi-tone marker packets** - Completed. Timing markers are now coded broadband packets, end marker A/B identity is checked, and tests cover single-tone false peaks plus reversed/duplicated marker artifacts.
- [x] **Session 5B: Top-viewport TXT drag-drop import for fixture-free testing** - Completed on 2026-05-06. Added top-plot drop handling in `dms/ui/dual_plot_widget.py`, a reusable permissive two-column loader in `dms/measurement_txt.py` (also used by `dms/hrtf.py`), import orchestration in `dms/ui/main_window.py`, and focused tests in `tests/test_measurement_txt.py` plus `tests/test_main_window_measurement_import.py`.
- [x] **Session 5C: Squiglink phone-book sync on upload** - Completed on 2026-05-06. Added upload name modifier UI, metadata-aware upload filename stems, SFTP read/merge/write flow for `data/phone_book.json`, and fallback choices for missing/invalid remote phone books.
- [x] **Session 5D: Remove wired-mode timing references** - Completed on 2026-05-08 and updated on 2026-05-22. Standard wired sweeps use a wake primer before configured silence, then sweep-only excitation and sweep alignment/SNR without marker drift retries; Bluetooth mode keeps the coded timing-marker path.
- [x] **Session 6: Bluetooth hybrid sweep fallback** - Completed on 2026-05-24. Bluetooth mode now uses marker validation when reliable and falls back to guarded sweep correlation when end markers are missing/weak, surfacing `bluetooth_sweep_fallback` diagnostics for review.
- [ ] **Session 6B: Real-headphone validation pass** - Next recommended session. Use multiple wireless headphones/IEMs and compare pass rate, warning frequency, and curve repeatability before changing marker gaps or profile timing again.
- [ ] **Session 7: Focused fake-audio retry tests** - Add minimal fake audio support only if we need confidence around queue retry behavior without real hardware.
- [ ] **Session 8: Measurement-critical settings validation** - Validate Bluetooth-critical settings and report recoverable warnings.
- [ ] **Deferred Session: Main window split** - Extract queue, device, and export/upload controllers later, after reliability stabilizes.
- [ ] **Deferred Session: Persistence and release hardening** - Save project persistence, filename/HRTF validation, upload worker, docs, CI, and release checklist for a later polish pass.

## Acceptance Criteria For This Plan

- [x] The plan uses checkboxes, priority labels, and short rationale for each item.
- [x] Each P0 item is concrete enough to implement in one or two sessions.
- [x] Bluetooth measurement reliability is explicitly identified as the top product risk.
- [x] The plan avoids speculative rewrites and keeps PyQt6, `sounddevice`, `numpy`/`scipy`, and `pyqtgraph` as the default stack.
- [x] After this file is added, run `python3 -m py_compile main.py dms/*.py dms/ui/*.py` and record the result in the working session notes. Verified on 2026-05-05.

## Working Session Notes

- 2026-05-06: Bumped app version from `0.1.3` to `0.2.0` in `dms/version.py`.
- Versioning guidance for future workers: when a session lands notable user-facing behavior changes, increment the app version in `dms/version.py` and add a short note in this plan so release intent stays visible.
- 2026-05-06: Implemented fixture-free measurement import by drag-dropping local `.txt` files onto the top viewport.
- Import rules: accept local `.txt` only; parse permissive two-column REW-style text (whitespace/comma delimiters, header/comment skipping); require at least 2 valid rows and at least 2 positive-frequency rows; sort ascending by frequency before use.
- Runtime behavior: import is allowed only while app state is idle; valid files append to kept curves and trigger recompute/update; mixed drops show warnings and keep partial success.
- Implementation files: `dms/ui/dual_plot_widget.py`, `dms/ui/main_window.py`, `dms/measurement_txt.py`, `dms/hrtf.py`, `tests/test_measurement_txt.py`, `tests/test_main_window_measurement_import.py`.
- Verification on 2026-05-06:
  - `PYTHONPATH=. .venv/bin/pytest -q tests/test_measurement_txt.py tests/test_main_window_measurement_import.py` -> `5 passed`
  - `PYTHONPATH=. .venv/bin/python -m py_compile main.py dms/*.py dms/ui/*.py` -> pass
- 2026-05-06: Implemented Squiglink phone-book sync during upload.
- Upload naming behavior (final): local export naming is unchanged; Squiglink upload writes measurement TXT files into `data/` and uses `Brand Model ModifierOrSide.txt`, where side defaults from metadata (`L`/`R`) when modifier is blank, and unit variants can use `L1/L2...` or `R1/R2...`.
- Metadata requirement for upload naming: upload now blocks until required `Brand`, `Model`, and `Channel Side` (`L`/`R`) are set, and the upload gate popup lets users fill those three fields inline before continuing.
- Phone-book naming behavior (final): phone-book `file` entries intentionally omit terminal side/unit suffixes (`L`, `R`, `L1`, `R2`, etc.) so upload filenames and phone-book entries remain related but not identical; side-specific suffixes are kept only in the TXT filename.
- Phone-book sync behavior: after TXT upload, app reads `data/phone_book.json` over SFTP, merges by normalized brand/model (`strip + casefold`), normalizes `file` values to list form, appends unique phone-book stems, preserves existing metadata/extra keys, and writes back to `data/phone_book.json` (overwrite on conflict).
- Missing/invalid remote phone-book behavior: app now prompts user to choose one of three deterministic branches: create fresh phone book and continue, fail upload, or upload measurement only and skip phone-book sync.
- Upload dialog copy polish: `Name Modifier` now shows helper text ("Optional. Type here if you're using different tips, pads, EQ modes, etc") and no placeholder example text.
- Implementation files: `dms/ui/main_window.py`, `dms/squiglink.py`, `tests/test_squiglink_phone_book.py`, `tests/test_main_window_squiglink_phone_book.py`.
- Verification on 2026-05-06:
  - `PYTHONPATH=. .venv/bin/pytest -q tests/test_squiglink_phone_book.py tests/test_main_window_squiglink_phone_book.py` -> `8 passed`
  - `PYTHONPATH=. .venv/bin/python -m py_compile main.py dms/*.py dms/ui/*.py` -> pass
- Worker/model context for this change: `gpt-5.3-codex`, reasoning effort `medium`.
- 2026-05-07: Updated Squiglink upload metadata guard.
- Upload metadata gate behavior (current): `Upload to Squiglink` no longer proceeds when `Brand`, `Model`, or `Channel Side` are missing; instead of a side-only warning, it now opens a compact popup that collects those three required fields and then continues upload.
- Implementation files: `dms/ui/main_window.py`, `tests/test_main_window_squiglink_phone_book.py`.
- Verification on 2026-05-07:
  - `PYTHONPATH=. .venv/bin/pytest -q tests/test_squiglink_phone_book.py tests/test_main_window_squiglink_phone_book.py` -> `14 passed`
- 2026-05-07: Bumped app version from `0.2.0` to `0.2.1` for the Windows duplicate audio device selection hotfix.
- Windows audio device selection hotfix: device selectors now persist PortAudio index plus host API metadata and pass numeric device indices to `sounddevice`, preventing ambiguous duplicate-name failures across MME/DirectSound/WASAPI.
- Implementation files: `dms/audio_engine.py`, `dms/ui/main_window.py`, `dms/ui/calibration_dialog.py`, `tests/test_audio_devices.py`, `tests/test_main_window_audio_devices.py`.
- Verification on 2026-05-07:
  - `PYTHONPATH=. .venv/bin/pytest -q` -> `82 passed`
  - `PYTHONPATH=. .venv/bin/python -m py_compile main.py dms/*.py dms/ui/*.py` -> pass
- 2026-05-07: Bumped app version from `0.2.1` to `0.2.2` for the Windows audio pairing and timing stability hotfix.
- Windows audio pairing behavior: normal Windows device lists now prefer a matched WASAPI backend, advanced driver entries are hidden behind an `Advanced Windows Drivers` toggle, mismatched input/output host APIs are blocked before queue start, and non-Bluetooth Windows sweeps default to high latency unless the user explicitly saves a latency choice.
- Implementation files: `dms/audio_engine.py`, `dms/ui/main_window.py`, `dms/ui/settings_dialog.py`, `dms/settings_manager.py`, `tests/test_audio_devices.py`, `tests/test_main_window_audio_devices.py`.
- Verification on 2026-05-07:
  - `PYTHONPATH=. .venv/bin/pytest -q` -> `89 passed`
  - `PYTHONPATH=. .venv/bin/python -m py_compile main.py dms/*.py dms/ui/*.py` -> pass
- 2026-05-08: Bumped app version from `0.2.2` to `0.2.3` for the standard wired sweep-only playback change.
- Standard wired measurement behavior: non-Bluetooth playback no longer includes wake primer, coded start marker, coded end-marker pair, or marker gaps. Standard alignment uses sweep correlation and SNR only, surfaces "Sweep Quality" in review/status UI, and does not trigger timing-quality retries from marker confidence or drift.
- Bluetooth behavior remains marker-based: Bluetooth mode still uses primer, coded timing references, marker identity checks, drift validation, retry prompts, and marginal-drift warnings.
- Implementation files: `dms/measurement_layout.py`, `dms/measurement_alignment.py`, `dms/ui/main_window.py`, `dms/ui/settings_dialog.py`, `tests/test_measurement_layout.py`, `tests/test_measurement_alignment.py`.
- Verification on 2026-05-08:
  - `PYTHONPATH=. .venv/bin/pytest -q tests/test_measurement_layout.py tests/test_measurement_alignment.py` -> `53 passed`
  - `PYTHONPATH=. .venv/bin/pytest -q` -> `92 passed`
  - `PYTHONPATH=. .venv/bin/python -m py_compile main.py dms/*.py dms/ui/*.py` -> pass
- 2026-05-22: Bumped app version from `0.2.3` to `0.2.4` for the standard-mode DAC wake-primer beta gate.
- Standard wired measurement behavior: non-Bluetooth playback now starts with the same wake primer and 0.24 s primer gap used by Bluetooth mode, then the configured pre-sweep silence and sweep-only excitation. Standard alignment remains sweep-correlation/SNR based with no coded marker drift checks or Bluetooth retry logic.
- Bluetooth behavior remains marker-based: Bluetooth mode still uses the shared primer, coded start/end timing references, marker identity checks, drift validation, retry prompts, and marginal-drift warnings.
- Implementation files: `dms/measurement_layout.py`, `tests/test_measurement_layout.py`, `dms/version.py`, `IMPROVEMENTS-PLAN.md`.
- 2026-05-24: Bumped app version from `0.2.4` to `0.2.5` for the Bluetooth hybrid sweep fallback reliability pass.
- Bluetooth measurement behavior: marker-validated Bluetooth sweeps still use the existing coded start/end marker path. When end markers are missing or weak, Bluetooth mode can now accept a guarded sweep-correlation fallback if sweep confidence is at least `3.0`, start evidence is usable, SNR is at least `18 dB`, the aligned sweep is non-silent, and the sweep window still correlates with the source sweep. Strong invalid marker identity or strong excessive marker drift still fail instead of falling back.
- Diagnostics behavior: accepted fallback runs now show warning reason `bluetooth_sweep_fallback`, alignment mode `sweep_fallback`, and the marker failure reason that caused fallback. Marker-validated Bluetooth runs report alignment mode `marker`; standard sweeps report alignment mode `sweep`.
- Implementation files: `dms/measurement_alignment.py`, `tests/test_measurement_alignment.py`, `dms/version.py`, `IMPROVEMENTS-PLAN.md`.
- Verification on 2026-05-24:
  - `PYTHONPATH=. .venv/bin/pytest -q tests/test_measurement_alignment.py` -> `44 passed`
  - `PYTHONPATH=. .venv/bin/pytest -q tests/test_measurement_layout.py tests/test_measurement_alignment.py` -> `55 passed`
  - `PYTHONPATH=. .venv/bin/pytest -q` -> `94 passed`
  - `PYTHONPATH=. .venv/bin/python -m py_compile main.py dms/*.py dms/ui/*.py` -> pass
- 2026-06-06: Bumped app version from `0.2.5` to `0.2.6` for variation-band export.
- Variation export behavior: when the bottom viewport is in `Variation Band` mode, the export button changes to `Export Variation...` and writes a tab-delimited TXT file with frequency, p10, p25, median, p75, and p90 columns matching the displayed HRTF/smoothed variation band. Squiglink upload remains average-only.
- Implementation files: `dms/export.py`, `dms/ui/main_window.py`, `tests/test_export.py`, `tests/test_main_window_variation_export.py`, `dms/version.py`, `README.md`, `IMPROVEMENTS-PLAN.md`.
- Verification on 2026-06-06:
  - `PYTHONPATH=. .venv/bin/pytest -q` -> `101 passed`
  - `PYTHONPATH=. .venv/bin/python -m py_compile main.py dms/*.py dms/ui/*.py` -> pass

## Assumptions

- Reliability for high-latency wireless headphones is the highest priority.
- The goal is a practical multi-session roadmap, not an immediate full rewrite.
- Completed P0 extraction work should preserve existing measurement behavior unless a later tuning slice explicitly changes thresholds, profiles, or retry policy.
- The next best step is real Bluetooth/IEM validation with the coded markers; after that, add fake-audio retry tests if queue/retry behavior still needs non-hardware coverage.
- Deferred items are intentionally not abandoned; they are parked so the near-term work stays focused on reliable wireless-headphone measurement.
