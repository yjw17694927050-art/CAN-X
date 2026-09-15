"""Adapter-to-subscriber capture orchestration."""

import asyncio
from contextlib import suppress

from canx.capture.normalizer import TimestampNormalizer
from canx.capture.subscriber import FrameSubscriber
from canx.devices.base import CanAdapter


class CapturePipeline:
    """Capture normalized frames and fan them out without UI ownership."""

    def __init__(self, adapter: CanAdapter) -> None:
        self._adapter = adapter
        self._normalizer = TimestampNormalizer()
        self._subscribers: dict[str, FrameSubscriber] = {}
        self._task: asyncio.Task[None] | None = None
        self._captured_frames = 0

    def subscribe(self, name: str, *, capacity: int, lossy: bool) -> FrameSubscriber:
        """Register one named subscriber before capture starts."""
        if name in self._subscribers:
            raise ValueError(f"subscriber already exists: {name}")
        subscriber = FrameSubscriber(name=name, capacity=capacity, lossy=lossy)
        self._subscribers[name] = subscriber
        return subscriber

    async def start(self) -> None:
        """Open the adapter and start the capture worker."""
        if self._task is not None:
            raise RuntimeError("capture is already running")
        await self._adapter.open()
        self._task = asyncio.create_task(self._capture_loop(), name="canx-capture")

    async def stop(self) -> None:
        """Stop adapter input and await worker cleanup."""
        task = self._task
        if task is None:
            return
        await self._adapter.close()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None

    async def _capture_loop(self) -> None:
        while True:
            frame = self._normalizer.normalize(await self._adapter.recv())
            for subscriber in self._subscribers.values():
                await subscriber.publish(frame)
            self._captured_frames += 1

    @property
    def captured_frames(self) -> int:
        """Return the number of frames published to all lossless consumers."""
        return self._captured_frames

    @property
    def generated_frames(self) -> int:
        """Return the adapter-owned generated frame counter."""
        return self._adapter.statistics().generated_frames

    @property
    def is_running(self) -> bool:
        """Return whether a capture worker is owned by this pipeline."""
        return self._task is not None and not self._task.done()
