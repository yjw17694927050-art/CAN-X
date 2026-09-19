"""Deterministic, offline replay of one persisted data session.

The data flow is deliberately linear and holds one page at a time::

    ProjectService.open(project_root)  the project domain validates the root
        └── DataSession (persisted)    resolved only inside a validated project
                └── ReplaySource.pages()          bounded QueryService pagination
                        └── ReplaySession         recorded timing on an injected clock
                                └── ReplaySink.emit(ReplayFrameEvent)

The project domain opens the root **first**: replay never trusts a directory that
is not a valid CAN-X project, and only a validated root is used to resolve the
session and read its frames. There is **no** device in that picture: offline
replay never calls an adapter, never opens a bus and never transmits — a frame
leaves the domain only through the sink the caller supplied. Real CAN replay,
which does need a transmit path and therefore a safety gate, is a later phase and
is not prepared for here.

Timing is the recording's own: frame ``n`` is scheduled at
``normalized_timestamp(n) - normalized_timestamp(first_frame)`` seconds after the
replay started, at exactly 1.0x. The scheduler reads time only through an
injected :class:`~canx.replay.clock.ReplayClock`, so the same recording, the same
configuration and the same clock behaviour always produce the same frame order,
the same relative schedule, the same event order and the same terminal state.

Lifecycle: ``IDLE`` → ``RUNNING`` → one of ``COMPLETED``, ``FAILED``,
``CANCELLED``. ``COMPLETED`` is reached only after the final frame of the
recording has been emitted *and* the number of emitted frames matches the
session's own durable frame count; a partial replay raises instead of completing.
"""

from __future__ import annotations

from collections.abc import Generator
from math import isfinite
from pathlib import Path

from canx.data.errors import (
    DataError,
    DataSessionError,
    DataStorageError,
    DataValidationError,
)
from canx.data.model import DataSession, DataSessionState
from canx.data.session import DataSessionService
from canx.domain.frame import Frame
from canx.project.errors import ProjectError
from canx.project.service import ProjectHandle, ProjectService
from canx.query.errors import QueryError
from canx.query.model import FrameQueryPage
from canx.query.service import QueryService
from canx.replay.cancellation import ReplayCancellation
from canx.replay.clock import MonotonicReplayClock, ReplayClock
from canx.replay.errors import (
    ReplayError,
    ReplayProjectError,
    ReplaySchedulerError,
    ReplaySessionError,
    ReplaySinkError,
    ReplaySourceError,
    ReplayStateError,
    ReplayValidationError,
)
from canx.replay.model import (
    EmptySessionPolicy,
    ReplayConfig,
    ReplayFrameEvent,
    ReplayReport,
    ReplayState,
    SessionCompletionPolicy,
)
from canx.replay.sink import ReplaySink
from canx.replay.source import ReplaySource, SessionReplaySource

#: How much of a missed deadline a real clock is allowed to report before the
#: scheduler treats it as a broken clock. A real sleep may return marginally
#: early; it cannot legitimately be milliseconds short of the deadline it was
#: given. A virtual clock is exact, so this tolerance never affects a
#: deterministic replay.
SCHEDULE_TOLERANCE_SECONDS = 0.005

#: Project-domain causes that mean the root path itself is unusable, rather than
#: a broken project at a usable path. They keep the replay domain's existing
#: "invalid project root" arm; every deeper project failure is reported as
#: ``replay.project_unavailable`` with the project's own cause code attached.
_INVALID_PROJECT_ROOT_CAUSES = frozenset({"project.not_found", "project.not_a_directory"})


