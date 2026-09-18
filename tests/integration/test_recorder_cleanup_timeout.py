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

RELIABILITY-01-FIX-2 hardened the harness itself. Two properties are now
structural rather than per-test discipline:

* **Two budgets, never one.** ``FAULT_CLEANUP_TIMEOUT_SECONDS`` is the deadline
  a failure injection *wants* to expire; a recovery capture that has to reach
  ``COMPLETED`` states ``HEALTHY_CLEANUP_TIMEOUT_SECONDS`` through its own
  runtime instead of inheriting the fault deadline. A test that mixes the two is
  claiming a healthy finalization fits inside a deliberately tiny budget.
* **Every gate is released, whatever the test does.** Every parkable worker is
  created through :class:`FinalizationHarness`, whose ``close()`` runs in a
  ``finally``. A failed assertion can no longer leave a worker parked on a gate
  (or a SQLite write lock held) for the next test to trip over — the failure
  mode that turns one red assertion into a suite-wide stall.
"""

import asyncio
import sqlite3
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import pytest
from canx.api.app import create_app
from canx.data.model import DataSessionState
from canx.data.session import DataSessionService, DataSessionWriter
from canx.devices.virtual import VirtualAdapterConfig
from canx.project.service import ProjectHandle, ProjectService
from canx.project.storage import DATABASE_FILENAME
from canx.runtime.errors import CaptureConfigurationError
from canx.runtime.service import CaptureSessionState, RuntimeService
from httpx import ASGITransport, AsyncClient

#: The deadline a failure injection wants to expire. Deliberately tiny.
FAULT_CLEANUP_TIMEOUT_SECONDS = 0.8

#: The stop budget a *healthy* capture gets. A recovery path that must reach
#: ``COMPLETED`` may not inherit the fault deadline above: the two contracts are
#: different questions — "does a bounded stop survive a missed deadline" versus
#: "does a normal capture finish" — and inheriting the first for the second is
#: how a healthy path silently acquires a budget sized for a deliberate failure.
HEALTHY_CLEANUP_TIMEOUT_SECONDS = 6.0

#: A wider stop budget for the concurrent status test: the status read has to
#: land while a real stop is still in progress, so the window must not be tight.
STOP_WINDOW_SECONDS = 2.0

#: Small enough that the archive drain finishes well inside the fault deadline,
#: so the deadline lands on the *finalize* rather than on the drain.
SEGMENT_LIMIT = 64
BATCH_SIZE = 32
RATE_HZ = 1_000.0


def project(tmp_path: Path) -> ProjectHandle:
    return ProjectService().create(tmp_path / "vehicle.canx", display_name="Vehicle A")


def assert_completed(service: RuntimeService, stored: Any, *, label: str | None = None) -> None:
    """Assert a session reached ``COMPLETED``, or say exactly why it did not.

    A bare ``FAILED != COMPLETED`` in a CI log told us nothing about which stage
    consumed the budget, so every success assertion in this module reports the
    runtime's own explanation instead: the durable state, the runtime's capture
    state, the failure code and context behind it, and whether a finalization is
    still outstanding.
    """
    if stored.state is DataSessionState.COMPLETED:
        return
    failure = service.failure
    code = failure.code if failure is not None else None
    message = failure.message if failure is not None else None
    context = dict(failure.context) if failure is not None else None
    where = f"{label}: " if label else ""
    raise AssertionError(
        f"{where}session {stored.session_id} is {stored.state}, not COMPLETED "
        f"[capture_state={service.capture_state} failure={code} "
        f"message={message!r} context={context} "
        f"finalization_pending={service.finalization_pending}]"
    )


class WorkerSignals:
    """Async-side notifications raised from worker threads.

    ``asyncio.to_thread(threading.Event.wait, …)`` occupies a default-executor
    worker for the whole wait, and this module parks workers on purpose, so the
    *waiting* side must not cost an executor thread. A worker thread therefore
    signals an ``asyncio.Event`` through ``loop.call_soon_threadsafe``. The
    blocking side stays a ``threading.Event``: no worker can await.
    """

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._events: dict[str, asyncio.Event] = {}

    def _event(self, name: str) -> asyncio.Event:
        return self._events.setdefault(name, asyncio.Event())

    def signal(self, name: str) -> None:
        """Raise ``name`` from a worker thread. Never raises."""
        loop = self._loop
        if loop.is_closed():  # teardown raced the worker
            return
        event = self._event(name)
        with suppress(RuntimeError):  # the loop closed between the check and the call
            loop.call_soon_threadsafe(event.set)

    async def wait(self, name: str, *, timeout: float, failure: str) -> None:
        try:
            await asyncio.wait_for(self._event(name).wait(), timeout=timeout)
        except TimeoutError as exc:
            raise AssertionError(failure) from exc


class BlockingFinalize:
    """Block ``DataSessionWriter.<target>`` on explicit events (a deterministic seam).

    The replacement is a plain function so the descriptor protocol still binds
    the writer as its first argument; the events make the window reproducible
    instead of racing it against a sleep.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, target: str = "flush_pending") -> None:
        self.signals = WorkerSignals()
        self.release = threading.Event()
        real = getattr(DataSessionWriter, target)

        def blocking(writer: DataSessionWriter) -> object:
            self.signals.signal("entered")
            self.release.wait(timeout=30)
            try:
                return real(writer)
            finally:
                self.signals.signal("finished")

        monkeypatch.setattr(DataSessionWriter, target, blocking)

    async def wait_entered(self) -> None:
        await self.signals.wait(
            "entered",
            timeout=20,
            failure="the finalization worker never entered its blocking phase",
        )

    async def wait_finished(self) -> None:
        await self.signals.wait(
            "finished", timeout=25, failure="the finalization worker never finished"
        )


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
        self.signals = WorkerSignals()
        self.terminal_unblocked = threading.Event()
        real_flush = DataSessionWriter.flush_pending

        def hooked(writer: DataSessionWriter) -> None:
            real_flush(writer)
            self.signals.signal("flushed")
            self.terminal_unblocked.wait(timeout=60)

        monkeypatch.setattr(DataSessionWriter, "flush_pending", hooked)

    async def wait_flushed(self) -> None:
        await self.signals.wait(
            "flushed",
            timeout=25,
            failure="the finalization worker never finished its flush",
        )


