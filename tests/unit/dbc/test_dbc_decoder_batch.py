"""Batch decode: one outcome per frame, same order, nothing silently dropped.

A caller that decodes a batch must be able to tell, per frame, whether it
decoded and — when it did not — why. That makes "how many frames were skipped"
a checkable property instead of a guess, which is what a later Trace view or
Agent tool needs before it can trust a decoded stream.

The decoder is also asserted to be a read-only object: decoding mutates neither
the database it was built from nor the frames it is given, and the same inputs
produce equal results from any thread.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from canx.dbc.decode import DbcDecoder
from canx.dbc.errors import DbcMessageNotFoundError, DbcPayloadTooShortError
from canx.domain.batch import FrameBatch
from decode_builders import LE, decoder_for, frame, message, signal
from decode_builders import database as build_database


def two_message_decoder():
    """A decoder with one 8-byte message on 0x100 and one on 0x200."""
    return decoder_for(
        message((signal("A", 0, 8, byte_order=LE),), name="MessageA", frame_id=0x100),
        message((signal("B", 0, 8, byte_order=LE),), name="MessageB", frame_id=0x200),
    )


def batch_of(*frames) -> FrameBatch:
    return FrameBatch.create(stream_id="stream-1", frames=list(frames))


def known(sequence: int, value: int = 1):
    return frame(bytes([value]) + bytes(7), sequence=sequence, arbitration_id=0x100)


def unknown(sequence: int):
    return frame(bytes(8), sequence=sequence, arbitration_id=0x7FF)


# --- one outcome per frame --------------------------------------------------


def test_a_batch_returns_one_outcome_per_input_frame() -> None:
    decoder = two_message_decoder()
    source = batch_of(known(1), known(2, 5), known(3, 9))

    decoded = decoder.decode_batch(source)

    assert len(decoded.outcomes) == len(source.frames)
    assert decoded.frame_count == source.frame_count
    assert decoded.stream_id == source.stream_id
    assert (decoded.first_sequence, decoded.last_sequence) == (
        source.first_sequence,
        source.last_sequence,
    )


def test_outcomes_keep_the_input_order_and_sequence() -> None:
    decoder = two_message_decoder()
    source = batch_of(known(10), unknown(11), known(12))

    decoded = decoder.decode_batch(source)

    assert [outcome.frame.sequence for outcome in decoded.outcomes] == [10, 11, 12]
    assert [outcome.frame for outcome in decoded.outcomes] == list(source.frames)


def test_an_unknown_message_does_not_drop_its_frame() -> None:
    """The skipped frame is still a row — with a reason attached."""
    decoder = two_message_decoder()
    source = batch_of(known(1), unknown(2), known(3))

    decoded = decoder.decode_batch(source)

    assert [outcome.ok for outcome in decoded.outcomes] == [True, False, True]
    skipped = decoded.outcomes[1]
    assert skipped.decoded is None
    assert skipped.failure is not None
    assert skipped.failure.code == "dbc.message_not_found"


def test_a_per_frame_failure_does_not_abort_the_batch() -> None:
    decoder = two_message_decoder()
    source = batch_of(unknown(1), unknown(2), unknown(3))

    decoded = decoder.decode_batch(source)

    assert [outcome.ok for outcome in decoded.outcomes] == [False, False, False]
    assert decoded.frame_count == 3


def test_every_outcome_is_exactly_one_of_a_result_or_a_failure() -> None:
    decoder = two_message_decoder()
    source = batch_of(known(1), unknown(2))

    for outcome in decoder.decode_batch(source).outcomes:
        assert (outcome.decoded is None) != (outcome.failure is None)


def test_a_mixed_batch_reports_each_failure_with_its_own_code() -> None:
    decoder = two_message_decoder()
    short = frame(bytes(2), sequence=3, arbitration_id=0x100)
    source = batch_of(known(1), unknown(2), short)

    decoded = decoder.decode_batch(source)

    codes = [
        None if outcome.failure is None else outcome.failure.code for outcome in decoded.outcomes
    ]
    assert codes == [None, "dbc.message_not_found", "dbc.payload_too_short"]


def test_a_failure_snapshot_carries_the_shared_envelope() -> None:
    decoder = two_message_decoder()
    source = batch_of(unknown(1))

    failure = decoder.decode_batch(source).outcomes[0].failure

    assert failure is not None
    assert failure.source == "dbc"
    assert failure.recoverable is False
    assert failure.message
    assert dict(failure.details)["arbitration_id"] == 0x7FF


def test_a_successful_outcome_carries_the_decoded_frame_of_its_own_frame() -> None:
    decoder = two_message_decoder()
    source = batch_of(known(7, 0x2A))

    outcome = decoder.decode_batch(source).outcomes[0]

    assert outcome.decoded is not None
    assert outcome.decoded.frame is outcome.frame
    assert outcome.decoded.message_name == "MessageA"
    assert outcome.decoded.signals[0].raw_value == 0x2A


# --- argument types ---------------------------------------------------------


@pytest.mark.parametrize("value", [None, "batch", [known(1)], 7])
def test_decoding_something_that_is_not_a_batch_is_a_programming_error(value: object) -> None:
    decoder = two_message_decoder()

    with pytest.raises(TypeError):
        decoder.decode_batch(value)  # type: ignore[arg-type]


# --- determinism and read-only behaviour ------------------------------------


def test_decoding_the_same_batch_twice_gives_equal_results() -> None:
    decoder = two_message_decoder()
    source = batch_of(known(1), unknown(2), known(3, 4))

    assert decoder.decode_batch(source) == decoder.decode_batch(source)


def test_decoding_the_same_frame_twice_gives_equal_results() -> None:
    decoder = two_message_decoder()
    subject = known(1, 7)

    assert decoder.decode_frame(subject) == decoder.decode_frame(subject)


def test_decoding_does_not_modify_the_database_it_was_built_from() -> None:
    """The database is an input, not a cache the decoder writes through."""
    definition = message((signal("A", 0, 8, byte_order=LE),), name="MessageA", frame_id=0x100)
    database = build_database(definition)
    decoder = DbcDecoder(database)
    reference = build_database(definition)

    decoder.decode_frame(known(1))
    decoder.decode_batch(batch_of(known(1), unknown(2)))

    assert database == reference
    assert database.messages[0].signals[0].name == "A"
    assert database.messages[0].frame_id == 0x100


def test_decoding_does_not_modify_the_frame_it_is_given() -> None:
    decoder = two_message_decoder()
    subject = known(1)
    snapshot = (subject.sequence, subject.data, subject.arbitration_id)

    decoder.decode_frame(subject)

    assert (subject.sequence, subject.data, subject.arbitration_id) == snapshot


def test_the_same_decoder_serves_many_batches() -> None:
    decoder = two_message_decoder()

    first = decoder.decode_batch(batch_of(known(1)))
    second = decoder.decode_batch(batch_of(known(2)))

    assert [outcome.frame.sequence for outcome in first.outcomes] == [1]
    assert [outcome.frame.sequence for outcome in second.outcomes] == [2]


def test_one_decoder_can_be_shared_by_concurrent_readers() -> None:
    """The plan is read-only after compilation, so readers cannot disturb it."""
    decoder = two_message_decoder()
    source = batch_of(known(1, 3), unknown(2), known(3, 4))
    expected = decoder.decode_batch(source)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: decoder.decode_batch(source), range(64)))

    assert all(result == expected for result in results)


# --- the two entry points agree ---------------------------------------------


def test_the_batch_entry_point_reports_what_the_strict_entry_point_raises() -> None:
    decoder = two_message_decoder()
    subject = unknown(1)

    with pytest.raises(DbcMessageNotFoundError) as info:
        decoder.decode_frame(subject)
    outcome = decoder.decode_batch(batch_of(subject)).outcomes[0]

    assert outcome.failure is not None
    assert outcome.failure.code == info.value.code
    assert dict(outcome.failure.details) == info.value.details
    assert outcome.failure.message == info.value.message


def test_a_short_payload_is_visibly_the_same_failure_in_both_entry_points() -> None:
    decoder = two_message_decoder()
    subject = frame(bytes(2), sequence=1, arbitration_id=0x100)

    with pytest.raises(DbcPayloadTooShortError) as info:
        decoder.decode_frame(subject)
    outcome = decoder.decode_batch(batch_of(subject)).outcomes[0]

    assert outcome.failure is not None
    assert outcome.failure.code == info.value.code
    assert outcome.failure.details["expected_length"] == 8
    assert outcome.failure.details["actual_length"] == 2
