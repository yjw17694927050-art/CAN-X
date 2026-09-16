"""Behavior tests for the footer-only segment header validation.

This gate is what lets a query reject a foreign, mislabelled or corrupt file
before an engine scans it, and it must do so without reading the row payload —
otherwise "validate then query" would materialize the dataset it is trying to
avoid.
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from canx.data.errors import DataIntegrityError, ParquetReadError
from canx.data.model import SegmentHeader
from canx.data.parquet import (
    segment_filename,
    validate_segment_header,
    write_segment,
)
from canx.data.schema import FRAME_ARROW_SCHEMA, FRAME_COLUMNS, frames_to_table
from canx.domain.frame import Direction, Frame, TimestampQuality

SESSION_ID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
OTHER_SESSION_ID = "11111111-2222-4333-8444-555555555555"
STREAM_ID = "stream-1"


def frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x1AB,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=2,
        data=bytes([sequence, 0x10]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=10.0 + sequence,
        normalized_timestamp=float(sequence),
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def _write(
    path: Path,
    frames: list[Frame],
    *,
    session_id: str = SESSION_ID,
    stream_id: str = STREAM_ID,
    segment_index: int = 0,
) -> Path:
    write_segment(
        path,
        session_id=session_id,
        stream_id=stream_id,
        segment_index=segment_index,
        frames=frames,
    )
    return path


def _metadata_table(
    *, session_id: str, segment_index: int, version: int = 1
) -> pa.Table:
    table = pa.Table.from_arrays(
        [pa.array([], type=field.type) for field in FRAME_ARROW_SCHEMA],
        schema=FRAME_ARROW_SCHEMA,
    )
    return table.replace_schema_metadata(
        {
            b"canx.format": b"can-x-frame-segment",
            b"canx.schema_version": str(version).encode(),
            b"canx.session_id": session_id.encode(),
            b"canx.stream_id": STREAM_ID.encode(),
            b"canx.segment_index": str(segment_index).encode(),
        }
    )


def test_a_header_reports_the_identity_stamped_into_the_footer(tmp_path: Path) -> None:
    path = _write(tmp_path / segment_filename(2), [frame(0), frame(1)], segment_index=2)

    header = validate_segment_header(path)

    assert isinstance(header, SegmentHeader)
    assert header.session_id == SESSION_ID
    assert header.stream_id == STREAM_ID
    assert header.segment_index == 2
    assert header.schema_version == 1
    assert header.row_count == 2


def test_a_header_can_be_checked_against_an_explicit_identity(tmp_path: Path) -> None:
    path = _write(tmp_path / segment_filename(0), [frame(0)])

    header = validate_segment_header(
        path,
        expected_session_id=SESSION_ID,
        expected_stream_id=STREAM_ID,
        expected_segment_index=0,
    )

    assert header.row_count == 1


def test_a_missing_file_is_a_read_failure(tmp_path: Path) -> None:
    with pytest.raises(ParquetReadError) as info:
        validate_segment_header(tmp_path / segment_filename(9))

    assert info.value.code == "data.parquet_read_failed"


def test_a_file_that_is_not_parquet_is_a_read_failure(tmp_path: Path) -> None:
    target = tmp_path / "corrupt.parquet"
    target.write_bytes(b"definitely not parquet")

    with pytest.raises(ParquetReadError) as info:
        validate_segment_header(target)

    assert info.value.code == "data.parquet_read_failed"


def test_a_foreign_parquet_file_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "foreign.parquet"
    pq.write_table(pa.table({"value": pa.array([1, 2, 3], type=pa.int64())}), target)

    with pytest.raises(DataIntegrityError) as info:
        validate_segment_header(target)

    assert info.value.code == "data.integrity.format_mismatch"


def test_a_wrong_session_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path / segment_filename(0), [frame(0)])

    with pytest.raises(DataIntegrityError) as info:
        validate_segment_header(path, expected_session_id=OTHER_SESSION_ID)

    assert info.value.code == "data.integrity.metadata_mismatch"
    assert info.value.details["field"] == "session_id"


def test_a_wrong_stream_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path / segment_filename(0), [frame(0)])

    with pytest.raises(DataIntegrityError) as info:
        validate_segment_header(path, expected_stream_id="other-stream")

    assert info.value.code == "data.integrity.metadata_mismatch"
    assert info.value.details["field"] == "stream_id"


def test_a_wrong_segment_index_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path / segment_filename(1), [frame(0)], segment_index=1)

    with pytest.raises(DataIntegrityError) as info:
        validate_segment_header(path, expected_segment_index=0)

    assert info.value.code == "data.integrity.metadata_mismatch"
    assert info.value.details["field"] == "segment_index"


def test_an_unsupported_schema_version_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "version-99.parquet"
    pq.write_table(
        _metadata_table(session_id=SESSION_ID, segment_index=0, version=99), target
    )

    with pytest.raises(DataIntegrityError) as info:
        validate_segment_header(target)

    assert info.value.code == "data.integrity.schema_version_unsupported"


def test_a_non_numeric_schema_version_is_refused(tmp_path: Path) -> None:
    table = _metadata_table(session_id=SESSION_ID, segment_index=0)
    metadata = dict(table.schema.metadata or {})
    metadata[b"canx.schema_version"] = b"one"
    target = tmp_path / "version-text.parquet"
    pq.write_table(table.replace_schema_metadata(metadata), target)

    with pytest.raises(DataIntegrityError) as info:
        validate_segment_header(target)

    assert info.value.code == "data.integrity.schema_version_unsupported"


def test_a_canx_marker_with_the_wrong_column_layout_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "wrong-layout.parquet"
    pq.write_table(
        pa.table({"value": pa.array([1], type=pa.int64())}).replace_schema_metadata(
            {
                b"canx.format": b"can-x-frame-segment",
                b"canx.schema_version": b"1",
                b"canx.session_id": SESSION_ID.encode(),
                b"canx.stream_id": STREAM_ID.encode(),
                b"canx.segment_index": b"0",
            }
        ),
        target,
    )

    with pytest.raises(DataIntegrityError) as info:
        validate_segment_header(target)

    assert info.value.code == "data.integrity.parquet_schema_mismatch"


def test_a_canx_marker_with_a_wrong_column_type_is_refused(tmp_path: Path) -> None:
    table = pa.Table.from_arrays(
        [
            pa.array([], type=pa.int64() if field.name == "sequence" else field.type)
            for field in FRAME_ARROW_SCHEMA
        ],
        schema=pa.schema(
            [
                pa.field(
                    field.name,
                    pa.int64() if field.name == "sequence" else field.type,
                    nullable=field.nullable,
                )
                for field in FRAME_ARROW_SCHEMA
            ]
        ),
    )
    target = tmp_path / "wrong-types.parquet"
    pq.write_table(
        table.replace_schema_metadata(
            {
                b"canx.format": b"can-x-frame-segment",
                b"canx.schema_version": b"1",
                b"canx.session_id": SESSION_ID.encode(),
                b"canx.stream_id": STREAM_ID.encode(),
                b"canx.segment_index": b"0",
            }
        ),
        target,
    )

    with pytest.raises(DataIntegrityError) as info:
        validate_segment_header(target)

    assert info.value.code == "data.integrity.parquet_schema_mismatch"
    assert "sequence" in info.value.details["columns"]


def test_a_missing_canx_metadata_field_is_refused(tmp_path: Path) -> None:
    table = _metadata_table(session_id=SESSION_ID, segment_index=0)
    metadata = dict(table.schema.metadata or {})
    del metadata[b"canx.session_id"]
    target = tmp_path / "no-session-metadata.parquet"
    pq.write_table(table.replace_schema_metadata(metadata), target)

    with pytest.raises(DataIntegrityError) as info:
        validate_segment_header(target)

    assert info.value.code == "data.integrity.metadata_mismatch"


def test_header_validation_never_reads_the_row_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate must stay footer-only: reading rows would defeat its purpose."""
    path = _write(tmp_path / segment_filename(0), [frame(index) for index in range(5)])
    reads: list[object] = []
    original = pq.read_table

    def observing_read_table(*arguments: object, **keywords: object) -> pa.Table:
        reads.append(arguments)
        return original(*arguments, **keywords)  # type: ignore[arg-type]

    monkeypatch.setattr(pq, "read_table", observing_read_table)

    header = validate_segment_header(path)

    assert header.row_count == 5
    assert reads == []


def test_the_canonical_column_order_is_the_persisted_contract() -> None:
    """A layout change would invalidate every existing segment, so it is pinned."""
    assert FRAME_COLUMNS[0] == "sequence"
    assert FRAME_COLUMNS[-1] == "flags"
    assert list(FRAME_COLUMNS) == FRAME_ARROW_SCHEMA.names
    assert len(FRAME_COLUMNS) == len(FRAME_ARROW_SCHEMA) == 16


def test_a_header_matches_the_registered_metadata_of_a_real_segment(tmp_path: Path) -> None:
    path = _write(
        tmp_path / segment_filename(3), [frame(index) for index in range(7)], segment_index=3
    )
    table = frames_to_table(
        [frame(index) for index in range(7)],
        session_id=SESSION_ID,
        stream_id=STREAM_ID,
        segment_index=3,
    )

    header = validate_segment_header(path)

    assert header.row_count == table.num_rows == 7
