"""Boundedness: a long session must never hold the whole session in memory.

The design claim is that frames live only in the active segment buffer and are
released as soon as the threshold is reached. These tests check the observable
consequence of that claim: the durable segment count keeps pace with the frames
appended, and no segment ever exceeds the configured threshold.
"""

from pathlib import Path

from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService

STREAM_ID = "bounded-stream"
FRAMES_PER_SEGMENT = 100
TOTAL_FRAMES = 10_000


def frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x400,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=2,
        data=bytes([sequence % 256, 0x40]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=4_000.0 + sequence,
        normalized_timestamp=float(sequence),
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def batch(start: int, count: int, *, stream_id: str = STREAM_ID) -> FrameBatch:
    return FrameBatch.create(
        stream_id=stream_id, frames=[frame(sequence) for sequence in range(start, start + count)]
    )


def project(tmp_path: Path) -> ProjectHandle:
    return ProjectService().create(tmp_path / "vehicle.canx", display_name="Vehicle A")


def test_a_long_session_commits_segments_in_step_with_the_threshold(tmp_path: Path) -> None:
    """Every full threshold is flushed immediately, so nothing accumulates."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
        writer = service.start(stream_id=STREAM_ID)

        for start in range(0, TOTAL_FRAMES, FRAMES_PER_SEGMENT):
            writer.append(batch(start, FRAMES_PER_SEGMENT))
            committed = service.list_segments(writer.session_id)
            expected = (start + FRAMES_PER_SEGMENT) // FRAMES_PER_SEGMENT
            assert len(committed) == expected, f"flush did not keep pace at frame {start}"
            assert all(segment.frame_count == FRAMES_PER_SEGMENT for segment in committed)

        completed = writer.finalize()
        segments = service.list_segments(writer.session_id)

        assert completed.frame_count == TOTAL_FRAMES
        assert completed.segment_count == TOTAL_FRAMES // FRAMES_PER_SEGMENT
        assert len(segments) == TOTAL_FRAMES // FRAMES_PER_SEGMENT
        assert [segment.segment_index for segment in segments] == list(
            range(TOTAL_FRAMES // FRAMES_PER_SEGMENT)
        )
        assert sum(segment.frame_count for segment in segments) == TOTAL_FRAMES
        assert completed.first_sequence == 0
        assert completed.last_sequence == TOTAL_FRAMES - 1
        assert completed.first_timestamp == 0.0
        assert completed.last_timestamp == float(TOTAL_FRAMES - 1)


def test_no_segment_exceeds_the_configured_threshold(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
        writer = service.start(stream_id=STREAM_ID)
        for start in range(0, TOTAL_FRAMES, FRAMES_PER_SEGMENT):
            writer.append(batch(start, FRAMES_PER_SEGMENT))
        writer.finalize()

        segments = service.list_segments(writer.session_id)

        assert segments
        assert max(segment.frame_count for segment in segments) <= FRAMES_PER_SEGMENT


def test_segment_file_names_are_deterministic_and_ordered(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
        writer = service.start(stream_id=STREAM_ID)
        for start in range(0, FRAMES_PER_SEGMENT * 5, FRAMES_PER_SEGMENT):
            writer.append(batch(start, FRAMES_PER_SEGMENT))
        writer.finalize()

        segments = service.list_segments(writer.session_id)

        assert [segment.relative_path for segment in segments] == [
            f"data/sessions/{writer.session_id}/segments/{index:06d}.parquet"
            for index in range(5)
        ]


def test_many_small_batches_do_not_create_a_segment_each(tmp_path: Path) -> None:
    """Sub-threshold appends stay buffered; they must not become one file each."""
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
        writer = service.start(stream_id=STREAM_ID)

        for start in range(0, 50):
            writer.append(batch(start, 1))

        assert service.list_segments(writer.session_id) == ()
        completed = writer.finalize()
        assert completed.frame_count == 50
        assert completed.segment_count == 1
        assert service.list_segments(writer.session_id)[0].frame_count == 50


def test_one_oversized_batch_is_split_across_segments(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
        writer = service.start(stream_id=STREAM_ID)

        writer.append(batch(0, 350))

        segments = service.list_segments(writer.session_id)
        assert [segment.frame_count for segment in segments] == [100, 100, 100]
        assert service.get_session(writer.session_id).frame_count == 300

        completed = writer.finalize()
        assert completed.frame_count == 350
        assert completed.segment_count == 4
        assert service.list_segments(writer.session_id)[-1].frame_count == 50


def test_a_bounded_session_stays_consistent_end_to_end(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
        writer = service.start(stream_id=STREAM_ID)
        for start in range(0, 300, 50):
            writer.append(batch(start, 50))
        completed = writer.finalize()

        assert completed.frame_count == 300
        assert completed.segment_count == 3
        assert service.inspect_integrity().clean is True
        assert sum(
            len(service.read_segment(writer.session_id, segment.segment_index))
            for segment in service.list_segments(writer.session_id)
        ) == 300
