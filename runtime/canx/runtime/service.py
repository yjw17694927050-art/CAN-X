"""Runtime-owned orchestration for capture, batching, streaming, and recording."""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from canx.capture.pipeline import CapturePipeline
from canx.capture.subscriber import FrameSubscriber
from canx.devices.virtual import VirtualAdapter, VirtualAdapterConfig
from canx.domain.batch import FrameBatch
from canx.domain.frame import Frame
from canx.metrics.collector import MetricsCollector
from canx.metrics.models import MetricsSnapshot
from canx.recorder.msgpack_recorder import MsgpackRecorder, RecorderState
from canx.transport.broker import BatchBroker


class RuntimeService:
    """Coordinate independent runtime consumers without UI ownership."""

    def __init__(
        self, *, history_capacity: int = 100_000, recorder: MsgpackRecorder | None = None
    ) -> None:
        if history_capacity <= 0:
            raise ValueError("history_capacity must be positive")
        self.broker = BatchBroker()
        self.metrics = MetricsCollector()
        self._history: deque[Frame] = deque(maxlen=history_capacity)
        self._pipeline: CapturePipeline | None = None
        self._archive_subscriber: FrameSubscriber | None = None
        self._stream_subscriber: FrameSubscriber | None = None
        self._consumer_tasks: tuple[asyncio.Task[None], ...] = ()
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
            and all(not task.done() for task in self._consumer_tasks)
        )

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
        pipeline = CapturePipeline(VirtualAdapter(config))
        archive_subscriber = pipeline.subscribe(
            "archive", capacity=max(batch_size * 4, 1_000), lossy=False
        )
        stream_subscriber = pipeline.subscribe(
            "stream", capacity=max(batch_size * 2, 500), lossy=True
        )
        self._pipeline = pipeline
        self._archive_subscriber = archive_subscriber
        self._stream_subscriber = stream_subscriber
        if recording_path is not None:
            await self._recorder.start(recording_path, stream_id=self._stream_id)
        self._consumer_tasks = (
            asyncio.create_task(
                self._consume_subscriber(
                    archive_subscriber,
                    self._publish_archive,
                    "recorder_queue_depth",
                    track_gaps=False,
                ),
                name="canx-archive",
            ),
            asyncio.create_task(
                self._consume_subscriber(
                    stream_subscriber,
                    self._publish_stream,
                    "stream_queue_depth",
                    track_gaps=True,
                ),
                name="canx-stream",
            ),
        )
        try:
            await pipeline.start()
        except BaseException:
            self._stop_event.set()
            for task in self._consumer_tasks:
                task.cancel()
            for task in self._consumer_tasks:
                with suppress(asyncio.CancelledError):
                    await task
            self._consumer_tasks = ()
            self._pipeline = None
            self._archive_subscriber = None
            self._stream_subscriber = None
            await self._recorder.stop()
            raise
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
        errors = await asyncio.gather(*self._consumer_tasks, return_exceptions=True)
        await self._recorder.stop()
        self._consumer_tasks = ()
        self._pipeline = None
        self._archive_subscriber = None
        self._stream_subscriber = None
        self._active_channels = 0
        for error in errors:
            if isinstance(error, BaseException):
                raise error

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
            self.metrics.observe_queue("ingress_queue_depth", subscriber.queue_depth)
            self.metrics.observe_queue(queue_metric, subscriber.queue_depth)
            if len(pending) >= self._batch_size:
                await publish(pending)
                pending = []
        if pending:
            await publish(pending)

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
