"""Independent bounded queues for realtime WebSocket clients."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from canx.domain.batch import FrameBatch


class BatchBroker:
    """Fan batches out to clients without allowing them to block capture."""

    def __init__(self, *, client_capacity: int = 8) -> None:
        if client_capacity <= 0:
            raise ValueError("client_capacity must be positive")
        self._client_capacity = client_capacity
        self._clients: set[asyncio.Queue[FrameBatch]] = set()
        self._condition = asyncio.Condition()
        self.dropped_frames = 0

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[FrameBatch]]:
        """Register a bounded client queue for the context lifetime."""
        queue: asyncio.Queue[FrameBatch] = asyncio.Queue(maxsize=self._client_capacity)
        async with self._condition:
            self._clients.add(queue)
            self._condition.notify_all()
        try:
            yield queue
        finally:
            async with self._condition:
                self._clients.discard(queue)

    async def publish(self, batch: FrameBatch) -> None:
        """Publish to all current clients, replacing oldest work under pressure."""
        for queue in tuple(self._clients):
            if queue.full():
                dropped = queue.get_nowait()
                self.dropped_frames += dropped.frame_count
            queue.put_nowait(batch)

    async def wait_for_subscribers(self, count: int) -> None:
        """Wait until a test or producer observes the requested client count."""
        async with self._condition:
            await self._condition.wait_for(lambda: len(self._clients) >= count)
