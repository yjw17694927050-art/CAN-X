"""Explicit MessagePack wire codec for versioned FrameBatch values."""

from typing import cast

import msgpack  # type: ignore[import-untyped]  # upstream package has no py.typed marker

from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality


def encode_batch(batch: FrameBatch) -> bytes:
    """Encode a batch without leaking Python-specific object metadata."""
    payload = {
        "schema_version": batch.schema_version,
        "stream_id": batch.stream_id,
        "first_sequence": batch.first_sequence,
        "last_sequence": batch.last_sequence,
        "frame_count": batch.frame_count,
        "frames": [
            {
                "sequence": frame.sequence,
                "channel_id": frame.channel_id,
                "arbitration_id": frame.arbitration_id,
                "is_extended": frame.is_extended,
                "is_fd": frame.is_fd,
                "bitrate_switch": frame.bitrate_switch,
                "error_state_indicator": frame.error_state_indicator,
                "dlc": frame.dlc,
                "data": frame.data,
                "direction": frame.direction.value,
                "hardware_timestamp": frame.hardware_timestamp,
                "host_timestamp": frame.host_timestamp,
                "normalized_timestamp": frame.normalized_timestamp,
                "clock_domain": frame.clock_domain,
                "timestamp_quality": frame.timestamp_quality.value,
                "flags": frame.flags,
            }
            for frame in batch.frames
        ],
    }
    return cast(bytes, msgpack.packb(payload, use_bin_type=True))


def decode_batch(payload: bytes) -> FrameBatch:
    """Decode and validate an untrusted binary FrameBatch payload."""
    unpacked = msgpack.unpackb(payload, raw=False, strict_map_key=True)
    if not isinstance(unpacked, dict):
        raise ValueError("FrameBatch payload must be a map")
    data = cast(dict[str, object], unpacked)
    schema_version = _integer(data, "schema_version")
    if schema_version != FrameBatch.CURRENT_SCHEMA_VERSION:
        raise ValueError("unsupported FrameBatch schema_version")
    raw_frames = data.get("frames")
    if not isinstance(raw_frames, list):
        raise ValueError("FrameBatch frames must be an array")
    frames = tuple(_decode_frame(item) for item in raw_frames)
    return FrameBatch(
        schema_version=schema_version,
        stream_id=_string(data, "stream_id"),
        first_sequence=_integer(data, "first_sequence"),
        last_sequence=_integer(data, "last_sequence"),
        frame_count=_integer(data, "frame_count"),
        frames=frames,
    )


def _decode_frame(value: object) -> Frame:
    if not isinstance(value, dict):
        raise ValueError("Frame payload must be a map")
    data = cast(dict[str, object], value)
    hardware_timestamp = data.get("hardware_timestamp")
    if hardware_timestamp is not None and not isinstance(hardware_timestamp, float):
        raise ValueError("hardware_timestamp must be a float or null")
    raw_data = data.get("data")
    if not isinstance(raw_data, bytes):
        raise ValueError("Frame data must be binary")
    return Frame(
        sequence=_integer(data, "sequence"),
        channel_id=_string(data, "channel_id"),
        arbitration_id=_integer(data, "arbitration_id"),
        is_extended=_boolean(data, "is_extended"),
        is_fd=_boolean(data, "is_fd"),
        bitrate_switch=_boolean(data, "bitrate_switch"),
        error_state_indicator=_boolean(data, "error_state_indicator"),
        dlc=_integer(data, "dlc"),
        data=raw_data,
        direction=Direction(_string(data, "direction")),
        hardware_timestamp=hardware_timestamp,
        host_timestamp=_number(data, "host_timestamp"),
        normalized_timestamp=_number(data, "normalized_timestamp"),
        clock_domain=_string(data, "clock_domain"),
        timestamp_quality=TimestampQuality(_string(data, "timestamp_quality")),
        flags=_integer(data, "flags"),
    )


def _integer(data: dict[str, object], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _number(data: dict[str, object], key: str) -> float:
    value = data.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{key} must be a number")
    return float(value)


def _string(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _boolean(data: dict[str, object], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value
