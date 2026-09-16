"""Behavior tests for the engine-independent query domain models."""

from dataclasses import FrozenInstanceError
from math import inf, nan

import pytest
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.query.errors import QueryValidationError
from canx.query.model import (
    DEFAULT_ARBITRATION_ID_TOP_N,
    DEFAULT_FRAME_QUERY_LIMIT,
    MAX_ARBITRATION_ID_TOP_N,
    MAX_QUERY_ROWS,
    ArbitrationIdCount,
    FrameFilter,
    FrameQuery,
    FrameQueryPage,
    FrameQuerySummary,
    QueryPlan,
)

SESSION_ID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"


def frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x123,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=2,
        data=bytes([sequence % 256, 0x01]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=1.0 + sequence,
        normalized_timestamp=float(sequence),
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def _filter(**changes: object) -> FrameFilter:
    values: dict[str, object] = {"session_id": SESSION_ID}
    values.update(changes)
    return FrameFilter(**values)  # type: ignore[arg-type]


def test_a_filter_with_only_a_session_matches_every_frame_in_it() -> None:
    frame_filter = _filter()

    assert frame_filter.session_id == SESSION_ID
    assert frame_filter.sequence_start is None
    assert frame_filter.sequence_end is None
    assert frame_filter.normalized_timestamp_start is None
    assert frame_filter.normalized_timestamp_end is None
    assert frame_filter.channel_ids is None
    assert frame_filter.arbitration_ids is None
    assert frame_filter.directions is None
    assert frame_filter.is_extended is None
    assert frame_filter.is_fd is None


def test_the_default_limits_are_the_documented_runtime_bounds() -> None:
    assert DEFAULT_FRAME_QUERY_LIMIT == 1_000
    assert MAX_QUERY_ROWS == 10_000
    assert DEFAULT_ARBITRATION_ID_TOP_N == 100
    assert MAX_ARBITRATION_ID_TOP_N == 1_000


def test_a_filter_normalizes_the_session_identity() -> None:
    assert _filter(session_id=SESSION_ID.upper()).session_id == SESSION_ID


def test_a_valid_filter_accepts_every_supported_axis() -> None:
    frame_filter = _filter(
        sequence_start=0,
        sequence_end=10,
        normalized_timestamp_start=0.0,
        normalized_timestamp_end=10.0,
        channel_ids=("can0", "can1"),
        arbitration_ids=(0x123, 0x456),
        directions=(Direction.RX, Direction.TX),
        is_extended=False,
        is_fd=True,
    )

    assert frame_filter.channel_ids == ("can0", "can1")
    assert frame_filter.arbitration_ids == (0x123, 0x456)
    assert frame_filter.directions == (Direction.RX, Direction.TX)
    assert frame_filter.is_fd is True


@pytest.mark.parametrize(
    "changes",
    [
        {"session_id": "not-a-uuid"},
        {"session_id": ""},
        {"session_id": 42},
        {"sequence_start": -1},
        {"sequence_end": -1},
        {"sequence_start": 5, "sequence_end": 4},
        {"normalized_timestamp_start": -1.0},
        {"normalized_timestamp_end": -0.5},
        {"normalized_timestamp_start": 2.0, "normalized_timestamp_end": 1.0},
        {"normalized_timestamp_start": nan},
        {"normalized_timestamp_end": inf},
        {"channel_ids": ()},
        {"channel_ids": ("can0", "")},
        {"channel_ids": ("can0", "   ")},
        {"channel_ids": ["can0"]},
        {"arbitration_ids": ()},
        {"arbitration_ids": (-1,)},
        {"arbitration_ids": (0x20000000,)},
        {"arbitration_ids": (True,)},
        {"directions": ()},
        {"directions": ("rx",)},
        {"is_extended": 1},
        {"is_extended": "true"},
        {"is_fd": 0},
    ],
    ids=[
        "session-not-uuid",
        "session-empty",
        "session-not-a-string",
        "sequence-start-negative",
        "sequence-end-negative",
        "sequence-inverted",
        "timestamp-start-negative",
        "timestamp-end-negative",
        "timestamp-inverted",
        "timestamp-nan",
        "timestamp-infinity",
        "channels-empty",
        "channel-blank-item",
        "channel-whitespace-item",
        "channels-not-a-tuple",
        "arbitration-empty",
        "arbitration-negative",
        "arbitration-too-large",
        "arbitration-bool",
        "directions-empty",
        "direction-raw-string",
        "is-extended-int",
        "is-extended-string",
        "is-fd-int",
    ],
)
def test_an_invalid_filter_is_never_accepted(changes: dict[str, object]) -> None:
    with pytest.raises(QueryValidationError):
        _filter(**changes)


def test_a_filter_is_immutable() -> None:
    frame_filter = _filter()

    with pytest.raises(FrozenInstanceError):
        frame_filter.sequence_start = 1  # type: ignore[misc]


def test_a_query_defaults_to_the_bounded_default_limit() -> None:
    query = FrameQuery(filter=_filter())

    assert query.limit == DEFAULT_FRAME_QUERY_LIMIT
    assert query.after_sequence is None
    assert query.session_id == SESSION_ID


@pytest.mark.parametrize(
    "changes",
    [
        {"filter": "not-a-filter"},
        {"after_sequence": -1},
        {"after_sequence": "5"},
        {"limit": 0},
        {"limit": -1},
        {"limit": MAX_QUERY_ROWS + 1},
        {"limit": 1.5},
        {"limit": True},
    ],
    ids=[
        "filter-type",
        "cursor-negative",
        "cursor-not-int",
        "limit-zero",
        "limit-negative",
        "limit-above-hard-maximum",
        "limit-float",
        "limit-bool",
    ],
)
def test_an_invalid_query_is_never_accepted(changes: dict[str, object]) -> None:
    values: dict[str, object] = {"filter": _filter()}
    values.update(changes)

    with pytest.raises(QueryValidationError):
        FrameQuery(**values)  # type: ignore[arg-type]


def test_a_query_accepts_the_hard_maximum_limit() -> None:
    query = FrameQuery(filter=_filter(), limit=MAX_QUERY_ROWS)

    assert query.limit == MAX_QUERY_ROWS


def test_a_page_carries_its_continuation_cursor() -> None:
    page = FrameQueryPage(
        session_id=SESSION_ID,
        frames=(frame(0), frame(1)),
        has_more=True,
        next_after_sequence=1,
    )

    assert page.has_more is True
    assert page.next_after_sequence == 1
    assert [item.sequence for item in page.frames] == [0, 1]


def test_a_final_page_reports_no_continuation() -> None:
    page = FrameQueryPage(
        session_id=SESSION_ID,
        frames=(frame(0),),
        has_more=False,
        next_after_sequence=None,
    )

    assert page.next_after_sequence is None


def test_an_empty_page_reports_no_continuation() -> None:
    page = FrameQueryPage(
        session_id=SESSION_ID, frames=(), has_more=False, next_after_sequence=None
    )

    assert page.frames == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"frames": (), "has_more": True, "next_after_sequence": 1},
        {"frames": (frame(0), frame(1)), "has_more": True, "next_after_sequence": 0},
        {"frames": (frame(0),), "has_more": False, "next_after_sequence": 0},
        {"frames": (frame(0),), "has_more": "yes", "next_after_sequence": None},
        {"frames": [frame(0)], "has_more": False, "next_after_sequence": None},
        {"frames": (object(),), "has_more": False, "next_after_sequence": None},
    ],
    ids=[
        "more-but-empty",
        "cursor-not-last-sequence",
        "final-page-with-cursor",
        "has-more-not-bool",
        "frames-not-a-tuple",
        "frames-not-canonical",
    ],
)
def test_an_incoherent_page_is_never_accepted(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "session_id": SESSION_ID,
        "frames": (),
        "has_more": False,
        "next_after_sequence": None,
    }
    values.update(changes)

    with pytest.raises(QueryValidationError):
        FrameQueryPage(**values)  # type: ignore[arg-type]


