import numpy as np
import pytest
import sounddevice as sd
from dms import channel_balance

from dms.channel_balance import (
    ChannelBalanceEngine,
    db_to_gain,
    frequency_limit,
    polyblep_square,
)


def test_channel_balance_frequency_limit_tracks_sample_rate() -> None:
    assert frequency_limit(48000) == 20000.0
    assert frequency_limit(32000) == 14400.0


def test_generator_gain_uses_dbfs_peak_scale() -> None:
    assert abs(db_to_gain(-6.0) - 10.0 ** (-6.0 / 20.0)) < 1e-12


def test_polyblep_square_is_finite_bounded_and_bipolar() -> None:
    phases = np.arange(4096, dtype=float) * (500.0 / 48000.0)
    result = polyblep_square(phases, 500.0 / 48000.0)
    assert np.all(np.isfinite(result))
    assert np.max(result) <= 1.0
    assert np.min(result) >= -1.0
    assert np.max(result) > 0.8
    assert np.min(result) < -0.8


def test_engine_keeps_phase_and_ramps_parameter_changes_and_stop() -> None:
    engine = ChannelBalanceEngine()
    engine._sample_rate = 48000
    inputs = np.column_stack(
        (
            np.full(257, 0.25, dtype=np.float32),
            np.full(257, 0.125, dtype=np.float32),
        )
    )
    first_output = np.zeros((257, 2), dtype=np.float32)
    engine._callback(inputs, first_output, 257, None, None)

    assert np.allclose(first_output[:, 0], first_output[:, 1])
    phase_after_first = engine._phase
    previous_tail = float(first_output[-1, 0])

    engine.set_parameters("square", 1000.0, -12.0)
    changed_output = np.zeros((257, 2), dtype=np.float32)
    engine._callback(inputs, changed_output, 257, None, None)

    assert engine._phase != 0.0
    assert engine._phase != phase_after_first
    assert changed_output[0, 0] == pytest.approx(previous_tail)
    assert abs(changed_output[-1, 0]) <= db_to_gain(-12.0) + 1e-6

    engine.stop()
    stopped_output = np.ones((257, 2), dtype=np.float32)
    with pytest.raises(sd.CallbackStop):
        engine._callback(inputs, stopped_output, 257, None, None)
    assert stopped_output[0, 0] == pytest.approx(float(changed_output[-1, 0]))
    assert stopped_output[-1, 0] == pytest.approx(0.0)


def test_snapshot_returns_raw_delta_inputs_and_signed_rms_difference() -> None:
    engine = ChannelBalanceEngine()
    engine._frames.append(
        np.column_stack(
            (
                np.full(128, 0.5, dtype=np.float32),
                np.full(128, 0.25, dtype=np.float32),
            )
        )
    )

    left, right, left_db, right_db, delta_db = engine.snapshot(64)

    np.testing.assert_allclose(left - right, 0.25)
    assert left_db == pytest.approx(-6.0206, abs=1e-3)
    assert right_db == pytest.approx(-12.0412, abs=1e-3)
    assert delta_db == pytest.approx(6.0206, abs=1e-3)


def test_engine_opens_one_two_by_two_duplex_stream(monkeypatch) -> None:
    calls = []
    engine = ChannelBalanceEngine()

    class _FakeStream:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def __enter__(self):
            engine.stop()
            engine._ramp_done.set()
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(channel_balance.sd, "Stream", _FakeStream)

    engine.run(
        input_device=7,
        output_device=9,
        sample_rate=48000,
        block_size=256,
        latency="low",
    )

    assert calls[0]["device"] == (7, 9)
    assert calls[0]["channels"] == (2, 2)
    assert calls[0]["callback"] == engine._callback
