"""Decode work sets through the decoder: frame-local, so gaps are irrelevant.

A decoder answers "what does this frame mean", one frame at a time. Nothing in a
decode consults a neighbouring sequence, so a request carrying 1, 3 and 5 must give
exactly the answers 1, 3 and 5 would get on their own — which is what makes the
non-contiguous request a legitimate shape rather than a tolerated one. The
equivalent-batch test pins that claim against the contiguous path.
"""

from __future__ import annotations

import pytest
from canx.dbc.decode import DbcDecoder
from canx.dbc.decode_set import DecodeFrameSet
from canx.domain.batch import FrameBatch
from decode_builders import LE, decoder_for, frame, message, signal


def two_message_decoder() -> DbcDecoder:
    """A decoder with one 8-byte message on 0x100 and one on 0x200."""
    return decoder_for(
        message((signal("A", 0, 8, byte_order=LE),), name="MessageA", frame_id=0x100),
        message((signal("B", 0, 8, byte_order=LE),), name="MessageB", frame_id=0x200),
    )


def known(sequence: int, value: int = 1):
    return frame(bytes([value]) + bytes(7), sequence=sequence, arbitration_id=0x100)


def unknown(sequence: int):
    return frame(bytes(8), sequence=sequence, arbitration_id=0x7FF)


def work_set(*frames) -> DecodeFrameSet:
    return DecodeFrameSet.create(stream_id="stream-1", frames=list(frames))


def test_a_non_contiguous_work_set_decodes_every_frame() -> None:
    decoder = two_message_decoder()

    decoded = decoder.decode_frames(work_set(known(1), known(3, 5), known(5, 9)))

    assert [outcome.frame.sequence for outcome in decoded.outcomes] == [1, 3, 5]
    assert decoded.frame_count == 3
    assert decoded.stream_id == "stream-1"
    assert all(outcome.ok for outcome in decoded.outcomes)


def test_a_failure_inside_a_gap_bearing_work_set_stays_data() -> None:
    """The middle frame is unknown; its neighbours are unaffected and still decoded."""
    decoder = two_message_decoder()

    decoded = decoder.decode_frames(work_set(known(1), unknown(100), known(10_000)))

    assert [outcome.ok for outcome in decoded.outcomes] == [True, False, True]
    assert decoded.outcomes[1].failure is not None
    assert decoded.outcomes[1].failure.code == "dbc.message_not_found"


def test_a_non_contiguous_work_set_agrees_with_decoding_each_frame_alone() -> None:
    """The gaps change nothing: the same frame, decoded alone, gives the same answer."""
    decoder = two_message_decoder()

    decoded = decoder.decode_frames(work_set(known(1), known(3, 5)))

    assert decoded.outcomes[0] == decoder.decode_frames(work_set(known(1))).outcomes[0]
    assert decoded.outcomes[1] == decoder.decode_frames(work_set(known(3, 5))).outcomes[0]


def test_a_contiguous_work_set_agrees_with_the_batch_path() -> None:
    """Same frames, same answers — the two containers share one decode loop."""
    decoder = two_message_decoder()
    frames = [known(1), known(2, 5), known(3, 9)]

    from_set = decoder.decode_frames(work_set(*frames))
    from_batch = decoder.decode_batch(
        FrameBatch.create(stream_id="stream-1", frames=frames)
    )

    assert from_set.outcomes == from_batch.outcomes
    assert from_set.stream_id == from_batch.stream_id


def test_a_non_contiguous_work_set_refuses_a_foreign_container() -> None:
    decoder = two_message_decoder()

    with pytest.raises(TypeError):
        decoder.decode_frames(  # type: ignore[arg-type]
            FrameBatch.create(stream_id="stream-1", frames=[known(1), known(2)])
        )
