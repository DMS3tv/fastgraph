# DMS Fastgraph

> **ASIO notice:** ASIO support is currently experimental and may have device
> enumeration, channel pairing, or recording issues with some drivers. For
> reliable measurements, use WASAPI on Windows unless you have verified your
> ASIO setup locally.

DMS Fastgraph is a PyQt6 desktop app for headphone frequency-response
measurement. It plays log sweeps through `sounddevice`, records the fixture
response, plots live/kept curves with `pyqtgraph`, supports HRTF compensation,
and can export or upload TXT measurements for Squiglink workflows.

## Two-Channel Measure

Enable **Two Channel** on the Measure tab to use the first two physical input
and output channels. Fastgraph treats channel 1 as `L` and channel 2 as `R`.
Each queue item measures both channels and presents the complete pair for one
Keep or Fail decision. A failed stage causes Fastgraph to discard and retry the
complete pair.

The top viewports show channel 2/R on the left and channel 1/L on the right.
The bottom result can show one Combined power average or separate R and L
results. Select a separate bottom viewport to route Export, Send to R&D, Send
to Curator, Squiglink upload, and BRAND Export All through that channel.

Use the segmented mode control to select **Frequency Response** or **Channel
Balance**. Channel Balance sends the same continuous
sine or band-limited square signal to output channels 1 and 2. It shows an L/R
overlay scope, an `L - R` scope, and live L, R, and signed level-difference
values. The generator starts at a 500 Hz sine and uses the current Measure
output level. Fastgraph stops the generator when the mode, tab, or audio device
changes and when the application closes.

Current beta version: `0.4.2`

## Release Notes

See `CHANGELOG.md` for what changed in each release.

## Quick Start

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

