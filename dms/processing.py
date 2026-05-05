"""
DSP: log sweep generation, frequency response computation,
normalization, and downsampling.
"""

import numpy as np
from scipy.interpolate import interp1d


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
    Compute magnitude frequency response from the recorded sweep and the
    known excitation sweep using regularized spectral division.

    This keeps the displayed curve raw while avoiding the alignment/windowing
    errors that can come from taking an FFT of a loosely sliced deconvolved IR.
    Returns (freqs_hz, magnitude_db) — full resolution.
    """
    sweep64 = sweep.astype(np.float64)
    rec64 = recording.astype(np.float64)

    nfft = int(2 ** np.ceil(np.log2(max(len(sweep64), len(rec64)))))

    SWEEP = np.fft.rfft(sweep64, n=nfft)
    REC = np.fft.rfft(rec64, n=nfft)

    # Estimate the transfer function directly:
    # H = Y * conj(X) / (|X|^2 + eps)
    sweep_power = np.abs(SWEEP) ** 2
    eps = max(float(np.max(sweep_power)) * 1e-12, 1e-18)
    H_fr = REC * np.conj(SWEEP) / (sweep_power + eps)
    freqs = np.fft.rfftfreq(nfft, d=1.0 / fs)

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
    n_points: int = 600,
    f_ref: float = 1000.0,
    normalize_ref: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Resample to ~n_points log-spaced frequencies.
    Guarantees f_ref (1 kHz) is one of the output points.
    Optionally re-normalizes so f_ref = 0 dB exactly.
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

    if normalize_ref:
        idx_ref_out = int(np.argmin(np.abs(target - f_ref)))
        out_mag -= out_mag[idx_ref_out]

    return target, out_mag


# ---------------------------------------------------------------------------
# RMS average across kept curves
# ---------------------------------------------------------------------------

def compute_rms_average(
    curves: list[tuple[np.ndarray, np.ndarray]],
    n_points: int = 1200,
    f_ref: float = 1000.0,
    f_min: float = 20.0,
    f_max: float = 20000.0,
    normalize_ref: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Average all kept curves in linear amplitude, then convert back to dB.
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

    if normalize_ref:
        avg_db -= avg_db[idx_ref]

    return common_freqs, avg_db


def smooth_fractional_octave(
    freqs: np.ndarray,
    mag_db: np.ndarray,
    fraction: int = 48,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Apply Gaussian smoothing on a log-frequency axis.

    The smoothing bandwidth is specified in fractional octaves using the
    full-width at half maximum of the Gaussian window.
    """
    if len(freqs) < 3 or len(freqs) != len(mag_db) or fraction <= 0:
        return freqs, mag_db

    log_freqs = np.log2(freqs)
    step = float(np.median(np.diff(log_freqs)))
    if not np.isfinite(step) or step <= 0.0:
        return freqs, mag_db

    fwhm_oct = 1.0 / float(fraction)
    sigma_oct = fwhm_oct / (2.0 * np.sqrt(2.0 * np.log(2.0)))
    sigma_idx = sigma_oct / step
    if not np.isfinite(sigma_idx) or sigma_idx <= 0.0:
        return freqs, mag_db

    radius = max(2, int(np.ceil(sigma_idx * 4.0)))
    offsets = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-0.5 * (offsets / sigma_idx) ** 2)
    kernel /= np.sum(kernel)

    padded = np.pad(mag_db.astype(np.float64), (radius, radius), mode="edge")
    smoothed = np.convolve(padded, kernel, mode="valid")
    return freqs, smoothed.astype(np.float64)
