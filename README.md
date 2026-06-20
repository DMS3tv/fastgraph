# DMS Fastgraph

> **ASIO notice:** ASIO support is currently experimental and may have device
> enumeration, channel pairing, or recording issues with some drivers. For
> reliable measurements, use WASAPI on Windows unless you have verified your
> ASIO setup locally.

DMS Fastgraph is a PyQt6 desktop app for headphone frequency-response
measurement. It plays log sweeps through `sounddevice`, records the fixture
response, plots live/kept curves with `pyqtgraph`, supports HRTF compensation,
and can export or upload TXT measurements for Squiglink workflows.

Current beta version: `0.3.0`

## What's New in 0.3.0

Fastgraph now includes **Curator**, a graph image generation tool for comparing
headphone frequency-response and variation-band measurements and turning them
into presentation-ready graph images. Curator lives between the Measure and
Console tabs, shares Fastgraph's HRTF library and theme, and reports its actions
to the same diagnostic console.

The Measure tab can send its current average or variation view directly to
Curator. The transferred layer receives an editable 1 kHz offset so it sits at
0 dB without changing the source data or frequency-response shape. Any active
HRTF remains selected and can be changed or removed in Curator.

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

## Measure, Curator, and Console Tabs

The **Measure** tab contains the normal measurement interface. Its **Send to
Curator** button adds the currently selected average or variation view to the
Curator workspace and switches to that tab. The new layer is offset to 0 dB at
1 kHz without changing its source data or frequency-response shape, and its
HRTF selection remains editable.

The **Curator** tab is a graph image generation tool. It imports and compares
two-column frequency-response TXT files and six-column Fastgraph variation
exports, with per-layer visibility, color, offset, and HRTF controls. It also
supports combined variation layers, optional preference bounds, fixed graph
presentation controls, and composed 1920x1080 PNG export. Curator state is kept
only for the current application launch.

The **Console** tab shows live application, device, sweep, processing, Curator,
and timing diagnostics. It supports filtering, search, copy, and explicit log
export; console history is kept in memory only for the current launch.

Type `help` in the Console for safe Fastgraph commands. Available commands can
inspect status, devices, settings, and the latest diagnostics; apply temporary
measurement-setting overrides; start/cancel a queue; pass or fail a pending
measurement; export average/variation TXT files; and launch Squiglink upload.
Temporary settings are persisted only with `settings save`.

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

## Light and Dark Themes

Fastgraph starts in Dark mode. Use the sun/moon switch beside the feedback
button in the bottom status bar to change themes immediately. The selected
theme is saved and restored the next time Fastgraph starts.

## HRTF Files

Plain TXT, two columns: `frequency_hz  magnitude_db`
One header line is OK if it is non-numeric.
Mono files apply to both ears equally.

## Squiglink Uploads

The app can upload exported measurement TXT files over SFTP for Squiglink use.
Uploads require `Brand`, `Model`, and `Channel Side` metadata. The upload flow
can prompt for missing metadata, write side-aware TXT filenames into the remote
`data/` directory, and merge measurement entries into the account's
`data/phone_book.json` when available.

Configure the Squiglink SFTP host in the app settings file:

- macOS: `~/Library/Application Support/DMSFastgraph/settings.json`
- Windows: `%APPDATA%/DMSFastgraph/settings.json`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/DMSFastgraph/settings.json`

Example:

```json
{
  "squiglink_sftp_host": "your-sftp-host.example"
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
PYTHONPATH=. .venv/bin/python -m py_compile main.py dms/*.py dms/curator/*.py dms/ui/*.py
git status --short
```

## Packaging For Windows

Build the Windows app on a Windows machine:

```powershell
cd C:\path\to\fastgraph
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.\build_windows.ps1
```

The finished app folder and shareable zip will be created at:

```powershell
dist\FastGraph Beta
dist\FastGraph Beta-windows-x64.zip
```

## Packaging For macOS

On your Mac:

```bash
cd /path/to/fastgraph
python3 -m venv .venv
source .venv/bin/activate
./build_macos.sh
```

The finished app bundle will be created at:

```bash
dist/FastGraph Beta.app
```

Notes:

- The app bundle includes a microphone usage description for macOS permission prompts.
- Packaged apps include the shared HRTF library and Curator preference bounds.
- If Gatekeeper warns about the app because it is unsigned, right-click the app and choose `Open`.

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
  "version": "0.3.0",
  "url": "https://github.com/DMS3tv/fastgraph/releases/tag/v0.3.0",
  "summary": "Adds the integrated Curator graph image generation tool"
}
```
