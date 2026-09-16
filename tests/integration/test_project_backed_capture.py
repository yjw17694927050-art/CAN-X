"""The V0.2-04 acceptance chain: live capture → project → Parquet → query.

These integration tests use the real Project, DataSession, Parquet and DuckDB
implementations with no mock in the path. They exist to prove four things that a
unit test cannot:

* a realtime capture becomes a ``COMPLETED`` data session whose frames are
  readable back through ``QueryService``, with the capture ``stream_id``
  preserved end to end and the sequence contiguous across every segment;
* each named failure (invalid project, aborted startup, a storage fault,
  archive saturation) leaves a session that is *not* ``COMPLETED`` and never an
  ``ACTIVE`` orphan, while keeping segments that really were committed;
* the sibling realtime stream keeps working throughout.
"""

import asyncio
from pathlib import Path

import pytest
from canx.data.errors import ParquetWriteError
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService, DataSessionWriter
from canx.devices.virtual import VirtualAdapterConfig
from canx.domain.batch import FrameBatch
from canx.project.service import ProjectHandle, ProjectService
from canx.query.model import FrameFilter, FrameQuery
from canx.query.service import QueryService
from canx.recorder.project_recorder import ProjectRecorder
from canx.runtime.errors import CaptureConfigurationError
from canx.runtime.service import CaptureSessionState, RuntimeService

SEGMENT_LIMIT = 32


def project(tmp_path: Path, *, name: str = "vehicle.canx") -> ProjectHandle:
    return ProjectService().create(tmp_path / name, display_name="Vehicle A")


def read_every_frame(query: QueryService, session_id: str, *, limit: int = 1000) -> list:
    """Page the whole session through the public bounded query API."""
    frames: list = []
    cursor: int | None = None
    while True:
        page = query.query_frames(
            FrameQuery(
                filter=FrameFilter(session_id=session_id), after_sequence=cursor, limit=limit
            )
        )
        frames.extend(page.frames)
        if not page.has_more:
            return frames
        cursor = page.next_after_sequence


async def test_a_realtime_capture_becomes_a_queryable_completed_session(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = RuntimeService(project_max_frames_per_segment=SEGMENT_LIMIT)
        async with service.broker.subscribe() as stream:
            stream_id = await service.start_capture(
                VirtualAdapterConfig(rate_hz=4_000, seed=11),
                batch_size=16,
                project_path=handle.root,
            )
            session_id = service.data_session_id
            # The sibling realtime path must stay live for the whole capture.
            first_batch = await asyncio.wait_for(stream.get(), timeout=3)
            await asyncio.sleep(0.5)
            last_batch = await asyncio.wait_for(stream.get(), timeout=3)
            await asyncio.wait_for(service.stop_capture(), timeout=10)
        metrics = service.metrics_snapshot()

        assert session_id is not None
        assert first_batch.frame_count > 0
        assert last_batch.first_sequence > first_batch.first_sequence

        data = DataSessionService(handle.root)
        session = data.get_session(session_id)
        segments = data.list_segments(session_id)

        # Normal stop → COMPLETED, and nothing was lost on the way.
        assert session.state is DataSessionState.COMPLETED
        assert session.ended_at is not None
        assert session.frame_count > SEGMENT_LIMIT * 2
        assert session.segment_count == len(segments) > 1
        assert session.stream_id == stream_id
        assert session.project_id == handle.project_id
        assert session.frame_count == metrics.captured_frames == metrics.recorded_frames
        assert metrics.recorder_failures == 0
        assert metrics.sequence_gaps == 0
        assert metrics.dropped_frames == 0
        assert data.inspect_integrity().clean is True

        # Bounded Parquet segments, in order, summing to the session aggregate.
        assert [segment.segment_index for segment in segments] == list(range(len(segments)))
        assert [segment.session_id for segment in segments] == [session_id] * len(segments)
        assert sum(segment.frame_count for segment in segments) == session.frame_count
        assert max(segment.frame_count for segment in segments) <= SEGMENT_LIMIT

        # The persisted capture is readable back through the query foundation.
        query = QueryService(handle.root)
        summary = query.summarize_frames(FrameFilter(session_id=session_id))
        assert summary.matching_frame_count == session.frame_count
        assert summary.first_sequence == 0
        assert summary.last_sequence == session.frame_count - 1

        frames = read_every_frame(query, session_id)
        assert len(frames) == session.frame_count
        assert [frame.sequence for frame in frames] == list(range(session.frame_count))
        assert all(frame.channel_id.startswith("can") for frame in frames)


async def test_case_a_an_invalid_project_target_creates_nothing(tmp_path: Path) -> None:
    plain_directory = tmp_path / "not-a-project"
    plain_directory.mkdir()
    service = RuntimeService()

    with pytest.raises(CaptureConfigurationError) as info:
        await service.start_capture(
            VirtualAdapterConfig(rate_hz=1_000), project_path=plain_directory
        )

    assert info.value.code == "capture.invalid_project"
    assert info.value.details["cause"] == "project.manifest_missing"
    assert service.has_session is False
    # No partial data session, and no directory masquerading as a project.
    assert sorted(path.name for path in plain_directory.iterdir()) == []


async def test_case_b_an_aborted_startup_leaves_no_active_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import canx.runtime.service as service_module

    class BrokenAdapter:
        def __init__(self, config: VirtualAdapterConfig) -> None:
            self._config = config

        async def open(self) -> None:
            raise OSError("adapter could not be opened")

        async def close(self) -> None:
            return None

    with project(tmp_path) as handle:
        service = RuntimeService(project_max_frames_per_segment=SEGMENT_LIMIT)
        monkeypatch.setattr(service_module, "VirtualAdapter", BrokenAdapter)

        with pytest.raises(OSError):
            await service.start_capture(
                VirtualAdapterConfig(rate_hz=1_000), batch_size=16, project_path=handle.root
            )
        monkeypatch.undo()

        data = DataSessionService(handle.root)
        sessions = data.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].state is DataSessionState.FAILED
        assert sessions[0].ended_at is None
        assert sessions[0].frame_count == 0
        assert not [s for s in sessions if s.state is DataSessionState.ACTIVE]
        assert data.inspect_integrity().clean is True
        assert service.capture_state is CaptureSessionState.FAILED


