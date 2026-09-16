"""Typed, engine-independent query domain models.

Nothing in this module imports ``duckdb``, ``sqlite3``, ``pyarrow`` or FastAPI.
A ``DuckDBPyRelation``, a ``DuckDBPyConnection``, a ``pyarrow.Table`` or a raw
row tuple must never leak into the query API: the engine translates both ways
and the public surface speaks only in CAN-X models.

Bounds are always inclusive; a pagination cursor is always exclusive. That
asymmetry is deliberate and matches §27 of the V0.2-03 contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from uuid import UUID

from canx.domain.frame import Direction, Frame
from canx.query.errors import QueryValidationError

#: Query safety bounds. These are runtime limits, not product defaults.
DEFAULT_FRAME_QUERY_LIMIT = 1_000
MAX_QUERY_ROWS = 10_000
DEFAULT_ARBITRATION_ID_TOP_N = 100
MAX_ARBITRATION_ID_TOP_N = 1_000

#: The largest arbitration id an extended CAN id can carry.
MAX_ARBITRATION_ID = 0x1FFFFFFF


@dataclass(frozen=True, slots=True)
class FrameFilter:
    """A bounded, fully typed description of which frames a query may see.

    Every field is optional except the owning session; ``None`` means "do not
    restrict on this axis". The filter never carries SQL, a column name or an
    ordering: the engine owns those, the filter only owns the predicates.
    """

    session_id: str
    sequence_start: int | None = None
    sequence_end: int | None = None
    normalized_timestamp_start: float | None = None
    normalized_timestamp_end: float | None = None
    channel_ids: tuple[str, ...] | None = None
    arbitration_ids: tuple[int, ...] | None = None
    directions: tuple[Direction, ...] | None = None
    is_extended: bool | None = None
    is_fd: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "session_id", _normalize_session_id(self.session_id)
        )
        _require_optional_sequence(self.sequence_start, field="sequence_start")
        _require_optional_sequence(self.sequence_end, field="sequence_end")
        if (
            self.sequence_start is not None
            and self.sequence_end is not None
            and self.sequence_end < self.sequence_start
        ):
            raise QueryValidationError(
                "sequence_end must not be smaller than sequence_start.",
                code="query.invalid_sequence_range",
                details={
                    "sequence_start": self.sequence_start,
                    "sequence_end": self.sequence_end,
                },
            )
        _require_optional_timestamp(
            self.normalized_timestamp_start, field="normalized_timestamp_start"
        )
        _require_optional_timestamp(
            self.normalized_timestamp_end, field="normalized_timestamp_end"
        )
        if (
            self.normalized_timestamp_start is not None
            and self.normalized_timestamp_end is not None
            and self.normalized_timestamp_end < self.normalized_timestamp_start
        ):
            raise QueryValidationError(
                "normalized_timestamp_end must not be smaller than"
                " normalized_timestamp_start.",
                code="query.invalid_timestamp_range",
                details={
                    "normalized_timestamp_start": self.normalized_timestamp_start,
                    "normalized_timestamp_end": self.normalized_timestamp_end,
                },
            )
        object.__setattr__(self, "channel_ids", _normalize_channel_ids(self.channel_ids))
        object.__setattr__(
            self, "arbitration_ids", _normalize_arbitration_ids(self.arbitration_ids)
        )
        object.__setattr__(self, "directions", _normalize_directions(self.directions))
        _require_optional_bool(self.is_extended, field="is_extended")
        _require_optional_bool(self.is_fd, field="is_fd")


@dataclass(frozen=True, slots=True)
class FrameQuery:
    """One bounded page request: a filter, a cursor and a row budget."""

    filter: FrameFilter
    after_sequence: int | None = None
    limit: int = DEFAULT_FRAME_QUERY_LIMIT

    def __post_init__(self) -> None:
        if not isinstance(self.filter, FrameFilter):
            raise QueryValidationError(
                "FrameQuery requires a FrameFilter.",
                code="query.invalid_filter",
                details={"type": type(self.filter).__name__},
            )
        _require_optional_sequence(self.after_sequence, field="after_sequence")
        if (
            not isinstance(self.limit, int)
            or isinstance(self.limit, bool)
            or self.limit <= 0
        ):
            raise QueryValidationError(
                "limit must be a positive integer.",
                code="query.invalid_limit",
                details={"limit": repr(self.limit)},
            )
        if self.limit > MAX_QUERY_ROWS:
            raise QueryValidationError(
                "limit exceeds the maximum number of rows a single query may return.",
                code="query.limit_exceeded",
                details={"limit": self.limit, "max_query_rows": MAX_QUERY_ROWS},
            )

    @property
    def session_id(self) -> str:
        """Return the session this query is scoped to."""
        return self.filter.session_id


@dataclass(frozen=True, slots=True)
class FrameQueryPage:
    """One deterministic page of canonical frames plus its continuation cursor.

    ``has_more`` is decided by the engine fetching one row beyond ``limit``, so
    it never depends on an ``OFFSET`` scan. ``next_after_sequence`` is the
    exclusive cursor for the next page and is ``None`` exactly when no further
    page exists.
    """

    session_id: str
    frames: tuple[Frame, ...]
    has_more: bool
    next_after_sequence: int | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "session_id", _normalize_session_id(self.session_id)
        )
        if not isinstance(self.frames, tuple) or not all(
            isinstance(frame, Frame) for frame in self.frames
        ):
            raise QueryValidationError(
                "frames must be a tuple of canonical frames.",
                code="query.invalid_page",
            )
        if not isinstance(self.has_more, bool):
            raise QueryValidationError(
                "has_more must be a boolean.",
                code="query.invalid_page",
                details={"has_more": repr(self.has_more)},
            )
        if self.has_more:
            if not self.frames:
                raise QueryValidationError(
                    "A page that reports more rows must carry at least one frame.",
                    code="query.invalid_page",
                )
            if self.next_after_sequence != self.frames[-1].sequence:
                raise QueryValidationError(
                    "next_after_sequence must be the last returned sequence"
                    " when more rows exist.",
                    code="query.invalid_page",
                    details={
                        "next_after_sequence": self.next_after_sequence,
                        "last_sequence": self.frames[-1].sequence,
                    },
                )
        elif self.next_after_sequence is not None:
            raise QueryValidationError(
                "A final page must not carry a continuation cursor.",
                code="query.invalid_page",
                details={"next_after_sequence": self.next_after_sequence},
            )


@dataclass(frozen=True, slots=True)
class FrameQuerySummary:
    """Bounded aggregate over a filter, computed inside the engine.

    Bounds are ``None`` exactly when the filter matched nothing, so an empty
    session summarizes as ``count = 0`` with no invented bounds.
    """

    session_id: str
    matching_frame_count: int
    first_sequence: int | None
    last_sequence: int | None
    first_normalized_timestamp: float | None
    last_normalized_timestamp: float | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "session_id", _normalize_session_id(self.session_id)
        )
        _require_count(
            self.matching_frame_count, field="matching_frame_count", scope="summary"
        )
        bounds_present = (
            self.first_sequence,
            self.last_sequence,
            self.first_normalized_timestamp,
            self.last_normalized_timestamp,
        )
        if self.matching_frame_count == 0:
            if any(value is not None for value in bounds_present):
                raise QueryValidationError(
                    "An empty summary must not report sequence or timestamp bounds.",
                    code="query.invalid_summary",
                )
            return
        if any(value is None for value in bounds_present):
            raise QueryValidationError(
                "A non-empty summary must report both sequence and timestamp bounds.",
                code="query.invalid_summary",
            )
        assert self.first_sequence is not None
        assert self.last_sequence is not None
        assert self.first_normalized_timestamp is not None
        assert self.last_normalized_timestamp is not None
        if self.last_sequence < self.first_sequence:
            raise QueryValidationError(
                "summary sequence bounds are inverted.",
                code="query.invalid_summary",
            )
        if self.last_normalized_timestamp < self.first_normalized_timestamp:
            raise QueryValidationError(
                "summary timestamp bounds are inverted.",
                code="query.invalid_summary",
            )


@dataclass(frozen=True, slots=True)
class ArbitrationIdCount:
    """One row of a bounded arbitration-id histogram."""

    arbitration_id: int
    is_extended: bool
    frame_count: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.arbitration_id, int)
            or isinstance(self.arbitration_id, bool)
            or not 0 <= self.arbitration_id <= MAX_ARBITRATION_ID
        ):
            raise QueryValidationError(
                "arbitration_id is out of range.",
                code="query.invalid_arbitration_id",
                details={"arbitration_id": repr(self.arbitration_id)},
            )
        if not isinstance(self.is_extended, bool):
            raise QueryValidationError(
                "is_extended must be a boolean.",
                code="query.invalid_arbitration_id",
                details={"is_extended": repr(self.is_extended)},
            )
        if (
            not isinstance(self.frame_count, int)
            or isinstance(self.frame_count, bool)
            or self.frame_count <= 0
        ):
            raise QueryValidationError(
                "frame_count must be a positive integer.",
                code="query.invalid_arbitration_id_count",
                details={"frame_count": repr(self.frame_count)},
            )


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """Internal record of which registered segments a query will actually scan.

    Exposed for diagnostics and pruning tests; it is not a public query result
    and carries no rows.
    """

    session_id: str
    registered_segment_count: int
    candidate_segment_count: int
    relative_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "session_id", _normalize_session_id(self.session_id)
        )
        _require_count(
            self.registered_segment_count,
            field="registered_segment_count",
            scope="plan",
        )
        _require_count(
            self.candidate_segment_count,
            field="candidate_segment_count",
            scope="plan",
        )
        if self.candidate_segment_count > self.registered_segment_count:
            raise QueryValidationError(
                "a query plan cannot scan more segments than are registered.",
                code="query.invalid_plan",
                details={
                    "registered_segment_count": self.registered_segment_count,
                    "candidate_segment_count": self.candidate_segment_count,
                },
            )
        if len(self.relative_paths) != self.candidate_segment_count:
            raise QueryValidationError(
                "relative_paths must describe exactly the candidate segments.",
                code="query.invalid_plan",
                details={
                    "relative_paths": len(self.relative_paths),
                    "candidate_segment_count": self.candidate_segment_count,
                },
            )

    @property
    def pruned_segment_count(self) -> int:
        """Return how many registered segments the plan deliberately skipped."""
        return self.registered_segment_count - self.candidate_segment_count

    @property
    def empty(self) -> bool:
        """Return whether the plan scans nothing."""
        return self.candidate_segment_count == 0


def _normalize_session_id(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise QueryValidationError(
            "session_id must be a non-empty UUID string.",
            code="query.invalid_session_id",
            details={"session_id": repr(value)},
        )
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError) as error:
        raise QueryValidationError(
            "session_id is not a UUID.",
            code="query.invalid_session_id",
            details={"session_id": value},
        ) from error


def _require_optional_sequence(value: object, *, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise QueryValidationError(
            f"{field} must be a non-negative integer or None.",
            code="query.invalid_sequence",
            details={field: repr(value)},
        )


def _require_optional_timestamp(value: object, *, field: str) -> None:
    if value is None:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise QueryValidationError(
            f"{field} must be a finite non-negative number of seconds or None.",
            code="query.invalid_timestamp",
            details={field: repr(value)},
        )


def _require_optional_bool(value: object, *, field: str) -> None:
    if value is not None and not isinstance(value, bool):
        raise QueryValidationError(
            f"{field} must be a boolean or None.",
            code="query.invalid_flag",
            details={field: repr(value)},
        )


def _require_count(value: object, *, field: str, scope: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise QueryValidationError(
            f"{field} must be a non-negative integer.",
            code=f"query.invalid_{scope}",
            details={field: repr(value)},
        )


def _normalize_channel_ids(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, tuple) or not value:
        raise QueryValidationError(
            "channel_ids must be a non-empty tuple of channel names or None.",
            code="query.invalid_channel_id",
            details={"channel_ids": repr(value)},
        )
    for channel_id in value:
        if not isinstance(channel_id, str) or not channel_id.strip():
            raise QueryValidationError(
                "each channel id must be a non-blank string.",
                code="query.invalid_channel_id",
                details={"channel_id": repr(channel_id)},
            )
    return value


def _normalize_arbitration_ids(value: object) -> tuple[int, ...] | None:
    if value is None:
        return None
    if not isinstance(value, tuple) or not value:
        raise QueryValidationError(
            "arbitration_ids must be a non-empty tuple of ids or None.",
            code="query.invalid_arbitration_id",
            details={"arbitration_ids": repr(value)},
        )
    for arbitration_id in value:
        if (
            not isinstance(arbitration_id, int)
            or isinstance(arbitration_id, bool)
            or not 0 <= arbitration_id <= MAX_ARBITRATION_ID
        ):
            raise QueryValidationError(
                "each arbitration id must be an integer in"
                " [0, 0x1FFFFFFF].",
                code="query.invalid_arbitration_id",
                details={"arbitration_id": repr(arbitration_id)},
            )
    return value


def _normalize_directions(value: object) -> tuple[Direction, ...] | None:
    if value is None:
        return None
    if not isinstance(value, tuple) or not value:
        raise QueryValidationError(
            "directions must be a non-empty tuple of Direction values or None.",
            code="query.invalid_direction",
            details={"directions": repr(value)},
        )
    for direction in value:
        if not isinstance(direction, Direction):
            raise QueryValidationError(
                "each direction must be a Direction.",
                code="query.invalid_direction",
                details={"direction": repr(direction)},
            )
    return value
