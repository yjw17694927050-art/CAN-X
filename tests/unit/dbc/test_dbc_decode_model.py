"""Invariants of the immutable decoded-result domain.

These tests are about shapes and guarantees, not about bit extraction: they never
build a database and never call a decoder. What is asserted here is what every
later consumer — a batch caller, a Trace view, an Agent tool — is entitled to
assume: results are immutable, a signal keeps its raw number next to its choice
label, an outcome is exactly one of a result or a failure, and a decoded batch
cannot lose or reorder a frame.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from canx.dbc.decode_model import (
    DbcDecodeFailure,
    DecodedFrame,
    DecodedFrameBatch,
    DecodedFrameOutcome,
    DecodedSignal,
)
from canx.dbc.errors import DbcError, DbcMessageNotFoundError
from canx.domain.frame import Direction, Frame, TimestampQuality


def make_frame(sequence: int = 1, **changes: object) -> Frame:
    """Create a valid Classic CAN frame with explicit domain fields."""
    values: dict[str, object] = {
        "sequence": sequence,
        "channel_id": "can0",
        "arbitration_id": 0x123,
        "is_extended": False,
        "is_fd": False,
        "bitrate_switch": False,
        "error_state_indicator": False,
        "dlc": 3,
        "data": bytes.fromhex("010203"),
        "direction": Direction.RX,
        "hardware_timestamp": None,
        "host_timestamp": 100.25,
        "normalized_timestamp": 0.25,
        "clock_domain": "host.monotonic",
        "timestamp_quality": TimestampQuality.HOST,
        "flags": 0,
    }
    values.update(changes)
    return Frame(**values)  # type: ignore[arg-type]


def make_signal(**changes: object) -> DecodedSignal:
    values: dict[str, object] = {
        "name": "EngineSpeed",
        "raw_value": 1000,
        "physical_value": 210.0,
        "choice_label": None,
        "unit": "rpm",
    }
    values.update(changes)
    return DecodedSignal(**values)  # type: ignore[arg-type]


def make_decoded(**changes: object) -> DecodedFrame:
    values: dict[str, object] = {
        "frame": make_frame(),
        "message_name": "EngineData",
        "signals": (make_signal(),),
    }
    values.update(changes)
    return DecodedFrame(**values)  # type: ignore[arg-type]


def make_failure(**changes: object) -> DbcDecodeFailure:
    values: dict[str, object] = {
        "code": "dbc.message_not_found",
        "message": "The frame's identifier pair is not defined.",
        "recoverable": False,
        "source": "dbc",
        "details": {"arbitration_id": 0x123},
    }
    values.update(changes)
    return DbcDecodeFailure(**values)  # type: ignore[arg-type]


# --- DecodedSignal ----------------------------------------------------------


def test_a_decoded_signal_keeps_the_raw_number_next_to_the_choice_label() -> None:
    """A named value is additional semantics, never a replacement."""
    signal = make_signal(raw_value=2, physical_value=2.0, choice_label="Error")

    assert signal.raw_value == 2
    assert signal.physical_value == 2.0
    assert signal.choice_label == "Error"


def test_a_decoded_signal_without_a_choice_reports_no_label() -> None:
    assert make_signal(choice_label=None).choice_label is None


def test_a_decoded_signal_normalizes_an_integer_physical_value_to_float() -> None:
    assert make_signal(physical_value=2).physical_value == 2.0
    assert isinstance(make_signal(physical_value=2).physical_value, float)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_decoded_signal_refuses_a_non_finite_physical_value(value: float) -> None:
    with pytest.raises(ValueError, match="physical_value"):
        make_signal(physical_value=value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("name", "   "),
        ("name", 3),
        ("raw_value", 1.5),
        ("raw_value", "1"),
        ("raw_value", True),
        ("physical_value", "1.5"),
        ("choice_label", 2),
        ("unit", 2),
    ],
)
def test_a_decoded_signal_refuses_unusable_fields(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=field):
        make_signal(**{field: value})


def test_a_decoded_signal_is_immutable() -> None:
    signal = make_signal()

    with pytest.raises(FrozenInstanceError):
        signal.raw_value = 0  # type: ignore[misc]


# --- DecodedFrame -----------------------------------------------------------


def test_a_decoded_frame_keeps_the_canonical_frame_it_came_from() -> None:
    """Provenance is not summarised away."""
    frame = make_frame(sequence=7, is_fd=True, dlc=12, data=bytes(12))

    decoded = make_decoded(frame=frame)

    assert decoded.frame is frame
    assert decoded.frame.sequence == 7
    assert decoded.frame.is_fd is True


def test_a_decoded_frame_with_no_active_signals_is_legal() -> None:
    assert make_decoded(signals=()).signals == ()


def test_a_decoded_frame_refuses_two_signals_with_the_same_name() -> None:
    with pytest.raises(ValueError, match="share a name"):
        make_decoded(signals=(make_signal(name="A"), make_signal(name="A")))


def test_a_decoded_frame_requires_a_tuple_of_signals() -> None:
    with pytest.raises(ValueError, match="signals"):
        make_decoded(signals=[make_signal()])


@pytest.mark.parametrize(
    ("field", "value"),
    [("frame", "not-a-frame"), ("message_name", ""), ("message_name", 3), ("signals", "x")],
)
def test_a_decoded_frame_refuses_unusable_fields(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        make_decoded(**{field: value})


def test_a_decoded_frame_looks_a_signal_up_by_name_and_reports_absent_ones() -> None:
    decoded = make_decoded(signals=(make_signal(name="A"), make_signal(name="B")))

    assert decoded.signal("B") == make_signal(name="B")
    assert decoded.signal("C") is None


def test_a_decoded_frame_is_immutable() -> None:
    decoded = make_decoded()

    with pytest.raises(FrozenInstanceError):
        decoded.message_name = "Other"  # type: ignore[misc]


def test_repeated_decodes_of_the_same_inputs_compare_equal() -> None:
    assert make_decoded() == make_decoded()
    assert make_decoded(signals=(make_signal(name="A"),)) != make_decoded()


# --- DbcDecodeFailure -------------------------------------------------------


def test_a_failure_snapshot_copies_the_shared_five_field_envelope() -> None:
    error = DbcMessageNotFoundError(
        "The frame is not defined in this database.", details={"arbitration_id": 0x123}
    )

    failure = DbcDecodeFailure.from_error(error)

    assert failure.code == "dbc.message_not_found"
    assert failure.message == error.message
    assert failure.recoverable is False
    assert failure.source == "dbc"
    assert dict(failure.details) == {"arbitration_id": 0x123}


def test_a_failure_snapshot_does_not_alias_the_error_it_came_from() -> None:
    error = DbcError("boom", details={"arbitration_id": 0x123})
    failure = DbcDecodeFailure.from_error(error)

    error.details["arbitration_id"] = 0x456

    assert dict(failure.details) == {"arbitration_id": 0x123}


def test_a_failure_snapshot_cannot_be_mutated_through_its_details() -> None:
    failure = make_failure()

    with pytest.raises(TypeError):
        failure.details["arbitration_id"] = 0  # type: ignore[index]


def test_a_failure_snapshot_does_not_alias_the_mapping_it_was_given() -> None:
    supplied = {"arbitration_id": 0x123}
    failure = make_failure(details=supplied)

    supplied["arbitration_id"] = 0x456

    assert dict(failure.details) == {"arbitration_id": 0x123}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code", ""),
        ("code", 1),
        ("message", 1),
        ("recoverable", "no"),
        ("source", ""),
        ("details", "not-a-mapping"),
    ],
)
def test_a_failure_snapshot_refuses_an_incomplete_envelope(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=field):
        make_failure(**{field: value})


def test_a_failure_snapshot_is_immutable() -> None:
    failure = make_failure()

    with pytest.raises(FrozenInstanceError):
        failure.code = "dbc.other"  # type: ignore[misc]


# --- DecodedFrameOutcome ----------------------------------------------------


def test_an_outcome_carries_exactly_one_of_a_result_or_a_failure() -> None:
    frame = make_frame()
    decoded = make_decoded(frame=frame)
    failure = make_failure()

    assert DecodedFrameOutcome.succeeded(frame, decoded) == DecodedFrameOutcome(
        frame=frame, decoded=decoded, failure=None
    )
    assert DecodedFrameOutcome.failed(frame, failure) == DecodedFrameOutcome(
        frame=frame, decoded=None, failure=failure
    )


def test_an_outcome_reports_success_only_when_there_is_no_failure() -> None:
    frame = make_frame()

    assert DecodedFrameOutcome.succeeded(frame, make_decoded(frame=frame)).ok is True
    assert DecodedFrameOutcome.failed(frame, make_failure()).ok is False


@pytest.mark.parametrize(
    ("decoded", "failure"),
    [(None, None), ("both", "both")],
)
def test_an_outcome_refuses_anything_but_a_strict_either_or(
    decoded: object, failure: object
) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        DecodedFrameOutcome(
            frame=make_frame(),
            decoded=None if decoded is None else make_decoded(),
            failure=None if failure is None else make_failure(),
        )


def test_an_outcome_refuses_a_result_that_belongs_to_a_different_frame() -> None:
    with pytest.raises(ValueError, match="belong to the outcome's frame"):
        DecodedFrameOutcome.succeeded(
            make_frame(sequence=1), make_decoded(frame=make_frame(sequence=2))
        )


def test_an_outcome_is_immutable() -> None:
    outcome = DecodedFrameOutcome.succeeded(make_frame(), make_decoded())

    with pytest.raises(FrozenInstanceError):
        outcome.decoded = None  # type: ignore[misc]


# --- DecodedFrameBatch ------------------------------------------------------


def outcomes(count: int, *, start: int = 1) -> tuple[DecodedFrameOutcome, ...]:
    built = []
    for offset in range(count):
        frame = make_frame(sequence=start + offset)
        built.append(DecodedFrameOutcome.succeeded(frame, make_decoded(frame=frame)))
    return tuple(built)


def test_a_batch_derives_its_bounds_and_count_from_its_outcomes() -> None:
    batch = DecodedFrameBatch.create(stream_id="stream-1", outcomes=outcomes(3, start=10))

    assert batch.schema_version == DecodedFrameBatch.CURRENT_SCHEMA_VERSION
    assert (batch.first_sequence, batch.last_sequence, batch.frame_count) == (10, 12, 3)
    assert [outcome.frame.sequence for outcome in batch.outcomes] == [10, 11, 12]


def test_a_batch_keeps_failures_alongside_results_without_dropping_a_frame() -> None:
    frame_a = make_frame(sequence=1)
    frame_b = make_frame(sequence=2)

    batch = DecodedFrameBatch.create(
        stream_id="stream-1",
        outcomes=(
            DecodedFrameOutcome.failed(frame_a, make_failure()),
            DecodedFrameOutcome.succeeded(frame_b, make_decoded(frame=frame_b)),
        ),
    )

    assert [outcome.ok for outcome in batch.outcomes] == [False, True]
    assert batch.frame_count == 2


def test_a_batch_refuses_an_empty_outcome_sequence() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        DecodedFrameBatch.create(stream_id="stream-1", outcomes=())


def test_a_batch_refuses_non_contiguous_outcomes() -> None:
    first = DecodedFrameOutcome.succeeded(make_frame(sequence=1), make_decoded())
    third_frame = make_frame(sequence=3)

    with pytest.raises(ValueError, match="contiguous"):
        DecodedFrameBatch.create(
            stream_id="stream-1",
            outcomes=(
                first,
                DecodedFrameOutcome.succeeded(third_frame, make_decoded(frame=third_frame)),
            ),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("first_sequence", 99), ("last_sequence", 99), ("frame_count", 99)],
)
def test_a_batch_refuses_bookkeeping_that_disagrees_with_its_outcomes(
    field: str, value: object
) -> None:
    batch = DecodedFrameBatch.create(stream_id="stream-1", outcomes=outcomes(2, start=5))
    values = {
        "schema_version": batch.schema_version,
        "stream_id": batch.stream_id,
        "first_sequence": batch.first_sequence,
        "last_sequence": batch.last_sequence,
        "frame_count": batch.frame_count,
        "outcomes": batch.outcomes,
    }
    values[field] = value

    with pytest.raises(ValueError):
        DecodedFrameBatch(**values)  # type: ignore[arg-type]


def test_a_batch_refuses_an_unknown_schema_version() -> None:
    batch = DecodedFrameBatch.create(stream_id="stream-1", outcomes=outcomes(1))

    with pytest.raises(ValueError, match="schema_version"):
        DecodedFrameBatch(
            schema_version=batch.schema_version + 1,
            stream_id=batch.stream_id,
            first_sequence=batch.first_sequence,
            last_sequence=batch.last_sequence,
            frame_count=batch.frame_count,
            outcomes=batch.outcomes,
        )


def test_a_batch_refuses_a_list_of_outcomes() -> None:
    """``create`` takes any sequence; the model itself stores a tuple or nothing."""
    batch = DecodedFrameBatch.create(stream_id="stream-1", outcomes=outcomes(1))

    with pytest.raises(ValueError, match="outcomes"):
        DecodedFrameBatch(
            schema_version=batch.schema_version,
            stream_id=batch.stream_id,
            first_sequence=batch.first_sequence,
            last_sequence=batch.last_sequence,
            frame_count=batch.frame_count,
            outcomes=list(batch.outcomes),  # type: ignore[arg-type]
        )


def test_a_batch_created_from_any_sequence_stores_a_tuple() -> None:
    batch = DecodedFrameBatch.create(stream_id="stream-1", outcomes=outcomes(2))

    assert isinstance(batch.outcomes, tuple)
    assert batch == DecodedFrameBatch.create(stream_id="stream-1", outcomes=list(outcomes(2)))


def test_a_batch_is_immutable_and_its_outcomes_are_a_tuple() -> None:
    batch = DecodedFrameBatch.create(stream_id="stream-1", outcomes=outcomes(2))

    assert isinstance(batch.outcomes, tuple)
    with pytest.raises(FrozenInstanceError):
        batch.frame_count = 5  # type: ignore[misc]


def test_a_batch_refuses_a_blank_stream_id() -> None:
    with pytest.raises(ValueError, match="stream_id"):
        DecodedFrameBatch.create(stream_id="", outcomes=outcomes(1))
