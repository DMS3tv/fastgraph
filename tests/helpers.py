"""Plain helpers shared by several test modules.

Import them directly (``from helpers import ramp_curve``); pytest puts the
``tests`` directory on ``sys.path`` because it has no ``__init__.py``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from dms.hrtf import _Z_P75, _Z_P90, HRTFCurve
from dms.measure_session import MeasureSession
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
