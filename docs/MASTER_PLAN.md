# FastGraph Master Plan — Hardening 2026-09

Permanent record of the hardening and next-level work that started on
2026-09-08 from the audit in `FASTGRAPH_REVIEW_2026-09-08.md`. The approved
implementation plan lives outside the repo (Claude plan file); this file is
the in-repo summary and the progress log. One dated row per shipped slice.

## Phases

| # | Phase | Status |
|---|---|---|
| 0 | Records and hygiene (branch, records, .gitignore, PR #8, README fixes) | in progress |
| 1 | Test infrastructure and the widget leak (conftest, ThemeController singleton) | pending |
| 2 | Measurement integrity gate for standard mode | pending |
| 3 | Bluetooth reliability | pending |
| 4 | Measurement queue extraction and audio-thread hygiene | pending |
| 5 | Persistence and transport hardening (PR #9 themes, credited) | pending |
| 6 | Curator correctness and rendering | pending |
| 7 | Release engineering | pending |
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
