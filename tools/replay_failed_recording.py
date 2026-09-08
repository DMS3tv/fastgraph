#!/usr/bin/env python3
"""
Replay a failed-recording dump through the alignment pipeline.

Usage:
    PYTHONPATH=. .venv/bin/python tools/replay_failed_recording.py <dump.json> [more.json ...]
        [--confidence-min X] [--noise-margin-min X] [--end-marker-min X]
        [--drift-max-ms X] [--standard | --bluetooth]

Each dump was written by Fastgraph when "Save failed recordings for
diagnosis" was enabled in Settings. The script rebuilds the sweep and timing
layout from the sidecar, reruns ``align_recording_to_layout`` with the stored
settings (or overrides), and prints the diagnostics summary. Use it to test
threshold changes against real failures without hardware.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dms.measurement_alignment import (  # noqa: E402
    AlignmentSettings,
    MeasurementAlignmentError,
    align_recording_to_layout,
    format_diagnostics_summary,
)
from dms.measurement_layout import build_measurement_layout  # noqa: E402
from dms.processing import generate_log_sweep  # noqa: E402
from dms.recording_dump import load_failed_recording  # noqa: E402


def settings_from_payload(payload: dict) -> AlignmentSettings:
    stored = dict(payload.get("alignment_settings") or {})
    known = {
        name: stored[name]
        for name in AlignmentSettings.__dataclass_fields__
        if name in stored
    }
    return AlignmentSettings(**known)


def replay(json_path: Path, overrides: dict) -> int:
    payload, recording = load_failed_recording(json_path)
    fs = int(payload["fs"])
    sweep_info = payload["sweep"]
    layout_info = payload["layout"]
    settings = settings_from_payload(payload)
    if overrides:
        settings = replace(settings, **overrides)

    sweep = generate_log_sweep(
        duration=float(sweep_info["duration_s"]),
        fs=fs,
        f_low=float(sweep_info["f_low"]),
        f_high=float(sweep_info["f_high"]),
    )
    layout = build_measurement_layout(
        sweep=sweep,
        fs=fs,
        pre_silence_s=float(layout_info["pre_silence_s"]),
        post_silence_s=float(layout_info["post_silence_s"]),
        bluetooth_headphone_mode=bool(settings.bluetooth_headphone_mode),
    )

    print(f"=== {json_path.name} ===")
    original = payload.get("failure") or {}
    print(f"Original failure: {original.get('reason')} - {original.get('message')}")
    print(f"Settings: {settings}")
    try:
        result = align_recording_to_layout(recording, sweep, layout, settings)
    except MeasurementAlignmentError as exc:
        print(f"Replay result: FAIL ({exc.reason})")
        print(format_diagnostics_summary(exc.diagnostics))
        return 1
    print("Replay result: OK")
    print(format_diagnostics_summary(result.diagnostics))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("dumps", nargs="+", type=Path, help="JSON sidecar files")
    parser.add_argument("--confidence-min", type=float, dest="start_alignment_confidence_min")
    parser.add_argument("--noise-margin-min", type=float, dest="sweep_noise_margin_min_db")
    parser.add_argument("--end-marker-min", type=float, dest="end_marker_confidence_min")
    parser.add_argument("--drift-max-ms", type=float, dest="timing_drift_max_ms")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--standard", action="store_true", help="force standard mode")
    mode.add_argument("--bluetooth", action="store_true", help="force Bluetooth mode")
    args = parser.parse_args(argv)

    overrides = {
        key: value
        for key, value in vars(args).items()
        if key in AlignmentSettings.__dataclass_fields__ and value is not None
    }
    if args.standard:
        overrides["bluetooth_headphone_mode"] = False
    if args.bluetooth:
        overrides["bluetooth_headphone_mode"] = True

    worst = 0
    for dump in args.dumps:
        worst = max(worst, replay(dump, overrides))
        print()
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
