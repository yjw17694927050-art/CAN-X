"""Regression: a segment's timestamp bounds are not trustworthy min/max.

V0.2-03 first pruned candidate segments with ``first_timestamp`` /
``last_timestamp``. Those describe the first and last frame *in segment order*,
not the extremes: the V0.2-02 writer only requires that a batch's last timestamp
is not before its first, and that a batch does not begin before the previous one
ended. A segment may therefore legitimately hold ``[0, 100, 1]`` and record
``first = 0`` / ``last = 1``, so a query for ``timestamp = 100`` pruned away the
very segment holding the match and silently returned nothing.

Timestamp pruning is now disabled; these tests exercise the real path (project →
data session → Parquet → QueryService → planning → DuckDB → frames), so they fail
again if that pruning is ever reintroduced. Sequence and cursor pruning, which
rest on genuinely ordered metadata, must stay active.
"""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from canx.data import repository
from canx.data.model import DataSegment
from canx.data.parquet import write_segment
from canx.data.session import (
    DataSessionService,
    resolve_within_root,
    segment_relative_path,
)
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.query.model import FrameFilter, FrameQuery
from canx.query.service import QueryService

STREAM_ID = "timestamp-regression-stream"
FRAMES_PER_SEGMENT = 3

#: A legal segment: first = 0, last = 1, with a frame at 100 in the middle.
NON_MONOTONIC = [(0, 0.0), (1, 100.0), (2, 1.0)]


