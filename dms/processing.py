"""
DSP: log sweep generation, frequency response computation,
normalization, and downsampling.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.interpolate import interp1d

#: The shared analysis grid: 1200 log-spaced points over 20 Hz - 20 kHz with an
#: exact 1 kHz reference point (see :func:`log_grid`).
GRID_POINTS = 1200
F_LOW = 20.0
F_HIGH = 20000.0
F_REF = 1000.0

#: Standard-normal quantiles for the 90th and 75th percentiles. Percentile
#: columns are converted to a sigma through these, so independent spreads can
#: be added in quadrature.
_Z_P90 = 1.2815515655446004
_Z_P75 = 0.6744897501960817


def sigma_from_percentiles(
    p10: np.ndarray,
    p25: np.ndarray,
    p75: np.ndarray,
    p90: np.ndarray,
) -> np.ndarray:
    """Estimate the standard deviation behind a set of percentile columns.

    Both the 10/90 and the 25/75 pairs give an estimate of sigma for a normal
    distribution; averaging them uses all four columns and is less sensitive to
    one noisy tail than either alone.
    """
    outer = (np.asarray(p90, dtype=float) - np.asarray(p10, dtype=float)) / (2.0 * _Z_P90)
    inner = (np.asarray(p75, dtype=float) - np.asarray(p25, dtype=float)) / (2.0 * _Z_P75)
    return 0.5 * (outer + inner)


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
    **window_kwargs,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute the magnitude frequency response from the recorded sweep and the
    known excitation sweep.

    The recording is deconvolved into a circular impulse response, the linear
    part of that response is windowed — which discards the harmonic distortion
    packets that sit at negative time, and the noise that sits between the
    direct sound and the end of the buffer — and the spectrum of the window is
    returned.

    Extra keyword arguments are forwarded to :func:`window_impulse_response`.

    Returns (freqs_hz, magnitude_db) — full resolution.
    """
    deconv = deconvolve_sweep(recording, sweep, fs)
    ir_window = window_impulse_response(deconv, f_low=f_low, f_high=f_high, **window_kwargs)
    return frequency_response_from_ir(ir_window, fs, f_low=f_low, f_high=f_high)


# ---------------------------------------------------------------------------
# Sweep deconvolution — circular impulse response and its windows
# ---------------------------------------------------------------------------

#: Search span for the linear peak around index 0, in milliseconds.
PEAK_SEARCH_PRE_MS = 5.0
PEAK_SEARCH_POST_MS = 50.0

#: Fraction of the distance to the neighbouring distortion packet that a
#: harmonic window is allowed to span.
HARMONIC_WINDOW_SPAN = 0.45

#: Samples either side of the analytic packet position that the refinement
#: search for a distortion packet's peak is allowed to move.
HARMONIC_PEAK_REFINE_SAMPLES = 400

#: Hard ceiling on the automatic post-peak window length, in milliseconds.
MAX_AUTO_POST_MS = 300.0


def _next_pow2(n: int) -> int:
    """Smallest power of two >= ``n`` (at least 1)."""
    n = int(n)
    if n <= 1:
        return 1
    return int(2 ** int(np.ceil(np.log2(n))))


@dataclass(frozen=True)
class SweepDeconvolution:
    """The circular impulse response recovered from a swept-sine measurement.

    ``ir`` is circular: the linear response starts at ``peak_index`` (which is
    at or just after index 0 for an aligned recording), and the harmonic
    distortion packets sit at negative time — that is, at the end of the
    buffer — offset by the Farina delays returned by
    :func:`harmonic_time_offsets`.
    """

    ir: np.ndarray
    fs: int
    nfft: int
    sweep_samples: int
    recording_samples: int
    peak_index: int
    duration_s: float

    @property
    def available_tail_ms(self) -> float:
        """Milliseconds of recording captured after the sweep had ended."""
        extra = int(self.recording_samples) - int(self.sweep_samples)
        if extra <= 0:
            return 0.0
        return extra / float(self.fs) * 1000.0


