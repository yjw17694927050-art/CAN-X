"""CAN id range and mask filtering over persisted, multi-segment data.

This is the acceptance path for the V0.3-01 id axes: one data session spans many
Parquet segments, and an id range / mask filter must select exactly the frames
the documented predicate describes, no matter which segment a frame lives in —
and must never prune a segment on metadata that does not exist.

Expected results are computed independently in Python from the source frames, so
a DuckDB-specific mistake cannot be confirmed by DuckDB itself.

Two fixtures are used on purpose:

* a 60-frame / 12-segment session whose ids cycle a spread that straddles the
  windows, which is what makes a *filtered* page walk non-contiguous and
  therefore able to catch an offset-style or count-based cursor;
* a small, hand-built session in which every axis of a full combination filter
  has at least one frame that violates **only** that axis, so "some predicate was
  silently dropped" is mechanically detectable.

Filter bounds in this module always use the canonical :class:`FrameFilter` field
names, so the same mapping drives both the request and the independent selection.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest
from canx.data.session import DataSessionService
from canx.domain.batch import FrameBatch
from canx.domain.frame import Direction, Frame, TimestampQuality
from canx.project.service import ProjectHandle, ProjectService
from canx.query.model import FrameFilter, FrameQuery
from canx.query.service import QueryService

STREAM_ID = "id-filter-session"
FRAMES_PER_SEGMENT = 5
TOTAL_FRAMES = 60
SEGMENT_COUNT = TOTAL_FRAMES // FRAMES_PER_SEGMENT

#: Ids that straddle ``0x100..0x1FF`` and the ``0x7F0 / 0x120`` masked group, so
#: a filtered result is never a contiguous run of sequences.
ID_PATTERN: tuple[int, ...] = (
    0x0FF,
    0x100,
    0x101,
    0x17F,
    0x1FF,
    0x200,
    0x120,
    0x12F,
    0x130,
    0x220,
)

MASK = 0x7F0
MASK_VALUE = 0x120
WINDOW = (0x100, 0x1FF)

#: Sequences inside ``0x100..0x1FF``, computed from :data:`ID_PATTERN` by hand so
#: the expectation does not depend on the selection helper it is checking. The
#: in-window pattern indices are 1, 2, 3, 4, 6, 7 and 8 — ``0x130`` is inside
#: ``0x1FF``, while ``0x0FF`` is below the window and ``0x200``/``0x220`` above it.
IN_WINDOW_SEQUENCES = [
    1, 2, 3, 4, 6, 7, 8,
    11, 12, 13, 14, 16, 17, 18,
    21, 22, 23, 24, 26, 27, 28,
    31, 32, 33, 34, 36, 37, 38,
    41, 42, 43, 44, 46, 47, 48,
    51, 52, 53, 54, 56, 57, 58,
]

#: Sequences whose id is ``0x120`` or ``0x12F``, i.e. inside the masked group
#: (``0x130`` is in the range but not in the group).
MASKED_SEQUENCES = [6, 7, 16, 17, 26, 27, 36, 37, 46, 47, 56, 57]


def frame(sequence: int) -> Frame:
    """One deterministic source frame; the id cycles :data:`ID_PATTERN`."""
    is_fd = sequence % 4 == 0
    return Frame(
        sequence=sequence,
        channel_id=f"can{sequence % 3}",
        arbitration_id=ID_PATTERN[sequence % len(ID_PATTERN)],
        is_extended=sequence % 11 == 0,
        is_fd=is_fd,
        bitrate_switch=is_fd and sequence % 8 == 0,
        error_state_indicator=is_fd and sequence % 24 == 0,
        dlc=2,
        data=bytes([sequence % 256, 0x5A]),
        direction=Direction.RX if sequence % 2 == 0 else Direction.TX,
        hardware_timestamp=None,
        host_timestamp=1_000.0 + sequence,
        normalized_timestamp=sequence / 100.0,
        clock_domain="host.monotonic",
        timestamp_quality=TimestampQuality.HOST,
        flags=0,
    )


SOURCE_FRAMES: tuple[Frame, ...] = tuple(frame(sequence) for sequence in range(TOTAL_FRAMES))


def _contiguous_batches(frames: tuple[Frame, ...], size: int) -> list[tuple[Frame, ...]]:
    """Split frames into batches that are contiguous *and* at most ``size`` long.

    A ``FrameBatch`` requires contiguous sequences inside one batch, and the
    session writer now requires each following batch to continue the previous
    sequence exactly. The fixtures feed a contiguous sequence space, so a batch
    only has to end when it reaches ``size``.
    """
    batches: list[tuple[Frame, ...]] = []
    current: list[Frame] = []
    for item in frames:
        if current and (len(current) == size or item.sequence != current[-1].sequence + 1):
            batches.append(tuple(current))
            current = []
        current.append(item)
    if current:
        batches.append(tuple(current))
    return batches


def _write_session(handle: ProjectHandle, frames: Iterable[Frame]) -> str:
    materialized = tuple(frames)
    service = DataSessionService(handle.root, max_frames_per_segment=FRAMES_PER_SEGMENT)
    writer = service.start(stream_id=STREAM_ID)
    for batch in _contiguous_batches(materialized, FRAMES_PER_SEGMENT):
        writer.append(FrameBatch.create(stream_id=STREAM_ID, frames=list(batch)))
    writer.finalize()
    return writer.session_id


def _selected(frames: Iterable[Frame], **bounds: object) -> list[int]:
    """Independently apply the documented predicates and return the sequences.

    The predicate is written out here in full — including the masked equality as
    ``(id & mask) == (value & mask)`` — so this is a second, independent statement
    of the semantics rather than a call back into the code under test.
    """
    selected: list[int] = []
    for item in frames:
        if "sequence_start" in bounds and item.sequence < bounds["sequence_start"]:  # type: ignore[operator]
            continue
        if "sequence_end" in bounds and item.sequence > bounds["sequence_end"]:  # type: ignore[operator]
            continue
        if (
            "normalized_timestamp_start" in bounds
            and item.normalized_timestamp < bounds["normalized_timestamp_start"]  # type: ignore[operator]
        ):
            continue
        if (
            "normalized_timestamp_end" in bounds
            and item.normalized_timestamp > bounds["normalized_timestamp_end"]  # type: ignore[operator]
        ):
            continue
        if "channel_ids" in bounds and item.channel_id not in bounds["channel_ids"]:  # type: ignore[operator]
            continue
        if (
            "arbitration_ids" in bounds
            and item.arbitration_id not in bounds["arbitration_ids"]  # type: ignore[operator]
        ):
            continue
        if (
            "arbitration_id_start" in bounds
            and item.arbitration_id < bounds["arbitration_id_start"]  # type: ignore[operator]
        ):
            continue
        if (
            "arbitration_id_end" in bounds
            and item.arbitration_id > bounds["arbitration_id_end"]  # type: ignore[operator]
        ):
            continue
        if "arbitration_id_mask" in bounds:
            mask = bounds["arbitration_id_mask"]
            if (item.arbitration_id & mask) != (  # type: ignore[operator]
                bounds["arbitration_id_mask_value"] & mask  # type: ignore[operator]
            ):
                continue
        if "directions" in bounds and item.direction not in bounds["directions"]:  # type: ignore[operator]
            continue
        if "is_extended" in bounds and item.is_extended is not bounds["is_extended"]:
            continue
        if "is_fd" in bounds and item.is_fd is not bounds["is_fd"]:
            continue
        selected.append(item.sequence)
    return selected


def _filter(session_id: str, **bounds: object) -> FrameFilter:
    return FrameFilter(session_id=session_id, **bounds)  # type: ignore[arg-type]


def _page(service: QueryService, session_id: str, *, limit: int = 1_000, **bounds: object):
    return service.query_frames(
        FrameQuery(filter=_filter(session_id, **bounds), limit=limit)
    )


def _query(
    service: QueryService, session_id: str, *, limit: int = 1_000, **bounds: object
) -> list[int]:
    return [item.sequence for item in _page(service, session_id, limit=limit, **bounds).frames]


def _walk(service: QueryService, session_id: str, *, limit: int, **bounds: object) -> list[int]:
    """Collect every filtered page by following the exclusive sequence cursor."""
    collected: list[int] = []
    cursor = None
    while True:
        page = service.query_frames(
            FrameQuery(filter=_filter(session_id, **bounds), after_sequence=cursor, limit=limit)
        )
        collected.extend(item.sequence for item in page.frames)
        if not page.has_more:
            return collected
        cursor = page.next_after_sequence


def test_a_multi_segment_session_is_written_as_the_fixture_describes(tmp_path: Path) -> None:
    """Guard the fixture itself: 12 segments of 5 frames, integrity clean."""
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _write_session(handle, SOURCE_FRAMES)
        stored = DataSessionService(handle.root)

        session = stored.get_session(session_id)
        segments = stored.list_segments(session_id)

        assert session.frame_count == TOTAL_FRAMES
        assert session.segment_count == SEGMENT_COUNT == len(segments)
        assert sum(segment.frame_count for segment in segments) == TOTAL_FRAMES
        assert stored.inspect_integrity().clean is True


def test_an_id_range_over_a_multi_segment_session_is_exact(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _write_session(handle, SOURCE_FRAMES)
        service = QueryService(handle.root)

        returned = _query(
            service,
            session_id,
            arbitration_id_start=WINDOW[0],
            arbitration_id_end=WINDOW[1],
        )

    expected = _selected(
        SOURCE_FRAMES, arbitration_id_start=WINDOW[0], arbitration_id_end=WINDOW[1]
    )
    assert returned == expected
    assert returned == IN_WINDOW_SEQUENCES
    # A non-contiguous result is the whole point of this fixture.
    assert returned != list(range(returned[0], returned[-1] + 1))


@pytest.mark.parametrize(
    ("arbitration_id_start", "arbitration_id_end"),
    [
        (0x100, 0x100),
        (0x17F, 0x17F),
        (0x1FF, 0x1FF),
        (0x12F, 0x12F),
        (0x200, 0x200),
    ],
    ids=[
        "edges-at-0x100",
        "single-0x17F",
        "upper-edge-0x1FF",
        "masked-0x12F",
        "above-window-0x200",
    ],
)
def test_a_single_id_window_returns_every_occurrence_of_that_id(
    tmp_path: Path, arbitration_id_start: int, arbitration_id_end: int
) -> None:
    """An inclusive one-id window must catch the id in every segment it appears in."""
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _write_session(handle, SOURCE_FRAMES)
        service = QueryService(handle.root)

        returned = _query(
            service,
            session_id,
            arbitration_id_start=arbitration_id_start,
            arbitration_id_end=arbitration_id_end,
        )

    occurrences = [
        item.sequence for item in SOURCE_FRAMES if item.arbitration_id == arbitration_id_start
    ]
    assert returned == occurrences
    # Every id in the pattern occurs once per 10-frame cycle, across segments.
    assert len(occurrences) == TOTAL_FRAMES // len(ID_PATTERN)


def test_a_mask_over_a_multi_segment_session_is_exact(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _write_session(handle, SOURCE_FRAMES)
        service = QueryService(handle.root)

        returned = _query(
            service, session_id, arbitration_id_mask=MASK, arbitration_id_mask_value=MASK_VALUE
        )

    expected = _selected(
        SOURCE_FRAMES, arbitration_id_mask=MASK, arbitration_id_mask_value=MASK_VALUE
    )
    assert returned == expected
    assert returned == MASKED_SEQUENCES
    # Only 0x120 and 0x12F live in the masked group.
    assert {SOURCE_FRAMES[sequence].arbitration_id for sequence in returned} == {0x120, 0x12F}


def test_a_range_and_a_mask_together_are_and_ed_on_persisted_data(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _write_session(handle, SOURCE_FRAMES)
        service = QueryService(handle.root)

        returned = _query(
            service,
            session_id,
            arbitration_id_start=WINDOW[0],
            arbitration_id_end=WINDOW[1],
            arbitration_id_mask=MASK,
            arbitration_id_mask_value=MASK_VALUE,
        )

    assert returned == _selected(
        SOURCE_FRAMES,
        arbitration_id_start=WINDOW[0],
        arbitration_id_end=WINDOW[1],
        arbitration_id_mask=MASK,
        arbitration_id_mask_value=MASK_VALUE,
    )
    # The range contributes nothing here (the masked group already sits inside
    # it); the mask is what removes the 0x100/0x101/0x17F/0x1FF frames.
    assert returned == MASKED_SEQUENCES


@pytest.mark.parametrize(
    "bounds",
    [
        {"arbitration_id_start": 0x100, "arbitration_id_end": 0x1FF},
        {"arbitration_id_mask": MASK, "arbitration_id_mask_value": MASK_VALUE},
        {"arbitration_id_mask": 0x7FF, "arbitration_id_mask_value": 0x101},
        {"arbitration_ids": (0x100, 0x200)},
        {"arbitration_ids": (0x0FF, 0x120), "arbitration_id_start": 0x100},
        {
            "arbitration_id_start": 0x100,
            "arbitration_id_end": 0x12A,
            "arbitration_id_mask": MASK,
            "arbitration_id_mask_value": MASK_VALUE,
        },
        {"arbitration_id_start": 0x100, "channel_ids": ("can1",)},
        {
            "arbitration_id_mask": MASK,
            "arbitration_id_mask_value": MASK_VALUE,
            "channel_ids": ("can2",),
        },
        {"arbitration_id_start": 0x100, "arbitration_id_end": 0x1FF, "directions": (Direction.TX,)},
        {"arbitration_id_start": 0x100, "arbitration_id_end": 0x1FF, "is_fd": True},
        {"arbitration_id_start": 0x100, "arbitration_id_end": 0x1FF, "is_extended": True},
        {
            "arbitration_id_mask": MASK,
            "arbitration_id_mask_value": MASK_VALUE,
            "is_extended": True,
        },
        {
            "arbitration_id_start": 0x100,
            "arbitration_id_end": 0x1FF,
            "sequence_start": 20,
            "sequence_end": 40,
        },
        {
            "arbitration_id_mask": MASK,
            "arbitration_id_mask_value": MASK_VALUE,
            "normalized_timestamp_start": 0.20,
            "normalized_timestamp_end": 0.40,
        },
        {
            "arbitration_id_start": 0x100,
            "arbitration_id_end": 0x1FF,
            "sequence_start": 10,
            "sequence_end": 50,
            "normalized_timestamp_start": 0.10,
            "normalized_timestamp_end": 0.50,
            "channel_ids": ("can0", "can1"),
            "directions": (Direction.RX,),
            "is_fd": False,
        },
        {"arbitration_id_start": 0x1FFFFFF0, "arbitration_id_end": 0x1FFFFFFF},
        {"arbitration_id_mask": 0x1F000000, "arbitration_id_mask_value": 0x10000000},
    ],
    ids=[
        "range-only",
        "mask-only",
        "full-mask",
        "id-set",
        "set-and-range",
        "range-and-mask",
        "range-and-channel",
        "mask-and-channel",
        "range-and-direction",
        "range-and-fd",
        "range-and-extended",
        "mask-and-extended",
        "range-and-sequence",
        "mask-and-timestamp",
        "everything-except-ids",
        "range-above-the-data",
        "extended-mask-above-the-data",
    ],
)
def test_every_combination_matches_the_independent_selection(
    tmp_path: Path, bounds: dict[str, object]
) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _write_session(handle, SOURCE_FRAMES)
        service = QueryService(handle.root)

        returned = _query(service, session_id, **bounds)

    assert returned == _selected(SOURCE_FRAMES, **bounds)


def test_the_planner_never_prunes_a_segment_on_an_id_filter(tmp_path: Path) -> None:
    """A segment carries no id metadata, so an id filter cannot prune anything.

    Inventing segment-level id bounds would be a silent false negative: a segment
    whose frames span several ids cannot be skipped on a single recorded range.
    """
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _write_session(handle, SOURCE_FRAMES)
        service = QueryService(handle.root)

        by_range = service.plan_frames(
            _filter(session_id, arbitration_id_start=WINDOW[0], arbitration_id_end=WINDOW[1])
        )
        by_mask = service.plan_frames(
            _filter(session_id, arbitration_id_mask=MASK, arbitration_id_mask_value=MASK_VALUE)
        )

    for plan in (by_range, by_mask):
        assert plan.registered_segment_count == SEGMENT_COUNT
        assert plan.candidate_segment_count == SEGMENT_COUNT
        assert plan.pruned_segment_count == 0


def test_a_filtered_page_walk_from_a_sequence_cursor_is_lossless(tmp_path: Path) -> None:
    """The cursor is the last returned sequence, not the number of matching rows.

    The result set here is non-contiguous (every id filter skips sequences), so a
    cursor that advanced by count, or an engine that paginated before filtering,
    would lose or duplicate frames.
    """
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _write_session(handle, SOURCE_FRAMES)
        service = QueryService(handle.root)

        single = _query(
            service,
            session_id,
            arbitration_id_start=WINDOW[0],
            arbitration_id_end=WINDOW[1],
        )
        walked = _walk(
            service,
            session_id,
            limit=5,
            arbitration_id_start=WINDOW[0],
            arbitration_id_end=WINDOW[1],
        )
        masked_walk = _walk(
            service,
            session_id,
            limit=3,
            arbitration_id_mask=MASK,
            arbitration_id_mask_value=MASK_VALUE,
        )

    assert walked == single == IN_WINDOW_SEQUENCES
    assert len(walked) == len(set(walked)) == len(IN_WINDOW_SEQUENCES)
    assert masked_walk == MASKED_SEQUENCES


def test_a_filtered_walk_advances_the_cursor_strictly(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _write_session(handle, SOURCE_FRAMES)
        service = QueryService(handle.root)
        frame_filter = _filter(
            session_id, arbitration_id_start=WINDOW[0], arbitration_id_end=WINDOW[1]
        )

        pages = []
        cursor = None
        while True:
            page = service.query_frames(
                FrameQuery(filter=frame_filter, after_sequence=cursor, limit=5)
            )
            pages.append(page)
            if not page.has_more:
                break
            cursor = page.next_after_sequence

    cursors = [item.next_after_sequence for item in pages[:-1]]
    assert cursors == sorted(cursors)
    assert len(set(cursors)) == len(cursors)
    for page, page_cursor in zip(pages[:-1], cursors, strict=True):
        assert page_cursor == page.frames[-1].sequence
    assert pages[-1].has_more is False
    assert pages[-1].next_after_sequence is None


def test_an_id_filter_that_matches_nothing_returns_an_empty_page(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _write_session(handle, SOURCE_FRAMES)
        service = QueryService(handle.root)

        page = _page(service, session_id, arbitration_id_start=0x800, arbitration_id_end=0x8FF)
        summary = service.summarize_frames(
            _filter(session_id, arbitration_id_start=0x800, arbitration_id_end=0x8FF)
        )

    assert page.frames == ()
    assert page.has_more is False
    assert page.next_after_sequence is None
    assert summary.matching_frame_count == 0
    assert summary.first_sequence is None
    assert summary.last_sequence is None
    assert summary.first_normalized_timestamp is None
    assert summary.last_normalized_timestamp is None


def test_a_mask_that_matches_nothing_returns_an_empty_page(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _write_session(handle, SOURCE_FRAMES)
        service = QueryService(handle.root)

        page = _page(
            service, session_id, arbitration_id_mask=MASK, arbitration_id_mask_value=0x700
        )
        summary = service.summarize_frames(
            _filter(session_id, arbitration_id_mask=MASK, arbitration_id_mask_value=0x700)
        )

    assert page.frames == ()
    assert page.has_more is False
    assert page.next_after_sequence is None
    assert summary.matching_frame_count == 0


def test_the_summary_of_an_id_filtered_session_reports_its_bounds(tmp_path: Path) -> None:
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _write_session(handle, SOURCE_FRAMES)
        service = QueryService(handle.root)

        summary = service.summarize_frames(
            _filter(
                session_id,
                arbitration_id_mask=MASK,
                arbitration_id_mask_value=MASK_VALUE,
            )
        )

    assert summary.matching_frame_count == 12
    assert summary.first_sequence == 6
    assert summary.last_sequence == 57
    assert summary.first_normalized_timestamp == pytest.approx(0.06)
    assert summary.last_normalized_timestamp == pytest.approx(0.57)


# --- A combination filter in which every axis has its own witness frame ---------

#: ``(sequence, arbitration_id, channel, direction, is_fd)``. Each entry after the
#: first two violates exactly one axis of :data:`COMBINED`, so the frame that
#: appears when an axis is relaxed proves that the axis was being applied.
COMBINATION_SOURCE: tuple[tuple[int, int, str, Direction, bool], ...] = (
    (0, 0x120, "can1", Direction.RX, True),
    (1, 0x12A, "can1", Direction.RX, True),
    (2, 0x12F, "can1", Direction.RX, True),  # only outside the range
    (3, 0x110, "can1", Direction.RX, True),  # only outside the masked group
    (4, 0x121, "can0", Direction.RX, True),  # only the wrong channel
    (5, 0x121, "can1", Direction.TX, True),  # only the wrong direction
    (6, 0x121, "can1", Direction.RX, False),  # only a classic frame
    (7, 0x121, "can1", Direction.RX, True),  # only outside the sequence window
)

#: The sequence space is contiguous on purpose: the session writer refuses a
#: batch that does not continue the previous sequence exactly, so the witnesses
#: can no longer be spaced apart. They are consecutive instead, and the sequence
#: window ends just before the last one.
#:
#: The range ends *inside* the masked group on purpose. With a range that fully
#: contains it (``0x100..0x1FF``) the mask would already imply the range, and the
#: range axis could not be shown to still be applied.
COMBINED: dict[str, object] = {
    "sequence_start": 0,
    "sequence_end": 6,
    "channel_ids": ("can1",),
    "directions": (Direction.RX,),
    "arbitration_id_start": 0x100,
    "arbitration_id_end": 0x12A,
    "arbitration_id_mask": MASK,
    "arbitration_id_mask_value": MASK_VALUE,
    "is_fd": True,
}

#: An axis whose relaxation must add exactly this frame.
WITNESSES: dict[str, int] = {
    "arbitration_id_range": 2,
    "arbitration_id_mask": 3,
    "channel_ids": 4,
    "directions": 5,
    "is_fd": 6,
    "sequence_end": 7,
}


def _combination_frames() -> tuple[Frame, ...]:
    return tuple(
        Frame(
            sequence=sequence,
            channel_id=channel,
            arbitration_id=arbitration_id,
            is_extended=False,
            is_fd=is_fd,
            bitrate_switch=False,
            error_state_indicator=False,
            dlc=2,
            data=bytes([sequence % 256, 0x11]),
            direction=direction,
            hardware_timestamp=None,
            host_timestamp=2_000.0 + sequence,
            normalized_timestamp=sequence / 10.0,
            clock_domain="host.monotonic",
            timestamp_quality=TimestampQuality.HOST,
            flags=0,
        )
        for sequence, arbitration_id, channel, direction, is_fd in COMBINATION_SOURCE
    )


COMBINATION_FRAMES = _combination_frames()


def _without(axis: str) -> dict[str, object]:
    """Return :data:`COMBINED` with one axis removed."""
    if axis == "arbitration_id_range":
        dropped = ("arbitration_id_start", "arbitration_id_end")
    elif axis == "arbitration_id_mask":
        dropped = ("arbitration_id_mask", "arbitration_id_mask_value")
    else:
        dropped = (axis,)
    return {key: value for key, value in COMBINED.items() if key not in dropped}


def test_a_full_combination_filter_applies_every_axis(tmp_path: Path) -> None:
    """All the axes at once, and every one of them demonstrably in force.

    Relaxing a single axis must add exactly the frame that violates only that
    axis — no more, and never nothing. "Nothing added" would mean the axis was
    ignored; "something else added" would mean the frame was excluded for the
    wrong reason.
    """
    with ProjectService().create(tmp_path / "vehicle.canx", display_name="A") as handle:
        session_id = _write_session(handle, COMBINATION_FRAMES)
        service = QueryService(handle.root)

        combined = _query(service, session_id, **COMBINED)
        relaxed = {
            axis: _query(service, session_id, **_without(axis))
            for axis in (*WITNESSES, "sequence_start")
        }

    assert combined == _selected(COMBINATION_FRAMES, **COMBINED)
    assert combined == [0, 1]
    for axis, witness in WITNESSES.items():
        assert set(relaxed[axis]) == {*combined, witness}, axis
    # The lower sequence bound excludes nothing in this fixture, so relaxing it
    # must change nothing at all — an axis may be redundant, never silent.
    assert relaxed["sequence_start"] == combined


def test_the_combination_fixture_is_exactly_the_intended_one() -> None:
    """A guard on the witness design: each extra frame violates only its axis."""
    combined = _selected(COMBINATION_FRAMES, **COMBINED)

    assert combined == [0, 1]
    for frame_sequence in sorted(WITNESSES.values()):
        assert frame_sequence not in combined
