"""Adapter-to-subscriber capture orchestration."""

import asyncio
from collections.abc import Callable
from contextlib import suppress

from canx.capture.normalizer import TimestampNormalizer
from canx.capture.subscriber import (
    FrameSubscriber,
    SubscriberBackpressureError,
    SubscriberDisabledError,
    SubscriberFailure,
)
from canx.devices.base import CanAdapter
from canx.domain.frame import Frame


class CapturePipeline:
    """Capture normalized frames and fan them out without UI ownership."""

    def __init__(
        self,
        adapter: CanAdapter,
        subscriber_failure_handler: Callable[[SubscriberFailure], None] | None = None,
    ) -> None:
        self._adapter = adapter
        self._normalizer = TimestampNormalizer()
        self._subscribers: dict[str, FrameSubscriber] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop_requested = asyncio.Event()
        self._captured_frames = 0
        self._subscriber_failure_handler = subscriber_failure_handler

    def subscribe(
        self,
        name: str,
        *,
        capacity: int,
        lossy: bool,
        publish_timeout_seconds: float | None = None,
    ) -> FrameSubscriber:
        """Register a named subscriber, optionally bounding lossless backpressure."""
        if name in self._subscribers:
            raise ValueError(f"subscriber already exists: {name}")
        subscriber = FrameSubscriber(
            name=name,
            capacity=capacity,
            lossy=lossy,
            publish_timeout_seconds=publish_timeout_seconds,
        )
        self._subscribers[name] = subscriber
        return subscriber

    def unsubscribe(self, name: str) -> FrameSubscriber | None:
        """Detach a subscriber; its owner decides whether to drain or disable it."""
        return self._subscribers.pop(name, None)

    async def start(self) -> None:
        """Open the adapter and start the capture worker."""
        if self._task is not None:
            raise RuntimeError("capture is already running")
        await self._adapter.open()
        self._stop_requested.clear()
        self._task = asyncio.create_task(self._capture_loop(), name="canx-capture")

    async def stop(self) -> None:
        """Stop adapter input and await worker cleanup."""
        task = self._task
        if task is None:
            return
        self._stop_requested.set()
        try:
            await task
        finally:
            await self._adapter.close()
            self._task = None

    async def _capture_loop(self) -> None:
        stopped = asyncio.create_task(self._stop_requested.wait())
        try:
            while not self._stop_requested.is_set():
                frame = await self._receive_frame(stopped)
                if frame is None:
                    break
                if await self._publish_frame(self._normalizer.normalize(frame), stopped):
                    self._captured_frames += 1
        finally:
            stopped.cancel()
            with suppress(asyncio.CancelledError):
                await stopped

    async def _receive_frame(self, stopped: asyncio.Task[bool]) -> Frame | None:
        receive = asyncio.create_task(self._adapter.recv())
        try:
            await asyncio.wait((receive, stopped), return_when=asyncio.FIRST_COMPLETED)
            # Receive may settle alongside stop. Its returned frame still belongs
            # to capture and must enter fan-out before shutdown can finish.
            if receive.done():
                return receive.result()
            return None
        finally:
            if not receive.done():
                receive.cancel()
                with suppress(asyncio.CancelledError):
                    await receive

    async def _publish_frame(self, frame: Frame, stopped: asyncio.Task[bool]) -> bool:
        subscribers = tuple(self._subscribers.values())
        if not subscribers:
            return True
        all_started = asyncio.Event()
        started_count = 0

        async def publish(subscriber: FrameSubscriber) -> None:
            nonlocal started_count
            started_count += 1
            if started_count == len(subscribers):
                all_started.set()
            # No yield between the handshake and publish: each subscriber gets
            # its first delivery attempt before stop can cancel blocked work.
            await subscriber.publish(frame)

        deliveries = [asyncio.create_task(publish(subscriber)) for subscriber in subscribers]
        fanout = asyncio.gather(*deliveries, return_exceptions=True)
        try:
            await all_started.wait()
            await asyncio.wait((fanout, stopped), return_when=asyncio.FIRST_COMPLETED)
            if not fanout.done():
                for delivery in deliveries:
                    if not delivery.done():
                        delivery.cancel()
            results = await fanout
        finally:
            if not fanout.done():
                for delivery in deliveries:
                    if not delivery.done():
                        delivery.cancel()
                # Also join children if cancellation interrupts phase coordination.
                await fanout
        settled = True
        for subscriber, result in zip(subscribers, results, strict=True):
            if isinstance(result, SubscriberBackpressureError):
                if self._subscribers.get(subscriber.name) is subscriber:
                    self.unsubscribe(subscriber.name)
                if self._subscriber_failure_handler is not None:
                    self._subscriber_failure_handler(result.failure)
            elif isinstance(result, SubscriberDisabledError) and (
                self._subscribers.get(subscriber.name) is not subscriber
            ):
                # Explicit detach/disable may race an already captured snapshot.
                continue
            elif isinstance(result, asyncio.CancelledError) and self._stop_requested.is_set():
                settled = False
            elif isinstance(result, BaseException):
                raise result
        return settled

    @property
    def captured_frames(self) -> int:
        """Count frames whose snapshot deliveries succeeded or explicitly failed."""
        return self._captured_frames

    @property
    def generated_frames(self) -> int:
        """Return the adapter-owned generated frame counter."""
        return self._adapter.statistics().generated_frames

    @property
    def is_running(self) -> bool:
        """Return whether a capture worker is owned by this pipeline."""
        return self._task is not None and not self._task.done()
