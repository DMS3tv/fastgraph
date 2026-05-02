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

## Packaging (optional)

```bash
pip install pyinstaller
pyinstaller --onefile --windowed main.py --name "DMS Fastgraph"
```