def test_an_empty_summary_reports_no_bounds() -> None:
    summary = FrameQuerySummary(
        session_id=SESSION_ID,
        matching_frame_count=0,
        first_sequence=None,
        last_sequence=None,
        first_normalized_timestamp=None,
        last_normalized_timestamp=None,
    )

    assert summary.matching_frame_count == 0


def test_a_populated_summary_reports_its_bounds() -> None:
    summary = FrameQuerySummary(
        session_id=SESSION_ID,
        matching_frame_count=3,
        first_sequence=0,
        last_sequence=2,
        first_normalized_timestamp=0.0,
        last_normalized_timestamp=2.0,
    )

    assert summary.last_sequence == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"matching_frame_count": 0, "first_sequence": 0},
        {"matching_frame_count": 3, "first_sequence": None},
        {"matching_frame_count": -1},
        {"matching_frame_count": 3, "first_sequence": 5},
        {"matching_frame_count": 3, "first_normalized_timestamp": 5.0},
    ],
    ids=[
        "empty-with-bounds",
        "populated-without-bounds",
        "negative-count",
        "inverted-sequences",
        "inverted-timestamps",
    ],
)
def test_an_incoherent_summary_is_never_accepted(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "session_id": SESSION_ID,
        "matching_frame_count": 3,
        "first_sequence": 0,
        "last_sequence": 2,
        "first_normalized_timestamp": 0.0,
        "last_normalized_timestamp": 2.0,
    }
    values.update(changes)

    with pytest.raises(QueryValidationError):
        FrameQuerySummary(**values)  # type: ignore[arg-type]


