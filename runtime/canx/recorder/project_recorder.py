"""Project-backed recorder: the runtime boundary onto ``DataSessionWriter``.

This module is the single seam where the realtime capture path meets the V0.2
persistence domain. It deliberately owns no storage logic of its own:

* the segment format, the bounded flush, the atomic commit, the SQLite
  registration, the sequence/timestamp validation and the ``ACTIVE`` /
  ``COMPLETED`` / ``FAILED`` lifecycle all stay in
  :class:`~canx.data.session.DataSessionWriter`;
* this module only translates ``FrameBatch`` into writer calls, moves the
  blocking work off the realtime event loop, and turns a recorder-level failure
  into an explicit, non-``COMPLETED`` data session.

Three invariants are load-bearing and are why the code looks the way it does.

**Ordering.** ``DataSessionWriter`` is synchronous and not re-entrant. Every
call into it is therefore dispatched to a worker thread through
``asyncio.to_thread`` and serialised by one instance lock. ``asyncio`` cannot
cancel a running thread, so an ``append`` whose awaiting coroutine was cancelled
still finishes its disk write while holding that lock; the later finalization
simply waits for it. That is what keeps a cancelled capture from interleaving a
final flush with an in-flight segment write or reordering segments.

**Failure is explicit.** A recorder that observed loss or a storage fault marks
the session ``FAILED`` and never finalizes it as ``COMPLETED``. Already
committed segments are preserved exactly as they were, and frames still in the
writer buffer are dropped rather than counted.

**Completion is gated.** Finalization is split into a long flush — which only
ever adds committed segments and leaves the session ``ACTIVE`` — and one short
terminal decision that is the only place ``COMPLETED`` is ever written. That
decision is taken by a single worker against a gate: the recorder's failure flag
plus the deadline its owner armed before waiting. Because ``asyncio`` cannot
cancel a thread, an owner whose cleanup deadline expires abandons the worker
rather than stopping it — and the abandoned worker still refuses to complete the
session when it resumes. The gate check and the decisive write are held under a
separate terminal lock, so the owner's deadline declaration cannot interleave
between them.

**No verdict without a durable fact.** The owner can only decide the outcome
while it holds that terminal lock: there it closes the gate, and a worker that
has not yet taken the decision is then guaranteed to terminate ``FAILED``. Once
the worker holds the lock instead, its decisive write is already in flight and
nothing in-process can revoke it — ``asyncio`` cannot cancel the thread and
SQLite cannot abort a transaction owned by another connection. The owner
therefore reports :attr:`FinalizationOutcome.PENDING` rather than a failure it
cannot back, and waits for the worker's real result. This is why a cleanup
deadline expiration is *not* by itself a recorder failure: it is a failure only
once the gate has actually been closed, or the worker has actually failed. A
bounded stop is still bounded — it returns control without a verdict — but the
runtime's observable terminal state can never contradict the database's.
"""

from __future__ import annotations

import asyncio
import threading
import time
from enum import StrEnum
from math import isfinite
from pathlib import Path

from canx.data.errors import DataError
from canx.data.model import DataSessionState
from canx.data.session import (
    DEFAULT_MAX_FRAMES_PER_SEGMENT,
    DataSessionService,
    DataSessionWriter,
)
from canx.domain.batch import FrameBatch
from canx.recorder.msgpack_recorder import RecorderFailure, RecorderState

#: How long the owner waits for an in-flight terminal decision before it stops
#: believing the recorder can still finish. The terminal section is a clock
#: comparison plus one small SQLite statement, so this is generous by orders of
#: magnitude; it is bounded so that shutdown stays bounded.
TERMINAL_GRACE_SECONDS = 0.25


class FinalizationOutcome(StrEnum):
    """What is actually known about a recorder's terminal state.

    Reported instead of a boolean so a caller can tell a settled verdict from an
    unknown one. ``PENDING`` is not a deferral of a decision: it means the
    decisive write has already begun and no in-process owner can revoke it, so
    assuming either verdict would be inventing one.
    """

    COMPLETED = "completed"
    FAILED = "failed"
    PENDING = "pending"


class RecorderContinuityError(RuntimeError):
    """Raised when a batch would leave an unexplained gap in the recording.

    The archive subscriber is lossless, so a gap in the sequence means a frame
    that capture produced never reached the recorder. Treating that as ordinary
    data would let an incomplete recording be presented as a complete one.
    """

    def __init__(self, message: str, *, code: str = "recorder.sequence_gap") -> None:
        super().__init__(message)
        self.code = code


