"""Canonical, UI-independent CAN frame model."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

_CLASSIC_DLC = frozenset(range(9))
_FD_DLC = _CLASSIC_DLC | frozenset({12, 16, 20, 24, 32, 48, 64})


class Direction(StrEnum):
    """Direction observed at the runtime adapter boundary."""

    RX = "rx"
    TX = "tx"


class TimestampQuality(StrEnum):
    """Quality of the source used for the normalized timestamp."""

    HARDWARE = "hardware"
    HOST = "host"
    ESTIMATED = "estimated"


@dataclass(frozen=True, slots=True)
class Frame:
    """One immutable CAN or CAN FD frame captured by the runtime."""

    sequence: int
    channel_id: str
    arbitration_id: int
    is_extended: bool
    is_fd: bool
    bitrate_switch: bool
    error_state_indicator: bool
    dlc: int
    data: bytes
    direction: Direction
    hardware_timestamp: float | None
    host_timestamp: float
    normalized_timestamp: float
    clock_domain: str
    timestamp_quality: TimestampQuality
    flags: int

    def __post_init__(self) -> None:
        """Reject values that cannot represent a valid canonical frame."""
        integer_values = (self.sequence, self.arbitration_id, self.dlc, self.flags)
        if any(not isinstance(value, int) or isinstance(value, bool) for value in integer_values):
            raise ValueError("sequence, arbitration_id, DLC, and flags must be integers")
        boolean_values = (
            self.is_extended,
            self.is_fd,
            self.bitrate_switch,
            self.error_state_indicator,
        )
        if any(not isinstance(value, bool) for value in boolean_values):
            raise ValueError("frame flag fields must be booleans")
        if not 0 <= self.sequence <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("sequence must be an unsigned 64-bit integer")
        if not isinstance(self.channel_id, str) or not self.channel_id:
            raise ValueError("channel_id must not be empty")
        maximum_id = 0x1FFFFFFF if self.is_extended else 0x7FF
        id_kind = "extended" if self.is_extended else "standard"
        if not 0 <= self.arbitration_id <= maximum_id:
            raise ValueError(f"{id_kind} arbitration_id is out of range")
        allowed_dlc = _FD_DLC if self.is_fd else _CLASSIC_DLC
        if self.dlc not in allowed_dlc:
            raise ValueError("DLC is not valid for the selected CAN frame type")
        if not isinstance(self.data, bytes) or len(self.data) != self.dlc:
            raise ValueError("data length must equal DLC and data must be bytes")
        if not self.is_fd and (self.bitrate_switch or self.error_state_indicator):
            raise ValueError("Classic CAN cannot use CAN FD flags")
        if not isinstance(self.direction, Direction):
            raise ValueError("direction must be a Direction")
        if not isinstance(self.timestamp_quality, TimestampQuality):
            raise ValueError("timestamp_quality must be a TimestampQuality")
        timestamp_values = [self.host_timestamp, self.normalized_timestamp]
        if self.hardware_timestamp is not None:
            timestamp_values.append(self.hardware_timestamp)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or value < 0
            for value in timestamp_values
        ):
            raise ValueError("timestamps must be finite non-negative seconds")
        if not isinstance(self.clock_domain, str) or not self.clock_domain:
            raise ValueError("clock_domain must not be empty")
        if not 0 <= self.flags <= 0xFFFFFFFF:
            raise ValueError("flags must be an unsigned 32-bit bit set")
