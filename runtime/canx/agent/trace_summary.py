"""Deterministic read-only summaries over runtime-owned frames."""

from collections import Counter
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, model_validator

from canx.domain.frame import Frame


class TraceSummaryInput(BaseModel):
    """Validated filter for trace summary computation."""

    model_config = ConfigDict(frozen=True)
    start_time: float
    end_time: float
    channel_id: str | None = None
    arbitration_id: int | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "TraceSummaryInput":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        return self


class TopId(BaseModel):
    model_config = ConfigDict(frozen=True)
    arbitration_id: int
    count: int


class TimeRange(BaseModel):
    model_config = ConfigDict(frozen=True)
    start_time: float
    end_time: float


class TraceSummaryOutput(BaseModel):
    """Stable output for the V0.1 trace.summary tool."""

    model_config = ConfigDict(frozen=True)
    frame_count: int
    unique_ids: int
    top_ids: tuple[TopId, ...]
    frame_rate: float
    time_range: TimeRange


def summarize_frames(frames: Iterable[Frame], query: TraceSummaryInput) -> TraceSummaryOutput:
    """Filter and summarize frames with deterministic tie ordering."""
    selected = [
        frame
        for frame in frames
        if query.start_time <= frame.normalized_timestamp <= query.end_time
        and (query.channel_id is None or frame.channel_id == query.channel_id)
        and (query.arbitration_id is None or frame.arbitration_id == query.arbitration_id)
    ]
    counts = Counter(frame.arbitration_id for frame in selected)
    top_ids = tuple(
        TopId(arbitration_id=arbitration_id, count=count)
        for arbitration_id, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[
            :10
        ]
    )
    duration = query.end_time - query.start_time
    return TraceSummaryOutput(
        frame_count=len(selected),
        unique_ids=len(counts),
        top_ids=top_ids,
        frame_rate=len(selected) / duration,
        time_range=TimeRange(start_time=query.start_time, end_time=query.end_time),
    )
