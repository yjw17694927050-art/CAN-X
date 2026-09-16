from canx.metrics.collector import MetricsCollector


def test_metrics_own_monotonic_counters_rates_and_queue_peaks() -> None:
    now = 10.0
    collector = MetricsCollector(
        clock=lambda: now,
        system_probe=lambda: (1234, 4.5),
        rate_window_seconds=10.0,
    )
    collector.increment("generated_frames", 4)
    collector.increment("captured_frames", 3)
    collector.observe_queue("ingress_queue_depth", 7)
    collector.observe_queue("ingress_queue_depth", 2)
    now = 12.0

    snapshot = collector.snapshot(active_channels=2, recorder_state="idle")

    assert snapshot.generated_frames == 4
    assert snapshot.captured_frames == 3
    assert snapshot.capture_rate == 1.5
    assert snapshot.ingress_queue_depth == 2
    assert snapshot.queue_peaks["ingress_queue_depth"] == 7
    assert snapshot.memory_usage_bytes == 1234
    assert snapshot.runtime_cpu_percent == 4.5
    assert snapshot.system_metrics_available is True


def test_metrics_report_unavailable_system_values_as_none() -> None:
    collector = MetricsCollector(clock=lambda: 1.0, system_probe=lambda: None)
    snapshot = collector.snapshot(active_channels=0, recorder_state="idle")
    assert snapshot.memory_usage_bytes is None
    assert snapshot.runtime_cpu_percent is None
    assert snapshot.system_metrics_available is False


def test_rates_expire_outside_the_rolling_window() -> None:
    now = 0.0
    collector = MetricsCollector(
        clock=lambda: now,
        system_probe=lambda: None,
        rate_window_seconds=1.0,
    )
    collector.increment("captured_frames", 10)
    now = 2.0
    assert collector.snapshot(active_channels=0, recorder_state="idle").capture_rate == 0


def test_recorder_failure_metrics_are_nullable_and_counters_are_owned() -> None:
    collector = MetricsCollector(clock=lambda: 1.0, system_probe=lambda: None)
    data = collector.snapshot(active_channels=0, recorder_state="idle").model_dump(mode="json")
    assert data["recorder_failure_code"] is None
    assert data["recorder_failure_message"] is None
    assert data["recorder_failure_recoverable"] is None
    assert data["recorder_failure_context"] is None
    for name, count in (
        ("recorder_backpressure_events", 1),
        ("recorder_failures", 1),
        ("recorder_uncommitted_frames", 8),
    ):
        collector.increment(name, count)
    data = collector.snapshot(active_channels=0, recorder_state="failed").model_dump()
    assert data["recorder_backpressure_events"] == 1
    assert data["recorder_failures"] == 1
    assert data["recorder_uncommitted_frames"] == 8
