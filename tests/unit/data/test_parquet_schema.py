"""Behavior tests for the canonical CAN-X frame Parquet schema."""

import pyarrow as pa
import pytest
from canx.data.errors import DataIntegrityError
from canx.data.schema import (
    FRAME_ARROW_SCHEMA,
    FRAME_COLUMNS,
    FRAME_PARQUET_SCHEMA_VERSION,
    METADATA_FORMAT_KEY,
    METADATA_SCHEMA_VERSION_KEY,
    METADATA_SEGMENT_INDEX_KEY,
    METADATA_SESSION_ID_KEY,
    METADATA_STREAM_ID_KEY,
    SEGMENT_FORMAT_MARKER,
    frame_segment_metadata,
    frames_to_table,
    table_to_frames,
)
from canx.domain.frame import Direction, Frame, TimestampQuality

SESSION_ID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
STREAM_ID = "stream-1"

_EXPECTED_TYPES: dict[str, pa.DataType] = {
    "sequence": pa.uint64(),
    "channel_id": pa.string(),
    "arbitration_id": pa.uint32(),
    "is_extended": pa.bool_(),
    "is_fd": pa.bool_(),
    "bitrate_switch": pa.bool_(),
    "error_state_indicator": pa.bool_(),
    "dlc": pa.uint8(),
    "data": pa.binary(),
    "direction": pa.string(),
    "hardware_timestamp": pa.float64(),
    "host_timestamp": pa.float64(),
    "normalized_timestamp": pa.float64(),
    "clock_domain": pa.string(),
    "timestamp_quality": pa.string(),
    "flags": pa.uint32(),
}


def classic_frame(sequence: int, **changes: object) -> Frame:
    """Build a valid Classic CAN frame with explicit domain fields."""
    values: dict[str, object] = {
        "sequence": sequence,
        "channel_id": "can0",
        "arbitration_id": 0x123,
        "is_extended": False,
        "is_fd": False,
        "bitrate_switch": False,
        "error_state_indicator": False,
        "dlc": 3,
        "data": bytes.fromhex("010203"),
        "direction": Direction.RX,
        "hardware_timestamp": None,
        "host_timestamp": 100.0 + sequence,
        "normalized_timestamp": float(sequence),
        "clock_domain": "host.monotonic",
        "timestamp_quality": TimestampQuality.HOST,
        "flags": 0,
    }
    values.update(changes)
    return Frame(**values)  # type: ignore[arg-type]


def fd_frame(sequence: int, **changes: object) -> Frame:
    """Build a valid CAN FD frame with a 64-byte payload."""
    values: dict[str, object] = {
        "is_fd": True,
        "bitrate_switch": True,
        "dlc": 64,
        "data": bytes(range(64)),
        "hardware_timestamp": 55.5,
        "timestamp_quality": TimestampQuality.HARDWARE,
    }
    values.update(changes)
    return classic_frame(sequence, **values)


def build_table(frames: list[Frame], *, segment_index: int = 0) -> pa.Table:
    return frames_to_table(
        frames, session_id=SESSION_ID, stream_id=STREAM_ID, segment_index=segment_index
    )


def test_the_segment_format_is_versioned_and_named() -> None:
    assert FRAME_PARQUET_SCHEMA_VERSION == 1
    assert SEGMENT_FORMAT_MARKER == "can-x-frame-segment"


def test_the_arrow_schema_matches_the_canonical_column_order() -> None:
    assert FRAME_ARROW_SCHEMA.names == list(FRAME_COLUMNS)


def test_every_frame_field_has_the_documented_arrow_type() -> None:
    actual = {field.name: field.type for field in FRAME_ARROW_SCHEMA}

    assert actual == _EXPECTED_TYPES


def test_only_the_hardware_timestamp_may_be_absent() -> None:
    nullable = {field.name for field in FRAME_ARROW_SCHEMA if field.nullable}

    assert nullable == {"hardware_timestamp"}


def test_segment_metadata_names_the_format_and_identity() -> None:
    metadata = frame_segment_metadata(
        session_id=SESSION_ID, stream_id=STREAM_ID, segment_index=7
    )

    assert metadata == {
        METADATA_FORMAT_KEY: SEGMENT_FORMAT_MARKER,
        METADATA_SCHEMA_VERSION_KEY: str(FRAME_PARQUET_SCHEMA_VERSION),
        METADATA_SESSION_ID_KEY: SESSION_ID,
        METADATA_STREAM_ID_KEY: STREAM_ID,
        METADATA_SEGMENT_INDEX_KEY: "7",
    }


