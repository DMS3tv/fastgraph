"""
Pure measurement-queue state machine.

This module owns the queue/sweep/review state that today lives inline in
``dms.ui.main_window.MainWindow``. It is deliberately free of Qt, numpy,
sounddevice and every other side effect: each method mutates the dataclass and
returns a :class:`QueueDecision` describing the new state plus the side effects
the window should perform. That makes the whole transition table testable in
milliseconds without a GUI or audio hardware.

Design rules:

- ``QueueState`` values are byte-identical to the ``AppState`` strings used by
  the window (``dms/ui/main_window.py``), so a window field can be swapped for
  this state with no comparison changes.
- :meth:`MeasurementQueue.reset` is the single place that clears sweep-scoped
  state (stage, pending curves, last diagnostics) and the counters. Every other
  method delegates to it, so no future transition can leave half of the state
  behind. ``keep_counters=True`` preserves ``target``, ``index`` and
  ``attempts`` — the three counters — and clears everything else.
- The queue never decides *how* to do anything; it only says *what* must
  happen, via :class:`Effect`.

Bugs closed by construction relative to the inline implementation (see
``FASTGRAPH_REVIEW_2026-09-08.md`` section B):

- **B4** — a terminal sweep error runs a full ``reset()``, so ``target`` and
  ``index`` cannot survive as a phantom queue.
- **B5** — :meth:`on_fail` sets ``attempts`` back to 0, so a manual Fail
  restores the full retry budget for the repeated index.
- **B6** — :meth:`cancel` runs a full ``reset()``, clearing the two-channel
  pending state (``pending_pair``, ``pending_pair_first_raw``,
  ``pending_pair_first_diagnostics``, ``stage``) that the inline version left
  behind.
- **B7** — every transition that starts a fresh sweep emits
  :attr:`Effect.STOP_BALANCE`, so a queue started from a shortcut, the console
  or an automation stops the Channel Balance tone exactly like the button does.
- **B8** — a device failure is terminal even in two-channel mode; only a timing
  failure (or a non-device failure in two-channel mode) offers a pair retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class QueueState(str, Enum):
    """Queue state names.

    The values must stay equal to ``dms.ui.main_window.AppState`` strings.
    """

    IDLE = "idle"
    SWEEPING = "sweeping"
    PASS_FAIL = "pass_fail"
    QUEUE_RUNNING = "queue_running"


#: Total sweep attempts allowed for one queue index, including the first one.
#: Mirrors ``_MAX_SWEEP_ATTEMPTS`` in ``dms/ui/main_window.py``.
MAX_SWEEP_ATTEMPTS = 3


class Effect(str, Enum):
    """Side effects the owning window must perform for a decision."""

    #: Start (or schedule) the next sweep for the current index and stage.
    START_SWEEP = "start_sweep"
    #: Start the second channel of a two-channel pair.
    START_SECOND_STAGE = "start_second_stage"
    #: Show the pass/fail review dialog for the pending measurement.
    SHOW_REVIEW = "show_review"
    #: Ask the operator whether to retry; see ``retry_attempt``/``retry_is_pair``.
    PROMPT_RETRY = "prompt_retry"
    #: Show a terminal error dialog using ``QueueDecision.message``.
    SHOW_ERROR = "show_error"
    #: Run the queue-complete path (status message, automation trigger).
    FINISH = "finish"
    #: Restart the input level monitor.
    RESUME_MONITOR = "resume_monitor"
    #: Repaint the plots and queue progress.
    REDRAW = "redraw"
    #: Stop the Channel Balance tone before touching the audio device.
    STOP_BALANCE = "stop_balance"


@dataclass(frozen=True)
class QueueDecision:
    """The result of one queue event: new state, effects, and dialog data."""

    state: QueueState
    effects: tuple[Effect, ...] = ()
    message: str = ""
    #: For :attr:`Effect.PROMPT_RETRY`: the 1-based number of the attempt the
    #: operator is being offered, i.e. the "X" in "Retry attempt X of 3".
    retry_attempt: Optional[int] = None
    #: For :attr:`Effect.PROMPT_RETRY`: whether the retry re-measures a whole
    #: two-channel pair rather than a single sweep.
    retry_is_pair: bool = False


#: Terminal message used when a two-channel second stage completes without a
#: stored first channel. Matches the inline ``ValueError`` text.
FIRST_CHANNEL_UNAVAILABLE = "The first channel result is unavailable."


@dataclass
class MeasurementQueue:
    """Queue/sweep/review state machine for the Measure tab.

    All methods are pure in the sense that they touch nothing but ``self`` and
    return a :class:`QueueDecision`. Events that are not legal in the current
    state are no-ops: they mutate nothing and return the unchanged state with
    no effects.
    """

    two_channel: bool = False
    max_attempts: int = MAX_SWEEP_ATTEMPTS

    state: QueueState = QueueState.IDLE
    #: Number of measurements the queue must keep. 0 means "no queue".
    target: int = 0
    #: Number of measurements kept so far in this queue.
    index: int = 0
    #: Sweep attempts spent on the current index, including the running one.
    attempts: int = 0

    #: Two-channel stage: 0 = not in a pair, 1 = first channel, 2 = second.
    stage: int = 0
    #: Set when the first channel finished and the second must follow.
    start_second_stage: bool = False

    pending_curve: Any = None
    pending_pair: Any = None
    pending_pair_first_raw: Any = None
    pending_pair_first_diagnostics: Any = None

    last_timing_quality: Optional[tuple[float, float, float, float]] = None
    last_diagnostics: Any = None
    last_distortion: Any = None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def is_active(self) -> bool:
        """Whether a queue is running (a target was set and not cleared)."""
        return self.target > 0

    def allows_device_reselect(self) -> bool:
        """Whether it is safe to re-enumerate and re-select audio devices.

        False for every non-idle state, including ``QUEUE_RUNNING`` and
        ``PASS_FAIL`` — closing B3's window where a hotplug between the two
        halves of a pair could move channel 2 onto a different device.
        """
        return self.state == QueueState.IDLE and not self.is_active()

    # ------------------------------------------------------------------
    # The one place state is cleared
    # ------------------------------------------------------------------

    def reset(self, *, keep_counters: bool = False) -> None:
        """Clear sweep-scoped state, and the counters unless asked not to.

        ``keep_counters=True`` preserves the three counters ``target``,
        ``index`` and ``attempts``; the retry path depends on ``attempts``
        surviving so the budget advances, and :meth:`on_keep` / :meth:`on_fail`
        zero ``attempts`` explicitly when the budget should restart.

        ``state`` is deliberately not touched here — each transition assigns it
        explicitly, so the state assignment always reads in one place.
        """
        self.stage = 0
        self.start_second_stage = False
        self.pending_curve = None
        self.pending_pair = None
        self.pending_pair_first_raw = None
        self.pending_pair_first_diagnostics = None
        self.last_timing_quality = None
        self.last_diagnostics = None
        self.last_distortion = None
        if not keep_counters:
            self.target = 0
            self.index = 0
            self.attempts = 0

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def begin(self, *, target: int, two_channel: bool) -> QueueDecision:
        """Start a queue of ``target`` measurements. Ignored unless idle."""
        if self.state != QueueState.IDLE:
            return QueueDecision(state=self.state)
        self.reset()
        self.two_channel = bool(two_channel)
        self.target = int(target)
        self.index = 0
        self.attempts = 0
        self.state = QueueState.QUEUE_RUNNING
        return QueueDecision(
            state=self.state,
            effects=(Effect.STOP_BALANCE, Effect.START_SWEEP),
        )

    def begin_sweep(self, *, second_stage: bool = False) -> QueueDecision:
        """Prepare the next sweep, or finish the queue when it is complete.

        ``second_stage=True`` starts the second channel of a two-channel pair:
        it moves to stage 2 and leaves ``attempts`` alone, so a pair costs one
        attempt, not two.
        """
        if not self.is_active():
            self.reset()
            self.state = QueueState.IDLE
            return QueueDecision(state=self.state)

        if self.index >= self.target:
            self.reset()
            self.state = QueueState.IDLE
            return QueueDecision(state=self.state, effects=(Effect.FINISH,))

        if second_stage:
            self.stage = 2
            effects = (Effect.START_SWEEP,)
        else:
            self.attempts += 1
            if self.two_channel:
                self.stage = 1
                self.pending_pair = None
                self.pending_pair_first_raw = None
                self.pending_pair_first_diagnostics = None
            effects = (Effect.STOP_BALANCE, Effect.START_SWEEP)

        # The inline implementation clears these unconditionally before every
        # sweep, including the second stage of a pair; the first channel's
        # diagnostics are already parked in ``pending_pair_first_diagnostics``.
        self.last_timing_quality = None
        self.last_diagnostics = None
        self.last_distortion = None

        self.state = QueueState.SWEEPING
        return QueueDecision(state=self.state, effects=effects)

    def on_stage_result(
        self,
        *,
        curve: Any,
        diagnostics: Any = None,
        timing: Optional[tuple[float, float, float, float]] = None,
    ) -> QueueDecision:
        """Accept one processed sweep result.

        In two-channel mode ``curve`` is the raw first-channel result during
        stage 1 and the fully built pair during stage 2 — the caller owns the
        DSP that turns two raw curves into a pair.
        """
        if self.state != QueueState.SWEEPING:
            return QueueDecision(state=self.state)

        self.last_timing_quality = timing
        self.last_diagnostics = diagnostics

        if not self.two_channel:
            self.pending_curve = curve
            self.state = QueueState.PASS_FAIL
            return QueueDecision(
                state=self.state,
                effects=(Effect.SHOW_REVIEW, Effect.REDRAW),
            )

        if self.stage == 1:
            self.pending_pair_first_raw = curve
            self.pending_pair_first_diagnostics = diagnostics
            self.start_second_stage = True
            self.state = QueueState.QUEUE_RUNNING
            return QueueDecision(
                state=self.state,
                effects=(Effect.START_SECOND_STAGE,),
            )

        if self.stage != 2 or self.pending_pair_first_raw is None:
            self.reset()
            self.state = QueueState.IDLE
            return QueueDecision(
                state=self.state,
                effects=(Effect.SHOW_ERROR,),
                message=FIRST_CHANNEL_UNAVAILABLE,
            )

        self.pending_pair = curve
        self.state = QueueState.PASS_FAIL
        return QueueDecision(
            state=self.state,
            effects=(Effect.SHOW_REVIEW, Effect.REDRAW),
        )

    def on_error(
        self,
        *,
        message: str,
        timing_failure: bool = False,
        device_failure: bool = False,
    ) -> QueueDecision:
        """Handle a sweep or processing failure.

        A failure is retryable when it is a timing-quality failure, or when a
        two-channel pair failed for a reason that is not the audio device
        itself. Device failures are always terminal (B8): an unplugged
        interface must not produce three "retry the pair?" prompts.

        ``retry_attempt`` is ``attempts + 1``: :meth:`begin_sweep` already
        counted the attempt that just failed, so the operator is being offered
        the *next* one. That reproduces today's dialog text exactly —
        ``f"Retry attempt {self._current_sweep_attempts + 1} of
        {_MAX_SWEEP_ATTEMPTS}?"`` in ``_on_sweep_error``.
        """
        retryable = bool(timing_failure) or (
            self.two_channel and not bool(device_failure)
        )
        if (
            self.is_active()
            and retryable
            and self.attempts < self.max_attempts
        ):
            retry_is_pair = self.two_channel
            self.reset(keep_counters=True)
            self.state = QueueState.QUEUE_RUNNING
            return QueueDecision(
                state=self.state,
                effects=(Effect.PROMPT_RETRY,),
                message=message,
                retry_attempt=self.attempts + 1,
                retry_is_pair=retry_is_pair,
            )

        self.reset()
        self.state = QueueState.IDLE
        return QueueDecision(
            state=self.state,
            effects=(Effect.SHOW_ERROR, Effect.RESUME_MONITOR),
            message=message,
        )

    def on_retry_accepted(self) -> QueueDecision:
        """Operator accepted the retry prompt: run the same index again."""
        if self.state != QueueState.QUEUE_RUNNING:
            return QueueDecision(state=self.state)
        return QueueDecision(state=self.state, effects=(Effect.START_SWEEP,))

    def on_retry_declined(self) -> QueueDecision:
        """Operator declined the retry prompt: the queue is cancelled."""
        if self.state != QueueState.QUEUE_RUNNING:
            return QueueDecision(state=self.state)
        self.reset()
        self.state = QueueState.IDLE
        return QueueDecision(
            state=self.state,
            effects=(Effect.RESUME_MONITOR, Effect.REDRAW),
        )

    def on_keep(self, *, kept_total: int = 0) -> QueueDecision:
        """Keep the pending measurement and advance the queue.

        ``kept_total`` is the caller's count of stored measurements after the
        keep; it only feeds the progress label text.
        """
        if self.state != QueueState.PASS_FAIL:
            return QueueDecision(state=self.state)
        pending = self.pending_pair if self.two_channel else self.pending_curve
        if pending is None:
            return QueueDecision(state=self.state)

        self.index += 1
        self.attempts = 0
        self.reset(keep_counters=True)

        message = f"Kept: {int(kept_total)}"
        if self.index >= self.target:
            self.reset()
            self.state = QueueState.IDLE
            return QueueDecision(
                state=self.state,
                effects=(Effect.REDRAW, Effect.FINISH),
                message=message,
            )
        self.state = QueueState.QUEUE_RUNNING
        return QueueDecision(
            state=self.state,
            effects=(Effect.REDRAW, Effect.START_SWEEP),
            message=message,
        )

    def on_fail(self) -> QueueDecision:
        """Reject the pending measurement and repeat the same index.

        The retry budget restarts (B5): a manual Fail is an operator judgement,
        not a spent automatic attempt, so a later timing failure on this index
        still gets its retry prompts.
        """
        if self.state != QueueState.PASS_FAIL:
            return QueueDecision(state=self.state)
        self.reset(keep_counters=True)
        self.attempts = 0
        self.state = QueueState.QUEUE_RUNNING
        return QueueDecision(
            state=self.state,
            effects=(Effect.REDRAW, Effect.START_SWEEP),
            message=f"Measurement {self.index + 1} failed. Redoing same index.",
        )

    def cancel(self) -> QueueDecision:
        """Cancel the queue from any state and clear everything."""
        self.reset()
        self.state = QueueState.IDLE
        return QueueDecision(
            state=self.state,
            effects=(Effect.RESUME_MONITOR, Effect.REDRAW),
            message="Queue canceled.",
        )

    def finish(self) -> QueueDecision:
        """Complete the queue normally."""
        self.reset()
        self.state = QueueState.IDLE
        return QueueDecision(
            state=self.state,
            effects=(Effect.RESUME_MONITOR,),
            message="Queue complete.",
        )

    def clear_all(self) -> QueueDecision:
        """Clear All: drop every counter and pending result. Idle only.

        The window keeps its own "cannot clear while the queue is active"
        dialog; here a non-idle call is simply a no-op.
        """
        if self.state != QueueState.IDLE:
            return QueueDecision(state=self.state)
        self.reset()
        self.state = QueueState.IDLE
        return QueueDecision(state=self.state, effects=(Effect.REDRAW,))
