"""Behavior tests for the query service: snapshot, planning, validation, results.

The service is where the pieces meet, so these tests assert the observable
contract rather than any internal call: a query sees exactly the committed frames
of one session, an unrelated segment is never opened, and every broken persisted
fact fails with a typed query error instead of a plausible partial result.
"""

import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from canx.data.parquet import segment_filename
from canx.data.schema import frames_to_table
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.project.storage import DATABASE_FILENAME
from canx.query.engine import QueryEngine
from canx.query.errors import (
    QueryDataUnavailableError,
    QueryError,
    QueryIntegrityError,
    QuerySessionError,
    QueryValidationError,
)
from canx.query.model import (
    ArbitrationIdCount,
    FrameFilter,
    FrameQuery,
    FrameQueryPage,
    FrameQuerySummary,
)
from canx.query.service import QueryService

STREAM_ID = "service-stream"
FRAMES_PER_SEGMENT = 10
OTHER_SESSION_ID = "11111111-2222-4333-8444-555555555555"
UNKNOWN_SESSION_ID = "99999999-8888-4777-8666-555555555555"


def frame(sequence: int, **changes: Any) -> Frame:
    values: dict[str, Any] = {
        "sequence": sequence,
        "channel_id": "can0",
        "arbitration_id": 0x200 + (sequence % 2),
        "is_extended": False,
        "is_fd": False,
        "bitrate_switch": False,
        "error_state_indicator": False,
        "dlc": 2,
        "data": bytes([sequence % 256, 0x77]),
        "direction": Direction.RX,
        "hardware_timestamp": None,
        "host_timestamp": 1_000.0 + sequence,
        "normalized_timestamp": float(sequence),
        "clock_domain": "host.monotonic",
        "timestamp_quality": TimestampQuality.HOST,
        "flags": 0,
    }
    values.update(changes)
    return Frame(**values)


def _project(tmp_path: Path) -> ProjectHandle:
    return ProjectService().create(tmp_path / "vehicle.canx", display_name="Vehicle A")


def _write_session(
    handle: ProjectHandle,
    *,
    total: int,
    stream_id: str = STREAM_ID,
    frames_per_segment: int = FRAMES_PER_SEGMENT,
    finalize: bool = True,
) -> str:
    service = DataSessionService(handle.root, max_frames_per_segment=frames_per_segment)
    writer = service.start(stream_id=stream_id)
    for start in range(0, total, frames_per_segment):
        size = min(frames_per_segment, total - start)
        writer.append(
            FrameBatch.create(
                stream_id=stream_id,
                frames=[frame(sequence) for sequence in range(start, start + size)],
            )
        )
    if finalize:
        writer.finalize()
    return writer.session_id


def _mutate_segment(root: Path, session_id: str, segment_index: int, **columns: object) -> None:
    connection = sqlite3.connect(str(root / DATABASE_FILENAME))
    try:
        assignments = ", ".join(f"{name} = ?" for name in columns)
        connection.execute(
            f"UPDATE data_segments SET {assignments}"
            " WHERE session_id = ? AND segment_index = ?",
            (*columns.values(), session_id, segment_index),
        )
        connection.commit()
    finally:
        connection.close()


def _overwrite_segment(
    path: Path,
    frames: list[Frame],
    *,
    session_id: str = "",
    stream_id: str = STREAM_ID,
    segment_index: int = 0,
    schema_version: int = 1,
) -> None:
    table = frames_to_table(
        frames, session_id=session_id, stream_id=stream_id, segment_index=segment_index
    )
    if schema_version != 1:
        metadata = dict(table.schema.metadata or {})
        metadata[b"canx.schema_version"] = str(schema_version).encode()
        table = table.replace_schema_metadata(metadata)
    pq.write_table(table, path)


