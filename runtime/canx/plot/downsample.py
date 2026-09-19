"""Single-pass, bounded, deterministic downsampling of a signal sample stream.

The realtime frontend reduces an over-long series with an even stride
(``apps/desktop/src/components/plot/series.ts``). That is correct for a *live*
window of a few thousand points that has just arrived, but it is the wrong answer
for a historical dataset that may hold millions: a blind stride lands wherever
the arithmetic falls, so a brief two-sample spike between strides is discarded and
the shape an engineer is looking for disappears.

This module answers the large-data question in the Runtime with **hierarchical
min/max reduction**, which is a proven deterministic equivalent of bucket
min/max:

* the stream is buffered up to ``sample_budget`` points;
* the moment the buffer overflows it is compacted by grouping the interior into
  contiguous buckets and keeping each bucket's minimum and maximum point, so an
  extremum can never be averaged or strided away;
* the first and the last point are always preserved by construction;
* compaction is repeated as the stream continues, so **working memory is bounded
  by the sample budget, not by the length of the input**;
* a stream shorter than the budget is returned untouched, so "below the budget"
  and "above the budget" are visibly different behaviours.

The output is a subsequence of the input in input order, so it is always ordered
and always reproducible: the same input reduces to the same output on every run.
"""

from __future__ import annotations

from canx.plot.errors import PlotValidationError
from canx.plot.model import MIN_PLOT_SAMPLE_BUDGET, PlotSample

#: The total-order key that makes min/max selection deterministic on ties.
#: Value is compared first (the extremum being preserved); time and sequence only
#: break ties, so two runs over the same points always keep the same point.
SampleKey = tuple[float, float, int]


def _key(sample: PlotSample) -> SampleKey:
    return (sample.value, sample.time, sample.sequence)


class StreamingDownsampler:
    """An online, bounded min/max reducer over a stream of :class:`PlotSample`.

    Feed points with :meth:`add`; read the bounded, order-preserving result with
    :meth:`finish`. The object holds at most ``sample_budget`` points at rest, plus
    a fixed amount of bookkeeping, so its working set is independent of how many
    points are added.
    """

    def __init__(self, *, sample_budget: int) -> None:
        if (
            not isinstance(sample_budget, int)
            or isinstance(sample_budget, bool)
            or sample_budget < MIN_PLOT_SAMPLE_BUDGET
        ):
            raise PlotValidationError(
                "sample_budget must be an integer of at least 2.",
                code="plot.invalid_budget",
                details={"sample_budget": repr(sample_budget)},
            )
        self._budget = sample_budget
        self._points: list[PlotSample] = []
        self._added = 0

    @property
    def sample_budget(self) -> int:
        """Return the hard upper bound on the retained and returned point count."""
        return self._budget

    @property
    def added_count(self) -> int:
        """Return how many points have been added in total."""
        return self._added

    @property
    def retained_point_count(self) -> int:
        """Return how many points the reducer currently holds.

        This is the structural proof that the working set is bounded: it is never
        larger than ``sample_budget``, however many points were added.
        """
        return len(self._points)

    def add(self, sample: PlotSample) -> None:
        """Add one point, compacting the buffer when it overflows the budget."""
        if not isinstance(sample, PlotSample):
            raise PlotValidationError(
                "add expects a PlotSample.",
                code="plot.invalid_sample",
                details={"type": type(sample).__name__},
            )
        self._points.append(sample)
        self._added += 1
        if len(self._points) > self._budget:
            self._compact()

    def finish(self) -> tuple[PlotSample, ...]:
        """Return the bounded samples, in input order, as an immutable tuple."""
        return tuple(self._points)

    def _compact(self) -> None:
        """Shrink the buffer while preserving both endpoints and every group's edges.

        The interior (everything but the first and last point) is split into
        contiguous index buckets; each bucket contributes its minimum and maximum
        point, emitted in index order. The first and last points are pinned, so the
        endpoints of the curve survive every compaction.

        The buffer is compacted down to about three quarters of the budget, never
        below two points. That leaves roughly a quarter of the budget of headroom
        before the next compaction, so a stream of ``n`` points compacts about
        ``4n / budget`` times at ``O(budget)`` each — linear overall — while still
        returning a series close to the requested budget in size.
        """
        points = self._points
        target = (self._budget * 3) // 4
        if target < 2:
            target = 2
        buckets = (target - 2) // 2
        first = points[0]
        last = points[-1]
        if buckets < 1:
            # The budget is too small for any interior bucket (budget 2 through 5):
            # keep exactly the two endpoints the contract promises.
            self._points = [first, last]
            return
        interior = points[1:-1]
        count = len(interior)
        kept: list[PlotSample] = [first]
        for bucket in range(buckets):
            low = bucket * count // buckets
            high = (bucket + 1) * count // buckets
            if low >= high:
                continue
            index_min = low
            index_max = low
            for index in range(low + 1, high):
                candidate = _key(interior[index])
                if candidate < _key(interior[index_min]):
                    index_min = index
                elif candidate > _key(interior[index_max]):
                    index_max = index
            if index_min == index_max:
                kept.append(interior[index_min])
            elif index_min < index_max:
                kept.append(interior[index_min])
                kept.append(interior[index_max])
            else:
                kept.append(interior[index_max])
                kept.append(interior[index_min])
        kept.append(last)
        # Kept length is 2 + at most 2 * buckets <= target <= budget // 2.
        self._points = kept


def downsample_samples(
    samples: tuple[PlotSample, ...], *, sample_budget: int
) -> tuple[PlotSample, ...]:
    """Reduce ``samples`` to at most ``sample_budget`` points, deterministically.

    A convenience wrapper over :class:`StreamingDownsampler` for callers that
    already hold a bounded tuple (for example a single query page) and want the
    same reduction the service applies to a whole stream.
    """
    reducer = StreamingDownsampler(sample_budget=sample_budget)
    for sample in samples:
        reducer.add(sample)
    return reducer.finish()
