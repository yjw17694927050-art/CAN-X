"""Behavior tests for the DuckDB query engine.

These tests exercise the engine directly against real Parquet files: the engine
is the only component that speaks SQL, so its filters, its bound (fetch
``limit + 1``) pagination and its failure translation are checked without any
service-layer shortcuts.
"""

from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from canx.data.schema import frames_to_table
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.query import engine as engine_module
from canx.query.engine import QueryEngine
from canx.query.errors import QueryExecutionError, QueryIntegrityError
from canx.query.model import (
    ArbitrationIdCount,
    FrameFilter,
    FrameQueryPage,
    FrameQuerySummary,
)

SESSION_ID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
STREAM_ID = "engine-stream"
OTHER_SESSION_ID = "11111111-2222-4333-8444-555555555555"


def frame(sequence: int, **changes: Any) -> Frame:
    values: dict[str, Any] = {
        "sequence": sequence,
        "channel_id": "can0",
        "arbitration_id": 0x100 + (sequence % 3),
        "is_extended": False,
        "is_fd": False,
        "bitrate_switch": False,
        "error_state_indicator": False,
        "dlc": 2,
        "data": bytes([sequence % 256, 0x99]),
        "direction": Direction.RX if sequence % 2 == 0 else Direction.TX,
        "hardware_timestamp": None,
        "host_timestamp": 100.0 + sequence,
        "normalized_timestamp": sequence / 4.0,
        "clock_domain": "host.monotonic",
        "timestamp_quality": TimestampQuality.HOST,
        "flags": 0,
    }
    values.update(changes)
    return Frame(**values)


def _write_segment(
    path: Path,
    frames: list[Frame],
    *,
    session_id: str = SESSION_ID,
    stream_id: str = STREAM_ID,
    segment_index: int = 0,
) -> Path:
    table = frames_to_table(
        frames, session_id=session_id, stream_id=stream_id, segment_index=segment_index
    )
    pq.write_table(table, path)
    return path


def _data_segment(tmp_path: Path, frames: list[Frame], index: int = 0) -> Path:
    return _write_segment(tmp_path / f"{index:06d}.parquet", frames, segment_index=index)


def test_a_page_returns_frames_in_sequence_order(tmp_path: Path) -> None:
    path = _data_segment(tmp_path, [frame(sequence) for sequence in range(5)])

    page = QueryEngine().query_frames(
        paths=(path,),
        frame_filter=FrameFilter(session_id=SESSION_ID),
        after_sequence=None,
        limit=10,
    )

    assert isinstance(page, FrameQueryPage)
    assert [item.sequence for item in page.frames] == [0, 1, 2, 3, 4]
    assert page.has_more is False
    assert page.next_after_sequence is None


def test_a_page_scans_every_given_segment_in_one_query(tmp_path: Path) -> None:
    first = _data_segment(tmp_path, [frame(sequence) for sequence in range(5)], index=0)
    second = _data_segment(
        tmp_path, [frame(sequence) for sequence in range(5, 10)], index=1
    )

    page = QueryEngine().query_frames(
        paths=(first, second),
        frame_filter=FrameFilter(session_id=SESSION_ID),
        after_sequence=None,
        limit=100,
    )

    assert [item.sequence for item in page.frames] == list(range(10))


def test_an_exact_limit_page_reports_no_more_rows(tmp_path: Path) -> None:
    path = _data_segment(tmp_path, [frame(sequence) for sequence in range(4)])

    page = QueryEngine().query_frames(
        paths=(path,),
        frame_filter=FrameFilter(session_id=SESSION_ID),
        after_sequence=None,
        limit=4,
    )

    assert len(page.frames) == 4
    assert page.has_more is False
    assert page.next_after_sequence is None


def test_one_row_beyond_the_limit_sets_the_cursor(tmp_path: Path) -> None:
    path = _data_segment(tmp_path, [frame(sequence) for sequence in range(4)])

    page = QueryEngine().query_frames(
        paths=(path,),
        frame_filter=FrameFilter(session_id=SESSION_ID),
        after_sequence=None,
        limit=3,
    )

    assert [item.sequence for item in page.frames] == [0, 1, 2]
    assert page.has_more is True
    assert page.next_after_sequence == 2


def test_a_cursor_is_exclusive(tmp_path: Path) -> None:
    path = _data_segment(tmp_path, [frame(sequence) for sequence in range(5)])

    page = QueryEngine().query_frames(
        paths=(path,),
        frame_filter=FrameFilter(session_id=SESSION_ID),
        after_sequence=2,
        limit=10,
    )

    assert [item.sequence for item in page.frames] == [3, 4]


def test_a_cursor_past_the_end_returns_nothing(tmp_path: Path) -> None:
    path = _data_segment(tmp_path, [frame(sequence) for sequence in range(5)])

    page = QueryEngine().query_frames(
        paths=(path,),
        frame_filter=FrameFilter(session_id=SESSION_ID),
        after_sequence=99,
        limit=10,
    )

    assert page.frames == ()
    assert page.has_more is False