def test_an_arbitration_id_count_holds_its_boundary_values() -> None:
    assert ArbitrationIdCount(arbitration_id=0, is_extended=False, frame_count=1)
    assert ArbitrationIdCount(
        arbitration_id=0x1FFFFFFF, is_extended=True, frame_count=5
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"arbitration_id": -1},
        {"arbitration_id": 0x20000000},
        {"arbitration_id": True},
        {"is_extended": 1},
        {"frame_count": 0},
        {"frame_count": -1},
    ],
    ids=[
        "id-negative",
        "id-too-large",
        "id-bool",
        "extended-not-bool",
        "count-zero",
        "count-negative",
    ],
)
def test_an_invalid_arbitration_id_count_is_never_accepted(
    changes: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "arbitration_id": 0x123,
        "is_extended": False,
        "frame_count": 3,
    }
    values.update(changes)

    with pytest.raises(QueryValidationError):
        ArbitrationIdCount(**values)  # type: ignore[arg-type]


def test_a_plan_reports_how_many_segments_it_pruned() -> None:
    plan = QueryPlan(
        session_id=SESSION_ID,
        registered_segment_count=5,
        candidate_segment_count=2,
        relative_paths=("data/sessions/x/segments/000001.parquet", "b"),
    )

    assert plan.pruned_segment_count == 3
    assert plan.empty is False


def test_a_plan_with_no_candidates_is_empty() -> None:
    plan = QueryPlan(
        session_id=SESSION_ID,
        registered_segment_count=3,
        candidate_segment_count=0,
        relative_paths=(),
    )

    assert plan.empty is True
    assert plan.pruned_segment_count == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"candidate_segment_count": 4},
        {"candidate_segment_count": 1},
        {"registered_segment_count": -1},
    ],
    ids=[
        "more-candidates-than-registered",
        "paths-do-not-match-candidate-count",
        "negative-registered",
    ],
)
def test_an_incoherent_plan_is_never_accepted(changes: dict[str, object]) -> None:
    values: dict[str, object] = {
        "session_id": SESSION_ID,
        "registered_segment_count": 3,
        "candidate_segment_count": 2,
        "relative_paths": ("a", "b"),
    }
    values.update(changes)

    with pytest.raises(QueryValidationError):
        QueryPlan(**values)  # type: ignore[arg-type]
