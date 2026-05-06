"""
Pure measurement signal construction helpers.

These functions build the playback buffer and timing metadata used by the
audio engine. They intentionally do not touch devices or Qt state, which makes
Bluetooth timing assumptions testable without hardware.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class MeasurementSignalLayout:
    fs: int
    sweep_samples: int
    pre_silence_samples: int
    post_silence_samples: int
    primer_gap_samples: int
    start_marker_gap_samples: int
    end_marker_gap_samples: int
    end_marker_pair_gap_samples: int
    excitation_start_sample: int
    sweep_start_sample: int
    sweep_end_sample: int
    end_marker_1_start_sample: int
    end_marker_2_start_sample: int
    total_samples: int
    start_marker: np.ndarray
    end_marker: np.ndarray
    wake_primer: Optional[np.ndarray]
    excitation: np.ndarray


def _validate_fs(fs: int) -> None:
    if int(fs) <= 0:
        raise ValueError("Sample rate must be positive.")


def build_end_marker(fs: int) -> np.ndarray:
    """
    Build a short broadband chirp marker used to validate end timing.
    """
    _validate_fs(fs)
    dur_s = 0.032
    n = max(8, int(round(dur_s * fs)))
    t = np.arange(n, dtype=np.float64) / float(fs)
    # Keep energy in a Bluetooth/ANC-friendly range.
    f0 = 1400.0
    f1 = 6800.0
    k = (f1 - f0) / max(dur_s, 1e-9)
    phase = 2.0 * np.pi * (f0 * t + 0.5 * k * t * t)
    marker = np.sin(phase)
    marker *= np.hanning(n)
    return (0.60 * marker).astype(np.float32)


def build_start_marker(fs: int) -> np.ndarray:
    """
    Build a short chirp marker used to lock sweep start timing robustly.
    """
    _validate_fs(fs)
    dur_s = 0.022
    n = max(8, int(round(dur_s * fs)))
    t = np.arange(n, dtype=np.float64) / float(fs)
    f0 = 1200.0
    f1 = 5600.0
    k = (f1 - f0) / max(dur_s, 1e-9)
    phase = 2.0 * np.pi * (f0 * t + 0.5 * k * t * t)
    marker = np.sin(phase)
    marker *= np.hanning(n)
    return (0.45 * marker).astype(np.float32)


def build_wake_primer(fs: int) -> np.ndarray:
    """
    Build a short non-measurement primer to wake Bluetooth headphones before sweep start.
    """
    _validate_fs(fs)
    dur_s = 0.20
    n = max(16, int(round(dur_s * fs)))
    t = np.arange(n, dtype=np.float64) / float(fs)
    tone = (
        0.5 * np.sin(2.0 * np.pi * 900.0 * t)
        + 0.35 * np.sin(2.0 * np.pi * 2300.0 * t)
        + 0.25 * np.sin(2.0 * np.pi * 5200.0 * t)
    )
    tone *= np.hanning(n)
    return (0.30 * tone).astype(np.float32)


def build_measurement_layout(
    sweep: np.ndarray,
    fs: int,
    pre_silence_s: float,
    post_silence_s: float,
    bluetooth_headphone_mode: bool,
) -> MeasurementSignalLayout:
    """
    Build the playback excitation and expected timing positions.

    `excitation_start_sample` is where the start marker begins in the output
    buffer. `sweep_start_sample` is where the actual measurement sweep begins.
    """
    _validate_fs(fs)
    if pre_silence_s < 0.0 or post_silence_s < 0.0:
        raise ValueError("Silence durations must be non-negative.")

    sweep_array = np.asarray(sweep)
    if sweep_array.ndim != 1 or len(sweep_array) == 0:
        raise ValueError("Sweep must be a non-empty one-dimensional array.")
    if not np.all(np.isfinite(sweep_array)):
        raise ValueError("Sweep must contain only finite values.")

    sweep_f32 = sweep_array.astype(np.float32, copy=False)
    pre_n = int(pre_silence_s * fs)
    post_n = int(post_silence_s * fs)
    primer_gap_n = int(round(0.24 * fs))
    wake_primer = build_wake_primer(fs) if bool(bluetooth_headphone_mode) else None
    primer_n = len(wake_primer) if wake_primer is not None else 0
    sweep_n = len(sweep_f32)
    start_marker = build_start_marker(fs)
    start_marker_gap_n = int(round(0.025 * fs))
    marker = build_end_marker(fs)
    marker_gap_n = int(round(0.03 * fs))
    marker_pair_gap_n = int(round(0.05 * fs))
    excitation = np.concatenate(
        [
            start_marker,
            np.zeros(start_marker_gap_n, dtype=np.float32),
            sweep_f32,
            np.zeros(marker_gap_n, dtype=np.float32),
            marker,
            np.zeros(marker_pair_gap_n, dtype=np.float32),
            marker,
        ]
    )

    lead_n = primer_n + (primer_gap_n if wake_primer is not None else 0)
    excitation_start_n = lead_n + pre_n
    sweep_start_n = excitation_start_n + len(start_marker) + start_marker_gap_n
    sweep_end_n = sweep_start_n + sweep_n
    marker_1_start_n = sweep_end_n + marker_gap_n
    marker_2_start_n = marker_1_start_n + len(marker) + marker_pair_gap_n
    total_n = excitation_start_n + len(excitation) + post_n

    return MeasurementSignalLayout(
        fs=int(fs),
        sweep_samples=sweep_n,
        pre_silence_samples=pre_n,
        post_silence_samples=post_n,
        primer_gap_samples=primer_gap_n,
        start_marker_gap_samples=start_marker_gap_n,
        end_marker_gap_samples=marker_gap_n,
        end_marker_pair_gap_samples=marker_pair_gap_n,
        excitation_start_sample=excitation_start_n,
        sweep_start_sample=sweep_start_n,
        sweep_end_sample=sweep_end_n,
        end_marker_1_start_sample=marker_1_start_n,
        end_marker_2_start_sample=marker_2_start_n,
        total_samples=total_n,
        start_marker=start_marker,
        end_marker=marker,
        wake_primer=wake_primer,
        excitation=excitation.astype(np.float32, copy=False),
    )


def build_output_signal(
    layout: MeasurementSignalLayout,
    output_channels: int,
) -> np.ndarray:
    """
    Build the mono/stereo output buffer consumed by sounddevice.playrec.
    """
    if int(output_channels) <= 0:
        raise ValueError("Output channel count must be positive.")

    channels = 2 if int(output_channels) >= 2 else 1
    out_signal = np.zeros((layout.total_samples, channels), dtype=np.float32)
    if layout.wake_primer is not None:
        out_signal[:len(layout.wake_primer), :] = layout.wake_primer[:, None]
    start = layout.excitation_start_sample
    stop = start + len(layout.excitation)
    out_signal[start:stop, :] = layout.excitation[:, None]
    return out_signal
