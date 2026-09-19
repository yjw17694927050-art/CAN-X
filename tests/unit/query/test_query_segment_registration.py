"""A query refuses a disagreeing registration before the engine can read a byte.

``QueryService`` prunes and validates its candidate files, and the validation now
covers the same registered claims the DataSession read path covers — including
the two the query path used to ignore: the row count and the byte size. This file
locks the observable consequence: a segment whose file disagrees with its
registered metadata is never handed to the engine at all, while a healthy one is.
"""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from canx.data.parquet import segment_filename
from canx.data.schema import frames_to_table
from canx.data.session import DataSessionService, segment_relative_path
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.project.storage import DATABASE_FILENAME
from canx.query.engine import QueryEngine
from canx.query.errors import QueryIntegrityError
from canx.query.model import FrameFilter, FrameQuery, FrameQueryPage
from canx.query.service import QueryService

STREAM_ID = "query-registration-stream"
FRAMES_PER_SEGMENT = 3
TOTAL_FRAMES = 6


class _RecordingEngine(QueryEngine):
    """A QueryEngine that records the candidate paths it was handed."""

    def __init__(self) -> None:
        self.seen: list[tuple[Path, ...]] = []

    def query_frames(
        self,
        *,
        paths: tuple[Path, ...],
        frame_filter: FrameFilter,
        after_sequence: int | None,
        limit: int,
    ) -> FrameQueryPage:
        self.seen.append(paths)
        return super().query_frames(
            paths=paths, frame_filter=frame_filter, after_sequence=after_sequence, limit=limit
        )


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
        data=bytes([sequence % 256, 0x10]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=100.0 + sequence,
        normalized_timestamp=float(sequence),
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def project(tmp_path: Path) -> ProjectHandle:
    return ProjectService().create(tmp_path / "vehicle.canx", display_name="Vehicle A")


def write_session(handle: ProjectHandle) -> str:
    service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
    writer = service.start(stream_id=STREAM_ID)
    for start in range(0, TOTAL_FRAMES, FRAMES_PER_SEGMENT):
        writer.append(
            FrameBatch.create(
                stream_id=STREAM_ID,
                frames=[frame(sequence) for sequence in range(start, start + FRAMES_PER_SEGMENT)],
            )
        )
    writer.finalize()
    return writer.session_id


def mutate_segment_row(root: Path, session_id: str, segment_index: int, **columns: object) -> None:
    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        assignments = ", ".join(f"{name} = ?" for name in columns)
        connection.execute(
            f"UPDATE data_segments SET {assignments} WHERE session_id = ? AND segment_index = ?",
            (*columns.values(), session_id, segment_index),
        )
        connection.commit()
    finally:
        connection.close()


def _shrink_the_file(root: Path, session_id: str) -> str:
    """Rewrite segment 0 with one frame instead of the three that are registered."""
    path = root / segment_relative_path(session_id, 0)
    pq.write_table(
        frames_to_table([frame(0)], session_id=session_id, stream_id=STREAM_ID, segment_index=0),
        path,
    )
    return "data.integrity.frame_count_mismatch"


def _lie_about_the_size(root: Path, session_id: str) -> str:
    registered = DataSessionService(root).list_segments(session_id)[0]
    mutate_segment_row(root, session_id, 0, byte_size=registered.byte_size + 1)
    return "data.integrity.byte_size_mismatch"


def _point_at_a_non_canonical_path(root: Path, session_id: str) -> str:
    canonical = root / segment_relative_path(session_id, 0)
    displaced = canonical.with_name("displaced.parquet")
    displaced.write_bytes(canonical.read_bytes())
    mutate_segment_row(
        root,
        session_id,
        0,
        relative_path=segment_relative_path(session_id, 0).replace(
            segment_filename(0), "displaced.parquet"
        ),
    )
    return "data.integrity.registered_path_mismatch"


CORRUPTIONS: dict[str, Callable[[Path, str], str]] = {
    "row-count": _shrink_the_file,
    "byte-size": _lie_about_the_size,
    "non-canonical-path": _point_at_a_non_canonical_path,
}


@pytest.mark.parametrize("corruption", sorted(CORRUPTIONS))
def test_a_disagreeing_registration_never_reaches_the_engine(
    tmp_path: Path, corruption: str
) -> None:
    with project(tmp_path) as handle:
        session_id = write_session(handle)
        expected_code = CORRUPTIONS[corruption](handle.root, session_id)
        engine = _RecordingEngine()

        with pytest.raises(QueryIntegrityError) as info:
            QueryService(handle.root, engine=engine).query_frames(
                FrameQuery(filter=FrameFilter(session_id=session_id))
            )

        assert info.value.code == "query.segment_invalid"
        assert info.value.details["cause"] == expected_code
        assert engine.seen == []


def test_a_healthy_registration_reaches_the_engine_with_its_canonical_paths(
    tmp_path: Path,
) -> None:
    """The control: validation refuses disagreement, not a normal session."""
    with project(tmp_path) as handle:
        session_id = write_session(handle)
        engine = _RecordingEngine()

        page = QueryService(handle.root, engine=engine).query_frames(
            FrameQuery(filter=FrameFilter(session_id=session_id))
        )

        assert [item.sequence for item in page.frames] == list(range(TOTAL_FRAMES))
        assert len(engine.seen) == 1
        assert engine.seen[0] == (
            handle.root / segment_relative_path(session_id, 0),
            handle.root / segment_relative_path(session_id, 1),
        )
