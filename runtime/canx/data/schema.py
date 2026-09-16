"""Canonical Parquet layout for one CAN-X frame segment.

The layout is derived from the canonical :class:`canx.domain.frame.Frame` and is
versioned. A file is never trusted just because it ends in ``.parquet``: readers
confirm the CAN-X format marker, the schema version, the owning session identity
and the exact column layout before reconstructing frames.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pyarrow as pa

from canx.data.errors import DataIntegrityError
from canx.domain.frame import Direction, Frame, TimestampQuality

FRAME_PARQUET_SCHEMA_VERSION = 1
SEGMENT_FORMAT_MARKER = "can-x-frame-segment"

METADATA_FORMAT_KEY = "canx.format"
METADATA_SCHEMA_VERSION_KEY = "canx.schema_version"
METADATA_SESSION_ID_KEY = "canx.session_id"
METADATA_STREAM_ID_KEY = "canx.stream_id"
METADATA_SEGMENT_INDEX_KEY = "canx.segment_index"

#: Column order is part of the persisted contract and must stay stable.
FRAME_COLUMNS: tuple[str, ...] = (
    "sequence",
    "channel_id",
    "arbitration_id",
    "is_extended",
    "is_fd",
    "bitrate_switch",
    "error_state_indicator",
    "dlc",
    "data",
    "direction",
    "hardware_timestamp",
    "host_timestamp",
    "normalized_timestamp",
    "clock_domain",
    "timestamp_quality",
    "flags",
)

FRAME_ARROW_SCHEMA = pa.schema(
    [
        pa.field("sequence", pa.uint64(), nullable=False),
        pa.field("channel_id", pa.string(), nullable=False),
        pa.field("arbitration_id", pa.uint32(), nullable=False),
        pa.field("is_extended", pa.bool_(), nullable=False),
        pa.field("is_fd", pa.bool_(), nullable=False),
        pa.field("bitrate_switch", pa.bool_(), nullable=False),
        pa.field("error_state_indicator", pa.bool_(), nullable=False),
        pa.field("dlc", pa.uint8(), nullable=False),
        pa.field("data", pa.binary(), nullable=False),
        pa.field("direction", pa.string(), nullable=False),
        pa.field("hardware_timestamp", pa.float64(), nullable=True),
        pa.field("host_timestamp", pa.float64(), nullable=False),
        pa.field("normalized_timestamp", pa.float64(), nullable=False),
        pa.field("clock_domain", pa.string(), nullable=False),
        pa.field("timestamp_quality", pa.string(), nullable=False),
        pa.field("flags", pa.uint32(), nullable=False),
    ],
    metadata={},
)


def frame_segment_metadata(
    *, session_id: str, stream_id: str, segment_index: int
) -> dict[str, str]:
    """Return the deterministic key/value metadata stamped into every segment."""
    return {
        METADATA_FORMAT_KEY: SEGMENT_FORMAT_MARKER,
        METADATA_SCHEMA_VERSION_KEY: str(FRAME_PARQUET_SCHEMA_VERSION),
        METADATA_SESSION_ID_KEY: session_id,
        METADATA_STREAM_ID_KEY: stream_id,
        METADATA_SEGMENT_INDEX_KEY: str(segment_index),
    }


def frames_to_table(
    frames: Sequence[Frame],
    *,
    session_id: str,
    stream_id: str,
    segment_index: int,
) -> pa.Table:
    """Build the Arrow table for one bounded segment.

    Only the frames handed in are materialized, so the working set is bounded by
    the segment threshold rather than by the session length.

    Raises:
        DataIntegrityError: If ``frames`` is empty.
    """
    if not frames:
        raise DataIntegrityError(
            "A frame segment must hold at least one frame.",
            code="data.integrity.empty_segment",
        )
    arrays = [
        pa.array([frame.sequence for frame in frames], type=pa.uint64()),
        pa.array([frame.channel_id for frame in frames], type=pa.string()),
        pa.array([frame.arbitration_id for frame in frames], type=pa.uint32()),
        pa.array([frame.is_extended for frame in frames], type=pa.bool_()),
        pa.array([frame.is_fd for frame in frames], type=pa.bool_()),
        pa.array([frame.bitrate_switch for frame in frames], type=pa.bool_()),
        pa.array([frame.error_state_indicator for frame in frames], type=pa.bool_()),
        pa.array([frame.dlc for frame in frames], type=pa.uint8()),
        pa.array([frame.data for frame in frames], type=pa.binary()),
        pa.array([frame.direction.value for frame in frames], type=pa.string()),
        pa.array([frame.hardware_timestamp for frame in frames], type=pa.float64()),
        pa.array([frame.host_timestamp for frame in frames], type=pa.float64()),
        pa.array([frame.normalized_timestamp for frame in frames], type=pa.float64()),
        pa.array([frame.clock_domain for frame in frames], type=pa.string()),
        pa.array([frame.timestamp_quality.value for frame in frames], type=pa.string()),
        pa.array([frame.flags for frame in frames], type=pa.uint32()),
    ]
    table = pa.Table.from_arrays(arrays, schema=FRAME_ARROW_SCHEMA)
    metadata = {
        key.encode("utf-8"): value.encode("utf-8")
        for key, value in frame_segment_metadata(
            session_id=session_id, stream_id=stream_id, segment_index=segment_index
        ).items()
    }
    return table.replace_schema_metadata(metadata)


def table_to_frames(
    table: pa.Table,
    *,
    expected_session_id: str | None = None,
    expected_stream_id: str | None = None,
    expected_segment_index: int | None = None,
) -> tuple[Frame, ...]:
    """Validate a segment table and reconstruct canonical frames.

    Raises:
        DataIntegrityError: If the table is not a CAN-X segment, declares an
            unsupported schema version, belongs to another session/stream/index,
            has a different column layout, or holds a value that no longer
            satisfies the canonical :class:`~canx.domain.frame.Frame`.
    """
    metadata = _decode_metadata(table)
    _require_format_marker(metadata)
    _require_supported_version(metadata)
    _require_identity(
        metadata,
        expected_session_id=expected_session_id,
        expected_stream_id=expected_stream_id,
        expected_segment_index=expected_segment_index,
    )
    _require_layout(table)
    return _reconstruct_frames(table)


def _decode_metadata(table: pa.Table) -> dict[str, str]:
    raw = table.schema.metadata
    if not raw:
        return {}
    return {
        key.decode("utf-8", errors="replace"): value.decode("utf-8", errors="replace")
        for key, value in raw.items()
    }


def _require_format_marker(metadata: dict[str, str]) -> None:
    marker = metadata.get(METADATA_FORMAT_KEY)
    if marker != SEGMENT_FORMAT_MARKER:
        raise DataIntegrityError(
            "The Parquet file is not a CAN-X frame segment.",
            code="data.integrity.format_mismatch",
            details={"format": repr(marker)},
        )


def _require_supported_version(metadata: dict[str, str]) -> None:
    raw_version = metadata.get(METADATA_SCHEMA_VERSION_KEY)
    try:
        version = int(raw_version) if raw_version is not None else -1
    except ValueError:
        version = -1
    if version != FRAME_PARQUET_SCHEMA_VERSION:
        raise DataIntegrityError(
            "The segment was written with an unsupported CAN-X schema version.",
            code="data.integrity.schema_version_unsupported",
            details={
                "schema_version": version,
                "supported_schema_version": FRAME_PARQUET_SCHEMA_VERSION,
            },
        )


def _require_identity(
    metadata: dict[str, str],
    *,
    expected_session_id: str | None,
    expected_stream_id: str | None,
    expected_segment_index: int | None,
) -> None:
    checks: tuple[tuple[str, str, str | None], ...] = (
        (METADATA_SESSION_ID_KEY, "session_id", expected_session_id),
        (METADATA_STREAM_ID_KEY, "stream_id", expected_stream_id),
        (
            METADATA_SEGMENT_INDEX_KEY,
            "segment_index",
            None if expected_segment_index is None else str(expected_segment_index),
        ),
    )
    for metadata_key, field, expected in checks:
        if expected is None:
            continue
        actual = metadata.get(metadata_key)
        if actual != expected:
            raise DataIntegrityError(
                f"The segment does not belong to the expected {field}.",
                code="data.integrity.metadata_mismatch",
                details={"field": field, "expected": expected, "actual": actual},
            )


def _require_layout(table: pa.Table) -> None:
    names = table.schema.names
    if names != list(FRAME_COLUMNS):
        raise DataIntegrityError(
            "The segment column layout is not the canonical CAN-X frame layout.",
            code="data.integrity.parquet_schema_mismatch",
            details={"columns": names},
        )
    mismatched = [
        field.name
        for field in table.schema
        if field.type != FRAME_ARROW_SCHEMA.field(field.name).type
    ]
    if mismatched:
        raise DataIntegrityError(
            "The segment column types do not match the canonical CAN-X frame layout.",
            code="data.integrity.parquet_schema_mismatch",
            details={"columns": mismatched},
        )


def _reconstruct_frames(table: pa.Table) -> tuple[Frame, ...]:
    frames: list[Frame] = []
    try:
        for row in table.to_pylist():
            frames.append(_row_to_frame(row))
    except (ValueError, TypeError, KeyError) as error:
        raise DataIntegrityError(
            "The segment holds a frame value that is no longer valid.",
            code="data.integrity.frame_invalid",
            details={"error": str(error), "row": len(frames)},
        ) from error
    return tuple(frames)


def _row_to_frame(row: dict[str, Any]) -> Frame:
    return Frame(
        sequence=row["sequence"],
        channel_id=row["channel_id"],
        arbitration_id=row["arbitration_id"],
        is_extended=row["is_extended"],
        is_fd=row["is_fd"],
        bitrate_switch=row["bitrate_switch"],
        error_state_indicator=row["error_state_indicator"],
        dlc=row["dlc"],
        data=row["data"],
        direction=Direction(row["direction"]),
        hardware_timestamp=row["hardware_timestamp"],
        host_timestamp=row["host_timestamp"],
        normalized_timestamp=row["normalized_timestamp"],
        clock_domain=row["clock_domain"],
        timestamp_quality=TimestampQuality(row["timestamp_quality"]),
        flags=row["flags"],
    )
