"""Atomic Parquet segment files for CAN-X data sessions.

A segment is only ever published through a temporary file that is fully written
and closed before an atomic replace. A crash therefore can never leave a
plausibly-named ``.parquet`` file holding half a segment; at worst it leaves a
``*.parquet.tmp`` residue that integrity inspection reports.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from canx.data.errors import ParquetReadError, ParquetWriteError
from canx.data.schema import frames_to_table, table_to_frames
from canx.domain.frame import Frame

TEMPORARY_SUFFIX = ".parquet.tmp"
PARQUET_COMPRESSION = "zstd"


def segment_filename(segment_index: int) -> str:
    """Return the deterministic, sort-friendly file name for a segment index.

    Raises:
        ParquetWriteError: If ``segment_index`` is not a non-negative integer.
    """
    if not isinstance(segment_index, int) or isinstance(segment_index, bool):
        raise ParquetWriteError(
            "A segment index must be a non-negative integer.",
            code="data.parquet_write_failed",
            details={"segment_index": repr(segment_index)},
        )
    if segment_index < 0:
        raise ParquetWriteError(
            "A segment index must not be negative.",
            code="data.parquet_write_failed",
            details={"segment_index": segment_index},
        )
    return f"{segment_index:06d}.parquet"


def write_segment(
    path: Path,
    *,
    session_id: str,
    stream_id: str,
    segment_index: int,
    frames: Sequence[Frame],
) -> int:
    """Write one segment atomically and return the committed byte size.

    The temporary file is fully written and closed before it replaces the final
    name. On any failure the temporary file is removed and the committed name is
    never created.

    Raises:
        ParquetWriteError: If the segment could not be durably committed.
        DataIntegrityError: If ``frames`` cannot describe a valid segment.
    """
    frozen = tuple(frames)
    if not frozen:
        raise ParquetWriteError(
            "A frame segment must hold at least one frame.",
            code="data.parquet_write_failed",
            details={"path": str(path)},
        )
    temporary = path.with_name(path.name + TEMPORARY_SUFFIX)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        table = frames_to_table(
            frozen,
            session_id=session_id,
            stream_id=stream_id,
            segment_index=segment_index,
        )
        _write_table(table, temporary)
        byte_size = temporary.stat().st_size
        _replace(temporary, path)
    except (pa.ArrowException, OSError) as error:
        _remove_quietly(temporary)
        raise ParquetWriteError(
            "The Parquet segment could not be committed.",
            code="data.parquet_write_failed",
            details={
                "path": str(path),
                "session_id": session_id,
                "segment_index": segment_index,
            },
        ) from error
    return byte_size


def read_segment(
    path: Path,
    *,
    expected_session_id: str | None = None,
    expected_stream_id: str | None = None,
    expected_segment_index: int | None = None,
) -> tuple[Frame, ...]:
    """Read a segment back and verify it against the expected identity.

    Raises:
        ParquetReadError: If the file is missing or is not readable Parquet.
        DataIntegrityError: If the file is readable but is not a CAN-X segment of
            the expected session/stream/index, or holds an invalid frame.
    """
    if not path.is_file():
        raise ParquetReadError(
            "The Parquet segment file is missing.",
            code="data.parquet_read_failed",
            details={"path": str(path)},
        )
    try:
        table = pq.read_table(path)
    except (pa.ArrowException, OSError) as error:
        raise ParquetReadError(
            "The Parquet segment file could not be read.",
            code="data.parquet_read_failed",
            details={"path": str(path)},
        ) from error
    return table_to_frames(
        table,
        expected_session_id=expected_session_id,
        expected_stream_id=expected_stream_id,
        expected_segment_index=expected_segment_index,
    )


def _write_table(table: pa.Table, path: Path) -> None:
    """Write and close ``table`` at ``path`` (a seam for failure tests)."""
    pq.write_table(table, path, compression=PARQUET_COMPRESSION)


def _replace(source: Path, destination: Path) -> None:
    """Atomically replace ``destination`` with ``source`` (a seam for tests)."""
    os.replace(source, destination)


def _remove_quietly(path: Path) -> None:
    with suppress(OSError):
        path.unlink()
