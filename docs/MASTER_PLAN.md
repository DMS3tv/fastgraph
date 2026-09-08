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
| 4 | Measurement queue extraction and audio-thread hygiene | pending |
| 5 | Persistence and transport hardening (PR #9 themes, credited) | 5a done 2026-09-08; 5b (R&D, automation editor) pending |
| 6 | Curator correctness and rendering | done 2026-09-08 |
| 7 | Release engineering | done 2026-09-08 (signing slot documented, not enabled) |
| 8 | DSP upgrade (deconvolution, distortion, band averaging, SPL) | pending |
| 9 | Measure sessions and comparison features | pending |
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