class ProjectRecorder:
    """Persist captured ``FrameBatch``es into one project's data session.

    The observable surface mirrors :class:`~canx.recorder.msgpack_recorder.MsgpackRecorder`
    on purpose: the runtime owns both backends behind the same lifecycle and the
    same failure contract. ``start`` opens one ``ACTIVE`` data session for the
    capture stream, ``append`` commits frames through ``DataSessionWriter``, and
    ``stop`` either finalizes the session to ``COMPLETED`` or, when any failure
    was recorded, marks it ``FAILED``.
    """

    def __init__(self, *, max_frames_per_segment: int = DEFAULT_MAX_FRAMES_PER_SEGMENT) -> None:
        self.state = RecorderState.IDLE
        self.recorded_frames = 0
        self.failure: RecorderFailure | None = None
        self._max_frames_per_segment = max_frames_per_segment
        self._writer: DataSessionWriter | None = None
        self._last_sequence: int | None = None
        #: Retained so the gate can settle the session after the worker is gone.
        self._project_root: Path | None = None
        self._session_id: str | None = None
        #: Serialises every call into the non-re-entrant data-session writer.
        self._lock = threading.Lock()
        #: Guards the terminal decision; held across the gate check and the write.
        self._terminal_lock = threading.Lock()
        #: Absolute monotonic deadline armed by the owner before it awaits stop().
        self._deadline: float | None = None
        self._completed = False
        self._finalization_in_flight = False
        self._finalization_started = False
        self._finalization_settled = threading.Event()
        self._finalization_settled.set()

    @property
    def data_session_id(self) -> str | None:
        """Return the data session this recorder is writing, if any."""
        writer = self._writer
        return None if writer is None else writer.session_id

    @property
    def completed(self) -> bool:
        """Return whether this recorder durably completed its data session."""
        return self._completed

    @property
    def finalization_pending(self) -> bool:
        """Return whether a finalization worker is still running.

        After a cleanup deadline expires this stays true until the abandoned
        worker really exits, which is what lets an owner refuse to start work
        that would race it.
        """
        return self._finalization_in_flight and not self._finalization_settled.is_set()

    @property
    def finalization_outcome(self) -> FinalizationOutcome:
        """Return the terminal result of finalization, or ``PENDING`` if unknown.

        Only meaningful once :meth:`stop` has been attempted; before that — and
        while the worker still runs — the answer is ``PENDING`` rather than a
        default verdict, so a caller can never mistake "not known yet" for
        "failed".
        """
        if self._finalization_in_flight or not self._finalization_started:
            return FinalizationOutcome.PENDING
        if self._completed:
            return FinalizationOutcome.COMPLETED
        return FinalizationOutcome.FAILED

    def wait_for_finalization(self, timeout: float | None = None) -> bool:
        """Block until the finalization worker has settled; return whether it did.

        ``False`` means the worker is still running, which after a missed
        deadline means the abandoned thread has not exited yet.
        """
        return self._finalization_settled.wait(timeout)

    def arm_stop_deadline(self, seconds: float) -> None:
        """Tell the recorder how long its owner will wait for :meth:`stop`.

        The recorder — not the owner — decides whether a terminal transition
        still fits inside that budget, so a deadline that expires while the
        finalization worker is blocked in disk IO keeps the decisive write from
        ever being *started*, even though the worker later resumes.

        Raises:
            ValueError: If ``seconds`` is not finite and positive.
        """
        if not isfinite(seconds) or seconds <= 0:
            raise ValueError("stop deadline must be finite and positive")
        self._deadline = time.monotonic() + seconds

    def close_finalization_gate(
        self,
        *,
        code: str,
        message: str,
        recoverable: bool,
        context: dict[str, object],
        grace_seconds: float = TERMINAL_GRACE_SECONDS,
    ) -> FinalizationOutcome:
        """Declare the owner's deadline expired and report what is actually known.

        The only moment the owner can decide the outcome is while it holds the
        terminal lock: there it can close the deadline face of the gate, and any
        worker that has not yet taken the decision is then guaranteed to refuse
        ``COMPLETED``. Three mechanisms cooperate, in this order: the terminal
        lock orders this call against an in-flight decision; the deadline face of
        the gate makes the worker refuse to complete; and a conditional
        ``ACTIVE`` → ``FAILED`` update lets the database referee even a worker
        blocked inside its own completion, because that completion then finds the
        row already decided.

        What no in-process owner can do is revoke a decision already taken. Once
        the worker holds the terminal lock, it has passed its gate check and its
        decisive write is in flight; ``asyncio`` cannot cancel the thread and
        SQLite cannot abort a transaction another thread owns. Closing the gate
        then would be a lie — the worker may be about to commit — so this call
        reports :attr:`FinalizationOutcome.PENDING` instead and leaves the gate
        open for the worker to settle truthfully. The owner must then wait for
        that worker rather than publish a verdict.

        Returns:
            :attr:`FinalizationOutcome.COMPLETED` when the session is durably
            ``COMPLETED``; :attr:`FinalizationOutcome.FAILED` when the gate is
            closed and a durable ``FAILED`` has won; and
            :attr:`FinalizationOutcome.PENDING` when the decisive write has
            already begun and cannot be revoked.
        """
        if not self._terminal_lock.acquire(timeout=grace_seconds):
            # The worker is inside the protected section: its gate check has
            # already run, so its outcome is in flight and irrevocable. Refusing
            # to guess is the only truthful answer.
            return FinalizationOutcome.PENDING
        try:
            if self._completed:
                return FinalizationOutcome.COMPLETED
            # Close the gate while the terminal lock is held, so the worker
            # cannot slip a completion in between deciding and being refused.
            self._close_gate()
        finally:
            self._terminal_lock.release()

        state = self._settle_durable_state(grace_seconds)
        if state is DataSessionState.COMPLETED:
            return FinalizationOutcome.COMPLETED
        self.fail(code=code, message=message, recoverable=recoverable, context=dict(context))
        return FinalizationOutcome.FAILED

    def fail(
        self, *, code: str, message: str, recoverable: bool, context: dict[str, object]
    ) -> None:
        """Retain the first diagnostic, even when cleanup also fails."""
        if self.failure is None:
            self.failure = RecorderFailure(code, message, recoverable, dict(context))
        self.state = RecorderState.FAILED

    def reset_session(self) -> None:
        """Begin a non-recording capture session without stale recorder diagnostics."""
        if self.state is RecorderState.RECORDING:
            raise RuntimeError("cannot reset an active recorder")
        self.state = RecorderState.IDLE
        self.recorded_frames = 0
        self.failure = None
        self._writer = None
        self._last_sequence = None
        self._deadline = None
        self._completed = False
        self._finalization_in_flight = False
        self._finalization_started = False
        self._finalization_settled.set()

    async def start(self, project_root: Path, *, stream_id: str) -> None:
        """Open one ``ACTIVE`` data session for ``stream_id`` in the project.

        The session is created through :class:`DataSessionService`, so the
        project is validated and the session directory/row land atomically
        exactly as they do for any other data session.

        Raises:
            RuntimeError: If the recorder is already recording.
            DataValidationError: If ``max_frames_per_segment`` is unusable.
            DataStorageError: If the project is not a usable CAN-X project.
        """
        if self.state is RecorderState.RECORDING:
            raise RuntimeError("recorder is already running")
        service = DataSessionService(
            project_root, max_frames_per_segment=self._max_frames_per_segment
        )
        writer = await asyncio.to_thread(service.start, stream_id=stream_id)
        self._writer = writer
        self._project_root = project_root
        self._session_id = writer.session_id
        self._last_sequence = None
        self.recorded_frames = 0
        self.failure = None
        self._deadline = None
        self._completed = False
        self._finalization_in_flight = False
        self._finalization_started = False
        self._finalization_settled.set()
        self.state = RecorderState.RECORDING

    async def append(self, batch: FrameBatch) -> None:
        """Commit one contiguous batch, flushing full segments as they fill.

        The blocking Parquet/SQLite work runs on a worker thread; only the
        sequence continuity check and the committed-frame counter touch the
        event loop.

        Raises:
            RuntimeError: If the recorder is not recording.
            RecorderContinuityError: If the batch does not continue the recorded
                sequence, which would mean capture frames were lost.
            DataError: If the data session rejected the batch or could not
                commit it; the session is then already ``FAILED``.
        """
        writer = self._writer
        if writer is None or self.state is not RecorderState.RECORDING:
            raise RuntimeError("recorder is not running")
        self._require_contiguous(batch)
        await asyncio.to_thread(self._append_serially, writer, batch)
        self._last_sequence = batch.last_sequence
        self.recorded_frames += batch.frame_count

    async def stop(self) -> None:
        """Flush and take the gated terminal decision for the session.

        A healthy session finalizes to ``COMPLETED``; a recorded failure — or a
        deadline that expired before the decisive write was started — turns it
        into ``FAILED`` with the committed segments left untouched, because
        presenting a lossy recording as ``COMPLETED`` would misrepresent the data
        as evidence. A deadline that expires *while* the decisive write is in
        flight is not a verdict at all: see
        :meth:`close_finalization_gate` and
        :attr:`finalization_outcome`.

        When the owner's deadline cancels this coroutine, the worker keeps
        running (``asyncio`` cannot stop a thread) and :attr:`finalization_pending`
        stays true until it exits. The worker still consults the gate, so it
        cannot complete a session the owner has already declared failed.
        """
        writer = self._writer
        if writer is None:
            return
        self._finalization_started = True
        self._finalization_in_flight = True
        self._finalization_settled.clear()
        try:
            await asyncio.to_thread(self._finish_serially, writer)
        finally:
            # The worker owns its local writer reference; clearing ours only
            # forbids *new* operations on a session that is being terminated.
            self._writer = None
            self._last_sequence = None

    def _require_contiguous(self, batch: FrameBatch) -> None:
        """Refuse a batch that would leave a hole in this recording.

        Raises:
            RecorderContinuityError: If ``batch`` does not directly continue the
                last recorded sequence.
        """
        if self._last_sequence is None or batch.first_sequence == self._last_sequence + 1:
            return
        message = (
            "The batch does not continue this recording's sequence; frames were lost"
            " before reaching the recorder."
        )
        self.fail(
            code="recorder.sequence_gap",
            message=message,
            recoverable=False,
            context={
                "stream_id": batch.stream_id,
                "previous_last_sequence": self._last_sequence,
                "batch_first_sequence": batch.first_sequence,
                "data_session_id": self.data_session_id,
            },
        )
        raise RecorderContinuityError(message)

    def _append_serially(self, writer: DataSessionWriter, batch: FrameBatch) -> None:
        """Run one writer ``append`` while holding the writer lock."""
        with self._lock:
            writer.append(batch)

    def _finish_serially(self, writer: DataSessionWriter) -> None:
        """Flush the tail, then take the single terminal decision — one worker.

        The flush is the only step that can take long, and it only adds committed
        segments. The decision after it is what makes the session ``COMPLETED``,
        and it is taken against the gate, so an abandoned worker that resumes
        after a missed deadline still terminates the session ``FAILED``.
        """
        try:
            if self.failure is None:
                with self._lock:
                    writer.flush_pending()
        except DataError as error:
            self._diagnose_finalize_failure(writer, error)
        finally:
            self._decide_terminally(writer)

    def _decide_terminally(self, writer: DataSessionWriter) -> None:
        """Take the one terminal transition, gated and serialised.

        The terminal lock is held across the gate check and the decisive write so
        the owner cannot interleave a deadline declaration between them.
        """
        try:
            with self._terminal_lock:
                if self._gate_open():
                    try:
                        with self._lock:
                            writer.commit_completed()
                    except DataError as error:
                        self._diagnose_finalize_failure(writer, error)
                    else:
                        self._completed = True
                if self._completed:
                    self.state = RecorderState.STOPPED
                else:
                    if self.failure is None:
                        self.fail(
                            code="recorder.stop_deadline_exceeded",
                            message="The session was not completed inside the allowed stop budget.",
                            recoverable=False,
                            context={
                                "stream_id": writer.stream_id,
                                "data_session_id": writer.session_id,
                            },
                        )
                    self.state = RecorderState.FAILED
                    with self._lock:
                        writer.fail()
        finally:
            self._finalization_in_flight = False
            self._finalization_settled.set()

    def _gate_open(self) -> bool:
        """Return whether the session may still be completed right now.

        The remaining-budget test is not a formality: the decisive write must fit
        inside the owner's deadline, so a commit is never *started* in the last
        moment of the budget and then discovered to have landed after it.
        """
        if self.failure is not None:
            return False
        deadline = self._deadline
        if deadline is None:
            return True
        return time.monotonic() + TERMINAL_GRACE_SECONDS <= deadline

    def _close_gate(self) -> None:
        """Close the deadline face of the gate for good."""
        self._deadline = 0.0

    def _settle_durable_state(self, timeout: float) -> DataSessionState | None:
        """Make the terminal decision durable and report the row's state.

        The conditional update is the arbiter: it either claims the still-``ACTIVE``
        row for ``FAILED`` — after which the worker's own completion is refused by
        the same condition — or it finds the session already decided. It runs on a
        short-lived worker because the finalization worker may still be blocked in
        disk IO, and it is joined with a bound so shutdown stays bounded.
        """
        root = self._project_root
        session_id = self._session_id
        if root is None or session_id is None:
            return None
        outcome: list[DataSessionState | None] = [None]

        def settle() -> None:
            try:
                service = DataSessionService(root)
                service.fail_active_session(session_id)
                outcome[0] = service.get_session(session_id).state
            except DataError:
                outcome[0] = None

        worker = threading.Thread(target=settle, name="canx-recorder-gate", daemon=True)
        worker.start()
        worker.join(timeout)
        return outcome[0]

    def _diagnose_finalize_failure(self, writer: DataSessionWriter, error: DataError) -> None:
        """Record a flush/commit fault so the terminal decision refuses COMPLETED."""
        self.fail(
            code="recorder.finalize_failed",
            message=str(error) or "the data session could not be finalized",
            recoverable=False,
            context={
                "stream_id": writer.stream_id,
                "data_session_id": writer.session_id,
                "cause": error.code,
            },
        )
