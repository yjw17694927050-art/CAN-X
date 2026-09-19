"""Large-data structural proof for Runtime-side plot downsampling.

The point of this file is *structural*, not a wall-clock gate: it proves that

* the reducer's working set tracks the requested sample budget and never the
  length of the input, and
* a real, large synthetic session downsamples end to end to at most the budget.

Timings are printed as stable benchmark numbers for a human to read; they are
deliberately **not** asserted, because a threshold on this machine would be a
flaky CI gate rather than a property of the code.
"""

from __future__ import annotations

import time
from pathlib import Path

from canx.data.session import DataSessionService
from canx.dbc.project_service import ProjectDbcService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.plot.downsample import StreamingDownsampler
from canx.plot.model import PlotQuery, PlotSample, SignalIdentity
from canx.plot.service import HistoricalSignalQueryService
from canx.project.service import ProjectService

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BASIC_FIXTURE = REPOSITORY_ROOT / "tests" / "fixtures" / "dbc" / "basic_standard.dbc"

#: The reducer-side measured input: a million synthetic points.
REDUCER_POINTS = 1_000_000

#: The end-to-end measured session: a real Parquet-backed session.
SESSION_FRAMES = 25_000
FRAMES_PER_SEGMENT = 5_000
SESSION_BUDGET = 1_000
STREAM_ID = "perf-stream"
ENGINE_DATA_ID = 291


def synthetic_points(count: int) -> list[PlotSample]:
    return [
        PlotSample(time=float(sequence), value=float(sequence % 17), sequence=sequence)
        for sequence in range(count)
    ]


def test_the_reducer_working_set_is_independent_of_input_length() -> None:
    """A million points reduce inside a budget-sized working set."""
    points = synthetic_points(REDUCER_POINTS)
    reducer = StreamingDownsampler(sample_budget=SESSION_BUDGET)

    started = time.perf_counter()
    peak = 0
    for sample in points:
        reducer.add(sample)
        peak = max(peak, reducer.retained_point_count)
    elapsed = time.perf_counter() - started

    result = reducer.finish()
    assert reducer.added_count == REDUCER_POINTS
    assert peak <= SESSION_BUDGET
    assert len(result) <= SESSION_BUDGET
    assert result[0] == points[0]
    assert result[-1] == points[-1]
    print(
        f"\n[downsampling] points={REDUCER_POINTS} budget={SESSION_BUDGET}"
        f" peak_working_set={peak} output={len(result)} seconds={elapsed:.3f}"
    )


def test_the_working_set_does_not_grow_with_the_dataset() -> None:
    """Ten thousand and one million points share the same bounded working set."""
    peaks = []
    for count in (10_000, 200_000, 1_000_000):
        reducer = StreamingDownsampler(sample_budget=SESSION_BUDGET)
        peak = 0
        for sample in synthetic_points(count):
            reducer.add(sample)
            peak = max(peak, reducer.retained_point_count)
        peaks.append(peak)

    assert all(peak <= SESSION_BUDGET for peak in peaks)
    assert len(set(peaks)) == 1  # the same ceiling, regardless of input length.


def _engine_data(raw_engine_speed: int) -> bytes:
    return bytes(
        [raw_engine_speed & 0xFF, (raw_engine_speed >> 8) & 0xFF, 0, 0, 0, 0, 0, 0]
    )


def _frame(sequence: int) -> Frame:
    return Frame(
        sequence=sequence,
        channel_id="can0",
        arbitration_id=ENGINE_DATA_ID,
        is_extended=False,
        is_fd=False,
        bitrate_switch=False,
        error_state_indicator=False,
        dlc=8,
        data=_engine_data(sequence % 4_000),
        direction=Direction.RX,
        hardware_timestamp=None,
        host_timestamp=1_000.0 + sequence,
        normalized_timestamp=float(sequence) * 0.01,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


def test_a_large_synthetic_session_downsamples_end_to_end(tmp_path: Path) -> None:
    """A real 25k-frame Parquet session becomes a bounded series, end to end."""
    handle = ProjectService().create(tmp_path / "perf.canx", display_name="Perf")
    with handle:
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        source = inbox / "basic.dbc"
        source.write_bytes(BASIC_FIXTURE.read_bytes())
        asset_id = ProjectDbcService(handle.root).import_asset(source).asset_id

        service = DataSessionService(
            handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT
        )
        writer = service.start(stream_id=STREAM_ID)
        for start in range(0, SESSION_FRAMES, FRAMES_PER_SEGMENT):
            batch = [_frame(sequence) for sequence in range(start, start + FRAMES_PER_SEGMENT)]
            writer.append(FrameBatch.create(stream_id=STREAM_ID, frames=batch))
        writer.finalize()
        session_id = writer.session_id

        identity = SignalIdentity(
            channel_id="can0",
            asset_id=asset_id,
            message_name="EngineData",
            signal_name="EngineSpeed",
            unit="rpm",
        )
        started = time.perf_counter()
        series = HistoricalSignalQueryService(handle.root).query_signal_series(
            PlotQuery(session_id=session_id, identity=identity, sample_budget=SESSION_BUDGET)
        )
        elapsed = time.perf_counter() - started

    assert series.matched_frame_count == SESSION_FRAMES
    assert 0 < len(series.samples) <= SESSION_BUDGET
    assert series.downsampled is True
    assert series.samples[0].sequence == 0
    assert series.samples[-1].sequence == SESSION_FRAMES - 1
    times = [sample.time for sample in series.samples]
    assert times == sorted(times)
    print(
        f"\n[plot-query] session_frames={SESSION_FRAMES} budget={SESSION_BUDGET}"
        f" output={len(series.samples)} seconds={elapsed:.3f}"
    )
