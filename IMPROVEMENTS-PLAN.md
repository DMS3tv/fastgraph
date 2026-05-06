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
- [ ] **[P1] Simple packaging scripts** - macOS and Windows build scripts exist and are easy to smoke test.

### Current Concerns

- [ ] **[P0] Bluetooth measurement reliability is the top product risk** - Wireless headphones can introduce wake-up delay, codec buffering, latency jitter, sample-rate drift, and false marker locks. The current app has some compensation, but the detection logic is still hard to reason about and hard to regression test.
- [ ] **[P0] No automated test suite** - The most important DSP, alignment, settings, export, and retry logic currently depends on manual testing with real hardware.
- [ ] **[P0] Hardware-dependent logic has no simulator** - There is no fake audio backend or synthetic recording harness for repeatable latency/jitter cases.
- [ ] **[P1] Measurement engine is too concentrated** - `SweepWorker._run_inner()` mixes sweep construction, device I/O, alignment, marker detection, validation, diagnostics, and user-facing error text.
- [ ] **[P1] Main window does too much** - `MainWindow` handles UI construction, queue state, measurement orchestration, device monitoring, export, upload, settings, metadata, HRTF, calibration, update checks, and error prompts.
- [ ] **[P1] Settings are weakly typed** - Settings load/save silently ignores failures, has no schema validation, and Bluetooth mode overwrites normal-mode values with hard-coded defaults.
- [ ] **[P2] Measurement sessions are not durable** - Metadata and kept curves are in memory only, so a queue cannot be resumed after restart or crash.
- [ ] **[P2] Network upload blocks the UI** - Squiglink upload runs synchronously from the main window.

## P0: Measurement Trust And Bluetooth Reliability

- [ ] **Extract pure measurement-building functions** - Move wake primer, start marker, end markers, sweep envelope, and excitation layout into testable functions that return both audio and expected timing positions. This makes marker positions explicit and removes guesswork from `SweepWorker`.
- [ ] **Extract alignment and marker detection** - Move normalized correlation, start candidate selection, end marker scoring, drift calculation, spacing validation, and SNR estimation into pure functions. Keep `SweepWorker` responsible for device I/O and orchestration only.
- [ ] **Add synthetic Bluetooth fixtures** - Build repeatable test recordings for fixed latency, random latency jitter, missing start audio, truncated tail audio, low SNR, false marker peaks, codec-like ringing, and sample-rate drift.
- [ ] **Add acceptance tests for current failure modes** - Cover low start-alignment confidence, low end-marker confidence, aligned recording shorter than expected, drift spikes around 100-150 ms, and successful retry after a bad first run.
- [ ] **Introduce typed diagnostics** - Replace float-only timing signals with a `TimingQuality` or `MeasurementDiagnostics` object containing selected start candidate, marker positions, confidence metrics, drift, SNR, retry reason, selected measurement profile, and whether Bluetooth mode was active.
- [ ] **Separate retry reason from user copy** - Use structured failure reasons internally, then map them to friendly UI messages. This prevents fragile string matching for retry behavior.
- [ ] **Preserve custom normal-mode settings** - When Bluetooth mode is enabled, store the user's previous non-Bluetooth sweep duration, buffer, latency, silence, confidence, and drift settings. When Bluetooth mode is disabled, restore those values instead of forcing hard-coded defaults.
- [ ] **Make Bluetooth mode a named profile** - Treat Bluetooth as a measurement profile with explicit defaults and rationale, not a scattered set of settings updates. Keep PyQt6, `sounddevice`, `numpy`/`scipy`, and `pyqtgraph` as the default stack.
- [ ] **Add diagnostics UI for failed runs** - Add an expandable error-details area showing start confidence, marker confidence, drift, SNR, candidate timing, active profile, buffer size, latency mode, sweep duration, and retry count.
- [ ] **Add a measurement debug export** - Add an optional debug export for failed runs containing enough metadata to reproduce alignment decisions without sharing private user settings.

## P1: Architecture And Testability

- [ ] **Split queue orchestration from UI** - Extract queue state, retry counting, keep/fail/cancel transitions, and progress updates into a small controller that can be tested without rendering the main window.
- [ ] **Split device handling from UI** - Extract device enumeration, selected device persistence, channel refresh, and hot-plug handling behind a small service with fake-device tests.
- [ ] **Split export/upload flow** - Move export path selection, filename construction, temporary-file handling, and Squiglink upload orchestration away from `MainWindow`.
- [ ] **Keep view composition in `MainWindow`** - Let `MainWindow` assemble widgets and route signals, while controllers own behavior.
- [ ] **Add `pytest`** - Cover `processing.py`, export filename/header behavior, HRTF file loading, settings migration/validation, measurement alignment, and queue retry behavior.
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

- [ ] **Session 1: Measurement extraction and synthetic fixtures** - Extract pure timing/alignment functions and add tests for the Bluetooth failure cases already observed.
- [ ] **Session 2: Diagnostics object and UI details** - Replace float-only timing signals with structured diagnostics and expose useful failure details in the UI.
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
- The first version of this plan should add only this markdown file and should not modify repo-tracked application code.
