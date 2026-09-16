"""Cursor pagination contract for bounded historical queries.

The contract is deterministic and lossless: consecutive pages must concatenate
into exactly the single-query result, with no duplicate and no missing frame. A
silent off-by-one here would corrupt every future Trace reconstruction, so the
order, the duplicates and the cursor advance are all asserted directly.
"""

from pathlib import Path

import pytest
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.query.model import FrameFilter, FrameQuery, FrameQueryPage
from canx.query.service import QueryService

STREAM_ID = "pagination-stream"
FRAMES_PER_SEGMENT = 7
TOTAL_FRAMES = 30
WINDOW_START = 5
WINDOW_END = 27
MATCHING_FRAMES = WINDOW_END - WINDOW_START + 1


def frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x300,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=1,
        data=bytes([sequence % 256]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=3000.0 + sequence,
        normalized_timestamp=float(sequence),
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def _session(handle: ProjectHandle) -> str:
    service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
    writer = service.start(stream_id=STREAM_ID)
    writer.append(
        FrameBatch.create(
            stream_id=STREAM_ID, frames=[frame(sequence) for sequence in range(TOTAL_FRAMES)]
        )
    )
    writer.finalize()
    return writer.session_id


def _filter(session_id: str) -> FrameFilter:
    return FrameFilter(
        session_id=session_id, sequence_start=WINDOW_START, sequence_end=WINDOW_END
    )


def _pages(
    service: QueryService, frame_filter: FrameFilter, *, limit: int
) -> list[FrameQueryPage]:
    pages: list[FrameQueryPage] = []
    cursor = None
    while True:
        page = service.query_frames(
            FrameQuery(filter=frame_filter, after_sequence=cursor, limit=limit)
        )
        pages.append(page)
        if not page.has_more:
            return pages
        cursor = page.next_after_sequence


def test_twenty_three_matching_frames_page_as_five_five_five_five_three(
    tmp_path: Path,
) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _session(handle)
        pages = _pages(QueryService(handle.root), _filter(session_id), limit=5)

    assert MATCHING_FRAMES == 23
    assert [len(page.frames) for page in pages] == [5, 5, 5, 5, 3]


def test_the_last_page_reports_no_further_rows(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _session(handle)
        pages = _pages(QueryService(handle.root), _filter(session_id), limit=5)

    assert [page.has_more for page in pages] == [True, True, True, True, False]
    assert pages[-1].next_after_sequence is None
    assert all(page.next_after_sequence is not None for page in pages[:-1])


def test_consecutive_pages_carry_no_duplicate_and_no_missing_frame(
    tmp_path: Path,
) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _session(handle)
        pages = _pages(QueryService(handle.root), _filter(session_id), limit=5)

    sequences = [item.sequence for page in pages for item in page.frames]

    assert sequences == list(range(WINDOW_START, WINDOW_END + 1))
    assert len(sequences) == len(set(sequences)) == MATCHING_FRAMES


def test_the_cursor_strictly_advances_and_matches_each_page_end(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _session(handle)
        pages = _pages(QueryService(handle.root), _filter(session_id), limit=5)

    cursors = [page.next_after_sequence for page in pages[:-1]]

    assert cursors == sorted(cursors)
    assert len(set(cursors)) == len(cursors)
    for page, cursor in zip(pages[:-1], cursors, strict=True):
        assert cursor == page.frames[-1].sequence


def test_paged_reads_reconstruct_the_single_query_result(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        service = QueryService(handle.root)
        session_id = _session(handle)
        frame_filter = _filter(session_id)

        single = service.query_frames(FrameQuery(filter=frame_filter, limit=1_000))
        paged = _pages(service, frame_filter, limit=5)

    assert tuple(item for page in paged for item in page.frames) == single.frames


def test_a_limit_of_one_still_terminates_and_is_lossless(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _session(handle)
        pages = _pages(QueryService(handle.root), _filter(session_id), limit=1)

    assert len(pages) == MATCHING_FRAMES
    assert [len(page.frames) for page in pages] == [1] * MATCHING_FRAMES
    assert [item.sequence for page in pages for item in page.frames] == list(
        range(WINDOW_START, WINDOW_END + 1)
    )


def test_a_limit_larger_than_the_match_returns_one_final_page(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _session(handle)
        pages = _pages(QueryService(handle.root), _filter(session_id), limit=1_000)

    assert len(pages) == 1
    assert len(pages[0].frames) == MATCHING_FRAMES
    assert pages[0].has_more is False


def test_repeating_a_page_request_is_deterministic(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        service = QueryService(handle.root)
        session_id = _session(handle)
        request = FrameQuery(filter=_filter(session_id), limit=5)

        first = service.query_frames(request)
        second = service.query_frames(request)

    assert first == second


def test_a_page_starts_strictly_after_its_cursor(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        service = QueryService(handle.root)
        session_id = _session(handle)

        page = service.query_frames(
            FrameQuery(filter=_filter(session_id), after_sequence=WINDOW_START, limit=5)
        )

    assert page.frames[0].sequence == WINDOW_START + 1
    assert all(item.sequence > WINDOW_START for item in page.frames)


@pytest.mark.parametrize("limit", [1, 3, 5, 7, 22, 23, 24])
def test_every_limit_partition_is_lossless(tmp_path: Path, limit: int) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _session(handle)
        pages = _pages(QueryService(handle.root), _filter(session_id), limit=limit)

    sequences = [item.sequence for page in pages for item in page.frames]

    assert sequences == list(range(WINDOW_START, WINDOW_END + 1))
    for page in pages[:-1]:
        assert len(page.frames) == limit
        assert page.has_more is True
    assert len(pages[-1].frames) <= limit
    assert pages[-1].has_more is False
