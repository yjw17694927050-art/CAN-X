"""Message lookup: ``(arbitration_id, is_extended)`` is the identity, never the id.

The decoder's first job is to decide *whether* a frame is defined at all, and the
answer must be decided by the identifier **pair**. ``0x123`` standard and
``0x123`` extended are two different messages that happen to share a number, and
matching on the number alone would let one silently answer for the other.
"""

from __future__ import annotations

import json

import pytest
from canx.dbc.decode import DbcDecoder
from canx.dbc.errors import (
    DbcFrameTypeMismatchError,
    DbcMessageNotFoundError,
)
from decode_builders import LE, decoder_for, frame, message, signal


def standard_and_extended() -> DbcDecoder:
    """Build a database holding 0x123 as two distinct messages."""
    return decoder_for(
        message(
            (signal("StandardOnly", 0, 8),),
            frame_id=0x123,
            name="StandardFrame",
            is_extended=False,
        ),
        message(
            (signal("ExtendedOnly", 0, 8),),
            frame_id=0x123,
            name="ExtendedFrame",
            is_extended=True,
        ),
    )


# --- identity ---------------------------------------------------------------


def test_a_known_standard_frame_decodes_to_its_standard_message() -> None:
    decoder = standard_and_extended()

    decoded = decoder.decode_frame(frame(bytes([1]) + bytes(7)))

    assert decoded.message_name == "StandardFrame"
    assert [item.name for item in decoded.signals] == ["StandardOnly"]


def test_a_known_extended_frame_decodes_to_its_extended_message() -> None:
    decoder = standard_and_extended()

    decoded = decoder.decode_frame(frame(bytes(8), is_extended=True))

    assert decoded.message_name == "ExtendedFrame"
    assert [item.name for item in decoded.signals] == ["ExtendedOnly"]


def test_the_same_number_in_two_spaces_never_crosses_over() -> None:
    """The dangerous case: identical numbers, different definitions."""
    decoder = standard_and_extended()

    standard = decoder.decode_frame(frame(bytes([0xAA]) + bytes(7)))
    extended = decoder.decode_frame(frame(bytes([0xAA]) + bytes(7), is_extended=True))

    assert standard.message_name == "StandardFrame"
    assert extended.message_name == "ExtendedFrame"
    assert standard.signals[0].name != extended.signals[0].name


def test_an_extended_message_is_reachable_only_through_an_extended_frame() -> None:
    decoder = decoder_for(
        message((signal("Only", 0, 8),), frame_id=0x123, name="ExtendedFrame", is_extended=True)
    )

    with pytest.raises(DbcMessageNotFoundError):
        decoder.decode_frame(frame(bytes(8), is_extended=False))


def test_a_standard_message_is_reachable_only_through_a_standard_frame() -> None:
    decoder = decoder_for(
        message((signal("Only", 0, 8),), frame_id=0x123, name="StandardFrame", is_extended=False)
    )

    with pytest.raises(DbcMessageNotFoundError):
        decoder.decode_frame(frame(bytes(8), is_extended=True))


# --- unknown messages -------------------------------------------------------


def test_an_undefined_identifier_is_reported_rather_than_returned_empty() -> None:
    """A strict caller must be told the question has no answer."""
    decoder = decoder_for(message((signal("Only", 0, 8),), frame_id=0x100, name="Known"))

    with pytest.raises(DbcMessageNotFoundError) as info:
        decoder.decode_frame(frame(bytes(8), arbitration_id=0x200))

    error = info.value
    assert error.code == "dbc.message_not_found"
    assert error.source == "dbc"
    assert error.recoverable is False
    assert error.details["arbitration_id"] == 0x200
    assert error.details["is_extended"] is False
    assert error.details["sequence"] == 1


def test_an_empty_database_defines_nothing() -> None:
    decoder = decoder_for()

    with pytest.raises(DbcMessageNotFoundError):
        decoder.decode_frame(frame(bytes(8)))


