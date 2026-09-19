"""Decode work sets: ordered and bounded, but deliberately not contiguous.

The regression this file exists for is a *shape* regression. A decode request used
to be constrained to the container a capture stream uses, whose sequences must be
consecutive; a viewport that alternates two channels therefore had to be cut into
one request per frame. These tests pin the replacement's own contract — and, just
as importantly, pin that the captured container was **not** widened to make it work:
the last section re-asserts that a ``FrameBatch`` still refuses ``1, 3``.
"""

from __future__ import annotations

import pytest
from canx.dbc.decode_model import (
    DecodedFrame,
    DecodedFrameOutcome,
    DecodedSignal,
)
from canx.dbc.decode_set import DecodedFrameSet, DecodeFrameSet
from canx.domain.batch import FrameBatch
from canx.domain.frame import Frame
from decode_builders import frame


def sample(sequence: int, *, data: bytes | None = None) -> Frame:
    """A canonical frame carrying ``sequence``; the payload never matters here."""
    return frame(bytes(8) if data is None else data, sequence=sequence)


def outcomes(sequences: tuple[int, ...]) -> tuple[DecodedFrameOutcome, ...]:
    """One successful outcome per sequence, in the given order."""
    built = []
    for sequence in sequences:
        subject = sample(sequence)
        built.append(
            DecodedFrameOutcome.succeeded(
                subject,
                DecodedFrame(
                    frame=subject,
                    message_name="MessageA",
                    signals=(DecodedSignal("A", 1, 1.0, None, None),),
                ),
            )
        )
    return tuple(built)


# --- DecodeFrameSet: what it accepts ----------------------------------------


def test_a_contiguous_work_set_is_accepted() -> None:
    work = DecodeFrameSet.create(
        stream_id="stream-1", frames=[sample(1), sample(2), sample(3)]
    )

    assert work.sequences == (1, 2, 3)
    assert work.frame_count == 3
    assert work.stream_id == "stream-1"


def test_a_non_contiguous_work_set_is_accepted() -> None:
    """The whole point: 1, 3, 5 is a decode request, not a broken batch."""
    work = DecodeFrameSet.create(
        stream_id="stream-1", frames=[sample(1), sample(3), sample(5)]
    )

    assert work.sequences == (1, 3, 5)
    assert work.frame_count == 3


def test_a_work_set_with_large_gaps_is_accepted() -> None:
    """A gap is a gap whatever its size; nothing about this container interpolates."""
    work = DecodeFrameSet.create(
        stream_id="stream-1", frames=[sample(1), sample(100), sample(10_000)]
    )

    assert work.sequences == (1, 100, 10_000)


def test_the_order_of_a_work_set_is_the_caller_s_order() -> None:
    """No sorting: an outcome is aligned to a frame by position, not by sequence."""
    work = DecodeFrameSet.create(stream_id="stream-1", frames=[sample(5), sample(9)])

    assert work.sequences == (5, 9)


def test_exactly_the_maximum_number_of_frames_is_accepted() -> None:
    frames = [sample(sequence) for sequence in range(1, DecodeFrameSet.MAX_FRAMES + 1)]

    work = DecodeFrameSet.create(stream_id="stream-1", frames=frames)

    assert work.frame_count == DecodeFrameSet.MAX_FRAMES


def test_more_than_the_maximum_number_of_frames_is_refused() -> None:
    frames = [sample(sequence) for sequence in range(1, DecodeFrameSet.MAX_FRAMES + 2)]

    with pytest.raises(ValueError, match="at most"):
        DecodeFrameSet.create(stream_id="stream-1", frames=frames)


def test_a_work_set_of_any_sequence_stores_a_tuple() -> None:
    work = DecodeFrameSet.create(stream_id="stream-1", frames=[sample(1), sample(4)])

    assert isinstance(work.frames, tuple)
    assert work == DecodeFrameSet.create(stream_id="stream-1", frames=(sample(1), sample(4)))


# --- DecodeFrameSet: what it refuses ----------------------------------------


def test_a_duplicated_sequence_is_refused() -> None:
    """Strictly increasing is what makes 'one outcome per submitted frame' real."""
    with pytest.raises(ValueError, match="strictly increasing"):
        DecodeFrameSet.create(stream_id="stream-1", frames=[sample(1), sample(1)])


def test_a_reordered_work_set_is_refused() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        DecodeFrameSet.create(
            stream_id="stream-1", frames=[sample(3), sample(1), sample(2)]
        )


