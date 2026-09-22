# Contributing

## Setup

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

## Before every commit

```bash
ruff format dms tests tools main.py && ruff check dms tests tools main.py && python -m pytest -q -n auto
```

CI runs the same three commands (plus mypy on the core modules listed in
`pyproject.toml`) and refuses a build that fails any of them. The one-off
formatting commit is listed in `.git-blame-ignore-revs`; run
`git config blame.ignoreRevsFile .git-blame-ignore-revs` once.

## What a change has to satisfy

- **Tests for every value change on the measurement path.** If a change
  touches anything named in `docs/MEASUREMENT_DATA_PATH.md`, update that
  document and the test it cites.
- **Render and look.** A green test is not proof for UI work. Run
  `QT_QPA_PLATFORM=offscreen python tools/render_tabs.py before build/renders`
  on the old code and `... after ...` on the new, and compare the PNGs with
  `cmp` or your eyes. Theme changes must be checked in dark, FastGraph 95 and
  Dither at least.
- **Export what you display.** Exports and uploads write the curve the bottom
  plot shows, with the smoothing and level recorded in the header.
- **No band-edge rejections.** Headphones and devices roll off; a gate that
  fails on silence at 20 Hz or 20 kHz is a bug.
- **One home per helper.** Before writing a helper, grep for it. Grid and
  reference constants live in `dms/processing.py`; file writes go through
  `dms/file_io.py`; logging goes through `logging.getLogger(__name__)`.
- **Size limits.** ruff enforces a complexity of 15 and 80 statements per
  function. A flat dispatcher may carry a `noqa` with a reason.
- **Records.** `CHANGELOG.md` Unreleased gets a bullet per user-visible
  change. `docs/MASTER_PLAN.md` is the engineering progress log.

## Layout

See `docs/ARCHITECTURE.md` for the module map, the import direction, where
state lives and how to add a tab.
