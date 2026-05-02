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