def frame(sequence: int, normalized_timestamp: float) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x300,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=2,
        data=bytes([sequence % 256, 0x5E]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=2_000.0 + sequence,
        normalized_timestamp=normalized_timestamp,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def _session(handle: ProjectHandle, contents: list[tuple[int, float]]) -> str:
    """Persist one session the way the earlier, more permissive writer did.

    The session writer now refuses a batch whose timestamps descend anywhere, so
    a segment like ``[0, 100, 1]`` can no longer be produced through it. Such a
    segment can still exist in a project written by that earlier version — which
    is exactly the data this module defends — so the frames are committed with the
    same Parquet writer and registered with the same repository call, bypassing
    only the newer writer admission check.
    """
    service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
    writer = service.start(stream_id=STREAM_ID)
    session = writer.session
    frames = [frame(sequence, timestamp) for sequence, timestamp in contents]
    for index, start in enumerate(range(0, len(frames), FRAMES_PER_SEGMENT)):
        chunk = tuple(frames[start : start + FRAMES_PER_SEGMENT])
        relative_path = segment_relative_path(session.session_id, index)
        byte_size = write_segment(
            resolve_within_root(handle.root, relative_path),
            session_id=session.session_id,
            stream_id=STREAM_ID,
            segment_index=index,
            frames=chunk,
        )
        at = datetime.now(UTC)
        segment = DataSegment(
            segment_id=str(uuid4()),
            session_id=session.session_id,
            segment_index=index,
            relative_path=relative_path,
            frame_count=len(chunk),
            first_sequence=chunk[0].sequence,
            last_sequence=chunk[-1].sequence,
            first_timestamp=chunk[0].normalized_timestamp,
            last_timestamp=chunk[-1].normalized_timestamp,
            byte_size=byte_size,
            created_at=at,
        )
        updated = replace(
            session,
            frame_count=session.frame_count + len(chunk),
            segment_count=index + 1,
            first_sequence=(
                chunk[0].sequence if session.first_sequence is None else session.first_sequence
            ),
            last_sequence=chunk[-1].sequence,
            first_timestamp=(
                chunk[0].normalized_timestamp
                if session.first_timestamp is None
                else session.first_timestamp
            ),
            last_timestamp=chunk[-1].normalized_timestamp,
            updated_at=at,
        )
        with repository.data_connection(handle.root) as connection:
            repository.register_segment(connection, segment=segment, session=updated)
        session = updated
    writer.finalize()
    return session.session_id


def _project(tmp_path: Path) -> ProjectHandle:
    return ProjectService().create(tmp_path / "vehicle.canx", display_name="A")


def test_a_non_monotonic_segment_still_yields_its_middle_frame(tmp_path: Path) -> None:
    """The persisted segment records bounds 0..1, yet the frame at 100 must be found."""
    with _project(tmp_path) as handle:
        session_id = _session(handle, NON_MONOTONIC)
        sessions = DataSessionService(handle.root)
        segment = sessions.list_segments(session_id)[0]
        stored = sessions.read_segment(session_id, 0)

        # The persisted bounds are first/last frame *in segment order*, not min/max:
        # the segment really does hold a frame at 100, yet records 0.0 .. 1.0.
        assert segment.first_timestamp == 0.0
        assert segment.last_timestamp == 1.0
        assert [item.normalized_timestamp for item in stored] == [0.0, 100.0, 1.0]

        service = QueryService(handle.root)
        frame_filter = FrameFilter(
            session_id=session_id,
            normalized_timestamp_start=100.0,
            normalized_timestamp_end=100.0,
        )
        page = service.query_frames(FrameQuery(filter=frame_filter, limit=100))
        summary = service.summarize_frames(frame_filter)

    assert [item.sequence for item in page.frames] == [1]
    assert page.frames[0].normalized_timestamp == 100.0
    assert summary.matching_frame_count == 1
    assert summary.first_sequence == summary.last_sequence == 1


def test_the_plan_keeps_a_segment_whose_bounds_exclude_the_wanted_timestamp(
    tmp_path: Path,
) -> None:
    with _project(tmp_path) as handle:
        session_id = _session(handle, NON_MONOTONIC)
        service = QueryService(handle.root)

        plan = service.plan_frames(
            FrameFilter(
                session_id=session_id,
                normalized_timestamp_start=100.0,
                normalized_timestamp_end=100.0,
            )
        )

    assert plan.registered_segment_count == 1
    assert plan.candidate_segment_count == 1
    assert plan.pruned_segment_count == 0


def test_a_non_monotonic_frame_is_found_next_to_another_segment(tmp_path: Path) -> None:
    """Over-inclusion must cost a scan, never a wrong answer."""
    contents = [(0, 0.0), (1, 100.0), (2, 1.0), (3, 2.0), (4, 3.0), (5, 4.0)]

    with _project(tmp_path) as handle:
        session_id = _session(handle, contents)
        assert len(DataSessionService(handle.root).list_segments(session_id)) == 2

        service = QueryService(handle.root)
        frame_filter = FrameFilter(
            session_id=session_id,
            normalized_timestamp_start=100.0,
            normalized_timestamp_end=100.0,
        )
        plan = service.plan_frames(frame_filter)
        page = service.query_frames(FrameQuery(filter=frame_filter, limit=100))

    assert plan.candidate_segment_count == 2
    assert [item.sequence for item in page.frames] == [1]


def test_a_timestamp_only_filter_keeps_every_registered_segment(tmp_path: Path) -> None:
    contents = [(sequence, float(sequence)) for sequence in range(36)]

    with _project(tmp_path) as handle:
        session_id = _session(handle, contents)
        service = QueryService(handle.root)
        frame_filter = FrameFilter(
            session_id=session_id,
            normalized_timestamp_start=17.0,
            normalized_timestamp_end=19.0,
        )

        plan = service.plan_frames(frame_filter)
        page = service.query_frames(FrameQuery(filter=frame_filter, limit=100))

    assert plan.registered_segment_count == 12
    assert plan.candidate_segment_count == 12
    assert plan.pruned_segment_count == 0
    assert [item.sequence for item in page.frames] == [17, 18, 19]


def test_a_combined_filter_still_prunes_on_sequence(tmp_path: Path) -> None:
    contents = [(sequence, float(sequence)) for sequence in range(36)]

    with _project(tmp_path) as handle:
        session_id = _session(handle, contents)
        plan = QueryService(handle.root).plan_frames(
            FrameFilter(
                session_id=session_id,
                sequence_start=3,
                sequence_end=11,
                normalized_timestamp_start=0.0,
                normalized_timestamp_end=35.0,
            )
        )

    assert plan.registered_segment_count == 12
    assert plan.candidate_segment_count == 3
    assert plan.pruned_segment_count == 9


def test_a_timestamp_range_that_matches_nothing_returns_an_empty_result(
    tmp_path: Path,
) -> None:
    contents = [(sequence, float(sequence)) for sequence in range(9)]

    with _project(tmp_path) as handle:
        session_id = _session(handle, contents)
        service = QueryService(handle.root)
        frame_filter = FrameFilter(
            session_id=session_id,
            normalized_timestamp_start=9_000.0,
            normalized_timestamp_end=9_001.0,
        )

        page = service.query_frames(FrameQuery(filter=frame_filter, limit=100))
        summary = service.summarize_frames(frame_filter)

    assert page.frames == ()
    assert page.has_more is False
    assert page.next_after_sequence is None
    assert summary.matching_frame_count == 0
    assert summary.first_sequence is None
    assert summary.last_sequence is None


def test_ordinary_monotonic_timestamps_still_filter_correctly(tmp_path: Path) -> None:
    contents = [(sequence, float(sequence)) for sequence in range(30)]

    with _project(tmp_path) as handle:
        session_id = _session(handle, contents)
        service = QueryService(handle.root)
        frame_filter = FrameFilter(
            session_id=session_id,
            normalized_timestamp_start=7.0,
            normalized_timestamp_end=12.0,
        )

        page = service.query_frames(FrameQuery(filter=frame_filter, limit=100))
        summary = service.summarize_frames(frame_filter)

    assert [item.sequence for item in page.frames] == [7, 8, 9, 10, 11, 12]
    assert summary.matching_frame_count == 6
    assert summary.first_normalized_timestamp == 7.0
    assert summary.last_normalized_timestamp == 12.0


def test_sequence_and_cursor_pruning_are_still_active(tmp_path: Path) -> None:
    contents = [(sequence, float(sequence)) for sequence in range(36)]

    with _project(tmp_path) as handle:
        session_id = _session(handle, contents)
        service = QueryService(handle.root)

        window = service.plan_frames(
            FrameFilter(session_id=session_id, sequence_start=7, sequence_end=8)
        )
        cursor = service.plan_frames(
            FrameFilter(session_id=session_id), after_sequence=32
        )

    assert window.registered_segment_count == 12
    assert window.candidate_segment_count == 1
    assert window.pruned_segment_count == 11
    assert cursor.candidate_segment_count == 1


def test_a_cursor_still_pages_over_non_monotonic_timestamps(tmp_path: Path) -> None:
    contents = [(0, 0.0), (1, 100.0), (2, 1.0), (3, 2.0), (4, 3.0), (5, 4.0)]

    with _project(tmp_path) as handle:
        session_id = _session(handle, contents)
        service = QueryService(handle.root)
        frame_filter = FrameFilter(session_id=session_id)

        collected: list[int] = []
        cursor = None
        while True:
            page = service.query_frames(
                FrameQuery(filter=frame_filter, after_sequence=cursor, limit=2)
            )
            collected.extend(item.sequence for item in page.frames)
            if not page.has_more:
                break
            cursor = page.next_after_sequence

    assert collected == [0, 1, 2, 3, 4, 5]
