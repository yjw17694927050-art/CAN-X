"""V0.2-04-FINAL — a cleanup timeout must never be recorded as ``COMPLETED``.

The runtime bounds ``recorder.stop()`` with a cleanup deadline. When that
deadline expires, the awaiting coroutine is cancelled — but ``asyncio`` cannot
cancel a worker thread, so the synchronous ``DataSessionWriter.finalize()`` it
had already started keeps running and can still commit the session as
``COMPLETED``. The runtime would then report ``recorder.cleanup_timeout`` for a
session the database calls ``COMPLETED``.

These tests reproduce that window deterministically — the worker is blocked on a
``threading.Event`` rather than raced against a sleep — and pin the terminal
outcome: once the deadline is declared, ``COMPLETED`` is no longer reachable.
"""

import asyncio
import threading
from pathlib import Path

import pytest
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService, DataSessionWriter
from canx.devices.virtual import VirtualAdapterConfig
from canx.project.service import ProjectHandle, ProjectService
from canx.runtime.errors import CaptureConfigurationError
from canx.runtime.service import CaptureSessionState, RuntimeService

CLEANUP_TIMEOUT = 0.8
#: Small enough that the archive drain finishes well inside CLEANUP_TIMEOUT, so
#: the deadline lands on the *finalize* rather than on the drain.
SEGMENT_LIMIT = 64
BATCH_SIZE = 32
RATE_HZ = 1_000.0


def project(tmp_path: Path) -> ProjectHandle:
    return ProjectService().create(tmp_path / "vehicle.canx", display_name="Vehicle A")


class BlockingFinalize:
    """Block ``DataSessionWriter.<target>`` on explicit events (a deterministic seam).

    The replacement is a plain function so the descriptor protocol still binds
    the writer as its first argument; the events make the window reproducible
    instead of racing it against a sleep.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, target: str = "flush_pending") -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        real = getattr(DataSessionWriter, target)

        def blocking(writer: DataSessionWriter) -> object:
            self.entered.set()
            self.release.wait(timeout=30)
            try:
                return real(writer)
            finally:
                self.finished.set()

        monkeypatch.setattr(DataSessionWriter, target, blocking)

    async def wait_entered(self) -> None:
        entered = await asyncio.wait_for(asyncio.to_thread(self.entered.wait, 15), timeout=20)
        assert entered, "the finalization worker never entered its blocking phase"

    async def wait_finished(self) -> None:
        done = await asyncio.wait_for(asyncio.to_thread(self.finished.wait, 20), timeout=25)
        assert done, "the finalization worker never finished"


async def _run_capture_until_blocked(
    service: RuntimeService, handle: ProjectHandle, block: BlockingFinalize
) -> str:
    await service.start_capture(
        VirtualAdapterConfig(rate_hz=RATE_HZ, seed=5),
        batch_size=BATCH_SIZE,
        project_path=handle.root,
    )
    session_id = service.data_session_id
    assert session_id is not None
    await asyncio.sleep(0.25)
    return session_id


async def test_a_cleanup_timeout_can_never_become_a_completed_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test A/D: declared timeout, then the worker resumes — still not COMPLETED."""
    with project(tmp_path) as handle:
        service = RuntimeService(
            project_max_frames_per_segment=SEGMENT_LIMIT,
            recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT,
        )
        block = BlockingFinalize(monkeypatch)
        session_id = await _run_capture_until_blocked(service, handle, block)

        stop_task = asyncio.create_task(service.stop_capture())
        await block.wait_entered()

        # The deadline must bound the stop even though the worker is stuck.
        await asyncio.wait_for(stop_task, timeout=10)

        assert service.failure is not None
        assert service.failure.code == "recorder.cleanup_timeout", (
            f"code={service.failure.code} message={service.failure.message!r} "
            f"context={service.failure.context}"
        )
        assert service.capture_state is CaptureSessionState.FAILED
        assert service.has_session is False

        # Now let the abandoned worker resume and finish its disk write.
        block.release.set()
        await block.wait_finished()
        monkeypatch.undo()
        await asyncio.wait_for(asyncio.to_thread(service.wait_for_finalization, 10), timeout=15)

        stored = DataSessionService(handle.root).get_session(session_id)
        assert stored.state is DataSessionState.FAILED
        assert stored.state is not DataSessionState.COMPLETED


