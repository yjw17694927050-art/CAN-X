"""Timestamp and sequence normalization for captured frames."""

from dataclasses import replace

from canx.domain.frame import Frame


class TimestampNormalizer:
    """Assign one stream sequence and a relative monotonic timeline."""

    def __init__(self) -> None:
        self._first_host_timestamp: float | None = None
        self._next_sequence = 0

    def normalize(self, frame: Frame) -> Frame:
        """Return a frame normalized to this capture stream."""
        if self._first_host_timestamp is None:
            self._first_host_timestamp = frame.host_timestamp
        normalized = replace(
            frame,
            sequence=self._next_sequence,
            normalized_timestamp=max(0.0, frame.host_timestamp - self._first_host_timestamp),
        )
        self._next_sequence += 1
        return normalized