def deconvolve_sweep(
    recording: np.ndarray,
    sweep: np.ndarray,
    fs: int,
    *,
    regularization: float = 1e-12,
) -> SweepDeconvolution:
    """Deconvolve ``recording`` by ``sweep`` into a circular impulse response.

    The transfer function is estimated exactly as the legacy frequency-response
    path does — ``H = Y * conj(X) / (|X|^2 + eps)`` — so the windowed spectrum
    of the resulting impulse response reproduces the historical curve on a
    linear system. A time-reversed Farina inverse filter is deliberately *not*
    used: its magnitude ramp diverges from this estimator by tens of dB at the
    top of the band.

    The FFT length is twice the longest input so the second-harmonic packet,
    which sits ``duration * ln2 / ln(f_high / f_low)`` seconds before the linear
    response, cannot alias onto it.
    """
    sweep64 = np.asarray(sweep, dtype=np.float64).ravel()
    rec64 = np.asarray(recording, dtype=np.float64).ravel()
    if sweep64.size == 0 or rec64.size == 0:
        raise ValueError("Both the recording and the sweep must be non-empty.")
    fs = int(fs)
    if fs <= 0:
        raise ValueError("Sample rate must be positive.")

    nfft = _next_pow2(2 * max(rec64.size, sweep64.size))

    SWEEP = np.fft.rfft(sweep64, n=nfft)
    REC = np.fft.rfft(rec64, n=nfft)

    sweep_power = np.abs(SWEEP) ** 2
    eps = max(float(np.max(sweep_power)) * float(regularization), 1e-18)
    H_fr = REC * np.conj(SWEEP) / (sweep_power + eps)
    ir = np.fft.irfft(H_fr, n=nfft)

    duration_s = sweep64.size / float(fs)
    peak_index = _find_linear_peak(ir, fs, duration_s)

    return SweepDeconvolution(
        ir=ir,
        fs=fs,
        nfft=nfft,
        sweep_samples=int(sweep64.size),
        recording_samples=int(rec64.size),
        peak_index=int(peak_index),
        duration_s=float(duration_s),
    )


def _default_second_harmonic_delay(duration_s: float) -> float:
    """Δt₂ for the default 20 Hz – 20 kHz sweep band."""
    return harmonic_time_offsets((2,), duration_s=duration_s, f_low=20.0, f_high=20000.0)[2]


