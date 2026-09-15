"""Behavior tests for the binary FrameBatch transport codec."""

import msgpack
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.transport.msgpack_codec import decode_batch, encode_batch


def test_frame_batch_round_trips_as_binary_messagepack() -> None:
    frame = Frame(
        sequence=9,
        channel_id="can3",
        arbitration_id=0x18DAF110,
        is_extended=True,
        is_fd=True,
        bitrate_switch=True,
        error_state_indicator=False,
        dlc=12,
        data=bytes(range(12)),
        direction=Direction.RX,
        hardware_timestamp=12.5,
        host_timestamp=13.0,
        normalized_timestamp=0.5,
        clock_domain="device.0",
        timestamp_quality=TimestampQuality.HARDWARE,
        flags=3,
    )
    batch = FrameBatch.create(stream_id="stream-1", frames=[frame])

    payload = encode_batch(batch)

    assert isinstance(payload, bytes)
    assert decode_batch(payload) == batch


def test_decoder_rejects_an_unknown_schema_version() -> None:
    payload = msgpack.packb({"schema_version": 2}, use_bin_type=True)

    try:
        decode_batch(payload)
    except ValueError as error:
        assert "schema_version" in str(error)
    else:
        raise AssertionError("unknown schema version was accepted")