pip install -r requirements.txt
python main.py
```

## Measurement Behavior

- Standard mode plays a short DAC/headphone wake primer, waits 0.24 seconds,
  waits the configured pre-sweep silence, then plays the sweep. Alignment stays
  sweep-correlation based with SNR reporting; no coded timing markers or
  Bluetooth drift retries are used.
- Bluetooth Headphone Mode applies Bluetooth-safe timing defaults, keeps coded
  start/end timing markers around the sweep, reports timing diagnostics, and can
  allow marginal drift to reach review with a visible warning when marker
  evidence is still usable. If end markers are missing or weak, it can use a
  guarded sweep-correlation fallback when sweep confidence, SNR, and sweep-window
  match checks are strong enough; fallback runs are clearly marked with a warning
  before review.
- Bluetooth mode is reversible: custom standard-mode measurement settings are
  restored when Bluetooth mode is turned off.

## Measurement TXT Import

Drop one or more local `.txt` measurement files onto the top plot to import
them as kept curves without live fixture hardware. Files should contain two
columns: frequency in Hz and magnitude in dB. Whitespace- or comma-delimited
REW-style text is accepted; comments/header rows are skipped when possible.

## Measure, R&D, Curator, Automation, and Settings Tabs

The **Measure** tab contains the normal measurement interface. The animated
input glow, bottom-view controls, Undo, and the guarded Clear All action sit
between its two plots. Export directory, TXT export, Curator transfer, and the
mode-specific upload or batch-export action sit below the bottom plot. **Send
to R&D** sends one average as an ungrouped R&D measurement, or sends all kept
curves from the Var view into one Var-enabled R&D group. **Send to Curator**
adds the selected average or variation view to Curator. Both transfers keep
source data unchanged and retain active HRTF selection as an editable setting.

The tab header keeps the current headphone and rig visible alongside Headphone
Metadata, Clear Metadata, and Bluetooth mode controls. These application-wide
controls remain available while moving between Measure, R&D, Curator,
Automation, and Settings.

The **R&D** tab is a single-sweep exploratory measurement workspace. New kept
measurements default to View 1, can be shown in View 2, and can be grouped for
product-development comparisons. The measurement list uses compact View 1,
View 2, Var, and Milestone checkboxes. Groups also support notes, drag/drop
organization, optional confidence-style variation bands, and additive dB
offsets. Each measurement can choose its own HRTF from the shared HRTF library
and can be shifted with its own dB offset. R&D sessions save processed
measurement curves and workspace state to JSON; raw recordings are not saved.

In brand mode, Curator includes a **Clean Slate** option. It disables all
editable poster text and removes the header, guide box, and text footer from
the BRAND PNG. The export keeps the graph and BRAND logo. It also keeps colored
layer names when **Show Names** is enabled.

The Curator **View** and **BRAND Poster Text** sections can be collapsed below
the Data list. Curator preserves the layer-list and control-panel scroll
positions when a layer setting changes.

The R&D workspace starts with an equal list-to-viewport split at every window
size. Moving the splitter saves the new ratio for later launches.

R&D measurement, input-channel, HRTF, target, and bounds controls share a
responsive toolbar above the plots. Session and export actions sit below the
bottom viewport, and the Notes panel can be collapsed to give the measurement
list more room. The R&D and Measure input-channel selectors stay synchronized.
Fastgraph also keeps a local recovery copy after R&D changes. Recovery starts
only after the user handles any recovery data found at startup. Manual Save and
Load continue to use named `.fastgraph-rnd.json` files and their attachment
folders.

The **Curator** tab is a graph image generation tool. It imports and compares
two-column frequency-response TXT files and six-column Fastgraph variation
exports, with per-layer visibility, color, offset, and HRTF controls. It also
supports combined variation layers, optional preference bounds, fixed graph
presentation controls, and composed 1920x1080 PNG export. Curator state is kept
only for the current application launch.

TXT files can also be dropped directly onto Curator. Its right-side layer list
supports editable measurement names, while View provides fractional-octave
smoothing and an optional color-keyed name legend shared by the preview and PNG
export. Long export titles automatically shrink and elide to stay in frame.

The **Automation** tab shows live application, device, sweep, processing,
Curator, automation, and timing diagnostics in the left console pane. The right
Events pane manages local automation JSON files, row-based workflow steps, and
manual/app-event triggered runs. The Guide button swaps the console pane for an
in-app cheat-sheet and examples until it is closed. The console keeps up to
5,000 live events and writes a rotating diagnostic log to the application data
folder. The log does not include passwords, credentials, tokens, or secrets.
Use **System Info** to add the app, operating system, Python, and dependency
versions to a support report.

Type `help` in the Automation console for safe Fastgraph commands. Available commands can
inspect status, devices, settings, system information, and the latest diagnostics; apply temporary
measurement-setting overrides; start/cancel a queue; pass or fail a pending
measurement; export average/variation TXT files; and launch Squiglink upload.
Temporary settings are persisted only with `settings save`.

The **Settings** tab saves sweep and timing changes immediately and contains
SPL Calibration, Test Level, and options to restore the measurement and
metadata clearing warnings. Its controls are grouped in a compact left-aligned
column and remain visible but disabled while a measurement queue or R&D sweep is
active. Settings also includes default folders for R&D session save/load dialogs
and Automation event libraries. Keyboard shortcut mapping lives in a separate
right-side Settings column so it does not add more vertical scrolling to the
measurement settings.

Type `curator help` for the built-in command reference. Curator commands include:

```text
curator status | curator layers | curator send
curator import <path> [<path>...]
curator layer <n> show|hide|remove
curator layer <n> offset <db> | color <#RRGGBB> | hrtf <name|none>
curator combine <n> <n> [...] | curator clear
curator bounds on|off
curator view limits <min_db> <max_db> | aspect on|off
curator view background <#RRGGBB|theme>
curator text title|fixture|footer <text>
curator reset | curator export <path>
```

Layer numbers are one-based and are shown by `curator layers`. Quote paths or
text containing spaces.

## Themes

Fastgraph starts with Default dark. Open Settings to select Default dark,
Default light, FastGraph 95, FastGraph 95 Dark, or Hackerman 95. Fastgraph saves
the selection and restores it at the next start.

Developers can use [`docs/HOW_TO_ADD_THEMES.md`](docs/HOW_TO_ADD_THEMES.md) for
the theme registry, token, graph, export, test, and visual-review procedure.

## brand Mode

Enable **brand mode** in Settings to use the BRAND colors and the 3840x2160
Curator poster export. The Curator preview uses the same layout as the saved
image. BRAND mode keeps the normal dark application backdrop. BRAND panels,
controls, plots, and Curator content keep their mode-specific colors.

In the Measure tab, BRAND mode replaces the visible Squiglink button with
**Export All…**. The batch needs an average, at least two kept measurements, an
idle Measure state, and a selected HRTF. It writes four files to one directory:

- `RAW AVG`: the average without HRTF correction.
- `COMP AVG`: the average with the selected HRTF.
- `RAW VAR`: variation without HRTF correction.
- `COMP VAR`: variation with the selected HRTF.

Fastgraph checks all four names before it writes the batch. It uses one
overwrite prompt if any files already exist. Standard mode keeps Squiglink
upload in the same button position.

A clean installation starts in the standard FastGraph view. The first BRAND mode
activation on each computer requires the BRAND access password. FastGraph stores
the successful local unlock, not the password. This access check limits
accidental brand use. It does not prove that an export came from brand.

When a measurement has headphone metadata, Curator fills the poster title,
headphone details, fixture or HRTF footer, asset footer, and legend labels.
Each field remains editable. Right-click a field to restore its metadata value,
or use **Fill from Metadata** to restore all automatic fields.

Curator reduces text size when a value is too long for its poster area. It
shows a warning before export when the text must go below the preferred
readable size. BRAND mode reports missing Heading or Inconsolata fonts and
uses a visible fallback without bundling licensed font files.

## HRTF Files

Fastgraph accepts these plain TXT formats:

- Standard: `frequency_hz  magnitude_db`
- Population variation:
  `frequency_hz  p10_db  p25_db  median_db  p75_db  p90_db`

One header line is allowed if it is non-numeric. Standard mono files apply to
both ears equally. Population files produce or expand a percentile variation
band. Fastgraph uses the median line when an operation needs one compensated
frequency-response curve.

## Squiglink Uploads

The app can upload exported measurement TXT files over SFTP for Squiglink use.
Uploads require `Brand`, `Model`, and `Channel Side` metadata. The upload flow
can prompt for missing metadata, write side-aware TXT filenames into the remote
`data/` directory, and merge measurement entries into the account's
`data/phone_book.json` when available.

Squiglink accepts one channel side per file and cannot represent a combined
L/R result. A Two Channel **Combined** upload therefore carries the `L` side
and the `BOTH L` name modifier so it can be told apart from a true left-only
measurement. This is a limitation of the site, not of Fastgraph.

Configure the Squiglink SFTP host in the app settings file:

- macOS: `~/Library/Application Support/DMSFastgraph/settings.json`
- Windows: `%APPDATA%/DMSFastgraph/settings.json`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/DMSFastgraph/settings.json`

