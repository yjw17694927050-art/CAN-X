from canx.benchmarks.v01_pipeline import BenchmarkConfig, matrix_configs, run_benchmark


def test_benchmark_matrix_covers_required_dimensions() -> None:
    configs = matrix_configs(seed=1, warmup_seconds=0.0, duration_seconds=0.01)
    assert len(configs) == 3 * 2 * 3 * 5
    assert {config.channels for config in configs} == {1, 4, 8}
    assert {config.mode for config in configs} == {"classic", "fd"}
    assert {config.load for config in configs} == {"low", "medium", "high"}
    assert {config.batch_size for config in configs} == {50, 100, 250, 500, 1000}


async def test_runtime_benchmark_reports_measured_and_unavailable_fields(tmp_path) -> None:
    config = BenchmarkConfig(
        seed=9,
        channels=1,
        mode="classic",
        load="low",
        batch_size=50,
        warmup_seconds=0.0,
        duration_seconds=0.03,
    )
    result = await run_benchmark(config, recording_path=tmp_path / "run.canxmsg")
    assert result.schema_version == 1
    assert result.generated_frames > 0
    assert result.generated_frames == result.captured_frames == result.recorded_frames
    assert result.disk_bytes > 0
    assert result.trace_render_latency_ms is None
    assert result.availability["trace_render_latency_ms"] == "NOT VERIFIED"