async def test_case_c_a_storage_fault_fails_the_session_but_keeps_committed_segments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import canx.data.session as session_module

    real_write = session_module.write_segment_file

    def fail_the_second_segment(
        path: Path,
        *,
        session_id: str,
        stream_id: str,
        segment_index: int,
        frames: object,
    ) -> int:
        if segment_index == 1:
            raise ParquetWriteError(
                "simulated disk fault", details={"segment_index": segment_index}
            )
        return real_write(
            path,
            session_id=session_id,
            stream_id=stream_id,
            segment_index=segment_index,
            frames=frames,  # type: ignore[arg-type]
        )

    with project(tmp_path) as handle:
        service = RuntimeService(project_max_frames_per_segment=SEGMENT_LIMIT)
        monkeypatch.setattr(session_module, "write_segment_file", fail_the_second_segment)
        async with service.broker.subscribe() as stream:
            await service.start_capture(
                VirtualAdapterConfig(rate_hz=8_000, seed=5),
                batch_size=16,
                project_path=handle.root,
            )
            session_id = service.data_session_id
            try:
                async with asyncio.timeout(5):
                    while service.capture_state is not CaptureSessionState.DEGRADED:
                        await asyncio.sleep(0.001)
                # Capture is degraded, not dead: the sibling stream still flows.
                streamed = await asyncio.wait_for(stream.get(), timeout=3)
                assert streamed.frame_count > 0
                assert service.capture_active is True
                assert service.failure is not None
                assert service.failure.code == "recorder.write_failed"
                assert service.failure.context["data_session_id"] == session_id
            finally:
                await asyncio.wait_for(service.stop_capture(), timeout=10)
        monkeypatch.undo()

        assert session_id is not None
        data = DataSessionService(handle.root)
        session = data.get_session(session_id)
        segments = data.list_segments(session_id)

        assert session.state is DataSessionState.FAILED
        assert session.ended_at is None
        # Exactly the first segment committed before the fault — nothing more is
        # claimed, and nothing that was committed is discarded.
        assert session.segment_count == len(segments) == 1
        assert session.frame_count == segments[0].frame_count == SEGMENT_LIMIT
        assert (handle.root / segments[0].relative_path).is_file()
        assert data.inspect_integrity().clean is True

        query = QueryService(handle.root)
        summary = query.summarize_frames(FrameFilter(session_id=session_id))
        assert summary.matching_frame_count == SEGMENT_LIMIT
        assert len(read_every_frame(query, session_id)) == SEGMENT_LIMIT


