"""Bounded subscriber queue policies for capture fan-out."""

import asyncio
from dataclasses import dataclass, field

from canx.domain.frame import Frame


@dataclass(slots=True)
class FrameSubscriber:
    """One independent bounded consumer of normalized frames."""

    name: str
    capacity: int
    lossy: bool
    dropped_frames: int = 0
    _queue: asyncio.Queue[Frame] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("subscriber name must not be empty")
        if self.capacity <= 0:
            raise ValueError("subscriber capacity must be positive")
        self._queue = asyncio.Queue(maxsize=self.capacity)

    async def publish(self, frame: Frame) -> None:
        """Publish with backpressure or explicit oldest-frame loss."""
        if not self.lossy:
            await self._queue.put(frame)
            return
        if self._queue.full():
            self._queue.get_nowait()
            self.dropped_frames += 1
        self._queue.put_nowait(frame)

    async def get(self) -> Frame:
        """Wait for the next queued frame."""
        return await self._queue.get()

    @property
    def queue_depth(self) -> int:
        """Return the current observable backlog."""
        return self._queue.qsize()