class ReplaySession:
    """One offline replay of one persisted data session.

    Construction is where every argument is validated and where the session's
    own recorded state is checked against the configured policy; :meth:`run` is
    then a single, one-shot transition that either finishes or fails. A replay
    never leaves work behind: when :meth:`run` returns or raises, the state is
    terminal, the source is closed and a second :meth:`run` is refused.
    """

    def __init__(
        self,
        session: DataSession,
        source: ReplaySource,
        sink: ReplaySink,
        *,
        config: ReplayConfig | None = None,
        clock: ReplayClock | None = None,
        cancellation: ReplayCancellation | None = None,
    ) -> None:
        self._session = _require_data_session(session)
        self._source = _require_source(source)
        self._sink = _require_sink(sink)
        self._config = _require_config(config)
        self._clock = MonotonicReplayClock() if clock is None else _require_clock(clock)
        if cancellation is None:
            self._cancellation = ReplayCancellation()
        else:
            self._cancellation = _require_cancellation(cancellation)
        self._state = ReplayState.IDLE
        self._require_replayable()

    @classmethod
    def open(
        cls,
        project_root: Path,
        *,
        session_id: str,
        sink: ReplaySink,
        config: ReplayConfig | None = None,
        clock: ReplayClock | None = None,
        cancellation: ReplayCancellation | None = None,
    ) -> ReplaySession:
        """Resolve ``session_id`` inside a project and prepare its replay.

        This is the production entry point, and the project domain is its first
        authority: the root is opened through the project service before anything
        about the recording is read. A directory that is not a valid CAN-X
        project — no manifest, an unreadable manifest, a missing database, a
        mismatched identity or an unsupported version — is refused there rather
        than trusted as a place to look for frames. Only a validated project's
        own root is then used to resolve the session metadata, wire the bounded
        query-backed source and apply the configured policies. Nothing is
        replayed until :meth:`run` is called.

        Raises:
            ReplayValidationError: If the session id or the configuration is
                unusable.
            ReplayProjectError: If the project domain refuses the root. The
                project's own stable cause is carried in ``details["cause"]``.
            ReplaySessionError: If the session is not registered, is not
                replayable under the configured policy, or is empty and empty
                sessions are rejected.
        """
        resolved_config = _require_config(config)
        handle = _open_project(Path(project_root))
        try:
            validated_root = handle.root
        finally:
            # The project handle is released as soon as validation is done: the
            # replay reads the recording through the query domain's own
            # short-lived connections, never through this handle.
            _close_project(handle)
        session = _resolve_data_session(validated_root, session_id)
        source = SessionReplaySource(
            QueryService(validated_root),
            session.session_id,
            page_size=resolved_config.page_size,
        )
        return cls(
            session,
            source,
            sink,
            config=resolved_config,
            clock=clock,
            cancellation=cancellation,
        )

    @property
    def session_id(self) -> str:
        """Return the session this replay reads."""
        return self._session.session_id

    @property
    def state(self) -> ReplayState:
        """Return the current lifecycle state."""
        return self._state

    @property
    def config(self) -> ReplayConfig:
        """Return the validated configuration of this replay."""
        return self._config

    @property
    def source(self) -> ReplaySource:
        """Return the source this replay reads frames from."""
        return self._source

    @property
    def cancellation(self) -> ReplayCancellation:
        """Return the cancellation signal shared with this replay."""
        return self._cancellation

    def cancel(self) -> None:
        """Request cancellation of this replay.

        Idempotent, and honoured at the next frame boundary. A replay cancelled
        before it started never reads a page at all.
        """
        self._cancellation.cancel()

    def run(self) -> ReplayReport:
        """Replay the recording and return what was delivered.

        Returns:
            The report of a ``COMPLETED`` or ``CANCELLED`` replay. A cancelled
            replay carries the frames it had already delivered.

        Raises:
            ReplayStateError: If this replay is not ``IDLE`` — a replay is a
                one-shot operation, and refusing a second run is what guarantees
                no replay continues after the first one ended.
            ReplaySourceError: If the recorded frames could not be delivered
                exactly as recorded.
            ReplaySinkError: If the sink refused a frame.
            ReplaySchedulerError: If the injected clock broke the scheduling
                contract.
        """
        self._require_idle()
        self._state = ReplayState.RUNNING
        try:
            return self._replay()
        except BaseException:
            # Every failure arm — including an interruption from outside — ends
            # in a terminal state, so no caller can observe a replay that is
            # still "running" after run() has left.
            self._state = ReplayState.FAILED
            raise
        finally:
            self._source.close()

    def _replay(self) -> ReplayReport:
        started_at = self._reading()
        if self._cancellation.cancelled:
            return self._finish(
                ReplayState.CANCELLED,
                frames_emitted=0,
                source_page_count=0,
                source_peak_page_frames=0,
                relative_span=None,
                first_sequence=None,
                last_sequence=None,
            )

        origin: float | None = None
        previous_frame: Frame | None = None
        previous_reading = started_at
        emitted = 0
        page_count = 0
        peak_page_frames = 0
        last_offset: float | None = None
        first_sequence: int | None = None
        last_sequence: int | None = None
        completed = False
        cancelled = False

        stream: Generator[FrameQueryPage] = self._source.pages()
        try:
            for page in stream:
                self._require_page_session(page)
                page_count += 1
                peak_page_frames = max(peak_page_frames, len(page.frames))
                for frame in page.frames:
                    if self._cancellation.cancelled:
                        cancelled = True
                        break
                    if emitted >= self._session.frame_count:
                        raise ReplaySourceError(
                            "The replay source delivered more frames than the"
                            " session records.",
                            code="replay.source_overrun",
                            details={
                                "session_id": self._session.session_id,
                                "recorded_frame_count": self._session.frame_count,
                                "delivered_frame_count": emitted + 1,
                            },
                        )
                    if origin is None:
                        origin = frame.normalized_timestamp
                    _require_ordered_after(previous_frame, frame)
                    previous_frame = frame
                    offset = frame.normalized_timestamp - origin
                    deadline = started_at + offset
                    previous_reading = self._wait_until(
                        deadline, previous_reading=previous_reading
                    )
                    if self._cancellation.cancelled:
                        cancelled = True
                        break
                    self._emit(
                        ReplayFrameEvent(
                            index=emitted,
                            frame=frame,
                            relative_offset=offset,
                            scheduled_at=deadline,
                        )
                    )
                    emitted += 1
                    last_offset = offset
                    if first_sequence is None:
                        first_sequence = frame.sequence
                    last_sequence = frame.sequence
                if cancelled:
                    break
                if not page.has_more:
                    completed = True
                    break
        except (QueryError, DataError) as error:
            # The source is the only place a storage failure can enter a replay.
            # It is translated here, once, so a caller of run() never sees a
            # query or data error and always knows the recording could not be
            # delivered as recorded.
            raise ReplaySourceError(
                "The recorded frames could not be read back for replay.",
                code="replay.source_unavailable",
                details={
                    "session_id": self._session.session_id,
                    "cause": error.code,
                    "cause_type": type(error).__name__,
                    "page_count": page_count,
                },
                recoverable=error.recoverable,
            ) from error
        finally:
            # A stream that was abandoned mid-page is closed here rather than
            # left suspended: an unfinished replay leaves nothing running.
            stream.close()

        if cancelled:
            return self._finish(
                ReplayState.CANCELLED,
                frames_emitted=emitted,
                source_page_count=page_count,
                source_peak_page_frames=peak_page_frames,
                relative_span=last_offset,
                first_sequence=first_sequence,
                last_sequence=last_sequence,
            )
        if not completed:
            raise ReplaySourceError(
                "The replay source ended without a final page.",
                code="replay.source_truncated",
                details={
                    "session_id": self._session.session_id,
                    "page_count": page_count,
                    "replayed_frame_count": emitted,
                },
            )
        if emitted != self._session.frame_count:
            raise ReplaySourceError(
                "The replay source did not deliver every frame the session records.",
                code="replay.source_incomplete",
                details={
                    "session_id": self._session.session_id,
                    "recorded_frame_count": self._session.frame_count,
                    "replayed_frame_count": emitted,
                },
            )
        return self._finish(
            ReplayState.COMPLETED,
            frames_emitted=emitted,
            source_page_count=page_count,
            source_peak_page_frames=peak_page_frames,
            relative_span=last_offset,
            first_sequence=first_sequence,
            last_sequence=last_sequence,
        )

    def _wait_until(self, deadline: float, *, previous_reading: float) -> float:
        """Wait for one deadline and return the reading reached afterwards.

        Raises:
            ReplaySchedulerError: If the clock cannot wait, moved backwards, or
                did not reach the deadline by more than the scheduling tolerance.
        """
        try:
            self._clock.sleep_until(deadline)
        except ReplayError:
            raise
        except Exception as error:
            raise ReplaySchedulerError(
                "The replay clock could not wait for the next frame.",
                code="replay.scheduler_wait_failed",
                details={"deadline": deadline, "cause": type(error).__name__},
            ) from error
        reading = self._reading()
        if reading < previous_reading:
            raise ReplaySchedulerError(
                "The replay clock moved backwards.",
                code="replay.scheduler_clock_backwards",
                details={
                    "previous_reading": previous_reading,
                    "reading": reading,
                    "deadline": deadline,
                },
            )
        if reading + SCHEDULE_TOLERANCE_SECONDS < deadline:
            raise ReplaySchedulerError(
                "The replay clock did not reach the deadline it was given.",
                code="replay.scheduler_deadline_missed",
                details={
                    "deadline": deadline,
                    "reading": reading,
                    "tolerance_seconds": SCHEDULE_TOLERANCE_SECONDS,
                },
            )
        return reading

    def _reading(self) -> float:
        """Return one validated clock reading.

        Raises:
            ReplaySchedulerError: If the clock did not return a finite number.
        """
        value = self._clock.monotonic()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
        ):
            raise ReplaySchedulerError(
                "The replay clock returned an unusable monotonic reading.",
                code="replay.scheduler_failed",
                details={"reading": repr(value)},
            )
        return float(value)

    def _emit(self, event: ReplayFrameEvent) -> None:
        """Hand one event to the sink.

        Raises:
            ReplaySinkError: If the sink refused the frame.
        """
        try:
            self._sink.emit(event)
        except ReplaySinkError:
            raise
        except Exception as error:
            raise ReplaySinkError(
                "The replay sink did not accept a frame.",
                code="replay.sink_failed",
                details={
                    "session_id": self._session.session_id,
                    "event_index": event.index,
                    "sequence": event.frame.sequence,
                    "cause_type": type(error).__name__,
                },
            ) from error

    def _require_page_session(self, page: FrameQueryPage) -> None:
        if page.session_id != self._session.session_id:
            raise ReplaySourceError(
                "The replay source returned a page from another data session.",
                code="replay.source_session_mismatch",
                details={
                    "session_id": self._session.session_id,
                    "page_session_id": page.session_id,
                },
            )

    def _require_idle(self) -> None:
        if self._state is not ReplayState.IDLE:
            raise ReplayStateError(
                f"A replay cannot run from the {self._state.value} state.",
                code="replay.invalid_run_state",
                details={"session_id": self._session.session_id, "state": self._state.value},
            )

    def _require_replayable(self) -> None:
        """Apply the configured session policies before any frame is read.

        Raises:
            ReplaySessionError: If the session is still being written, is not
                replayable under the configured completion policy, or is empty
                and empty sessions are rejected.
        """
        state = self._session.state
        if state is DataSessionState.ACTIVE:
            raise ReplaySessionError(
                "An active data session is still being written and cannot be"
                " replayed deterministically.",
                code="replay.session_not_replayable",
                details={
                    "session_id": self._session.session_id,
                    "state": state.value,
                    "policy": self._config.session_completion_policy.value,
                },
            )
        if (
            state is not DataSessionState.COMPLETED
            and self._config.session_completion_policy
            is SessionCompletionPolicy.COMPLETED_ONLY
        ):
            raise ReplaySessionError(
                "The data session did not complete and the completion policy"
                " only replays completed recordings.",
                code="replay.session_not_replayable",
                details={
                    "session_id": self._session.session_id,
                    "state": state.value,
                    "policy": self._config.session_completion_policy.value,
                },
            )
        if (
            self._session.frame_count == 0
            and self._config.empty_session_policy is EmptySessionPolicy.REJECT
        ):
            raise ReplaySessionError(
                "The data session holds no committed frame to replay.",
                code="replay.empty_session",
                details={
                    "session_id": self._session.session_id,
                    "state": state.value,
                    "frame_count": self._session.frame_count,
                    "policy": self._config.empty_session_policy.value,
                },
            )

    def _finish(
        self,
        state: ReplayState,
        *,
        frames_emitted: int,
        source_page_count: int,
        source_peak_page_frames: int,
        relative_span: float | None,
        first_sequence: int | None,
        last_sequence: int | None,
    ) -> ReplayReport:
        self._state = state
        return ReplayReport(
            session_id=self._session.session_id,
            state=state,
            frames_emitted=frames_emitted,
            source_page_count=source_page_count,
            source_peak_page_frames=source_peak_page_frames,
            relative_span=relative_span,
            first_sequence=first_sequence,
            last_sequence=last_sequence,
        )


