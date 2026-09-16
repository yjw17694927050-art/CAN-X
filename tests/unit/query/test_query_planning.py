"""Behavior tests for segment-level pruning.

Pruning is the only place where a query decides *not* to look at data, so the
tests pin down both directions: a segment that can match must stay, and a segment
that cannot must go. A false prune is a silent wrong answer, so the "cannot match"
cases are checked as carefully as the results of a real query.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from canx.data.model import DataSegment
from canx.domain.frame import Direction
from canx.query.errors import QueryIntegrityError, QueryValidationError
from canx.query.model import FrameFilter
from canx.query.planning import plan_segments

SESSION_ID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
OTHER_SESSION_ID = "11111111-2222-4333-8444-555555555555"
SEGMENT_SIZE = 10


def _segment(
    index: int, *, session_id: str = SESSION_ID, size: int = SEGMENT_SIZE
) -> DataSegment:
    first = index * size
    last = first + size - 1
    return DataSegment(
        segment_id=str(uuid4()),
        session_id=session_id,
        segment_index=index,
        relative_path=f"data/sessions/{session_id}/segments/{index:06d}.parquet",
        frame_count=size,
        first_sequence=first,
        last_sequence=last,
        first_timestamp=float(first),
        last_timestamp=float(last),
        byte_size=256,
        created_at=datetime(2026, 9, 16, 9, 0, tzinfo=UTC),
    )


def _segments(count: int = 5, *, session_id: str = SESSION_ID) -> tuple[DataSegment, ...]:
    return tuple(_segment(index, session_id=session_id) for index in range(count))


def _candidate_indexes(plan_paths: tuple[str, ...]) -> list[int]:
    return [int(path.rsplit("/", 1)[-1].removesuffix(".parquet")) for path in plan_paths]


def test_an_unbounded_filter_keeps_every_registered_segment() -> None:
    segments = _segments()

    plan = plan_segments(segments, FrameFilter(session_id=SESSION_ID))

    assert plan.registered_segment_count == 5
    assert plan.candidate_segment_count == 5
    assert plan.pruned_segment_count == 0
    assert plan.empty is False


def test_a_sequence_window_keeps_only_the_segments_it_overlaps() -> None:
    segments = _segments()

    plan = plan_segments(
        segments, FrameFilter(session_id=SESSION_ID, sequence_start=15, sequence_end=24)
    )

    assert _candidate_indexes(plan.relative_paths) == [1, 2]
    assert plan.pruned_segment_count == 3


def test_a_window_inside_one_segment_keeps_only_that_segment() -> None:
    plan = plan_segments(
        _segments(), FrameFilter(session_id=SESSION_ID, sequence_start=12, sequence_end=13)
    )

    assert _candidate_indexes(plan.relative_paths) == [1]


def test_a_window_touching_a_segment_boundary_keeps_it() -> None:
    """Bounds are inclusive, so a request that names the last frame still matches."""
    plan = plan_segments(
        _segments(), FrameFilter(session_id=SESSION_ID, sequence_start=19, sequence_end=19)
    )

    assert _candidate_indexes(plan.relative_paths) == [1]


def test_an_open_ended_sequence_start_prunes_earlier_segments() -> None:
    plan = plan_segments(_segments(), FrameFilter(session_id=SESSION_ID, sequence_start=25))

    assert _candidate_indexes(plan.relative_paths) == [2, 3, 4]


def test_an_open_ended_sequence_end_prunes_later_segments() -> None:
    plan = plan_segments(_segments(), FrameFilter(session_id=SESSION_ID, sequence_end=14))

    assert _candidate_indexes(plan.relative_paths) == [0, 1]


def test_a_window_beyond_the_data_prunes_everything() -> None:
    plan = plan_segments(
        _segments(), FrameFilter(session_id=SESSION_ID, sequence_start=1_000)
    )

    assert plan.empty is True
    assert plan.relative_paths == ()


def test_a_cursor_prunes_segments_that_are_already_consumed() -> None:
    plan = plan_segments(
        _segments(), FrameFilter(session_id=SESSION_ID), after_sequence=29
    )

    assert _candidate_indexes(plan.relative_paths) == [3, 4]


def test_a_cursor_matching_a_segment_end_still_keeps_that_segment() -> None:
    """The cursor is exclusive per frame, so the containing segment must stay."""
    plan = plan_segments(
        _segments(), FrameFilter(session_id=SESSION_ID), after_sequence=25
    )

    assert _candidate_indexes(plan.relative_paths) == [2, 3, 4]


def test_a_timestamp_window_prunes_the_same_way_as_a_sequence_window() -> None:
    plan = plan_segments(
        _segments(),
        FrameFilter(session_id=SESSION_ID, normalized_timestamp_start=15.0,
                    normalized_timestamp_end=24.0),
    )

    assert _candidate_indexes(plan.relative_paths) == [1, 2]


def test_frame_level_axes_never_prune_a_segment() -> None:
    """Channel, id, direction and frame type are invisible at the segment level."""
    plan = plan_segments(
        _segments(),
        FrameFilter(
            session_id=SESSION_ID,
            channel_ids=("can0",),
            arbitration_ids=(0x123,),
            directions=(Direction.RX,),
            is_extended=False,
            is_fd=True,
        ),
    )

    assert plan.candidate_segment_count == 5


def test_sequence_and_timestamp_windows_intersect() -> None:
    plan = plan_segments(
        _segments(),
        FrameFilter(
            session_id=SESSION_ID,
            sequence_start=15,
            sequence_end=34,
            normalized_timestamp_start=20.0,
            normalized_timestamp_end=29.0,
        ),
    )

    assert _candidate_indexes(plan.relative_paths) == [2]


def test_the_plan_scans_the_candidate_paths_of_the_registered_segments() -> None:
    segments = _segments()

    plan = plan_segments(
        segments, FrameFilter(session_id=SESSION_ID, sequence_start=15, sequence_end=24)
    )

    registered = {segment.relative_path for segment in segments}
    assert set(plan.relative_paths) <= registered
    assert len(plan.relative_paths) == plan.candidate_segment_count


def test_an_empty_session_plans_nothing() -> None:
    plan = plan_segments((), FrameFilter(session_id=SESSION_ID))

    assert plan.registered_segment_count == 0
    assert plan.empty is True


def test_a_segment_from_another_session_is_refused() -> None:
    foreign = (_segment(0, session_id=OTHER_SESSION_ID),)

    with pytest.raises(QueryIntegrityError) as info:
        plan_segments(foreign, FrameFilter(session_id=SESSION_ID))

    assert info.value.code == "query.session_mismatch"
    assert info.value.details["session_id"] == SESSION_ID


@pytest.mark.parametrize("cursor", [-1, "5", True, 1.5])
def test_an_invalid_cursor_is_refused(cursor: object) -> None:
    with pytest.raises(QueryValidationError):
        plan_segments(_segments(), FrameFilter(session_id=SESSION_ID), after_sequence=cursor)  # type: ignore[arg-type]
