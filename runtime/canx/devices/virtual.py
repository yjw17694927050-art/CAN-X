"""Deterministic synthetic CAN adapter for tests and V0.1 validation."""

import asyncio
import random
from dataclasses import dataclass

from canx.devices.base import AdapterCapabilities, AdapterClosedError, AdapterStatistics
from canx.domain.frame import Direction, Frame, TimestampQuality

_FD_LENGTHS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)


@dataclass(frozen=True, slots=True)
class VirtualAdapterConfig:
    """Configuration for a reproducible synthetic CAN stream."""

    channel_count: int = 1
    is_fd: bool = False
    is_extended: bool = False
    rate_hz: float = 1_000.0
    seed: int = 1

    def __post_init__(self) -> None:
        if self.channel_count not in {1, 4, 8}:
            raise ValueError("channel_count must be 1, 4, or 8")
        if self.rate_hz <= 0:
            raise ValueError("rate_hz must be positive")


class VirtualAdapter:
    """Generate deterministic frame content on a real monotonic schedule."""

    def __init__(self, config: VirtualAdapterConfig) -> None:
        self._config = config
        self._random = random.Random(config.seed)
        self._is_open = False
        self._sequence = 0
        self._next_deadline = 0.0

    async def open(self) -> None:
        """Reset and activate the deterministic stream."""
        loop = asyncio.get_running_loop()
        self._random.seed(self._config.seed)
        self._sequence = 0
        self._next_deadline = loop.time()
        self._is_open = True

    async def close(self) -> None:
        """Deactivate the stream."""
        self._is_open = False

    async def recv(self) -> Frame:
        """Return the next generated frame at the configured aggregate rate."""
        if not self._is_open:
            raise AdapterClosedError("VirtualAdapter is not open")
        loop = asyncio.get_running_loop()
        delay = self._next_deadline - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
        host_timestamp = loop.time()
        sequence = self._sequence
        self._sequence += 1
        self._next_deadline += 1.0 / self._config.rate_hz

        dlc = self._random.choice(_FD_LENGTHS) if self._config.is_fd else self._random.randrange(9)
        maximum_id = 0x1FFFFFFF if self._config.is_extended else 0x7FF
        return Frame(
            sequence=sequence,
            channel_id=f"can{sequence % self._config.channel_count}",
            arbitration_id=self._random.randint(0, maximum_id),
            is_extended=self._config.is_extended,
            is_fd=self._config.is_fd,
            bitrate_switch=self._config.is_fd,
            error_state_indicator=False,
            dlc=dlc,
            data=self._random.randbytes(dlc),
            direction=Direction.RX,
            hardware_timestamp=None,
            host_timestamp=host_timestamp,
            normalized_timestamp=host_timestamp,
            clock_domain="host.monotonic",
            timestamp_quality=TimestampQuality.HOST,
            flags=0,
        )

    def capabilities(self) -> AdapterCapabilities:
        """Report receive-only synthetic capabilities."""
        return AdapterCapabilities(
            classic_can=True,
            can_fd=True,
            hardware_timestamp=False,
            tx=False,
            listen_only=True,
            error_frames=False,
            bus_statistics=True,
            multi_channel=self._config.channel_count > 1,
            hardware_sync=False,
        )

    def statistics(self) -> AdapterStatistics:
        """Return the current generated-frame count."""
        return AdapterStatistics(generated_frames=self._sequence)
