"""The pure Measure session model: serialization, tolerance and rebuild."""

from __future__ import annotations

import numpy as np
import pytest

from dms.measure_session import (
    MEASURE_SESSION_SCHEMA_VERSION,
    KeptSweep,
    MeasureSession,
    UnsupportedMeasureSessionVersion,
    diagnostics_to_dict,
    distortion_summary,
)
from dms.measurement_alignment import MeasurementDiagnostics
from dms.processing import HarmonicAnalysis
from dms.session import SessionData
from dms.two_channel import TwoChannelCurvePair


def _curve(offset: float = 0.0, points: int = 600):
    freqs = np.geomspace(20.0, 20000.0, points)
    mag_db = np.linspace(-5.0, 5.0, points) + offset
    return freqs, mag_db


def _session_data() -> SessionData:
    return SessionData(
        rig="B&K 5128",
        brand="DMS",
        model="Demo",
        asset_tag="A-17",
        channel_side="L",
        eq_applied=True,
        open_back=False,
    )


def _diagnostics() -> MeasurementDiagnostics:
    return MeasurementDiagnostics(
        fs=48000,
        bluetooth_headphone_mode=False,
        latency="low",
        start_alignment_confidence_min=0.5,
        end_marker_confidence_min=0.4,
        timing_drift_max_ms=2.0,
        snr_db=61.25,
        coverage_db=(1.0, 2.0),
    )


def _harmonics() -> HarmonicAnalysis:
    freqs = np.array([100.0, 1000.0, 10000.0])
    return HarmonicAnalysis(
        freqs=freqs,
        linear_db=np.zeros(3),
        orders={2: np.array([-40.0, -50.0, np.nan])},
        thd_db=np.array([-40.0, -50.0, np.nan]),
        thd_percent=np.array([1.0, 0.3, np.nan]),
    )


def _populated(two_channel: bool = False) -> MeasureSession:
    session = MeasureSession(
        metadata=_session_data(),
        two_channel=two_channel,
        bottom_mode="separate",
        level_mode="dbspl",
        hrtf_path="/tmp/hrtf.txt",
        hrtf_name="Fixture",
        hrtf_enabled=True,
        notes="Pads reseated",
    )
    freqs, mag_db = _curve()
    session.add_sweep(
        freqs,
        mag_db,
        diagnostics=_diagnostics(),
        timing_quality=(0.9, 0.8, 0.25, 61.0),
        distortion=_harmonics(),
        note="first",
    )
    session.add_sweep(*_curve(1.0))
    session.add_pair(
        TwoChannelCurvePair(
            channel_1=_curve(0.5),
            channel_2=_curve(-0.5),
            channel_1_diagnostics=_diagnostics(),
            channel_2_diagnostics={"fs": 48000},
        )
    )
    return session


def _assert_curves_close(left, right) -> None:
    assert len(left) == len(right)
    for (lf, lm), (rf, rm) in zip(left, right):
        assert np.allclose(lf, rf, atol=1e-6)
        assert np.allclose(lm, rm, atol=1e-6)


def test_round_trip_preserves_sweeps_pairs_and_settings() -> None:
    session = _populated()

    restored = MeasureSession.from_dict(session.to_dict())

    assert restored.to_dict() == session.to_dict()
    assert restored.metadata == session.metadata
    assert restored.level_mode == "dbspl"
    assert restored.bottom_mode == "separate"
    assert restored.hrtf_path == "/tmp/hrtf.txt"
    assert restored.hrtf_name == "Fixture"
    assert restored.hrtf_enabled is True
    assert restored.notes == "Pads reseated"
    _assert_curves_close(restored.sweep_curves(), session.sweep_curves())
    assert restored.sweeps[0].kept_at == session.sweeps[0].kept_at
    assert restored.sweeps[0].note == "first"
    assert restored.sweeps[0].timing_quality == (0.9, 0.8, 0.25, 61.0)
    assert restored.pairs[0].kept_at == session.pairs[0].kept_at
    _assert_curves_close(
        [pair.channel_1.curve for pair in restored.pairs],
        [pair.channel_1.curve for pair in session.pairs],
    )


def test_arrays_round_trip_within_serialization_tolerance() -> None:
    freqs, mag_db = _curve()
    mag_db = mag_db + 1e-9  # below the six-decimal rounding
    session = MeasureSession(metadata=_session_data())
    session.add_sweep(freqs, mag_db)

    restored = MeasureSession.from_dict(session.to_dict())

    assert np.allclose(restored.sweeps[0].freqs, freqs, atol=1e-6)
    assert np.allclose(restored.sweeps[0].mag_db, mag_db, atol=1e-6)


def test_older_files_load_with_this_versions_defaults() -> None:
    freqs, mag_db = _curve(points=8)
    legacy = {
        "schema_version": 0,
        "metadata": {"rig": "Rig", "brand": "DMS", "model": "Old"},
        "sweeps": [{"freqs": freqs.tolist(), "mag_db": mag_db.tolist()}],
    }

    session = MeasureSession.from_dict(legacy)

    assert session.schema_version == MEASURE_SESSION_SCHEMA_VERSION
    assert session.metadata.model == "Old"
    assert session.two_channel is False
    assert session.bottom_mode == "combined"
    assert session.level_mode == "ref_1khz"
    assert session.hrtf_path is None
    assert session.hrtf_enabled is False
    assert session.notes == ""
    assert len(session.sweeps) == 1
    assert session.sweeps[0].diagnostics is None
    assert session.sweeps[0].timing_quality is None
    assert session.sweeps[0].kept_at


