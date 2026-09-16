"""First-layer segment pruning from registered segment metadata.

This is not an optimizer. It only uses the *sequence* bounds V0.2-02 already
records for every committed segment (``first_sequence`` / ``last_sequence``) to
leave segments out of a query when the request cannot possibly match any frame
inside them. Everything finer — timestamp, arbitration id, channel, direction,
frame type — stays a frame-level predicate applied by the engine.

Timestamp bounds are deliberately excluded from pruning; :func:`_may_match`
explains why.
"""

from __future__ import annotations

from collections.abc import Sequence

from canx.data.model import DataSegment
from canx.query.errors import QueryIntegrityError, QueryValidationError
from canx.query.model import FrameFilter, QueryPlan


def plan_segments(
    segments: Sequence[DataSegment],
    frame_filter: FrameFilter,
    *,
    after_sequence: int | None = None,
) -> QueryPlan:
    """Return which registered segments a query over ``frame_filter`` must scan.

    Args:
        segments: The frozen candidate list for one session, in segment order.
        frame_filter: The typed filter; its inclusive *sequence* bounds prune
            whole segments. Timestamp bounds never prune.
        after_sequence: An exclusive pagination cursor, pruned like sequence.

    Raises:
        QueryValidationError: If ``after_sequence`` is not a non-negative int.
        QueryIntegrityError: If a supplied segment belongs to another session.
    """
    if after_sequence is not None and (
        not isinstance(after_sequence, int)
        or isinstance(after_sequence, bool)
        or after_sequence < 0
    ):
        raise QueryValidationError(
            "after_sequence must be a non-negative integer or None.",
            code="query.invalid_sequence",
            details={"after_sequence": repr(after_sequence)},
        )
    foreign = [
        segment.relative_path
        for segment in segments
        if segment.session_id != frame_filter.session_id
    ]
    if foreign:
        raise QueryIntegrityError(
            "A segment in the candidate list belongs to another session.",
            code="query.session_mismatch",
            details={
                "session_id": frame_filter.session_id,
                "relative_paths": foreign,
            },
        )
    candidates = tuple(
        segment
        for segment in segments
        if _may_match(segment, frame_filter, after_sequence=after_sequence)
    )
    return QueryPlan(
        session_id=frame_filter.session_id,
        registered_segment_count=len(segments),
        candidate_segment_count=len(candidates),
        relative_paths=tuple(segment.relative_path for segment in candidates),
    )


def _may_match(
    segment: DataSegment, frame_filter: FrameFilter, *, after_sequence: int | None
) -> bool:
    """Return whether ``segment`` can hold at least one frame the filter accepts.

    Each check is the segment-level relaxation of the frame-level predicate: a
    segment is skipped only when the whole segment lies outside the bound.

    Only *sequence* metadata may prune. ``first_timestamp`` / ``last_timestamp``
    describe the first and last frame **in segment order**; they are NOT
    guaranteed to be the minimum and maximum timestamps of every frame in an
    already-persisted segment. The V0.2-02 writer only checks that a batch's last
    timestamp is not before its first, and that a batch does not begin before the
    previous batch ended — so a segment may legitimately hold ``[0, 100, 1]``,
    recorded as ``first = 0`` / ``last = 1`` while a frame at ``100`` sits in the
    middle.

    Pruning on those bounds would skip a segment that really does contain a
    matching frame, silently returning fewer rows than the persisted data
    supports. A timestamp filter therefore stays a frame-level predicate inside
    DuckDB, where it is evaluated against each row.

    Do not reintroduce timestamp pruning until the persistence contract stores,
    or otherwise guarantees, trustworthy per-segment min/max timestamp bounds.
    """
    if (
        frame_filter.sequence_start is not None
        and segment.last_sequence < frame_filter.sequence_start
    ):
        return False
    if (
        frame_filter.sequence_end is not None
        and segment.first_sequence > frame_filter.sequence_end
    ):
        return False
    # Cursor: a segment whose last frame is at or before the cursor holds nothing new.
    return after_sequence is None or segment.last_sequence > after_sequence
