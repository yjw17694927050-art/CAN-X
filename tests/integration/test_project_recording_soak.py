"""Bounded soak: a sustained project-backed recording must stay lossless.

Recorder and capture are the performance-sensitive path, so V0.2-04 cannot be
accepted on functional tests alone. This runs one bounded (a couple of seconds)
project-backed recording at a high synthetic rate and checks the properties that
degrade silently under load: nothing lost between capture and Parquet, bounded
segment size, bounded queues, no invented ``COMPLETED``, and a query round-trip
that returns exactly what was recorded.
"""

import asyncio
import time
from pathlib import Path

from canx.data.model import DataSessionState
from canx.data.session import DataSessionService
from canx.devices.virtual import VirtualAdapterConfig
from canx.project.service import ProjectHandle, ProjectService
from canx.query.model import FrameFilter
from canx.query.service import QueryService
from canx.runtime.service import RuntimeService

RATE_HZ = 20_000.0
DURATION_SECONDS = 2.0
BATCH_SIZE = 250
SEGMENT_LIMIT = 4_096
HISTORY_CAPACITY = 5_000


def project(tmp_path: Path) -> ProjectHandle:
    return ProjectService().create(tmp_path / "soak.canx", display_name="Soak")


async def test_a_sustained_project_recording_stays_lossless_and_bounded(
    tmp_path: Path, capsys
) -> None:
    with project(tmp_path) as handle:
        service = RuntimeService(
            history_capacity=HISTORY_CAPACITY,
            project_max_frames_per_segment=SEGMENT_LIMIT,
        )
        started = time.perf_counter()
        stream_id = await service.start_capture(
            VirtualAdapterConfig(rate_hz=RATE_HZ, seed=23),
            batch_size=BATCH_SIZE,
            project_path=handle.root,
        )
        session_id = service.data_session_id
        await asyncio.sleep(DURATION_SECONDS)
        await service.stop_capture()
        elapsed = time.perf_counter() - started
        metrics = service.metrics_snapshot()

        assert session_id is not None
        data = DataSessionService(handle.root)
        session = data.get_session(session_id)
        segments = data.list_segments(session_id)
        summary = QueryService(handle.root).summarize_frames(FrameFilter(session_id=session_id))

        with capsys.disabled():
            print(
                "\nproject-backend soak:"
                f" rate={RATE_HZ:.0f}Hz duration={DURATION_SECONDS}s"
                f" generated={metrics.generated_frames}"
                f" captured={metrics.captured_frames}"
                f" persisted={session.frame_count}"
                f" segments={len(segments)}"
                f" max_segment={max((s.frame_count for s in segments), default=0)}"
                f" sequence_gaps={metrics.sequence_gaps}"
                f" recorder_failures={metrics.recorder_failures}"
                f" dropped={metrics.dropped_frames}"
                f" recorder_queue_peak={metrics.queue_peaks['recorder_queue_depth']}"
                f" elapsed={elapsed:.3f}s"
                f" state={session.state.value}"
            )

        # Data integrity: every captured frame reached a committed Parquet segment.
        assert session.state is DataSessionState.COMPLETED
        assert session.stream_id == stream_id
        assert session.frame_count > SEGMENT_LIMIT
        assert session.frame_count == metrics.captured_frames == metrics.recorded_frames
        assert metrics.recorder_failures == 0
        assert metrics.sequence_gaps == 0
        assert metrics.dropped_frames == 0

        # Boundedness: bounded segments, bounded queues, bounded runtime history.
        assert session.segment_count == len(segments)
        assert sum(segment.frame_count for segment in segments) == session.frame_count
        assert max(segment.frame_count for segment in segments) <= SEGMENT_LIMIT
        assert metrics.queue_peaks["recorder_queue_depth"] <= 1_000
        assert metrics.recorder_queue_depth == metrics.ingress_queue_depth == 0
        assert len(service.frames()) <= HISTORY_CAPACITY
        assert data.inspect_integrity().clean is True

        # Query round-trip over the whole recording.
        assert summary.matching_frame_count == session.frame_count
        assert summary.first_sequence == 0
        assert summary.last_sequence == session.frame_count - 1

        throughput = session.frame_count / elapsed
        assert throughput > 0
