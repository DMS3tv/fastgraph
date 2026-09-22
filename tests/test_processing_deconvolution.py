"""Sweep deconvolution: IR windowing, harmonic packets, band edges.

The reference system is a linear 2nd-order 40 Hz highpass plus a 2nd-order
15 kHz lowpass, driven by a 2 s / 48 kHz log sweep with 0.5 s of post-silence.
Everything here is pure DSP; nothing touches audio hardware or the UI.
"""

import numpy as np
import pytest
from scipy import signal

from dms.processing import (
    compute_frequency_response,
    deconvolve_sweep,
    frequency_response_from_ir,
    generate_log_sweep,
    harmonic_responses,
    harmonic_time_offsets,
    normalize_at_1khz,
    resample_log_band_average,
    window_impulse_response,
)

FS = 48000
DURATION = 2.0
F_LOW = 20.0
F_HIGH = 20000.0


def _sweep() -> np.ndarray:
    return generate_log_sweep(DURATION, FS, F_LOW, F_HIGH).astype(np.float64)


def _system() -> np.ndarray:
    """Second-order 40 Hz highpass cascaded with a 15 kHz lowpass."""
    return np.vstack(
        [
            signal.butter(2, 40.0, "highpass", fs=FS, output="sos"),
            signal.butter(2, 15000.0, "lowpass", fs=FS, output="sos"),
        ]
    )


def _excitation(sweep: np.ndarray, tail_s: float = 0.5) -> np.ndarray:
    return np.concatenate([sweep, np.zeros(int(round(tail_s * FS)))])


def _record(sweep: np.ndarray, tail_s: float = 0.5) -> np.ndarray:
    return signal.sosfilt(_system(), _excitation(sweep, tail_s))


def _curve(recording: np.ndarray, sweep: np.ndarray, **kwargs):
    """The 600-point normalized curve the application would plot."""
    freqs, mag_db = compute_frequency_response(recording, sweep, FS, F_LOW, F_HIGH, **kwargs)
    # A fixed grid so the windowed and legacy paths — whose first FFT bin
    # inside the band differs — land on exactly the same frequencies.
    return resample_log_band_average(
        freqs,
        normalize_at_1khz(freqs, mag_db),
        n_points=600,
        f_ref=1000.0,
        f_min=20.0,
        f_max=20000.0,
    )


def _largest_reversal(values: np.ndarray) -> float:
    """How far the curve turns back on itself, in its better direction.

    A monotonically rising segment never dips below its running maximum; a
    falling one never climbs above its running minimum. Taking the smaller of
    the two measures asks only that the segment be monotone *somehow*, which is
    what a band-edge rolloff must be.
    """
    if values.size < 2:
        return 0.0
    falling = float(np.max(values - np.minimum.accumulate(values)))
    rising = float(np.max(np.maximum.accumulate(values) - values))
    return min(falling, rising)


def test_deconvolved_ir_matches_legacy_response_within_0_1_db() -> None:
    sweep = _sweep()
    recording = _record(sweep)

    freqs_win, mag_win = _curve(recording, sweep, window=True)
    freqs_legacy, mag_legacy = _curve(recording, sweep, window=False)

    np.testing.assert_allclose(freqs_win, freqs_legacy)
    band = (freqs_win >= 20.0) & (freqs_win <= 20000.0)
    assert np.max(np.abs(mag_win[band] - mag_legacy[band])) < 0.1


def test_deconvolved_ir_peak_is_near_zero_delay() -> None:
    sweep = _sweep()
    deconv = deconvolve_sweep(_record(sweep), sweep, FS)

    # Circular: the peak sits at index 0/1, or a sample or two before it.
    signed = deconv.peak_index
    if signed > deconv.nfft // 2:
        signed -= deconv.nfft
    assert abs(signed) <= 2
    assert deconv.nfft >= 2 * max(sweep.size, deconv.recording_samples)
    assert deconv.sweep_samples == sweep.size
    assert deconv.duration_s == pytest.approx(DURATION)


def test_harmonic_time_offsets_match_farina_formula() -> None:
    offsets = harmonic_time_offsets(
        (1, 2, 3, 4, 5), duration_s=DURATION, f_low=F_LOW, f_high=F_HIGH
    )

    ratio = np.log(F_HIGH / F_LOW)
    assert offsets[1] == 0.0
    for order in (2, 3, 4, 5):
        assert offsets[order] == pytest.approx(DURATION * np.log(order) / ratio, rel=1e-12)
    # 2 s over three decades puts H2 about 200 ms before the linear response.
    assert offsets[2] == pytest.approx(0.2007, abs=1e-4)
    assert offsets[2] < offsets[3] < offsets[4] < offsets[5]


def test_window_uses_post_sweep_tail_when_available() -> None:
    sweep = _sweep()
    n_pre = int(round(0.005 * FS))

    short_tail = deconvolve_sweep(_record(sweep, tail_s=0.02), sweep, FS)
    long_tail = deconvolve_sweep(_record(sweep, tail_s=0.5), sweep, FS)

    assert short_tail.available_tail_ms == pytest.approx(20.0)
    short_window = window_impulse_response(short_tail)
    long_window = window_impulse_response(long_tail)

    # The 20 ms tail is the binding constraint; the 500 ms one is not.
    assert short_window.size == n_pre + int(round(0.020 * FS)) + 1
    assert long_window.size > short_window.size

    no_tail = deconvolve_sweep(_record(sweep, tail_s=0.0), sweep, FS)
    assert no_tail.available_tail_ms == 0.0
    # Degenerate gracefully: the whole response, exactly as before windowing.
    assert window_impulse_response(no_tail).size == no_tail.nfft


