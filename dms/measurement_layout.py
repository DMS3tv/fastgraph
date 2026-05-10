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
    end_marker_2: np.ndarray
    wake_primer: Optional[np.ndarray]
    excitation: np.ndarray


def _validate_fs(fs: int) -> None:
    if int(fs) <= 0:
        raise ValueError("Sample rate must be positive.")


def build_legacy_chirp_marker(fs: int, *, start_marker: bool = False) -> np.ndarray:
    """
    Build the previous short chirp marker for quick rollback/tests.
    """
    _validate_fs(fs)
    dur_s = 0.022 if start_marker else 0.032
    n = max(8, int(round(dur_s * fs)))
    t = np.arange(n, dtype=np.float64) / float(fs)
    f0 = 1200.0 if start_marker else 1400.0
    f1 = 5600.0 if start_marker else 6800.0
    k = (f1 - f0) / max(dur_s, 1e-9)
    phase = 2.0 * np.pi * (f0 * t + 0.5 * k * t * t)
    marker = np.sin(phase)
    marker *= np.hanning(n)
    gain = 0.45 if start_marker else 0.60
    return (gain * marker).astype(np.float32)


def build_coded_timing_marker(fs: int, code_id: str) -> np.ndarray:
    """
    Build a broadband coded marker packet for robust timing correlation.

    The marker uses several headphone-friendly tones across short chips. Each
    code id has a different sign/phase pattern, so end marker A cannot be
    cleanly substituted for end marker B by one loud resonant artifact.
    """
    _validate_fs(fs)
    code_key = str(code_id).lower()
    code_seeds = {
        "start": 3,
        "end_a": 7,
        "end_b": 13,
    }
    if code_key not in code_seeds:
        raise ValueError("Unknown timing marker code id.")

    dur_s = 0.056 if code_key == "start" else 0.072
    n = max(32, int(round(dur_s * fs)))
    chip_count = 7
    chip_edges = np.linspace(0, n, chip_count + 1, dtype=int)
    marker = np.zeros(n, dtype=np.float64)
    t = np.arange(n, dtype=np.float64) / float(fs)

    frequency_sets = {
        "start": [650.0, 980.0, 1500.0, 2300.0, 3500.0, 5400.0, 7600.0],
        "end_a": [600.0, 950.0, 1450.0, 2300.0, 3600.0, 5600.0, 7600.0],
        "end_b": [760.0, 1180.0, 1780.0, 2750.0, 4300.0, 6500.0, 7900.0],
    }
    base_freqs = np.array(frequency_sets[code_key], dtype=np.float64)
    usable = base_freqs[base_freqs < 0.45 * float(fs)]
    if len(usable) < 3:
        usable = np.linspace(0.12 * fs, 0.40 * fs, 3, dtype=np.float64)
    weights = np.linspace(1.0, 0.72, len(usable), dtype=np.float64)

    seed = code_seeds[code_key]
    for chip_idx in range(chip_count):
        lo = chip_edges[chip_idx]
        hi = chip_edges[chip_idx + 1]
        if hi <= lo:
            continue
        chip_t = t[lo:hi]
        chip = np.zeros(hi - lo, dtype=np.float64)
        for tone_idx, freq in enumerate(usable):
            pattern = (seed + 3 * chip_idx + 5 * tone_idx + chip_idx * tone_idx) % 7
            sign = 1.0 if pattern in {0, 1, 3} else -1.0
            phase_offset = 0.41 * tone_idx + 0.29 * chip_idx + 0.17 * seed
            chip += weights[tone_idx] * np.sin(
                2.0 * np.pi * freq * chip_t + phase_offset
            ) * sign
        chip /= max(float(np.max(np.abs(chip))), 1e-12)
        marker[lo:hi] = chip

    marker *= np.hanning(n)
    marker /= max(float(np.max(np.abs(marker))), 1e-12)
    gain = 0.45 if code_key == "start" else 0.58
    return (gain * marker).astype(np.float32)


def build_end_marker(fs: int) -> np.ndarray:
    """
    Build coded end marker A used to validate timing.
    """
    return build_coded_timing_marker(fs, "end_a")


def build_end_marker_2(fs: int) -> np.ndarray:
    """
    Build coded end marker B used to verify ordered marker identity.
    """
    return build_coded_timing_marker(fs, "end_b")


def build_start_marker(fs: int) -> np.ndarray:
    """
    Build a coded marker used to lock sweep start timing robustly.
    """
    return build_coded_timing_marker(fs, "start")


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

    In standard wired mode the excitation is only the sweep. In Bluetooth mode
    the excitation includes coded timing markers around the sweep.
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
    bluetooth_mode = bool(bluetooth_headphone_mode)
    primer_gap_n = int(round(0.24 * fs)) if bluetooth_mode else 0
    wake_primer = build_wake_primer(fs) if bluetooth_mode else None
    primer_n = len(wake_primer) if wake_primer is not None else 0
    sweep_n = len(sweep_f32)
    if bluetooth_mode:
        start_marker = build_start_marker(fs)
        start_marker_gap_n = int(round(0.025 * fs))
        marker = build_end_marker(fs)
        marker_2 = build_end_marker_2(fs)
        marker_gap_n = int(round(0.12 * fs))
        marker_pair_gap_n = int(round(0.12 * fs))
        excitation = np.concatenate(
            [
                start_marker,
                np.zeros(start_marker_gap_n, dtype=np.float32),
                sweep_f32,
                np.zeros(marker_gap_n, dtype=np.float32),
                marker,
                np.zeros(marker_pair_gap_n, dtype=np.float32),
                marker_2,
            ]
        )
    else:
        start_marker = np.array([], dtype=np.float32)
        start_marker_gap_n = 0
        marker = np.array([], dtype=np.float32)
        marker_2 = np.array([], dtype=np.float32)
        marker_gap_n = 0
        marker_pair_gap_n = 0
        excitation = sweep_f32

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
        end_marker_2=marker_2,
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
