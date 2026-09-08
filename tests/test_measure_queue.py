"""Transition-table tests for the pure measurement-queue state machine.

No Qt, no audio, no numpy: the whole file runs in well under a second. The
parametrized ``test_transition_row`` block is the executable form of the
transition table documented in ``dms/measure_queue.py``; the named tests below
it pin the specific review findings the extraction is meant to close.
"""

from __future__ import annotations

import pytest

from dms.measure_queue import (
    FIRST_CHANNEL_UNAVAILABLE,
    MAX_SWEEP_ATTEMPTS,
    Effect,
    MeasurementQueue,
    QueueState,
)
from dms.measurement_alignment import (
    MeasurementFailureReason,
    is_device_failure,
    is_retryable_timing_failure,
)


CURVE = ("freqs", "mags")
PAIR = ("pair",)
TIMING = (12.0, 9.0, 1.5, 22.0)


def queue_in(state: QueueState, **fields) -> MeasurementQueue:
    """Build a queue parked in ``state`` with the given fields."""
    queue = MeasurementQueue(**fields)
    queue.state = state
    return queue


def running(**fields) -> MeasurementQueue:
    """A one-channel queue mid-run: target 3, index 0, one attempt spent."""
    defaults = dict(target=3, index=0, attempts=1)
    defaults.update(fields)
    return queue_in(QueueState.QUEUE_RUNNING, **defaults)


# ---------------------------------------------------------------------------
# The transition table, one parametrized case per row
# ---------------------------------------------------------------------------


def _row_01(queue: MeasurementQueue):
    return queue.begin(target=3, two_channel=False)


def _row_02(queue: MeasurementQueue):
    return queue.begin(target=5, two_channel=True)


