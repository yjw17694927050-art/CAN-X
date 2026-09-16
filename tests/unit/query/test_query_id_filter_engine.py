"""Observable selection of the CAN id range and mask axes in the engine.

These tests exercise the engine directly against real Parquet files and compute
their expectations independently in Python, so a DuckDB-specific mistake cannot
be confirmed by DuckDB itself. Nothing here asserts on generated SQL text: the
contract is which frames come back, not how the statement is spelled. The one
exception is the injection boundary, where the observable claim is behavioural —
a quote inside a caller value must remain data.

The mask cases are chosen so that the *wrong* readings fail loudly:

* ``mask 0x7F0 / value 0x12F`` separates ``(id & mask) == (value & mask)`` from
  ``(id & mask) == value`` (the second accepts nothing at all).
* ``0x130`` and ``0x11F`` sit one step outside the masked group, so an engine
  that ignores the mask, or that compares the raw id, cannot pass.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest
from canx.data.schema import frames_to_table
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.query.engine import QueryEngine
from canx.query.model import FrameFilter

SESSION_ID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
STREAM_ID = "id-filter-stream"


def frame(sequence: int, **changes: Any) -> Frame:
    values: dict[str, Any] = {
        "sequence": sequence,
        "channel_id": "can0",
        "arbitration_id": 0x100,
        "is_extended": False,
        "is_fd": False,
        "bitrate_switch": False,
        "error_state_indicator": False,
        "dlc": 2,
        "data": bytes([sequence % 256, 0x40]),
        "direction": Direction.RX,
        "hardware_timestamp": None,
        "host_timestamp": 500.0 + sequence,
        "normalized_timestamp": sequence / 8.0,
        "clock_domain": "host.monotonic",
        "timestamp_quality": TimestampQuality.HOST,
        "flags": 0,
    }
    values.update(changes)
    return Frame(**values)


def _segment(tmp_path: Path, frames: list[Frame]) -> Path:
    path = tmp_path / "000000.parquet"
    pq.write_table(
        frames_to_table(
            frames, session_id=SESSION_ID, stream_id=STREAM_ID, segment_index=0
        ),
        path,
    )
    return path


def _ids(frames: Iterable[Frame], **bounds: Any) -> list[int]:
    """Independently apply the documented id predicates and return the ids.

    The predicate is written out here rather than delegated to the engine, so
    this function is an independent statement of the semantics.
    """
    selected: list[int] = []
    for item in frames:
        if (
            "exact" in bounds
            and item.arbitration_id not in bounds["exact"]
        ):
            continue
        if "start" in bounds and item.arbitration_id < bounds["start"]:
            continue
        if "end" in bounds and item.arbitration_id > bounds["end"]:
            continue
        if "mask" in bounds:
            mask = bounds["mask"]
            if (item.arbitration_id & mask) != (bounds["mask_value"] & mask):
                continue
        selected.append(item.arbitration_id)
    return selected


def _query(path: Path, **filter_changes: Any) -> list[Frame]:
    page = QueryEngine().query_frames(
        paths=(path,),
        frame_filter=FrameFilter(session_id=SESSION_ID, **filter_changes),
        after_sequence=None,
        limit=1_000,
    )
    return list(page.frames)


#: The exact fixture from the V0.3-01 acceptance description: six ids straddling
#: the requested window, one step below its start and one step above its end.
RANGE_IDS = (0x0FF, 0x100, 0x101, 0x17F, 0x1FF, 0x200)
RANGE_WINDOW = (0x100, 0x1FF)
EXPECTED_IN_WINDOW = (0x100, 0x101, 0x17F, 0x1FF)


def test_an_id_range_is_inclusive_at_both_ends(tmp_path: Path) -> None:
    frames = [frame(index, arbitration_id=value) for index, value in enumerate(RANGE_IDS)]
    path = _segment(tmp_path, frames)

    returned = _query(path, arbitration_id_start=RANGE_WINDOW[0], arbitration_id_end=RANGE_WINDOW[1])

    assert [item.arbitration_id for item in returned] == list(EXPECTED_IN_WINDOW)
    assert [item.sequence for item in returned] == [1, 2, 3, 4]
    assert 0x0FF not in [item.arbitration_id for item in returned]
    assert 0x200 not in [item.arbitration_id for item in returned]


def test_the_id_range_expectation_matches_an_independent_selection(tmp_path: Path) -> None:
    frames = [frame(index, arbitration_id=value) for index, value in enumerate(RANGE_IDS)]
    path = _segment(tmp_path, frames)

    returned = _query(path, arbitration_id_start=RANGE_WINDOW[0], arbitration_id_end=RANGE_WINDOW[1])

    expected = _ids(frames, start=RANGE_WINDOW[0], end=RANGE_WINDOW[1])
    assert expected == list(EXPECTED_IN_WINDOW)
    assert [item.arbitration_id for item in returned] == expected


@pytest.mark.parametrize(
    ("start", "end", "expected_ids"),
    [
        (None, None, list(RANGE_IDS)),
        (0x100, None, [0x100, 0x101, 0x17F, 0x1FF, 0x200]),
        (None, 0x1FF, [0x0FF, 0x100, 0x101, 0x17F, 0x1FF]),
        (0x100, 0x100, [0x100]),
        (0x17F, 0x17F, [0x17F]),
        (0x201, None, []),
        (None, 0x0FE, []),
        (0x0FF, 0x0FF, [0x0FF]),
    ],
    ids=[
        "open-both-ends",
        "open-end",
        "open-start",
        "exact-boundary-lower",
        "exact-boundary-inner",
        "entirely-above",
        "entirely-below",
        "single-lowest-id",
    ],
)
def test_every_range_shape_selects_the_expected_ids(
    tmp_path: Path, start: int | None, end: int | None, expected_ids: list[int]
) -> None:
    frames = [frame(index, arbitration_id=value) for index, value in enumerate(RANGE_IDS)]
    path = _segment(tmp_path, frames)

    returned = _query(path, arbitration_id_start=start, arbitration_id_end=end)

    assert [item.arbitration_id for item in returned] == expected_ids
    assert [item.arbitration_id for item in returned] == _ids(
        frames, **({} if start is None else {"start": start}), **({} if end is None else {"end": end})
    )


#: The exact fixture from the V0.3-01 acceptance description for masks.
MASK_IDS = (0x11F, 0x120, 0x121, 0x12A, 0x12F, 0x130)
MASK = 0x7F0
MASK_VALUE = 0x120
EXPECTED_MASKED = (0x120, 0x121, 0x12A, 0x12F)


def test_a_mask_selects_the_masked_group_and_nothing_around_it(tmp_path: Path) -> None:
    frames = [frame(index, arbitration_id=value) for index, value in enumerate(MASK_IDS)]
    path = _segment(tmp_path, frames)

    returned = _query(
        path, arbitration_id_mask=MASK, arbitration_id_mask_value=MASK_VALUE
    )

    assert [item.arbitration_id for item in returned] == list(EXPECTED_MASKED)
    assert [item.sequence for item in returned] == [1, 2, 3, 4]
    assert [item.arbitration_id for item in returned] == _ids(
        frames, mask=MASK, mask_value=MASK_VALUE
    )


def test_a_mask_value_is_reduced_through_the_mask_before_comparison(
    tmp_path: Path,
) -> None:
    """``(id & mask) == (value & mask)``, never ``(id & mask) == value``.

    With mask ``0x7F0`` and value ``0x12F`` the two readings disagree: the raw
    comparison can never be true (any id masked by ``0x7F0`` has a zero low
    nibble, while ``0x12F`` does not), so only the reduced reading accepts
    ``0x120``…``0x12F``. This test therefore fails loudly if the reduction moves
    out of the domain model.
    """
    frames = [frame(index, arbitration_id=value) for index, value in enumerate(MASK_IDS)]
    path = _segment(tmp_path, frames)

    returned = _query(path, arbitration_id_mask=MASK, arbitration_id_mask_value=0x12F)

    assert [item.arbitration_id for item in returned] == [0x120, 0x121, 0x12A, 0x12F]


def test_a_zero_mask_matches_every_frame(tmp_path: Path) -> None:
    """``(id & 0) == (0 & 0)`` holds for every id, so a zero mask filters nothing."""
    frames = [frame(index, arbitration_id=value) for index, value in enumerate(MASK_IDS)]
    path = _segment(tmp_path, frames)

    returned = _query(path, arbitration_id_mask=0, arbitration_id_mask_value=0)

    assert [item.arbitration_id for item in returned] == list(MASK_IDS)


def test_a_full_mask_behaves_like_an_exact_id(tmp_path: Path) -> None:
    frames = [frame(index, arbitration_id=value) for index, value in enumerate(MASK_IDS)]
    path = _segment(tmp_path, frames)

    masked = _query(path, arbitration_id_mask=0x7FF, arbitration_id_mask_value=0x121)
    exact = _query(path, arbitration_ids=(0x121,))

    assert [item.sequence for item in masked] == [item.sequence for item in exact] == [2]


def test_a_mask_that_matches_nothing_returns_an_empty_page(tmp_path: Path) -> None:
    frames = [frame(index, arbitration_id=value) for index, value in enumerate(MASK_IDS)]
    path = _segment(tmp_path, frames)

    returned = _query(path, arbitration_id_mask=0x7F0, arbitration_id_mask_value=0x700)

    assert returned == []


def test_an_id_set_and_an_id_range_are_combined_with_and(tmp_path: Path) -> None:
    """An id set is OR-ed inside itself, then AND-ed with the range."""
    frames = [frame(index, arbitration_id=value) for index, value in enumerate(RANGE_IDS)]
    path = _segment(tmp_path, frames)

    returned = _query(
        path,
        arbitration_ids=(0x0FF, 0x101, 0x200),
        arbitration_id_start=0x100,
        arbitration_id_end=0x1FF,
    )

    # 0x0FF and 0x200 are in the set but outside the window; only 0x101 survives.
    assert [item.arbitration_id for item in returned] == [0x101]


def test_a_range_and_a_mask_are_combined_with_and(tmp_path: Path) -> None:
    frames = [
        frame(index, arbitration_id=value)
        for index, value in enumerate((0x120, 0x121, 0x12F, 0x220, 0x320))
    ]
    path = _segment(tmp_path, frames)

    returned = _query(
        path,
        arbitration_id_start=0x100,
        arbitration_id_end=0x1FF,
        arbitration_id_mask=0x7F0,
        arbitration_id_mask_value=0x120,
    )

    assert [item.arbitration_id for item in returned] == [0x120, 0x121, 0x12F]


def test_every_id_axis_is_and_ed_with_the_other_filter_axes(tmp_path: Path) -> None:
    """A combined filter must not let one axis be silently dropped.

    Each unwanted frame violates exactly one axis, so a predicate that stops
    being applied (or that starts being OR-ed) shows up as an extra sequence.
    """
    frames = [
        frame(0, arbitration_id=0x120, channel_id="can1", is_fd=True, dlc=2),
        frame(1, arbitration_id=0x120, channel_id="can1", is_fd=True, dlc=2),
        # Outside the id set and outside the range.
        frame(2, arbitration_id=0x220, channel_id="can1", is_fd=True, dlc=2),
        # Wrong channel.
        frame(3, arbitration_id=0x120, channel_id="can0", is_fd=True, dlc=2),
        # Wrong direction.
        frame(
            4,
            arbitration_id=0x120,
            channel_id="can1",
            direction=Direction.TX,
            is_fd=True,
            dlc=2,
        ),
        # Classic CAN, so it fails the FD axis only.
        frame(5, arbitration_id=0x120, channel_id="can1", is_fd=False, dlc=2),
        # In the set and in the range, but outside the masked group.
        frame(6, arbitration_id=0x1FF, channel_id="can1", is_fd=True, dlc=2),
        frame(7, arbitration_id=0x120, channel_id="can1", is_fd=True, dlc=2),
    ]
    path = _segment(tmp_path, frames)

    returned = _query(
        path,
        arbitration_ids=(0x120, 0x121, 0x1FF),
        arbitration_id_start=0x100,
        arbitration_id_end=0x1FF,
        arbitration_id_mask=0x7F0,
        arbitration_id_mask_value=0x120,
        channel_ids=("can1",),
        directions=(Direction.RX,),
        is_fd=True,
    )

    assert [item.sequence for item in returned] == [0, 1, 7]


def test_an_id_set_alone_is_still_an_or_within_itself(tmp_path: Path) -> None:
    frames = [frame(index, arbitration_id=value) for index, value in enumerate(RANGE_IDS)]
    path = _segment(tmp_path, frames)

    returned = _query(path, arbitration_ids=(0x0FF, 0x200))

    assert [item.arbitration_id for item in returned] == [0x0FF, 0x200]


def test_an_id_range_compares_numbers_not_frame_types(tmp_path: Path) -> None:
    """``is_extended`` and the numeric id are independent fields.

    An extended frame may legitimately carry a low id, so a low window must return
    it; conversely a high id is only reachable by an extended frame because the
    frame model requires it, not because the range axis inferred a type from the
    number. ``is_extended`` therefore has to be applied as its own predicate.
    """
    frames = [
        frame(0, arbitration_id=0x123, is_extended=True),
        frame(1, arbitration_id=0x400, is_extended=False),
        frame(2, arbitration_id=0x10000000, is_extended=True),
        frame(3, arbitration_id=0x1000FFFF, is_extended=True),
        frame(4, arbitration_id=0x10010000, is_extended=True),
    ]
    path = _segment(tmp_path, frames)

    low = _query(path, arbitration_id_start=0x100, arbitration_id_end=0x7FF)
    low_extended = _query(
        path,
        arbitration_id_start=0x100,
        arbitration_id_end=0x7FF,
        is_extended=True,
    )
    high = _query(
        path, arbitration_id_start=0x10000000, arbitration_id_end=0x1000FFFF
    )

    assert [item.sequence for item in low] == [0, 1]
    assert [item.sequence for item in low_extended] == [0]
    assert [item.sequence for item in high] == [2, 3]


def test_a_caller_value_is_bound_not_interpolated_into_sql(tmp_path: Path) -> None:
    """A quote inside a channel name must stay data.

    The channel axis is the one id-adjacent axis that carries caller *text*, so
    it is the realistic injection surface. If a value were concatenated into the
    statement this query would either fail or match rows it must not match.
    """
    frames = [frame(index) for index in range(3)]
    path = _segment(tmp_path, frames)

    injected = _query(path, channel_ids=("can0' OR 1=1 --",))
    literal = _query(path, channel_ids=("can0",))

    assert injected == []
    assert len(literal) == 3


def test_a_quoted_channel_name_never_matches_a_different_channel(tmp_path: Path) -> None:
    frames = [
        frame(0, channel_id="can0"),
        frame(1, channel_id="can1"),
        frame(2, channel_id="can0' --"),
    ]
    path = _segment(tmp_path, frames)

    returned = _query(path, channel_ids=("can0' --",))

    assert [item.sequence for item in returned] == [2]


def test_the_summary_respects_the_id_axes(tmp_path: Path) -> None:
    frames = [frame(index, arbitration_id=value) for index, value in enumerate(MASK_IDS)]
    path = _segment(tmp_path, frames)

    summary = QueryEngine().summarize(
        paths=(path,),
        frame_filter=FrameFilter(
            session_id=SESSION_ID,
            arbitration_id_start=0x120,
            arbitration_id_end=0x12F,
            arbitration_id_mask=MASK,
            arbitration_id_mask_value=MASK_VALUE,
        ),
    )

    assert summary.matching_frame_count == 4
    assert summary.first_sequence == 1
    assert summary.last_sequence == 4


def test_the_summary_of_an_id_filtered_empty_result_reports_no_bounds(
    tmp_path: Path,
) -> None:
    frames = [frame(index, arbitration_id=value) for index, value in enumerate(RANGE_IDS)]
    path = _segment(tmp_path, frames)

    summary = QueryEngine().summarize(
        paths=(path,),
        frame_filter=FrameFilter(session_id=SESSION_ID, arbitration_id_start=0x400),
    )

    assert summary.matching_frame_count == 0
    assert summary.first_sequence is None
    assert summary.last_sequence is None


def test_the_arbitration_histogram_respects_the_id_axes(tmp_path: Path) -> None:
    frames = [frame(index, arbitration_id=value) for index, value in enumerate(MASK_IDS)]
    path = _segment(tmp_path, frames)

    counts = QueryEngine().count_by_arbitration_id(
        paths=(path,),
        frame_filter=FrameFilter(
            session_id=SESSION_ID,
            arbitration_id_mask=MASK,
            arbitration_id_mask_value=MASK_VALUE,
        ),
        top_n=100,
    )

    assert [(row.arbitration_id, row.frame_count) for row in counts] == [
        (0x120, 1),
        (0x121, 1),
        (0x12A, 1),
        (0x12F, 1),
    ]


def test_an_id_filter_pages_with_the_sequence_cursor(tmp_path: Path) -> None:
    """The cursor is a sequence, so a filtered page set must still be lossless."""
    frames = [
        frame(sequence, arbitration_id=0x120 if sequence % 2 == 0 else 0x220)
        for sequence in range(10)
    ]
    path = _segment(tmp_path, frames)
    frame_filter = FrameFilter(
        session_id=SESSION_ID,
        arbitration_id_start=0x100,
        arbitration_id_end=0x1FF,
    )
    engine = QueryEngine()

    collected: list[int] = []
    cursor: int | None = None
    while True:
        page = engine.query_frames(
            paths=(path,),
            frame_filter=frame_filter,
            after_sequence=cursor,
            limit=2,
        )
        collected.extend(item.sequence for item in page.frames)
        if not page.has_more:
            break
        cursor = page.next_after_sequence

    # Only the even sequences carry 0x120; the cursor must walk past the skipped
    # odd sequences without skipping a match.
    assert collected == [0, 2, 4, 6, 8]
