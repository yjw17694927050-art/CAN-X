"""Volume smoke for the bounded historical query path.

This is a scale *smoke*, not a benchmark: it proves that a realistic
multi-segment volume answers a bounded page, a selective id query and a narrow
window without ever handing the whole dataset to Python. It makes no large-scale
claim — the 10-50 GB target stays NOT VERIFIED until a separate benchmark exists.

Timings recorded here are informational only and are deliberately *not* a pass
threshold.
"""

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.query.model import FrameFilter, FrameQuery
from canx.query.service import QueryService

STREAM_ID = "smoke-stream"
TOTAL_FRAMES = 100_000
FRAMES_PER_SEGMENT = 5_000
APPEND_BATCH_SIZE = 2_500
SEGMENT_COUNT = TOTAL_FRAMES // FRAMES_PER_SEGMENT
DISTINCT_IDS = 8
SELECTIVE_ID = 0x103
NARROW_START = 40_000
NARROW_END = 40_099


def frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x100 + (sequence % DISTINCT_IDS),
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=4,
        data=bytes([sequence % 256, (sequence >> 8) % 256, 0xBB, 0xCC]),
        direction=Direction.RX if sequence % 2 == 0 else Direction.TX,
        hardware_timestamp=None,
        host_timestamp=6_000.0 + sequence,
        normalized_timestamp=sequence / 1_000.0,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def _write_smoke_session(handle: ProjectHandle) -> float:
    service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
    writer = service.start(stream_id=STREAM_ID)
    started = time.perf_counter()
    for start in range(0, TOTAL_FRAMES, APPEND_BATCH_SIZE):
        writer.append(
            FrameBatch.create(
                stream_id=STREAM_ID,
                frames=[
                    frame(sequence)
                    for sequence in range(start, start + APPEND_BATCH_SIZE)
                ],
            )
        )
    completed = writer.finalize()
    duration = time.perf_counter() - started
    assert completed.frame_count == TOTAL_FRAMES
    assert completed.segment_count == SEGMENT_COUNT
    return duration


@pytest.fixture(scope="module")
def smoke_project(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, str, float]]:
    """Write the volume once for every smoke assertion in this module."""
    root = tmp_path_factory.mktemp("query-smoke") / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Smoke Project")
    session_id = None
    try:
        write_seconds = _write_smoke_session(handle)
        service = DataSessionService(handle.root)
        session_id = service.list_sessions()[0].session_id
        handle.close()
        yield root, session_id, write_seconds
    finally:
        handle.close()


def _write_report(**fields: object) -> None:
    rendered = " · ".join(f"{key}={value}" for key, value in fields.items())
    print(f"[query-smoke] {rendered}")


def test_a_bounded_page_never_returns_the_whole_dataset(
    smoke_project: tuple[Path, str, float],
) -> None:
    root, session_id, write_seconds = smoke_project
    service = QueryService(root)
    started = time.perf_counter()

    page = service.query_frames(
        FrameQuery(filter=FrameFilter(session_id=session_id), limit=1_000)
    )
    elapsed = time.perf_counter() - started

    _write_report(
        total_frames=TOTAL_FRAMES,
        segments=SEGMENT_COUNT,
        write_seconds=round(write_seconds, 2),
        filter="unbounded",
        query_limit=1_000,
        returned_rows=len(page.frames),
        page_count=1,
        candidate_segments=SEGMENT_COUNT,
        registered_segments=SEGMENT_COUNT,
        result="bounded",
        first_query_seconds=round(elapsed, 3),
        timing="informational only, not a gate",
    )

    assert len(page.frames) == 1_000
    assert page.has_more is True
    assert page.next_after_sequence == page.frames[-1].sequence == 999
    assert [item.sequence for item in page.frames] == list(range(1_000))


def test_a_second_page_continues_from_the_cursor(
    smoke_project: tuple[Path, str, float],
) -> None:
    root, session_id, _seconds = smoke_project
    service = QueryService(root)
    frame_filter = FrameFilter(session_id=session_id)

    first = service.query_frames(FrameQuery(filter=frame_filter, limit=1_000))
    second = service.query_frames(
        FrameQuery(filter=frame_filter, after_sequence=first.next_after_sequence, limit=1_000)
    )

    assert len(second.frames) == 1_000
    assert second.frames[0].sequence == 1_000
    assert second.frames[-1].sequence == 1_999
    assert second.has_more is True