TRANSITION_ROWS = [
    pytest.param(
        lambda: MeasurementQueue(),
        _row_01,
        QueueState.QUEUE_RUNNING,
        (Effect.STOP_BALANCE, Effect.START_SWEEP),
        lambda q: (q.target, q.index, q.attempts, q.two_channel) == (3, 0, 0, False),
        id="row01-idle-begin-starts-queue",
    ),
    pytest.param(
        lambda: running(),
        _row_02,
        QueueState.QUEUE_RUNNING,
        (),
        lambda q: (q.target, q.two_channel) == (3, False),
        id="row02-begin-ignored-unless-idle",
    ),
    pytest.param(
        lambda: running(target=3, index=1, attempts=1, two_channel=True,
                        pending_pair=PAIR, pending_pair_first_raw=CURVE,
                        last_diagnostics="stale"),
        lambda q: q.begin_sweep(),
        QueueState.SWEEPING,
        (Effect.STOP_BALANCE, Effect.START_SWEEP),
        lambda q: (
            q.attempts == 2
            and q.stage == 1
            and q.pending_pair is None
            and q.pending_pair_first_raw is None
            and q.pending_pair_first_diagnostics is None
            and q.last_diagnostics is None
            and q.last_timing_quality is None
            and q.last_distortion is None
        ),
        id="row03-begin-sweep-counts-attempt-and-clears-stage",
    ),
    pytest.param(
        lambda: running(target=3, index=3, attempts=2),
        lambda q: q.begin_sweep(),
        QueueState.IDLE,
        (Effect.FINISH,),
        lambda q: (q.target, q.index, q.attempts) == (0, 0, 0),
        id="row04-begin-sweep-at-target-finishes",
    ),
    pytest.param(
        lambda: queue_in(QueueState.QUEUE_RUNNING, target=0, index=0),
        lambda q: q.begin_sweep(),
        QueueState.IDLE,
        (),
        lambda q: not q.is_active(),
        id="row05-begin-sweep-without-queue-idles",
    ),
    pytest.param(
        lambda: running(two_channel=True, attempts=1, stage=1,
                        pending_pair_first_raw=CURVE),
        lambda q: q.begin_sweep(second_stage=True),
        QueueState.SWEEPING,
        (Effect.START_SWEEP,),
        lambda q: q.stage == 2 and q.attempts == 1
        and q.pending_pair_first_raw is CURVE,
        id="row06-second-stage-keeps-attempts-and-first-channel",
    ),
    pytest.param(
        lambda: queue_in(QueueState.SWEEPING, target=3, index=0, attempts=1),
        lambda q: q.on_stage_result(curve=CURVE, diagnostics="diag", timing=TIMING),
        QueueState.PASS_FAIL,
        (Effect.SHOW_REVIEW, Effect.REDRAW),
        lambda q: q.pending_curve is CURVE
        and q.last_diagnostics == "diag"
        and q.last_timing_quality == TIMING,
        id="row07-single-channel-result-goes-to-review",
    ),
    pytest.param(
        lambda: queue_in(QueueState.SWEEPING, target=3, index=0, attempts=1,
                         two_channel=True, stage=1),
        lambda q: q.on_stage_result(curve=CURVE, diagnostics="diag1", timing=TIMING),
        QueueState.QUEUE_RUNNING,
        (Effect.START_SECOND_STAGE,),
        lambda q: q.pending_pair_first_raw is CURVE
        and q.pending_pair_first_diagnostics == "diag1"
        and q.start_second_stage is True
        and q.pending_pair is None,
        id="row08-first-channel-parks-and-requests-second",
    ),
    pytest.param(
        lambda: queue_in(QueueState.SWEEPING, target=3, index=0, attempts=1,
                         two_channel=True, stage=2,
                         pending_pair_first_raw=CURVE,
                         pending_pair_first_diagnostics="diag1"),
        lambda q: q.on_stage_result(curve=PAIR, diagnostics="diag2", timing=TIMING),
        QueueState.PASS_FAIL,
        (Effect.SHOW_REVIEW, Effect.REDRAW),
        lambda q: q.pending_pair is PAIR and q.stage == 2
        and q.pending_pair_first_raw is CURVE,
        id="row09-second-channel-completes-the-pair",
    ),
    pytest.param(
        lambda: queue_in(QueueState.SWEEPING, target=3, index=0, attempts=1,
                         two_channel=True, stage=2, pending_pair_first_raw=None),
        lambda q: q.on_stage_result(curve=PAIR, diagnostics="diag2", timing=TIMING),
        QueueState.IDLE,
        (Effect.SHOW_ERROR,),
        lambda q: (q.target, q.index, q.attempts) == (0, 0, 0)
        and q.pending_pair is None,
        id="row10-missing-first-channel-is-terminal",
    ),
    pytest.param(
        lambda: running(target=3, index=1, attempts=1),
        lambda q: q.on_error(message="timing drift too large",
                             timing_failure=True, device_failure=False),
        QueueState.QUEUE_RUNNING,
        (Effect.PROMPT_RETRY,),
        lambda q: (q.target, q.index, q.attempts) == (3, 1, 1),
        id="row11-retryable-error-prompts-retry",
    ),
    pytest.param(
        lambda: running(target=3, index=1, attempts=1, pending_curve=CURVE),
        lambda q: q.on_error(message="PortAudio error: no device",
                             timing_failure=False, device_failure=True),
        QueueState.IDLE,
        (Effect.SHOW_ERROR, Effect.RESUME_MONITOR),
        lambda q: (q.target, q.index, q.attempts) == (0, 0, 0)
        and q.pending_curve is None,
        id="row12-terminal-error-clears-everything",
    ),
    pytest.param(
        lambda: running(target=3, index=1, attempts=1),
        lambda q: q.on_retry_accepted(),
        QueueState.QUEUE_RUNNING,
        (Effect.START_SWEEP,),
        lambda q: (q.target, q.index, q.attempts) == (3, 1, 1),
        id="row13-retry-accepted-restarts-the-sweep",
    ),
    pytest.param(
        lambda: running(target=3, index=1, attempts=1),
        lambda q: q.on_retry_declined(),
        QueueState.IDLE,
        (Effect.RESUME_MONITOR, Effect.REDRAW),
        lambda q: (q.target, q.index, q.attempts) == (0, 0, 0),
        id="row14-retry-declined-cancels-the-queue",
    ),
    pytest.param(
        lambda: queue_in(QueueState.PASS_FAIL, target=3, index=0, attempts=2,
                         pending_curve=CURVE),
        lambda q: q.on_keep(kept_total=1),
        QueueState.QUEUE_RUNNING,
        (Effect.REDRAW, Effect.START_SWEEP),
        lambda q: (q.index, q.attempts, q.target) == (1, 0, 3)
        and q.pending_curve is None and q.stage == 0,
        id="row15a-keep-advances-the-index",
    ),
    pytest.param(
        lambda: queue_in(QueueState.PASS_FAIL, target=2, index=1, attempts=2,
                         two_channel=True, stage=2, pending_pair=PAIR,
                         pending_pair_first_raw=CURVE),
        lambda q: q.on_keep(kept_total=2),
        QueueState.IDLE,
        (Effect.REDRAW, Effect.FINISH),
        lambda q: (q.index, q.attempts, q.target) == (0, 0, 0)
        and q.pending_pair is None and q.pending_pair_first_raw is None,
        id="row15b-keep-on-last-index-finishes",
    ),
    pytest.param(
        lambda: queue_in(QueueState.PASS_FAIL, target=3, index=0, attempts=2),
        lambda q: q.on_keep(kept_total=0),
        QueueState.PASS_FAIL,
        (),
        lambda q: (q.index, q.attempts) == (0, 2),
        id="row16-keep-without-pending-is-a-noop",
    ),
    pytest.param(
        lambda: queue_in(QueueState.PASS_FAIL, target=3, index=1, attempts=2,
                         pending_curve=CURVE, two_channel=True, stage=2,
                         pending_pair=PAIR),
        lambda q: q.on_fail(),
        QueueState.QUEUE_RUNNING,
        (Effect.REDRAW, Effect.START_SWEEP),
        lambda q: (q.index, q.attempts, q.target) == (1, 0, 3)
        and q.pending_curve is None and q.pending_pair is None and q.stage == 0,
        id="row17-fail-repeats-index-with-fresh-budget",
    ),
    pytest.param(
        lambda: running(target=3, index=1, attempts=2),
        lambda q: q.on_fail(),
        QueueState.QUEUE_RUNNING,
        (),
        lambda q: (q.index, q.attempts) == (1, 2),
        id="row18-fail-outside-review-is-a-noop",
    ),
    pytest.param(
        lambda: queue_in(QueueState.SWEEPING, target=3, index=1, attempts=2,
                         two_channel=True, stage=2, pending_pair=PAIR,
                         pending_pair_first_raw=CURVE),
        lambda q: q.cancel(),
        QueueState.IDLE,
        (Effect.RESUME_MONITOR, Effect.REDRAW),
        lambda q: (q.target, q.index, q.attempts) == (0, 0, 0),
        id="row19-cancel-clears-everything",
    ),
    pytest.param(
        lambda: running(target=3, index=3, attempts=1),
        lambda q: q.finish(),
        QueueState.IDLE,
        (Effect.RESUME_MONITOR,),
        lambda q: (q.target, q.index, q.attempts) == (0, 0, 0),
        id="row20-finish-clears-everything",
    ),
    pytest.param(
        lambda: queue_in(QueueState.IDLE, target=0, index=0, pending_curve=CURVE),
        lambda q: q.clear_all(),
        QueueState.IDLE,
        (Effect.REDRAW,),
        lambda q: q.pending_curve is None and not q.is_active(),
        id="row21-clear-all-when-idle",
    ),
]


