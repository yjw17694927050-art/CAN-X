"""A DataSessionWriter refuses any batch that does not directly continue its own sequence.

The writer owns this invariant independently: ``ProjectRecorder`` already refuses
a gap with ``recorder.sequence_gap``, but the writer must be correct on its own so
a caller with no recorder in front of it can never persist a session that
silently skips a sequence number.

The rule is exactly one step: once a session has appended frames, the next
batch's ``first_sequence`` must equal the previous ``last_sequence`` + 1. The
*first* batch of a session is exempt — a session may legally start at any
sequence — and a regression or an overlap stays refused with the existing typed
``data.integrity.sequence_regression`` code.
"""

from pathlib import Path

import pytest
from canx.data.errors import DataIntegrityError
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService

STREAM_ID = "continuity-stream"


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


def batch(start: int, count: int, *, stream_id: str = STREAM_ID) -> FrameBatch:
    return FrameBatch.create(
        stream_id=stream_id,
        frames=[frame(sequence) for sequence in range(start, start + count)],
    )


def project(tmp_path: Path, *, name: str = "vehicle.canx") -> ProjectHandle:
    return ProjectService().create(tmp_path / name, display_name="Vehicle A")


@pytest.mark.parametrize(
    "start", [0, 1, 7, 4096, 2**31], ids=["zero", "one", "offset", "large", "very-large"]
)
def test_a_first_batch_may_start_at_any_legal_sequence(tmp_path: Path, start: int) -> None:
    """No fixed start is invented: the first batch only has to be contiguous."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=64)
        writer = service.start(stream_id=STREAM_ID)

        writer.append(batch(start, 3))
        completed = writer.finalize()

        assert completed.state is DataSessionState.COMPLETED
        assert (completed.first_sequence, completed.last_sequence) == (start, start + 2)
        assert service.read_segment(writer.session_id, 0) == tuple(
            frame(sequence) for sequence in range(start, start + 3)
        )


@pytest.mark.parametrize(
    ("previous_last_sequence", "next_first_sequence", "batch_size"),
    [(0, 1, 2), (9, 10, 3), (2**31, 2**31 + 1, 1)],
    ids=["from-zero", "mid-session", "very-large"],
)
def test_an_exact_continuation_is_accepted(
    tmp_path: Path,
    previous_last_sequence: int,
    next_first_sequence: int,
    batch_size: int,
) -> None:
    with project(tmp_path) as handle:
        start = previous_last_sequence
        service = DataSessionService(handle.root, max_frames_per_segment=64)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(start, 1))
        writer.append(batch(next_first_sequence, batch_size))

        completed = writer.finalize()

        assert completed.frame_count == 1 + batch_size
        assert completed.last_sequence == next_first_sequence + batch_size - 1


def test_a_multi_segment_session_that_continues_exactly_stays_accepted(tmp_path: Path) -> None:
    """The rule is checked at every batch boundary, including segment rollovers."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=2)
        writer = service.start(stream_id=STREAM_ID)

        writer.append(batch(10, 2))
        writer.append(batch(12, 3))
        completed = writer.finalize()

        assert [segment.segment_index for segment in service.list_segments(writer.session_id)] == [
            0,
            1,
            2,
        ]
        assert completed.frame_count == 5
        assert service.read_segment(writer.session_id, 0) == (frame(10), frame(11))
        assert service.read_segment(writer.session_id, 2) == (frame(14),)


@pytest.mark.parametrize(
    "gap_start", [4, 5, 100], ids=["one-frame-gap", "two-frame-gap", "large-gap"]
)
def test_a_positive_sequence_gap_is_refused(tmp_path: Path, gap_start: int) -> None:
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root, max_frames_per_segment=64).start(
            stream_id=STREAM_ID
        )
        writer.append(batch(0, 3))

        with pytest.raises(DataIntegrityError) as info:
            writer.append(batch(gap_start, 2))

        assert info.value.code == "data.integrity.sequence_regression"
        assert info.value.details["previous_last_sequence"] == 2
        assert info.value.details["batch_first_sequence"] == gap_start
        assert info.value.details["expected_first_sequence"] == 3
        assert writer.state is DataSessionState.ACTIVE
        assert writer.frame_count == 0
        assert writer.segment_count == 0


def test_a_gap_is_refused_while_the_previous_frames_are_still_buffered(tmp_path: Path) -> None:
    """The refusal happens before a single buffered frame is committed."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=64)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 3))
        assert writer.frame_count == 0

        with pytest.raises(DataIntegrityError) as info:
            writer.append(batch(10, 1))

        assert info.value.code == "data.integrity.sequence_regression"
        assert service.list_segments(writer.session_id) == ()
        assert writer.frame_count == 0

        writer.append(batch(3, 2))
        completed = writer.finalize()

        assert completed.frame_count == 5
        assert service.read_segment(writer.session_id, 0) == tuple(
            frame(sequence) for sequence in range(5)
        )


def test_a_gap_across_a_committed_segment_boundary_is_refused(tmp_path: Path) -> None:
    """The boundary at a committed segment does not reset the continuity rule."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=3)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(batch(0, 3))
        assert writer.segment_count == 1

        with pytest.raises(DataIntegrityError) as info:
            writer.append(batch(4, 3))

        assert info.value.code == "data.integrity.sequence_regression"
        assert info.value.details["previous_last_sequence"] == 2
        assert writer.segment_count == 1
        assert writer.frame_count == 3
        assert [
            segment.segment_index for segment in service.list_segments(writer.session_id)
        ] == [0]
        assert service.inspect_integrity().clean is True

        writer.append(batch(3, 1))
        writer.finalize()

        assert service.read_segment(writer.session_id, 0) == (frame(0), frame(1), frame(2))
        assert service.read_segment(writer.session_id, 1) == (frame(3),)


def test_a_refused_gap_leaves_the_writer_able_to_continue(tmp_path: Path) -> None:
    """A refusal is not terminal: the session keeps its sequence and stays usable."""
    with project(tmp_path) as handle:
        writer = DataSessionService(handle.root, max_frames_per_segment=64).start(
            stream_id=STREAM_ID
        )
        writer.append(batch(5, 2))

        with pytest.raises(DataIntegrityError):
            writer.append(batch(9, 1))

        writer.append(batch(7, 2))
        completed = writer.finalize()

        assert completed.first_sequence == 5
        assert completed.last_sequence == 8
        assert completed.frame_count == 4


def test_the_writer_rule_is_exactly_one_step_ahead(tmp_path: Path) -> None:
    """A table of accepted and refused batch starts, one session per row."""
    accepted = [(0, 1), (5, 6), (2**31, 2**31 + 1)]
    refused = [(0, 0), (0, 2), (5, 7)]
    for previous, candidate in accepted:
        with project(tmp_path, name=f"accepted-{previous}-{candidate}.canx") as handle:
            writer = DataSessionService(handle.root, max_frames_per_segment=64).start(
                stream_id=STREAM_ID
            )
            writer.append(batch(previous, 1))
            writer.append(batch(candidate, 1))
            assert writer.session.state is DataSessionState.ACTIVE
    for previous, candidate in refused:
        with project(tmp_path, name=f"refused-{previous}-{candidate}.canx") as handle:
            writer = DataSessionService(handle.root, max_frames_per_segment=64).start(
                stream_id=STREAM_ID
            )
            writer.append(batch(previous, 1))
            with pytest.raises(DataIntegrityError) as info:
                writer.append(batch(candidate, 1))
            assert info.value.code == "data.integrity.sequence_regression"
            assert info.value.details["previous_last_sequence"] == previous
            assert info.value.details["expected_first_sequence"] == previous + 1
