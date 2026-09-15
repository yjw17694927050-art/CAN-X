"""V0.1 runtime-only production-pipeline benchmark."""

import argparse
import asyncio
import json
import platform
import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict

from canx.devices.virtual import VirtualAdapterConfig
from canx.runtime.service import RuntimeService

Mode = Literal["classic", "fd"]
Load = Literal["low", "medium", "high"]
_LOAD_RATES: dict[Load, float] = {"low": 1_000.0, "medium": 5_000.0, "high": 20_000.0}
_CHANNELS: tuple[Literal[1, 4, 8], ...] = (1, 4, 8)
_MODES: tuple[Mode, ...] = ("classic", "fd")
_LOADS: tuple[Load, ...] = ("low", "medium", "high")


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """One reproducible point in the documented benchmark matrix."""

    seed: int
    channels: Literal[1, 4, 8]
    mode: Mode
    load: Load
    batch_size: int
    warmup_seconds: float
    duration_seconds: float

    @property
    def rate_hz(self) -> float:
        return _LOAD_RATES[self.load]


class BenchmarkResult(BaseModel):
    """Versioned raw benchmark result; unavailable values remain null."""

    model_config = ConfigDict(frozen=True)
    schema_version: Literal[1] = 1
    benchmark_kind: Literal["runtime-only"] = "runtime-only"
    python_version: str
    platform: str
    config: dict[str, object]
    measured_seconds: float
    generated_frames: int
    captured_frames: int
    recorded_frames: int
    streamed_frames: int
    sequence_gaps: int
    dropped_frames: int
    dropped_stream_frames: int
    queue_peaks: dict[str, int]
    memory_usage_bytes: int | None
    runtime_cpu_percent: float | None
    disk_bytes: int
    disk_throughput_bytes_per_second: float
    stream_latency_ms: float | None
    trace_render_latency_ms: float | None
    plot_update_latency_ms: float | None
    desktop_cpu_percent: float | None
    availability: dict[str, str]


def matrix_configs(
    *, seed: int, warmup_seconds: float, duration_seconds: float
) -> list[BenchmarkConfig]:
    """Return the complete required 90-point matrix in stable order."""
    return [
        BenchmarkConfig(seed, channels, mode, load, batch_size, warmup_seconds, duration_seconds)
        for channels, mode, load, batch_size in product(
            _CHANNELS, _MODES, _LOADS, (50, 100, 250, 500, 1000)
        )
    ]


async def _run_interval(
    config: BenchmarkConfig, path: Path | None, duration: float
) -> RuntimeService:
    service = RuntimeService()
    await service.start_capture(
        VirtualAdapterConfig(
            channel_count=config.channels,
            is_fd=config.mode == "fd",
            rate_hz=config.rate_hz,
            seed=config.seed,
        ),
        batch_size=config.batch_size,
        recording_path=path,
    )
    await asyncio.sleep(duration)
    await service.stop_capture()
    return service


async def run_benchmark(config: BenchmarkConfig, *, recording_path: Path) -> BenchmarkResult:
    """Run separate warm-up and measured intervals using production runtime code."""
    if config.warmup_seconds < 0 or config.duration_seconds <= 0:
        raise ValueError("warmup must be non-negative and duration must be positive")
    if config.warmup_seconds:
        await _run_interval(config, None, config.warmup_seconds)
    started = perf_counter()
    service = await _run_interval(config, recording_path, config.duration_seconds)
    elapsed = perf_counter() - started
    snapshot = service.metrics_snapshot()
    disk_bytes = recording_path.stat().st_size
    system_status = "measured" if snapshot.system_metrics_available else "NOT VERIFIED"
    return BenchmarkResult(
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        config={
            "seed": config.seed,
            "channels": config.channels,
            "mode": config.mode,
            "load": config.load,
            "rate_hz": config.rate_hz,
            "batch_size": config.batch_size,
            "warmup_seconds": config.warmup_seconds,
            "duration_seconds": config.duration_seconds,
        },
        measured_seconds=elapsed,
        generated_frames=snapshot.generated_frames,
        captured_frames=snapshot.captured_frames,
        recorded_frames=snapshot.recorded_frames,
        streamed_frames=snapshot.streamed_frames,
        sequence_gaps=snapshot.sequence_gaps,
        dropped_frames=snapshot.dropped_frames,
        dropped_stream_frames=snapshot.dropped_stream_frames,
        queue_peaks=snapshot.queue_peaks,
        memory_usage_bytes=snapshot.memory_usage_bytes,
        runtime_cpu_percent=snapshot.runtime_cpu_percent,
        disk_bytes=disk_bytes,
        disk_throughput_bytes_per_second=disk_bytes / elapsed,
        stream_latency_ms=None,
        trace_render_latency_ms=None,
        plot_update_latency_ms=None,
        desktop_cpu_percent=None,
        availability={
            "memory_usage_bytes": system_status,
            "runtime_cpu_percent": system_status,
            "stream_latency_ms": "NOT VERIFIED",
            "trace_render_latency_ms": "NOT VERIFIED",
            "plot_update_latency_ms": "NOT VERIFIED",
            "desktop_cpu_percent": "NOT VERIFIED",
        },
    )


def main() -> None:
    """Run one benchmark configuration and preserve raw JSON output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--channels", type=int, choices=(1, 4, 8), default=1)
    parser.add_argument("--mode", choices=("classic", "fd"), default="classic")
    parser.add_argument("--load", choices=("low", "medium", "high"), default="low")
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--warmup", type=float, default=0.5)
    parser.add_argument("--duration", type=float, default=2.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output: Path = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    recording = output.with_suffix(".canxmsg")
    config = BenchmarkConfig(
        seed=args.seed,
        channels=args.channels,
        mode=args.mode,
        load=args.load,
        batch_size=args.batch_size,
        warmup_seconds=args.warmup,
        duration_seconds=args.duration,
    )
    result = asyncio.run(run_benchmark(config, recording_path=recording))
    output.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "captured_frames": result.captured_frames}))


if __name__ == "__main__":
    main()
