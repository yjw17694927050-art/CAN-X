"""The payload policy, and definitions that must be refused rather than guessed.

Three rules, each of which a decoder can get wrong in a way that looks fine:

* **shorter than the definition is refused.** Partial decode is not supported in
  this increment, so a truncated payload is reported instead of half-decoded;
* **longer than the definition is accepted.** A CAN FD payload bucket may
  legitimately be larger than the engineering length, so only the declared bytes
  take part and the extra bytes are never interpreted;
* **a definition that cannot be decoded unambiguously is refused.** Bits outside
  the declared payload, an IEEE-754 payload this increment does not implement,
  and a multiplexing topology the canonical model cannot express are all
  reported — never half-decoded, and never allowed to escape as an
  ``IndexError`` or a plausible-looking wrong number.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from canx.dbc.errors import DbcDecodeUnsupportedError, DbcPayloadTooShortError
from decode_builders import BE, LE, decode_text, decoder_for, frame, message, signal

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "dbc"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- exact, longer, shorter -------------------------------------------------


def test_a_payload_of_exactly_the_declared_length_decodes() -> None:
    decoder = decoder_for(
        message((signal("S", 0, 16, byte_order=LE),), name="Only", length=8)
    )

    decoded = decoder.decode_frame(frame(bytes([0x34, 0x12]) + bytes(6)))

    assert decoded.signals[0].raw_value == 0x1234


def test_a_longer_payload_is_accepted_and_the_extra_bytes_are_ignored() -> None:
    """The case a CAN FD bucket forces: 12 declared bytes inside 16 carried."""
    decoder = decode_text(fixture_text("fd_extra_payload.dbc"))

    first = decoder.decode_frame(
        frame(bytes([7, 0x34, 0x12]) + bytes(13), is_fd=True, arbitration_id=0x600)
    )
    second = decoder.decode_frame(
        frame(bytes([7, 0x34, 0x12]) + bytes([0xFF]) * 13, is_fd=True, arbitration_id=0x600)
    )

    assert [(item.name, item.raw_value) for item in first.signals] == [
        ("Counter", 7),
        ("Value", 0x1234),
    ]
    assert first.signals == second.signals


def test_a_longer_payload_does_not_widen_a_signal() -> None:
    """Extra bytes must not leak into a signal that does not declare them."""
    decoder = decode_text(fixture_text("fd_extra_payload.dbc"))

    decoded = decoder.decode_frame(frame(bytes(16), is_fd=True, arbitration_id=0x600))

    assert [item.raw_value for item in decoded.signals] == [0, 0]


def test_a_shorter_payload_is_refused_as_a_typed_failure() -> None:
    decoder = decoder_for(
        message((signal("S", 0, 16, byte_order=LE),), name="Only", length=8)
    )

    with pytest.raises(DbcPayloadTooShortError) as info:
        decoder.decode_frame(frame(bytes(4)))

    error = info.value
    assert error.code == "dbc.payload_too_short"
    assert error.source == "dbc"
    assert error.recoverable is False
    assert error.details["expected_length"] == 8
    assert error.details["actual_length"] == 4
    assert error.details["message_name"] == "Only"


def test_a_truncated_payload_is_never_partially_decoded() -> None:
    """Even the signals that *would* fit are not reported."""
    decoder = decoder_for(
        message(
            (
                signal("Fits", 0, 8, byte_order=LE),
                signal("DoesNotFit", 8, 32, byte_order=LE),
            ),
            name="Only",
            length=8,
        )
    )

    with pytest.raises(DbcPayloadTooShortError):
        decoder.decode_frame(frame(bytes(2)))


def test_a_zero_length_message_decodes_an_empty_payload() -> None:
    decoder = decoder_for(message((), name="Heartbeat", length=0))

    decoded = decoder.decode_frame(frame(b""))

    assert decoded.message_name == "Heartbeat"
    assert decoded.signals == ()


def test_a_zero_length_message_cannot_decode_a_signal_it_does_not_have_room_for() -> None:
    decoder = decoder_for(message((signal("S", 0, 8, byte_order=LE),), name="Odd", length=0))

    with pytest.raises(DbcDecodeUnsupportedError):
        decoder.decode_frame(frame(b""))


# --- CAN FD -----------------------------------------------------------------


def test_an_fd_message_of_64_bytes_decodes_a_64_byte_frame() -> None:
    decoder = decode_text(fixture_text("can_fd.dbc"))

    data = bytes.fromhex("01020304a1b2c3d4") + bytes(56)
    decoded = decoder.decode_frame(frame(data, is_fd=True, arbitration_id=0x600))

    assert [item.raw_value for item in decoded.signals] == [0x04030201, 0xD4C3B2A1]


def test_an_fd_message_with_a_fewer_declared_bytes_still_decodes_a_full_bucket() -> None:
    decoder = decode_text(fixture_text("fd_extra_payload.dbc"))

    decoded = decoder.decode_frame(
        frame(bytes(64), is_fd=True, arbitration_id=0x600)
    )

    assert decoded.message_name == "FdShortFrame"
    assert decoded.frame.dlc == 64


# --- definitions that must be refused ---------------------------------------


def test_a_signal_reaching_past_the_declared_payload_is_refused() -> None:
    """The canonical model can hold it; the decoder must not read past the end."""
    decoder = decoder_for(
        message((signal("Oversized", 56, 16, byte_order=LE),), name="Only", length=8)
    )

    with pytest.raises(DbcDecodeUnsupportedError) as info:
        decoder.decode_frame(frame(bytes(8)))

    assert info.value.code == "dbc.decode_unsupported"
    assert info.value.details["reason"] == "signal_outside_message_payload"
    assert info.value.recoverable is False


def test_a_motorola_signal_reaching_past_the_declared_payload_is_refused() -> None:
    decoder = decoder_for(
        message((signal("Oversized", 7, 72, byte_order=BE),), name="Only", length=8)
    )

    with pytest.raises(DbcDecodeUnsupportedError):
        decoder.decode_frame(frame(bytes(8)))


def test_a_defective_definition_does_not_take_the_rest_of_the_database_with_it() -> None:
    """One unusable message must not make every other message undecodable."""
    decoder = decoder_for(
        message((signal("Oversized", 56, 16, byte_order=LE),), name="Broken", frame_id=0x100),
        message((signal("Fine", 0, 8, byte_order=LE),), name="Fine", frame_id=0x200, length=8),
    )

    decoded = decoder.decode_frame(frame(bytes([9]) + bytes(7), arbitration_id=0x200))

    assert decoded.signals[0].raw_value == 9
    with pytest.raises(DbcDecodeUnsupportedError):
        decoder.decode_frame(frame(bytes(8), arbitration_id=0x100))


def test_a_float_payload_is_refused_rather_than_read_as_an_integer() -> None:
    """The dangerous case: the bits would decode to a plausible wrong number."""
    decoder = decode_text(fixture_text("float_signal.dbc"))

    with pytest.raises(DbcDecodeUnsupportedError) as info:
        decoder.decode_frame(frame(bytes(8)))

    assert info.value.details["reason"] == "float_signal_payload"
    assert info.value.details["message_name"] == "FloatFrame"


def test_a_hand_written_float_definition_is_refused_the_same_way() -> None:
    decoder = decoder_for(
        message((signal("Float", 0, 32, byte_order=LE, is_float=True),), name="Only", length=8)
    )

    with pytest.raises(DbcDecodeUnsupportedError) as info:
        decoder.decode_frame(frame(bytes(8)))

    assert info.value.details["reason"] == "float_signal_payload"


def test_a_float_signal_is_refused_even_when_it_is_not_the_only_signal() -> None:
    """A partial result would be worse than none: the whole frame is refused."""
    decoder = decoder_for(
        message(
            (
                signal("Integer", 0, 8, byte_order=LE),
                signal("Float", 32, 32, byte_order=LE, is_float=True),
            ),
            name="Only",
            length=8,
        )
    )

    with pytest.raises(DbcDecodeUnsupportedError):
        decoder.decode_frame(frame(bytes(8)))


def test_two_multiplexer_switches_are_refused_as_an_ambiguous_topology() -> None:
    """The shape a nested / extended topology collapses into in this model."""
    decoder = decoder_for(
        message(
            (
                signal("Outer", 0, 4, byte_order=LE, is_multiplexer=True),
                signal("Inner", 4, 4, byte_order=LE, is_multiplexer=True),
                signal("Leaf", 8, 8, byte_order=LE),
            ),
            name="Only",
            length=8,
        )
    )

    with pytest.raises(DbcDecodeUnsupportedError) as info:
        decoder.decode_frame(frame(bytes(8)))

    assert info.value.details["reason"] == "ambiguous_multiplexing"


def test_a_multiplexed_signal_naming_an_absent_parent_is_refused() -> None:
    decoder = decoder_for(
        message(
            (
                signal(
                    "Child",
                    0,
                    8,
                    byte_order=LE,
                    multiplexer_signal="Missing",
                    multiplexer_ids=(0,),
                ),
            ),
            name="Only",
            length=8,
        )
    )

    with pytest.raises(DbcDecodeUnsupportedError) as info:
        decoder.decode_frame(frame(bytes(8)))

    assert info.value.details["reason"] == "ambiguous_multiplexing"


def test_an_unsupported_definition_is_reported_before_the_payload_is_judged() -> None:
    """Both are true; the definition is the fact worth reporting first."""
    decoder = decoder_for(
        message((signal("Oversized", 56, 16, byte_order=LE),), name="Only", length=8)
    )

    with pytest.raises(DbcDecodeUnsupportedError):
        decoder.decode_frame(frame(bytes(2)))
