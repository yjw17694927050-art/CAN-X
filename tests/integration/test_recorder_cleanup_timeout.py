"""A cleanup timeout must never produce a verdict the database can contradict.

The runtime bounds ``recorder.stop()`` with a cleanup deadline. When that
deadline expires, the awaiting coroutine is cancelled — but ``asyncio`` cannot
cancel a worker thread, so the synchronous persistence it had already started
keeps running. The runtime would then report ``recorder.cleanup_timeout`` for a
session the database later calls ``COMPLETED``.

There are exactly two truthful outcomes, and which one applies depends on where
the worker was when the deadline landed:

* the decisive write had not started — the owner holds the recorder's terminal
  lock, closes the gate, and ``FAILED`` really does win durably; or
* the decisive write was already in flight — nothing in-process can revoke it,
  so the runtime reports a pending finalization and waits for the worker's real
  result instead of guessing.

``V0.2-04-FINAL-2`` added the second case and the real-contention tests that
reach it: a second connection holds the project database's writer lock, so the
terminal ``commit_completed`` blocks *inside* SQLite rather than at a patched
seam. The earlier tests below cover the first case and the healthy paths.
"""

import asyncio
import sqlite3
import threading
from contextlib import suppress
from pathlib import Path

import pytest
from canx.api.app import create_app
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService, DataSessionWriter
from canx.devices.virtual import VirtualAdapterConfig
from canx.project.service import ProjectHandle, ProjectService
from canx.project.storage import DATABASE_FILENAME
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


async def test_a_worker_past_its_gate_check_is_settled_not_condemned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deadline cannot condemn a worker that already cleared its gate check.

    Once the worker holds the terminal lock its decisive write is in flight and
    nothing in-process can revoke it. The runtime must report a pending
    finalization rather than a failure, and when the worker resumes the session
    must land ``COMPLETED`` with no failure ever having been published.
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
        assert service.failure is None
        assert service.capture_state is CaptureSessionState.FINALIZING

        block.release.set()
        await block.wait_finished()
        monkeypatch.undo()
        await asyncio.wait_for(service.wait_for_finalization_settlement(), timeout=25)

        assert service.failure is None
        assert service.capture_state is CaptureSessionState.IDLE
        data = DataSessionService(handle.root)
        assert data.get_session(session_id).state is DataSessionState.COMPLETED
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


# ---------------------------------------------------------------------------
# V0.2-04-FINAL-2 — a deadline must not pre-judge a terminal commit in flight.
#
# The tests above pin two mechanisms: the long flush is cancellable, and a
# decisive write that has not started yet is arbitrated durably. Neither reaches
# the case this file exists to close: the worker is *inside* the terminal SQLite
# transaction, holding the recorder's terminal lock, when the cleanup deadline
# expires. ``asyncio`` cannot cancel the thread and SQLite cannot revoke a
# transaction another thread owns, so no in-process owner can force a verdict.
#
# These tests make that window real — a second connection holds the project
# database's writer lock, so ``commit_completed`` blocks inside SQLite itself
# rather than at a monkeypatched entry point — and pin the only truthful answer:
# report a pending finalization, never a failure the database may contradict.
# ---------------------------------------------------------------------------


class SqliteWriteLock:
    """Hold the project database's writer lock from a second, real connection.

    A monkeypatched seam would only prove the code path was reached. Holding the
    actual SQLite write lock makes the worker's ``BEGIN IMMEDIATE`` block inside
    the storage engine, which is the contention the runtime has to survive.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._connection: sqlite3.Connection | None = None

    def acquire(self) -> None:
        connection = sqlite3.connect(
            str(self._root / DATABASE_FILENAME), isolation_level=None, timeout=30.0
        )
        connection.execute("BEGIN IMMEDIATE")
        self._connection = connection

    def release(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        with suppress(sqlite3.Error):
            connection.execute("ROLLBACK")
        with suppress(sqlite3.Error):
            connection.close()


class FlushCompletedSeam:
    """Pause the worker between its real flush and its gated terminal commit.

    The worker runs its own ``flush_pending``; the seam then waits for the test
    to take the SQLite write lock, so the very next thing the worker does — the
    terminal ``commit_completed`` — genuinely blocks inside SQLite. The seam
    creates neither the lock nor the transaction: it only makes the instant the
    terminal transaction is reached deterministic.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.flushed = threading.Event()
        self.terminal_unblocked = threading.Event()
        real_flush = DataSessionWriter.flush_pending

        def hooked(writer: DataSessionWriter) -> None:
            real_flush(writer)
            self.flushed.set()
            self.terminal_unblocked.wait(timeout=60)

        monkeypatch.setattr(DataSessionWriter, "flush_pending", hooked)

    async def wait_flushed(self) -> None:
        flushed = await asyncio.wait_for(asyncio.to_thread(self.flushed.wait, 20), timeout=25)
        assert flushed, "the finalization worker never finished its flush"


async def capture_into(service: RuntimeService, handle: ProjectHandle) -> str:
    """Run one project-backed capture to steady state and return its session id."""
    await service.start_capture(
        VirtualAdapterConfig(rate_hz=RATE_HZ, seed=5),
        batch_size=BATCH_SIZE,
        project_path=handle.root,
    )
    session_id = service.data_session_id
    assert session_id is not None
    await asyncio.sleep(0.25)
    return session_id