def _find_linear_peak(ir: np.ndarray, fs: int, duration_s: float) -> int:
    """Circular index of the largest sample in the guard band around zero."""
    nfft = ir.size
    delta_t2 = _default_second_harmonic_delay(duration_s)
    guard_post = int(round(PEAK_SEARCH_POST_MS * 1e-3 * fs))
    if delta_t2 > 0.0:
        guard_post = min(guard_post, int(round(0.4 * delta_t2 * fs)))
    guard_post = max(0, min(guard_post, nfft // 4))
    guard_pre = int(round(PEAK_SEARCH_PRE_MS * 1e-3 * fs))
    guard_pre = max(0, min(guard_pre, nfft // 4))

    offsets = np.arange(-guard_pre, guard_post + 1)
    if offsets.size == 0:
        return 0
    candidates = np.take(ir, offsets, mode="wrap")
    best = int(offsets[int(np.argmax(np.abs(candidates)))])
    return int(best % nfft)


def harmonic_time_offsets(
    orders: Iterable[int],
    *,
    duration_s: float,
    f_low: float,
    f_high: float,
) -> dict[int, float]:
    """Farina harmonic delays: Δt_k = T · ln(k) / ln(f_high / f_low).

    The k-th harmonic packet of a log sweep appears ``Δt_k`` seconds *before*
    the linear response. Δt₁ is zero by construction.
    """
    if duration_s <= 0.0:
        raise ValueError("Sweep duration must be positive.")
    if f_low <= 0.0 or f_high <= f_low:
        raise ValueError("Require 0 < f_low < f_high.")
    ratio = np.log(f_high / f_low)
    out: dict[int, float] = {}
    for order in orders:
        k = int(order)
        if k < 1:
            raise ValueError(f"Harmonic order must be >= 1, got {order}.")
        out[k] = float(duration_s) * float(np.log(k)) / float(ratio)
    return out


def _half_hann_window(n: int, fs: int, head_taper_ms: float, tail_taper_ms: float) -> np.ndarray:
    """Rectangular window with half-Hann tapers at both ends."""
    if n <= 0:
        return np.zeros(0)
    w = np.ones(n, dtype=np.float64)
    n_head = max(0, int(round(head_taper_ms * 1e-3 * fs)))
    n_tail = max(0, int(round(tail_taper_ms * 1e-3 * fs)))
    # Never let the two tapers meet in the middle.
    n_head = min(n_head, n // 2)
    n_tail = min(n_tail, n - n_head)
    if n_head > 0:
        rise = 0.5 * (1.0 - np.cos(np.pi * np.arange(n_head) / n_head))
        w[:n_head] = rise
    if n_tail > 0:
        fall = 0.5 * (1.0 - np.cos(np.pi * np.arange(n_tail) / n_tail))[::-1]
        w[n - n_tail :] = fall
    return w


def _extract_circular(
    ir: np.ndarray,
    centre: int,
    n_before: int,
    n_after: int,
    fs: int,
    head_taper_ms: float,
    tail_taper_ms: float,
) -> np.ndarray:
    """Tapered circular slice ``[centre - n_before, centre + n_after]``."""
    n_before = max(0, int(n_before))
    n_after = max(0, int(n_after))
    idx = np.arange(centre - n_before, centre + n_after + 1)
    segment = np.take(ir, idx, mode="wrap").astype(np.float64)
    return segment * _half_hann_window(segment.size, fs, head_taper_ms, tail_taper_ms)


def window_impulse_response(
    deconv: SweepDeconvolution,
    *,
    pre_ms: float = 5.0,
    post_ms: float | None = None,
    head_taper_ms: float = 2.0,
    tail_taper_ms: float = 20.0,
    f_low: float = 20.0,
    f_high: float = 20000.0,
) -> np.ndarray:
    """Window the linear part of a deconvolved impulse response.

    The window runs from ``pre_ms`` before the linear peak to ``post_ms`` after
    it, with half-Hann tapers at both ends and a rectangular middle, and is read
    circularly so a peak at index 0 keeps its (silent) pre-ring.

    ``post_ms`` defaults to the shortest of: the recording actually captured
    after the sweep ended, 45 % of the second-harmonic delay (so the H2 packet
    can never leak in), and 300 ms. When no post-sweep tail was recorded the
    default becomes zero and the window degenerates to the whole response —
    exactly the legacy, unwindowed behaviour.
    """
    ir = np.asarray(deconv.ir, dtype=np.float64)
    fs = int(deconv.fs)

    if post_ms is None:
        delta_t2 = harmonic_time_offsets(
            (2,), duration_s=deconv.duration_s, f_low=f_low, f_high=f_high
        )[2]
        post_ms = min(
            deconv.available_tail_ms,
            HARMONIC_WINDOW_SPAN * delta_t2 * 1000.0,
            MAX_AUTO_POST_MS,
        )
    post_ms = max(0.0, float(post_ms))

    if post_ms == 0.0:
        # Nothing to window against: hand back the whole circular response, so
        # the spectrum is the plain deconvolution the legacy path produced.
        return ir.copy()

    n_pre = max(0, int(round(max(0.0, float(pre_ms)) * 1e-3 * fs)))
    n_post = int(round(post_ms * 1e-3 * fs))
    total = n_pre + n_post + 1
    if total > ir.size:
        n_post = max(0, ir.size - n_pre - 1)
    return _extract_circular(
        ir, int(deconv.peak_index), n_pre, n_post, fs, head_taper_ms, tail_taper_ms
    )


def _ir_spectrum(
    ir_window: np.ndarray, fs: int, bin_hz_max: float
) -> tuple[np.ndarray, np.ndarray]:
    """Full-resolution (freqs, magnitude_db) of a windowed impulse response."""
    data = np.asarray(ir_window, dtype=np.float64).ravel()
    if data.size == 0:
        raise ValueError("Cannot take the spectrum of an empty window.")
    if bin_hz_max <= 0.0:
        raise ValueError("bin_hz_max must be positive.")
    min_bins = int(np.ceil(float(fs) / float(bin_hz_max)))
    nfft = _next_pow2(max(data.size, min_bins))
    spectrum = np.fft.rfft(data, n=nfft)
    freqs = np.fft.rfftfreq(nfft, d=1.0 / float(fs))
    mag_db = 20.0 * np.log10(np.clip(np.abs(spectrum), 1e-12, None))
    return freqs, mag_db


def frequency_response_from_ir(
    ir_window: np.ndarray,
    fs: int,
    *,
    f_low: float = 20.0,
    f_high: float = 20000.0,
    bin_hz_max: float = 0.1,
) -> tuple[np.ndarray, np.ndarray]:
    """Magnitude spectrum of a windowed impulse response, restricted to band.

    The transform is zero-padded until the bin spacing is at most
    ``bin_hz_max`` so that even the narrowest cell of a 600-point log grid —
    about 0.23 Hz wide at 20 Hz — still contains two or more bins to average.
    """
    freqs, mag_db = _ir_spectrum(ir_window, fs, bin_hz_max)
    mask = (freqs >= f_low) & (freqs <= f_high)
    return freqs[mask], mag_db[mask]


@dataclass(frozen=True)
class HarmonicAnalysis:
    """Linear response and harmonic distortion products on one log grid.

    ``orders`` maps harmonic order to the level of that harmonic relative to
    the fundamental, in dB, sampled on ``freqs`` (the *excitation* frequency).
    Points whose harmonic would land above Nyquist or above the top of the
    measurement band are NaN.
    """

    freqs: np.ndarray
    linear_db: np.ndarray
    orders: dict[int, np.ndarray]
    thd_db: np.ndarray
    thd_percent: np.ndarray


#: Fraction of the harmonic band limit (sweep top frequency or Nyquist) above
#: which an order is reported as NaN, so the window taper at the band edge
#: never shows as a rise in the distortion curves.
_HARMONIC_LIMIT_GUARD = 0.9


def harmonic_responses(
    deconv: SweepDeconvolution,
    *,
    f_low: float = 20.0,
    f_high: float = 20000.0,
    orders: Sequence[int] = (2, 3, 4, 5),
    n_points: int = 600,
) -> HarmonicAnalysis:
    """Window each Farina distortion packet and measure it against the linear part.

    Packet ``k`` sits ``Δt_k`` before the linear response, so in circular time
    the packets run 1, 2, 3 … towards more negative time with ever smaller
    gaps. Each window therefore spans 45 % of the way to the packet on either
    side: ``Δt_{k+1} - Δt_k`` towards negative time and ``Δt_k - Δt_{k-1}``
    back towards the linear peak.
    """
    wanted = sorted({int(k) for k in orders})
    if any(k < 2 for k in wanted):
        raise ValueError("Harmonic orders must be >= 2.")
    fs = int(deconv.fs)
    ir = np.asarray(deconv.ir, dtype=np.float64)

    # One extra order so the highest requested packet has a right-hand
    # neighbour to measure its window against.
    needed = [1] + wanted + [max(wanted) + 1]
    offsets = harmonic_time_offsets(
        needed, duration_s=deconv.duration_s, f_low=f_low, f_high=f_high
    )

    windows: dict[int, np.ndarray] = {}
    for k in [1] + wanted:
        centre = int(deconv.peak_index) - int(round(offsets[k] * fs))
        # Refine on the real packet peak; the analytic position is exact only
        # for an ideal sweep.
        search = np.arange(
            centre - HARMONIC_PEAK_REFINE_SAMPLES,
            centre + HARMONIC_PEAK_REFINE_SAMPLES + 1,
        )
        local = np.take(ir, search, mode="wrap")
        centre = int(search[int(np.argmax(np.abs(local)))])

        if k == 1:
            # Nothing but silence sits after the linear response, so mirror the
            # distance to H2 on both sides.
            span_after = offsets[2]
            span_before = offsets[2]
        else:
            span_before = offsets[k + 1] - offsets[k]
            span_after = offsets[k] - offsets[k - 1]
        n_before = int(round(HARMONIC_WINDOW_SPAN * span_before * fs))
        n_after = int(round(HARMONIC_WINDOW_SPAN * span_after * fs))
        windows[k] = _extract_circular(ir, centre, n_before, n_after, fs, 2.0, 20.0)

    longest = max(w.size for w in windows.values())
    padded = {k: np.pad(w, (0, longest - w.size)) for k, w in windows.items()}

    grid_freqs, linear_db = _band_response(padded[1], fs, f_low, f_high, n_points, f_ref=1000.0)

    nyquist = fs / 2.0
    order_db: dict[int, np.ndarray] = {}
    for k in wanted:
        _, mag_k = _band_response(padded[k], fs, f_low * k, f_high * k, n_points, f_ref=1000.0 * k)
        rel = mag_k - linear_db
        harmonic_freqs = grid_freqs * k
        # Mask a little below the hard limit: the last few percent before
        # the harmonic reaches the sweep's top frequency (or Nyquist) carry
        # the window taper and the band edge, which read as a spurious rise.
        limit = _HARMONIC_LIMIT_GUARD * min(nyquist, f_high)
        rel = np.where(harmonic_freqs > limit, np.nan, rel)
        order_db[k] = rel

    if order_db:
        stack = np.vstack([order_db[k] for k in wanted])
        power = 10.0 ** (stack / 10.0)
        any_valid = np.any(np.isfinite(power), axis=0)
        total = np.where(any_valid, np.nansum(power, axis=0), np.nan)
    else:
        total = np.full(grid_freqs.size, np.nan)
    thd_ratio = np.sqrt(total)
    with np.errstate(divide="ignore", invalid="ignore"):
        thd_db = 20.0 * np.log10(np.clip(thd_ratio, 1e-30, None))

    return HarmonicAnalysis(
        freqs=grid_freqs,
        linear_db=linear_db,
        orders=order_db,
        thd_db=thd_db,
        thd_percent=100.0 * thd_ratio,
    )


def _band_response(
    ir_window: np.ndarray,
    fs: int,
    f_min: float,
    f_max: float,
    n_points: int,
    f_ref: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Band-averaged log-grid spectrum of one windowed packet.

    The grid for harmonic ``k`` is the fundamental grid scaled by ``k`` — the
    reference-frequency snap picks the same index in both — so the harmonic and
    the fundamental line up sample for sample.
    """
    freqs, mag_db = _ir_spectrum(ir_window, fs, 0.1)
    return resample_log_band_average(
        freqs,
        mag_db,
        n_points=n_points,
        f_ref=f_ref,
        f_min=f_min,
        f_max=f_max,
        normalize_ref=False,
    )


# ---------------------------------------------------------------------------
# Absolute SPL calibration
# ---------------------------------------------------------------------------


def absolute_spl_offset_db(
    *,
    sensitivity_pa_per_fs: float,
    output_level_db: float,
    sweep_peak_fs: float = 1.0,
) -> float:
    """dB offset that turns a normalized curve into absolute dB SPL.

    ``sensitivity_pa_per_fs`` is the measured pascals produced per unit of
    full-scale digital amplitude, ``output_level_db`` the playback attenuation
    applied to the sweep, and ``sweep_peak_fs`` the sweep's peak amplitude.
    The sweep is a sine, so its RMS is its peak over sqrt(2).
    """
    if sensitivity_pa_per_fs <= 0.0:
        raise ValueError("Sensitivity must be positive.")
    if sweep_peak_fs <= 0.0:
        raise ValueError("Sweep peak amplitude must be positive.")
    amplitude = float(sweep_peak_fs) * 10.0 ** (float(output_level_db) / 20.0)
    pressure_rms = amplitude / np.sqrt(2.0) * float(sensitivity_pa_per_fs)
    return float(20.0 * np.log10(pressure_rms / 20e-6))


# ---------------------------------------------------------------------------
# Normalization — skip-noise, anchor at 1 kHz
# ---------------------------------------------------------------------------


def normalize_at_1khz(
    freqs: np.ndarray,
    mag_db: np.ndarray,
    f_ref: float = F_REF,
) -> np.ndarray:
    """
    Normalize so that 1 kHz = 0 dB.
    Uses linear interpolation to find the exact value at 1 kHz.
    """
    return mag_db - value_at(freqs, mag_db, f_ref)


def value_at(
    freqs: np.ndarray,
    values: np.ndarray,
    f: float,
    *,
    log_x: bool = False,
) -> float:
    """Linearly interpolated value at ``f`` (in log-frequency with ``log_x``).

    Raises ``ValueError`` when ``f`` lies outside ``freqs`` instead of holding
    the nearest edge.
    """
    freqs = np.asarray(freqs, dtype=float)
    values = np.asarray(values, dtype=float)
    if freqs.size < 2 or freqs.size != values.size:
        raise ValueError("Response data is incomplete.")
    if f < freqs[0] or f > freqs[-1]:
        raise ValueError(f"Reference frequency {f:g} Hz out of data range.")
    if log_x:
        return float(np.interp(np.log10(f), np.log10(freqs), values))
    return float(np.interp(f, freqs, values))


# ---------------------------------------------------------------------------
# Downsampling — log-spaced, guaranteed 1 kHz point
# ---------------------------------------------------------------------------


def log_grid(
    n_points: int = GRID_POINTS,
    f_low: float = F_LOW,
    f_high: float = F_HIGH,
    f_ref: float = F_REF,
) -> np.ndarray:
    """Log-spaced grid with the point nearest ``f_ref`` snapped onto it."""
    target = np.logspace(np.log10(f_low), np.log10(f_high), n_points)
    # Replace nearest point to f_ref with exactly f_ref
    idx_ref = int(np.argmin(np.abs(target - f_ref)))
    target[idx_ref] = f_ref
    # Ensure sorted (replacing shouldn't break sort, but guard it)
    return np.sort(target)


def resample_log_band_average(
    freqs: np.ndarray,
    mag_db: np.ndarray,
    *,
    n_points: int = 600,
    f_ref: float = F_REF,
    f_min: float | None = None,
    f_max: float | None = None,
    normalize_ref: bool = True,
    min_bins: int = 2,
) -> tuple[np.ndarray, np.ndarray]:
    """Resample onto a log grid by averaging power inside each grid cell.

    Point-sampling a dense FFT grid throws away every bin between the sampled
    ones, which makes narrow noise spikes survive and broad energy vanish.
    Instead each output point owns the cell bounded by the geometric midpoints
    to its neighbours, and takes the mean *power* of every source bin inside it.

    Cells that hold fewer than ``min_bins`` source bins — the bottom of the band
    on a coarse grid, or any sparse synthetic curve — fall back to the
    historical linear interpolation, so low-resolution inputs behave exactly as
    they always did.
    """
    freqs = np.asarray(freqs, dtype=np.float64).ravel()
    values = np.asarray(mag_db, dtype=np.float64).ravel()
    if freqs.size == 0 or freqs.size != values.size:
        raise ValueError("freqs and mag_db must be non-empty and the same length.")
    n_points = int(n_points)
    if n_points < 1:
        raise ValueError("n_points must be >= 1.")

    if not np.all(np.diff(freqs) >= 0.0):
        order = np.argsort(freqs, kind="stable")
        freqs = freqs[order]
        values = values[order]

    lo = float(freqs[0]) if f_min is None else float(f_min)
    hi = float(freqs[-1]) if f_max is None else float(f_max)
    target = log_grid(n_points, lo, hi, f_ref)

    def _interpolated() -> np.ndarray:
        if freqs.size == 1:
            return np.full(target.size, values[0])
        interp = interp1d(
            freqs, values, kind="linear", bounds_error=False, fill_value=(values[0], values[-1])
        )
        return np.asarray(interp(target), dtype=np.float64)

    if target.size < 2 or freqs.size < 2:
        out_mag = _interpolated()
    else:
        log_t = np.log10(target)
        inner = 10.0 ** (0.5 * (log_t[:-1] + log_t[1:]))
        first = 10.0 ** (log_t[0] - 0.5 * (log_t[1] - log_t[0]))
        last = 10.0 ** (log_t[-1] + 0.5 * (log_t[-1] - log_t[-2]))
        edges = np.concatenate(([first], inner, [last]))

        idx = np.searchsorted(freqs, edges, side="left")
        counts = np.diff(idx)

        power = 10.0 ** (values / 10.0)
        cumulative = np.concatenate(([0.0], np.cumsum(power)))
        sums = cumulative[idx[1:]] - cumulative[idx[:-1]]
        means = sums / np.maximum(counts, 1)
        with np.errstate(divide="ignore"):
            out_mag = 10.0 * np.log10(np.clip(means, 1e-30, None))

        sparse = counts < int(min_bins)
        if np.any(sparse):
            out_mag[sparse] = _interpolated()[sparse]

    if normalize_ref:
        idx_ref_out = int(np.argmin(np.abs(target - f_ref)))
        out_mag = out_mag - out_mag[idx_ref_out]

    return target, out_mag


def downsample_to_log_points(
    freqs: np.ndarray,
    mag_db: np.ndarray,
    n_points: int = 600,
    f_ref: float = F_REF,
    normalize_ref: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Resample to ~n_points log-spaced frequencies.
    Guarantees f_ref (1 kHz) is one of the output points.
    Optionally re-normalizes so f_ref = 0 dB exactly.

    Averages the power of every source bin inside each log cell instead of
    point-sampling it; cells too sparse to average fall back to linear
    interpolation.
    """
    return resample_log_band_average(
        freqs,
        mag_db,
        n_points=n_points,
        f_ref=f_ref,
        normalize_ref=normalize_ref,
    )


# ---------------------------------------------------------------------------
# RMS average across kept curves
# ---------------------------------------------------------------------------


def compute_rms_average(
    curves: list[tuple[np.ndarray, np.ndarray]],
    n_points: int = GRID_POINTS,
    f_ref: float = F_REF,
    f_min: float = F_LOW,
    f_max: float = F_HIGH,
    normalize_ref: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Average all kept curves in linear amplitude, then convert back to dB.
    Uses linear interpolation to a common grid.
    """
    if not curves:
        return np.array([]), np.array([])

    common_freqs = log_grid(n_points, f_min, f_max, f_ref)
    idx_ref = int(np.argmin(np.abs(common_freqs - f_ref)))

    sum_lin = np.zeros(n_points)
    count = 0
    for freqs, mag_db in curves:
        interp = interp1d(
            freqs, mag_db, kind="linear", bounds_error=False, fill_value=(mag_db[0], mag_db[-1])
        )
        vals = interp(common_freqs)
        # RMS average in linear (power) space
        sum_lin += 10.0 ** (vals / 10.0)
        count += 1

    avg_lin = sum_lin / count
    avg_db = 10.0 * np.log10(np.clip(avg_lin, 1e-30, None))

    if normalize_ref:
        avg_db -= avg_db[idx_ref]

    return common_freqs, avg_db


LOG_SPACING_TOLERANCE = 0.01
LOG_SPACING_MIN_UNIFORM_FRACTION = 0.99
MAX_RESAMPLED_SMOOTHING_POINTS = 4096


def _is_log_spaced(log_freqs: np.ndarray) -> bool:
    """True when the log2 frequency steps are uniform to within ~1 %.

    A handful of stray steps are tolerated because Fastgraph's own export grids
    move their nearest point onto exactly 1 kHz, which perturbs two steps of an
    otherwise perfectly log-spaced grid.
    """
    steps = np.diff(log_freqs)
    if steps.size == 0:
        return True
    median_step = float(np.median(steps))
    if not np.isfinite(median_step) or median_step <= 0.0:
        return False
    uniform = np.abs(steps - median_step) / median_step <= LOG_SPACING_TOLERANCE
    return float(np.mean(uniform)) >= LOG_SPACING_MIN_UNIFORM_FRACTION


def _smooth_on_log_grid(
    log_freqs: np.ndarray,
    values: np.ndarray,
    fraction: int,
) -> np.ndarray | None:
    """Gaussian-smooth values sampled on a uniform log2 frequency grid."""
    step = float(np.median(np.diff(log_freqs)))
    if not np.isfinite(step) or step <= 0.0:
        return None

    fwhm_oct = 1.0 / float(fraction)
    sigma_oct = fwhm_oct / (2.0 * np.sqrt(2.0 * np.log(2.0)))
    sigma_idx = sigma_oct / step
    if not np.isfinite(sigma_idx) or sigma_idx <= 0.0:
        return None

    radius = max(2, int(np.ceil(sigma_idx * 4.0)))
    offsets = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-0.5 * (offsets / sigma_idx) ** 2)
    kernel /= np.sum(kernel)

    padded = np.pad(values.astype(np.float64), (radius, radius), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def smooth_fractional_octave(
    freqs: np.ndarray,
    mag_db: np.ndarray,
    fraction: int = 48,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Apply Gaussian smoothing on a log-frequency axis.

    The smoothing bandwidth is specified in fractional octaves using the
    full-width at half maximum of the Gaussian window.

    The kernel radius is an index count, so it is only a fractional-octave
    bandwidth when the input is log-spaced. Inputs that are not log-spaced
    (linear FFT-bin grids from REW or ARTA, for example) are resampled onto a
    uniform log grid, smoothed there, and interpolated back onto the caller's
    frequencies. Log-spaced inputs take the original path unchanged.
    """
    if len(freqs) < 3 or len(freqs) != len(mag_db) or fraction <= 0:
        return freqs, mag_db

    log_freqs = np.log2(freqs)
    if not np.all(np.isfinite(log_freqs)):
        return freqs, mag_db

    if _is_log_spaced(log_freqs):
        smoothed = _smooth_on_log_grid(log_freqs, mag_db, fraction)
        if smoothed is None:
            return freqs, mag_db
        return freqs, smoothed.astype(np.float64)

    order = np.argsort(log_freqs, kind="stable")
    sorted_log = log_freqs[order]
    sorted_values = np.asarray(mag_db, dtype=np.float64)[order]
    log_min = float(sorted_log[0])
    log_max = float(sorted_log[-1])
    if not np.isfinite(log_min) or not np.isfinite(log_max) or log_max <= log_min:
        return freqs, mag_db

    n_points = int(min(MAX_RESAMPLED_SMOOTHING_POINTS, max(len(freqs), 3)))
    grid_log = np.linspace(log_min, log_max, n_points)
    grid_values = np.interp(grid_log, sorted_log, sorted_values)
    smoothed_grid = _smooth_on_log_grid(grid_log, grid_values, fraction)
    if smoothed_grid is None:
        return freqs, mag_db

    smoothed = np.interp(log_freqs, grid_log, smoothed_grid)
    return freqs, smoothed.astype(np.float64)