def test_an_empty_work_set_is_refused() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        DecodeFrameSet.create(stream_id="stream-1", frames=[])


def test_a_blank_stream_id_is_refused() -> None:
    with pytest.raises(ValueError, match="stream_id"):
        DecodeFrameSet.create(stream_id="", frames=[sample(1)])


def test_frames_that_are_not_canonical_are_refused() -> None:
    with pytest.raises(ValueError, match="canonical Frame"):
        DecodeFrameSet(
            schema_version=DecodeFrameSet.CURRENT_SCHEMA_VERSION,
            stream_id="stream-1",
            frame_count=1,
            frames=[sample(1)],  # type: ignore[arg-type]
        )


def test_a_count_that_disagrees_with_the_frames_is_refused() -> None:
    with pytest.raises(ValueError, match="frame_count"):
        DecodeFrameSet(
            schema_version=DecodeFrameSet.CURRENT_SCHEMA_VERSION,
            stream_id="stream-1",
            frame_count=2,
            frames=(sample(1),),
        )


def test_an_unknown_schema_version_is_refused() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        DecodeFrameSet(
            schema_version=DecodeFrameSet.CURRENT_SCHEMA_VERSION + 1,
            stream_id="stream-1",
            frame_count=1,
            frames=(sample(1),),
        )


def test_a_work_set_is_immutable() -> None:
    from dataclasses import FrozenInstanceError

    work = DecodeFrameSet.create(stream_id="stream-1", frames=[sample(1)])

    with pytest.raises(FrozenInstanceError):
        work.stream_id = "other"  # type: ignore[misc]


# --- DecodedFrameSet --------------------------------------------------------


def test_a_decoded_set_accepts_non_contiguous_outcomes() -> None:
    decoded = DecodedFrameSet.create(stream_id="stream-1", outcomes=outcomes((1, 3, 5)))

    assert decoded.sequences == (1, 3, 5)
    assert decoded.frame_count == 3
    assert decoded.schema_version == DecodedFrameSet.CURRENT_SCHEMA_VERSION


def test_a_decoded_set_keeps_every_frame_including_the_failures() -> None:
    subject = sample(2)
    decoded = DecodedFrameSet.create(
        stream_id="stream-1",
        outcomes=(
            outcomes((1,))[0],
            DecodedFrameOutcome.failed(subject, _failure()),
        ),
    )

    assert [outcome.ok for outcome in decoded.outcomes] == [True, False]
    assert decoded.frame_count == 2


def test_a_decoded_set_refuses_a_duplicated_outcome_sequence() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        DecodedFrameSet.create(stream_id="stream-1", outcomes=outcomes((4, 4)))


def test_a_decoded_set_refuses_reordered_outcomes() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        DecodedFrameSet.create(stream_id="stream-1", outcomes=outcomes((9, 2)))


def test_a_decoded_set_refuses_an_empty_outcome_sequence() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        DecodedFrameSet.create(stream_id="stream-1", outcomes=())


def test_a_decoded_set_refuses_a_count_that_disagrees() -> None:
    with pytest.raises(ValueError, match="frame_count"):
        DecodedFrameSet(
            schema_version=DecodedFrameSet.CURRENT_SCHEMA_VERSION,
            stream_id="stream-1",
            frame_count=3,
            outcomes=outcomes((1, 2)),
        )


def test_a_decoded_set_refuses_an_unknown_schema_version() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        DecodedFrameSet(
            schema_version=DecodedFrameSet.CURRENT_SCHEMA_VERSION + 1,
            stream_id="stream-1",
            frame_count=1,
            outcomes=outcomes((1,)),
        )


def test_a_decoded_set_refuses_a_blank_stream_id() -> None:
    with pytest.raises(ValueError, match="stream_id"):
        DecodedFrameSet.create(stream_id="  ", outcomes=outcomes((1,)))


# --- the captured container was not widened ---------------------------------


def test_the_captured_batch_still_refuses_a_gap() -> None:
    """The decode container is new; the capture container is untouched.

    ``FrameBatch`` describes a slice of one stream's numbering, and that is true
    regardless of what any decode request needs. If this test ever fails, the
    fix went into the wrong container.
    """
    with pytest.raises(ValueError, match="contiguous"):
        FrameBatch.create(stream_id="stream-1", frames=[sample(1), sample(3)])


def _failure():
    from canx.dbc.decode_model import DbcDecodeFailure

    return DbcDecodeFailure(
        code="dbc.message_not_found",
        message="not defined",
        recoverable=False,
        source="dbc",
        details={},
    )
