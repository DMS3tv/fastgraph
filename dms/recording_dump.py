"""
Save failed measurement recordings for offline diagnosis.

When the opt-in setting is enabled, every alignment failure writes the raw
mono recording as a WAV file plus a JSON sidecar holding everything needed to
replay the alignment without hardware: sweep parameters, layout timing,
alignment settings, and the diagnostics summary. ``tools/replay_failed_recording.py``
reads the pair and reruns ``align_recording_to_layout``.

No Qt and no sounddevice here; the audio worker calls ``save_failed_recording``
from its background thread.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np
from scipy.io import wavfile


DUMP_VERSION = 1


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def save_failed_recording(
    directory: Path | str,
    recording: np.ndarray,
    *,
    fs: int,
    sweep_duration_s: float,
    f_low: float,
    f_high: float,
    pre_silence_s: float,
    post_silence_s: float,
    bluetooth_headphone_mode: bool,
    alignment_settings: Any,
    diagnostics: Any,
    failure_message: str,
    failure_reason: Optional[str],
    extra: Optional[dict[str, Any]] = None,
) -> Path:
    """Write ``<stamp>.wav`` and ``<stamp>.json``; return the JSON path."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = directory / f"failed-{stamp}"
    counter = 1
    while (base.with_suffix(".json")).exists():
        counter += 1
        base = directory / f"failed-{stamp}-{counter}"

    data = np.asarray(recording, dtype=np.float32)
    wavfile.write(str(base.with_suffix(".wav")), int(fs), data)

    payload = {
        "dump_version": DUMP_VERSION,
        "recorded_at": stamp,
        "wav": base.with_suffix(".wav").name,
        "fs": int(fs),
        "sweep": {
            "duration_s": float(sweep_duration_s),
            "f_low": float(f_low),
            "f_high": float(f_high),
        },
        "layout": {
            "pre_silence_s": float(pre_silence_s),
            "post_silence_s": float(post_silence_s),
            "bluetooth_headphone_mode": bool(bluetooth_headphone_mode),
        },
        "alignment_settings": _jsonable(alignment_settings),
        "failure": {
            "reason": failure_reason,
            "message": failure_message,
        },
        "diagnostics": _jsonable(diagnostics),
        "extra": _jsonable(extra or {}),
    }
    json_path = base.with_suffix(".json")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return json_path


def load_failed_recording(json_path: Path | str) -> tuple[dict[str, Any], np.ndarray]:
    """Return the sidecar payload and the mono float32 recording."""
    json_path = Path(json_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    wav_path = json_path.with_name(str(payload.get("wav") or json_path.with_suffix(".wav").name))
    fs, data = wavfile.read(str(wav_path))
    if int(fs) != int(payload.get("fs", fs)):
        raise ValueError(
            f"Sample rate mismatch between {wav_path.name} ({fs}) and the sidecar "
            f"({payload.get('fs')})."
        )
    if data.ndim > 1:
        data = data[:, 0]
    if data.dtype.kind in "iu":
        scale = float(np.iinfo(data.dtype).max)
        data = data.astype(np.float32) / scale
    return payload, np.asarray(data, dtype=np.float32)
