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
from canx.query.model import MAX_QUERY_ROWS, FrameFilter, FrameQuery
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

# --- V0.3-01: CAN id range, mask and combination on the same 100k volume --------
#
# These assertions reuse the module fixture, so the 100,000 frames are written
# once for the whole module. Their expected counts are derived from the fixture's
# own id formula rather than from a second query, so the engine cannot confirm
# itself. Timings are still informational only — nothing here is a threshold.

#: The canonical ids the fixture cycles through.
CANONICAL_IDS = tuple(0x100 + offset for offset in range(DISTINCT_IDS))
#: ``0x100`` has bit 2 clear, so masking the canonical ids with ``0x04`` compares
#: bit 2 of the fixture's own cycle offset: the four ids ``0x104``-``0x107`` match
#: and the other four do not — exactly half the volume.
MASK_LOW_BITS = 0x04
MASK_LOW_BITS_VALUE = 0x04


def _expected_id_count(predicate: object) -> int:
    """Count frames the fixture's id formula will produce, independently."""
    matching = 0
    for sequence in range(TOTAL_FRAMES):
        arbitration_id = 0x100 + (sequence % DISTINCT_IDS)
        if predicate(arbitration_id):  # type: ignore[operator]
            matching += 1
    return matching


def test_a_full_page_walk_reconstructs_every_frame_exactly_once(
    smoke_project: tuple[Path, str, float],
) -> None:
    """The core scale claim: 100,000 frames paged, in order, with nothing lost.

    A filtered or unfiltered walk must concatenate into the single-query result —
    no duplicate, no gap, strictly increasing order — and the cursor must be the
    last returned sequence rather than a row count.
    """
    root, session_id, _seconds = smoke_project
    service = QueryService(root)
    frame_filter = FrameFilter(session_id=session_id)

    collected: list[int] = []
    cursors: list[int] = []
    pages = 0
    cursor = None
    started = time.perf_counter()
    while True:
        page = service.query_frames(
            FrameQuery(filter=frame_filter, after_sequence=cursor, limit=MAX_QUERY_ROWS)
        )
        pages += 1
        collected.extend(item.sequence for item in page.frames)
        if not page.has_more:
            assert page.next_after_sequence is None
            break
        assert page.next_after_sequence == page.frames[-1].sequence
        cursors.append(page.next_after_sequence)
        cursor = page.next_after_sequence
    elapsed = time.perf_counter() - started

    _write_report(
        total_frames=TOTAL_FRAMES,
        segments=SEGMENT_COUNT,
        filter="unbounded",
        page_size=MAX_QUERY_ROWS,
        page_count=pages,
        returned_rows=len(collected),
        full_walk_seconds=round(elapsed, 3),
        timing="informational only, not a gate",
    )

    assert pages == TOTAL_FRAMES // MAX_QUERY_ROWS == 10
    assert collected == list(range(TOTAL_FRAMES))
    assert len(set(collected)) == TOTAL_FRAMES
    assert cursors == sorted(cursors) and len(set(cursors)) == len(cursors)


def test_an_id_range_covering_the_whole_id_space_returns_every_frame(
    smoke_project: tuple[Path, str, float],
) -> None:
    root, session_id, _seconds = smoke_project
    service = QueryService(root)
    frame_filter = FrameFilter(
        session_id=session_id,
        arbitration_id_start=CANONICAL_IDS[0],
        arbitration_id_end=CANONICAL_IDS[-1],
    )

    plan = service.plan_frames(frame_filter)
    summary = service.summarize_frames(frame_filter)

    assert summary.matching_frame_count == TOTAL_FRAMES
    assert summary.first_sequence == 0
    assert summary.last_sequence == TOTAL_FRAMES - 1
    # An id filter carries no segment metadata to prune on, so every registered
    # segment stays a candidate — correctness before pruning.
    assert plan.candidate_segment_count == SEGMENT_COUNT