class FinalizationHarness:
    """Owns every gate, lock and runtime a test here can park a worker on.

    ``asyncio`` cannot cancel a worker thread, so a test that fails while a
    worker is parked on a gate would leave that thread — and its default-executor
    slot — behind for the next test. Every parkable resource is therefore created
    through this harness, and :meth:`close` releases all of them, restores the
    monkeypatch and joins the worker no matter how the test ended.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        self._gates: list[BlockingFinalize] = []
        self._seams: list[FlushCompletedSeam] = []
        self._locks: list[SqliteWriteLock] = []
        self._runtimes: list[RuntimeService] = []

    def runtime(self, *, cleanup_seconds: float, **overrides: Any) -> RuntimeService:
        """A runtime whose stop budget is stated here, never inherited."""
        service = RuntimeService(
            project_max_frames_per_segment=SEGMENT_LIMIT,
            recorder_cleanup_timeout_seconds=cleanup_seconds,
            **overrides,
        )
        self._runtimes.append(service)
        return service

    def blocking_finalize(self, target: str = "flush_pending") -> BlockingFinalize:
        gate = BlockingFinalize(self._monkeypatch, target)
        self._gates.append(gate)
        return gate

    def flush_seam(self) -> FlushCompletedSeam:
        seam = FlushCompletedSeam(self._monkeypatch)
        self._seams.append(seam)
        return seam

    def write_lock(self, handle: ProjectHandle) -> SqliteWriteLock:
        lock = SqliteWriteLock(handle.root)
        self._locks.append(lock)
        return lock

    async def close(self) -> None:
        """Release every gate and join every worker. Never raises."""
        for gate in self._gates:
            gate.release.set()
        for seam in self._seams:
            seam.terminal_unblocked.set()
        for lock in self._locks:
            lock.release()
        self._monkeypatch.undo()
        for service in self._runtimes:
            with suppress(Exception):
                await asyncio.wait_for(service.wait_for_finalization_settlement(), timeout=20)
            with suppress(Exception):
                await asyncio.wait_for(
                    asyncio.to_thread(service.wait_for_finalization, 20), timeout=30
                )


@asynccontextmanager
async def finalization_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[FinalizationHarness]:
    """Fail-safe scope for every test in this module (see AGENT rules §teardown)."""
    harness = FinalizationHarness(monkeypatch)
    try:
        yield harness
    finally:
        await harness.close()


async def settle(service: RuntimeService) -> None:
    """Wait for a pending finalization to resolve and its worker to exit."""
    await asyncio.wait_for(service.wait_for_finalization_settlement(), timeout=25)
    assert await asyncio.wait_for(
        asyncio.to_thread(service.wait_for_finalization, 20), 25
    ), "the abandoned persistence worker never exited"


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


async def test_a_cleanup_timeout_can_never_become_a_completed_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test A/D: declared timeout, then the worker resumes — still not COMPLETED."""
    with project(tmp_path) as handle:
        async with finalization_harness(monkeypatch) as harness:
            service = harness.runtime(cleanup_seconds=FAULT_CLEANUP_TIMEOUT_SECONDS)
            block = harness.blocking_finalize()
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
            await settle(service)

            stored = DataSessionService(handle.root).get_session(session_id)
            assert stored.state is DataSessionState.FAILED
            assert stored.state is not DataSessionState.COMPLETED


