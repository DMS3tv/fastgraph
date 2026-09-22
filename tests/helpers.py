"""Plain helpers shared by several test modules.

Import them directly (``from helpers import ramp_curve``); pytest puts the
``tests`` directory on ``sys.path`` because it has no ``__init__.py``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from dms.curator.models import CurveData, GraphState, LayerState
from dms.hrtf import _Z_P75, _Z_P90, HRTFCurve
from dms.measure_session import MeasureSession
from dms.measurement_alignment import AlignmentSettings
from dms.measurement_layout import build_measurement_layout
from dms.rnd.models import RnDMeasurement
from dms.session import SessionData
from dms.two_channel import TwoChannelCurvePair


def ramp_curve(
    offset: float = 0.0, points: int = 64, span: float = 3.0
) -> tuple[np.ndarray, np.ndarray]:
    """A 20 Hz-20 kHz curve rising linearly from ``-span`` to ``+span`` dB."""
    freqs = np.geomspace(20.0, 20000.0, points)
    return freqs, np.linspace(-span, span, points) + offset


def flat_curve(level: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """A three-point curve sitting flat at ``level`` dB."""
    return np.array([100.0, 1000.0, 10000.0]), np.full(3, float(level))


def measure_session(model: str = "Demo", *, two_channel: bool = False, **fields: Any):
    """A ``MeasureSession`` with one kept sweep, plus one pair when two-channel."""
    session = MeasureSession(
        metadata=SessionData(rig="Rig", brand="DMS", model=model),
        two_channel=two_channel,
        **fields,
    )
    session.add_sweep(*ramp_curve(), note=model)
    if two_channel:
        session.add_pair(TwoChannelCurvePair(channel_1=ramp_curve(1.0), channel_2=ramp_curve(-1.0)))
    return session


def rnd_measurement(
    mid: str = "m1", name: str = "R&D One", shift: float = 0.0, **fields: Any
) -> RnDMeasurement:
    """An ``RnDMeasurement`` with a falling two-point curve."""
    values: dict[str, Any] = {
        "id": mid,
        "name": name,
        "freqs": np.array([100.0, 1000.0]),
        "mag_db": np.array([1.0, 0.0]) + shift,
        "metadata": {"brand": "DMS", "model": "Demo", "rig": "Rig"},
        "rig": "Rig",
        "input_device_label": "Input",
        "input_channel_index": 0,
        "input_channel_label": "Channel 1",
        "output_device_label": "Output",
    }
    values.update(fields)
    return RnDMeasurement(**values)


def population_hrtf(tmp_path: Path, sigma: float = 2.0) -> HRTFCurve:
    """A population HRTF whose band is an exact normal of width ``sigma``."""
    path = tmp_path / "population.txt"
    lines = [
        f"{freq} {-_Z_P90 * sigma} {-_Z_P75 * sigma} 0.0 {_Z_P75 * sigma} {_Z_P90 * sigma}"
        for freq in (20.0, 1000.0, 20000.0)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return HRTFCurve(str(path))


def corrupt_backups(directory: Path, stem: str) -> list[Path]:
    """Backups ``backup_corrupt_file`` left for ``stem`` in ``directory``."""
    return sorted(directory.glob(f"{stem}.corrupt-*"))


def pump_until(qapp, predicate, timeout: float = 5.0) -> bool:
    """Spin the GUI event loop until ``predicate`` holds or time runs out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    qapp.processEvents()
    return bool(predicate())


def variation_graph_state(**kwargs) -> GraphState:
    """A curator graph holding one flat variation layer."""
    freqs = np.array([20.0, 1000.0, 20000.0])
    state = GraphState(**kwargs)
    state.layers.append(
        LayerState(
            curve=CurveData(
                kind="variation",
                freqs=freqs,
                p10_db=np.array([-4.0, -4.0, -4.0]),
                p25_db=np.array([-2.0, -2.0, -2.0]),
                median_db=np.array([0.0, 0.0, 0.0]),
                p75_db=np.array([2.0, 2.0, 2.0]),
                p90_db=np.array([4.0, 4.0, 4.0]),
            ),
            source_path=Path("variation.txt"),
            name="Variation",
            color="#15f4ee",
        )
    )
    return state


# Alignment: a short noise excitation keeps each alignment test fast.


def noise_sweep(length: int) -> np.ndarray:
    rng = np.random.default_rng(1234)
    sweep = rng.normal(0.0, 0.2, length).astype(np.float32)
    sweep *= np.hanning(length).astype(np.float32)
    return sweep


def recording_from_layout(layout, delay_samples: int = 0) -> np.ndarray:
    rec = np.zeros(layout.total_samples + delay_samples + 256, dtype=np.float32)
    start = delay_samples + layout.excitation_start_sample
    rec[start : start + len(layout.excitation)] = layout.excitation
    return rec


def short_layout(fs: int = 8_000, bluetooth: bool = False):
    sweep = noise_sweep(2_048)
    layout = build_measurement_layout(
        sweep=sweep,
        fs=fs,
        pre_silence_s=0.08,
        post_silence_s=0.16,
        bluetooth_headphone_mode=bluetooth,
    )
    return sweep, layout


def bluetooth_alignment_settings() -> AlignmentSettings:
    return AlignmentSettings(
        latency="high",
        bluetooth_headphone_mode=True,
        start_alignment_confidence_min=9.0,
        end_marker_confidence_min=7.0,
        timing_drift_max_ms=120.0,
    )


def write_at(rec: np.ndarray, start: int, values: np.ndarray) -> None:
    lo = max(0, int(start))
    hi = min(len(rec), int(start) + len(values))
    if hi <= lo:
        return
    src_lo = lo - int(start)
    src_hi = src_lo + (hi - lo)
    rec[lo:hi] += values[src_lo:src_hi]


def recording_with_shifted_end_markers(
    layout,
    drift_samples: int,
    marker_2_extra_shift: int = 0,
    marker_scale: float = 3.0,
) -> np.ndarray:
    rec = recording_from_layout(layout)
    marker_len = len(layout.end_marker)
    marker_2_len = len(layout.end_marker_2)
    rec[layout.end_marker_1_start_sample : layout.end_marker_1_start_sample + marker_len] = 0.0
    rec[layout.end_marker_2_start_sample : layout.end_marker_2_start_sample + marker_2_len] = 0.0
    marker_1 = (marker_scale * layout.end_marker).astype(np.float32)
    marker_2 = (marker_scale * layout.end_marker_2).astype(np.float32)
    write_at(rec, layout.end_marker_1_start_sample + drift_samples, marker_1)
    write_at(
        rec,
        layout.end_marker_2_start_sample + drift_samples + marker_2_extra_shift,
        marker_2,
    )
    return rec
