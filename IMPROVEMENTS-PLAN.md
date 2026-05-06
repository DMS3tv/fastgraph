# DMS Fastgraph Improvements Plan

This is a living working plan for improving DMS Fastgraph over the next few sessions. The priority order is intentional: measurement trust first, then architecture and tests, then workflow polish, persistence, export, and release quality.

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
- [ ] **[P0] Automated test suite is still narrow outside measurement timing** - The repo now has focused measurement layout/alignment tests, but processing, settings, export, HRTF, queue retry, fake-device, and UI-controller paths still need coverage.
- [ ] **[P0] Hardware-dependent logic still needs a broader simulator** - Synthetic recording tests exist for alignment, but there is no fake audio backend for queue/retry behavior or full play/record orchestration.
- [x] **[P0] False marker peak regression is covered** - End-marker scoring now evaluates ordered marker pairs, so louder false marker-like peaks no longer beat the valid pair in the focused synthetic Bluetooth test.
- [ ] **[P1] Measurement engine is improved but still concentrated** - Sweep construction and alignment have been extracted, but `SweepWorker._run_inner()` still mixes device I/O, progress polling, validation, Qt signals, and user-facing error flow.
- [ ] **[P1] Main window does too much** - `MainWindow` handles UI construction, queue state, measurement orchestration, device monitoring, export, upload, settings, metadata, HRTF, calibration, update checks, and error prompts.
- [ ] **[P1] Settings are weakly typed** - Settings load/save silently ignores failures, has no schema validation, and Bluetooth mode overwrites normal-mode values with hard-coded defaults.
- [ ] **[P2] Measurement sessions are not durable** - Metadata and kept curves are in memory only, so a queue cannot be resumed after restart or crash.
- [ ] **[P2] Network upload blocks the UI** - Squiglink upload runs synchronously from the main window.

## P0: Measurement Trust And Bluetooth Reliability

- [x] **Extract pure measurement-building functions** - Done in `dms/measurement_layout.py`. Wake primer, start marker, end marker, excitation layout, expected timing positions, and mono/stereo output buffer construction are pure and covered by tests.
- [x] **Extract alignment and marker detection** - Done in `dms/measurement_alignment.py`. Normalized correlation, start candidate selection, marker lock, paired end-marker scoring, drift calculation, spacing validation, and SNR estimation are pure and covered by tests.
- [x] **Expand synthetic Bluetooth fixtures** - Done in `tests/test_measurement_alignment.py`. Coverage now includes random Bluetooth latency jitter, clipped/missing start audio, truncated tail audio, false marker-like peaks, codec-like ringing, sample-rate drift, and retry-after-failure behavior.
- [x] **Add acceptance tests for current failure modes** - Done for low start confidence, low end-marker confidence, short/truncated recordings, drift rejection, sample-rate drift, and retry-after-failure.
- [x] **Harden end-marker pair scoring** - Done in `dms/measurement_alignment.py`. Detection now compares ordered marker-pair candidates, prefers low timing/spacing error before raw peak strength, and rejects duplicated or reversed marker selections.
- [x] **Introduce typed diagnostics** - `MeasurementDiagnostics`, `MeasurementAlignmentError`, failure reason constants, and a formatter now expose selected start candidate, marker positions, confidence metrics, drift, SNR, Bluetooth mode, latency, thresholds, retry reason, and buffer size when available.
- [ ] **Separate retry reason from user copy** - Structured failure reasons now exist, but the main window still uses string matching for retry eligibility. Replace that with diagnostics reasons in a later cleanup slice.
- [ ] **Preserve custom normal-mode settings** - When Bluetooth mode is enabled, store the user's previous non-Bluetooth sweep duration, buffer, latency, silence, confidence, and drift settings. When Bluetooth mode is disabled, restore those values instead of forcing hard-coded defaults.
- [ ] **Make Bluetooth mode a named profile** - Treat Bluetooth as a measurement profile with explicit defaults and rationale, not a scattered set of settings updates. Keep PyQt6, `sounddevice`, `numpy`/`scipy`, and `pyqtgraph` as the default stack.
- [x] **Add diagnostics UI for failed runs** - Retry prompts, final sweep errors, and successful review dialogs now include formatted diagnostics with confidence, marker positions, drift, SNR, Bluetooth mode, latency mode, thresholds, and buffer size where available.
- [ ] **Add a measurement debug export** - Add an optional debug export for failed runs containing enough metadata to reproduce alignment decisions without sharing private user settings.

## P1: Architecture And Testability

