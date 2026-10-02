"""Golden end-to-end measurement test.

One synthetic headphone is measured three times through the whole chain the
app runs, in the app's order and with the app's arguments:

    generate_log_sweep -> build_measurement_layout (standard, wired)
    -> simulated device -> align_recording_to_layout (+ tail, as SweepWorker
    emits it) -> compute_frequency_response -> normalize_at_1khz
    -> downsample_to_log_points(600) -> compute_rms_average -> percentile_band
    -> smooth_fractional_octave(DEFAULT_SMOOTHING) -> export_curve and
    export_variation

The two exported files are compared with ``tests/golden/measurement_reference.txt``
to 1e-6 dB, so any change that alters a measured frequency response fails here.
The device is plain deterministic arithmetic (hard-coded RBJ biquads, a fixed
delay, a sum of sinusoids as "noise"): no random generator and no scipy filter
design, whose output is not guaranteed stable across versions.

If a change is meant to alter a measurement result, regenerate the reference::

    FASTGRAPH_UPDATE_GOLDEN=1 python -m pytest tests/test_golden_measurement.py

The run rewrites the reference and skips. Commit the new file and say why the
response changed. Without the variable a missing reference is a failure.
"""

import math
import os
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import lfilter

from dms.export import export_curve, export_variation
from dms.measurement_alignment import AlignmentSettings, align_recording_to_layout
from dms.measurement_layout import build_measurement_layout, build_output_signal
from dms.processing import (
    DEFAULT_SMOOTHING,
    F_HIGH,
    F_LOW,
    F_REF,
    GRID_POINTS,
    compute_frequency_response,
    compute_rms_average,
    downsample_to_log_points,
    generate_log_sweep,
    normalize_at_1khz,
    percentile_band,
    smooth_fractional_octave,
)
from dms.session import SessionData

REFERENCE = Path(__file__).parent / "golden" / "measurement_reference.txt"

# App defaults (settings_manager), written out so a default change is visible.
FS = 48000
SWEEP_S = 2.0
PRE_SILENCE_S = 0.2
POST_SILENCE_S = 0.5
OUTPUT_LEVEL_DB = -6.0

DELAY_SAMPLES = int(round(0.007 * FS))
# (recording scale dB, peaking gain dB) per sweep.
SWEEPS = ((0.0, 6.0), (0.4, 5.5), (-0.3, 6.5))
# Eight incommensurate tones (ratios are powers of e), summing to <= -70 dBFS peak.
NOISE_FREQS = [23.0 * math.exp(0.9 * k) for k in range(8)]
NOISE_AMP = 10.0 ** (-70.0 / 20.0) / len(NOISE_FREQS)
# One unit in the exported sixth decimal, plus float slack for parsed text.
ATOL_DB = 1e-6 + 1e-12


def _rbj(kind: str, f0: float, q: float, gain_db: float = 0.0) -> tuple[list, list]:
    """Audio EQ Cookbook biquad, normalized so a0 == 1."""
    w0 = 2.0 * math.pi * f0 / FS
    cos_w0 = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * q)
    if kind == "highpass":
        b = [(1 + cos_w0) / 2, -(1 + cos_w0), (1 + cos_w0) / 2]
        a = [1 + alpha, -2 * cos_w0, 1 - alpha]
    elif kind == "lowpass":
        b = [(1 - cos_w0) / 2, 1 - cos_w0, (1 - cos_w0) / 2]
        a = [1 + alpha, -2 * cos_w0, 1 - alpha]
    else:  # peaking
        amp = 10.0 ** (gain_db / 40.0)
        b = [1 + alpha * amp, -2 * cos_w0, 1 - alpha * amp]
        a = [1 + alpha / amp, -2 * cos_w0, 1 - alpha / amp]
    return [x / a[0] for x in b], [x / a[0] for x in a]


def _filters(peak_db: float) -> list[tuple[list, list]]:
    butterworth_q = 1.0 / math.sqrt(2.0)
    return [
        _rbj("highpass", 35.0, butterworth_q),
        _rbj("peaking", 3000.0, 1.4, peak_db),
        _rbj("lowpass", 16000.0, butterworth_q),
    ]