def test_window_post_length_capped_below_second_harmonic_delay() -> None:
    sweep = _sweep()
    deconv = deconvolve_sweep(_record(sweep, tail_s=1.0), sweep, FS)

    window = window_impulse_response(deconv)

    delta_t2 = harmonic_time_offsets((2,), duration_s=DURATION, f_low=F_LOW, f_high=F_HIGH)[2]
    expected_post = int(round(0.45 * delta_t2 * FS))
    assert window.size == int(round(0.005 * FS)) + expected_post + 1
    # 1000 ms of tail was available and 300 ms is the hard ceiling, so the
    # harmonic delay must be what actually limited the window.
    assert expected_post / FS * 1000.0 < 300.0
    assert expected_post < int(round(delta_t2 * FS))


def test_harmonic_responses_recovers_known_h2_h3_levels() -> None:
    sweep = _sweep()
    drive = _excitation(sweep, tail_s=0.3)
    recording = drive + 0.05 * drive**2 + 0.02 * drive**3

    analysis = harmonic_responses(
        deconvolve_sweep(recording, sweep, FS), f_low=F_LOW, f_high=F_HIGH
    )

    band = (analysis.freqs >= 100.0) & (analysis.freqs <= 5000.0)
    # a2/2 and a3/4 against a fundamental lifted to 1 + 3*a3/4 by x**3.
    assert np.allclose(analysis.orders[2][band], -32.17, atol=0.2)
    assert np.allclose(analysis.orders[3][band], -46.15, atol=0.2)
    # No fourth or fifth order was generated.
    # (Orders whose harmonic leaves the band are NaN, hence nanmax.)
    assert np.nanmax(analysis.orders[4][band]) < -80.0
    assert np.nanmax(analysis.orders[5][band]) < -80.0

    expected_thd = np.sqrt(10 ** (-32.17 / 10) + 10 ** (-46.15 / 10))
    assert np.allclose(analysis.thd_db[band], 20 * np.log10(expected_thd), atol=0.2)
    assert np.allclose(analysis.thd_percent[band], 100 * expected_thd, atol=0.05)

    # Harmonics that would land above the top of the band are not reported.
    assert np.all(np.isnan(analysis.orders[2][analysis.freqs > 10000.0]))
    assert np.all(np.isfinite(analysis.orders[2][analysis.freqs < 9000.0]))
    assert analysis.freqs.size == analysis.linear_db.size == 600


def test_windowing_rejects_harmonic_energy() -> None:
    sweep = _sweep()
    drive = _excitation(sweep)
    system = _system()
    clean = signal.sosfilt(system, drive)
    distorted = signal.sosfilt(system, drive + 0.05 * drive**2 + 0.02 * drive**3)

    freqs, clean_db = _curve(clean, sweep)
    _, distorted_db = _curve(distorted, sweep)

    # Above 15 kHz the second harmonic of the excitation folds back around
    # Nyquist, which is a property of the test signal rather than of the
    # window, so the comparison stops there.
    band = (freqs >= 20.0) & (freqs <= 15000.0)
    assert np.max(np.abs(distorted_db[band] - clean_db[band])) < 0.1

    _, clean_raw = _curve(clean, sweep, window=False)
    _, distorted_raw = _curve(distorted, sweep, window=False)
    # Without the window the same distortion visibly bends the curve.
    assert np.max(np.abs(distorted_raw[band] - clean_raw[band])) > 0.2


def test_band_edge_rolloff_has_no_ripple() -> None:
    sweep = _sweep()
    freqs, mag_db = _curve(_record(sweep), sweep)

    top = freqs >= 18000.0
    bottom = freqs <= 25.0
    assert np.count_nonzero(top) > 5
    assert np.count_nonzero(bottom) > 5
    assert _largest_reversal(mag_db[top]) <= 1.0
    assert _largest_reversal(mag_db[bottom]) <= 1.0


def test_deconvolution_handles_recording_shorter_than_sweep() -> None:
    sweep = _sweep()
    truncated = _record(sweep)[: sweep.size // 2]

    try:
        freqs, mag_db = compute_frequency_response(truncated, sweep, FS, F_LOW, F_HIGH)
    except ValueError:
        return  # A clean refusal is an acceptable answer.

    assert freqs.size == mag_db.size > 0
    assert np.all(np.isfinite(mag_db))

    deconv = deconvolve_sweep(truncated, sweep, FS)
    assert deconv.available_tail_ms == 0.0
    window = window_impulse_response(deconv)
    band_freqs, band_mag = frequency_response_from_ir(window, FS)
    assert np.all(np.isfinite(band_mag))
    assert band_freqs[0] >= F_LOW


def test_empty_input_is_rejected() -> None:
    with pytest.raises(ValueError):
        deconvolve_sweep(np.zeros(0), _sweep(), FS)