def test_newer_schema_version_is_rejected_as_unsupported() -> None:
    payload = _populated().to_dict()
    payload["schema_version"] = MEASURE_SESSION_SCHEMA_VERSION + 1

    with pytest.raises(UnsupportedMeasureSessionVersion, match="newer Fastgraph"):
        MeasureSession.from_dict(payload)


def test_is_empty_tracks_both_workspaces() -> None:
    session = MeasureSession(metadata=_session_data())
    assert session.is_empty() is True

    session.add_sweep(*_curve())
    assert session.is_empty() is False

    paired = MeasureSession(metadata=_session_data(), two_channel=True)
    assert paired.is_empty() is True
    paired.add_pair(_curve(), _curve(-1.0))
    assert paired.is_empty() is False
    assert paired.kept_count() == 1


def test_from_window_state_mirrors_the_measure_tab() -> None:
    kept_curves = [_curve(), _curve(1.0), _curve(2.0)]
    pairs = [TwoChannelCurvePair(channel_1=_curve(), channel_2=_curve(-1.0))]

    session = MeasureSession.from_window_state(
        session_data=_session_data(),
        kept_curves=kept_curves,
        pairs=pairs,
        two_channel=True,
        bottom_mode="separate",
        level_mode="dbspl",
        hrtf_path="/tmp/hrtf.txt",
        hrtf_name="Fixture",
        hrtf_enabled=True,
        sweep_diagnostics=[_diagnostics()],
        sweep_timing_quality=[(0.9, 0.8, 0.1, 60.0)],
        sweep_distortion=[_harmonics()],
        notes="From the window",
    )

    assert len(session.sweeps) == 3
    assert len(session.pairs) == 1
    assert session.two_channel is True
    assert session.level_mode == "dbspl"
    assert session.notes == "From the window"
    assert session.sweeps[0].diagnostics["fs"] == 48000
    assert session.sweeps[0].timing_quality == (0.9, 0.8, 0.1, 60.0)
    assert session.sweeps[0].distortion_summary["thd_percent_max"] == 1.0
    # The optional per-sweep sequences are shorter than the curve list.
    assert session.sweeps[1].diagnostics is None
    assert session.sweeps[2].timing_quality is None
    _assert_curves_close(session.sweep_curves(), kept_curves)


def test_curves_and_pair_objects_follow_the_active_workspace() -> None:
    single = _populated(two_channel=False)
    curves = single.curves()
    assert len(curves) == 2
    assert curves[0][0].shape == (600,)

    paired = _populated(two_channel=True)
    paired.add_pair(_curve(2.0), _curve(-2.0))

    assert len(paired.pair_objects()) == 2
    assert all(isinstance(item, TwoChannelCurvePair) for item in paired.pair_objects())
    left = paired.curves(selection="channel_1")
    assert len(left) == 2
    _assert_curves_close(left[:1], [_curve(0.5)])
    right = paired.curves(selection="channel_2")
    _assert_curves_close(right[:1], [_curve(-0.5)])
    combined = paired.curves(n_points=256)
    assert len(combined) == 2
    assert combined[0][0].shape == (256,)


def test_pair_objects_pass_diagnostics_through_as_dicts() -> None:
    session = _populated(two_channel=True)

    pair = session.pair_objects()[0]

    assert isinstance(pair.channel_1_diagnostics, dict)
    assert pair.channel_1_diagnostics["fs"] == 48000
    assert pair.channel_2_diagnostics == {"fs": 48000}


def test_diagnostics_dataclass_becomes_a_json_safe_dict() -> None:
    result = diagnostics_to_dict(_diagnostics())

    assert result["fs"] == 48000
    assert result["latency"] == "low"
    assert result["snr_db"] == 61.25
    assert result["coverage_db"] == [1.0, 2.0]
    assert result["failure_reason"] is None
    # A dictionary is accepted as-is, and ``None`` stays ``None``.
    assert diagnostics_to_dict({"fs": 44100}) == {"fs": 44100}
    assert diagnostics_to_dict(None) is None
    with pytest.raises(TypeError):
        diagnostics_to_dict(42)


def test_distortion_summary_reduces_a_harmonic_analysis() -> None:
    summary = distortion_summary(_harmonics())

    assert summary == {
        "thd_percent_median": 0.65,
        "thd_percent_max": 1.0,
        "thd_max_hz": 100.0,
    }
    assert distortion_summary(None) is None
    assert distortion_summary(summary) == summary


def test_distortion_summary_ignores_an_all_nan_analysis() -> None:
    analysis = HarmonicAnalysis(
        freqs=np.array([100.0, 1000.0]),
        linear_db=np.zeros(2),
        orders={},
        thd_db=np.array([np.nan, np.nan]),
        thd_percent=np.array([np.nan, np.nan]),
    )

    assert distortion_summary(analysis) is None


def test_non_finite_diagnostics_values_serialize_as_null() -> None:
    sweep = KeptSweep(
        freqs=np.array([100.0]),
        mag_db=np.array([0.0]),
        diagnostics={"snr_db": float("nan"), "peak": np.float64(2.5)},
    )

    assert sweep.diagnostics["snr_db"] is None
    assert sweep.diagnostics["peak"] == 2.5


def test_session_data_from_dict_ignores_unknown_and_missing_keys() -> None:
    metadata = SessionData.from_dict(
        {"rig": "Rig", "brand": "DMS", "model": "Demo", "unexpected": 1}
    )

    assert metadata.rig == "Rig"
    assert metadata.form_factor == "over-ear"
    assert metadata.eq_applied is False
    assert SessionData.from_dict({}).rig == ""


def test_source_path_is_runtime_only() -> None:
    session = _populated()
    session.source_path = "/tmp/session.fastgraph-measure.json"

    assert "source_path" not in session.to_dict()
    assert MeasureSession.from_dict(session.to_dict()).source_path is None
