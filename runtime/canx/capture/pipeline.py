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
        self._stopping = False
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
        self._stopping = False
        self._task = asyncio.create_task(self._capture_loop(), name="canx-capture")

    async def stop(self) -> None:
        """Stop adapter input and await worker cleanup."""
        task = self._task
        if task is None:
            return
        self._stopping = True
        await self._adapter.close()
        # Let already scheduled fan-out tasks accept the last received frame.
        # Blocked publications are still cancelled below, so stop stays bounded.
        await asyncio.sleep(0)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None

    async def _capture_loop(self) -> None:
        while not self._stopping:
            frame = self._normalizer.normalize(await self._adapter.recv())
            subscribers = tuple(self._subscribers.values())
            deliveries = [
                asyncio.create_task(subscriber.publish(frame)) for subscriber in subscribers
            ]
            cancellation: asyncio.CancelledError | None = None
            try:
                results = await asyncio.gather(*deliveries, return_exceptions=True)
            except asyncio.CancelledError as error:
                # A queue can accept a frame before gather resumes. Preserve settled
                # delivery outcomes on stop without counting cancelled publications.
                cancellation = error
                results = [
                    asyncio.CancelledError() if task.cancelled() else task.exception()
                    for task in deliveries
                ]
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
                elif isinstance(result, asyncio.CancelledError) and cancellation is not None:
                    continue
                elif isinstance(result, BaseException):
                    raise result
            if any(task.cancelled() for task in deliveries):
                raise asyncio.CancelledError
            self._captured_frames += 1
            if cancellation is not None:
                raise cancellation

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