async def test_a_cleanup_timeout_keeps_the_segments_that_were_already_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test B: a partial tail survives the timeout; nothing committed is lost."""
    with project(tmp_path) as handle:
        async with finalization_harness(monkeypatch) as harness:
            service = harness.runtime(cleanup_seconds=FAULT_CLEANUP_TIMEOUT_SECONDS)
            block = harness.blocking_finalize(target="flush_pending")
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
            # The worker's terminal decision runs right after its flush returns;
            # the durable outcome is already decided by the gate, so only the
            # abandoned thread has to be joined before the session can be read.
            await settle(service)

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

    ``COMPLETED`` here is decided by the worker clearing its own gate, not by the
    deadline, so the fault budget is the right one to state.
    """
    with project(tmp_path) as handle:
        async with finalization_harness(monkeypatch) as harness:
            service = harness.runtime(cleanup_seconds=FAULT_CLEANUP_TIMEOUT_SECONDS)
            block = harness.blocking_finalize(target="commit_completed")
            session_id = await _run_capture_until_blocked(service, handle, block)

            stop_task = asyncio.create_task(service.stop_capture())
            await block.wait_entered()
            await asyncio.wait_for(stop_task, timeout=10)
            assert service.failure is None
            assert service.capture_state is CaptureSessionState.FINALIZING

            block.release.set()
            await block.wait_finished()
            await settle(service)

            assert service.failure is None
            assert service.capture_state is CaptureSessionState.IDLE
            data = DataSessionService(handle.root)
            assert_completed(service, data.get_session(session_id), label="gate-cleared worker")
            assert data.list_segments(session_id)
            assert data.inspect_integrity().clean is True


async def test_a_slow_finalize_inside_the_deadline_still_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test C: the gate must not misjudge a slow-but-in-time finalize as a failure."""
    with project(tmp_path) as handle:
        async with finalization_harness(monkeypatch) as harness:
            service = harness.runtime(cleanup_seconds=HEALTHY_CLEANUP_TIMEOUT_SECONDS)
            block = harness.blocking_finalize()
            session_id = await _run_capture_until_blocked(service, handle, block)

            stop_task = asyncio.create_task(service.stop_capture())
            await block.wait_entered()
            await asyncio.sleep(0.3)  # slow, but well inside the deadline
            block.release.set()
            await asyncio.wait_for(stop_task, timeout=10)

            assert service.failure is None
            assert service.capture_state is CaptureSessionState.IDLE
            stored = DataSessionService(handle.root).get_session(session_id)
            assert_completed(service, stored, label="slow finalize inside the deadline")
            assert stored.frame_count > 0


async def test_repeated_stop_after_a_cleanup_timeout_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test E: a second stop must not resurrect COMPLETED or raise."""
    with project(tmp_path) as handle:
        async with finalization_harness(monkeypatch) as harness:
            service = harness.runtime(cleanup_seconds=FAULT_CLEANUP_TIMEOUT_SECONDS)
            block = harness.blocking_finalize()
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
            await settle(service)

            assert service.failure is first_failure
            assert service.capture_state is CaptureSessionState.FAILED
            stored = DataSessionService(handle.root).get_session(session_id)
            assert stored.state is DataSessionState.FAILED


