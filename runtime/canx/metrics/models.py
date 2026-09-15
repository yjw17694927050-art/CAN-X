"""Typed metrics exposed by the runtime control plane."""

from pydantic import BaseModel, ConfigDict


class MetricsSnapshot(BaseModel):
    """Point-in-time counters, rates, gauges, and availability metadata."""

    model_config = ConfigDict(frozen=True)

    generated_frames: int
    captured_frames: int
    recorded_frames: int
    streamed_frames: int
    sequence_gaps: int
    dropped_frames: int
    dropped_stream_frames: int
    capture_rate: float
    record_rate: float
    stream_rate: float
    rate_window_seconds: float
    ingress_queue_depth: int
    recorder_queue_depth: int
    stream_queue_depth: int
    queue_peaks: dict[str, int]
    memory_usage_bytes: int | None
    runtime_cpu_percent: float | None
    system_metrics_available: bool
    uptime_seconds: float
    active_channels: int
    recorder_state: str