@pytest.mark.parametrize("build,event,expected_state,expected_effects,check", TRANSITION_ROWS)
def test_transition_row(build, event, expected_state, expected_effects, check):
    queue = build()
    decision = event(queue)
    assert decision.state == expected_state
    assert queue.state == expected_state
    assert decision.effects == expected_effects
    assert check(queue)


def test_row22_allows_device_reselect_is_false_while_busy():
    for state in (
        QueueState.QUEUE_RUNNING,
        QueueState.SWEEPING,
        QueueState.PASS_FAIL,
    ):
        queue = queue_in(state, target=3)
        assert queue.allows_device_reselect() is False


# ---------------------------------------------------------------------------
# Named behaviour tests
# ---------------------------------------------------------------------------


def test_queue_state_values_match_app_state_strings():
    # The window compares these as plain strings (AppState in main_window.py).
    assert QueueState.IDLE.value == "idle"
    assert QueueState.SWEEPING.value == "sweeping"
    assert QueueState.PASS_FAIL.value == "pass_fail"
    assert QueueState.QUEUE_RUNNING.value == "queue_running"


def test_reset_clears_every_field():
    queue = MeasurementQueue(
        two_channel=True,
        state=QueueState.PASS_FAIL,
        target=4,
        index=2,
        attempts=3,
        stage=2,
        start_second_stage=True,
        pending_curve=CURVE,
        pending_pair=PAIR,
        pending_pair_first_raw=CURVE,
        pending_pair_first_diagnostics="diag",
        last_timing_quality=TIMING,
        last_diagnostics="diag",
        last_distortion="thd",
    )
    queue.reset()
    assert queue.target == 0
    assert queue.index == 0
    assert queue.attempts == 0
    assert queue.stage == 0
    assert queue.start_second_stage is False
    assert queue.pending_curve is None
    assert queue.pending_pair is None
    assert queue.pending_pair_first_raw is None
    assert queue.pending_pair_first_diagnostics is None
    assert queue.last_timing_quality is None
    assert queue.last_diagnostics is None
    assert queue.last_distortion is None
    # two_channel is a mode, not sweep state, and survives a reset.
    assert queue.two_channel is True


