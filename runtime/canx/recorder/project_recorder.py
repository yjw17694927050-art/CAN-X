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

Two invariants are load-bearing and are why the code looks the way it does.

**Ordering.** ``DataSessionWriter`` is synchronous and not re-entrant. Every
call into it is therefore dispatched to a worker thread through
``asyncio.to_thread`` and serialised by one instance lock. ``asyncio`` cannot
cancel a running thread, so an ``append`` whose awaiting coroutine was cancelled
still finishes its disk write while holding that lock; the later ``finalize``
simply waits for it. That is what keeps a cancelled capture from interleaving a
final flush with an in-flight segment write or reordering segments.

**Failure is explicit.** A recorder that observed loss or a storage fault marks
the session ``FAILED`` and never finalizes it as ``COMPLETED``. Already
committed segments are preserved exactly as they were, and frames still in the
writer buffer are dropped rather than counted.
"""

from __future__ import annotations

import asyncio
import threading
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
        #: Serialises every call into the non-re-entrant data-session writer.
        self._lock = threading.Lock()

    @property
    def data_session_id(self) -> str | None:
        """Return the data session this recorder is writing, if any."""
        writer = self._writer
        return None if writer is None else writer.session_id

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
        self._last_sequence = None
        self.recorded_frames = 0
        self.failure = None
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
        """Finalize a healthy session; mark a faulty one ``FAILED``.

        Finalizing is only attempted when nothing failed during the session. Any
        recorded failure — backpressure, a write fault, a sequence gap, a
        cleanup deadline — turns into ``FAILED`` with the committed segments
        left untouched, because presenting a lossy recording as ``COMPLETED``
        would misrepresent the data as evidence.
        """
        writer = self._writer
        if writer is None:
            return
        try:
            if self.failure is None:
                try:
                    await asyncio.to_thread(self._finalize_serially, writer)
                except DataError as error:
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
            if self.failure is not None:
                await asyncio.to_thread(self._fail_serially, writer)
        finally:
            self._writer = None
            self._last_sequence = None
            self.state = (
                RecorderState.FAILED if self.failure is not None else RecorderState.STOPPED
            )

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

    def _finalize_serially(self, writer: DataSessionWriter) -> None:
        """Run the writer ``finalize`` after any in-flight append has finished."""
        with self._lock:
            writer.finalize()

    def _fail_serially(self, writer: DataSessionWriter) -> None:
        """Run the writer ``fail`` after any in-flight append has finished."""
        with self._lock:
            if writer.state is DataSessionState.ACTIVE:
                writer.fail()