async def test_a_cleanup_timeout_keeps_the_segments_that_were_already_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test B: a partial tail survives the timeout; nothing committed is lost."""
    with project(tmp_path) as handle:
        service = RuntimeService(
            project_max_frames_per_segment=SEGMENT_LIMIT,
            recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT,
        )
        block = BlockingFinalize(monkeypatch, target="flush_pending")
        session_id = await _run_capture_until_blocked(service, handle, block)

        stop_task = asyncio.create_task(service.stop_capture())
        await block.wait_entered()
        await asyncio.wait_for(stop_task, timeout=10)
        assert service.failure is not None, "no recorder failure was recorded"
        assert service.failure.code == "recorder.cleanup_timeout", (
            f"got {service.failure.code}: {service.failure.message!r}"
        )
        block.release.set()
        await block.wait_finished()
        monkeypatch.undo()
        # The worker's terminal decision runs right after its flush returns; the
        # durable outcome is already decided by the gate, so only the abandoned
        # thread has to be joined before the session can be read.
        await asyncio.wait_for(asyncio.to_thread(service.wait_for_finalization, 10), timeout=15)

        data = DataSessionService(handle.root)
        stored = data.get_session(session_id)
        segments = data.list_segments(session_id)

        assert stored.state is DataSessionState.FAILED
        assert stored.segment_count == len(segments) > 0
        assert sum(segment.frame_count for segment in segments) == stored.frame_count
        for segment in segments:
            assert (handle.root / segment.relative_path).is_file()
        assert data.inspect_integrity().clean is True


async def test_a_worker_blocked_inside_its_own_completion_still_loses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decisive write is arbitrated durably, not only in memory.

    A worker that already cleared the in-memory gate but has not yet committed
    must still lose: the recorded failure claims the still-``ACTIVE`` row first,
    and the completion is then refused by the same condition.
    """
    with project(tmp_path) as handle:
        service = RuntimeService(
            project_max_frames_per_segment=SEGMENT_LIMIT,
            recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT,
        )
        block = BlockingFinalize(monkeypatch, target="commit_completed")
        session_id = await _run_capture_until_blocked(service, handle, block)

        stop_task = asyncio.create_task(service.stop_capture())
        await block.wait_entered()
        await asyncio.wait_for(stop_task, timeout=10)
        assert service.failure is not None
        assert service.failure.code == "recorder.cleanup_timeout"

        block.release.set()
        await block.wait_finished()
        monkeypatch.undo()
        await asyncio.wait_for(asyncio.to_thread(service.wait_for_finalization, 10), timeout=15)

        data = DataSessionService(handle.root)
        assert data.get_session(session_id).state is DataSessionState.FAILED
        assert data.list_segments(session_id)
        assert data.inspect_integrity().clean is True


async def test_a_slow_finalize_inside_the_deadline_still_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test C: the gate must not misjudge a slow-but-in-time finalize as a failure."""
    with project(tmp_path) as handle:
        service = RuntimeService(
            project_max_frames_per_segment=SEGMENT_LIMIT,
            recorder_cleanup_timeout_seconds=5.0,
        )
        block = BlockingFinalize(monkeypatch)
        session_id = await _run_capture_until_blocked(service, handle, block)

        stop_task = asyncio.create_task(service.stop_capture())
        await block.wait_entered()
        await asyncio.sleep(0.3)  # slow, but well inside the deadline
        block.release.set()
        await asyncio.wait_for(stop_task, timeout=10)
        monkeypatch.undo()

        assert service.failure is None
        assert service.capture_state is CaptureSessionState.IDLE
        stored = DataSessionService(handle.root).get_session(session_id)
        assert stored.state is DataSessionState.COMPLETED
        assert stored.frame_count > 0


async def test_repeated_stop_after_a_cleanup_timeout_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test E: a second stop must not resurrect COMPLETED or raise."""
    with project(tmp_path) as handle:
        service = RuntimeService(
            project_max_frames_per_segment=SEGMENT_LIMIT,
            recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT,
        )
        block = BlockingFinalize(monkeypatch)
        session_id = await _run_capture_until_blocked(service, handle, block)

        stop_task = asyncio.create_task(service.stop_capture())
        await block.wait_entered()
        await asyncio.wait_for(stop_task, timeout=10)
        first_failure = service.failure
        assert first_failure is not None

        # Second and third stop: bounded, silent, no state change.
        await asyncio.wait_for(service.stop_capture(), timeout=5)
        await asyncio.wait_for(service.stop_capture(), timeout=5)

        block.release.set()
        await block.wait_finished()
        monkeypatch.undo()
        await asyncio.wait_for(asyncio.to_thread(service.wait_for_finalization, 10), timeout=15)

        assert service.failure is first_failure
        assert service.capture_state is CaptureSessionState.FAILED
        stored = DataSessionService(handle.root).get_session(session_id)
        assert stored.state is DataSessionState.FAILED


async def test_a_new_capture_is_refused_while_a_finalize_worker_is_still_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old persistence worker must not race a new recording."""
    with project(tmp_path) as handle:
        service = RuntimeService(
            project_max_frames_per_segment=SEGMENT_LIMIT,
            recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT,
        )
        block = BlockingFinalize(monkeypatch)
        session_id = await _run_capture_until_blocked(service, handle, block)

        stop_task = asyncio.create_task(service.stop_capture())
        await block.wait_entered()
        await asyncio.wait_for(stop_task, timeout=10)

        with pytest.raises(CaptureConfigurationError) as info:
            await service.start_capture(
                VirtualAdapterConfig(rate_hz=1_000), project_path=handle.root
            )
        assert info.value.code == "capture.finalization_pending"
        assert info.value.recoverable is True

        block.release.set()
        await block.wait_finished()
        monkeypatch.undo()

        # Once the old worker has settled, a new capture is allowed again.
        assert await asyncio.wait_for(asyncio.to_thread(service.wait_for_finalization, 10), 15)
        await service.start_capture(
            VirtualAdapterConfig(rate_hz=1_000), batch_size=16, project_path=handle.root
        )
        second_session = service.data_session_id
        assert second_session is not None
        assert second_session != session_id
        await asyncio.sleep(0.1)
        await asyncio.wait_for(service.stop_capture(), timeout=10)

        data = DataSessionService(handle.root)
        assert data.get_session(session_id).state is DataSessionState.FAILED
        assert data.get_session(second_session).state is DataSessionState.COMPLETED