def test_reset_keep_counters_preserves_the_three_counters():
    queue = MeasurementQueue(
        target=4, index=2, attempts=2, stage=2,
        pending_curve=CURVE, pending_pair=PAIR, last_diagnostics="diag",
    )
    queue.reset(keep_counters=True)
    assert (queue.target, queue.index, queue.attempts) == (4, 2, 2)
    assert queue.stage == 0
    assert queue.pending_curve is None
    assert queue.pending_pair is None
    assert queue.last_diagnostics is None


def test_fail_resets_attempt_budget_and_repeats_index():
    """B5: a manual Fail must give the repeated index its full retry budget."""
    queue = queue_in(
        QueueState.PASS_FAIL, target=3, index=1,
        attempts=MAX_SWEEP_ATTEMPTS, pending_curve=CURVE,
    )
    decision = queue.on_fail()
    assert decision.state == QueueState.QUEUE_RUNNING
    assert queue.attempts == 0
    assert queue.index == 1  # same index is repeated

    # The very next sweep fails on timing: a retry must still be offered.
    queue.begin_sweep()
    decision = queue.on_error(message="timing drift too large", timing_failure=True)
    assert decision.effects == (Effect.PROMPT_RETRY,)
    assert decision.retry_attempt == 2


def test_cancel_clears_two_channel_pending_state():
    """B6: cancel must not leave pair state behind for Clear All to find."""
    queue = queue_in(
        QueueState.SWEEPING, target=3, index=1, attempts=1, two_channel=True,
        stage=2, pending_pair=PAIR, pending_pair_first_raw=CURVE,
        pending_pair_first_diagnostics="diag1", start_second_stage=True,
        pending_curve=CURVE,
    )
    queue.cancel()
    assert queue.pending_pair is None
    assert queue.pending_pair_first_raw is None
    assert queue.pending_pair_first_diagnostics is None
    assert queue.pending_curve is None
    assert queue.stage == 0
    assert queue.start_second_stage is False
    assert queue.is_active() is False


def test_terminal_error_resets_counters():
    """B4: a non-retryable error must not leave a phantom queue behind."""
    queue = running(target=3, index=1, attempts=1)
    decision = queue.on_error(
        message="Sweep error: something broke",
        timing_failure=False,
        device_failure=False,
    )
    assert decision.effects == (Effect.SHOW_ERROR, Effect.RESUME_MONITOR)
    assert decision.message == "Sweep error: something broke"
    assert (queue.target, queue.index, queue.attempts) == (0, 0, 0)
    assert queue.is_active() is False
    assert queue.allows_device_reselect() is True