async def test_a_new_capture_is_refused_while_a_finalize_worker_is_still_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old persistence worker must not race a new recording.

    The fault half runs on the fault deadline. The recovery half is a *healthy*
    capture — it has to reach ``COMPLETED`` — so it runs on its own runtime,
    which states the healthy budget instead of inheriting the 0.8 s fault
    deadline. The refusal and the release of the pending flag are both asserted
    on the fault runtime, so the blocking contract is still pinned end to end.
    """
    with project(tmp_path) as handle:
        async with finalization_harness(monkeypatch) as harness:
            fault = harness.runtime(cleanup_seconds=FAULT_CLEANUP_TIMEOUT_SECONDS)
            block = harness.blocking_finalize()
            session_id = await _run_capture_until_blocked(fault, handle, block)

            stop_task = asyncio.create_task(fault.stop_capture())
            await block.wait_entered()
            await asyncio.wait_for(stop_task, timeout=10)

            with pytest.raises(CaptureConfigurationError) as info:
                await fault.start_capture(
                    VirtualAdapterConfig(rate_hz=1_000), project_path=handle.root
                )
            assert info.value.code == "capture.finalization_pending"
            assert info.value.recoverable is True
            assert fault.finalization_pending is True

            block.release.set()
            await block.wait_finished()
            await settle(fault)

            # The old worker has settled: the same runtime is no longer pending.
            assert fault.finalization_pending is False

            healthy = harness.runtime(cleanup_seconds=HEALTHY_CLEANUP_TIMEOUT_SECONDS)
            await healthy.start_capture(
                VirtualAdapterConfig(rate_hz=1_000), batch_size=16, project_path=handle.root
            )
            second_session = healthy.data_session_id
            assert second_session is not None
            assert second_session != session_id
            await asyncio.sleep(0.1)
            await asyncio.wait_for(healthy.stop_capture(), timeout=20)

            data = DataSessionService(handle.root)
            assert data.get_session(session_id).state is DataSessionState.FAILED
            assert_completed(
                healthy,
                data.get_session(second_session),
                label="new capture after the old worker settled",
            )


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


async def test_a_terminal_commit_already_in_flight_is_never_pre_judged_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test A: real SQLite contention on the decisive ``ACTIVE`` → ``COMPLETED``.

    The writer lock is held by another connection while the cleanup deadline
    expires, so the terminal transaction is in flight and cannot be revoked. The
    runtime must not publish ``recorder.cleanup_timeout`` — it reports a pending
    finalization, and once the lock is released the session lands ``COMPLETED``
    with no failure ever having been declared.

    ``COMPLETED`` is produced by the worker resuming past SQLite, not by the
    deadline, so the fault budget is the right one to state here.
    """
    with project(tmp_path) as handle:
        async with finalization_harness(monkeypatch) as harness:
            service = harness.runtime(cleanup_seconds=FAULT_CLEANUP_TIMEOUT_SECONDS)
            seam = harness.flush_seam()
            session_id = await capture_into(service, handle)
            lock = harness.write_lock(handle)

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

            lock.release()
            await settle(service)

            assert service.failure is None
            assert service.capture_state is CaptureSessionState.IDLE
            stored = DataSessionService(handle.root).get_session(session_id)
            assert_completed(service, stored, label="terminal commit in flight")


