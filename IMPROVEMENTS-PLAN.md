# DMS Fastgraph Improvements Plan

This is a living working plan for improving DMS Fastgraph over the next few sessions. The priority order is intentional: measurement trust first, then architecture and tests, then workflow polish, persistence, export, and release quality.

## Fast-Track Priority Guide

For the next few sessions, optimize for faster progress on measurement reliability rather than broad app hardening.

- [ ] **[NOW] Bluetooth profile behavior** - Preserve custom standard-mode settings, make Bluetooth mode a clear profile, and keep the proven Bluetooth defaults easy to reason about.
- [ ] **[NOW] Structured retry cleanup** - Use the new failure reasons for retry decisions instead of string matching, because this is small and directly supports Bluetooth reliability.
- [ ] **[NEXT] Focused fake audio backend** - Add only enough fake audio plumbing to test queue retry behavior and diagnostics flow without real headphones.
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
- [x] **[P0] Pure measurement layout foundation** - `dms/measurement_layout.py` now builds markers, Bluetooth wake primer, excitation layout, output buffers, and expected timing positions without device or UI dependencies.
- [x] **[P0] Pure alignment foundation** - `dms/measurement_alignment.py` now handles sweep correlation, start-marker lock, paired end-marker detection, drift scoring, and SNR estimation without device or UI dependencies.
- [x] **[P0] Expanded focused measurement tests** - `tests/test_measurement_layout.py` and `tests/test_measurement_alignment.py` cover layout timing, output shape, synthetic latency/jitter, Bluetooth marker lock, clipped starts, truncated tails, confidence failures, drift rejection, SNR, sample-rate drift, retry-after-failure, and candidate choice.
- [ ] **[P1] Simple packaging scripts** - macOS and Windows build scripts exist and are easy to smoke test.

### Current Concerns

- [ ] **[P0] Bluetooth measurement reliability is the top product risk** - Wireless headphones can introduce wake-up delay, codec buffering, latency jitter, sample-rate drift, and false marker locks. The current app has some compensation, but the detection logic is still hard to reason about and hard to regression test.
- [ ] **[P1][NEXT] Automated test suite is still narrow outside measurement timing** - Focus next test expansion on settings/profile behavior and queue retry only. Defer processing, export, HRTF, and broad UI-controller coverage.
- [ ] **[P1][NEXT] Hardware-dependent logic still needs a focused simulator** - Add a minimal fake audio backend for queue/retry diagnostics. Defer a full play/record orchestration simulator.
- [x] **[P0] False marker peak regression is covered** - End-marker scoring now evaluates ordered marker pairs, so louder false marker-like peaks no longer beat the valid pair in the focused synthetic Bluetooth test.
- [ ] **[P2][DEFER] Measurement engine is improved but still concentrated** - `SweepWorker._run_inner()` still mixes I/O, progress polling, Qt signals, and error flow. Defer unless it blocks fake-audio retry tests.
- [ ] **[P2][DEFER] Main window does too much** - A controller split is valuable but large. Defer until Bluetooth measurement behavior is solid.
- [ ] **[P0][NOW] Measurement settings need profile-aware handling** - Bluetooth mode should stop overwriting custom standard-mode values permanently.
- [ ] **[P2][DEFER] Measurement sessions are not durable** - Useful for long-term workflow quality, but not needed for the immediate Bluetooth reliability push.
- [ ] **[P2][DEFER] Network upload blocks the UI** - Annoying during upload workflows, but not central to measurement trust. Defer unless users hit it often.

## P0: Measurement Trust And Bluetooth Reliability

