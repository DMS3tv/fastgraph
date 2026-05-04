# DMS Fastgraph

## Setup

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

pip install -r requirements.txt
python main.py
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

WASAPI or MME backends are used. No extra steps.

## HRTF Files

Plain TXT, two columns: `frequency_hz  magnitude_db`  
One header line is OK (will be skipped if non-numeric).  
Mono file applies to both ears equally.

## Quiet Update Indicator (No Popups)

The app supports a non-intrusive update badge in the bottom-right status area.
It only appears when a newer version is found.

Add these keys to your config file at:

- macOS: `~/Library/Application Support/DMSFastgraph/settings.json`
- Windows: `%APPDATA%/DMSFastgraph/settings.json`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/DMSFastgraph/settings.json`

Example:

```json
{
  "update_check_enabled": true,
  "update_feed_url": "https://example.com/dms-fastgraph-update.json"
}
```

The feed URL should return JSON like:

```json
{
  "version": "1.0.1",
  "url": "https://github.com/your-org/dms-fastgraph/releases/tag/v1.0.1",
  "summary": "Minor bug fixes"
}
```

## Packaging For macOS

You must build the `.app` on a Mac. This Linux workspace cannot produce a
working macOS app bundle directly.

On your Mac:

```bash
cd /path/to/dms-vibecode-trash-2026-edition
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