class SlowProjectRecorder(ProjectRecorder):
    """A project recorder whose disk write is slower than capture."""

    def __init__(self, *, max_frames_per_segment: int, delay_seconds: float) -> None:
        super().__init__(max_frames_per_segment=max_frames_per_segment)
        self._delay_seconds = delay_seconds

    async def append(self, batch: FrameBatch) -> None:
        await asyncio.sleep(self._delay_seconds)
        await super().append(batch)


async def test_case_d_archive_saturation_never_claims_a_completed_session(
    tmp_path: Path,
) -> None:
    with project(tmp_path) as handle:
        service = RuntimeService(
            project_recorder_factory=lambda limit: SlowProjectRecorder(
                max_frames_per_segment=limit, delay_seconds=0.4
            ),
            archive_capacity=4,
            archive_publish_timeout_seconds=0.05,
            recorder_cleanup_timeout_seconds=0.5,
        )
        async with service.broker.subscribe() as stream:
            await service.start_capture(
                VirtualAdapterConfig(rate_hz=50_000, seed=9),
                batch_size=1,
                project_path=handle.root,
            )
            session_id = service.data_session_id
            try:
                async with asyncio.timeout(5):
                    while service.capture_state is not CaptureSessionState.DEGRADED:
                        await asyncio.sleep(0.001)
                assert service.capture_active is True
                assert service.failure is not None
                assert service.failure.code == "recorder.backpressure"
                assert service.failure.context["capacity"] == 4
                # No deadlock: the lossy sibling stream keeps advancing.
                streamed = await asyncio.wait_for(stream.get(), timeout=3)
                assert streamed.frame_count > 0
            finally:
                # Stop stays bounded even with an unusable recorder.
                await asyncio.wait_for(service.stop_capture(), timeout=5)

        assert service.has_session is False
        assert service.capture_state is CaptureSessionState.FAILED
        assert session_id is not None
        data = DataSessionService(handle.root)
        session = data.get_session(session_id)
        assert session.state is DataSessionState.FAILED
        assert session.segment_count == len(data.list_segments(session_id))
        assert data.inspect_integrity().clean is True


async def test_case_e_a_normal_stop_flushes_the_partial_segment(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = RuntimeService(project_max_frames_per_segment=SEGMENT_LIMIT)
        await service.start_capture(
            VirtualAdapterConfig(rate_hz=6_000, seed=13),
            batch_size=16,
            project_path=handle.root,
        )
        session_id = service.data_session_id
        await asyncio.sleep(0.5)
        await service.stop_capture()

        assert session_id is not None
        data = DataSessionService(handle.root)
        session = data.get_session(session_id)
        segments = data.list_segments(session_id)

        assert session.state is DataSessionState.COMPLETED
        assert session.segment_count == len(segments)
        # The partial tail is flushed by finalize and is the only short segment.
        assert segments[-1].frame_count <= SEGMENT_LIMIT
        assert sum(segment.frame_count for segment in segments) == session.frame_count
        assert segments[-1].last_sequence == session.last_sequence

        query = QueryService(handle.root)
        assert query.summarize_frames(
            FrameFilter(session_id=session_id)
        ).matching_frame_count == session.frame_count
        frames = read_every_frame(query, session_id)
        assert [frame.sequence for frame in frames] == list(range(session.frame_count))


async def test_a_recorder_write_fault_does_not_stall_the_sibling_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every append fails: capture still streams and the session never completes."""
    with project(tmp_path) as handle:
        service = RuntimeService(project_max_frames_per_segment=SEGMENT_LIMIT)

        def always_fail(self: DataSessionWriter, batch: FrameBatch) -> None:
            raise ParquetWriteError("simulated permanent disk fault")

        monkeypatch.setattr(DataSessionWriter, "append", always_fail)
        async with service.broker.subscribe() as stream:
            await service.start_capture(
                VirtualAdapterConfig(rate_hz=5_000, seed=17),
                batch_size=16,
                project_path=handle.root,
            )
            session_id = service.data_session_id
            try:
                batches = [await asyncio.wait_for(stream.get(), timeout=3) for _ in range(5)]
            finally:
                await asyncio.wait_for(service.stop_capture(), timeout=10)
        monkeypatch.undo()

        assert sum(batch.frame_count for batch in batches) > 0
        assert session_id is not None
        data = DataSessionService(handle.root)
        session = data.get_session(session_id)
        assert session.state is DataSessionState.FAILED
        assert session.frame_count == session.segment_count == 0
        assert data.list_segments(session_id) == ()
        assert data.inspect_integrity().clean is True