async def test_a_pending_finalization_blocks_the_next_capture_and_repeated_stop_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests D/E: pending is explicit, blocking, and idempotent.

    As above, the blocking contract lives on the fault runtime and the recovery
    capture that must reach ``COMPLETED`` runs on a runtime stating the healthy
    budget — the two deadlines are different questions.
    """
    with project(tmp_path) as handle:
        async with finalization_harness(monkeypatch) as harness:
            fault = harness.runtime(cleanup_seconds=FAULT_CLEANUP_TIMEOUT_SECONDS)
            seam = harness.flush_seam()
            session_id = await capture_into(fault, handle)
            lock = harness.write_lock(handle)

            stop_task = asyncio.create_task(fault.stop_capture())
            await seam.wait_flushed()
            lock.acquire()
            seam.terminal_unblocked.set()
            await asyncio.wait_for(stop_task, timeout=20)
            assert fault.capture_state is CaptureSessionState.FINALIZING

            settlement = fault._finalization_settlement_task
            assert settlement is not None

            with pytest.raises(CaptureConfigurationError) as info:
                await fault.start_capture(
                    VirtualAdapterConfig(rate_hz=1_000, seed=1), project_path=handle.root
                )
            assert info.value.code == "capture.finalization_pending"
            assert info.value.recoverable is True

            # A realtime-only capture shares the runtime's capture state, so it is
            # refused too until the finalization settles.
            with pytest.raises(CaptureConfigurationError) as realtime_info:
                await fault.start_capture(VirtualAdapterConfig(rate_hz=1_000, seed=9))
            assert realtime_info.value.code == "capture.finalization_pending"

            # Repeated stop while pending: bounded, silent, no duplicate work.
            await asyncio.wait_for(fault.stop_capture(), timeout=10)
            await asyncio.wait_for(fault.stop_capture(), timeout=10)
            assert fault._finalization_settlement_task is settlement
            assert fault.capture_state is CaptureSessionState.FINALIZING
            assert fault.failure is None

            lock.release()
            await settle(fault)

            assert fault.capture_state is CaptureSessionState.IDLE
            assert fault.failure is None
            assert fault.finalization_pending is False
            data = DataSessionService(handle.root)
            assert_completed(
                fault, data.get_session(session_id), label="settled in-flight commit"
            )

            # A settled finalization no longer blocks a new capture.
            healthy = harness.runtime(cleanup_seconds=HEALTHY_CLEANUP_TIMEOUT_SECONDS)
            await healthy.start_capture(
                VirtualAdapterConfig(rate_hz=1_000, seed=2), batch_size=16, project_path=handle.root
            )
            second = healthy.data_session_id
            assert second is not None
            assert second != session_id
            await asyncio.sleep(0.1)
            await asyncio.wait_for(healthy.stop_capture(), timeout=20)
            assert_completed(
                healthy, data.get_session(second), label="capture after pending settled"
            )


async def test_shutdown_while_a_finalization_is_pending_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test F: process shutdown never guesses a verdict for an in-flight commit.

    The application lifespan is entered and exited for real, so the shutdown
    path — not just a direct call — is what has to stay bounded and honest.
    """
    with project(tmp_path) as handle:
        async with finalization_harness(monkeypatch) as harness:
            service = harness.runtime(cleanup_seconds=FAULT_CLEANUP_TIMEOUT_SECONDS)
            app = create_app(runtime_service=service)
            seam = harness.flush_seam()
            session_id = await capture_into(service, handle)
            lock = harness.write_lock(handle)

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

            lock.release()
            await settle(service)

            stored = DataSessionService(handle.root).get_session(session_id)
            assert_completed(service, stored, label="shutdown with a pending finalization")
            assert service.failure is None


# ---------------------------------------------------------------------------
# V0.2-04-FINAL-3 — a stop in flight is ``finalizing``, never a failure with no
# verdict behind it.
#
# V0.2-04-FINAL-2 stopped the runtime from *pre-judging* an in-flight terminal
# commit, but a narrower lie survived: ``RuntimeService.capture_state`` inferred
# ``FAILED`` from ``has_session and not capture_active`` — a pair that is true
# for the whole span between the pipeline stopping and the runtime releasing its
# session reference. During a perfectly normal stop, ``GET /runtime/status``
# therefore reported ``capture_state=failed`` with ``failure=None``: a verdict
# with no diagnostic grounding it. ``FAILED`` is a verdict the runtime may only
# publish when a real failure exists, so a stop declares ``FINALIZING``
# explicitly and stops inferring failure from pipeline activity.
# ---------------------------------------------------------------------------


