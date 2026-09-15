"""Lock-safe runtime metrics collection with explicit ownership."""

import os
import threading
import time
from collections import deque
from collections.abc import Callable

import psutil  # type: ignore[import-untyped]

from canx.metrics.models import MetricsSnapshot

_COUNTERS = {
    "generated_frames",
    "captured_frames",
    "recorded_frames",
    "streamed_frames",
    "sequence_gaps",
    "dropped_frames",
    "dropped_stream_frames",
}
_QUEUES = {"ingress_queue_depth", "recorder_queue_depth", "stream_queue_depth"}


class _ProcessProbe:
    """Reuse and prime one psutil process so CPU values cover a real interval."""

    def __init__(self) -> None:
        try:
            self._process: psutil.Process | None = psutil.Process(os.getpid())
            self._process.cpu_percent(interval=None)
        except (psutil.Error, OSError):
            self._process = None

    def __call__(self) -> tuple[int, float] | None:
        process = self._process
        if process is None:
            return None
        try:
            return process.memory_info().rss, process.cpu_percent(interval=None)
        except (psutil.Error, OSError):
            return None


class MetricsCollector:
    """Own monotonic counters and current/peak queue gauges."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        system_probe: Callable[[], tuple[int, float] | None] | None = None,
        rate_window_seconds: float = 1.0,
    ) -> None:
        if rate_window_seconds <= 0:
            raise ValueError("rate_window_seconds must be positive")
        self._clock = clock
        self._system_probe = _ProcessProbe() if system_probe is None else system_probe
        self._started = clock()
        self._lock = threading.Lock()
        self._rate_window_seconds = rate_window_seconds
        self._counters = {name: 0 for name in _COUNTERS}
        self._rate_events: dict[str, deque[tuple[float, int]]] = {
            name: deque() for name in ("captured_frames", "recorded_frames", "streamed_frames")
        }
        self._queues = {name: 0 for name in _QUEUES}
        self._peaks = {name: 0 for name in _QUEUES}

    def increment(self, name: str, count: int = 1) -> None:
        """Increment one known monotonic counter."""
        if name not in _COUNTERS or count < 0:
            raise ValueError("unknown counter or negative increment")
        with self._lock:
            self._counters[name] += count
            if name in self._rate_events and count:
                self._rate_events[name].append((self._clock(), count))

    def set_counter(self, name: str, value: int) -> None:
        """Advance one known counter to an owner-provided monotonic value."""
        if name not in _COUNTERS or value < 0:
            raise ValueError("unknown counter or negative value")
        with self._lock:
            if value < self._counters[name]:
                raise ValueError("counter values must not decrease")
            delta = value - self._counters[name]
            self._counters[name] = value
            if name in self._rate_events and delta:
                self._rate_events[name].append((self._clock(), delta))

    def observe_queue(self, name: str, depth: int) -> None:
        """Record a non-negative queue depth and its lifetime peak."""
        if name not in _QUEUES or depth < 0:
            raise ValueError("unknown queue or negative depth")
        with self._lock:
            self._queues[name] = depth
            self._peaks[name] = max(self._peaks[name], depth)

    def snapshot(self, *, active_channels: int, recorder_state: str) -> MetricsSnapshot:
        """Return an internally consistent typed metrics snapshot."""
        uptime = max(0.0, self._clock() - self._started)
        probe = self._system_probe()
        with self._lock:
            cutoff = self._clock() - self._rate_window_seconds
            for events in self._rate_events.values():
                while events and events[0][0] < cutoff:
                    events.popleft()
            counters = dict(self._counters)
            queues = dict(self._queues)
            peaks = dict(self._peaks)
            rate_counts = {
                name: sum(count for _timestamp, count in events)
                for name, events in self._rate_events.items()
            }
        denominator = min(uptime, self._rate_window_seconds) if uptime > 0 else 1.0
        return MetricsSnapshot(
            **counters,
            capture_rate=rate_counts["captured_frames"] / denominator,
            record_rate=rate_counts["recorded_frames"] / denominator,
            stream_rate=rate_counts["streamed_frames"] / denominator,
            rate_window_seconds=self._rate_window_seconds,
            **queues,
            queue_peaks=peaks,
            memory_usage_bytes=None if probe is None else probe[0],
            runtime_cpu_percent=None if probe is None else probe[1],
            system_metrics_available=probe is not None,
            uptime_seconds=uptime,
            active_channels=active_channels,
            recorder_state=recorder_state,
        )