Example:

```json
{
  "squiglink_host": "your-sftp-host.example",
  "squiglink_port": 2022
}
```

## Linux: ALSA/PulseAudio/PipeWire

If `sounddevice` has no devices, install PortAudio:

```bash
# Debian/Ubuntu
sudo apt install libportaudio2 portaudio19-dev
# Arch
sudo pacman -S portaudio
```

## macOS

CoreAudio is used automatically. No extra steps.

## Windows

WASAPI is preferred for normal measurements. The app hides advanced Windows
driver entries by default, can reveal them with the `Advanced Windows Drivers`
toggle, and blocks mismatched input/output host APIs before queue start.
ASIO support is experimental and may not behave reliably across all drivers.

If timing is unstable, use matched input/output devices on the same backend and
try high latency mode.

## Local Beta Gate

Before packaging or tagging a beta, run:

```bash
PYTHONPATH=. .venv/bin/pytest -q
PYTHONPATH=. .venv/bin/python -m py_compile main.py dms/*.py dms/curator/*.py dms/rnd/*.py dms/ui/*.py
git status --short
```

## Packaging For Linux

Linux packaging is experimental. `.linux/ubuntu_deps.sh` reads `/etc/os-release`
and picks package names that exist on the detected release: the non-`t64`
library names and `libpython3.10` on Ubuntu 22.04 (which is also what CI uses),
and the `t64` variants plus the system `python3` minor version on 24.04 and
newer. Install the system packages, then run the build with its test gate:

