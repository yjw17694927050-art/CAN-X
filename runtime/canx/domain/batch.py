"""Versioned realtime batches of canonical frames."""

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import ClassVar

from canx.domain.frame import Frame


@dataclass(frozen=True, slots=True)
class FrameBatch:
    """A non-empty contiguous batch within one capture stream."""

    CURRENT_SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int
    stream_id: str
    first_sequence: int
    last_sequence: int
    frame_count: int
    frames: tuple[Frame, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.CURRENT_SCHEMA_VERSION:
            raise ValueError("unsupported FrameBatch schema_version")
        if not self.stream_id:
            raise ValueError("stream_id must not be empty")
        if not self.frames:
            raise ValueError("FrameBatch frames must be non-empty")
        expected = tuple(range(self.frames[0].sequence, self.frames[0].sequence + len(self.frames)))
        actual = tuple(frame.sequence for frame in self.frames)
        if actual != expected:
            raise ValueError("FrameBatch sequences must be contiguous")
        if self.first_sequence != actual[0] or self.last_sequence != actual[-1]:
            raise ValueError("FrameBatch sequence bounds do not match frames")
        if self.frame_count != len(self.frames):
            raise ValueError("FrameBatch frame_count does not match frames")

    @classmethod
    def create(cls, *, stream_id: str, frames: Sequence[Frame]) -> "FrameBatch":
        """Create a validated batch and derive all redundant metadata."""
        frozen_frames = tuple(frames)
        if not frozen_frames:
            raise ValueError("FrameBatch frames must be non-empty")
        return cls(
            schema_version=cls.CURRENT_SCHEMA_VERSION,
            stream_id=stream_id,
            first_sequence=frozen_frames[0].sequence,
            last_sequence=frozen_frames[-1].sequence,
            frame_count=len(frozen_frames),
            frames=frozen_frames,
        )


def batch_frames(frames: Iterable[Frame], *, size: int, stream_id: str) -> Iterator[FrameBatch]:
    """Yield contiguous batches while preserving the final partial batch."""
    if size <= 0:
        raise ValueError("batch size must be positive")
    pending: list[Frame] = []
    previous_sequence: int | None = None
    for frame in frames:
        if previous_sequence is not None and frame.sequence != previous_sequence + 1:
            raise ValueError("input frame sequences must be contiguous")
        pending.append(frame)
        previous_sequence = frame.sequence
        if len(pending) == size:
            yield FrameBatch.create(stream_id=stream_id, frames=pending)
            pending = []
    if pending:
        yield FrameBatch.create(stream_id=stream_id, frames=pending)