def test_an_id_range_narrower_than_the_id_space_is_exact(
    smoke_project: tuple[Path, str, float],
) -> None:
    root, session_id, _seconds = smoke_project
    service = QueryService(root)
    frame_filter = FrameFilter(
        session_id=session_id, arbitration_id_start=0x101, arbitration_id_end=0x103
    )

    summary = service.summarize_frames(frame_filter)
    page = service.query_frames(FrameQuery(filter=frame_filter, limit=1_000))
    expected = _expected_id_count(lambda arbitration_id: 0x101 <= arbitration_id <= 0x103)

    _write_report(
        total_frames=TOTAL_FRAMES,
        filter="arbitration_id 0x101-0x103",
        matched_rows=summary.matching_frame_count,
        expected_rows=expected,
        result="bounded range",
        timing="informational only, not a gate",
    )

    assert expected == TOTAL_FRAMES * 3 // DISTINCT_IDS
    assert summary.matching_frame_count == expected
    assert len(page.frames) == 1_000
    assert page.has_more is True
    assert all(0x101 <= item.arbitration_id <= 0x103 for item in page.frames)


def test_an_id_range_that_matches_no_frame_is_empty_at_scale(
    smoke_project: tuple[Path, str, float],
) -> None:
    root, session_id, _seconds = smoke_project
    service = QueryService(root)
    frame_filter = FrameFilter(
        session_id=session_id, arbitration_id_start=0x200, arbitration_id_end=0x2FF
    )

    page = service.query_frames(FrameQuery(filter=frame_filter, limit=1_000))
    summary = service.summarize_frames(frame_filter)

    assert page.frames == ()
    assert page.has_more is False
    assert page.next_after_sequence is None
    assert summary.matching_frame_count == 0
    assert summary.first_sequence is None


def test_an_id_mask_selects_the_low_bit_group_exactly(
    smoke_project: tuple[Path, str, float],
) -> None:
    """``(id & 0x06) == (0x104 & 0x06)`` must hold for every returned frame."""
    root, session_id, _seconds = smoke_project
    service = QueryService(root)
    frame_filter = FrameFilter(
        session_id=session_id,
        arbitration_id_mask=MASK_LOW_BITS,
        arbitration_id_mask_value=MASK_LOW_BITS_VALUE,
    )

    summary = service.summarize_frames(frame_filter)
    page = service.query_frames(FrameQuery(filter=frame_filter, limit=1_000))
    expected = _expected_id_count(
        lambda arbitration_id: (arbitration_id & MASK_LOW_BITS)
        == (MASK_LOW_BITS_VALUE & MASK_LOW_BITS)
    )

    _write_report(
        total_frames=TOTAL_FRAMES,
        filter=f"mask 0x{MASK_LOW_BITS:X}/value 0x{MASK_LOW_BITS_VALUE:X}",
        matched_rows=summary.matching_frame_count,
        expected_rows=expected,
        result="bounded masked",
        timing="informational only, not a gate",
    )

    assert expected == TOTAL_FRAMES // 2
    assert summary.matching_frame_count == expected
    assert page.has_more is True
    assert all(
        (item.arbitration_id & MASK_LOW_BITS)
        == (MASK_LOW_BITS_VALUE & MASK_LOW_BITS)
        for item in page.frames
    )


def test_a_channel_filter_is_applied_on_the_smoke_volume(
    smoke_project: tuple[Path, str, float],
) -> None:
    """The fixture writes one channel, so the axis must match all or nothing."""
    root, session_id, _seconds = smoke_project
    service = QueryService(root)

    present = service.summarize_frames(FrameFilter(session_id=session_id, channel_ids=("can0",)))
    absent = service.summarize_frames(FrameFilter(session_id=session_id, channel_ids=("can9",)))

    assert present.matching_frame_count == TOTAL_FRAMES
    assert absent.matching_frame_count == 0