def _analytic_db(freqs: np.ndarray, peak_db: float) -> np.ndarray:
    z_inv = np.exp(-2j * np.pi * np.asarray(freqs, dtype=float) / FS)
    h = np.ones_like(z_inv)
    for b, a in _filters(peak_db):
        h *= (b[0] + b[1] * z_inv + b[2] * z_inv**2) / (a[0] + a[1] * z_inv + a[2] * z_inv**2)
    return 20.0 * np.log10(np.abs(h))


def _analytic_average_db(freqs: np.ndarray) -> np.ndarray:
    """Power mean of the three sweeps' analytic responses, each 0 dB at 1 kHz."""
    powers = [
        10.0 ** ((_analytic_db(freqs, peak) - _analytic_db(np.array([F_REF]), peak)) / 10.0)
        for _, peak in SWEEPS
    ]
    return 10.0 * np.log10(np.mean(powers, axis=0))


def _record(played: np.ndarray, scale_db: float, peak_db: float) -> np.ndarray:
    """What the sound card hands back: filtered, delayed, scaled, plus tones."""
    response = played.astype(np.float64)
    for b, a in _filters(peak_db):
        response = lfilter(b, a, response)
    delayed = np.concatenate([np.zeros(DELAY_SAMPLES), response])[: len(played)]
    t = np.arange(len(played)) / FS
    tones = sum(
        NOISE_AMP * np.sin(2.0 * np.pi * f * t + 1.1 * k) for k, f in enumerate(NOISE_FREQS)
    )
    return (delayed * 10.0 ** (scale_db / 20.0) + tones).astype(np.float32)


def _measure(sweep: np.ndarray, layout, scale_db: float, peak_db: float):
    played = build_output_signal(layout, 1)[:, 0]
    alignment = align_recording_to_layout(
        rec_mono=_record(played, scale_db, peak_db),
        sweep=sweep,
        layout=layout,
        settings=AlignmentSettings(),
    )
    # SweepWorker emits the aligned slice followed by the recorded tail.
    recording = alignment.aligned_recording
    tail = alignment.aligned_recording_tail
    if tail is not None and len(tail) > 0:
        recording = np.concatenate([recording, tail])
    # MeasureController.on_sweep_finished, 1 kHz reference mode.
    freqs, mag_db = compute_frequency_response(
        recording=recording, sweep=sweep, fs=FS, f_low=F_LOW, f_high=F_HIGH
    )
    mag_db = normalize_at_1khz(freqs, mag_db, f_ref=F_REF)
    return downsample_to_log_points(freqs, mag_db, n_points=600, f_ref=F_REF, normalize_ref=True)


def _without_date(text: str) -> str:
    return "".join(
        line for line in text.splitlines(keepends=True) if not line.startswith("* Export Date:")
    )


@pytest.fixture(scope="module")
def golden(tmp_path_factory):
    # MeasureController.start_next_sweep scales the sweep by the output level.
    sweep = generate_log_sweep(duration=SWEEP_S, fs=FS, f_low=F_LOW, f_high=F_HIGH)
    sweep = (sweep * 10.0 ** (OUTPUT_LEVEL_DB / 20.0)).astype(np.float32, copy=False)
    layout = build_measurement_layout(
        sweep=sweep,
        fs=FS,
        pre_silence_s=PRE_SILENCE_S,
        post_silence_s=POST_SILENCE_S,
        bluetooth_headphone_mode=False,
    )
    kept = [_measure(sweep, layout, scale, peak) for scale, peak in SWEEPS]

    # recompute_average, variation_from_curves, bottom_curve_for_display.
    average = compute_rms_average(
        kept, n_points=GRID_POINTS, f_ref=F_REF, f_min=F_LOW, f_max=F_HIGH, normalize_ref=True
    )
    band = percentile_band(kept, grid=average[0], smoothing=DEFAULT_SMOOTHING, hrtf=None)
    displayed = smooth_fractional_octave(*average, fraction=DEFAULT_SMOOTHING)

    # MeasureIO.export_average / export_variation, no HRTF, 1 kHz reference.
    out = tmp_path_factory.mktemp("golden")
    session = SessionData(rig="Golden Rig", brand="DMS", model="Synthetic Reference")
    common = {
        "session": session,
        "compensated": False,
        "hrtf": None,
        "n_sweeps": len(kept),
        "smoothing_fraction": DEFAULT_SMOOTHING,
        "level_mode": "ref_1khz",
    }
    export_curve(freqs=displayed[0], mag_db=displayed[1], output_path=out / "average.txt", **common)
    export_variation(
        freqs=band.freqs,
        p10_db=band.p10,
        p25_db=band.p25,
        median_db=band.median,
        p75_db=band.p75,
        p90_db=band.p90,
        p0_db=band.p0,
        p100_db=band.p100,
        output_path=out / "variation.txt",
        **common,
    )
    return {
        "average": average,
        "displayed": displayed,
        "band": band,
        "average_text": (out / "average.txt").read_text(encoding="utf-8"),
        "variation_text": (out / "variation.txt").read_text(encoding="utf-8"),
    }