def test_device_error_is_terminal_in_two_channel_mode():
    """B8: an unplugged interface must not offer three pair retries."""
    queue = running(target=3, index=0, attempts=1, two_channel=True)
    decision = queue.on_error(
        message="Input device unavailable: Scarlett 2i2",
        timing_failure=False,
        device_failure=True,
    )
    assert decision.effects == (Effect.SHOW_ERROR, Effect.RESUME_MONITOR)
    assert decision.state == QueueState.IDLE
    assert queue.is_active() is False


def test_timing_error_still_prompts_pair_retry():
    queue = running(target=3, index=0, attempts=1, two_channel=True)
    decision = queue.on_error(
        message="Start-alignment confidence too low",
        timing_failure=True,
        device_failure=False,
    )
    assert decision.effects == (Effect.PROMPT_RETRY,)
    assert decision.retry_is_pair is True
    assert decision.retry_attempt == 2
    assert queue.state == QueueState.QUEUE_RUNNING


def test_non_device_non_timing_error_retries_the_pair_in_two_channel_mode():
    """Two-channel mode still retries a pair for a plain processing failure."""
    queue = running(target=3, index=0, attempts=1, two_channel=True)
    decision = queue.on_error(message="Processing error: boom")
    assert decision.effects == (Effect.PROMPT_RETRY,)
    assert decision.retry_is_pair is True


def test_retry_attempt_numbering_matches_today_dialog_text():
    """``Retry attempt X of 3`` must number the *next* attempt, as today."""
    queue = MeasurementQueue()
    queue.begin(target=3, two_channel=False)
    for expected_attempt in (2, 3):
        queue.begin_sweep()
        decision = queue.on_error(message="timing drift", timing_failure=True)
        assert decision.retry_attempt == expected_attempt
        # Same text ``_on_sweep_error`` builds today.
        assert (
            f"Retry attempt {decision.retry_attempt} of {MAX_SWEEP_ATTEMPTS}?"
            == f"Retry attempt {expected_attempt} of 3?"
        )
        queue.on_retry_accepted()
    # The third attempt runs, fails, and has no budget left.
    queue.begin_sweep()
    assert queue.attempts == MAX_SWEEP_ATTEMPTS
    decision = queue.on_error(message="timing drift", timing_failure=True)
    assert decision.effects == (Effect.SHOW_ERROR, Effect.RESUME_MONITOR)


def test_retry_declined_cancels_queue():
    queue = running(target=3, index=1, attempts=1)
    queue.on_error(message="timing drift", timing_failure=True)
    decision = queue.on_retry_declined()
    assert decision.state == QueueState.IDLE
    assert decision.effects == (Effect.RESUME_MONITOR, Effect.REDRAW)
    assert queue.is_active() is False


def test_attempt_budget_exhausted_is_terminal():
    queue = running(target=3, index=0, attempts=MAX_SWEEP_ATTEMPTS)
    decision = queue.on_error(message="timing drift", timing_failure=True)
    assert decision.effects == (Effect.SHOW_ERROR, Effect.RESUME_MONITOR)
    assert queue.state == QueueState.IDLE
    assert queue.is_active() is False


def test_allows_device_reselect_only_when_idle():
    idle = MeasurementQueue()
    assert idle.allows_device_reselect() is True
    # Idle but with a live queue (a pending retry decision) still blocks.
    stale = queue_in(QueueState.IDLE, target=3, index=1)
    assert stale.allows_device_reselect() is False
    for state in (QueueState.QUEUE_RUNNING, QueueState.SWEEPING, QueueState.PASS_FAIL):
        assert queue_in(state, target=3).allows_device_reselect() is False


def test_second_stage_does_not_increment_attempts():
    queue = MeasurementQueue(two_channel=True)
    queue.begin(target=2, two_channel=True)
    queue.begin_sweep()
    assert (queue.attempts, queue.stage) == (1, 1)
    queue.on_stage_result(curve=CURVE, diagnostics="d1", timing=TIMING)
    assert queue.start_second_stage is True
    queue.begin_sweep(second_stage=True)
    assert (queue.attempts, queue.stage) == (1, 2)


