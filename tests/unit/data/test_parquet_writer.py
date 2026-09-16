"""Behavior tests for atomic Parquet segment writing and reading."""

from pathlib import Path

import canx.data.parquet as parquet_module
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from canx.data.errors import DataIntegrityError, ParquetReadError, ParquetWriteError
from canx.data.parquet import (
    TEMPORARY_SUFFIX,
    read_segment,
    segment_filename,
    write_segment,
)
from canx.domain.frame import Direction, Frame, TimestampQuality

SESSION_ID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
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


def _write(path: Path, frames: list[Frame], *, segment_index: int = 0) -> int:
    return write_segment(
        path,
        session_id=SESSION_ID,
        stream_id=STREAM_ID,
        segment_index=segment_index,
        frames=frames,
    )


def test_segment_file_names_are_zero_padded_and_sortable() -> None:
    assert segment_filename(0) == "000000.parquet"
    assert segment_filename(41) == "000041.parquet"
    assert segment_filename(123456) == "123456.parquet"


def test_a_negative_segment_index_cannot_name_a_file() -> None:
    with pytest.raises(ParquetWriteError):
        segment_filename(-1)


def test_a_written_segment_is_readable_and_reports_its_size(tmp_path: Path) -> None:
    target = tmp_path / segment_filename(0)

    size = _write(target, [frame(0), frame(1)])

    assert target.is_file()
    assert size == target.stat().st_size
    assert size > 0


def test_a_successful_write_leaves_no_temporary_residue(tmp_path: Path) -> None:
    target = tmp_path / segment_filename(0)

    _write(target, [frame(0)])

    assert not target.with_name(target.name + TEMPORARY_SUFFIX).exists()
    assert sorted(entry.name for entry in tmp_path.iterdir()) == [target.name]


def test_a_written_segment_round_trips_through_disk(tmp_path: Path) -> None:
    target = tmp_path / segment_filename(3)
    frames = [frame(0), frame(1)]

    _write(target, frames, segment_index=3)

    restored = read_segment(
        target,
        expected_session_id=SESSION_ID,
        expected_stream_id=STREAM_ID,
        expected_segment_index=3,
    )
    assert restored == tuple(frames)


def test_reading_a_segment_from_a_different_session_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / segment_filename(0)
    _write(target, [frame(0)])

    with pytest.raises(DataIntegrityError) as info:
        read_segment(target, expected_session_id="11111111-2222-4333-8444-555555555555")

    assert info.value.code == "data.integrity.metadata_mismatch"


def test_reading_a_foreign_parquet_file_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "foreign.parquet"
    pq.write_table(pa.table({"value": pa.array([1, 2, 3], type=pa.int64())}), target)

    with pytest.raises(DataIntegrityError) as info:
        read_segment(target)

    assert info.value.code == "data.integrity.format_mismatch"


def test_reading_a_file_that_is_not_parquet_at_all_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "not-parquet.parquet"
    target.write_bytes(b"not a parquet file")

    with pytest.raises(ParquetReadError) as info:
        read_segment(target)

    assert info.value.code == "data.parquet_read_failed"


def test_reading_a_missing_segment_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ParquetReadError) as info:
        read_segment(tmp_path / segment_filename(9))

    assert info.value.code == "data.parquet_read_failed"


def test_writing_a_segment_without_frames_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ParquetWriteError):
        _write(tmp_path / segment_filename(0), [])


def test_a_failed_table_write_leaves_no_file_and_no_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / segment_filename(0)

    def fail_write(table: pa.Table, path: Path) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr(parquet_module, "_write_table", fail_write)

    with pytest.raises(ParquetWriteError) as info:
        _write(target, [frame(0)])

    assert info.value.code == "data.parquet_write_failed"
    assert not target.exists()
    assert not target.with_name(target.name + TEMPORARY_SUFFIX).exists()
    assert list(tmp_path.iterdir()) == []


def test_a_failed_atomic_replace_never_publishes_a_half_segment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The committed name must only ever appear after a successful atomic replace."""
    target = tmp_path / segment_filename(0)

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr(parquet_module, "_replace", fail_replace)

    with pytest.raises(ParquetWriteError) as info:
        _write(target, [frame(0)])

    assert info.value.code == "data.parquet_write_failed"
    assert not target.exists()
    assert not target.with_name(target.name + TEMPORARY_SUFFIX).exists()


def test_the_temporary_file_is_written_before_the_committed_name_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observed ordering: the writer only ever publishes through a real rename."""
    target = tmp_path / segment_filename(0)
    observed: list[tuple[bool, bool]] = []
    original_replace = parquet_module._replace

    def observing_replace(source: Path, destination: Path) -> None:
        observed.append((source.exists(), destination.exists()))
        original_replace(source, destination)

    monkeypatch.setattr(parquet_module, "_replace", observing_replace)

    _write(target, [frame(0)])

    assert observed == [(True, False)]
    assert target.is_file()