def _require_ordered_after(previous: Frame | None, frame: Frame) -> None:
    """Refuse a stream whose frame order or recorded time contradicts itself.

    A recording is ordered by sequence and its normalized timestamps never move
    backwards — the writer enforces both when it commits a segment. A stream that
    breaks either claim is not the recording that was captured, so the replay
    stops rather than inventing a schedule: equal timestamps are legal (two frames
    may share a timestamp), a decreasing one is not.

    Raises:
        ReplaySourceError: If the sequence did not increase or recorded time moved
            backwards.
    """
    if previous is None:
        return
    if frame.sequence <= previous.sequence:
        raise ReplaySourceError(
            "The replay source returned a frame out of sequence order.",
            code="replay.non_increasing_sequence",
            details={
                "previous_sequence": previous.sequence,
                "sequence": frame.sequence,
            },
        )
    if frame.normalized_timestamp < previous.normalized_timestamp:
        raise ReplaySourceError(
            "The recorded timestamps of the session are not monotonic.",
            code="replay.non_monotonic_timestamps",
            details={
                "previous_timestamp": previous.normalized_timestamp,
                "timestamp": frame.normalized_timestamp,
                "previous_sequence": previous.sequence,
                "sequence": frame.sequence,
            },
        )


def _open_project(project_root: Path) -> ProjectHandle:
    """Open the root through the project domain's own authority.

    Raises:
        ReplayProjectError: If the project domain refuses the root, carrying the
            project's stable cause code in ``details["cause"]``.
    """
    try:
        return ProjectService().open(project_root)
    except ProjectError as error:
        raise _translate_project_error(error) from error


