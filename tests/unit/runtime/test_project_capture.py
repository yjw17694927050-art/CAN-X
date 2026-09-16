"""Project-backed capture control flow in :class:`RuntimeService`.

These tests pin the wiring the V0.2-04 increment adds: a capture that names a
CAN-X project records into a real data session, an aborted startup never leaves
an ``ACTIVE`` orphan, and a recorder failure while the archive path is saturated
degrades capture without stopping it or claiming a completed recording.
"""

import asyncio
from pathlib import Path

import pytest
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService
from canx.devices.virtual import VirtualAdapterConfig
from canx.domain.batch import FrameBatch
from canx.project.service import ProjectHandle, ProjectService
from canx.recorder.project_recorder import ProjectRecorder
from canx.runtime.errors import CaptureConfigurationError
from canx.runtime.service import CaptureSessionState, RuntimeService

CAPTURE_CONFIG = VirtualAdapterConfig(rate_hz=2_000, seed=3)


def project(tmp_path: Path, *, name: str = "vehicle.canx") -> ProjectHandle:
    return ProjectService().create(tmp_path / name, display_name="Vehicle A")


def sessions(root: Path) -> tuple:
    return DataSessionService(root).list_sessions()


def active_sessions(root: Path) -> list:
    return [session for session in sessions(root) if session.state is DataSessionState.ACTIVE]


async def test_a_project_capture_creates_one_data_session_bound_to_the_stream(
    tmp_path: Path,
) -> None:
    with project(tmp_path) as handle:
        service = RuntimeService(project_max_frames_per_segment=16)

        stream_id = await service.start_capture(
            CAPTURE_CONFIG, batch_size=10, project_path=handle.root
        )
        session_id = service.data_session_id
        await asyncio.sleep(0.1)
        await service.stop_capture()

        assert session_id is not None
        stored = DataSessionService(handle.root).get_session(session_id)
        assert stored.state is DataSessionState.COMPLETED
        assert stored.stream_id == stream_id
        assert stored.project_id == handle.project_id
        assert stored.frame_count > 0
        assert stored.segment_count > 0
        assert service.data_session_id is None
        assert service.capture_state is CaptureSessionState.IDLE


async def test_a_capture_without_a_project_reports_no_data_session(tmp_path: Path) -> None:
    service = RuntimeService()

    await service.start_capture(CAPTURE_CONFIG, batch_size=10)
    try:
        assert service.data_session_id is None
        assert service.capture_state is CaptureSessionState.RUNNING
    finally:
        await service.stop_capture()


async def test_a_project_capture_does_not_depend_on_a_stream_client(tmp_path: Path) -> None:
    """Recorder and UI stream stay siblings: no broker client is even opened."""
    with project(tmp_path) as handle:
        service = RuntimeService(project_max_frames_per_segment=64)
        await service.start_capture(CAPTURE_CONFIG, batch_size=10, project_path=handle.root)
        session_id = service.data_session_id
        await asyncio.sleep(0.1)
        await service.stop_capture()

        metrics = service.metrics_snapshot()
        assert session_id is not None
        stored = DataSessionService(handle.root).get_session(session_id)
        assert stored.state is DataSessionState.COMPLETED
        assert stored.frame_count == metrics.captured_frames == metrics.recorded_frames
        assert metrics.dropped_frames == 0
        assert metrics.sequence_gaps == 0