def test_an_empty_path_list_never_reaches_the_engine() -> None:
    page = QueryEngine().query_frames(
        paths=(),
        frame_filter=FrameFilter(session_id=SESSION_ID),
        after_sequence=None,
        limit=10,
    )

    assert page.frames == ()
    assert page.has_more is False


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"sequence_start": 2, "sequence_end": 3}, [2, 3]),
        ({"sequence_start": 3}, [3, 4]),
        ({"sequence_end": 1}, [0, 1]),
        ({"channel_ids": ("can1",)}, []),
        ({"arbitration_ids": (0x101,)}, [1, 4]),
        ({"directions": (Direction.TX,)}, [1, 3]),
        ({"is_extended": True}, []),
        ({"is_fd": True}, []),
        ({"normalized_timestamp_start": 0.5, "normalized_timestamp_end": 0.75}, [2, 3]),
    ],
    ids=[
        "sequence-window",
        "open-sequence-start",
        "open-sequence-end",
        "unknown-channel",
        "arbitration-id",
        "direction",
        "extended",
        "fd",
        "timestamp-window",
    ],
)
def test_every_filter_axis_selects_the_right_frames(
    tmp_path: Path, changes: dict[str, Any], expected: list[int]
) -> None:
    path = _data_segment(tmp_path, [frame(sequence) for sequence in range(5)])

    page = QueryEngine().query_frames(
        paths=(path,),
        frame_filter=FrameFilter(session_id=SESSION_ID, **changes),
        after_sequence=None,
        limit=100,
    )

    assert [item.sequence for item in page.frames] == expected


def test_filters_are_combined_with_and(tmp_path: Path) -> None:
    path = _data_segment(tmp_path, [frame(sequence) for sequence in range(10)])

    page = QueryEngine().query_frames(
        paths=(path,),
        frame_filter=FrameFilter(
            session_id=SESSION_ID,
            sequence_start=2,
            sequence_end=8,
            arbitration_ids=(0x100, 0x102),
            channel_ids=("can0",),
            directions=(Direction.RX,),
        ),
        after_sequence=None,
        limit=100,
    )

    # RX is even, and the id cycles 0x100..0x102: only 2, 6 and 8 survive all axes.
    assert [item.sequence for item in page.frames] == [2, 6, 8]


def test_a_frame_round_trips_every_canonical_field(tmp_path: Path) -> None:
    original = Frame(
        sequence=7,
        channel_id="can1",
        arbitration_id=0x1ABCDEF,
        is_extended=True,
        is_fd=True,
        bitrate_switch=True,
        error_state_indicator=True,
        dlc=64,
        data=bytes(range(64)),
        direction=Direction.TX,
        hardware_timestamp=123.5,
        host_timestamp=456.25,
        normalized_timestamp=789.125,
        clock_domain="hardware.channel1",
        timestamp_quality=TimestampQuality.HARDWARE,
        flags=0xDEADBEEF,
    )
    path = _data_segment(tmp_path, [original])

    page = QueryEngine().query_frames(
        paths=(path,),
        frame_filter=FrameFilter(session_id=SESSION_ID),
        after_sequence=None,
        limit=10,
    )

    assert page.frames == (original,)


def test_a_frame_without_a_hardware_timestamp_keeps_its_null(tmp_path: Path) -> None:
    path = _data_segment(tmp_path, [frame(0, hardware_timestamp=None)])

    page = QueryEngine().query_frames(
        paths=(path,),
        frame_filter=FrameFilter(session_id=SESSION_ID),
        after_sequence=None,
        limit=10,
    )

    assert page.frames[0].hardware_timestamp is None


def test_a_summary_is_computed_inside_the_engine(tmp_path: Path) -> None:
    path = _data_segment(tmp_path, [frame(sequence) for sequence in range(6)])

    summary = QueryEngine().summarize(
        paths=(path,), frame_filter=FrameFilter(session_id=SESSION_ID, sequence_start=2)
    )

    assert isinstance(summary, FrameQuerySummary)
    assert summary.matching_frame_count == 4
    assert summary.first_sequence == 2
    assert summary.last_sequence == 5
    assert summary.first_normalized_timestamp == 0.5
    assert summary.last_normalized_timestamp == 1.25


def test_a_summary_of_an_empty_result_reports_no_bounds(tmp_path: Path) -> None:
    path = _data_segment(tmp_path, [frame(sequence) for sequence in range(3)])

    summary = QueryEngine().summarize(
        paths=(path,), frame_filter=FrameFilter(session_id=SESSION_ID, sequence_start=99)
    )

    assert summary.matching_frame_count == 0
    assert summary.first_sequence is None
    assert summary.last_sequence is None


def test_a_summary_over_no_segments_is_empty() -> None:
    summary = QueryEngine().summarize(
        paths=(), frame_filter=FrameFilter(session_id=SESSION_ID)
    )

    assert summary.matching_frame_count == 0