async def test_a_terminal_commit_already_in_flight_is_never_pre_judged_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test A: real SQLite contention on the decisive ``ACTIVE`` → ``COMPLETED``.

    The writer lock is held by another connection while the cleanup deadline
    expires, so the terminal transaction is in flight and cannot be revoked. The
    runtime must not publish ``recorder.cleanup_timeout`` — it reports a pending
    finalization, and once the lock is released the session lands ``COMPLETED``
    with no failure ever having been declared.
    """
    with project(tmp_path) as handle:
        service = RuntimeService(
            project_max_frames_per_segment=SEGMENT_LIMIT,
            recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT,
        )
        seam = FlushCompletedSeam(monkeypatch)
        session_id = await capture_into(service, handle)
        lock = SqliteWriteLock(handle.root)
        try:
            stop_task = asyncio.create_task(service.stop_capture())
            await seam.wait_flushed()
            lock.acquire()
            seam.terminal_unblocked.set()
            await asyncio.wait_for(stop_task, timeout=20)

            # Bounded stop returned, but the durable outcome is not yet known:
            # publishing a verdict now is what produced the old contradiction.
            assert service.failure is None
            assert service.capture_state is CaptureSessionState.FINALIZING
            assert service.has_session is False
        finally:
            lock.release()
        monkeypatch.undo()

        await asyncio.wait_for(service.wait_for_finalization_settlement(), timeout=25)
        assert service.failure is None
        assert service.capture_state is CaptureSessionState.IDLE
        stored = DataSessionService(handle.root).get_session(session_id)
        assert stored.state is DataSessionState.COMPLETED


async def test_a_pending_finalization_blocks_the_next_capture_and_repeated_stop_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests D/E: pending is explicit, blocking, and idempotent."""
    with project(tmp_path) as handle:
        service = RuntimeService(
            project_max_frames_per_segment=SEGMENT_LIMIT,
            recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT,
        )
        seam = FlushCompletedSeam(monkeypatch)
        session_id = await capture_into(service, handle)
        lock = SqliteWriteLock(handle.root)
        try:
            stop_task = asyncio.create_task(service.stop_capture())
            await seam.wait_flushed()
            lock.acquire()
            seam.terminal_unblocked.set()
            await asyncio.wait_for(stop_task, timeout=20)
            assert service.capture_state is CaptureSessionState.FINALIZING

            settlement = service._finalization_settlement_task
            assert settlement is not None

            with pytest.raises(CaptureConfigurationError) as info:
                await service.start_capture(
                    VirtualAdapterConfig(rate_hz=1_000, seed=1), project_path=handle.root
                )
            assert info.value.code == "capture.finalization_pending"
            assert info.value.recoverable is True

            # A realtime-only capture shares the runtime's capture state, so it is
            # refused too until the finalization settles.
            with pytest.raises(CaptureConfigurationError) as realtime_info:
                await service.start_capture(VirtualAdapterConfig(rate_hz=1_000, seed=9))
            assert realtime_info.value.code == "capture.finalization_pending"

            # Repeated stop while pending: bounded, silent, no duplicate work.
            await asyncio.wait_for(service.stop_capture(), timeout=10)
            await asyncio.wait_for(service.stop_capture(), timeout=10)
            assert service._finalization_settlement_task is settlement
            assert service.capture_state is CaptureSessionState.FINALIZING
            assert service.failure is None
        finally:
            lock.release()
        monkeypatch.undo()

        await asyncio.wait_for(service.wait_for_finalization_settlement(), timeout=25)
        assert service.capture_state is CaptureSessionState.IDLE
        assert service.failure is None
        data = DataSessionService(handle.root)
        assert data.get_session(session_id).state is DataSessionState.COMPLETED

        # A settled finalization no longer blocks a new capture.
        await service.start_capture(
            VirtualAdapterConfig(rate_hz=1_000, seed=2), batch_size=16, project_path=handle.root
        )
        second = service.data_session_id
        assert second is not None
        assert second != session_id
        await asyncio.sleep(0.1)
        await asyncio.wait_for(service.stop_capture(), timeout=10)
        assert data.get_session(second).state is DataSessionState.COMPLETED


async def test_shutdown_while_a_finalization_is_pending_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test F: process shutdown never guesses a verdict for an in-flight commit.

    The application lifespan is entered and exited for real, so the shutdown
    path — not just a direct call — is what has to stay bounded and honest.
    """
    with project(tmp_path) as handle:
        service = RuntimeService(
            project_max_frames_per_segment=SEGMENT_LIMIT,
            recorder_cleanup_timeout_seconds=CLEANUP_TIMEOUT,
        )
        app = create_app(runtime_service=service)
        seam = FlushCompletedSeam(monkeypatch)
        session_id = await capture_into(service, handle)
        lock = SqliteWriteLock(handle.root)
        try:
            async with app.router.lifespan_context(app):
                stop_task = asyncio.create_task(service.stop_capture())
                await seam.wait_flushed()
                lock.acquire()
                seam.terminal_unblocked.set()
                await asyncio.wait_for(stop_task, timeout=20)
                assert service.capture_state is CaptureSessionState.FINALIZING
            # Shutdown ran bounded and invented no verdict for the in-flight commit.
            assert service.failure is None
            assert service.has_session is False
        finally:
            lock.release()
        monkeypatch.undo()

        assert await asyncio.wait_for(
            asyncio.to_thread(service.wait_for_finalization, 20), 25
        ), "the abandoned persistence worker never exited"
        stored = DataSessionService(handle.root).get_session(session_id)
        assert stored.state is DataSessionState.COMPLETED
        assert service.failure is None