def test_a_direction_filter_is_applied_on_the_smoke_volume(
    smoke_project: tuple[Path, str, float],
) -> None:
    root, session_id, _seconds = smoke_project
    service = QueryService(root)

    rx = service.summarize_frames(
        FrameFilter(session_id=session_id, directions=(Direction.RX,))
    )
    both = service.summarize_frames(
        FrameFilter(session_id=session_id, directions=(Direction.RX, Direction.TX))
    )

    assert rx.matching_frame_count == TOTAL_FRAMES // 2
    assert both.matching_frame_count == TOTAL_FRAMES


def test_a_combined_id_channel_direction_and_sequence_filter_is_and_ed(
    smoke_project: tuple[Path, str, float],
) -> None:
    """Four axes at once, with the expectation derived from the fixture formula."""
    root, session_id, _seconds = smoke_project
    service = QueryService(root)
    frame_filter = FrameFilter(
        session_id=session_id,
        arbitration_id_start=CANONICAL_IDS[0],
        arbitration_id_end=CANONICAL_IDS[-1],
        arbitration_id_mask=MASK_LOW_BITS,
        arbitration_id_mask_value=MASK_LOW_BITS_VALUE,
        channel_ids=("can0",),
        directions=(Direction.RX,),
        sequence_start=0,
        sequence_end=999,
    )

    page = service.query_frames(FrameQuery(filter=frame_filter, limit=MAX_QUERY_ROWS))
    summary = service.summarize_frames(frame_filter)
    expected = [
        sequence
        for sequence in range(1_000)
        if ((0x100 + sequence % DISTINCT_IDS) & MASK_LOW_BITS)
        == (MASK_LOW_BITS_VALUE & MASK_LOW_BITS)
        and sequence % 2 == 0
    ]

    _write_report(
        total_frames=TOTAL_FRAMES,
        filter="range + mask + channel + direction + sequence",
        matched_rows=summary.matching_frame_count,
        expected_rows=len(expected),
        result="bounded combined",
        timing="informational only, not a gate",
    )

    assert [item.sequence for item in page.frames] == expected
    assert summary.matching_frame_count == len(expected)
    assert len(expected) == 250


def test_a_filtered_page_walk_on_the_smoke_volume_is_lossless(
    smoke_project: tuple[Path, str, float],
) -> None:
    """A non-contiguous 100k-scale result set must page without loss or overlap."""
    root, session_id, _seconds = smoke_project
    service = QueryService(root)
    frame_filter = FrameFilter(
        session_id=session_id,
        arbitration_id_mask=MASK_LOW_BITS,
        arbitration_id_mask_value=MASK_LOW_BITS_VALUE,
    )

    single = service.query_frames(FrameQuery(filter=frame_filter, limit=MAX_QUERY_ROWS))
    collected: list[int] = []
    cursor = None
    pages = 0
    while True:
        page = service.query_frames(
            FrameQuery(filter=frame_filter, after_sequence=cursor, limit=MAX_QUERY_ROWS)
        )
        pages += 1
        collected.extend(item.sequence for item in page.frames)
        if not page.has_more:
            break
        cursor = page.next_after_sequence

        assert page.next_after_sequence == page.frames[-1].sequence

    assert len(single.frames) == MAX_QUERY_ROWS
    assert pages == 5
    assert collected == sorted(set(collected))
    assert len(collected) == TOTAL_FRAMES // 2
    # Every first page of a filtered walk must match the single-query head.
    assert collected[:MAX_QUERY_ROWS] == [item.sequence for item in single.frames]


def test_the_smoke_volume_reports_a_consistent_summary_under_an_id_filter(
    smoke_project: tuple[Path, str, float],
) -> None:
    root, session_id, _seconds = smoke_project
    service = QueryService(root)
    frame_filter = FrameFilter(
        session_id=session_id, arbitration_ids=(SELECTIVE_ID,)
    )

    summary = service.summarize_frames(frame_filter)

    assert summary.matching_frame_count == TOTAL_FRAMES // DISTINCT_IDS
    assert summary.first_sequence == 3
    assert summary.last_sequence == TOTAL_FRAMES - 5
    assert summary.first_normalized_timestamp == pytest.approx(3 / 1_000.0)
