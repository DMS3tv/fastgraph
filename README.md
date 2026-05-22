# DMS Fastgraph

DMS Fastgraph is a PyQt6 desktop app for headphone frequency-response
measurement. It plays log sweeps through `sounddevice`, records the fixture
response, plots live/kept curves with `pyqtgraph`, supports HRTF compensation,
and can export or upload TXT measurements for Squiglink workflows.

Current beta version: `0.2.4`

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
  evidence is still usable.
- Bluetooth mode is reversible: custom standard-mode measurement settings are
  restored when Bluetooth mode is turned off.

## Measurement TXT Import

Drop one or more local `.txt` measurement files onto the top plot to import
them as kept curves without live fixture hardware. Files should contain two
columns: frequency in Hz and magnitude in dB. Whitespace- or comma-delimited
REW-style text is accepted; comments/header rows are skipped when possible.

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

If timing is unstable, use matched input/output devices on the same backend and
try high latency mode.

## Local Beta Gate

Before packaging or tagging a beta, run:

```bash
PYTHONPATH=. .venv/bin/pytest -q
PYTHONPATH=. .venv/bin/python -m py_compile main.py dms/*.py dms/ui/*.py
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
dist\DMS Fastgraph Beta
dist\DMS Fastgraph Beta-windows-x64.zip
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
dist/DMS Fastgraph.app
```

Notes:

- The app bundle includes a microphone usage description for macOS permission prompts.
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
  "version": "0.2.5",
  "url": "https://github.com/DMS3tv/fastgraph/releases/tag/v0.2.5",
  "summary": "Minor bug fixes"
}
```