def test_keep_on_last_index_finishes_queue():
    queue = MeasurementQueue()
    queue.begin(target=2, two_channel=False)
    for expected in (1, 2):
        queue.begin_sweep()
        queue.on_stage_result(curve=CURVE, diagnostics="d", timing=TIMING)
        decision = queue.on_keep(kept_total=expected)
        assert decision.message == f"Kept: {expected}"
    assert decision.effects == (Effect.REDRAW, Effect.FINISH)
    assert queue.state == QueueState.IDLE
    assert queue.is_active() is False


def test_full_single_channel_run_reaches_finish():
    queue = MeasurementQueue()
    assert queue.begin(target=2, two_channel=False).effects == (
        Effect.STOP_BALANCE,
        Effect.START_SWEEP,
    )
    queue.begin_sweep()
    queue.on_stage_result(curve=CURVE, diagnostics="d", timing=TIMING)
    assert queue.on_keep(kept_total=1).effects == (Effect.REDRAW, Effect.START_SWEEP)
    queue.begin_sweep()
    queue.on_stage_result(curve=CURVE, diagnostics="d", timing=TIMING)
    assert queue.on_keep(kept_total=2).effects == (Effect.REDRAW, Effect.FINISH)
    assert queue.finish().effects == (Effect.RESUME_MONITOR,)


def test_stage_result_outside_sweeping_is_a_noop():
    queue = running(target=3)
    decision = queue.on_stage_result(curve=CURVE, diagnostics="d", timing=TIMING)
    assert decision.effects == ()
    assert queue.pending_curve is None
    assert queue.state == QueueState.QUEUE_RUNNING


def test_missing_first_channel_message():
    queue = queue_in(
        QueueState.SWEEPING, target=3, two_channel=True, stage=0,
    )
    decision = queue.on_stage_result(curve=PAIR, diagnostics="d", timing=TIMING)
    assert decision.effects == (Effect.SHOW_ERROR,)
    assert decision.message == FIRST_CHANNEL_UNAVAILABLE


def test_clear_all_is_a_noop_while_busy():
    queue = queue_in(QueueState.PASS_FAIL, target=3, index=1, pending_curve=CURVE)
    decision = queue.clear_all()
    assert decision.effects == ()
    assert queue.pending_curve is CURVE
    assert queue.state == QueueState.PASS_FAIL


# ---------------------------------------------------------------------------
# is_device_failure — every string the sweep path can actually emit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "PortAudio error: Invalid device",
        "PortAudio error starting stream: -9996",
        "Input device unavailable: Scarlett 2i2",
        "Output device unavailable: Built-in Output",
        "Input channel 1 not available (device has 1 ch).",
        "Output channel 1 not available (device has 1 ch).",
        "Selected device is unavailable.",
        "Output stream failed to open",
        "portaudio error: lowercase spelling",
    ],
)
def test_is_device_failure_true(message):
    assert is_device_failure(message=message, failure_reason=None) is True


@pytest.mark.parametrize(
    "message",
    [
        "Start-alignment confidence too low",
        "End-marker confidence too low",
        "Timing drift too large",
        "Processing error: shapes do not match",
        "Sweep error: unexpected",
        "",
    ],
)
def test_is_device_failure_false(message):
    assert is_device_failure(message=message, failure_reason=None) is False


def test_is_device_failure_false_when_failure_reason_present():
    # A measurement-quality failure is never a device failure, whatever the
    # message text happens to contain.
    assert is_device_failure(
        message="PortAudio error: Invalid device",
        failure_reason=MeasurementFailureReason.LOW_START_CONFIDENCE,
    ) is False


def test_device_and_timing_classifiers_do_not_overlap():
    device_messages = [
        "PortAudio error: Invalid device",
        "Input device unavailable: Scarlett 2i2",
        "Selected device is unavailable.",
    ]
    for message in device_messages:
        assert is_device_failure(message=message, failure_reason=None) is True
        assert is_retryable_timing_failure(message=message, failure_reason=None) is False
