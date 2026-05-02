"""
DSP: log sweep generation, frequency response computation,
normalization, and downsampling.
"""

import numpy as np
from scipy.interpolate import interp1d
from scipy.signal import windows


# ---------------------------------------------------------------------------
# Log swept-sine generation
# ---------------------------------------------------------------------------

def generate_log_sweep(
    duration: float,
    fs: int,
    f_low: float = 20.0,
    f_high: float = 20000.0,
    fade_ms: float = 10.0,
) -> np.ndarray:
    """Return mono log swept sine in range [-1, 1]."""
    n = int(duration * fs)
    t = np.arange(n) / fs
    R = np.log(f_high / f_low)
    # Farina log sweep phase
    phase = 2.0 * np.pi * f_low * duration / R * (np.exp(t * R / duration) - 1.0)
    sweep = np.sin(phase)

    # Cosine fade in/out to avoid clicks
    fade_n = min(int(fade_ms * 1e-3 * fs), n // 10)
    if fade_n > 0:
        fade = np.sin(np.linspace(0, np.pi / 2, fade_n)) ** 2
        sweep[:fade_n] *= fade
        sweep[-fade_n:] *= fade[::-1]

    return sweep.astype(np.float32)


def generate_inverse_filter(
    sweep: np.ndarray,
    fs: int,
    f_low: float = 20.0,
    f_high: float = 20000.0,
) -> np.ndarray:
    """
    Time-reversed sweep with spectral amplitude correction (Farina method).
    The log sweep's spectral envelope rises at ~3 dB/oct; the inverse filter
    compensates so that the deconvolved IR is flat.
    """
    inv = sweep[::-1].copy().astype(np.float64)
    n = len(inv)
    # Amplitude envelope: (f2/f1)^(-t/T) applied in time domain
    t = np.arange(n) / fs
    T = n / fs
    inv *= (f_high / f_low) ** (-t / T)
    return inv.astype(np.float32)


# ---------------------------------------------------------------------------
# Frequency response computation
# ---------------------------------------------------------------------------

def compute_frequency_response(
    recording: np.ndarray,
    sweep: np.ndarray,
    fs: int,
    f_low: float = 20.0,
    f_high: float = 20000.0,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute magnitude frequency response via spectral deconvolution.
    Returns (freqs_hz, magnitude_db) — full resolution.
    """
    inv = generate_inverse_filter(sweep, fs, f_low, f_high)

    # Use a power-of-2 FFT size covering both signals
    total_len = len(recording) + len(inv) - 1
    nfft = int(2 ** np.ceil(np.log2(total_len)))

    REC = np.fft.rfft(recording.astype(np.float64), n=nfft)
    INV = np.fft.rfft(inv.astype(np.float64), n=nfft)

    # Convolve recording with inverse filter → impulse response
    H = REC * INV
    h = np.fft.irfft(H)

    # The valid impulse response starts at 0
    # Take FFT of IR to get complex FR
    nfft2 = int(2 ** np.ceil(np.log2(len(sweep))))
    H_fr = np.fft.rfft(h[: len(sweep)], n=nfft2)
    freqs = np.fft.rfftfreq(nfft2, d=1.0 / fs)

    mag = np.abs(H_fr)
    # Avoid log(0)
    mag = np.clip(mag, 1e-12, None)
    mag_db = 20.0 * np.log10(mag)

    # Restrict to measurement band
    mask = (freqs >= f_low) & (freqs <= f_high)
    return freqs[mask], mag_db[mask]


# ---------------------------------------------------------------------------
# Normalization — skip-noise, anchor at 1 kHz
# ---------------------------------------------------------------------------

def normalize_at_1khz(
    freqs: np.ndarray,
    mag_db: np.ndarray,
    f_ref: float = 1000.0,
) -> np.ndarray:
    """
    Normalize so that 1 kHz = 0 dB.
    Uses linear interpolation to find the exact value at 1 kHz.
    """
    if f_ref < freqs[0] or f_ref > freqs[-1]:
        raise ValueError(f"Reference frequency {f_ref} Hz out of data range.")
    interp = interp1d(freqs, mag_db, kind="linear", bounds_error=True)
    ref_val = float(interp(f_ref))
    return mag_db - ref_val


# ---------------------------------------------------------------------------
# Downsampling — log-spaced, guaranteed 1 kHz point
# ---------------------------------------------------------------------------

def downsample_to_log_points(
    freqs: np.ndarray,
    mag_db: np.ndarray,
    n_points: int = 300,
    f_ref: float = 1000.0,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Resample to ~n_points log-spaced frequencies.
    Guarantees f_ref (1 kHz) is one of the output points.
    After downsampling, re-normalizes so f_ref = 0 dB exactly.
    """
    f_min = freqs[0]
    f_max = freqs[-1]

    target = np.logspace(np.log10(f_min), np.log10(f_max), n_points)

    # Replace nearest point to f_ref with exactly f_ref
    idx_ref = int(np.argmin(np.abs(target - f_ref)))
    target[idx_ref] = f_ref
    # Ensure sorted (replacing shouldn't break sort, but guard it)
    target = np.sort(target)

    interp = interp1d(freqs, mag_db, kind="linear", bounds_error=False,
                      fill_value=(mag_db[0], mag_db[-1]))
    out_mag = interp(target)

    # Re-normalize so 1 kHz is exactly 0.000 dB
    idx_ref_out = int(np.argmin(np.abs(target - f_ref)))
    out_mag -= out_mag[idx_ref_out]

    return target, out_mag


# ---------------------------------------------------------------------------
# RMS average across kept curves
# ---------------------------------------------------------------------------

def compute_rms_average(
    curves: list[tuple[np.ndarray, np.ndarray]],
    n_points: int = 300,
    f_ref: float = 1000.0,
    f_min: float = 20.0,
    f_max: float = 20000.0,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Average all kept curves in linear amplitude, then convert back to dB.
    All curves must already be normalized (1 kHz = 0 dB, ~300 points).
    Uses linear interpolation to a common grid.
    """
    if not curves:
        return np.array([]), np.array([])

    common_freqs = np.logspace(np.log10(f_min), np.log10(f_max), n_points)
    idx_ref = int(np.argmin(np.abs(common_freqs - f_ref)))
    common_freqs[idx_ref] = f_ref

    sum_lin = np.zeros(n_points)
    count = 0
    for freqs, mag_db in curves:
        interp = interp1d(freqs, mag_db, kind="linear", bounds_error=False,
                          fill_value=(mag_db[0], mag_db[-1]))
        vals = interp(common_freqs)
        # RMS average in linear (power) space
        sum_lin += 10.0 ** (vals / 10.0)
        count += 1

    avg_lin = sum_lin / count
    avg_db = 10.0 * np.log10(np.clip(avg_lin, 1e-30, None))

    # Re-normalize to 1 kHz = 0 dB
    avg_db -= avg_db[idx_ref]

    return common_freqs, avg_db