"""First-layer segment pruning from registered segment metadata.

This is not an optimizer. It only uses the bounds V0.2-02 already records for
every committed segment (``first_sequence``/``last_sequence`` and
``first_timestamp``/``last_timestamp``) to leave segments out of a query when
the request cannot possibly match any frame inside them. Everything finer —
arbitration id, channel, direction, frame type — stays a frame-level predicate
applied by the engine, because a segment carries no such metadata.
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
        frame_filter: The typed filter; its inclusive bounds prune whole segments.
        after_sequence: An exclusive pagination cursor, pruned the same way.

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
    if after_sequence is not None and segment.last_sequence <= after_sequence:
        return False
    if (
        frame_filter.normalized_timestamp_start is not None
        and segment.last_timestamp < frame_filter.normalized_timestamp_start
    ):
        return False
    return not (
        frame_filter.normalized_timestamp_end is not None
        and segment.first_timestamp > frame_filter.normalized_timestamp_end
    )