def test_a_query_returns_the_committed_frames_of_one_session(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        session_id = _write_session(handle, total=25)
        service = QueryService(handle.root)

        page = service.query_frames(
            FrameQuery(filter=FrameFilter(session_id=session_id), limit=1_000)
        )

    assert [item.sequence for item in page.frames] == list(range(25))
    assert page.has_more is False


def test_a_query_only_sees_its_own_session(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        first = _write_session(handle, total=12, stream_id="stream-a")
        second = _write_session(handle, total=7, stream_id="stream-b")
        service = QueryService(handle.root)

        first_page = service.query_frames(FrameQuery(filter=FrameFilter(session_id=first)))
        second_page = service.query_frames(FrameQuery(filter=FrameFilter(session_id=second)))

    assert [item.sequence for item in first_page.frames] == list(range(12))
    assert [item.sequence for item in second_page.frames] == list(range(7))


def test_pages_concatenate_into_the_single_query_result(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        session_id = _write_session(handle, total=23)
        service = QueryService(handle.root)
        frame_filter = FrameFilter(session_id=session_id)

        whole = service.query_frames(FrameQuery(filter=frame_filter, limit=1_000))
        collected = []
        cursor = None
        while True:
            page = service.query_frames(
                FrameQuery(filter=frame_filter, after_sequence=cursor, limit=5)
            )
            collected.extend(page.frames)
            if not page.has_more:
                break
            cursor = page.next_after_sequence

    assert tuple(collected) == whole.frames
    assert [item.sequence for item in collected] == list(range(23))


def test_an_empty_session_queries_cleanly(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        session_id = _write_session(handle, total=0)
        service = QueryService(handle.root)

        page = service.query_frames(FrameQuery(filter=FrameFilter(session_id=session_id)))
        summary = service.summarize_frames(FrameFilter(session_id=session_id))
        counts = service.count_by_arbitration_id(FrameFilter(session_id=session_id))

    assert page.frames == ()
    assert page.has_more is False
    assert page.next_after_sequence is None
    assert summary == FrameQuerySummary(
        session_id=session_id,
        matching_frame_count=0,
        first_sequence=None,
        last_sequence=None,
        first_normalized_timestamp=None,
        last_normalized_timestamp=None,
    )
    assert counts == ()


def test_a_summary_reports_the_bounds_of_the_matching_frames(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        session_id = _write_session(handle, total=25)
        service = QueryService(handle.root)

        summary = service.summarize_frames(
            FrameFilter(session_id=session_id, sequence_start=5, sequence_end=20)
        )

    assert summary.matching_frame_count == 16
    assert summary.first_sequence == 5
    assert summary.last_sequence == 20


def test_arbitration_counts_are_ordered_by_count_then_id(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        session_id = _write_session(handle, total=12)
        service = QueryService(handle.root)

        counts = service.count_by_arbitration_id(
            FrameFilter(session_id=session_id), top_n=1
        )

    assert counts == (
        ArbitrationIdCount(arbitration_id=0x200, is_extended=False, frame_count=6),
    )


def test_an_unregistered_session_is_refused(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        _write_session(handle, total=5)
        service = QueryService(handle.root)

        with pytest.raises(QuerySessionError) as info:
            service.query_frames(
                FrameQuery(filter=FrameFilter(session_id=UNKNOWN_SESSION_ID))
            )

    assert info.value.code == "query.session_not_found"
    assert info.value.details["session_id"] == UNKNOWN_SESSION_ID


def test_a_project_that_is_not_a_canx_project_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "not-a-project.canx"
    empty.mkdir()

    with pytest.raises(QuerySessionError) as info:
        QueryService(empty).summarize_frames(FrameFilter(session_id=UNKNOWN_SESSION_ID))

    assert info.value.code == "query.project_unavailable"


@pytest.mark.parametrize(
    "argument",
    ["not-a-query", None, dict],
    ids=["string", "none", "type"],
)
def test_a_non_query_argument_is_refused(tmp_path: Path, argument: object) -> None:
    with _project(tmp_path) as handle, pytest.raises(QueryValidationError):
        QueryService(handle.root).query_frames(argument)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "top_n", [0, -1, True, 1.5, 1_001], ids=["zero", "negative", "bool", "float", "above-max"]
)
def test_an_invalid_top_n_is_refused(tmp_path: Path, top_n: object) -> None:
    with _project(tmp_path) as handle:
        session_id = _write_session(handle, total=3)
        service = QueryService(handle.root)

        with pytest.raises(QueryValidationError):
            service.count_by_arbitration_id(
                FrameFilter(session_id=session_id), top_n=top_n  # type: ignore[arg-type]
            )


def test_a_missing_segment_file_fails_typed(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        session_id = _write_session(handle, total=25)
        service = QueryService(handle.root)
        victim = service.plan_frames(FrameFilter(session_id=session_id)).relative_paths[1]
        (handle.root / victim).unlink()

        with pytest.raises(QueryDataUnavailableError) as info:
            service.query_frames(FrameQuery(filter=FrameFilter(session_id=session_id)))

    assert info.value.code == "query.segment_missing"
    assert info.value.recoverable is True
    assert info.value.details["relative_path"] == victim
    assert info.value.details["session_id"] == session_id
    assert info.value.details["segment_index"] == 1


def test_a_foreign_parquet_file_fails_typed(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        session_id = _write_session(handle, total=25)
        victim = (
            handle.root
            / "data"
            / "sessions"
            / session_id
            / "segments"
            / segment_filename(1)
        )
        pq.write_table(
            pa.table({"value": pa.array([1, 2, 3], type=pa.int64())}), victim
        )

        with pytest.raises(QueryIntegrityError) as info:
            QueryService(handle.root).query_frames(
                FrameQuery(filter=FrameFilter(session_id=session_id))
            )

    assert info.value.code == "query.segment_invalid"
    assert info.value.details["segment_index"] == 1


def test_a_corrupt_parquet_file_fails_typed(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        session_id = _write_session(handle, total=25)
        victim = (
            handle.root
            / "data"
            / "sessions"
            / session_id
            / "segments"
            / segment_filename(0)
        )
        victim.write_bytes(b"this is not a parquet file")

        with pytest.raises(QueryIntegrityError) as info:
            QueryService(handle.root).summarize_frames(FrameFilter(session_id=session_id))

    assert info.value.code == "query.segment_unreadable"


def test_a_segment_from_another_session_fails_typed(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        session_id = _write_session(handle, total=25)
        victim = (
            handle.root
            / "data"
            / "sessions"
            / session_id
            / "segments"
            / segment_filename(0)
        )
        _overwrite_segment(
            victim,
            [frame(sequence) for sequence in range(3)],
            session_id=OTHER_SESSION_ID,
            segment_index=0,
        )

        with pytest.raises(QueryIntegrityError) as info:
            QueryService(handle.root).query_frames(
                FrameQuery(filter=FrameFilter(session_id=session_id))
            )

    assert info.value.code == "query.segment_invalid"
    assert info.value.details["cause"] == "data.integrity.metadata_mismatch"


def test_a_segment_with_the_wrong_registered_index_fails_typed(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        session_id = _write_session(handle, total=25)
        victim = (
            handle.root
            / "data"
            / "sessions"
            / session_id
            / "segments"
            / segment_filename(1)
        )
        _overwrite_segment(
            victim,
            [frame(sequence) for sequence in range(10, 20)],
            session_id=session_id,
            segment_index=9,
        )

        with pytest.raises(QueryIntegrityError) as info:
            QueryService(handle.root).query_frames(
                FrameQuery(filter=FrameFilter(session_id=session_id))
            )

    assert info.value.code == "query.segment_invalid"


def test_an_unsupported_segment_schema_version_fails_typed(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        session_id = _write_session(handle, total=25)
        victim = (
            handle.root
            / "data"
            / "sessions"
            / session_id
            / "segments"
            / segment_filename(0)
        )
        _overwrite_segment(
            victim,
            [frame(sequence) for sequence in range(5)],
            session_id=session_id,
            segment_index=0,
            schema_version=99,
        )

        with pytest.raises(QueryIntegrityError) as info:
            QueryService(handle.root).query_frames(
                FrameQuery(filter=FrameFilter(session_id=session_id))
            )

    assert info.value.code == "query.segment_invalid"
    assert info.value.details["cause"] == "data.integrity.schema_version_unsupported"


def test_a_stored_path_that_escapes_the_project_fails_typed(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        session_id = _write_session(handle, total=25)
        _mutate_segment(
            handle.root,
            session_id,
            0,
            relative_path="../../outside.parquet",
        )

        with pytest.raises(QueryIntegrityError) as info:
            QueryService(handle.root).query_frames(
                FrameQuery(filter=FrameFilter(session_id=session_id))
            )

    assert info.value.code == "query.path_escape"


def test_the_candidate_paths_are_the_only_files_handed_to_the_engine(
    tmp_path: Path,
) -> None:
    """Pruning is observable: the engine never sees a pruned segment."""
    recorded: list[tuple[Path, ...]] = []

    class _RecordingEngine(QueryEngine):
        def query_frames(
            self,
            *,
            paths: tuple[Path, ...],
            frame_filter: FrameFilter,
            after_sequence: int | None,
            limit: int,
        ) -> FrameQueryPage:
            recorded.append(paths)
            return FrameQueryPage(
                session_id=frame_filter.session_id,
                frames=(),
                has_more=False,
                next_after_sequence=None,
            )

    with _project(tmp_path) as handle:
        session_id = _write_session(handle, total=50)
        service = QueryService(handle.root, engine=_RecordingEngine())

        service.query_frames(
            FrameQuery(
                filter=FrameFilter(
                    session_id=session_id, sequence_start=12, sequence_end=21
                )
            )
        )

    assert len(recorded) == 1
    names = [path.name for path in recorded[0]]
    assert names == [segment_filename(1), segment_filename(2)]


def test_the_plan_reports_pruning_without_opening_a_segment(tmp_path: Path) -> None:
    with _project(tmp_path) as handle:
        session_id = _write_session(handle, total=50)
        service = QueryService(handle.root)

        plan = service.plan_frames(
            FrameFilter(session_id=session_id, sequence_start=12, sequence_end=21)
        )

    assert plan.registered_segment_count == 5
    assert plan.candidate_segment_count == 2
    assert plan.pruned_segment_count == 3


def test_a_query_releases_the_project_directory(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Vehicle A")
    session_id = _write_session(handle, total=25)
    service = QueryService(handle.root)
    service.query_frames(FrameQuery(filter=FrameFilter(session_id=session_id)))
    service.summarize_frames(FrameFilter(session_id=session_id))
    service.count_by_arbitration_id(FrameFilter(session_id=session_id))
    handle.close()

    shutil.rmtree(root)

    assert not root.exists()


def test_every_query_failure_is_a_query_error(tmp_path: Path) -> None:
    """No engine, storage or filesystem error escapes the query boundary."""
    with _project(tmp_path) as handle:
        _write_session(handle, total=5)
        service = QueryService(handle.root)

        with pytest.raises(QueryError) as info:
            service.query_frames(
                FrameQuery(filter=FrameFilter(session_id=UNKNOWN_SESSION_ID))
            )

    assert not isinstance(info.value, (OSError, ValueError, sqlite3.Error))
