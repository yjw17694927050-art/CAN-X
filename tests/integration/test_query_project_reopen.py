"""Historical query after a project reopen and after the project directory moves.

Persisted segments are the durable asset; a query must reach them through a fresh
handle and after the whole project directory is relocated, because every stored
path is project-relative.
"""

import shutil
from pathlib import Path

from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.query.model import FrameFilter, FrameQuery
from canx.query.service import QueryService

STREAM_ID = "reopen-stream"
FRAMES_PER_SEGMENT = 6
TOTAL_FRAMES = 36


def frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can1",
        arbitration_id=0x500 + (sequence % 3),
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=2,
        data=bytes([sequence % 256, 0x5A]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=5000.0 + sequence,
        normalized_timestamp=sequence / 2.0,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def _write(handle: ProjectHandle) -> str:
    service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
    writer = service.start(stream_id=STREAM_ID)
    for start in range(0, TOTAL_FRAMES, FRAMES_PER_SEGMENT):
        writer.append(
            FrameBatch.create(
                stream_id=STREAM_ID,
                frames=[
                    frame(sequence)
                    for sequence in range(start, start + FRAMES_PER_SEGMENT)
                ],
            )
        )
    writer.finalize()
    return writer.session_id


def test_a_reopened_project_answers_the_same_query(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="A")
    session_id = _write(handle)
    before = QueryService(handle.root).query_frames(
        FrameQuery(filter=FrameFilter(session_id=session_id), limit=1_000)
    )
    handle.close()

    with ProjectService().open(root) as reopened:
        after = QueryService(reopened.root).query_frames(
            FrameQuery(filter=FrameFilter(session_id=session_id), limit=1_000)
        )
        summary = QueryService(reopened.root).summarize_frames(
            FrameFilter(session_id=session_id)
        )

    assert after == before
    assert [item.sequence for item in after.frames] == list(range(TOTAL_FRAMES))
    assert summary.matching_frame_count == TOTAL_FRAMES


def test_a_moved_project_answers_through_its_new_root(tmp_path: Path) -> None:
    original = tmp_path / "original" / "vehicle.canx"
    original.parent.mkdir()
    handle = ProjectService().create(original, display_name="A")
    session_id = _write(handle)
    handle.close()

    moved = tmp_path / "moved" / "vehicle.canx"
    moved.parent.mkdir()
    shutil.copytree(original, moved)
    shutil.rmtree(original)

    with ProjectService().open(moved) as reopened:
        service = QueryService(reopened.root)
        page = service.query_frames(
            FrameQuery(filter=FrameFilter(session_id=session_id), limit=1_000)
        )
        plan = service.plan_frames(FrameFilter(session_id=session_id, sequence_start=12))

    assert [item.sequence for item in page.frames] == list(range(TOTAL_FRAMES))
    assert plan.registered_segment_count == TOTAL_FRAMES // FRAMES_PER_SEGMENT
    assert plan.candidate_segment_count == 4


def test_repeated_reopens_stay_consistent(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="A")
    session_id = _write(handle)
    handle.close()

    summaries = []
    for _ in range(3):
        with ProjectService().open(root) as reopened:
            summaries.append(
                QueryService(reopened.root).summarize_frames(
                    FrameFilter(session_id=session_id)
                )
            )

    assert summaries[0] == summaries[1] == summaries[2]
    assert summaries[0].matching_frame_count == TOTAL_FRAMES


def test_queries_do_not_block_removing_the_project_directory(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="A")
    session_id = _write(handle)
    service = QueryService(handle.root)
    service.query_frames(FrameQuery(filter=FrameFilter(session_id=session_id)))
    service.summarize_frames(FrameFilter(session_id=session_id))
    service.count_by_arbitration_id(FrameFilter(session_id=session_id))
    handle.close()

    shutil.rmtree(root)

    assert not root.exists()


def test_a_reopened_project_queries_a_multi_page_window(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="A")
    session_id = _write(handle)
    handle.close()

    with ProjectService().open(root) as reopened:
        service = QueryService(reopened.root)
        frame_filter = FrameFilter(session_id=session_id, sequence_start=7, sequence_end=29)
        collected = []
        cursor = None
        while True:
            page = service.query_frames(
                FrameQuery(filter=frame_filter, after_sequence=cursor, limit=7)
            )
            collected.extend(item.sequence for item in page.frames)
            if not page.has_more:
                break
            cursor = page.next_after_sequence

    assert collected == list(range(7, 30))
