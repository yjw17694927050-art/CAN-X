"""Query semantics against ACTIVE, INTERRUPTED and FAILED data sessions.

Query works on committed Parquet segments, not on session lifecycle state. Two
consequences are pinned here: frames still buffered in a live writer are not
visible, and a session that was interrupted by a crash or marked failed still
serves every segment it durably committed.
"""

import sqlite3
from pathlib import Path

from canx.data.model import DataSessionState
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectService
from canx.project.storage import DATABASE_FILENAME
from canx.query.model import FrameFilter, FrameQuery
from canx.query.service import QueryService

STREAM_ID = "lifecycle-stream"
MAX_FRAMES_PER_SEGMENT = 4


def frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x400,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=1,
        data=bytes([sequence % 256]),
        direction=Direction.TX,
        hardware_timestamp=None,
        host_timestamp=4000.0 + sequence,
        normalized_timestamp=float(sequence),
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def batch(start: int, count: int) -> FrameBatch:
    return FrameBatch.create(
        stream_id=STREAM_ID, frames=[frame(sequence) for sequence in range(start, start + count)]
    )


def _sequences(service: QueryService, session_id: str) -> list[int]:
    page = service.query_frames(
        FrameQuery(filter=FrameFilter(session_id=session_id), limit=1_000)
    )
    assert page.has_more is False
    return [item.sequence for item in page.frames]


def test_an_active_session_exposes_its_committed_frames_only(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        sessions = DataSessionService(
            handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT
        )
        writer = sessions.start(stream_id=STREAM_ID)
        writer.append(batch(0, 4))
        writer.append(batch(4, 2))  # still buffered

        assert sessions.get_session(writer.session_id).state is DataSessionState.ACTIVE
        assert _sequences(QueryService(handle.root), writer.session_id) == [0, 1, 2, 3]


def test_a_later_commit_becomes_visible_to_the_next_query(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        sessions = DataSessionService(
            handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT
        )
        writer = sessions.start(stream_id=STREAM_ID)
        writer.append(batch(0, 4))
        writer.append(batch(4, 2))
        service = QueryService(handle.root)
        before = _sequences(service, writer.session_id)

        writer.append(batch(6, 2))  # fills the buffer and commits segment 1
        after = _sequences(service, writer.session_id)

    assert before == [0, 1, 2, 3]
    assert after == [0, 1, 2, 3, 4, 5, 6, 7]


def test_unflushed_frames_are_never_claimed_by_a_query(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        sessions = DataSessionService(
            handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT
        )
        writer = sessions.start(stream_id=STREAM_ID)
        writer.append(batch(0, 9))  # commits 8, buffers 1

        page = QueryService(handle.root).query_frames(
            FrameQuery(filter=FrameFilter(session_id=writer.session_id), limit=1_000)
        )

    assert [item.sequence for item in page.frames] == list(range(8))


def test_an_interrupted_session_still_serves_its_committed_segments(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vehicle.canx"
    crashed = ProjectService().create(root, display_name="A")
    sessions = DataSessionService(crashed.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT)
    writer = sessions.start(stream_id=STREAM_ID)
    writer.append(batch(0, 4))
    session_id = writer.session_id
    crashed.close()
    del writer, sessions, crashed  # simulate a runtime that never finalized

    with ProjectService().open(root) as reopened:
        restored = DataSessionService(reopened.root)
        assert restored.get_session(session_id).state is DataSessionState.ACTIVE
        assert _sequences(QueryService(reopened.root), session_id) == [0, 1, 2, 3]

        assert restored.recover_incomplete_sessions() == (session_id,)

        assert restored.get_session(session_id).state is DataSessionState.INTERRUPTED
        assert _sequences(QueryService(reopened.root), session_id) == [0, 1, 2, 3]
        summary = QueryService(reopened.root).summarize_frames(
            FrameFilter(session_id=session_id)
        )
        assert summary.matching_frame_count == 4


def test_an_active_session_is_queryable_before_any_recovery(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="A")
    sessions = DataSessionService(handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT)
    writer = sessions.start(stream_id=STREAM_ID)
    writer.append(batch(0, 8))
    session_id = writer.session_id
    handle.close()

    with ProjectService().open(root) as reopened:
        assert DataSessionService(reopened.root).get_session(session_id).state is (
            DataSessionState.ACTIVE
        )
        assert _sequences(QueryService(reopened.root), session_id) == list(range(8))


def test_a_failed_session_still_serves_its_committed_segments(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="A")
    sessions = DataSessionService(handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT)
    writer = sessions.start(stream_id=STREAM_ID)
    writer.append(batch(0, 8))
    completed = writer.finalize()
    session_id = writer.session_id
    handle.close()

    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        connection.execute(
            "UPDATE data_sessions SET state = 'failed' WHERE session_id = ?", (session_id,)
        )
        connection.commit()
    finally:
        connection.close()

    with ProjectService().open(root) as reopened:
        restored = DataSessionService(reopened.root).get_session(session_id)
        assert restored.state is DataSessionState.FAILED
        assert restored.frame_count == completed.frame_count
        assert _sequences(QueryService(reopened.root), session_id) == list(range(8))


def test_a_session_with_no_committed_segments_queries_cleanly(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        sessions = DataSessionService(handle.root, max_frames_per_segment=MAX_FRAMES_PER_SEGMENT)
        writer = sessions.start(stream_id=STREAM_ID)
        writer.finalize()

        page = QueryService(handle.root).query_frames(
            FrameQuery(filter=FrameFilter(session_id=writer.session_id), limit=1_000)
        )

    assert page.frames == ()
    assert page.has_more is False
