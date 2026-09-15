"""Hardware-neutral adapter contracts."""

from dataclasses import dataclass
from typing import Protocol

from canx.domain.frame import Frame


class AdapterClosedError(RuntimeError):
    """Raised when receive is requested from an inactive adapter."""


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    """Capabilities consumed by Runtime clients instead of vendor names."""

    classic_can: bool
    can_fd: bool
    hardware_timestamp: bool
    tx: bool
    listen_only: bool
    error_frames: bool
    bus_statistics: bool
    multi_channel: bool
    hardware_sync: bool


@dataclass(frozen=True, slots=True)
class AdapterStatistics:
    """Monotonic counters owned by one adapter instance."""

    generated_frames: int


class CanAdapter(Protocol):
    """Minimum receive-only adapter interface required by V0.1."""

    async def open(self) -> None:
        """Open the configured adapter."""

    async def close(self) -> None:
        """Close the adapter and release its resources."""

    async def recv(self) -> Frame:
        """Receive the next canonical frame."""

    def capabilities(self) -> AdapterCapabilities:
        """Return stable adapter capabilities."""

    def statistics(self) -> AdapterStatistics:
        """Return an immutable statistics snapshot."""
