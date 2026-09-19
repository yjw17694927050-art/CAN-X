"""A DataSessionWriter refuses any batch that would persist a timestamp descent.

Reading a session in sequence order must never produce a frame whose
``normalized_timestamp`` is smaller than the one before it — neither inside a
batch nor across a batch boundary. The comparison is strict on purpose: equal
consecutive timestamps are legal (two frames may share one clock tick) and are
locked here by an explicit test, while any real descent is refused with the
existing typed ``data.integrity.timestamp_regression`` code.
"""

from itertools import pairwise
from pathlib import Path

import pytest
from canx.data.errors import DataIntegrityError
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService

STREAM_ID = "monotonic-stream"


def frame(sequence: int, **changes: object) -> Frame:
    values: dict[str, object] = {
        "sequence": sequence,
        "channel_id": "can0",
        "arbitration_id": 0x123,
        "is_extended": False,
        "is_fd": False,
        "bitrate_switch": False,
        "error_state_indicator": False,
        "dlc": 2,
        "data": bytes([sequence % 256, 0x10]),
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


def timed_batch(start: int, timestamps: list[float]) -> FrameBatch:
    """Build a sequence-contiguous batch whose timestamps are chosen literally."""
    return FrameBatch.create(
        stream_id=STREAM_ID,
        frames=[
            frame(start + offset, normalized_timestamp=value)
            for offset, value in enumerate(timestamps)
        ],
    )


def project(tmp_path: Path, *, name: str = "vehicle.canx") -> ProjectHandle:
    return ProjectService().create(tmp_path / name, display_name="Vehicle A")


@pytest.mark.parametrize(
    ("timestamps", "position"),
    [
        ([1.0, 3.0, 2.0, 4.0], 2),
        ([2.0, 1.0, 3.0], 1),
        ([5.0, 4.0], 1),
        ([1.0, 2.0, 3.0, 2.5, 4.0], 3),
    ],
    ids=["named-regression", "early-descent", "pair", "late-descent"],
)
def test_an_intra_batch_descent_between_adjacent_frames_is_refused(
    tmp_path: Path, timestamps: list[float], position: int
) -> None:
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root, max_frames_per_segment=64).start(
            stream_id=STREAM_ID
        )

        with pytest.raises(DataIntegrityError) as info:
            writer.append(timed_batch(0, timestamps))

        assert info.value.code == "data.integrity.timestamp_regression"
        assert info.value.details["position"] == position
        assert info.value.details["previous_timestamp"] == timestamps[position - 1]
        assert info.value.details["current_timestamp"] == timestamps[position]
        assert writer.state is DataSessionState.ACTIVE
        assert writer.frame_count == 0
        assert writer.segment_count == 0


def test_a_descent_across_a_batch_boundary_is_refused(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root, max_frames_per_segment=64).start(
            stream_id=STREAM_ID
        )
        writer.append(timed_batch(0, [1.0, 2.0]))

        with pytest.raises(DataIntegrityError) as info:
            writer.append(timed_batch(2, [1.5, 3.0]))

        assert info.value.code == "data.integrity.timestamp_regression"
        assert info.value.details["previous_last_timestamp"] == 2.0
        assert info.value.details["batch_first_timestamp"] == 1.5
        assert writer.frame_count == 0

        writer.append(timed_batch(2, [2.0, 3.0]))
        completed = writer.finalize()

        assert completed.frame_count == 4


def test_a_descent_across_a_committed_segment_boundary_is_refused(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(timed_batch(0, [1.0, 2.0]))
        assert writer.segment_count == 1

        with pytest.raises(DataIntegrityError) as info:
            writer.append(timed_batch(2, [1.0, 5.0]))

        assert info.value.code == "data.integrity.timestamp_regression"
        assert writer.segment_count == 1
        assert writer.frame_count == 2
        assert service.list_segments(writer.session_id)[0].last_timestamp == 2.0
        assert service.inspect_integrity().clean is True


def test_a_refused_batch_leaves_the_session_defined_and_registers_no_partial_segment(
    tmp_path: Path,
) -> None:
    """The refusal happens before buffering, so nothing committed can move."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=3)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(timed_batch(0, [1.0, 2.0, 3.0]))
        before = service.list_segments(writer.session_id)

        with pytest.raises(DataIntegrityError) as info:
            writer.append(timed_batch(3, [10.0, 20.0, 15.0]))

        assert info.value.code == "data.integrity.timestamp_regression"
        assert writer.state is DataSessionState.ACTIVE
        assert writer.frame_count == 3
        assert writer.segment_count == 1
        assert service.list_segments(writer.session_id) == before
        assert service.inspect_integrity().clean is True

        writer.append(timed_batch(3, [10.0, 20.0, 21.0]))
        completed = writer.finalize()

        assert completed.frame_count == 6
        assert service.read_segment(writer.session_id, 1) == (
            frame(3, normalized_timestamp=10.0),
            frame(4, normalized_timestamp=20.0),
            frame(5, normalized_timestamp=21.0),
        )


def test_equal_consecutive_timestamps_stay_legal_within_a_batch(tmp_path: Path) -> None:
    """Two frames may share one clock tick; equality must never be refused."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=64)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(timed_batch(0, [7.5, 7.5, 7.5]))
        completed = writer.finalize()

        assert completed.frame_count == 3
        assert completed.first_timestamp == 7.5
        assert completed.last_timestamp == 7.5
        assert service.read_segment(writer.session_id, 0) == tuple(
            frame(sequence, normalized_timestamp=7.5) for sequence in range(3)
        )


def test_equal_timestamps_across_a_batch_boundary_stay_legal(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=64)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(timed_batch(0, [4.0, 4.0]))
        writer.append(timed_batch(2, [4.0, 4.5]))
        completed = writer.finalize()

        assert completed.frame_count == 4
        assert service.read_segment(writer.session_id, 0) == (
            frame(0, normalized_timestamp=4.0),
            frame(1, normalized_timestamp=4.0),
            frame(2, normalized_timestamp=4.0),
            frame(3, normalized_timestamp=4.5),
        )


def test_every_adjacent_pair_read_back_in_sequence_order_is_non_descending(
    tmp_path: Path,
) -> None:
    """The whole persisted session, not just each batch, stays non-descending."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=3)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(timed_batch(0, [0.0, 0.0, 1.0]))
        writer.append(timed_batch(3, [1.0, 1.0, 2.0]))
        writer.append(timed_batch(6, [2.0, 9.0]))
        writer.finalize()

        observed = [
            frame.normalized_timestamp
            for segment in service.list_segments(writer.session_id)
            for frame in service.read_segment(writer.session_id, segment.segment_index)
        ]

        assert observed == [0.0, 0.0, 1.0, 1.0, 1.0, 2.0, 2.0, 9.0]
        assert all(later >= earlier for earlier, later in pairwise(observed))