async def test_the_realtime_stream_keeps_flowing_during_a_project_capture(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = RuntimeService(project_max_frames_per_segment=64)
        async with service.broker.subscribe() as stream:
            await service.start_capture(
                CAPTURE_CONFIG, batch_size=10, project_path=handle.root
            )
            try:
                batch = await asyncio.wait_for(stream.get(), timeout=2)
                assert batch.frame_count > 0
            finally:
                await service.stop_capture()


async def test_recording_targets_are_mutually_exclusive(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = RuntimeService()

        with pytest.raises(CaptureConfigurationError) as info:
            await service.start_capture(
                CAPTURE_CONFIG,
                recording_path=tmp_path / "capture.canxmsg",
                project_path=handle.root,
            )

        assert info.value.code == "capture.recording_target_conflict"
        assert info.value.recoverable is True
        assert info.value.source == "runtime"
        assert service.has_session is False
        assert sessions(handle.root) == ()
        assert not (tmp_path / "capture.canxmsg").exists()


async def test_an_invalid_project_target_is_rejected_without_creating_anything(
    tmp_path: Path,
) -> None:
    plain_directory = tmp_path / "not-a-project"
    plain_directory.mkdir()
    service = RuntimeService()

    with pytest.raises(CaptureConfigurationError) as info:
        await service.start_capture(CAPTURE_CONFIG, project_path=plain_directory)

    assert info.value.code == "capture.invalid_project"
    assert info.value.details["cause"] == "project.manifest_missing"
    assert service.has_session is False
    assert service.capture_state is CaptureSessionState.IDLE
    # A rejected target must never be turned into a project.
    assert sorted(path.name for path in plain_directory.iterdir()) == []


async def test_a_missing_project_path_is_a_structured_error(tmp_path: Path) -> None:
    service = RuntimeService()

    with pytest.raises(CaptureConfigurationError) as info:
        await service.start_capture(CAPTURE_CONFIG, project_path=tmp_path / "absent.canx")

    assert info.value.code == "capture.invalid_project"
    assert info.value.details["cause"] == "project.not_found"
    assert not (tmp_path / "absent.canx").exists()


async def test_a_startup_failure_after_the_session_was_created_leaves_it_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case B: an aborted startup must never leave an ``ACTIVE`` orphan."""
    import canx.runtime.service as service_module

    class BrokenAdapter:
        def __init__(self, config: VirtualAdapterConfig) -> None:
            self._config = config

        async def open(self) -> None:
            raise OSError("adapter could not be opened")

        async def close(self) -> None:
            return None

    with project(tmp_path) as handle:
        service = RuntimeService(project_max_frames_per_segment=16)
        monkeypatch.setattr(service_module, "VirtualAdapter", BrokenAdapter)

        with pytest.raises(OSError):
            await service.start_capture(CAPTURE_CONFIG, batch_size=10, project_path=handle.root)

        assert service.has_session is False
        assert service.capture_active is False
        assert service.data_session_id is None
        assert service.failure is not None
        assert service.failure.code == "recorder.open_failed"
        assert service.failure.context["error_type"] == "OSError"
        assert service.capture_state is CaptureSessionState.FAILED

        monkeypatch.undo()

        recorded = sessions(handle.root)
        assert [session.state for session in recorded] == [DataSessionState.FAILED]
        assert active_sessions(handle.root) == []
        assert service._consumer_tasks == ()
        assert service._recorder_cleanup_task is None

        # The runtime stays usable after the abort.
        await service.start_capture(CAPTURE_CONFIG, batch_size=10, project_path=handle.root)
        assert service.capture_state is CaptureSessionState.RUNNING
        await service.stop_capture()
        assert [session.state for session in sessions(handle.root)] == [
            DataSessionState.FAILED,
            DataSessionState.COMPLETED,
        ]


async def test_repeated_project_captures_create_independent_sessions(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = RuntimeService(project_max_frames_per_segment=16)
        session_ids: list[str | None] = []
        for _ in range(2):
            await service.start_capture(
                CAPTURE_CONFIG, batch_size=10, project_path=handle.root
            )
            session_ids.append(service.data_session_id)
            await asyncio.sleep(0.05)
            await service.stop_capture()

        assert session_ids[0] is not None
        assert session_ids[0] != session_ids[1]
        recorded = sessions(handle.root)
        assert {session.session_id for session in recorded} == set(session_ids)
        assert all(session.state is DataSessionState.COMPLETED for session in recorded)


async def test_stopping_a_project_capture_twice_is_safe(tmp_path: Path) -> None:
    with project(tmp_path) as handle:
        service = RuntimeService(project_max_frames_per_segment=16)
        await service.start_capture(CAPTURE_CONFIG, batch_size=10, project_path=handle.root)
        session_id = service.data_session_id
        await asyncio.sleep(0.05)

        await service.stop_capture()
        await service.stop_capture()

        assert session_id is not None
        stored = DataSessionService(handle.root).get_session(session_id)
        assert stored.state is DataSessionState.COMPLETED
        assert service.has_session is False


class BlockedProjectRecorder(ProjectRecorder):
    """A project recorder that never finishes its first append."""

    def __init__(self, *, max_frames_per_segment: int) -> None:
        super().__init__(max_frames_per_segment=max_frames_per_segment)
        self.append_started = asyncio.Event()
        self.release = asyncio.Event()

    async def append(self, batch: FrameBatch) -> None:
        self.append_started.set()
        await self.release.wait()
        await super().append(batch)


async def test_saturated_archive_fails_the_project_session_without_stopping_capture(
    tmp_path: Path,
) -> None:
    """Case D: recorder saturation is explicit, bounded and never ``COMPLETED``."""
    with project(tmp_path) as handle:
        recorder = BlockedProjectRecorder(max_frames_per_segment=4)
        service = RuntimeService(
            project_recorder_factory=lambda _limit: recorder,
            archive_capacity=4,
            archive_publish_timeout_seconds=0.05,
            recorder_cleanup_timeout_seconds=0.05,
        )
        async with service.broker.subscribe() as stream:
            await service.start_capture(
                VirtualAdapterConfig(rate_hz=50_000), batch_size=1, project_path=handle.root
            )
            session_id = service.data_session_id
            try:
                await asyncio.wait_for(recorder.append_started.wait(), timeout=2)
                async with asyncio.timeout(2):
                    while service.capture_state is not CaptureSessionState.DEGRADED:
                        await asyncio.sleep(0.001)
                assert service.capture_active is True
                assert service.failure is not None
                assert service.failure.code == "recorder.backpressure"
                assert service.failure.context["data_session_id"] == session_id
                # The sibling stream keeps delivering during the degradation.
                streamed = await asyncio.wait_for(stream.get(), timeout=2)
                assert streamed.frame_count > 0
            finally:
                await asyncio.wait_for(service.stop_capture(), timeout=2)

        assert service.has_session is False
        assert service.capture_state is CaptureSessionState.FAILED
        assert service._consumer_tasks == ()

        assert session_id is not None
        stored = DataSessionService(handle.root).get_session(session_id)
        assert stored.state is DataSessionState.FAILED
        assert stored.frame_count == stored.segment_count == 0
        assert DataSessionService(handle.root).inspect_integrity().clean is True


@pytest.mark.parametrize("value", [0, -1, True])
def test_an_unusable_project_segment_threshold_is_rejected_before_start(value: object) -> None:
    with pytest.raises(ValueError):
        RuntimeService(project_max_frames_per_segment=value)  # type: ignore[arg-type]


async def test_a_project_that_cannot_open_a_session_is_a_structured_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A storage fault at start is diagnosable, never an opaque server error."""
    import canx.data.session as session_module
    from canx.data.errors import DataStorageError

    def fail_start(self: DataSessionService, *, stream_id: str) -> object:
        raise DataStorageError(
            "The project database is missing.", code="data.project_database_missing"
        )

    with project(tmp_path) as handle:
        service = RuntimeService()
        monkeypatch.setattr(session_module.DataSessionService, "start", fail_start)

        with pytest.raises(CaptureConfigurationError) as info:
            await service.start_capture(CAPTURE_CONFIG, project_path=handle.root)

        assert info.value.code == "capture.session_start_failed"
        assert info.value.recoverable is True
        assert info.value.source == "runtime"
        assert info.value.details["cause"] == "data.project_database_missing"
        assert not isinstance(info.value, DataStorageError)
        assert service.has_session is False
        assert service.data_session_id is None
        assert service.capture_state is CaptureSessionState.FAILED
        assert DataSessionService(handle.root).list_sessions() == ()