async def test_status_reports_finalizing_while_a_real_stop_is_in_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests A/B/C: a stop in flight is observable as ``finalizing``, never failed.

    ``GET /runtime/status`` is a read-only observation and must answer while
    ``stop_capture()`` is still running — not wait on the lifecycle lock for it to
    finish. Across the whole span of the stop it has to report the honest
    lifecycle: ``finalizing`` while the terminal commit is in flight in SQLite,
    still ``finalizing`` when the bounded stop returns without a verdict, and
    ``idle`` once the worker settles the recording ``COMPLETED``. ``failure``
    stays ``None`` throughout, because no failure exists.

    ``STOP_WINDOW_SECONDS`` is a window, not a completion budget: it exists so a
    status read can land while the stop is genuinely still running.
    """
    with project(tmp_path) as handle:
        async with finalization_harness(monkeypatch) as harness:
            service = harness.runtime(cleanup_seconds=STOP_WINDOW_SECONDS)
            app = create_app(runtime_service=service)
            seam = harness.flush_seam()
            session_id = await capture_into(service, handle)
            recorder = service._last_project_recorder
            assert recorder is not None
            lock = harness.write_lock(handle)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                stop_task = asyncio.create_task(service.stop_capture())
                await seam.wait_flushed()
                lock.acquire()
                seam.terminal_unblocked.set()
                # Wait for the worker to genuinely enter its terminal SQLite
                # transaction, so the deadline lands on an in-flight commit
                # rather than at a patched seam.
                async with asyncio.timeout(5):
                    while not recorder._terminal_lock.locked():
                        await asyncio.sleep(0.005)

                # stop_capture() is still in progress here.
                assert not stop_task.done()
                during = (await client.get("/runtime/status")).json()
                assert during["capture_state"] == "finalizing", during
                assert during["capture_active"] is False, during
                assert during["failure"] is None, during

                # The bounded stop returns without a verdict — the terminal
                # commit is still blocked in SQLite — and must still report
                # finalizing, not a guessed failure.
                await asyncio.wait_for(stop_task, timeout=20)
                pending = (await client.get("/runtime/status")).json()
                assert pending["capture_state"] == "finalizing", pending
                assert pending["failure"] is None, pending

                lock.release()
                await settle(service)

                # With the writer lock released the worker resumes and settles the
                # recording COMPLETED; only then may the runtime report idle.
                settled = (await client.get("/runtime/status")).json()
                assert settled["capture_state"] == "idle", settled
                assert settled["failure"] is None, settled

            stored = DataSessionService(handle.root).get_session(session_id)
            assert_completed(service, stored, label="stop observed as finalizing")


async def test_a_public_failed_state_always_carries_a_failure_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test E: ``capture_state == failed`` always names a real failure verdict.

    ``failed`` is a verdict, not a description of an inactive pipeline. This is
    the observable contract the whole fix rests on: at no point of a capture's
    life may a status read see ``failed`` with ``failure`` None.
    """
    with project(tmp_path) as handle:
        async with finalization_harness(monkeypatch) as harness:
            service = harness.runtime(cleanup_seconds=FAULT_CLEANUP_TIMEOUT_SECONDS)

            def assert_consistent(label: str) -> None:
                if service.capture_state is CaptureSessionState.FAILED:
                    assert service.failure is not None, (
                        f"{label}: capture_state=failed but failure is None"
                    )

            assert_consistent("idle")
            assert service.capture_state is CaptureSessionState.IDLE

            block = harness.blocking_finalize()
            await _run_capture_until_blocked(service, handle, block)
            assert service.capture_state is CaptureSessionState.RUNNING
            assert_consistent("running")

            stop_task = asyncio.create_task(service.stop_capture())
            await block.wait_entered()
            # Ingress has stopped, but no verdict exists yet: the state must be an
            # honest finalizing, not a failed that names nothing.
            assert service.capture_state is CaptureSessionState.FINALIZING
            assert_consistent("stop in progress")

            await asyncio.wait_for(stop_task, timeout=10)
            assert service.capture_state is CaptureSessionState.FAILED
            assert_consistent("after cleanup timeout")

            block.release.set()
            await block.wait_finished()
            await settle(service)
            assert_consistent("settled")