def test_a_table_carries_the_canx_file_metadata() -> None:
    table = build_table([classic_frame(0)])

    assert table.schema.metadata is not None
    stored = {key.decode(): value.decode() for key, value in table.schema.metadata.items()}
    assert stored[METADATA_FORMAT_KEY] == SEGMENT_FORMAT_MARKER
    assert stored[METADATA_SCHEMA_VERSION_KEY] == "1"
    assert stored[METADATA_SESSION_ID_KEY] == SESSION_ID
    assert stored[METADATA_STREAM_ID_KEY] == STREAM_ID
    assert stored[METADATA_SEGMENT_INDEX_KEY] == "0"


def test_building_a_table_from_no_frames_is_rejected() -> None:
    with pytest.raises(DataIntegrityError):
        build_table([])


def test_classic_frames_round_trip_every_field() -> None:
    frames = [
        classic_frame(0),
        classic_frame(1, direction=Direction.TX, timestamp_quality=TimestampQuality.ESTIMATED),
        classic_frame(
            2,
            arbitration_id=0x7FF,
            is_extended=False,
            dlc=8,
            data=bytes.fromhex("0011223344556677"),
            hardware_timestamp=12.5,
            timestamp_quality=TimestampQuality.HARDWARE,
            clock_domain="pcan.channel0",
            flags=0xDEADBEEF,
        ),
    ]

    assert table_to_frames(build_table(frames)) == tuple(frames)


def test_can_fd_frames_round_trip_payload_and_flags() -> None:
    frames = [
        fd_frame(0),
        fd_frame(1, bitrate_switch=False, error_state_indicator=True, dlc=12, data=bytes(12)),
    ]

    assert table_to_frames(build_table(frames)) == tuple(frames)


def test_an_absent_hardware_timestamp_survives_the_round_trip() -> None:
    frames = [classic_frame(0, hardware_timestamp=None)]

    restored = table_to_frames(build_table(frames))

    assert restored[0].hardware_timestamp is None


def test_binary_payloads_are_not_text_decoded() -> None:
    payload = bytes([0x00, 0xFF, 0x80, 0x0A, 0x0D])
    frames = [classic_frame(0, dlc=5, data=payload)]

    restored = table_to_frames(build_table(frames))

    assert restored[0].data == payload


def test_reading_validates_the_canx_format_marker() -> None:
    foreign = pa.table({"sequence": pa.array([1], type=pa.uint64())})

    with pytest.raises(DataIntegrityError) as info:
        table_to_frames(foreign)

    assert info.value.code == "data.integrity.format_mismatch"


def test_reading_rejects_an_unsupported_segment_schema_version() -> None:
    table = build_table([classic_frame(0)])
    metadata = dict(table.schema.metadata or {})
    metadata[METADATA_SCHEMA_VERSION_KEY.encode()] = b"99"
    tampered = table.replace_schema_metadata(metadata)

    with pytest.raises(DataIntegrityError) as info:
        table_to_frames(tampered)

    assert info.value.code == "data.integrity.schema_version_unsupported"
    assert info.value.details["schema_version"] == 99


@pytest.mark.parametrize(
    "expected_changes",
    [
        {"expected_session_id": "11111111-2222-4333-8444-555555555555"},
        {"expected_stream_id": "other-stream"},
        {"expected_segment_index": 5},
    ],
    ids=["session", "stream", "index"],
)
def test_reading_rejects_a_segment_from_a_different_identity(
    expected_changes: dict[str, object],
) -> None:
    table = build_table([classic_frame(0)])

    with pytest.raises(DataIntegrityError) as info:
        table_to_frames(table, **expected_changes)  # type: ignore[arg-type]

    assert info.value.code == "data.integrity.metadata_mismatch"


def test_reading_rejects_a_file_with_the_wrong_column_layout() -> None:
    table = build_table([classic_frame(0)])
    reordered = table.select(
        ["channel_id", *[name for name in FRAME_COLUMNS if name != "channel_id"]]
    )

    with pytest.raises(DataIntegrityError) as info:
        table_to_frames(reordered)

    assert info.value.code == "data.integrity.parquet_schema_mismatch"


def test_reading_rejects_a_frame_value_that_is_no_longer_valid() -> None:
    table = build_table([classic_frame(0)])
    positions = list(FRAME_COLUMNS).index("direction")
    mutated = table.set_column(
        positions, "direction", pa.array(["sideways"], type=pa.string())
    )

    with pytest.raises(DataIntegrityError) as info:
        table_to_frames(mutated)

    assert info.value.code == "data.integrity.frame_invalid"
