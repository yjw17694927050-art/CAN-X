"""Runtime-owned orchestration for capture, batching, streaming, and recording."""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from enum import StrEnum
from math import isfinite
from pathlib import Path
from uuid import uuid4

from canx.capture.pipeline import CapturePipeline
from canx.capture.subscriber import FrameSubscriber, SubscriberDisabledError, SubscriberFailure
from canx.devices.virtual import VirtualAdapter, VirtualAdapterConfig
from canx.domain.batch import FrameBatch
from canx.domain.frame import Frame
from canx.metrics.collector import MetricsCollector
from canx.metrics.models import MetricsSnapshot
from canx.recorder.msgpack_recorder import MsgpackRecorder, RecorderFailure, RecorderState
from canx.transport.broker import BatchBroker


class CaptureSessionState(StrEnum):
    """Capture outcome, independent of control-plane process readiness."""

    IDLE = "idle"
    RUNNING = "running"
    DEGRADED = "degraded"
    FAILED = "failed"


class RuntimeService:
    """Coordinate independent runtime consumers without UI ownership."""

    def __init__(
        self,
        *,
        history_capacity: int = 100_000,
        recorder: MsgpackRecorder | None = None,
        archive_capacity: int | None = None,
        archive_publish_timeout_seconds: float = 1.0,
        recorder_cleanup_timeout_seconds: float = 1.0,
    ) -> None:
        if history_capacity <= 0:
            raise ValueError("history_capacity must be positive")
        if archive_capacity is not None and archive_capacity <= 0:
            raise ValueError("archive_capacity must be positive")
        for name, timeout in (
            ("archive_publish_timeout_seconds", archive_publish_timeout_seconds),
            ("recorder_cleanup_timeout_seconds", recorder_cleanup_timeout_seconds),
        ):
            if not isfinite(timeout) or timeout <= 0:
                raise ValueError(f"{name} must be finite and positive")
        self.broker = BatchBroker()
        self.metrics = MetricsCollector()
        self._history: deque[Frame] = deque(maxlen=history_capacity)
        self._pipeline: CapturePipeline | None = None
        self._archive_subscriber: FrameSubscriber | None = None
        self._stream_subscriber: FrameSubscriber | None = None
        self._archive_task: asyncio.Task[None] | None = None
        self._stream_task: asyncio.Task[None] | None = None
        self._recorder_cleanup_task: asyncio.Task[None] | None = None
        self._archive_pending_frames = 0
        self._archive_capacity = archive_capacity
        self._archive_publish_timeout_seconds = archive_publish_timeout_seconds
        self._recorder_cleanup_timeout_seconds = recorder_cleanup_timeout_seconds
        self._capture_state = CaptureSessionState.IDLE
        self._failure: RecorderFailure | None = None
        self._stop_event = asyncio.Event()
        self._lifecycle_lock = asyncio.Lock()
        self._batch_size = 250
        self._stream_id = ""
        self._recorder = MsgpackRecorder() if recorder is None else recorder
        self._active_channels = 0
        self._generated_total = 0
        self._captured_total = 0
        self._subscriber_drops_total = 0

    @property
    def capture_active(self) -> bool:
        return (
            self._pipeline is not None
            and self._pipeline.is_running
            and self._stream_task is not None
            and not self._stream_task.done()
        )

    @property
    def capture_state(self) -> CaptureSessionState:
        if self.has_session and not self.capture_active:
            return CaptureSessionState.FAILED
        return self._capture_state

    @property
    def failure(self) -> RecorderFailure | None:
        return self._failure

    @property
    def _consumer_tasks(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(task for task in (self._archive_task, self._stream_task) if task is not None)

    @property
    def has_session(self) -> bool:
        """Return whether a capture session still requires stop/cleanup."""
        return self._pipeline is not None

    async def start_capture(
        self,
        config: VirtualAdapterConfig,
        *,
        batch_size: int = 250,
        recording_path: Path | None = None,
    ) -> str:
        """Start one synthetic capture session and its sibling consumers."""
        async with self._lifecycle_lock:
            return await self._start_capture(
                config, batch_size=batch_size, recording_path=recording_path
            )

    async def _start_capture(
        self,
        config: VirtualAdapterConfig,
        *,
        batch_size: int,
        recording_path: Path | None,
    ) -> str:
        if self.has_session:
            raise RuntimeError("capture is already running")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self._stream_id = str(uuid4())
        self._history.clear()
        self._batch_size = batch_size
        self._active_channels = config.channel_count
        self._stop_event = asyncio.Event()
        pipeline = CapturePipeline(VirtualAdapter(config), self._handle_subscriber_failure)
        archive_subscriber = pipeline.subscribe(
            "archive",
            capacity=(
                max(batch_size * 4, 1_000)
                if self._archive_capacity is None
                else self._archive_capacity
            ),
            lossy=False,
            publish_timeout_seconds=self._archive_publish_timeout_seconds,
        )
        stream_subscriber = pipeline.subscribe(
            "stream", capacity=max(batch_size * 2, 500), lossy=True
        )
        self._pipeline = pipeline
        self._archive_subscriber = archive_subscriber
        self._stream_subscriber = stream_subscriber
        self._archive_pending_frames = 0
        try:
            if recording_path is not None:
                await self._recorder.start(recording_path, stream_id=self._stream_id)
            else:
                self._recorder.reset_session()
            self._archive_task = asyncio.create_task(
                self._run_consumer(
                    archive_subscriber,
                    self._publish_archive,
                    "recorder_queue_depth",
                    track_gaps=False,
                ),
                name="canx-archive",
            )
            self._stream_task = asyncio.create_task(
                self._run_consumer(
                    stream_subscriber,
                    self._publish_stream,
                    "stream_queue_depth",
                    track_gaps=True,
                ),
                name="canx-stream",
            )
            await pipeline.start()
        except BaseException as error:
            if isinstance(error, Exception):
                self._handle_recorder_failure(
                    code="recorder.open_failed",
                    message=str(error),
                    context={"stream_id": self._stream_id},
                )
            self._stop_event.set()
            for task in self._consumer_tasks:
                task.cancel()
            for task in self._consumer_tasks:
                with suppress(asyncio.CancelledError):
                    await task
            self._archive_task = None
            self._stream_task = None
            self._pipeline = None
            self._archive_subscriber = None
            self._stream_subscriber = None
            self._active_channels = 0
            self._capture_state = CaptureSessionState.FAILED
            await self._ensure_recorder_cleanup()
            self._recorder_cleanup_task = None
            raise
        self._failure = None
        self._capture_state = CaptureSessionState.RUNNING
        return self._stream_id

    async def stop_capture(self) -> None:
        """Stop ingress, drain queued frames, flush recorder, then stop streaming work."""
        async with self._lifecycle_lock:
            await self._stop_capture()

    async def _stop_capture(self) -> None:
        pipeline = self._pipeline
        if pipeline is None:
            return
        await pipeline.stop()
        self._sync_capture_counters(pipeline)
        self._sync_subscriber_drops()
        self._generated_total += pipeline.generated_frames
        self._captured_total += pipeline.captured_frames
        if self._stream_subscriber is not None:
            self._subscriber_drops_total += self._stream_subscriber.dropped_frames
        self._stop_event.set()
        live_tasks = tuple(task for task in self._consumer_tasks if not task.done())
        if live_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*live_tasks, return_exceptions=True),
                    timeout=self._recorder_cleanup_timeout_seconds,
                )
            except TimeoutError:
                self._handle_recorder_failure(
                    code="recorder.cleanup_timeout",
                    message="Archive drain exceeded its cleanup deadline",
                    context={"stream_id": self._stream_id, "operation": "drain"},
                )
        await self._ensure_recorder_cleanup()
        self._recorder_cleanup_task = None
        self._archive_pending_frames = 0
        self._archive_task = None
        self._stream_task = None
        self._pipeline = None
        self._archive_subscriber = None
        self._stream_subscriber = None
        self._active_channels = 0
        for queue_metric in ("ingress_queue_depth", "recorder_queue_depth", "stream_queue_depth"):
            self.metrics.observe_queue(queue_metric, 0)
        self._capture_state = (
            CaptureSessionState.FAILED if self._failure is not None else CaptureSessionState.IDLE
        )

    def _handle_subscriber_failure(self, failure: SubscriberFailure) -> None:
        if failure.subscriber != "archive":
            return
        # The subscriber disables its queue before invoking the owner. Preserve
        # the actual saturation depth from its pre-disable failure snapshot.
        self.metrics.observe_queue("recorder_queue_depth", failure.queue_depth)
        self._handle_recorder_failure(
            code="recorder.backpressure",
            message=failure.message,
            context={
                "subscriber": failure.subscriber,
                "capacity": failure.capacity,
                "queue_depth": failure.queue_depth,
                "frame_sequence": failure.frame_sequence,
                "stream_id": self._stream_id,
            },
            queued_frames=failure.queue_depth,
            rejected_frames=1,
        )

    def _handle_recorder_failure(
        self,
        *,
        code: str,
        message: str,
        context: dict[str, object],
        queued_frames: int | None = None,
        rejected_frames: int = 0,
    ) -> None:
        if self._failure is not None:
            return
        subscriber = self._archive_subscriber
        if queued_frames is None:
            queued_frames = 0 if subscriber is None else subscriber.queue_depth
        self._recorder.fail(code=code, message=message, recoverable=False, context=context)
        self._failure = self._recorder.failure
        self._capture_state = CaptureSessionState.DEGRADED
        self.metrics.increment("recorder_failures")
        if code == "recorder.backpressure":
            self.metrics.increment("recorder_backpressure_events")
        self.metrics.increment(
            "recorder_uncommitted_frames",
            queued_frames + self._archive_pending_frames + rejected_frames,
        )
        if self._pipeline is not None:
            self._pipeline.unsubscribe("archive")
        if subscriber is not None:
            subscriber.disable()
        self.metrics.observe_queue("recorder_queue_depth", 0)
        task = self._archive_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
        self._ensure_recorder_cleanup()

    def _ensure_recorder_cleanup(self) -> asyncio.Task[None]:
        if self._recorder_cleanup_task is None:
            self._recorder_cleanup_task = asyncio.create_task(
                self._cleanup_recorder(), name="canx-recorder-cleanup"
            )
        return self._recorder_cleanup_task

    async def _cleanup_recorder(self) -> None:
        """Join cancelled archive work and close the recorder under a deadline."""
        archive_task = self._archive_task
        if archive_task is not None:
            await asyncio.gather(archive_task, return_exceptions=True)
        self._archive_pending_frames = 0
        try:
            await asyncio.wait_for(
                self._recorder.stop(), timeout=self._recorder_cleanup_timeout_seconds
            )
        except TimeoutError:
            self._handle_recorder_failure(
                code="recorder.cleanup_timeout",
                message="Recorder close exceeded its cleanup deadline",
                context={"stream_id": self._stream_id, "operation": "close"},
            )
        except Exception as error:
            self._handle_recorder_failure(
                code="recorder.flush_failed",
                message=str(error),
                context={"stream_id": self._stream_id},
            )

    async def _run_consumer(
        self,
        subscriber: FrameSubscriber,
        publish: Callable[[list[Frame]], Awaitable[None]],
        queue_metric: str,
        *,
        track_gaps: bool,
    ) -> None:
        """Consume task exceptions at the owner boundary as soon as they occur."""
        try:
            await self._consume_subscriber(subscriber, publish, queue_metric, track_gaps=track_gaps)
        except SubscriberDisabledError:
            # Archive backpressure disables its queue before the capture
            # pipeline invokes the failure callback.  That ordering is an
            # expected shutdown path for this task, not a consumer failure.
            if subscriber.name != "archive" and self._failure is None:
                raise
        except Exception as error:
            self._handle_recorder_failure(
                code="recorder.write_failed",
                message=str(error),
                context={"stream_id": self._stream_id, "subscriber": subscriber.name},
            )
            if track_gaps:
                self._capture_state = CaptureSessionState.FAILED

    async def _consume_subscriber(
        self,
        subscriber: FrameSubscriber,
        publish: Callable[[list[Frame]], Awaitable[None]],
        queue_metric: str,
        *,
        track_gaps: bool,
    ) -> None:
        pending: list[Frame] = []
        last_sequence: int | None = None
        while not self._stop_event.is_set() or subscriber.queue_depth > 0:
            try:
                frame = await asyncio.wait_for(subscriber.get(), timeout=0.02)
            except TimeoutError:
                if pending:
                    await publish(pending)
                    pending = []
                    if not track_gaps:
                        self._archive_pending_frames = 0
                continue
            if last_sequence is not None and frame.sequence != last_sequence + 1:
                gap = max(0, frame.sequence - last_sequence - 1)
                if track_gaps and gap:
                    self.metrics.increment("sequence_gaps", gap)
                if pending:
                    await publish(pending)
                    pending = []
            last_sequence = frame.sequence
            pending.append(frame)
            if not track_gaps:
                self._archive_pending_frames = len(pending)
            self.metrics.observe_queue("ingress_queue_depth", subscriber.queue_depth)
            self.metrics.observe_queue(queue_metric, subscriber.queue_depth)
            if len(pending) >= self._batch_size:
                await publish(pending)
                pending = []
                if not track_gaps:
                    self._archive_pending_frames = 0
        if pending:
            await publish(pending)
        if not track_gaps:
            self._archive_pending_frames = 0

    async def _publish_archive(self, frames: list[Frame]) -> None:
        batch = FrameBatch.create(stream_id=self._stream_id, frames=frames)
        self._history.extend(frames)
        if self._recorder.state is RecorderState.RECORDING:
            await self._recorder.append(batch)
            self.metrics.increment("recorded_frames", batch.frame_count)

    async def _publish_stream(self, frames: list[Frame]) -> None:
        batch = FrameBatch.create(stream_id=self._stream_id, frames=frames)
        dropped_before = self.broker.dropped_frames
        await self.broker.publish(batch)
        dropped = self.broker.dropped_frames - dropped_before
        if dropped:
            self.metrics.increment("dropped_stream_frames", dropped)

    def frames(self) -> tuple[Frame, ...]:
        """Return an immutable snapshot from the bounded runtime query store."""
        return tuple(self._history)

    def metrics_snapshot(self) -> MetricsSnapshot:
        """Return metrics with current runtime-owned lifecycle dimensions."""
        if self._pipeline is not None:
            self._sync_capture_counters(self._pipeline)
        self._sync_subscriber_drops()
        return self.metrics.snapshot(
            active_channels=self._active_channels,
            recorder_state=self._recorder.state.value,
            recorder_failure=self._failure,
        )

    def _sync_capture_counters(self, pipeline: CapturePipeline) -> None:
        self.metrics.set_counter(
            "generated_frames", self._generated_total + pipeline.generated_frames
        )
        self.metrics.set_counter("captured_frames", self._captured_total + pipeline.captured_frames)

    def _sync_subscriber_drops(self) -> None:
        current = 0
        if self._stream_subscriber is not None:
            current = self._stream_subscriber.dropped_frames
        self.metrics.set_counter("dropped_frames", self._subscriber_drops_total + current)