def test_a_not_found_failure_never_echoes_the_payload_or_a_traceback() -> None:
    decoder = decoder_for()

    with pytest.raises(DbcMessageNotFoundError) as info:
        decoder.decode_frame(frame(bytes.fromhex("deadbeef00000000")))

    rendered = json.dumps(info.value.details, default=str)

    for marker in ("deadbeef", "Traceback", "cantools", "object at 0x"):
        assert marker not in rendered


# --- CAN FD identity --------------------------------------------------------


def test_an_fd_message_decodes_an_fd_frame() -> None:
    decoder = decoder_for(
        message((signal("Only", 0, 8),), name="FdFrame", is_fd=True, length=64)
    )

    decoded = decoder.decode_frame(frame(bytes(64), is_fd=True))

    assert decoded.message_name == "FdFrame"


def test_an_fd_definition_is_never_applied_to_a_classic_frame() -> None:
    decoder = decoder_for(
        message((signal("Only", 0, 8),), name="FdFrame", is_fd=True, length=64)
    )

    with pytest.raises(DbcFrameTypeMismatchError) as info:
        decoder.decode_frame(frame(bytes(8), is_fd=False))

    assert info.value.code == "dbc.frame_type_mismatch"
    assert info.value.details["message_is_fd"] is True
    assert info.value.details["frame_is_fd"] is False
    assert info.value.recoverable is False


def test_a_classic_definition_is_never_applied_to_an_fd_frame() -> None:
    decoder = decoder_for(
        message((signal("Only", 0, 8),), name="ClassicFrame", is_fd=False, length=8)
    )

    with pytest.raises(DbcFrameTypeMismatchError) as info:
        decoder.decode_frame(frame(bytes(12), is_fd=True))

    assert info.value.details["message_is_fd"] is False
    assert info.value.details["frame_is_fd"] is True


def test_an_fd_flag_mismatch_is_not_reported_as_an_unknown_message() -> None:
    """The identifier *was* found; the frame kind is what disagrees."""
    decoder = decoder_for(message((signal("Only", 0, 8),), name="Classic", is_fd=False))

    with pytest.raises(DbcFrameTypeMismatchError):
        decoder.decode_frame(frame(bytes(12), is_fd=True))


# --- argument types ---------------------------------------------------------


@pytest.mark.parametrize("value", [None, "frame", 7, bytes(8)])
def test_decoding_something_that_is_not_a_frame_is_a_programming_error(
    value: object,
) -> None:
    decoder = decoder_for(message((signal("Only", 0, 8),), name="Known"))

    with pytest.raises(TypeError):
        decoder.decode_frame(value)  # type: ignore[arg-type]


def test_building_a_decoder_from_something_that_is_not_a_database_is_a_programming_error() -> None:
    with pytest.raises(TypeError):
        DbcDecoder({"messages": ()})  # type: ignore[arg-type]


# --- the frame is never touched ---------------------------------------------


def test_decoding_preserves_every_provenance_field_of_the_frame() -> None:
    decoder = decoder_for(message((signal("Only", 0, 8),), name="Known"))
    original = frame(
        bytes([1]) + bytes(7),
        sequence=42,
        arbitration_id=0x123,
        channel_id="can2",
        is_fd=False,
    )

    decoded = decoder.decode_frame(original)

    assert decoded.frame is original
    assert decoded.frame.channel_id == "can2"
    assert decoded.frame.sequence == 42
    assert decoded.frame.normalized_timestamp == 0.25
    assert decoded.frame.clock_domain == "host.monotonic"


def test_a_signed_definition_over_a_wide_payload_keeps_the_declared_width() -> None:
    """A narrow signal inside a long message reads only its own bits."""
    decoder = decoder_for(
        message((signal("Narrow", 0, 8, byte_order=LE),), name="Known", length=64, is_fd=True)
    )

    decoded = decoder.decode_frame(frame(bytes([0xFE]) + bytes(63), is_fd=True))

    assert decoded.signals[0].raw_value == 254