- [x] **Extract pure measurement-building functions** - Done in `dms/measurement_layout.py`. Wake primer, start marker, end marker, excitation layout, expected timing positions, and mono/stereo output buffer construction are pure and covered by tests.
- [x] **Extract alignment and marker detection** - Done in `dms/measurement_alignment.py`. Normalized correlation, start candidate selection, marker lock, paired end-marker scoring, drift calculation, spacing validation, and SNR estimation are pure and covered by tests.
- [x] **Expand synthetic Bluetooth fixtures** - Done in `tests/test_measurement_alignment.py`. Coverage now includes random Bluetooth latency jitter, clipped/missing start audio, truncated tail audio, false marker-like peaks, codec-like ringing, sample-rate drift, and retry-after-failure behavior.
- [x] **Add acceptance tests for current failure modes** - Done for low start confidence, low end-marker confidence, short/truncated recordings, drift rejection, sample-rate drift, and retry-after-failure.
- [x] **Harden end-marker pair scoring** - Done in `dms/measurement_alignment.py`. Detection now compares ordered marker-pair candidates, prefers low timing/spacing error before raw peak strength, and rejects duplicated or reversed marker selections.
- [x] **Introduce typed diagnostics** - `MeasurementDiagnostics`, `MeasurementAlignmentError`, failure reason constants, and a formatter now expose selected start candidate, marker positions, confidence metrics, drift, SNR, Bluetooth mode, latency, thresholds, retry reason, and buffer size when available.
- [ ] **[NOW] Separate retry reason from user copy** - Structured failure reasons now exist, but the main window still uses string matching for retry eligibility. This is a small, high-leverage cleanup.
- [ ] **[NOW] Preserve custom normal-mode settings** - When Bluetooth mode is enabled, store the user's previous non-Bluetooth sweep duration, buffer, latency, silence, confidence, and drift settings. When Bluetooth mode is disabled, restore those values instead of forcing hard-coded defaults.
- [ ] **[NOW] Make Bluetooth mode a named profile** - Treat Bluetooth as a measurement profile with explicit defaults and rationale, not a scattered set of settings updates. Keep PyQt6, `sounddevice`, `numpy`/`scipy`, and `pyqtgraph` as the default stack.
- [x] **Add diagnostics UI for failed runs** - Retry prompts, final sweep errors, and successful review dialogs now include formatted diagnostics with confidence, marker positions, drift, SNR, Bluetooth mode, latency mode, thresholds, and buffer size where available.
- [ ] **[DEFER] Add a measurement debug export** - Diagnostics are visible in-app now. Defer file export until real-world failures show we need shareable debug bundles.

## P1: Architecture And Testability

- [ ] **[DEFER] Split queue orchestration from UI** - Defer the full controller extraction. If queue retry becomes hard to test, add a narrow fake-audio seam first.
- [ ] **[DEFER] Split device handling from UI** - Defer until duplicate device names or hot-plug behavior become a real blocker.
- [ ] **[DEFER] Split export/upload flow** - Defer; not measurement-critical.
- [ ] **[DEFER] Keep view composition in `MainWindow`** - Defer as part of the larger main-window split.
- [ ] **[NEXT] Add targeted `pytest` coverage across critical paths** - Cover Bluetooth profile restore, settings validation, and queue retry diagnostics first. Defer processing/export/HRTF breadth.
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
- [ ] **Session 3: Bluetooth profile behavior** - Preserve normal-mode settings, formalize measurement profiles, and move retry eligibility to structured failure reasons.
- [ ] **Session 4: Focused fake-audio retry tests** - Add minimal fake audio support only if we need confidence around queue retry behavior without real hardware.
- [ ] **Session 5: Measurement-critical settings validation** - Validate Bluetooth-critical settings and report recoverable warnings.
- [ ] **Deferred Session: Main window split** - Extract queue, device, and export/upload controllers later, after reliability stabilizes.
- [ ] **Deferred Session: Persistence and release hardening** - Save project persistence, filename/HRTF validation, upload worker, docs, CI, and release checklist for a later polish pass.

## Acceptance Criteria For This Plan

- [x] The plan uses checkboxes, priority labels, and short rationale for each item.
- [x] Each P0 item is concrete enough to implement in one or two sessions.
- [x] Bluetooth measurement reliability is explicitly identified as the top product risk.
- [x] The plan avoids speculative rewrites and keeps PyQt6, `sounddevice`, `numpy`/`scipy`, and `pyqtgraph` as the default stack.
- [x] After this file is added, run `python3 -m py_compile main.py dms/*.py dms/ui/*.py` and record the result in the working session notes. Verified on 2026-05-05.

## Assumptions

- Reliability for high-latency wireless headphones is the highest priority.
- The goal is a practical multi-session roadmap, not an immediate full rewrite.
- Completed P0 extraction work should preserve existing measurement behavior unless a later tuning slice explicitly changes thresholds, profiles, or retry policy.
- The next best technical step is Bluetooth profile behavior: preserve custom normal-mode settings, formalize Bluetooth as a named profile, and replace string-based retry matching with structured failure reasons.
- Deferred items are intentionally not abandoned; they are parked so the near-term work stays focused on reliable wireless-headphone measurement.