def test_a_selective_arbitration_id_query_is_correct(
    smoke_project: tuple[Path, str, float],
) -> None:
    root, session_id, _seconds = smoke_project
    service = QueryService(root)
    frame_filter = FrameFilter(session_id=session_id, arbitration_ids=(SELECTIVE_ID,))
    plan = service.plan_frames(frame_filter)
    started = time.perf_counter()

    summary = service.summarize_frames(frame_filter)
    page = service.query_frames(FrameQuery(filter=frame_filter, limit=1_000))
    elapsed = time.perf_counter() - started

    expected = sum(
        1 for sequence in range(TOTAL_FRAMES) if 0x100 + (sequence % DISTINCT_IDS) == SELECTIVE_ID
    )
    _write_report(
        total_frames=TOTAL_FRAMES,
        segments=SEGMENT_COUNT,
        filter=f"arbitration_id=0x{SELECTIVE_ID:X}",
        query_limit=1_000,
        returned_rows=len(page.frames),
        matched_rows=summary.matching_frame_count,
        page_count=1,
        candidate_segments=plan.candidate_segment_count,
        registered_segments=plan.registered_segment_count,
        result="bounded selective",
        selective_query_seconds=round(elapsed, 3),
        timing="informational only, not a gate",
    )

    assert summary.matching_frame_count == expected
    assert len(page.frames) == 1_000
    assert page.has_more is True
    # A segment carries no arbitration metadata, so this axis cannot prune.
    assert plan.candidate_segment_count == SEGMENT_COUNT
    assert all(item.arbitration_id == SELECTIVE_ID for item in page.frames)


def test_a_narrow_sequence_window_prunes_to_one_segment(
    smoke_project: tuple[Path, str, float],
) -> None:
    root, session_id, _seconds = smoke_project
    service = QueryService(root)
    frame_filter = FrameFilter(
        session_id=session_id, sequence_start=NARROW_START, sequence_end=NARROW_END
    )
    plan = service.plan_frames(frame_filter)
    started = time.perf_counter()

    page = service.query_frames(FrameQuery(filter=frame_filter, limit=1_000))
    elapsed = time.perf_counter() - started

    _write_report(
        total_frames=TOTAL_FRAMES,
        segments=SEGMENT_COUNT,
        filter=f"sequence {NARROW_START}-{NARROW_END}",
        query_limit=1_000,
        returned_rows=len(page.frames),
        page_count=1,
        candidate_segments=plan.candidate_segment_count,
        registered_segments=plan.registered_segment_count,
        result="bounded windowed",
        window_query_seconds=round(elapsed, 3),
        timing="informational only, not a gate",
    )

    assert plan.registered_segment_count == SEGMENT_COUNT
    assert plan.candidate_segment_count == 1
    assert plan.pruned_segment_count == SEGMENT_COUNT - 1
    assert [item.sequence for item in page.frames] == list(
        range(NARROW_START, NARROW_END + 1)
    )
    assert page.has_more is False


def test_a_narrow_timestamp_window_keeps_all_segments_but_still_returns_the_right_rows(
    smoke_project: tuple[Path, str, float],
) -> None:
    """Timestamp segment pruning is deliberately disabled; correctness is kept.

    The window still selects exactly the same frames, but the planner may no
    longer skip a segment on timestamp bounds, so the candidate set equals the
    registered set. The scan grows; the answer does not change.
    """
    root, session_id, _seconds = smoke_project
    service = QueryService(root)
    frame_filter = FrameFilter(
        session_id=session_id,
        normalized_timestamp_start=NARROW_START / 1_000.0,
        normalized_timestamp_end=NARROW_END / 1_000.0,
    )

    plan = service.plan_frames(frame_filter)
    summary = service.summarize_frames(frame_filter)
    page = service.query_frames(FrameQuery(filter=frame_filter, limit=1_000))

    assert plan.registered_segment_count == SEGMENT_COUNT
    assert plan.candidate_segment_count == SEGMENT_COUNT
    assert plan.pruned_segment_count == 0
    assert summary.matching_frame_count == NARROW_END - NARROW_START + 1
    assert summary.first_sequence == NARROW_START
    assert summary.last_sequence == NARROW_END
    assert [item.sequence for item in page.frames] == list(
        range(NARROW_START, NARROW_END + 1)
    )


def test_arbitration_counts_stay_bounded_on_the_smoke_volume(
    smoke_project: tuple[Path, str, float],
) -> None:
    root, session_id, _seconds = smoke_project
    service = QueryService(root)

    counts = service.count_by_arbitration_id(
        FrameFilter(session_id=session_id), top_n=3
    )

    assert len(counts) == 3
    assert all(row.frame_count == TOTAL_FRAMES // DISTINCT_IDS for row in counts)
    assert [row.arbitration_id for row in counts] == [0x100, 0x101, 0x102]


def test_the_smoke_volume_is_integrity_clean(
    smoke_project: tuple[Path, str, float],
) -> None:
    root, session_id, _seconds = smoke_project
    service = DataSessionService(root)
    session = service.get_session(session_id)
    segments = service.list_segments(session_id)

    assert session.frame_count == TOTAL_FRAMES
    assert session.segment_count == SEGMENT_COUNT
    assert len(segments) == SEGMENT_COUNT
    assert sum(segment.frame_count for segment in segments) == TOTAL_FRAMES
    assert service.inspect_integrity().clean is True