```bash
cd /path/to/fastgraph
sudo ./.linux/ubuntu_deps.sh
./build_linux.sh --test
```

To build without running pytest, use `./build_linux.sh`. CI always builds
without `--test`, because the workflow runs the suite in a separate job first.
The finished app folder will be created at:

```bash
dist/FastGraph Beta
```

This is currently a local application folder rather than an AppImage or distro
package. Runtime measurement behavior still needs wider Linux hardware testing.

## Packaging For Windows

Build the Windows app on a Windows machine:

```powershell
cd C:\path\to\fastgraph
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\build_windows.ps1
```

The Windows build regenerates `fastgraph.ico` from the tracked
`fastgraph icon.png` source before running PyInstaller.

The finished app folder and shareable zip will be created at:

```powershell
dist\FastGraph Beta
dist\FastGraph-Beta-windows-x64.zip
```

The app folder keeps its display name, but the zip is hyphenated so release
upload and checksum tooling never has to quote a filename with a space in it.

## Packaging For macOS

On your Mac:

```bash
cd /path/to/fastgraph
python3 -m venv .venv
source .venv/bin/activate
./build_macos.sh
```

## Pinned Build Dependencies

All three build scripts install `requirements.lock`, a fully pinned environment
covering the app dependencies, the test dependencies, and PyInstaller. It is
what makes two builds of the same commit produce the same binary contents;
`requirements.txt` and `requirements-dev.txt` keep their looser ranges for
day-to-day development, and `requirements-dev.txt` pins PyInstaller exactly
because packaged output is only validated against that release.

Every package in the lock is verified to publish a CPython 3.14 wheel for
Windows x64, macOS arm64, and manylinux x86_64, so CI runs the same interpreter
as local development and nothing builds from source on a runner.

