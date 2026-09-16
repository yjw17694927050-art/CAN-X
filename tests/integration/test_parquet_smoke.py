"""Volume smoke for the Parquet segment path.

This is a scale *smoke*, not a benchmark: it proves that a realistic multi-segment
volume writes, closes, reopens, and validates. It makes no large-scale claim —
the 10-50 GB target stays NOT VERIFIED until a separate benchmark exists.
"""

import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectService

STREAM_ID = "smoke-stream"
TOTAL_FRAMES = 50_000
FRAMES_PER_SEGMENT = 5_000
APPEND_BATCH_SIZE = 1_000


@dataclass(frozen=True, slots=True)
class SmokeOutcome:
    frames: int
    segments: int
    duration_seconds: float


def frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=0x500,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=4,
        data=bytes([sequence % 256, (sequence >> 8) % 256, 0x50, 0x5F]),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=5_000.0 + sequence,
        normalized_timestamp=sequence / 1_000.0,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def write_smoke_session(project_root: Path) -> tuple[str, SmokeOutcome]:
    """Write ``TOTAL_FRAMES`` frames in bounded batches; return the session id."""
    service = DataSessionService(project_root, max_frames_per_segment=FRAMES_PER_SEGMENT)
    writer = service.start(stream_id=STREAM_ID)
    started = time.perf_counter()
    for start in range(0, TOTAL_FRAMES, APPEND_BATCH_SIZE):
        size = min(APPEND_BATCH_SIZE, TOTAL_FRAMES - start)
        writer.append(
            FrameBatch.create(
                stream_id=STREAM_ID,
                frames=[frame(sequence) for sequence in range(start, start + size)],
            )
        )
    completed = writer.finalize()
    duration = time.perf_counter() - started
    assert completed.state is DataSessionState.COMPLETED
    return writer.session_id, SmokeOutcome(
        frames=completed.frame_count,
        segments=completed.segment_count,
        duration_seconds=duration,
    )


def test_fifty_thousand_frames_write_reopen_and_validate(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Smoke Project")

    session_id, outcome = write_smoke_session(handle.root)
    handle.close()

    assert outcome.frames == TOTAL_FRAMES
    assert outcome.segments == TOTAL_FRAMES // FRAMES_PER_SEGMENT == 10

    with ProjectService().open(root) as reopened:
        service = DataSessionService(reopened.root)
        restored = service.get_session(session_id)
        segments = service.list_segments(session_id)

        assert restored.frame_count == TOTAL_FRAMES
        assert restored.segment_count == 10
        assert restored.first_sequence == 0
        assert restored.last_sequence == TOTAL_FRAMES - 1
        assert sum(segment.frame_count for segment in segments) == TOTAL_FRAMES
        assert all(
            (reopened.root / segment.relative_path).stat().st_size == segment.byte_size
            for segment in segments
        )

        for index in (0, len(segments) // 2, len(segments) - 1):
            restored_frames = service.read_segment(session_id, index)
            assert len(restored_frames) == segments[index].frame_count
            assert restored_frames[0].sequence == segments[index].first_sequence
            assert restored_frames[-1].sequence == segments[index].last_sequence

        assert service.inspect_integrity().clean is True


def test_the_smoke_volume_stays_bounded_per_segment(tmp_path: Path) -> None:
    root = tmp_path / "vehicle.canx"
    handle = ProjectService().create(root, display_name="Smoke Project")

    session_id, outcome = write_smoke_session(handle.root)

    segments = DataSessionService(handle.root).list_segments(session_id)
    handle.close()

    assert outcome.frames == TOTAL_FRAMES
    assert max(segment.frame_count for segment in segments) == FRAMES_PER_SEGMENT
    assert outcome.duration_seconds < 120, "the smoke volume took far longer than expected"


@pytest.mark.parametrize("frames", [1, APPEND_BATCH_SIZE, FRAMES_PER_SEGMENT])
def test_small_volumes_round_trip_exactly(tmp_path: Path, frames: int) -> None:
    with ProjectService().create(
        tmp_path / "vehicle.canx", display_name="Smoke Project"
    ) as handle:
        service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
        writer = service.start(stream_id=STREAM_ID)
        writer.append(
            FrameBatch.create(
                stream_id=STREAM_ID, frames=[frame(sequence) for sequence in range(frames)]
            )
        )
        completed = writer.finalize()

        assert completed.frame_count == frames
        restored: list[Frame] = []
        for segment in service.list_segments(writer.session_id):
            restored.extend(service.read_segment(writer.session_id, segment.segment_index))
        assert restored == [frame(sequence) for sequence in range(frames)]
