"""Behavior tests for versioned realtime frame batches."""

import pytest
from canx.domain.batch import FrameBatch, batch_frames
from canx.domain.frame import Direction, Frame, TimestampQuality


def frame(sequence: int) -> Frame:
    """Create a literal valid frame for batch boundary tests."""
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x123,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=1,
        data=b"\x01",
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=10.0 + sequence,
        normalized_timestamp=float(sequence),
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def test_batch_derives_sequence_bounds_and_count() -> None:
    batch = FrameBatch.create(stream_id="stream-1", frames=[frame(4), frame(5), frame(6)])

    assert batch.schema_version == 1
    assert batch.first_sequence == 4
    assert batch.last_sequence == 6
    assert batch.frame_count == 3


def test_batch_rejects_a_sequence_gap() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        FrameBatch.create(stream_id="stream-1", frames=[frame(4), frame(6)])


def test_batch_rejects_empty_frames() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        FrameBatch.create(stream_id="stream-1", frames=[])


@pytest.mark.parametrize("size", [50, 100, 250, 500, 1000])
def test_batcher_preserves_a_final_partial_batch(size: int) -> None:
    source = [frame(sequence) for sequence in range(size + 3)]

    batches = list(batch_frames(source, size=size, stream_id="stream-1"))

    assert [batch.frame_count for batch in batches] == [size, 3]
    assert batches[1].first_sequence == size
    assert batches[1].last_sequence == size + 2


def test_batcher_rejects_non_positive_size() -> None:
    with pytest.raises(ValueError, match="positive"):
        list(batch_frames([frame(0)], size=0, stream_id="stream-1"))