def test_golden_chain_obeys_the_physics(golden) -> None:
    freqs, mag_db = golden["average"]
    assert float(np.interp(F_REF, freqs, mag_db)) == pytest.approx(0.0, abs=1e-12)

    shown_freqs, shown_db = golden["displayed"]
    for freq, tolerance in ((3000.0, 0.2), (35.0, 0.3)):
        measured = float(np.interp(freq, shown_freqs, shown_db))
        expected = float(_analytic_average_db(np.array([freq]))[0])
        assert abs(measured - expected) < tolerance, (freq, measured, expected)

    band = golden["band"]
    stacked = np.vstack([band.p0, band.p10, band.p25, band.median, band.p75, band.p90, band.p100])
    assert np.all(np.diff(stacked, axis=0) >= -1e-12)
    assert float(np.max(band.p100 - band.p0)) > 0.5  # the band is not degenerate


def _sections(text: str) -> dict[str, str]:
    average, variation = text.split("# variation\n")
    return {"average": average.removeprefix("# average\n"), "variation": variation}


def _parse(text: str) -> tuple[list[str], np.ndarray]:
    lines = _without_date(text).splitlines()
    header = [line for line in lines if line.startswith("*")]
    rows = np.array(
        [[float(v) for v in line.split("\t")] for line in lines if not line.startswith("*")]
    )
    return header, rows


def _compare(name: str, actual_text: str, expected_text: str) -> None:
    actual_header, actual = _parse(actual_text)
    expected_header, expected = _parse(expected_text)
    assert actual_header == expected_header, f"{name}: header lines changed"
    assert actual.shape == expected.shape, f"{name}: {actual.shape} rows vs {expected.shape}"
    np.testing.assert_array_equal(actual[:, 0], expected[:, 0], err_msg=f"{name}: frequencies")
    deviation = np.abs(actual[:, 1:] - expected[:, 1:])
    row, column = np.unravel_index(int(np.argmax(deviation)), deviation.shape)
    np.testing.assert_allclose(
        actual[:, 1:],
        expected[:, 1:],
        rtol=0,
        atol=ATOL_DB,
        err_msg=(
            f"{name}: max deviation {deviation[row, column]:.6f} dB at "
            f"{actual[row, 0]:.4f} Hz (value column {column + 1})"
        ),
    )


def test_golden_chain_matches_reference(golden) -> None:
    current = (
        "# average\n"
        + _without_date(golden["average_text"])
        + "# variation\n"
        + _without_date(golden["variation_text"])
    )
    if os.environ.get("FASTGRAPH_UPDATE_GOLDEN") == "1":
        REFERENCE.parent.mkdir(exist_ok=True)
        REFERENCE.write_text(current, encoding="utf-8")
        pytest.skip(f"Golden reference rewritten: {REFERENCE}")
    if not REFERENCE.exists():
        pytest.fail(f"{REFERENCE} is missing; generate it with FASTGRAPH_UPDATE_GOLDEN=1")

    expected = _sections(REFERENCE.read_text(encoding="utf-8"))
    actual = _sections(current)
    for name in ("average", "variation"):
        _compare(name, actual[name], expected[name])
