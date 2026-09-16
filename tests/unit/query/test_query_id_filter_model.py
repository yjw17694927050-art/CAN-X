"""CAN id range and mask in the engine-independent query domain.

CAN identifier filtering has three independent axes:

* an exact id set (``arbitration_ids``),
* an inclusive id range (``arbitration_id_start`` / ``arbitration_id_end``),
* a masked equality test (``arbitration_id_mask`` / ``arbitration_id_mask_value``).

Each axis is optional, every axis is ``AND``-ed with the others, and nothing in
the model can express an arbitrary boolean expression. These tests pin the
*validation* contract; the observable selection is covered by
``test_query_id_filter_engine.py``.

A mask and its value only mean something together: a mask alone would silently
degrade into an "every frame passes" predicate, so the pair is required.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from canx.query.errors import QueryValidationError
from canx.query.model import MAX_ARBITRATION_ID, FrameFilter

SESSION_ID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"


def _filter(**changes: object) -> FrameFilter:
    values: dict[str, object] = {"session_id": SESSION_ID}
    values.update(changes)
    return FrameFilter(**values)  # type: ignore[arg-type]


def test_the_id_axes_default_to_absent() -> None:
    frame_filter = _filter()

    assert frame_filter.arbitration_id_start is None
    assert frame_filter.arbitration_id_end is None
    assert frame_filter.arbitration_id_mask is None
    assert frame_filter.arbitration_id_mask_value is None


def test_the_extended_id_ceiling_is_the_twenty_nine_bit_maximum() -> None:
    assert MAX_ARBITRATION_ID == 0x1FFFFFFF


def test_a_range_accepts_its_boundary_values() -> None:
    frame_filter = _filter(
        arbitration_id_start=0, arbitration_id_end=MAX_ARBITRATION_ID
    )

    assert frame_filter.arbitration_id_start == 0
    assert frame_filter.arbitration_id_end == MAX_ARBITRATION_ID


def test_a_single_id_range_is_a_valid_one_row_window() -> None:
    frame_filter = _filter(arbitration_id_start=0x100, arbitration_id_end=0x100)

    assert frame_filter.arbitration_id_start == frame_filter.arbitration_id_end == 0x100


def test_an_open_ended_range_is_allowed() -> None:
    lower = _filter(arbitration_id_start=0x100)
    upper = _filter(arbitration_id_end=0x1FF)

    assert lower.arbitration_id_start == 0x100
    assert lower.arbitration_id_end is None
    assert upper.arbitration_id_start is None
    assert upper.arbitration_id_end == 0x1FF


def test_a_mask_pair_accepts_its_boundary_values() -> None:
    frame_filter = _filter(
        arbitration_id_mask=MAX_ARBITRATION_ID,
        arbitration_id_mask_value=MAX_ARBITRATION_ID,
    )

    assert frame_filter.arbitration_id_mask == MAX_ARBITRATION_ID
    assert frame_filter.arbitration_id_mask_value == MAX_ARBITRATION_ID


def test_a_zero_mask_is_legal_and_is_a_match_everything_predicate() -> None:
    """``(id & 0) == (0 & 0)`` is true for every id, so a zero mask is not an error.

    It is a vacuous predicate, not a rejected one: the model checks that the pair
    is coherent, not that it is selective.
    """
    frame_filter = _filter(arbitration_id_mask=0, arbitration_id_mask_value=0)

    assert frame_filter.arbitration_id_mask == 0
    assert frame_filter.arbitration_id_mask_value == 0


def test_every_id_axis_can_be_combined_with_the_other_filter_axes() -> None:
    frame_filter = _filter(
        sequence_start=0,
        sequence_end=10,
        normalized_timestamp_start=0.0,
        normalized_timestamp_end=10.0,
        channel_ids=("can0",),
        arbitration_ids=(0x123,),
        arbitration_id_start=0x100,
        arbitration_id_end=0x1FF,
        arbitration_id_mask=0x7F0,
        arbitration_id_mask_value=0x120,
        is_extended=False,
        is_fd=True,
    )

    assert frame_filter.arbitration_ids == (0x123,)
    assert frame_filter.arbitration_id_start == 0x100
    assert frame_filter.arbitration_id_end == 0x1FF
    assert frame_filter.arbitration_id_mask == 0x7F0
    assert frame_filter.arbitration_id_mask_value == 0x120


def test_the_id_axes_are_immutable() -> None:
    frame_filter = _filter(arbitration_id_start=0x100)

    with pytest.raises(FrozenInstanceError):
        frame_filter.arbitration_id_start = 0  # type: ignore[misc]


def test_new_id_axes_are_appended_so_existing_positional_calls_still_work() -> None:
    """V0.2-03 callers must not be re-pointed by this increment.

    Placing the new fields after every existing one keeps positional construction
    meaning exactly what it meant before the fields existed.
    """
    frame_filter = FrameFilter(SESSION_ID, 5, 9)

    assert frame_filter.sequence_start == 5
    assert frame_filter.sequence_end == 9
    assert frame_filter.arbitration_id_start is None
    assert frame_filter.arbitration_ids is None
    assert frame_filter.is_fd is None


def test_a_range_that_only_names_the_smaller_end_is_still_open_ended() -> None:
    """``arbitration_id_end`` alone is a ceiling, not an inverted range."""
    frame_filter = _filter(arbitration_id_end=0x1FF)

    assert frame_filter.arbitration_id_start is None


@pytest.mark.parametrize(
    "changes",
    [
        {"arbitration_id_start": -1},
        {"arbitration_id_end": -1},
        {"arbitration_id_start": MAX_ARBITRATION_ID + 1},
        {"arbitration_id_end": MAX_ARBITRATION_ID + 1},
        {"arbitration_id_start": True},
        {"arbitration_id_end": True},
        {"arbitration_id_start": "0x100"},
        {"arbitration_id_end": 1.5},
        {"arbitration_id_mask": -1},
        {"arbitration_id_mask_value": -1},
        {"arbitration_id_mask": MAX_ARBITRATION_ID + 1},
        {"arbitration_id_mask_value": MAX_ARBITRATION_ID + 1},
        {"arbitration_id_mask": True},
        {"arbitration_id_mask_value": True},
        {"arbitration_id_mask": "0x7F0"},
        {"arbitration_id_mask_value": 0.5},
    ],
    ids=[
        "range-start-negative",
        "range-end-negative",
        "range-start-too-large",
        "range-end-too-large",
        "range-start-bool",
        "range-end-bool",
        "range-start-string",
        "range-end-float",
        "mask-negative",
        "mask-value-negative",
        "mask-too-large",
        "mask-value-too-large",
        "mask-bool",
        "mask-value-bool",
        "mask-string",
        "mask-value-float",
    ],
)
def test_an_out_of_range_id_bound_is_never_accepted(changes: dict[str, object]) -> None:
    with pytest.raises(QueryValidationError) as info:
        _filter(**changes)

    assert info.value.code == "query.invalid_arbitration_id"
    assert set(info.value.details) == set(changes)


def test_an_inverted_id_range_is_rejected_with_its_own_code() -> None:
    with pytest.raises(QueryValidationError) as info:
        _filter(arbitration_id_start=0x1FF, arbitration_id_end=0x100)

    assert info.value.code == "query.invalid_arbitration_id_range"
    assert info.value.details == {
        "arbitration_id_start": 0x1FF,
        "arbitration_id_end": 0x100,
    }
    assert info.value.recoverable is False
    assert info.value.source == "query"


@pytest.mark.parametrize(
    "changes",
    [
        {"arbitration_id_mask": 0x7F0},
        {"arbitration_id_mask_value": 0x120},
    ],
    ids=["mask-without-value", "value-without-mask"],
)
def test_half_a_mask_pair_is_never_accepted(changes: dict[str, object]) -> None:
    """A lone mask or a lone value would silently mean something else.

    A mask with no value has no right-hand side, and a value with no mask would
    have to invent one — both are rejected rather than guessed at.
    """
    with pytest.raises(QueryValidationError) as info:
        _filter(**changes)

    assert info.value.code == "query.invalid_arbitration_id_mask"
    assert info.value.recoverable is False
    assert info.value.source == "query"
    assert "arbitration_id_mask" in info.value.details


def test_a_complete_mask_pair_is_never_reported_as_half_a_pair() -> None:
    frame_filter = _filter(arbitration_id_mask=0x7F0, arbitration_id_mask_value=0x120)

    assert frame_filter.arbitration_id_mask == 0x7F0
    assert frame_filter.arbitration_id_mask_value == 0x120


def test_an_out_of_range_mask_bound_is_validated_before_the_pair_is() -> None:
    """The bound check owns the failure when both halves are out of range."""
    with pytest.raises(QueryValidationError) as info:
        _filter(arbitration_id_mask=-1, arbitration_id_mask_value=-1)

    assert info.value.code == "query.invalid_arbitration_id"