def _close_project(handle: ProjectHandle) -> None:
    """Close a validated project handle, translating a close failure.

    Raises:
        ReplayProjectError: If the project database could not be closed, so a raw
            project error never crosses the replay boundary.
    """
    try:
        handle.close()
    except ProjectError as error:
        raise _translate_project_error(error) from error


def _translate_project_error(error: ProjectError) -> ReplayProjectError:
    """Translate a project-domain failure into the replay failure contract.

    The replay domain owns its own taxonomy, so a project failure becomes a
    :class:`ReplayProjectError`. Only the project's stable cause code crosses the
    boundary: the project domain's own details carry absolute host paths, which
    must never leave the project layer through a replay error.
    """
    if error.code in _INVALID_PROJECT_ROOT_CAUSES:
        return ReplayProjectError(
            "The replay project root is not an existing directory.",
            code="replay.invalid_project",
            details={"cause": error.code},
        )
    return ReplayProjectError(
        "The project is not a usable CAN-X project and cannot be replayed.",
        code="replay.project_unavailable",
        details={"cause": error.code},
    )


def _resolve_data_session(project_root: Path, session_id: str) -> DataSession:
    """Read one persisted session, translating data failures into replay ones.

    The project root has already been validated by the project domain, so this
    only resolves the session inside it: it never re-validates the project, and
    the authority stays :class:`~canx.project.service.ProjectService`.

    Raises:
        ReplayValidationError: If the session id is not a UUID string.
        ReplayProjectError: If the project's data store is missing or unusable.
        ReplaySessionError: If the session is not registered in this project.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        raise ReplayValidationError(
            "session_id must be a non-empty UUID string.",
            code="replay.invalid_session_id",
            details={"session_id": repr(session_id)},
        )
    try:
        return DataSessionService(project_root).get_session(session_id)
    except DataStorageError as error:
        # `DataStorageError` is a `DataSessionError`, so it is caught first: an
        # unusable project database is not a missing session.
        raise ReplayProjectError(
            "The project data store is not available for replay.",
            code="replay.project_unavailable",
            details={"cause": error.code},
        ) from error
    except DataSessionError as error:
        raise ReplaySessionError(
            "The requested data session is not registered in this project.",
            code="replay.session_not_found",
            details={"session_id": session_id, "cause": error.code},
        ) from error
    except DataValidationError as error:
        raise ReplayValidationError(
            "session_id must be a UUID string.",
            code="replay.invalid_session_id",
            details={"session_id": session_id, "cause": error.code},
        ) from error
    except DataError as error:
        raise ReplayProjectError(
            "The project data store is not available for replay.",
            code="replay.project_unavailable",
            details={"cause": error.code},
        ) from error


def _require_data_session(session: object) -> DataSession:
    if not isinstance(session, DataSession):
        raise ReplayValidationError(
            "A replay needs the persisted session metadata it will replay.",
            code="replay.validation_failed",
            details={"type": type(session).__name__},
        )
    return session


def _require_source(source: object) -> ReplaySource:
    if not isinstance(source, ReplaySource):
        raise ReplayValidationError(
            "A replay needs a ReplaySource.",
            code="replay.validation_failed",
            details={"type": type(source).__name__},
        )
    return source


def _require_sink(sink: object) -> ReplaySink:
    if not isinstance(sink, ReplaySink):
        raise ReplayValidationError(
            "A replay needs a sink with an emit(event) method.",
            code="replay.validation_failed",
            details={"type": type(sink).__name__},
        )
    return sink


def _require_config(config: ReplayConfig | None) -> ReplayConfig:
    if config is None:
        return ReplayConfig()
    if not isinstance(config, ReplayConfig):
        raise ReplayValidationError(
            "config must be a ReplayConfig.",
            code="replay.validation_failed",
            details={"type": type(config).__name__},
        )
    return config


def _require_clock(clock: object) -> ReplayClock:
    if not isinstance(clock, ReplayClock):
        raise ReplayValidationError(
            "clock must be a ReplayClock.",
            code="replay.validation_failed",
            details={"type": type(clock).__name__},
        )
    return clock


def _require_cancellation(cancellation: object) -> ReplayCancellation:
    if not isinstance(cancellation, ReplayCancellation):
        raise ReplayValidationError(
            "cancellation must be a ReplayCancellation.",
            code="replay.validation_failed",
            details={"type": type(cancellation).__name__},
        )
    return cancellation
