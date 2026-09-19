"""Typed, engine- and device-independent offline replay models.

Nothing in this module imports DuckDB, PyArrow, SQLite or FastAPI: the replay
domain speaks in canonical :class:`~canx.domain.frame.Frame` objects plus a
schedule, and a storage engine never reaches a sink.

Offline replay reproduces **recorded** timing, at exactly 1.0x:

    relative_offset(frame_n) = normalized_timestamp(frame_n)
                             - normalized_timestamp(first_frame)

The first frame of the session therefore has ``relative_offset == 0.0`` and the
last one carries the total span of the recording. A speed multiplier, pause,
resume, step, loop and a replay range are V0.4-09 and are deliberately absent
here — the schedule is a function of the recording alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from canx.domain.frame import Frame
from canx.query.model import MAX_QUERY_ROWS
from canx.replay.errors import ReplayValidationError

#: Frames fetched and emitted per bounded query page. Small enough that the
#: working set of a replay is a page rather than a session; tests drive it down
#: to a handful of frames to prove the pagination is real.
DEFAULT_REPLAY_PAGE_SIZE = 256

#: A replay page is one bounded query page, so it cannot exceed the query
#: domain's hard row limit. Reusing that constant is what keeps the two limits
#: from drifting apart.
MAX_REPLAY_PAGE_SIZE = MAX_QUERY_ROWS


class ReplayState(StrEnum):
    """Lifecycle state of one offline replay.

    The set is deliberately closed and small. ``PAUSED``, ``LOOPING`` and
    ``TRIGGER_WAIT`` belong to V0.4-09 and are not pre-implemented here, and
    ``CANCELLED`` is explicit rather than folded into ``FAILED`` because a
    cancellation the caller asked for is not a fault.
    """

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        """Return whether no further transition is possible from this state."""
        return self in (
            ReplayState.COMPLETED,
            ReplayState.FAILED,
            ReplayState.CANCELLED,
        )


class SessionCompletionPolicy(StrEnum):
    """Which persisted session states an offline replay may read.

    An ``ACTIVE`` session is never replayable: a writer still holds it, so its
    frame set can change between two pages and the replay could not be
    deterministic. The two terminal-but-incomplete states are a policy decision,
    which is why they are a configuration value rather than a silent default.
    """

    #: Only a ``COMPLETED`` recording is replayed.
    COMPLETED_ONLY = "completed_only"
    #: Also replay ``INTERRUPTED`` and ``FAILED`` recordings — their committed
    #: segments are still the bytes that were captured — but never ``ACTIVE``.
    ALLOW_INCOMPLETE = "allow_incomplete"


class EmptySessionPolicy(StrEnum):
    """What a replay does with a session that committed no frame at all."""

    #: Refuse it: an empty recording is far more often a mistake than a target.
    REJECT = "reject"
    #: Replay it: complete immediately, emitting nothing.
    ALLOW = "allow"


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    """A validated, immutable description of one replay run.

    There is no speed field on purpose: V0.4-08 replays recorded timing at
    exactly 1.0x, and a knob whose only legal value is ``1.0`` would advertise
    flexibility that does not exist.
    """

    page_size: int = DEFAULT_REPLAY_PAGE_SIZE
    session_completion_policy: SessionCompletionPolicy = (
        SessionCompletionPolicy.COMPLETED_ONLY
    )
    empty_session_policy: EmptySessionPolicy = EmptySessionPolicy.REJECT

    def __post_init__(self) -> None:
        if (
            not isinstance(self.page_size, int)
            or isinstance(self.page_size, bool)
            or self.page_size <= 0
        ):
            raise ReplayValidationError(
                "page_size must be a positive integer.",
                code="replay.invalid_page_size",
                details={"page_size": repr(self.page_size)},
            )
        if self.page_size > MAX_REPLAY_PAGE_SIZE:
            raise ReplayValidationError(
                "page_size exceeds the largest bounded query page.",
                code="replay.page_size_exceeded",
                details={
                    "page_size": self.page_size,
                    "max_page_size": MAX_REPLAY_PAGE_SIZE,
                },
            )
        _require_member(
            self.session_completion_policy,
            SessionCompletionPolicy,
            field="session_completion_policy",
        )
        _require_member(
            self.empty_session_policy, EmptySessionPolicy, field="empty_session_policy"
        )


@dataclass(frozen=True, slots=True)
class ReplayFrameEvent:
    """One canonical frame, placed on the replay schedule.

    ``frame`` is the canonical :class:`~canx.domain.frame.Frame` exactly as it
    was read back from the recording — sequence, channel, arbitration id, the
    extended/FD/BRS/ESI flags, DLC, data, direction, every timestamp and flags.
    Offline replay never rebuilds an information-reduced frame, so a sink can
    rebuild the original bus traffic byte for byte.

    ``relative_offset`` is the recorded timing (seconds after the first frame of
    the session) and ``scheduled_at`` is the deadline in the injected clock's
    own domain. At 1.0x the two differ only by the clock's starting reading.
    """

    index: int
    frame: Frame
    relative_offset: float
    scheduled_at: float

    def __post_init__(self) -> None:
        if not isinstance(self.frame, Frame):
            raise ReplayValidationError(
                "a replay event carries a canonical Frame.",
                code="replay.invalid_event",
                details={"type": type(self.frame).__name__},
            )
        if not isinstance(self.index, int) or isinstance(self.index, bool) or self.index < 0:
            raise ReplayValidationError(
                "event index must be a non-negative integer.",
                code="replay.invalid_event",
                details={"index": repr(self.index)},
            )
        _require_seconds(self.relative_offset, field="relative_offset", scope="event")
        _require_seconds(self.scheduled_at, field="scheduled_at", scope="event")

    @property
    def sequence(self) -> int:
        """Return the sequence number of the carried frame."""
        return self.frame.sequence


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """What one finished replay actually did.

    Only the two non-fault outcomes produce a report: a run that failed raises
    instead of returning, so a caller can never mistake a failure for a finished
    replay. ``frames_emitted`` counts frames the sink accepted, which is the
    only claim replay makes about delivery.
    """

    session_id: str
    state: ReplayState
    frames_emitted: int
    source_page_count: int
    source_peak_page_frames: int
    relative_span: float | None
    first_sequence: int | None
    last_sequence: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not self.session_id:
            raise ReplayValidationError(
                "report session_id must be a non-empty string.",
                code="replay.invalid_report",
            )
        if self.state not in (ReplayState.COMPLETED, ReplayState.CANCELLED):
            raise ReplayValidationError(
                "a replay report describes a completed or cancelled replay.",
                code="replay.invalid_report",
                details={"state": str(self.state)},
            )
        for field in ("frames_emitted", "source_page_count", "source_peak_page_frames"):
            _require_count(getattr(self, field), field=field)
        if self.source_peak_page_frames > MAX_REPLAY_PAGE_SIZE:
            raise ReplayValidationError(
                "no single replay page can exceed the bounded query page limit.",
                code="replay.invalid_report",
                details={"source_peak_page_frames": self.source_peak_page_frames},
            )
        if self.frames_emitted == 0:
            if any(
                value is not None
                for value in (
                    self.relative_span,
                    self.first_sequence,
                    self.last_sequence,
                )
            ):
                raise ReplayValidationError(
                    "a replay that emitted no frame reports no span and no sequence.",
                    code="replay.invalid_report",
                )
            return
        if self.relative_span is None:
            raise ReplayValidationError(
                "a replay that emitted frames reports its recorded span.",
                code="replay.invalid_report",
            )
        _require_seconds(self.relative_span, field="relative_span", scope="report")
        assert self.first_sequence is not None
        assert self.last_sequence is not None
        if self.last_sequence < self.first_sequence:
            raise ReplayValidationError(
                "report sequence bounds are inverted.",
                code="replay.invalid_report",
            )

    @property
    def completed(self) -> bool:
        """Return whether the whole recording was replayed."""
        return self.state is ReplayState.COMPLETED


def _require_member(value: object, enum_type: type[StrEnum], *, field: str) -> None:
    if not isinstance(value, enum_type):
        raise ReplayValidationError(
            f"{field} must be a {enum_type.__name__}.",
            code="replay.invalid_config",
            details={field: repr(value)},
        )


def _require_seconds(value: object, *, field: str, scope: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise ReplayValidationError(
            f"{field} must be a finite non-negative number of seconds.",
            code=f"replay.invalid_{scope}",
            details={field: repr(value)},
        )


def _require_count(value: object, *, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ReplayValidationError(
            f"{field} must be a non-negative integer.",
            code="replay.invalid_report",
            details={field: repr(value)},
        )