async def test_the_harness_releases_a_parked_worker_when_the_body_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RELIABILITY-01-FIX-2 P1-2: a failing body must not strand its worker.

    ``asyncio`` cannot cancel the worker thread, so the only thing that can hand
    the executor slot back after an assertion failure is the teardown itself.
    Before the harness existed, a failure at ``wait_entered()`` or later left the
    worker parked until its own 30 s gate timeout — and the following tests paid
    for it. This drives the same failure and then asserts the harness did the
    releasing and the joining.
    """
    parked: BlockingFinalize | None = None
    parked_service: RuntimeService | None = None
    with project(tmp_path) as handle:
        with pytest.raises(RuntimeError, match="simulated failure"):
            async with finalization_harness(monkeypatch) as harness:
                parked_service = harness.runtime(cleanup_seconds=FAULT_CLEANUP_TIMEOUT_SECONDS)
                parked = harness.blocking_finalize()
                await _run_capture_until_blocked(parked_service, handle, parked)
                stop_task = asyncio.create_task(parked_service.stop_capture())
                await parked.wait_entered()
                await asyncio.wait_for(stop_task, timeout=10)

                # The worker is parked and the gate is still closed: this is the
                # exact state a failing CI assertion used to leave behind.
                assert parked.release.is_set() is False
                assert parked_service.finalization_pending is True
                raise RuntimeError("simulated failure")

        assert parked is not None and parked_service is not None
        # The teardown released the gate rather than waiting out its 30 s timeout…
        assert parked.release.is_set() is True
        # …and joined the worker, so nothing is left parked or outstanding.
        assert parked_service.finalization_pending is False
        assert await asyncio.to_thread(parked_service.wait_for_finalization, 5) is True


# ---------------------------------------------------------------- TEMPORARY
# CI diagnostics: REMOVED before handoff. Not part of the delivered fix.
async def test_zzz_temporary_worker_arrival_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """How long does the worker take to reach its blocking point on the runner?

    Two Run-1 failures — and the FIX-1 attempt-1 failures with them — are both
    "the worker never reached its blocking phase", which a message that only says
    "did not arrive inside the window" cannot diagnose. If the 0.8 s fault
    deadline expires before the worker thread is scheduled onto ``flush_pending``,
    the gate closes first and the worker never arrives at all, so the arrival time
    is the number the deadline has to be sized against.
    """
    import time

    rows: list[str] = []
    for index in range(6):
        with project(tmp_path / f"arrival-{index}") as handle:
            service = RuntimeService(
                project_max_frames_per_segment=SEGMENT_LIMIT,
                recorder_cleanup_timeout_seconds=FAULT_CLEANUP_TIMEOUT_SECONDS,
            )
            block = BlockingFinalize(monkeypatch, "flush_pending")
            await service.start_capture(
                VirtualAdapterConfig(rate_hz=RATE_HZ, seed=5),
                batch_size=BATCH_SIZE,
                project_path=handle.root,
            )
            await asyncio.sleep(0.25)
            stop_started = time.perf_counter()
            stop_task = asyncio.create_task(service.stop_capture())
            try:
                await block.signals.wait(
                    "entered", timeout=20, failure="worker never reached flush_pending"
                )
                arrival = time.perf_counter() - stop_started
            except AssertionError:
                arrival = -1.0
            await asyncio.wait_for(stop_task, timeout=30)
            code = service.failure.code if service.failure is not None else "none"
            block.release.set()
            await asyncio.wait_for(
                asyncio.to_thread(service.wait_for_finalization, 20), timeout=25
            )
            rows.append(f"[{index}] arrival={arrival:.3f}s failure={code}")
            monkeypatch.undo()
    assert False, "TEMPORARY-WORKER-ARRIVAL :: " + " || ".join(rows)
