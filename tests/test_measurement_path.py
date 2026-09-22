"""Pins every magnitude change on the measurement path.

Each test below proves one documented operation changes the curve by exactly
the stated amount and nothing else:

1. ``normalize_at_1khz``: subtracts the linearly interpolated 1 kHz value.
2. ``compute_rms_average``: power (not dB) mean, re-zeroed at 1 kHz only when
   ``normalize_ref`` is set.
3. ``MainWindow._on_sweep_finished`` (1 kHz reference mode): the pending curve is
   ``downsample_to_log_points(normalize_at_1khz(response))`` on 600 points.
4. ``MainWindow._on_sweep_finished`` (dB SPL mode): adds exactly
   ``absolute_spl_offset_db(...)`` and skips the 1 kHz re-zero; an uncalibrated
   input falls back to the reference path.
5. ``SweepWorker``: emits the aligned recording plus its tail, and the sweep
   unchanged.
6. ``CuratorWidget.offset_layer_to_zero_at_1khz``: a pure vertical offset,
   computed from the HRTF-corrected curve when an HRTF is applied.
7. Squiglink upload: the uploaded file is byte-for-byte what ``export_curve``
   writes for the displayed curve.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import dms.ui.main_window as main_window_module
from dms import audio_engine
from dms.console import ConsoleEventStore
from dms.curator.models import CurveData
from dms.curator.transforms import apply_layer_transform
from dms.export import export_curve
from dms.measurement_alignment import MeasurementDiagnostics
from dms.processing import (
    absolute_spl_offset_db,
    compute_rms_average,
    downsample_to_log_points,
    normalize_at_1khz,
    smooth_fractional_octave,
)
from dms.session import SessionData
from dms.ui.curator_widget import CuratorWidget
from dms.ui.main_window import AppState

_FREQS = np.logspace(np.log10(20.0), np.log10(20000.0), 4000)
# Arbitrary shape with a non-zero 1 kHz level, so any re-zero is visible.
_KNOWN = 7.0 + 3.0 * np.sin(np.log10(_FREQS) * 4.0) - 0.5 * np.log10(_FREQS)


def _sweep_ready_window(make_main_window, monkeypatch, settings=None):
    window = make_main_window(settings=settings)
    monkeypatch.setattr(
        main_window_module,
        "compute_frequency_response",
        lambda **_kwargs: (_FREQS.copy(), _KNOWN.copy()),
    )
    monkeypatch.setattr(main_window_module.QTimer, "singleShot", lambda *_args: None)
    window._distortion_analysis_allowed = lambda: False
    window._queue_target = 1
    window._state = AppState.SWEEPING
    return window


def _value_at_1khz(curve):
    freqs, mag = curve
    (idx,) = np.nonzero(freqs == 1000.0)
    assert idx.size == 1
    return float(mag[idx[0]])


def test_normalize_at_1khz_subtracts_the_interpolated_reference() -> None:
    freqs = np.array([100.0, 900.0, 1100.0, 10000.0])
    mag = np.array([1.0, 2.0, 4.0, 0.0])

    result = normalize_at_1khz(freqs, mag)

    # Halfway between 900 Hz (2 dB) and 1100 Hz (4 dB) is 3 dB.
    np.testing.assert_array_equal(result, mag - 3.0)
    assert float(np.interp(1000.0, freqs, result)) == pytest.approx(0.0, abs=1e-12)
    with pytest.raises(ValueError, match="out of data range"):
        normalize_at_1khz(np.array([2000.0, 20000.0]), np.array([0.0, 1.0]))


def test_rms_average_is_a_power_mean_and_rezeroes_only_when_asked() -> None:
    freqs = np.array([20.0, 1000.0, 20000.0])
    flat = [(freqs, np.zeros(3)), (freqs, np.full(3, 6.0))]

    _, raw = compute_rms_average(flat, normalize_ref=False)
    power_mean = 10.0 * np.log10((1.0 + 10.0**0.6) / 2.0)
    np.testing.assert_allclose(raw, power_mean, atol=1e-12)
    assert raw[0] == pytest.approx(3.963, abs=1e-3)  # not the 3 dB a dB mean gives

    sloped = [(freqs, np.array([2.0, 5.0, 1.0])), (freqs, np.array([4.0, 11.0, 3.0]))]
    grid, zeroed = compute_rms_average(sloped, normalize_ref=True)
    _, unzeroed = compute_rms_average(sloped, normalize_ref=False)
    assert _value_at_1khz((grid, zeroed)) == 0.0
    assert _value_at_1khz((grid, unzeroed)) != 0.0
    np.testing.assert_allclose(zeroed, unzeroed - _value_at_1khz((grid, unzeroed)), atol=1e-12)


def test_single_channel_sweep_is_normalized_then_downsampled_only(
    make_main_window, monkeypatch
) -> None:
    window = _sweep_ready_window(make_main_window, monkeypatch)

    window._on_sweep_finished(np.zeros(8), np.zeros(8))

    pending = window._pending_curve
    assert pending is not None
    assert len(pending[0]) == 600 and len(pending[1]) == 600
    assert _value_at_1khz(pending) == 0.0
    expected = downsample_to_log_points(_FREQS, normalize_at_1khz(_FREQS, _KNOWN), n_points=600)
    np.testing.assert_allclose(pending[0], expected[0], rtol=0, atol=1e-9)
    np.testing.assert_allclose(pending[1], expected[1], rtol=0, atol=1e-9)


def test_dbspl_mode_adds_the_calibrated_offset_and_skips_the_rezero(
    make_main_window, monkeypatch
) -> None:
    window = _sweep_ready_window(make_main_window, monkeypatch, {"measure_level_mode": "dbspl"})
    window._cal_store.set_sensitivity("Mic", 0.5)
    window._current_input_device_info = lambda: {"name": "Mic"}
    offset = absolute_spl_offset_db(
        sensitivity_pa_per_fs=0.5,
        output_level_db=float(window._queue_level_spin.value()),
    )

    window._on_sweep_finished(np.zeros(8), np.zeros(8))

    pending = window._pending_curve
    assert len(pending[1]) == 600
    unshifted = downsample_to_log_points(_FREQS, _KNOWN, n_points=600, normalize_ref=False)
    np.testing.assert_allclose(pending[0], unshifted[0], rtol=0, atol=1e-9)
    np.testing.assert_allclose(pending[1] - unshifted[1], offset, rtol=0, atol=1e-9)
    assert abs(_value_at_1khz(pending)) > 1.0

    # Uncalibrated input: falls back to the 1 kHz reference path.
    fallback = _sweep_ready_window(make_main_window, monkeypatch, {"measure_level_mode": "dbspl"})
    fallback._current_input_device_info = lambda: {"name": "Uncalibrated"}
    fallback._on_sweep_finished(np.zeros(8), np.zeros(8))
    reference = downsample_to_log_points(_FREQS, normalize_at_1khz(_FREQS, _KNOWN), n_points=600)
    np.testing.assert_allclose(fallback._pending_curve[1], reference[1], rtol=0, atol=1e-9)
    assert _value_at_1khz(fallback._pending_curve) == 0.0


def test_sweep_worker_emits_aligned_recording_plus_tail(monkeypatch) -> None:
    aligned = np.array([0.1, -0.2, 0.3], dtype=np.float32)
    tail = np.array([0.04, -0.05], dtype=np.float32)
    sweep = np.array([0.5, -0.5, 0.25], dtype=np.float32)
    device = {"max_input_channels": 1, "max_output_channels": 2}
    monkeypatch.setattr(audio_engine, "device_by_index", lambda *_a, **_kw: device)
    monkeypatch.setattr(
        audio_engine,
        "build_measurement_layout",
        lambda **_kwargs: SimpleNamespace(total_samples=1, fs=48000),
    )
    monkeypatch.setattr(
        audio_engine,
        "build_output_signal",
        lambda _layout, n_out_ch: np.zeros((1, n_out_ch), dtype=np.float32),
    )
    monkeypatch.setattr(
        audio_engine,
        "align_recording_to_layout",
        lambda **_kwargs: SimpleNamespace(
            aligned_recording=aligned,
            aligned_recording_tail=tail,
            diagnostics=MeasurementDiagnostics(
                fs=48000,
                bluetooth_headphone_mode=False,
                latency="low",
                start_alignment_confidence_min=9.0,
                end_marker_confidence_min=7.0,
                timing_drift_max_ms=35.0,
            ),
            start=SimpleNamespace(start_confidence=10.0),
            end=SimpleNamespace(marker_confidence=8.0, timing_error_ms=0.0),
            snr_db=60.0,
        ),
    )
    times = iter([0.0, 1.0, 2.0, 3.0])
    monkeypatch.setattr(audio_engine.time, "monotonic", lambda: next(times, 3.0))
    monkeypatch.setattr(audio_engine.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(audio_engine.sd, "wait", lambda: None)
    monkeypatch.setattr(
        audio_engine.sd, "playrec", lambda *_a, **_kw: np.ones((5, 1), dtype=np.float32)
    )

    worker = audio_engine.SweepWorker()
    emitted = []
    errors = []
    worker.finished.connect(lambda rec, swp: emitted.append((rec, swp)))
    worker.error.connect(errors.append)
    worker.run(
        sweep=sweep, output_device=1, input_device=2, input_channel=0, fs=48000, buffer_size=256
    )

    assert errors == []
    assert len(emitted) == 1
    recording, passed_sweep = emitted[0]
    np.testing.assert_array_equal(recording, np.concatenate([aligned, tail]))
    np.testing.assert_array_equal(passed_sweep, sweep)


class _FakeHrtf:
    def __init__(self, freqs, mags) -> None:
        self.path = Path("hrtf.txt")
        self.name = "hrtf"
        self.freqs = freqs
        self.mags = mags

    def evaluate(self, freqs):
        return np.interp(freqs, self.freqs, self.mags)


def test_curator_offset_to_1khz_uses_the_corrected_curve(qapp) -> None:
    freqs = np.array([100.0, 1000.0, 10000.0])
    source = np.array([4.0, 8.0, 2.0])
    widget = CuratorWidget(ConsoleEventStore())
    try:
        plain = widget.add_curve(
            CurveData(kind="fr", freqs=freqs, mag_db=source),
            "plain",
            normalize=False,
            animate=False,
        )
        with_hrtf = widget.add_curve(
            CurveData(kind="fr", freqs=freqs, mag_db=source),
            "hrtf",
            normalize=False,
            animate=False,
            hrtf=_FakeHrtf(freqs, np.array([1.0, 3.0, 0.0])),
        )

        widget.offset_layer_to_zero_at_1khz(plain)
        widget.offset_layer_to_zero_at_1khz(with_hrtf)

        assert plain.vertical_offset_db == -8.0
        np.testing.assert_allclose(apply_layer_transform(plain).mag_db, [-4.0, 0.0, -6.0])
        # Corrected curve is source - HRTF = [3, 5, 2]; the offset cancels its 5 dB.
        assert with_hrtf.vertical_offset_db == -5.0
        np.testing.assert_allclose(apply_layer_transform(with_hrtf).mag_db, [-2.0, 0.0, -3.0])
        # Source arrays are never modified.
        np.testing.assert_array_equal(plain.curve.mag_db, source)
        np.testing.assert_array_equal(with_hrtf.curve.mag_db, source)
    finally:
        widget.close()
        widget.deleteLater()


class _AcceptedAuth:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def exec(self):
        return main_window_module.QDialog.DialogCode.Accepted

    def username(self):
        return "user"

    def password(self):
        return "pw"

    def remember_credentials(self):
        return False

    def name_modifier(self):
        return ""


def _data_without_date(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.startswith("* Export Date:")]


def test_squiglink_upload_sends_exactly_the_export(make_main_window, monkeypatch, tmp_path) -> None:
    window = make_main_window(
        session=SessionData(rig="Rig", brand="DMS", model="Demo", channel_side="L")
    )
    window._average = (_FREQS.copy(), _KNOWN - _KNOWN[0])
    monkeypatch.setattr(main_window_module, "SquiglinkAuthDialog", _AcceptedAuth)
    window._squiglink_endpoint = lambda: ("sftp.example", 22)
    window._ensure_upload_metadata = lambda **_kwargs: True
    uploaded: dict[str, str] = {}

    def _capture(*, local_path, **_kwargs):
        uploaded["text"] = Path(local_path).read_text(encoding="utf-8")
        Path(local_path).unlink()

    window._start_squiglink_upload = _capture

    window._upload_to_squiglink()

    displayed = smooth_fractional_octave(*window._average, fraction=48)
    direct = tmp_path / "direct.txt"
    export_curve(
        freqs=displayed[0],
        mag_db=displayed[1],
        session=window._session,
        output_path=direct,
        compensated=False,
        hrtf=None,
        n_sweeps=window._active_measure_count(),
        smoothing_fraction=48,
        level_mode="ref_1khz",
    )
    assert "text" in uploaded
    assert _data_without_date(uploaded["text"]) == _data_without_date(
        direct.read_text(encoding="utf-8")
    )