- [ ] **Split queue orchestration from UI** - Extract queue state, retry counting, keep/fail/cancel transitions, and progress updates into a small controller that can be tested without rendering the main window.
- [ ] **Split device handling from UI** - Extract device enumeration, selected device persistence, channel refresh, and hot-plug handling behind a small service with fake-device tests.
- [ ] **Split export/upload flow** - Move export path selection, filename construction, temporary-file handling, and Squiglink upload orchestration away from `MainWindow`.
- [ ] **Keep view composition in `MainWindow`** - Let `MainWindow` assemble widgets and route signals, while controllers own behavior.
- [ ] **Add `pytest` coverage across the app** - `pytest` has been added as dev tooling in `requirements-dev.txt`, and measurement layout/alignment tests exist. Still cover `processing.py`, export filename/header behavior, HRTF file loading, settings migration/validation, and queue retry behavior.
- [ ] **Add a fake audio backend** - Create an interface around `sounddevice.playrec`, `sounddevice.play`, and `InputStream` so tests can run without real headphones, microphones, or audio permissions.
- [ ] **Add typed settings definitions** - Centralize defaults, allowed ranges, migrations, and validation. Invalid settings should fall back safely and report a recoverable warning instead of failing silently.
- [ ] **Reduce silent exception handling** - Replace broad `except/pass` blocks with logged or surfaced errors where they affect measurement reliability, settings persistence, calibration, export, upload, or device state.
- [ ] **Add lightweight CI checks** - Run `py_compile`, unit tests, lint/type checks, and packaging smoke checks on every PR or release branch.

## P2: Workflow, Data, And Release Quality

- [ ] **Add session/project save-load** - Persist metadata, kept curves, average state, HRTF choice, measurement settings profile, diagnostics, and export directory so a measurement session can be reopened.
- [ ] **Add autosave or recovery** - Store enough queue progress to recover from accidental close, device failure, or crash during long wireless-headphone test sessions.
- [ ] **Sanitize export filenames** - Remove or replace characters that are unsafe on Windows/macOS/Linux and prevent accidental path issues from metadata.
- [ ] **Validate HRTF files** - Check for sorted frequencies, duplicate frequencies, non-finite values, and out-of-band coverage. Show actionable import errors.
- [ ] **Move Squiglink upload off the UI thread** - Use a background worker with progress, cancellation, timeout handling, and clearer authentication errors.
- [ ] **Improve device identity persistence** - Store device name plus host API, device index, channel counts, and last-seen metadata so duplicate names and device reorderings are less risky.
- [ ] **Improve calibration durability** - Record calibration date, sample rate, channel, device identity, reference SPL, and notes, not just Pa/FS sensitivity.
- [ ] **Update README and build docs** - Fix the stale macOS path, document setup/build/test commands, and add a hardware smoke-test checklist for wired and Bluetooth measurement paths.
- [ ] **Add release checklist** - Include version bump, compile/test pass, package build, launch smoke test, device permission check, export check, and Squiglink upload check.

## Suggested Session Order

- [x] **Session 1A: Measurement layout extraction** - Completed. `MeasurementSignalLayout` and pure output construction make excitation timing explicit.
- [x] **Session 1B: Alignment and marker extraction** - Completed. `AlignmentSettings`, `StartAlignmentResult`, `EndMarkerResult`, and `MeasurementAlignmentResult` now support pure synthetic tests.
- [x] **Session 1C: Expanded synthetic Bluetooth fixtures** - Completed. Added broader failure fixtures for jitter, truncated tails, clipped starts, false peaks, codec ringing, sample-rate drift, and retry-after-failure.
- [x] **Session 1D: Marker scoring and drift robustness** - Completed. The false-marker `xfail` is now a passing regression test, and ordered pair scoring protects against loud false, reversed, or duplicated marker artifacts.
- [x] **Session 2: Diagnostics object and UI details** - Completed. Alignment success/failure now emits structured diagnostics, and the UI surfaces those details without changing retry behavior.
- [ ] **Session 3: Bluetooth profile behavior** - Preserve normal-mode settings, formalize measurement profiles, and tune Bluetooth defaults using synthetic and hardware results.
- [ ] **Session 4: Main window split** - Extract queue, device, and export/upload controllers while preserving existing UI behavior.
- [ ] **Session 5: Persistence and release hardening** - Add project save-load, filename/HRTF validation, upload worker, README updates, and CI/release checklist.

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
- The next best technical step is Bluetooth profile behavior: preserve custom normal-mode settings, formalize Bluetooth as a named profile, and eventually replace string-based retry matching with structured failure reasons.
