"""Behaviour tests for the Runtime-side, bounded, deterministic downsampler.

The reducer is the answer to "a historical dataset may hold millions of points but
a plot may hold a thousand". These tests pin the four properties that make that
answer trustworthy: the hard bound, the preserved endpoints, the preserved
extremes, and determinism — plus the structural fact that the working set is the
budget, not the input length.

The very large runs live in ``tests/performance/test_plot_downsampling.py``; these
stay comfortably sized so the unit suite stays fast.
"""

from __future__ import annotations

import pytest
from canx.plot.downsample import StreamingDownsampler, downsample_samples
from canx.plot.errors import PlotValidationError
from canx.plot.model import PlotSample

BUDGET = 1_000
LARGE = 50_000


def series(count: int, *, value_at=None) -> tuple[PlotSample, ...]:
    """Build ``count`` samples with monotonic time and a deterministic value."""
    samples = []
    for sequence in range(count):
        value = float(sequence % 13) if value_at is None else value_at(sequence)
        samples.append(PlotSample(time=float(sequence), value=value, sequence=sequence))
    return tuple(samples)


def reduce(samples: tuple[PlotSample, ...], budget: int = BUDGET) -> tuple[PlotSample, ...]:
    return downsample_samples(samples, sample_budget=budget)


def test_a_series_below_the_budget_is_returned_unchanged() -> None:
    samples = series(37)
    assert reduce(samples) == samples


def test_exactly_the_budget_is_returned_unchanged() -> None:
    samples = series(BUDGET)
    assert reduce(samples) == samples


def test_a_single_point_is_returned_unchanged() -> None:
    samples = series(1)
    assert reduce(samples) == samples


def test_two_points_are_returned_unchanged() -> None:
    samples = series(2)
    assert reduce(samples) == samples


def test_a_series_above_the_budget_is_bounded() -> None:
    reduced = reduce(series(LARGE))
    assert 0 < len(reduced) <= BUDGET


def test_the_first_point_is_always_preserved() -> None:
    samples = series(LARGE)
    assert reduce(samples)[0] == samples[0]


def test_the_last_point_is_always_preserved() -> None:
    samples = series(LARGE)
    assert reduce(samples)[-1] == samples[-1]


def test_a_single_point_spike_is_never_strided_away() -> None:
    """A blind stride would drop the one sample that matters most."""
    spike = LARGE // 2 + 1

    def value_at(sequence: int) -> float:
        return 900.0 if sequence == spike else float(sequence % 5)

    reduced = reduce(series(LARGE, value_at=value_at))
    assert any(sample.sequence == spike and sample.value == 900.0 for sample in reduced)
    # The extremum survives even though it is a single interior point.
    assert max(sample.value for sample in reduced) == 900.0


def test_a_negative_spike_is_never_strided_away() -> None:
    def value_at(sequence: int) -> float:
        return -750.0 if sequence == LARGE // 3 else float(sequence % 4)

    reduced = reduce(series(LARGE, value_at=value_at))
    assert min(sample.value for sample in reduced) == -750.0


def test_time_ordering_is_preserved() -> None:
    reduced = reduce(series(LARGE))
    times = [sample.time for sample in reduced]
    assert times == sorted(times)
    assert len(set(times)) == len(times)


def test_the_reduction_is_deterministic_across_repeats() -> None:
    samples = series(40_000)
    assert reduce(samples) == reduce(samples)


def test_the_reduction_is_independent_of_how_the_stream_is_chunked() -> None:
    """Feeding the same points through one reducer equals feeding them one by one."""
    samples = series(30_000)
    one_shot = downsample_samples(samples, sample_budget=BUDGET)

    stepwise = StreamingDownsampler(sample_budget=BUDGET)
    for sample in samples:
        stepwise.add(sample)

    assert stepwise.finish() == one_shot


def test_the_working_set_never_exceeds_the_budget() -> None:
    """The structural proof: retained points track the budget, not the input."""
    reducer = StreamingDownsampler(sample_budget=BUDGET)
    peak = 0
    for sample in series(200_000):
        reducer.add(sample)
        peak = max(peak, reducer.retained_point_count)

    assert reducer.added_count == 200_000
    assert peak <= BUDGET
    assert reducer.retained_point_count <= BUDGET
    assert len(reducer.finish()) <= BUDGET


def test_the_output_is_a_subsequence_of_the_input() -> None:
    samples = series(LARGE)
    reduced = reduce(samples)
    positions = [sample.sequence for sample in reduced]
    assert positions == sorted(positions)
    assert len(set(positions)) == len(positions)
    by_sequence = {sample.sequence: sample for sample in samples}
    assert all(by_sequence[sample.sequence] == sample for sample in reduced)


@pytest.mark.parametrize(
    "budget", [0, 1, -5, True, 1.5], ids=["zero", "one", "neg", "bool", "float"]
)
def test_a_budget_too_small_for_a_curve_is_refused(budget: object) -> None:
    with pytest.raises(PlotValidationError):
        StreamingDownsampler(sample_budget=budget)  # type: ignore[arg-type]


def test_a_series_budget_of_two_keeps_exactly_the_endpoints() -> None:
    samples = series(500)
    reduced = reduce(samples, budget=2)
    assert reduced == (samples[0], samples[-1])