def test_arbitration_counts_are_ordered_and_bounded(tmp_path: Path) -> None:
    frames = [frame(sequence) for sequence in range(9)]
    path = _data_segment(tmp_path, frames)

    counts = QueryEngine().count_by_arbitration_id(
        paths=(path,), frame_filter=FrameFilter(session_id=SESSION_ID), top_n=2
    )

    assert counts == (
        ArbitrationIdCount(arbitration_id=0x100, is_extended=False, frame_count=3),
        ArbitrationIdCount(arbitration_id=0x101, is_extended=False, frame_count=3),
    )


def test_arbitration_counts_separate_standard_from_extended(tmp_path: Path) -> None:
    path = _data_segment(
        tmp_path,
        [
            frame(0, arbitration_id=0x123, is_extended=False),
            frame(1, arbitration_id=0x123, is_extended=True),
        ],
    )

    counts = QueryEngine().count_by_arbitration_id(
        paths=(path,), frame_filter=FrameFilter(session_id=SESSION_ID), top_n=10
    )

    assert {(row.arbitration_id, row.is_extended) for row in counts} == {
        (0x123, False),
        (0x123, True),
    }


def test_arbitration_counts_over_no_segments_are_empty() -> None:
    counts = QueryEngine().count_by_arbitration_id(
        paths=(), frame_filter=FrameFilter(session_id=SESSION_ID), top_n=10
    )

    assert counts == ()


def test_a_page_with_no_match_returns_an_empty_page(tmp_path: Path) -> None:
    path = _data_segment(tmp_path, [frame(0)])

    page = QueryEngine().query_frames(
        paths=(path,),
        frame_filter=FrameFilter(session_id=SESSION_ID, arbitration_ids=(0x7FF,)),
        after_sequence=None,
        limit=10,
    )

    assert page.frames == ()
    assert page.has_more is False


def test_an_engine_failure_is_translated_not_leaked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raw ``duckdb.Error`` must never cross the query boundary."""

    class _FailingConnection:
        def execute(self, statement: str, parameters: object) -> object:
            raise duckdb.Error("simulated engine failure")

        def close(self) -> None:
            return None

    monkeypatch.setattr(engine_module, "_connect", lambda: _FailingConnection())
    path = _data_segment(tmp_path, [frame(0)])

    with pytest.raises(QueryExecutionError) as info:
        QueryEngine().query_frames(
            paths=(path,),
            frame_filter=FrameFilter(session_id=SESSION_ID),
            after_sequence=None,
            limit=10,
        )

    assert info.value.code == "query.execution_failed"
    assert isinstance(info.value.__cause__, duckdb.Error)


def test_the_engine_releases_its_connection_after_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[bool] = []

    class _FailingConnection:
        def execute(self, statement: str, parameters: object) -> object:
            raise duckdb.Error("simulated engine failure")

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(engine_module, "_connect", lambda: _FailingConnection())

    with pytest.raises(QueryExecutionError):
        QueryEngine().summarize(
            paths=(_data_segment(tmp_path, [frame(0)]),),
            frame_filter=FrameFilter(session_id=SESSION_ID),
        )

    assert closed == [True]


def test_a_row_that_no_longer_describes_a_frame_is_refused(tmp_path: Path) -> None:
    """A schema-valid file whose values are illegal must fail typed, not silently."""
    table = frames_to_table(
        [frame(0)],
        session_id=SESSION_ID,
        stream_id=STREAM_ID,
        segment_index=0,
    )
    broken = table.set_column(
        table.schema.get_field_index("direction"),
        "direction",
        pa.array(["not-a-direction"], type=pa.string()),
    )
    path = tmp_path / "broken.parquet"
    pq.write_table(broken, path)

    with pytest.raises(QueryIntegrityError) as info:
        QueryEngine().query_frames(
            paths=(path,),
            frame_filter=FrameFilter(session_id=SESSION_ID),
            after_sequence=None,
            limit=10,
        )

    assert info.value.code == "query.frame_invalid"


def test_a_bound_path_is_escaped_so_a_glob_character_cannot_match_another_file(
    tmp_path: Path,
) -> None:
    """`[` is legal in a Windows file name and glob syntax in DuckDB."""
    bracket = tmp_path / "a[1]"
    plain = tmp_path / "a1"
    bracket.mkdir()
    plain.mkdir()
    _write_segment(bracket / "s.parquet", [frame(0)], segment_index=0)
    _write_segment(plain / "s.parquet", [frame(9)], segment_index=0)

    page = QueryEngine().query_frames(
        paths=(bracket / "s.parquet",),
        frame_filter=FrameFilter(session_id=SESSION_ID),
        after_sequence=None,
        limit=10,
    )

    assert [item.sequence for item in page.frames] == [0]


def test_the_engine_never_leaks_a_relation_or_connection(tmp_path: Path) -> None:
    path = _data_segment(tmp_path, [frame(0)])

    page = QueryEngine().query_frames(
        paths=(path,),
        frame_filter=FrameFilter(session_id=SESSION_ID),
        after_sequence=None,
        limit=10,
    )

    assert isinstance(page, FrameQueryPage)
    assert not isinstance(page, duckdb.DuckDBPyRelation)
