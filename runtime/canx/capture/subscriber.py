"""Bounded subscriber queue policies for capture fan-out."""

import asyncio
from dataclasses import dataclass, field
from math import isfinite

from canx.domain.frame import Frame


@dataclass(frozen=True, slots=True)
class SubscriberFailure:
    """Immutable capture failure context collected before a queue is disabled."""

    code: str
    subscriber: str
    message: str
    recoverable: bool
    capacity: int
    queue_depth: int
    frame_sequence: int


class SubscriberBackpressureError(RuntimeError):
    """Raised after a lossless subscriber exceeds its publication deadline."""

    def __init__(self, failure: SubscriberFailure) -> None:
        self.failure = failure
        super().__init__(failure.message)


class SubscriberDisabledError(RuntimeError):
    """Raised when publication or consumption encounters a disabled subscriber."""


@dataclass(slots=True)
class FrameSubscriber:
    """One independent bounded consumer of normalized frames."""

    name: str
    capacity: int
    lossy: bool
    dropped_frames: int = 0
    publish_timeout_seconds: float | None = None
    _queue: asyncio.Queue[Frame] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("subscriber name must not be empty")
        if self.capacity <= 0:
            raise ValueError("subscriber capacity must be positive")
        if self.publish_timeout_seconds is not None and (
            not isfinite(self.publish_timeout_seconds) or self.publish_timeout_seconds <= 0
        ):
            raise ValueError("subscriber publish timeout must be finite and positive")
        self._queue = asyncio.Queue(maxsize=self.capacity)

    async def publish(self, frame: Frame) -> None:
        """Publish, raising pressure on deadline or disabled after shutdown."""
        try:
            if not self.lossy:
                await asyncio.wait_for(self._queue.put(frame), timeout=self.publish_timeout_seconds)
                return
            if self._queue.full():
                self._queue.get_nowait()
                self.dropped_frames += 1
            self._queue.put_nowait(frame)
        except TimeoutError as error:
            failure = SubscriberFailure(
                code="subscriber.backpressure",
                subscriber=self.name,
                message=f"Subscriber {self.name!r} exceeded its publication deadline",
                recoverable=False,
                capacity=self.capacity,
                queue_depth=self.queue_depth,
                frame_sequence=frame.sequence,
            )
            self.disable()
            raise SubscriberBackpressureError(failure) from error
        except asyncio.QueueShutDown as error:
            raise SubscriberDisabledError(f"Subscriber {self.name!r} is disabled") from error

    def disable(self) -> int:
        """Reject delivery, wake waiters, and return the discarded queue depth."""
        queued_frames = self.queue_depth
        self._queue.shutdown(immediate=True)
        return queued_frames

    async def get(self) -> Frame:
        """Wait for a frame, raising SubscriberDisabledError after shutdown."""
        try:
            return await self._queue.get()
        except asyncio.QueueShutDown as error:
            raise SubscriberDisabledError(f"Subscriber {self.name!r} is disabled") from error

    @property
    def queue_depth(self) -> int:
        """Return the current observable backlog."""
        return self._queue.qsize()