Regenerate the lock after an intentional dependency upgrade:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pip freeze > requirements.lock
```

`pip freeze` drops the comment header, so restore it afterwards.

The finished app bundle will be created at:

```bash
dist/FastGraph Beta.app
```

Both macOS and Windows builds use the tracked `fastgraph icon.png` as their
shared icon source. The macOS build converts it to an `.icns` bundle icon
automatically, while the Windows build converts it to `fastgraph.ico` before
running PyInstaller. Replace the PNG and rerun the platform build script to
package a new icon.

Notes:

- The app bundle includes a microphone usage description for macOS permission prompts.
- Packaged apps include the shared HRTF library and Curator preference bounds.
- If Gatekeeper warns about the app because it is unsigned, right-click the app and choose `Open`.

### Signing and notarization (not yet enabled)

macOS builds currently ship with an ad-hoc signature. An ad-hoc signature is
regenerated on every build, so macOS sees each release as a different
application: **the user's microphone permission is revoked and has to be granted
again after every update**, and Gatekeeper shows an unidentified-developer
warning on first launch.

The release workflow contains a commented-out `Sign and notarize macOS app`
step with the exact `codesign`, `ditto`, `xcrun notarytool submit`, and
`xcrun stapler staple` commands. Enabling it requires an Apple Developer ID
Application certificate installed in the runner keychain plus these four
repository secrets:

| Secret | Value |
| --- | --- |
| `APPLE_DEVELOPER_ID` | Developer ID Application certificate name, for example `Developer ID Application: Example Inc (ABCDE12345)` |
| `APPLE_ID` | Apple ID email used to submit for notarization |
| `APPLE_APP_PASSWORD` | App-specific password for that Apple ID |
| `APPLE_TEAM_ID` | Apple Developer team identifier |

Once signed and notarized with a stable identity, the microphone grant survives
updates and the Gatekeeper warning disappears.

## Publishing a GitHub Release

GitHub Actions builds release packages for Apple Silicon macOS, Windows x64, and
Linux x64. The workflow runs as three jobs in sequence:

1. **`test`** runs the suite on macOS, Windows, and Linux (`pytest -q -n auto`
   with `QT_QPA_PLATFORM=offscreen`). No binary is built until it passes on all
   three, so macOS and Windows packages are no longer shipped untested.
2. **`build`** needs `test`, and packages the app on each platform from
   `requirements.lock`.
3. **`publish`** needs `build`, creates or updates the release, generates
   `SHA256SUMS.txt` over every asset, and uploads the assets one at a time.

Update `dms/version.py` and `CHANGELOG.md`, push the release commit to `main`,
then choose one of these release paths:

- Push a tag such as `v0.4.2`; the workflow builds all three packages and
  publishes a GitHub release automatically.
- In **Actions → Release builds → Run workflow**, choose the source ref, enable
  **Create or update a GitHub release**, and enter a release tag such as
  `v0.4.2`. This creates the tag at the selected ref and publishes the release.

Leaving **Create or update a GitHub release** disabled runs the test and build
jobs only. Their downloadable artifacts are useful for testing before
publishing. Release notes are taken from the matching version section in
`CHANGELOG.md`; if no section exists, GitHub generates notes automatically.

Published releases carry a `SHA256SUMS.txt` asset covering every package in that
release. Verify a download against it before installing:

```bash
sha256sum -c SHA256SUMS.txt --ignore-missing
```

## Code Style

The repository is formatted and linted with ruff (pinned in
`requirements-dev.txt`); CI refuses a build that fails either check. Before
committing:

```bash
ruff format dms tests tools main.py && ruff check dms tests tools main.py && python -m pytest -q -n auto
```

The one-off formatting commit is listed in `.git-blame-ignore-revs`; run
`git config blame.ignoreRevsFile .git-blame-ignore-revs` once so `git blame`
skips it.

## Project Records

Historical engineering records that are useful when maintaining the app are
kept with the source tree:

- `docs/ARCHITECTURE.md` maps the modules, the import direction, where state
  lives and how to add a tab. `CONTRIBUTING.md` lists the checks every change
  must pass.
- `docs/MEASUREMENT_DATA_PATH.md` follows one sweep from stimulus to export
  and names every operation that changes a magnitude, with the test that pins
  it. Start there when verifying that the app does not alter a measurement.
- `docs/investigations/2026-07-08-macos-audio-freeze.md` records the evidence
  and follow-up questions from a macOS audio freeze investigation.
- `docs/reviews/2026-06-20-linux-pr-7-review.md` preserves the review of the
  original Linux build pull request. It is a historical review, not a statement
  of the current build status.

## Quiet Update Indicator

The app supports a non-intrusive update badge in the bottom-right status area.
It appears only when a newer version is found.

Add these keys to your settings file:

```json
{
  "update_check_enabled": true,
  "update_feed_url": "https://example.com/dms-fastgraph-update.json"
}
```

The feed URL should return JSON like:

```json
{
  "version": "0.4.2",
  "url": "https://github.com/DMS3tv/fastgraph/releases/tag/v0.4.2",
  "summary": "Adds paired two-channel measurement and Channel Balance"
}
```
